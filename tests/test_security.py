"""Regression tests for the security review before the first public release.

Each test reproduces one attack that worked, or one promise the daemon makes, and
checks that it no longer works. The daemon runs as root, so the people and things
it must hold against are: a container on the Docker network or a local user who
can connect to its port, a signed-in person who is not the admin, a web page open
in the admin's browser, and a container that controls its own folder and log.
"""

from __future__ import annotations

import json
import os
import socket
import stat
import struct
import threading
import time
from pathlib import Path

import httpx
import pytest
import uvicorn
from fastapi.testclient import TestClient

from saltbox_ui import logcheck, recyclarr, safefs
from saltbox_ui.appsetup.discover import Discovery, Service, sabnzbd_api_key, xml_api_key
from saltbox_ui.daemon import Services, create_app
from saltbox_ui.daemon.app import DaemonGuard, ensure_token, listener_socket
from saltbox_ui.daemon.main import bind_sockets
from saltbox_ui.layout import Layout

AUTH = {"Authorization": "Bearer s3cret"}
LOCAL = "http://127.0.0.1:8701"  # a loopback listener: tools on the server itself
BRIDGE = "http://172.19.0.1:8701"  # the address Traefik reaches the daemon by
TRAEFIK = "172.19.0.2"
WEB_1080 = "72dae194fc92bf828f32cde7744e51a1"


# -- who gets in ---------------------------------------------------------------------------
@pytest.fixture
def daemon(scratch_layout: Layout, fake_sb: Path, tmp_path: Path):
    """The real app under uvicorn on a TCP port and on the root socket."""
    run_dir = Path("/tmp") / f"sbx-sec-{tmp_path.name[-12:]}"  # AF_UNIX paths must stay short
    api = str(run_dir / "api.sock")
    app = create_app(services=Services(scratch_layout, token="s3cret", root_socket=api))
    server = uvicorn.Server(uvicorn.Config(app, log_level="warning", lifespan="off"))
    sockets = bind_sockets(["127.0.0.1"], 0, api)
    port = sockets[0].getsockname()[1]
    thread = threading.Thread(target=server.run, kwargs={"sockets": sockets}, daemon=True)
    thread.start()
    for _ in range(200):
        if server.started:
            break
        time.sleep(0.02)
    local = httpx.Client(transport=httpx.HTTPTransport(uds=api), base_url="http://saltbox")
    yield port, local, scratch_layout
    server.should_exit = True
    thread.join(5)


def test_a_reset_tcp_connection_does_not_pass_for_the_root_socket(daemon) -> None:
    """A TCP peer that resets the connection right after sending leaves the server with no
    client address. That used to be read as "the root socket", which skipped the token:
    any container on the Docker network, and any local user, could run jobs as root."""
    port, local, layout = daemon
    body = json.dumps({"key": "attack:reset", "label": "no token was sent"}).encode()
    request = (
        b"POST /api/dismissals HTTP/1.1\r\nHost: x\r\nContent-Type: application/json\r\n"
        b"Content-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body
    )
    for _ in range(300):
        sock = socket.socket()
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
        sock.connect(("127.0.0.1", port))
        sock.sendall(request)
        sock.close()  # linger 0: a reset, not a normal close
    time.sleep(0.5)
    assert httpx.get(f"http://127.0.0.1:{port}/api/meta").status_code == 401
    assert not (layout.state_dir / "dismissed.json").exists()
    # the root socket itself still needs no token
    assert local.get("/api/meta").status_code == 200
    assert local.post("/api/dismissals", json={"key": "ok:1", "label": "x"}).status_code == 200
    assert "ok:1" in (layout.state_dir / "dismissed.json").read_text()


def test_only_the_bound_root_socket_is_trusted() -> None:
    """Trust comes from which listener served the connection, and from nothing else."""
    assert listener_socket({"server": ("/run/saltbox-ui/api.sock", None)}) == (
        "/run/saltbox-ui/api.sock"
    )
    for server in (None, ("127.0.0.1", 8701), ("172.19.0.1", 8701), ("", None), ("x",), "x"):
        assert listener_socket({"server": server}) is None

    seen: list[str] = []

    async def inner(scope, receive, send):  # the app behind the guard
        seen.append(scope["path"])

    guard = DaemonGuard(inner, "s3cret", root_socket="/run/saltbox-ui/api.sock")

    async def refusal(scope: dict) -> str | None:
        from starlette.datastructures import Headers

        return await guard._refusal(scope, Headers(scope=scope))

    import asyncio

    base = {"type": "http", "method": "GET", "path": "/api/meta", "headers": []}
    cases = [
        ({"client": None, "server": ("127.0.0.1", 8701)}, "unauthorised"),  # the reset socket
        ({"client": None, "server": None}, "unauthorised"),  # listener unknown: fail closed
        ({"client": None, "server": ("/run/other.sock", None)}, "unauthorised"),
        ({"client": ("10.0.0.9", 1), "server": ("/run/saltbox-ui/api.sock", 80)}, "unauthorised"),
        ({"client": None, "server": ("/run/saltbox-ui/api.sock", None)}, None),
    ]
    for extra, expected in cases:
        assert asyncio.run(refusal({**base, **extra})) == expected, extra


def test_the_token_is_compared_safely(scratch_layout: Layout) -> None:
    app = create_app(services=Services(scratch_layout, token="s3cret"))
    client = TestClient(app, base_url=LOCAL)
    for header in ("Bearer s3cre", "Bearer s3cret ", "Bearer S3CRET", "Basic s3cret", "s3cret"):
        assert client.get("/api/meta", headers={"Authorization": header}).status_code == 401
    # text that is not ASCII used to raise inside the comparison (a 500)
    raw = [(b"authorization", "Bearer s3crét".encode("latin-1"))]
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/api/meta",
        "headers": raw,
        "client": ("1.2.3.4", 5),
        "server": ("127.0.0.1", 8701),
    }
    import asyncio

    from starlette.datastructures import Headers

    guard = DaemonGuard(None, "s3cret")  # type: ignore[arg-type]
    assert asyncio.run(guard._refusal(scope, Headers(scope=scope))) == "unauthorised"


def test_unknown_single_sign_on_is_refused_at_start(scratch_layout: Layout) -> None:
    with pytest.raises(ValueError, match="unknown single sign-on"):
        create_app(
            services=Services(scratch_layout, token="s3cret", sso="other")
        ).build_middleware_stack()


# -- a page on another site ----------------------------------------------------------------
def test_state_changes_from_another_origin_are_refused(scratch_layout: Layout) -> None:
    """Browsers too old to send Sec-Fetch-Site still send Origin with a POST."""
    app = create_app(services=Services(scratch_layout, token="s3cret"))
    client = TestClient(app, base_url=LOCAL)
    body = {"key": "x:1", "label": "x"}
    for origin in ("https://evil.example", "https://sonarr.127.0.0.1:8701", "null"):
        refused = client.post("/api/dismissals", json=body, headers={**AUTH, "Origin": origin})
        assert refused.status_code == 403, origin
    by_referer = {**AUTH, "Referer": "https://evil.example/page"}
    assert client.post("/api/dismissals", json=body, headers=by_referer).status_code == 403
    own_page = {**AUTH, "Referer": f"{LOCAL}/system"}
    assert client.post("/api/dismissals", json=body, headers=own_page).status_code == 200
    for headers in (
        {},  # not a browser: curl, sbx
        {"Origin": LOCAL},
        {"Origin": "https://evil.example", "Sec-Fetch-Site": "same-origin"},  # the browser's word
    ):
        allowed = client.post("/api/dismissals", json=body, headers={**AUTH, **headers})
        assert allowed.status_code == 200, headers
    cross = {"Origin": LOCAL, "Sec-Fetch-Site": "cross-site"}
    assert client.post("/api/dismissals", json=body, headers={**AUTH, **cross}).status_code == 403
    # reading is not a state change
    assert (
        client.get("/api/meta", headers={**AUTH, "Origin": "https://evil.example"}).status_code
        == 200
    )


def test_pages_cannot_be_framed_and_answers_are_not_cached(
    scratch_layout: Layout, tmp_path: Path
) -> None:
    web = tmp_path / "web"
    (web / "assets").mkdir(parents=True)
    (web / "index.html").write_text("<!doctype html><title>x</title>")
    (web / "assets" / "app.js").write_text("1")
    client = TestClient(create_app(services=Services(scratch_layout, token="s3cret", web_dir=web)))
    for path, headers in (("/", {}), ("/assets/app.js", {}), ("/api/meta", AUTH), ("/api/x", {})):
        response = client.get(path, headers=headers)
        policy = response.headers["content-security-policy"]
        assert "frame-ancestors 'none'" in policy and "script-src 'self'" in policy, path
        assert "unsafe-inline" not in policy and "unsafe-eval" not in policy
        assert response.headers["x-frame-options"] == "DENY"
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["referrer-policy"] == "no-referrer"
    assert client.get("/api/meta", headers=AUTH).headers["cache-control"] == "no-store"
    assert client.get("/api/meta").headers["cache-control"] == "no-store"  # the 401 too
    assert client.get("/").headers["cache-control"] == "no-cache"
    # no interactive docs page: it would load a script from a CDN into this origin
    assert "swagger" not in client.get("/api/docs", headers=AUTH).text.lower()


# -- what is accepted ----------------------------------------------------------------------
def test_oversized_and_deeply_nested_bodies_are_refused(scratch_layout: Layout) -> None:
    client = TestClient(create_app(services=Services(scratch_layout)))
    big = client.post("/api/dismissals", content=b'{"key":"' + b"A" * 3_000_000 + b'"}')
    assert big.status_code == 413

    def chunks():
        yield b'{"key":"'
        for _ in range(40):
            yield b"A" * 100_000
        yield b'"}'

    streamed = client.post(
        "/api/dismissals", content=chunks(), headers={"Content-Type": "application/json"}
    )
    assert streamed.status_code == 413
    deep = b'{"a":' * 20000 + b"1" + b"}" * 20000
    assert client.post("/api/dismissals", content=deep).status_code in (400, 422)
    assert client.post("/api/dismissals", json={"key": "x", "label": "x"}).status_code == 200


def test_odd_paths_and_ids_do_not_crash(scratch_layout: Layout, tmp_path: Path) -> None:
    web = tmp_path / "web"
    web.mkdir()
    (web / "index.html").write_text("<!doctype html><title>page</title>")
    secret = tmp_path / "secret.txt"
    secret.write_text("root-only")
    client = TestClient(create_app(services=Services(scratch_layout, web_dir=web)))
    for path in ("/%00", "/a%00b", "/" + "a" * 5000, "/..%2f..%2fsecret.txt", "/%2e%2e/secret.txt"):
        response = client.get(path)
        assert response.status_code == 200 and "page" in response.text, path
    for job_id in ("a" * 300, "x.json", "ABC", "%00", "-v"):
        assert client.get(f"/api/jobs/{job_id}").status_code == 404, job_id
        assert client.get(f"/api/jobs/{job_id}/log").status_code == 404, job_id
        assert client.post(f"/api/jobs/{job_id}/cancel").status_code == 404, job_id


def test_a_tag_cannot_be_an_option(scratch_layout: Layout, fake_sb: Path) -> None:
    client = TestClient(create_app(services=Services(scratch_layout)))
    for tags in (
        ["--help"],
        ["-e", "x=1"],
        ["sonarr;id"],
        ["a b,-v"],
        ["--extra-vars=@/etc/shadow"],
    ):
        refused = client.post("/api/jobs", json={"kind": "install", "tags": tags})
        assert refused.status_code == 422 and "not a tag" in refused.text, tags
    skip = client.post(
        "/api/jobs", json={"kind": "install", "tags": ["sonarr"], "skip_tags": ["--become"]}
    )
    assert skip.status_code == 422
    from saltbox_ui.runner import SbRunner

    command = SbRunner(scratch_layout).install_command(
        ["sonarr,radarr", " plex "], skip_tags=["a b"]
    )
    assert command[1:] == ["install", "sonarr,radarr,plex", "--skip-tags", "a,b"]
    assert (
        not list(scratch_layout.jobs_dir.glob("*.json"))
        if scratch_layout.jobs_dir.exists()
        else True
    )


def test_job_output_is_shown_without_control_sequences(scratch_layout: Layout) -> None:
    from saltbox_ui.runner import SbRunner

    sb = scratch_layout.sb_binary
    sb.parent.mkdir(parents=True, exist_ok=True)
    sb.write_text(
        "#!/bin/sh\nprintf 'plain\\n\\033]0;evil title\\007\\033[31mred\\033[0m\\n'\n"
        "printf 'c1 \\302\\233 31m end\\n\\033]52;c;ZXZpbA==\\033\\\\after\\n'\n"
    )
    sb.chmod(0o755)
    runner = SbRunner(scratch_layout)
    job = runner.run(runner.install_command(["sonarr"]), kind="install")
    log = runner.log(job.id)
    assert "plain" in log and "red" in log and "after" in log
    assert "\x1b" not in log and "\x07" not in log and "\x9b" not in log and "evil title" not in log


def test_a_failed_job_reaches_the_alerts(scratch_layout: Layout, fake_sb: Path) -> None:
    """The hook that reports a failed job was only registered after a self-update."""
    services = Services(scratch_layout)
    seen: list[str] = []
    services.automation.on_job_finished = lambda job: seen.append(job.status)  # type: ignore[method-assign]
    os.environ["FAKE_SB_EXIT"] = "3"
    try:
        services.jobs.start("install", tags=["sonarr"])
        services.jobs.wait(20)
    finally:
        del os.environ["FAKE_SB_EXIT"]
    assert seen == ["failed"]


# -- files in folders that are not root's ----------------------------------------------------
def found() -> Discovery:
    return Discovery(
        services={
            "sonarr": [Service("sonarr", "sonarr", 8989, True, api_key="sonarrkey0123456789")],
            "radarr": [],
        }
    )


@pytest.fixture
def recyclarr_layout(scratch_layout: Layout) -> Layout:
    (scratch_layout.appdata / "recyclarr").mkdir(parents=True)
    return scratch_layout


def test_recyclarr_save_does_not_read_through_a_link(
    recyclarr_layout: Layout, tmp_path: Path
) -> None:
    """The container owns /opt/recyclarr. With recyclarr.yml a link to a root-only file,
    saving used to copy that file into a backup the container could read."""
    folder = recyclarr_layout.appdata / "recyclarr"
    token = tmp_path / "api-token"
    token.write_text("TOKEN-daemon-bearer\n")
    token.chmod(0o600)
    (folder / "recyclarr.yml").symlink_to(token)
    with pytest.raises(recyclarr.RecyclarrError, match="symbolic link"):
        recyclarr.write_config(recyclarr_layout, found(), {"sonarr": [WEB_1080]})
    assert [p.name for p in folder.iterdir()] == ["recyclarr.yml"]
    assert token.read_text() == "TOKEN-daemon-bearer\n"
    # the status page does not read it either
    assert recyclarr.status(recyclarr_layout, found(), {})["managed"] is True


def test_recyclarr_save_does_not_write_through_planted_links(
    recyclarr_layout: Layout, tmp_path: Path
) -> None:
    """Backup names were predictable to the second: the container planted links under those
    names, and root wrote the container's text through them."""
    folder = recyclarr_layout.appdata / "recyclarr"
    victim = tmp_path / "victim"
    victim.write_text("untouched")
    (folder / "recyclarr.yml").write_text("attacker controlled text\n")
    now = time.time()
    for second in range(-2, 8):
        stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(now + second))
        (folder / f"recyclarr.yml.bak-{stamp}").symlink_to(victim)
    (folder / "secrets.yml").symlink_to(victim)
    result = recyclarr.write_config(recyclarr_layout, found(), {"sonarr": [WEB_1080]})
    assert victim.read_text() == "untouched"
    backup = Path(result["backup"])
    assert not backup.is_symlink() and backup.read_text() == "attacker controlled text\n"
    secrets = folder / "secrets.yml"
    assert not secrets.is_symlink() and "sonarr_apikey" in secrets.read_text()
    assert stat.S_IMODE(secrets.stat().st_mode) == 0o600


def test_app_files_are_read_without_following_links_or_blocking(tmp_path: Path) -> None:
    """config.xml belongs to the app. A link to another app's file handed that app's key to
    whoever planted it; a pipe hung every page; a huge file filled memory."""
    other = tmp_path / "prowlarr.xml"
    other.write_text("<Config><ApiKey>prowlarrkey123</ApiKey></Config>")
    link = tmp_path / "config.xml"
    link.symlink_to(other)
    assert xml_api_key(other) == "prowlarrkey123" and xml_api_key(link) == ""

    fifo = tmp_path / "fifo.xml"
    os.mkfifo(fifo)
    done: list[str] = []
    reader = threading.Thread(target=lambda: done.append(xml_api_key(fifo)), daemon=True)
    reader.start()
    reader.join(5)
    assert done == [""], "reading a named pipe must not block"

    huge = tmp_path / "huge.ini"
    with open(huge, "wb") as handle:
        handle.truncate(64 * 1024 * 1024)  # sparse
    assert sabnzbd_api_key(huge) == "" and safefs.read_text_or_none(huge) is None

    odd = tmp_path / "odd.xml"
    odd.write_text('<Config><ApiKey>abc"def</ApiKey></Config>')
    assert xml_api_key(odd) == ""  # would have broken out of its quotes in secrets.yml
    ini = tmp_path / "sabnzbd.ini"
    ini.write_text("[misc]\napi_key = abc def\n")
    assert sabnzbd_api_key(ini) == ""


def test_safefs_refuses_links_and_keeps_files_private(tmp_path: Path) -> None:
    folder = tmp_path / "folder"
    folder.mkdir()
    outside = tmp_path / "outside"
    outside.write_text("outside")
    (folder / "link").symlink_to(outside)
    with pytest.raises(safefs.UnsafeFile):
        safefs.read_text(folder / "link")
    with safefs.open_dir(folder) as fd:
        with pytest.raises(FileExistsError):
            safefs.create_file(fd, "link", b"x")
        safefs.replace_file(fd, "link", b"new", mode=0o640)
    assert outside.read_text() == "outside"
    assert not (folder / "link").is_symlink() and (folder / "link").read_text() == "new"
    assert stat.S_IMODE((folder / "link").stat().st_mode) == 0o640
    assert [p.name for p in folder.iterdir()] == ["link"]  # no temporary file left behind

    linked_dir = tmp_path / "linked"
    linked_dir.symlink_to(folder)
    with pytest.raises(OSError), safefs.open_dir(linked_dir):
        pass

    state = tmp_path / "state"
    state.mkdir(mode=0o755)
    safefs.write_private(state / "api-token", "t\n")
    assert stat.S_IMODE(state.stat().st_mode) == 0o700
    assert stat.S_IMODE((state / "api-token").stat().st_mode) == 0o600


def test_secrets_on_disk_are_never_readable_by_others(
    scratch_layout: Layout, tmp_path: Path
) -> None:
    from saltbox_ui.daemon.install import write_route
    from saltbox_ui.installer.plan import InstallPlan

    token = ensure_token(scratch_layout)
    path = scratch_layout.state_dir / "api-token"
    assert path.read_text().strip() == token and stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(scratch_layout.state_dir.stat().st_mode) == 0o700

    plan = tmp_path / "plans" / "install-plan.json"
    InstallPlan().save(plan)
    assert stat.S_IMODE(plan.stat().st_mode) == 0o600
    assert stat.S_IMODE(plan.parent.stat().st_mode) == 0o700

    # The Traefik route holds the token. A file someone left under that name, open to
    # all or owned by another user, does not decide who can read it.
    scratch_layout.config_file("accounts").write_text("---\nuser:\n  domain: example.com\n")
    traefik = tmp_path / "traefik"
    traefik.mkdir()
    (traefik / "saltbox-ui.yml").write_text("planted")
    (traefik / "saltbox-ui.yml").chmod(0o666)
    if os.geteuid() == 0:
        os.chown(traefik / "saltbox-ui.yml", 65534, 65534)
    route = write_route(scratch_layout, token, traefik_dir=traefik)
    assert stat.S_IMODE(route.stat().st_mode) == 0o600 and token in route.read_text()
    if os.geteuid() == 0:
        assert route.stat().st_uid == 0


def test_the_anchor_fix_stays_in_the_local_folder(tmp_path: Path) -> None:
    local = tmp_path / "local"
    (local / "Media").mkdir(parents=True)
    for bad in (
        [f"{local}/Media/mounted.bin"],
        [f"{local}/.."],
        [f"{local}/-rf"],
        [f"{local}/a b"],
    ):
        with pytest.raises(ValueError):
            logcheck.create_anchors(bad, local)
    assert logcheck.create_anchors([f"{local}/mounted.bin"], local) == [f"{local}/mounted.bin"]
    assert stat.S_IMODE((local / "mounted.bin").stat().st_mode) == 0o644


# -- the installer's terminal ----------------------------------------------------------------
HOSTILE_TEXT = "sonarr: [@click=app.pwn]Failed[/] [bold red]x[/] path [/mnt/x] [/b]"


async def test_the_installer_shows_brackets_and_never_obeys_them() -> None:
    """Text from apps, rclone and cloud folders reaches the installer, which runs as root.
    Textual would read ``[@click=...]`` in it as a link that runs an action, and ``[/b]``
    out of place as an error that ends the app."""
    from textual.app import ComposeResult
    from textual.widgets import SelectionList

    from saltbox_ui.tui import widgets
    from saltbox_ui.tui.plain import Label, PlainApp, RadioButton, Static

    ran: list[str] = []

    class Probe(PlainApp):
        def compose(self) -> ComposeResult:
            yield Static(HOSTILE_TEXT, id="static")
            yield Label(HOSTILE_TEXT, id="label")
            yield RadioButton(HOSTILE_TEXT, id="radio")
            yield widgets.CheckRow("check", "title [/b]", id="row")
            yield widgets.FilterableSelection([(HOSTILE_TEXT, "value")], id="pick")

        def action_pwn(self) -> None:
            ran.append("pwn")

    app = Probe()
    async with app.run_test(size=(160, 40)) as pilot:
        app.query_one("#row", widgets.CheckRow).update("failed", HOSTILE_TEXT)
        app.query_one("#static", Static).update(HOSTILE_TEXT + " again")
        app.notify(HOSTILE_TEXT)
        await pilot.pause()
        for selector in ("#static", "#label"):
            assert "[@click=app.pwn]" in str(app.query_one(selector).render())
        assert "[@click=app.pwn]" in str(app.query_one("#radio", RadioButton).label)
        option = app.query_one("#pick SelectionList", SelectionList).get_option_at_index(0)
        assert "[@click=app.pwn]" in str(option.prompt)
        for selector in ("#static", "#label", "#radio", "#row", "#pick"):
            await pilot.click(selector)
        await pilot.pause()
    assert ran == []


def test_the_installer_cannot_be_served_to_a_browser() -> None:
    """``--serve`` put the root wizard on a port with no sign-in at all."""
    import saltbox_ui.tui.app as tui_app

    assert not hasattr(tui_app, "serve")
    source = Path(tui_app.__file__).read_text()
    assert "textual_serve" not in source and "--serve" not in source


# -- what the daemon asks of other things ------------------------------------------------------
def _serve(handler) -> tuple[str, object]:
    import http.server

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{server.server_port}", server


@pytest.fixture
def hostile_app():
    """Something answering on an app's port that is not the app, and a second service on
    the server that the first tries to send the daemon to."""
    import http.server

    seen: list[tuple[str, str, dict[str, str]]] = []

    class Internal(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args) -> None:
            pass

        def do_GET(self) -> None:
            seen.append(("internal", self.path, {k.lower(): v for k, v in self.headers.items()}))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"secret": "internal"}')

    internal, internal_server = _serve(Internal)

    class App(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args) -> None:
            pass

        def _answer(self, status: int, body: bytes = b"", **headers: str) -> None:
            self.send_response(status)
            for name, value in headers.items():
                self.send_header(name.replace("_", "-"), value)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_PUT(self) -> None:
            self.do_GET()

        def do_HEAD(self) -> None:
            self.do_GET()

        def do_GET(self) -> None:
            seen.append(("app", self.path, {k.lower(): v for k, v in self.headers.items()}))
            if self.path == "/elsewhere":
                self._answer(302, Location=f"{internal}/internal")
            elif self.path == "/file":
                self._answer(302, Location="file:///etc/hostname")
            elif self.path.startswith("/api/"):  # an app with a URL base
                self._answer(307, Location=f"/sonarr{self.path}")
            elif self.path.startswith("/sonarr/api/"):
                self._answer(200, b'{"version": "4"}')
            elif self.path == "/loop":
                self._answer(302, Location="/loop")
            elif self.path == "/huge":
                self.send_response(200)
                self.end_headers()
                try:
                    for _ in range(600):
                        self.wfile.write(b"A" * 100_000)
                except OSError:
                    pass
                self.close_connection = True
            elif self.path == "/short":
                self.send_response(200)
                self.send_header("Content-Length", "1000")
                self.end_headers()
                self.wfile.write(b"only this")
                self.close_connection = True
            elif self.path in ("/drip-headers", "/drip-body"):
                try:
                    if self.path == "/drip-body":
                        self.send_response(200)
                        self.send_header("Content-Length", "100000")
                        self.end_headers()
                    else:
                        self.wfile.write(b"HTTP/1.1 200 OK\r\n")
                    for _ in range(100):  # one byte at a time, for twenty seconds
                        self.wfile.write(b"X")
                        self.wfile.flush()
                        time.sleep(0.2)
                except OSError:
                    pass
                self.close_connection = True
            elif self.path == "/garbage":
                self.wfile.write(b"this is not http at all\r\n\r\n")
                self.close_connection = True
            else:
                self._answer(200, b"{}")

    app, app_server = _serve(App)
    yield app, seen
    app_server.shutdown()
    internal_server.shutdown()


def test_the_http_client_goes_only_where_it_was_sent(hostile_app, monkeypatch) -> None:
    """A redirect from an "app" used to be followed, API key and all, to any host and port
    it named; and nothing bounded how much it could send back or for how long."""
    from saltbox_ui.appsetup import http

    base, seen = hostile_app
    key = {"X-Api-Key": "sonarr-key"}
    redirected = http.urllib_transport("GET", f"{base}/elsewhere", key, None)
    assert redirected.status == 302 and b"internal" not in redirected.body
    assert [who for who, _, _ in seen] == ["app"]  # the other service was never asked
    assert http.urllib_transport("GET", f"{base}/file", key, None).status == 302
    for address in ("file:///etc/hostname", "ftp://127.0.0.1/x", "data:text/plain,x", "gopher://x"):
        with pytest.raises(http.SetupError, match="bad address"):
            http.urllib_transport("GET", address, {}, None)
    with pytest.raises(http.SetupError, match="has no address"):
        http.urllib_transport("GET", "http://sonarr.not-running.invalid:8989/api", key, None)

    # The same host and port is the app itself: one with a URL base answers this way,
    # and a PUT keeps its method and body.
    seen.clear()
    moved = http.urllib_transport("GET", f"{base}/api/v3/system/status", key, None)
    assert moved.status == 200 and moved.json() == {"version": "4"}
    put = http.urllib_transport("PUT", f"{base}/api/v3/series/editor", key, b"{}")
    assert put.status == 200 and seen[-1][1] == "/sonarr/api/v3/series/editor"
    assert seen[-1][2]["x-api-key"] == "sonarr-key"
    assert http.urllib_transport("GET", f"{base}/loop", key, None).status == 302  # and gives up

    # The registry transport may be handed on, but only to https.
    seen.clear()
    assert http.registry_transport("HEAD", f"{base}/elsewhere", {}, None).status == 302
    assert [who for who, _, _ in seen] == ["app"]

    monkeypatch.setattr(http, "MAX_BODY_BYTES", 1_000_000)
    with pytest.raises(http.SetupError, match="too large"):
        http.urllib_transport("GET", f"{base}/huge", {}, None)
    with pytest.raises(http.SetupError, match="cut short"):
        http.urllib_transport("GET", f"{base}/short", {}, None)
    with pytest.raises(http.SetupError, match="cannot reach"):
        http.urllib_transport("GET", f"{base}/garbage", {}, None)
    # Absurdly deep JSON must not raise: older Pythons give up on it (None), newer ones read it.
    deep = http.Response(200, b"[" * 100_000 + b"]" * 100_000).json()
    assert deep is None or isinstance(deep, list)


def test_an_answer_that_trickles_in_is_cut_off(hostile_app, monkeypatch) -> None:
    """One byte now and then keeps a per-read timeout happy for ever. The whole answer has
    a deadline, headers included, or each such request holds a worker for good."""
    from saltbox_ui.appsetup import http

    base, _ = hostile_app
    monkeypatch.setattr(http, "DEADLINE", 1.0)
    for path in ("/drip-headers", "/drip-body"):
        started = time.monotonic()
        with pytest.raises(http.SetupError, match="took too long"):
            http.urllib_transport("GET", f"{base}{path}", {}, None)
        assert time.monotonic() - started < 5, path


def test_a_container_name_is_never_looked_up_in_dns(monkeypatch, tmp_path: Path) -> None:
    """A stopped container is not in /etc/hosts. Its name then went to DNS, and whatever
    answered was sent the app's API key and the account password."""
    import importlib
    import socket as socket_module

    discover = importlib.import_module("saltbox_ui.appsetup.discover")

    def dns(*args, **kwargs):
        raise AssertionError("a container's name was looked up in DNS")

    for name in ("gethostbyname", "getaddrinfo", "gethostbyname_ex"):
        monkeypatch.setattr(socket_module, name, dns)
    monkeypatch.setattr(discover.shutil, "which", lambda name: None)  # no docker to ask
    assert discover.default_resolver("qbittorrent") is None

    hosts = tmp_path / "hosts"
    hosts.write_text(
        "127.0.0.1 localhost\n172.19.0.7 qbittorrent qbittorrent.saltbox # sdhm\n"
        "8.8.8.8 public\n# 172.19.0.9 commented\n127.0.0.1 looped\n169.254.169.254 meta\n"
    )
    assert discover.hosts_file_address("qbittorrent", hosts) == "172.19.0.7"
    assert discover.hosts_file_address("qbittorrent.saltbox", hosts) == "172.19.0.7"
    assert discover.hosts_file_address("commented", hosts) is None
    assert discover._container_address("172.19.0.7") == "172.19.0.7"
    assert discover._container_address("fd00::5") == "[fd00::5]"
    for elsewhere in ("8.8.8.8", "127.0.0.1", "169.254.169.254", "0.0.0.0", "224.0.0.1", "x"):
        assert discover._container_address(elsewhere) is None, elsewhere

    found = Discovery(services={"sonarr": [Service("sonarr", "sonarr", 8989, True)]})
    discover.resolve_hosts(found, resolver=lambda name: None)
    assert found.services["sonarr"][0].host_url == "http://sonarr.not-running.invalid:8989"


def test_a_registry_cannot_send_the_daemon_elsewhere() -> None:
    from saltbox_ui.appsetup.http import Response, SetupError
    from saltbox_ui.state.updates import Registry, parse_ref

    asked: list[str] = []

    def transport(method, url, headers, body):
        asked.append(url)
        if "token" in url or "127.0.0.1" in url or url.startswith("file"):
            return Response(200, b'{"token": "INTERNAL"}')
        return Response(401, b"", {"www-authenticate": f'Bearer realm="{realm}",service="x"'})

    for realm in (
        "http://127.0.0.1:3377/internal",
        "file:///dev/zero#",
        "https://auth.example/token#frag",
        "//auth.example/token",
    ):
        asked.clear()
        with pytest.raises(SetupError, match="not an https address"):
            Registry(transport=transport).digest(parse_ref("ghcr.io/x/y:latest"))
        assert len(asked) == 1 and asked[0].startswith("https://ghcr.io/"), realm


def test_secrets_are_found_however_the_file_writes_them(scratch_layout: Layout) -> None:
    from saltbox_ui.daemon.routes import _redactor
    from saltbox_ui.secrets import HIDDEN, Redactor, mask_text, secret_values

    # a password with a # in it is how the app's own YAML writer leaves it: unquoted
    text = "user:\n  pass: abc#def123456\n  name: seed # a comment\n"
    assert secret_values(text) == {"user.pass": "abc#def123456"}
    assert "abc#def123456" not in mask_text(text) and "# a comment" in mask_text(text)

    scratch_layout.config_file("accounts").write_text(
        "---\n"
        'user:\n  name: seed\n  pass: "it\'s a p@ss/word 1"\n'
        "apprise: tgram://123456:AAtelegramtoken/987\n"
        "cloudflare: {email: me@example.com, api: flowstylekey123}\n"
        "extra:\n  private_key: |\n    BLOCKSCALARKEY12345\n"
        "  tokens:\n    - listtoken-abcdef\n"
    )
    redact = _redactor(Services(scratch_layout))
    log = (
        "login it's a p@ss/word 1; url https://x/?p=it%27s%20a%20p%40ss%2Fword%201 "
        "form p=it%27s+a+p%40ss%2Fword+1 tgram://123456:AAtelegramtoken/987 "
        "flowstylekey123 BLOCKSCALARKEY12345 listtoken-abcdef seed"
    )
    cleaned = redact(log)
    for secret in (
        "p@ss",
        "p%40ss",
        "AAtelegramtoken",
        "flowstylekey123",
        "BLOCKSCALARKEY12345",
        "listtoken-abcdef",
    ):
        assert secret not in cleaned, secret
    assert cleaned.count(HIDDEN) >= 6 and cleaned.endswith(" seed")
    assert (
        Redactor(['say "hi" \\ there'])('{"p": "say \\"hi\\" \\\\ there"}')
        == f'{{"p": "{HIDDEN}"}}'
    )


def test_a_job_log_is_cut_after_it_is_redacted(scratch_layout: Layout) -> None:
    scratch_layout.config_file("accounts").write_text("---\nuser:\n  pass: hunter2hunter2\n")
    sb = scratch_layout.sb_binary
    sb.parent.mkdir(parents=True, exist_ok=True)
    sb.write_text("#!/bin/sh\necho 'the password is hunter2hunter2 ok'\n")
    sb.chmod(0o755)
    services = Services(scratch_layout)
    client = TestClient(create_app(services=services))
    job = client.post("/api/jobs", json={"kind": "install", "tags": ["sonarr"]}).json()
    services.jobs.wait(20)
    raw = services.runner.log(job["id"])
    inside = raw.index("hunter2hunter2") + 4  # an offset in the middle of the secret
    whole = client.get(f"/api/jobs/{job['id']}/log").json()
    assert "hunter2" not in whole["text"] and whole["offset"] == len(raw)
    line = raw.rfind("\n", 0, inside) + 1
    for offset in range(inside - 6, inside + 12):
        part = client.get(f"/api/jobs/{job['id']}/log?offset={offset}").json()
        # moved back to the start of the line, and hidden as a whole
        assert "nter2" not in part["text"] and "hunter" not in part["text"], offset
        assert part["text"] == whole["text"][line:], offset
    # An offset stays true when something already in the log becomes a secret later.
    end = client.get(f"/api/jobs/{job['id']}/log?offset={len(raw)}").json()
    assert end["text"] == "" and end["offset"] == len(raw)
    scratch_layout.config_file("accounts").write_text(
        "---\nuser:\n  pass: hunter2hunter2\n  token: the password is\n"
    )
    services.__dict__.pop("_redactor", None)
    with open(services.runner.jobs_dir / f"{job['id']}.log", "a") as log:
        log.write("NEW-LINE-AFTER\n")
    assert client.get(f"/api/jobs/{job['id']}/log?offset={len(raw)}").json()["text"] == (
        "NEW-LINE-AFTER\n"
    )


def test_claude_is_never_run_as_root_or_with_the_users_other_groups(monkeypatch) -> None:
    from saltbox_ui import explain

    with pytest.raises(explain.ExplainError, match="not run as root"):
        explain.runner_as("root")(["true"], None, 5)
    if os.geteuid() != 0:
        pytest.skip("needs root to change user")
    monkeypatch.setattr(explain, "WORK_DIR", Path("/tmp") / f"sbx-explain-{os.getpid()}")
    try:
        code, out, _ = explain.runner_as("nobody")(["sh", "-c", "id -u; id -G; pwd"], None, 10)
        uid, groups, cwd = out.split("\n")[:3]
        assert code == 0 and uid != "0" and len(groups.split()) == 1
        assert cwd.startswith(str(explain.WORK_DIR))
    finally:
        import shutil

        shutil.rmtree(explain.WORK_DIR, ignore_errors=True)


def test_settings_that_reach_other_programs_are_checked(scratch_layout: Layout) -> None:
    client = TestClient(create_app(services=Services(scratch_layout)))
    for url in ("ntfy://topic\ninclude /etc/shadow", "not a url", "a b://c"):
        bad = client.put("/api/automation", json={"alerts": {"url": url}})
        assert bad.status_code in (400, 422), url
    assert (
        client.put("/api/automation", json={"alerts": {"url": "ntfy://topic"}}).status_code == 200
    )

    from saltbox_ui.appsetup.vpnport import forwarded_port
    from saltbox_ui.seeding import SeedingError, validated
    from saltbox_ui.state.containers import web_urls

    assert (
        forwarded_port("gluetun", lambda c, p: "", lambda c: "port forwarded is 4294967296") is None
    )
    assert forwarded_port("gluetun", lambda c, p: "", lambda c: "port forwarded is 51413") == 51413
    with pytest.raises(SeedingError):
        validated(None, 1e-9)
    assert validated(2.0, 1.0) == (2.0, 1.0)
    labels = {
        "traefik.http.routers.sonarr.rule": "Host(`sonarr.example.com`)",
        "traefik.http.routers.x.rule": (
            "Host(`a b`) || Host(`javascript:alert(1)`) || Host(`ok.example.org`)"
        ),
    }
    assert web_urls("sonarr", labels) == ["https://sonarr.example.com", "https://ok.example.org"]


# -- second review: what the first round of fixes left open -------------------------------------
def _fake_verify(cookie: str | None, forwarded: dict[str, str]) -> tuple[str, str] | None:
    """Stand in for the sign-on in tests: a cookie ``who=<user>[;groups=<g>]`` is a session.

    It models the real contract that matters for the guard: a valid session is proven by a
    cookie the provider vouches for, never by a request header a router can set.
    """
    if not cookie:
        return None
    parts = dict(p.split("=", 1) for p in cookie.replace("&", ";").split(";") if "=" in p)
    user = parts.get("who", "").strip()
    return (user, parts.get("groups", "")) if user else None


def proxied_app(layout: Layout, *, session_verify=_fake_verify, **options) -> object:
    services = Services(
        layout,
        token="local-token",
        proxy_token="proxy-token",
        proxy_peer=lambda peer: peer == TRAEFIK,
        **options,
    )
    return create_app(services=services, session_verify=session_verify)


def test_the_token_traefik_holds_is_not_the_daemons(scratch_layout: Layout) -> None:
    """Traefik shows its configuration, the token header included, to every container on
    its network (its API answers on port 8080 there, with no sign-in). So that token is
    only good from Traefik's own address, with a signed-in admin, and never where the
    daemon's own token is accepted."""
    scratch_layout.config_file("accounts").write_text("---\nuser:\n  name: seed\n")
    app = proxied_app(scratch_layout)
    proxy = {"Authorization": "Bearer proxy-token"}
    local = {"Authorization": "Bearer local-token"}
    admin = {"X-Forwarded-Host": "saltbox.example.com", "Cookie": "who=seed"}

    def client(base: str, peer: str | None) -> TestClient:
        return TestClient(app, base_url=base, client=(peer, 40000) if peer else ("x", 1))

    traefik = client(BRIDGE, TRAEFIK)
    assert traefik.get("/api/meta", headers={**proxy, **admin}).status_code == 200
    # read out of Traefik by another container: same token, same headers, another address
    other = client(BRIDGE, "172.19.0.9")
    refused = other.get("/api/meta", headers={**proxy, **admin})
    assert refused.status_code == 403 and "only the reverse proxy" in refused.json()["error"]
    assert other.post("/api/host/reboot", headers={**proxy, **admin}).status_code == 403
    # by another account on the server: neither listener takes it
    on_host = client(BRIDGE, "172.19.0.1")
    assert on_host.get("/api/meta", headers={**proxy, **admin}).status_code == 403
    loopback = client(LOCAL, "127.0.0.1")
    assert loopback.get("/api/meta", headers=proxy).status_code == 401
    assert loopback.get("/api/meta", headers={**proxy, **admin}).status_code == 401
    assert loopback.get("/api/meta", headers=local).status_code == 200
    # the daemon's own token is not accepted where Traefik's is
    assert traefik.get("/api/meta", headers={**local, **admin}).status_code == 401
    # and from Traefik's address the token alone is still not enough
    assert traefik.get("/api/meta", headers=proxy).status_code == 403
    assert traefik.get("/api/meta", headers={**proxy, "Cookie": "who=guest"}).status_code == 403

    import asyncio

    from starlette.datastructures import Headers

    guard = DaemonGuard(None, "local-token", proxy_token="proxy-token", proxy_peer=lambda p: True)  # type: ignore[arg-type]
    raw = [(b"authorization", b"Bearer proxy-token")]
    reset = {"type": "http", "method": "GET", "path": "/api/meta", "headers": raw}
    for scope in (
        {**reset, "client": None, "server": ("172.19.0.1", 8701)},  # a reset connection
        {**reset, "client": None, "server": None},
    ):
        answer = asyncio.run(guard._refusal(scope, Headers(scope=scope)))
        assert answer == "only the reverse proxy may use this address", scope
    without = DaemonGuard(None, "local-token")  # type: ignore[arg-type]
    scope = {**reset, "client": (TRAEFIK, 1), "server": ("172.19.0.1", 8701)}
    assert asyncio.run(without._refusal(scope, Headers(scope=scope))) == "unauthorised"


def test_traefiks_address_is_asked_of_docker_but_not_in_a_loop() -> None:
    from saltbox_ui.daemon.app import ProxyPeer

    now = [0.0]
    answers = [{"172.19.0.2"}, {"172.19.0.2"}, {"172.19.0.30"}, {"172.19.0.30"}]
    asked: list[str] = []

    def inspect(name: str) -> set[str]:
        asked.append(name)
        return answers[min(len(asked), len(answers)) - 1]

    peer = ProxyPeer(inspect=inspect, clock=lambda: now[0])
    assert peer("172.19.0.2") and peer("172.19.0.2") and asked == ["traefik"]
    for _ in range(50):  # a stranger knocking does not make it run docker each time
        assert not peer("172.19.0.9")
    assert len(asked) == 1
    now[0] = 6.0
    assert not peer("172.19.0.9") and len(asked) == 2
    now[0] = 12.0  # Traefik restarted with another address: known after a few seconds
    assert peer("172.19.0.30") and len(asked) == 3
    assert not peer("172.19.0.2")


def test_a_container_label_router_cannot_forge_the_admin(scratch_layout: Layout) -> None:
    """A Traefik router a container defines in its own labels can reach the daemon's
    service, add the proxy token and set Remote-User/Remote-Groups, skipping the sign-on.
    So the daemon confirms the session with the sign-on itself: the forged headers, with
    no session cookie, are refused; the same request with a real session is admitted."""
    scratch_layout.config_file("accounts").write_text("---\nuser:\n  name: seed\n")
    app = proxied_app(scratch_layout)  # the fake verifier honours only a cookie
    traefik = TestClient(app, base_url=BRIDGE, client=(TRAEFIK, 40000))
    proxy = {"Authorization": "Bearer proxy-token", "X-Forwarded-Host": "saltbox.example.com"}

    # The attack: proxy token (readable by every container) + forged admin headers, no cookie.
    forged = {**proxy, "Remote-User": "seed", "Remote-Groups": "admins"}
    assert traefik.get("/api/meta", headers=forged).status_code == 403
    assert traefik.post("/api/host/reboot", headers=forged).status_code == 403
    # A real session (a cookie the sign-on vouches for) is admitted; headers are not consulted.
    assert traefik.get("/api/meta", headers={**proxy, "Cookie": "who=seed"}).status_code == 200


def test_the_session_is_confirmed_with_the_sign_on() -> None:
    """SessionVerifier asks the provider, with the request cookie, who is signed in, and
    believes only its answer; it caches a positive, and fails closed when it is unreachable."""
    from saltbox_ui.daemon.app import SessionVerifier, VerifyUnavailable
    from saltbox_ui.appsetup.http import Response, SetupError

    calls: list[tuple[str, dict[str, str]]] = []

    def fetch(method: str, url: str, headers: dict[str, str], body: object) -> Response:
        calls.append((url, headers))
        if headers.get("Cookie") not in {"authelia_session=good", "authelia_session=bypass"}:
            return Response(401)
        if headers.get("X-Forwarded-Host") == "bypassed.example.com":
            return Response(200, headers={})  # authorised, but no identity (a bypass rule)
        return Response(200, headers={"remote-user": "seed", "remote-groups": "admins,users"})

    verify = SessionVerifier(
        "authelia", "remote-user", "remote-groups",
        addresses=lambda name: {"172.19.0.5"}, fetch=fetch,
    )
    fwd = {"X-Forwarded-Host": "saltbox.example.com"}
    assert verify("authelia_session=good", fwd) == ("seed", "admins,users")
    assert verify(None, fwd) is None and verify("authelia_session=bad", fwd) is None
    assert verify("authelia_session=bypass", {"X-Forwarded-Host": "bypassed.example.com"}) is None
    # the url is the container address and the verify path; the cookie is forwarded
    assert calls[0][0] == "http://172.19.0.5:9091/api/verify"
    # a positive is cached: asking again for the same cookie does not call the provider
    before = len(calls)
    assert verify("authelia_session=good", fwd) == ("seed", "admins,users")
    assert len(calls) == before
    # no address for the provider, or it cannot be reached: fail closed
    unresolved = SessionVerifier("authelia", "remote-user", "remote-groups", addresses=lambda n: set())
    with pytest.raises(VerifyUnavailable):
        unresolved("authelia_session=good", fwd)

    def refuse(method: str, url: str, headers: dict[str, str], body: object) -> Response:
        raise SetupError("connection refused")

    down = SessionVerifier(
        "authelia", "remote-user", "remote-groups", addresses=lambda n: {"172.19.0.5"}, fetch=refuse
    )
    with pytest.raises(VerifyUnavailable):
        down("authelia_session=good", fwd)


def test_an_old_route_is_given_the_proxy_token(scratch_layout: Layout, tmp_path: Path) -> None:
    from saltbox_ui.daemon.app import ensure_proxy_token, rotate_token
    from saltbox_ui.daemon.install import refresh_route, traefik_route

    scratch_layout.config_file("accounts").write_text("---\nuser:\n  domain: example.com\n")
    traefik = tmp_path / "traefik"
    traefik.mkdir()
    old = ensure_token(scratch_layout)
    route = traefik / "saltbox-ui.yml"
    route.write_text(traefik_route(domain="example.com", token=old, subdomain="box.home"))
    proxy = ensure_proxy_token(scratch_layout)
    assert proxy != old
    assert refresh_route(scratch_layout, proxy, traefik_dir=traefik) is True
    text = route.read_text()
    assert f'"Bearer {proxy}"' in text and old not in text
    assert "Host(`box.home.example.com`)" in text and "- authelia@docker" in text
    assert refresh_route(scratch_layout, proxy, traefik_dir=traefik) is False
    assert rotate_token(scratch_layout) != old and ensure_token(scratch_layout) != old
    # the route follows the sign-on the daemon reads
    assert refresh_route(scratch_layout, proxy, sso="authentik", traefik_dir=traefik) is True
    assert "- authentik@docker" in route.read_text()
    # a file that is not ours is left alone, and so is no file at all
    route.write_text("http: {}\n")
    assert refresh_route(scratch_layout, proxy, traefik_dir=traefik) is False
    assert route.read_text() == "http: {}\n"
    route.unlink()
    assert refresh_route(scratch_layout, proxy, traefik_dir=traefik) is False


def test_git_is_never_run_as_root_where_someone_else_has_a_say(monkeypatch) -> None:
    import shutil
    import tempfile

    from saltbox_ui import gitsafe

    if os.geteuid() != 0:
        assert gitsafe.owner_of(Path("/")) is None  # nothing to drop
        pytest.skip("needs root to tell owners apart")
    base = Path(tempfile.mkdtemp(prefix="sbx-own-", dir="/tmp"))
    try:
        base.chmod(0o755)
        repo = base / "repo"
        (repo / ".git").mkdir(parents=True)
        assert gitsafe.owner_of(repo) is None  # all root's: root may
        os.chown(repo, 65534, 65534)  # the checkout is theirs, .git is still root's
        assert gitsafe.owner_of(repo)[0] == 65534
        os.chown(repo, 0, 0)
        os.chown(repo / ".git", 65534, 65534)
        assert gitsafe.owner_of(repo)[0] == 65534
        os.chown(repo / ".git", 0, 0)
        os.chown(base, 65534, 65534)  # a folder above it is theirs: they can swap the lot
        assert gitsafe.owner_of(repo)[0] == 65534
        os.chown(base, 0, 0)
        base.chmod(0o777)  # root's, but anyone can rename what is in it
        assert gitsafe.owner_of(repo) == (65534, 65534)
        base.chmod(0o755)
        with pytest.raises(OSError):
            gitsafe.owner_of(base / "gone" / "repo")
        with pytest.raises(OSError):
            gitsafe.run(base / "gone" / "repo", ["status"])
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_reads_under_an_app_folder_follow_no_link_on_the_way(tmp_path: Path) -> None:
    """Only the last part of a path was checked. A container owns its folder, so it could
    make a folder inside it a link to someone else's, and have root read from there."""
    from saltbox_ui.appsetup.tasks import read_bazarr_settings

    appdata = tmp_path / "opt"
    private = tmp_path / "root-only"
    (appdata / "bazarr").mkdir(parents=True)
    private.mkdir()
    (private / "config.yaml").write_text("auth:\n  apikey: SOMEONE-ELSES\n")
    (private / "config.xml").write_text("<Config><ApiKey>otherappkey</ApiKey></Config>")
    (appdata / "bazarr" / "config").symlink_to(private)
    assert read_bazarr_settings(appdata / "bazarr", appdata) is None
    (appdata / "sonarr").symlink_to(private)  # the app's whole folder swapped for a link
    assert xml_api_key(appdata / "sonarr" / "config.xml", appdata) == ""
    with pytest.raises(safefs.UnsafeFile):
        safefs.read_text(appdata / "sonarr" / "config.xml", base=appdata)
    with pytest.raises(safefs.UnsafeFile):
        safefs.read_text(private / "config.xml", base=appdata)  # not under the base at all
    (appdata / "radarr").mkdir()
    (appdata / "radarr" / "config.xml").write_text("<Config><ApiKey>radarrkey1</ApiKey></Config>")
    assert xml_api_key(appdata / "radarr" / "config.xml", appdata) == "radarrkey1"
    # a second name for a file (a hard link) is refused: it may be someone else's file
    os.link(private / "config.xml", appdata / "radarr" / "linked.xml")
    assert xml_api_key(appdata / "radarr" / "linked.xml", appdata) == ""


def test_a_file_moved_into_recyclarrs_folder_is_not_copied_out(recyclarr_layout: Layout) -> None:
    """A root-only file can be moved by whoever owns the folder it is in, even unread.
    Moved to recyclarr.yml, saving used to copy it into a backup they could read."""
    folder = recyclarr_layout.appdata / "recyclarr"
    moved = folder / "recyclarr.yml"
    moved.write_text('Authorization: "Bearer THE-TOKEN"\n')
    moved.chmod(0o600)
    inode = moved.stat().st_ino
    result = recyclarr.write_config(recyclarr_layout, found(), {"sonarr": [WEB_1080]})
    backup = Path(result["backup"])
    # the same file under another name: as private as it was, whoever owns it
    assert backup.stat().st_ino == inode and stat.S_IMODE(backup.stat().st_mode) == 0o600
    assert backup.stat().st_nlink == 1
    assert (folder / "recyclarr.yml").read_text().startswith(recyclarr.MARKER)


def test_media_folders_are_not_made_through_a_link(tmp_path: Path) -> None:
    from saltbox_ui.appsetup import owned_dir_maker

    unionfs, elsewhere = tmp_path / "unionfs", tmp_path / "elsewhere"
    unionfs.mkdir()
    elsewhere.mkdir()
    make = owned_dir_maker("nobody")
    make(unionfs / "Media" / "TV")
    assert (unionfs / "Media" / "TV").is_dir()
    if os.geteuid() == 0:
        assert (unionfs / "Media").stat().st_uid == 65534
        assert unionfs.stat().st_uid == 0  # only what it created is handed over
    (unionfs / "downloads").symlink_to(elsewhere)
    with pytest.raises(OSError, match="not a plain folder"):
        make(unionfs / "downloads" / "torrents" / "completed")
    assert list(elsewhere.iterdir()) == []
    make(unionfs / "Media" / "TV")  # existing folders are fine, and left as they are


def test_the_page_may_reach_its_own_websocket(scratch_layout: Layout) -> None:
    client = TestClient(
        create_app(services=Services(scratch_layout)), base_url="http://box.example:8443"
    )
    policy = client.get("/api/meta").headers["content-security-policy"]
    assert "connect-src 'self' ws://box.example:8443 wss://box.example:8443;" in policy
    odd = client.get("/api/meta", headers={"Host": "evil.example; script-src *"})
    assert "connect-src 'self';" in odd.headers["content-security-policy"]
    assert "script-src *" not in odd.headers["content-security-policy"]


def test_the_update_refuses_what_it_cannot_check(monkeypatch, capfd, tmp_path: Path) -> None:
    import subprocess
    import sys
    import urllib.error

    from saltbox_ui import selfupdate

    version = "99.0.0"
    answer = {
        "tag_name": f"v{version}",
        "assets": [{"browser_download_url": selfupdate.wheel_url(version), "digest": None}],
    }
    ran: list[list[str]] = []
    monkeypatch.setattr(selfupdate, "step", lambda args, **options: ran.append(args) or 0)
    monkeypatch.setattr(
        selfupdate, "download", lambda url, target: target.write_bytes(b"x") and "ab" * 32
    )
    monkeypatch.setattr(selfupdate, "_has_pip", lambda: True)
    assert selfupdate.update_wheel(lambda url: answer) == 1
    assert ran == [] and "no checksum" in capfd.readouterr().out
    answer["assets"][0]["digest"] = "SHA256:" + "AB" * 32  # GitHub's, in capitals
    assert selfupdate.update_wheel(lambda url: answer) == 0 and len(ran) == 1

    handler = selfupdate._HttpsOnly()
    with pytest.raises(urllib.error.URLError, match="away from https"):
        handler.redirect_request(None, None, 302, "Found", {}, "http://objects.example/x.whl")

    # The update's own Python does not pick up code from the folder it is started in.
    planted = tmp_path / "saltbox_ui"
    planted.mkdir()
    (planted / "__init__.py").write_text("raise SystemExit('planted module ran')\n")
    done = subprocess.run(
        [sys.executable, "-I", "-c", "import saltbox_ui; print(saltbox_ui.__file__)"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 0 and str(tmp_path) not in done.stdout, done.stderr
