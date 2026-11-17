"""Recurring errors in container logs, and the ones that come with an explanation."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from saltbox_ui import logcheck
from saltbox_ui.daemon import Services, create_app
from saltbox_ui.daemon import routes as daemon_routes
from saltbox_ui.layout import Layout

T = "2026-11-13T14:46:35.123456789Z "
AUTOSCAN = (
    T
    + "Nov 13 14:46:35 ERR Anchor files unavailable, retaining queued scans "
    + 'missing_anchors=["/mnt/unionfs/mounted.bin"]'
)
BAZARR = (
    "2026-11-13T07:06:22.300000000Z 2026-11-13 07:06:22,275 - root                             "
    "(7d2c7209db30) :  ERROR (utils:55) - BAZARR Error ('.iso' is not a valid video extension) "
    "trying to get video information for this file: "
    "/mnt/unionfs/Media/Movies/Some Film (1999)/Some Film (1999).iso"
)


def test_error_lines_are_told_from_ordinary_ones() -> None:
    for line in (
        "Nov 13 14:46:35 ERR Anchor files unavailable",
        "2026-11-13 07:06:22,275 - root :  ERROR (utils:55) - BAZARR Error",
        "[Error] DownloadedEpisodesImportService: Import failed",
        'time="2026-11-13" level=error msg="dial tcp: connection refused"',
        '{"level":"error","msg":"boom"}',
        "panic: runtime error: index out of range",
        "Traceback (most recent call last):",
        "CRITICAL: cannot continue",
    ):
        assert logcheck.is_error(line), line
    for line in (
        "[Info] RssSyncService: RSS Sync Completed",
        "INF Scan moved to target",
        "0 errors, 12 files checked",
        "GET /api/v3/error-pages 200",
        "level=info msg=started",
    ):
        assert not logcheck.is_error(line), line


def test_signature_ignores_what_changes_between_occurrences() -> None:
    first = logcheck.signature(
        "[Error] Import failed for /downloads/Show.S01E02/file.mkv after 3 tries (id 9f8e7d6c5b4a)"
    )
    second = logcheck.signature(
        "[Error] Import failed for /downloads/Other.S09E11/x.mkv after 12 tries (id 0a1b2c3d4e5f)"
    )
    assert first == second == "[Error] Import failed for <path> after # tries (id <id>)"
    assert logcheck.clean("\x1b[31mNov 13 14:46:35 ERR boom\x1b[0m") == "ERR boom"
    assert logcheck.clean("2026-11-13 07:06:22,275 - root : ERROR x") == "root : ERROR x"
    assert logcheck.split_time(T + "hello") == ("2026-11-13T14:46:35Z", "hello")


AUTHELIA = (
    T
    + 'time="2026-11-13T15:16:34-05:00" level=error msg="Request timeout occurred while handling '
    + 'request from client." error="read tcp 172.19.0.13:9091->172.19.0.15:49418: i/o timeout" '
    + "method=GET path=/ remote_ip=172.19.0.15 status_code=408"
)
FLARESOLVERR = T + "ERROR Error: Error solving the challenge. Timeout after 60.0 seconds."


def test_known_errors_come_with_what_they_mean_and_what_to_do() -> None:
    found = logcheck.analyse("autoscan", [AUTOSCAN] * 40 + [T + "INF Scan moved to target"])
    assert len(found) == 1
    group = found[0]
    assert group["count"] == 40 and group["last_seen"] == "2026-11-13T14:46:35Z"
    assert group["message"].startswith("ERR Anchor files unavailable")
    assert group["rule"]["id"] == "autoscan-anchor" and group["rule"]["severity"] == "problem"
    assert group["key"] == "log:autoscan:autoscan-anchor"

    # Seen once is enough for a known one; each file is a different line but one finding,
    # and the files are listed once each.
    other = BAZARR.replace("Some Film (1999)", "Another Film (2001)")
    bazarr = logcheck.analyse("bazarr", [BAZARR, other, BAZARR])
    assert [(g["rule"]["id"], g["count"]) for g in bazarr] == [("bazarr-not-a-video", 3)]
    assert bazarr[0]["rule"]["severity"] == "notice"
    assert bazarr[0]["subjects"] == [
        "/mnt/unionfs/Media/Movies/Some Film (1999)/Some Film (1999).iso",
        "/mnt/unionfs/Media/Movies/Another Film (2001)/Another Film (2001).iso",
    ]
    assert bazarr[0]["more_subjects"] == 0

    # A rule for one app does not fire on another app's log.
    assert logcheck.analyse("sonarr", [AUTOSCAN]) == []

    # The two that were only "other errors" on the real server.
    authelia = logcheck.analyse("authelia", [AUTHELIA] * 5)
    assert authelia[0]["rule"]["id"] == "authelia-idle-timeout"
    assert authelia[0]["rule"]["severity"] == "notice" and authelia[0]["count"] == 5
    solver = logcheck.analyse("flaresolverr", [FLARESOLVERR] * 10)
    assert solver[0]["rule"]["id"] == "flaresolverr-timeout"
    assert solver[0]["rule"]["severity"] == "minor"
    assert "several hundred MB" in solver[0]["rule"]["meaning"]


def test_advice_and_the_fix_on_offer_depend_on_cloud_storage(tmp_path: Path) -> None:
    unionfs, local = tmp_path / "unionfs", tmp_path / "local"
    local.mkdir()
    line = AUTOSCAN.replace("/mnt/unionfs/mounted.bin", f"{unionfs}/mounted.bin")
    roots = (unionfs, local)

    # With cloud storage a missing anchor means a mount is down: no button to paper over it.
    cloud = logcheck.analyse("autoscan", [line], cloud=True, roots=roots)[0]
    assert cloud["action"] is None
    assert "a remote that is not mounted" in cloud["rule"]["fix"][0]

    plain = logcheck.analyse("autoscan", [line], cloud=False, roots=roots)[0]
    assert "no cloud storage set up" in plain["rule"]["fix"][0]
    assert plain["action"] == {
        "id": "create-anchor",
        "label": "Create the anchor file",
        "detail": f"Creates an empty {local}/mounted.bin, owned by whoever owns {local}.",
        "targets": [f"{local}/mounted.bin"],
    }
    assert logcheck.create_anchors(plain["action"]["targets"], local) == [f"{local}/mounted.bin"]
    assert (local / "mounted.bin").read_text() == ""

    # An anchor somewhere else, or in a folder that does not exist, gets no button.
    elsewhere = AUTOSCAN.replace("/mnt/unionfs/mounted.bin", "/etc/passwd")
    assert logcheck.analyse("autoscan", [elsewhere], cloud=False, roots=roots)[0]["action"] is None
    deep = AUTOSCAN.replace("/mnt/unionfs/mounted.bin", f"{unionfs}/missing/mounted.bin")
    assert logcheck.analyse("autoscan", [deep], cloud=False, roots=roots)[0]["action"] is None


def test_create_anchors_only_writes_inside_the_local_folder(tmp_path: Path) -> None:
    local = tmp_path / "local"
    (local / "Media").mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (local / "link").symlink_to(outside / "target")
    for bad in (
        [],
        [str(outside / "mounted.bin")],
        [f"{local}/../outside/mounted.bin"],
        [str(local)],
        [f"{local}/nowhere/mounted.bin"],
        [f"{local}/link"],
        [f"{local}/mounted.bin", "/etc/cron.d/x"],
    ):
        with pytest.raises(ValueError):
            logcheck.create_anchors(bad, local)
    assert list(outside.iterdir()) == [] and not (local / "mounted.bin").exists()
    # Only a file at the top of the local folder: nothing deeper, on a log line's say-so.
    with pytest.raises(ValueError):
        logcheck.create_anchors([f"{local}/Media/mounted.bin"], local)
    assert logcheck.create_anchors([f"{local}/mounted.bin"], local) == [f"{local}/mounted.bin"]


def test_anchors_never_follow_a_link_a_container_planted(tmp_path: Path) -> None:
    """The local media folder is written by containers. A link there, as the anchor itself
    or as a folder on the way to it, must not make root create or hand over a file elsewhere."""
    unionfs, local, etc = tmp_path / "unionfs", tmp_path / "local", tmp_path / "etc"
    for folder in (unionfs, local, etc):
        folder.mkdir()
    shadow = etc / "shadow"
    shadow.write_text("root:hash\n")
    shadow.chmod(0o600)
    (local / "a").symlink_to(etc)
    line = AUTOSCAN.replace(
        '"/mnt/unionfs/mounted.bin"', f'"{unionfs}/a/shadow","{unionfs}/a/cron.d"'
    )
    assert line != AUTOSCAN
    found = logcheck.analyse("autoscan", [line], cloud=False, roots=(unionfs, local))
    assert found[0]["action"] is None
    with pytest.raises(ValueError):
        logcheck.create_anchors([f"{local}/a/shadow"], local)

    (local / "mounted.bin").symlink_to(etc / "created-through-a-link")
    with pytest.raises(ValueError, match="not a file"):
        logcheck.create_anchors([f"{local}/mounted.bin"], local)
    assert sorted(p.name for p in etc.iterdir()) == ["shadow"]
    assert shadow.read_text() == "root:hash\n" and shadow.stat().st_mode & 0o777 == 0o600

    # A file that is already there is left alone: not truncated, not handed over.
    (local / "mounted.bin").unlink()
    (local / "mounted.bin").write_text("kept")
    (local / "mounted.bin").chmod(0o600)
    assert logcheck.create_anchors([f"{local}/mounted.bin"], local) == [f"{local}/mounted.bin"]
    assert (local / "mounted.bin").read_text() == "kept"
    assert (local / "mounted.bin").stat().st_mode & 0o777 == 0o600


def test_unknown_errors_are_listed_only_when_they_recur() -> None:
    def failed(n: int) -> str:
        return f"{T}[Error] ImportService: Import failed for /downloads/file{n}.mkv"

    once = logcheck.analyse("sonarr", [failed(1), T + "[Info] fine"])
    assert once == []
    lines = [failed(n) for n in range(5)] + [T + "[Error] Something else"]
    lines += [T + "[Error] No space left on device"]
    found = logcheck.analyse("sonarr", lines)
    assert [(g["rule"] and g["rule"]["id"], g["count"]) for g in found] == [
        ("disk-full", 1),  # known ones first
        (None, 5),
    ]
    assert found[1]["message"] == "[Error] ImportService: Import failed for /downloads/file4.mkv"
    # General rules need an error line: a note about permissions at info level is not one.
    assert logcheck.analyse("sonarr", [T + "[Info] Permission denied is handled"]) == []


def container(name: str, managed: bool = True) -> dict:
    return {
        "name": name,
        "status": "running",
        "health": None,
        "managed": managed,
        "image_ref": f"example/{name}",
        "network_mode": "saltbox",
        "urls": [],
        "labels": {},
    }


def test_problems_route_scans_saltbox_containers_and_caches(
    scratch_layout: Layout, fake_sb: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        daemon_routes,
        "_containers_or_none",
        lambda: [container("autoscan"), container("sonarr"), container("stray", managed=False)],
    )
    calls: list[str] = []

    def docker(args: list[str]) -> tuple[int, str]:
        assert args[:6] == ["docker", "logs", "--since", "24h", "--tail", "3000"]
        calls.append(args[-1])
        if args[-1] == "autoscan":
            return 0, "\n".join([AUTOSCAN] * 3)
        return 0, T + "[Info] all good\n"

    services = Services(scratch_layout, log_runner=docker)
    client = TestClient(create_app(services=services))
    data = client.get("/api/problems").json()
    assert sorted(calls) == ["autoscan", "sonarr"]  # not the container Saltbox does not manage
    assert data["available"] is True and data["scanned"] == 2 and data["window"] == "24h"
    assert [c["name"] for c in data["containers"]] == ["autoscan"]
    assert data["containers"][0]["app"] == {
        "repo": "saltbox",
        "role": "autoscan",
        "title": "Autoscan",
    }
    group = data["containers"][0]["groups"][0]
    assert group["rule"]["id"] == "autoscan-anchor" and group["dismissed"] is False
    client.get("/api/problems")
    assert len(calls) == 2  # kept for ten minutes
    client.get("/api/problems?refresh=true")
    assert len(calls) == 4

    # Dismissed on the server, so for every browser; the cached scan shows it at once.
    client.post("/api/dismissals", json={"key": group["key"], "label": "Autoscan"})
    assert client.get("/api/problems").json()["containers"][0]["groups"][0]["dismissed"] is True
    assert len(calls) == 4


def test_the_anchor_fix_is_offered_and_applied_only_without_cloud_storage(
    scratch_layout: Layout, fake_sb: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    unionfs, local = tmp_path / "unionfs", tmp_path / "local"
    local.mkdir()
    line = AUTOSCAN.replace("/mnt/unionfs/mounted.bin", f"{unionfs}/mounted.bin")
    monkeypatch.setattr(daemon_routes, "_containers_or_none", lambda: [container("autoscan")])
    logs = {"autoscan": "\n".join([line] * 3)}
    services = Services(
        scratch_layout,
        log_runner=lambda args: (0, logs["autoscan"]),
        storage_roots=(unionfs, local),
    )
    client = TestClient(create_app(services=services))
    settings = scratch_layout.config_file("settings")
    default = (scratch_layout.repo / "defaults" / "settings.yml.default").read_text()

    # Saltbox's default settings have a cloud remote: no button, and the fix is refused.
    settings.write_text(default)
    data = client.get("/api/problems").json()
    group = data["containers"][0]["groups"][0]
    assert data["cloud_storage"] is True and group["action"] is None
    assert client.post("/api/problems/fix", json={"key": group["key"]}).status_code == 409

    # Local disks only.
    settings.write_text(default.replace("  enabled: yes\n  remotes:", "  enabled: no\n  remotes:"))
    data = client.get("/api/problems?refresh=true").json()
    group = data["containers"][0]["groups"][0]
    assert data["cloud_storage"] is False and group["action"]["label"] == "Create the anchor file"
    assert client.post("/api/problems/fix", json={"key": "log:nope:x"}).status_code == 409
    logs["autoscan"] = T + "INF Scan moved to target"  # what Autoscan logs once the file exists
    fixed = client.post("/api/problems/fix", json={"key": group["key"]}).json()
    assert fixed["created"] == [f"{local}/mounted.bin"] and (local / "mounted.bin").is_file()
    assert fixed["problems"]["containers"] == []  # scanned again straight away


def test_issues_marked_fixed_stay_away_unless_they_happen_again(
    scratch_layout: Layout, fake_sb: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Marked fixed: out of sight while nothing newer turns up, back (and saying so) when
    it does. Not seen for hours: quiet. Dismissed: away for good."""
    import time as time_module

    monkeypatch.setattr(daemon_routes, "_containers_or_none", lambda: [container("autoscan")])

    def stamp(seconds_ago: float) -> str:
        moment = time_module.gmtime(time_module.time() - seconds_ago)
        return time_module.strftime("%Y-%m-%dT%H:%M:%S.123456789Z ", moment)

    last = {"ago": 60.0}

    def docker(args: list[str]) -> tuple[int, str]:
        line = AUTOSCAN.replace(T, stamp(last["ago"]))
        return 0, "\n".join([AUTOSCAN.replace(T, stamp(3000)), line])

    services = Services(scratch_layout, log_runner=docker)
    client = TestClient(create_app(services=services))

    def group() -> dict:
        data = client.get("/api/problems?refresh=true").json()
        return data["containers"][0]["groups"][0]

    first = group()
    assert first["fixed"] is False and first["quiet"] is False and first["returned"] is False
    assert abs(first["last_seen_at"] - (time_module.time() - 60)) < 5

    marked = client.post(
        "/api/dismissals", json={"key": first["key"], "label": "x", "kind": "fixed"}
    )
    assert marked.status_code == 200 and marked.json()["kind"] == "fixed"
    after = group()
    assert after["fixed"] is True and after["dismissed"] is False and after["returned"] is False
    assert after["fixed_at"] > after["last_seen_at"]

    last["ago"] = -5.0  # it happens again, after the mark
    again = group()
    assert again["returned"] is True and again["fixed"] is False and again["quiet"] is False

    # Marked fixed again, then it goes quiet: still fixed, and not counted as "came back".
    client.post("/api/dismissals", json={"key": first["key"], "label": "x", "kind": "fixed"})
    last["ago"] = 7 * 3600.0
    client.post("/api/dismissals/restore", json={"key": first["key"]})
    quiet = group()
    assert quiet["quiet"] is True and quiet["fixed"] is False and quiet["returned"] is False

    # A drive's dismissal is unaffected by any of this.
    client.post("/api/dismissals", json={"key": first["key"], "label": "x"})
    assert group()["dismissed"] is True
    assert (
        client.post(
            "/api/dismissals", json={"key": first["key"], "label": "x", "kind": "gone"}
        ).status_code
        == 422
    )


def test_a_fix_this_app_made_marks_the_issue_fixed(
    scratch_layout: Layout, fake_sb: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    unionfs, local = tmp_path / "unionfs", tmp_path / "local"
    unionfs.mkdir()
    local.mkdir()
    monkeypatch.setattr(daemon_routes, "_containers_or_none", lambda: [container("autoscan")])
    monkeypatch.setattr(daemon_routes, "_has_cloud_storage", lambda svc: False)
    line = AUTOSCAN.replace("/mnt/unionfs/mounted.bin", f"{unionfs}/mounted.bin")
    services = Services(
        scratch_layout,
        log_runner=lambda args: (0, "\n".join([line] * 3)),
        storage_roots=(unionfs, local),
    )
    client = TestClient(create_app(services=services))
    group = client.get("/api/problems").json()["containers"][0]["groups"][0]
    fixed = client.post("/api/problems/fix", json={"key": group["key"]}).json()
    # The log still holds the old lines (the day is read again), but they predate the fix.
    after = fixed["problems"]["containers"][0]["groups"][0]
    assert after["fixed"] is True and after["returned"] is False
    assert services.dismissals.kind(group["key"]) == "fixed"


def test_docker_timestamps_are_read_as_moments() -> None:
    assert logcheck.seen_at("2026-11-13T14:46:35.123456789Z") == pytest.approx(1794581195.123456)
    assert logcheck.seen_at("2026-11-13T14:46:35Z") == 1794581195.0
    assert logcheck.seen_at("") is None and logcheck.seen_at("yesterday") is None


def test_a_long_log_line_does_not_stall_the_scan() -> None:
    """A container can print one line of megabytes with no newline; the anchor scan of it
    must stay fast (it was quadratic), and a well-formed anchor list must still parse."""
    import time

    line = T + "ERR Anchor files unavailable " + "missing_anchors=[" * 20000
    start = time.monotonic()
    logcheck.analyse("autoscan", [line], cloud=False)
    assert time.monotonic() - start < 1.0
    good = T + 'ERR Anchor files unavailable missing_anchors=["mounted.bin"]'
    _, text = logcheck.split_time(good)
    assert logcheck.missing_anchors(logcheck.clean(text)) == ["mounted.bin"]


def test_a_secret_near_the_cut_is_masked_before_the_line_is_cut(
    scratch_layout: Layout, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The latest line was cut to its display length before secrets were masked, so a
    secret that straddled the cut showed its head. It is masked first, then cut."""
    monkeypatch.setattr(daemon_routes, "_containers_or_none", lambda: [container("sonarr")])
    secret = "Zqx7Z9Km2Vb4Rn8"  # shares no prefix with the words around it in the line
    scratch_layout.config_file("accounts").write_text(
        f"---\nuser:\n  name: seed\n  pass: {secret}\n"
    )
    body = "ERROR failed to connect to the database " + "x" * 352 + " token=" + secret + " end"
    services = Services(scratch_layout, log_runner=lambda args: (0, "\n".join([T + body] * 3)))
    client = TestClient(create_app(services=services))
    message = client.get("/api/problems").json()["containers"][0]["groups"][0]["message"]
    assert secret not in message
    for cut in range(4, len(secret)):
        assert secret[:cut] not in message, f"leaked a {cut}-char prefix of the secret"
