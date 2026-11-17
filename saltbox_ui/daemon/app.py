"""FastAPI application factory."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import hmac
import ipaddress
import logging
import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from starlette.datastructures import Headers
from starlette.types import ASGIApp, Receive, Scope, Send
from starlette.websockets import WebSocketClose

from .. import __version__, safefs, selfupdate
from ..catalog import Catalog
from ..config import ConfigService, Inventory
from ..layout import Layout
from ..runner import SbRunner
from ..yamlio import DuplicateKeys
from .automation import AutomationService
from .dismissals import Dismissals
from .jobs import JobManager
from .routes import router
from .updates import UpdateService

log = logging.getLogger("saltbox_ui.daemon")

TOKEN_FILENAME = "api-token"
PROXY_TOKEN_FILENAME = "proxy-token"


# When this process started: a page that sees it change knows the daemon restarted.
STARTED_AT = time.time()


@dataclass
class Services:
    """Everything the routes need, built once per process."""

    layout: Layout
    config: ConfigService = field(init=False)
    inventory: Inventory = field(init=False)
    catalog: Catalog = field(init=False)
    runner: SbRunner = field(init=False)
    jobs: JobManager = field(init=False)
    token: str | None = None
    # What the reverse proxy (Traefik) presents. It is not the token above: Traefik shows
    # its configuration to every container, so what it holds is not a secret from them.
    proxy_token: str | None = None
    # Whether an address is the reverse proxy's own (tests replace it); None: not checked.
    proxy_peer: Callable[[str], bool] | None = None
    web_dir: Path | None = None
    tailscale_socket: str | None = None
    # The root-only Unix socket: the one listener where no token is asked for.
    root_socket: str | None = None
    admin_check: bool = True
    # Which single sign-on the Traefik route sends people through. On a proxied request
    # the daemon asks it, with the request's cookie, who signed in, rather than trusting
    # the headers (a container's Traefik labels can set those). ``verify_session`` off
    # falls back to trusting the headers; ``sso_container`` names the provider's container
    # when it is not the default (``authelia``/``authentik``).
    sso: str = "authelia"
    verify_session: bool = True
    sso_container: str | None = None
    app_setup_factory: Callable[[Layout], Any] | None = None
    # Restarting the server, and the file Ubuntu leaves when it needs one (tests replace both).
    reboot: Callable[[], None] | None = None
    reboot_flag: Path = Path("/var/run/reboot-required")
    # Seconds since the server booted (tests replace it).
    uptime: Callable[[], int | None] | None = None
    # Updating this app: how it is installed and what is newer, the job's command, and
    # restarting the daemon afterwards (tests replace all three).
    self_status: Callable[[], dict[str, Any]] | None = None
    self_command: list[str] | None = None
    restart: Callable[[], None] | None = None
    # The Claude Code command and how to run it, for explanations (tests replace it).
    claude: tuple[str, Callable[[list[str], str | None, float], tuple[int, str, str]]] | None = None
    # The merged and the local media folders, for fixes the problem finder offers.
    storage_roots: tuple[Path, Path] = (Path("/mnt/unionfs"), Path("/mnt/local"))
    # Runs ``docker logs`` for the problem finder (tests replace it).
    log_runner: Callable[[list[str]], tuple[int, str]] | None = None
    updates: UpdateService = field(init=False)
    automation: AutomationService = field(init=False)
    dismissals: Dismissals = field(init=False)

    def __post_init__(self) -> None:
        self.config = ConfigService(self.layout)
        self.inventory = Inventory(self.layout.inventory)
        self.catalog = Catalog(self.layout)
        self.runner = SbRunner(self.layout)
        self.jobs = JobManager(self.runner)
        self.updates = UpdateService(self.layout)
        # An install job pulls new images; check again once it has finished.
        self.jobs.on_finish.append(
            lambda job: self.updates.refresh() if job.kind in {"install", "update"} else None
        )
        self.automation = AutomationService(self)
        self.jobs.on_finish.append(lambda job: self.automation.on_job_finished(job))
        self.dismissals = Dismissals(self.layout.state_dir / "dismissed.json")
        # What this process loaded, to tell after an update whether a restart is due.
        source = selfupdate.source_dir()
        self.loaded_commit = selfupdate.head_commit(source) if source is not None else None
        self.jobs.on_finish.append(self._after_self_update)
        # A Recyclarr sync creates profiles: what Sonarr and Radarr hold is worth reading again.
        self.jobs.on_finish.append(
            lambda job: (
                self.__dict__.pop("_recyclarr_usage", None) if job.kind == "recyclarr" else None
            )
        )

    def _after_self_update(self, job: Any) -> None:
        """Once the app has updated itself, restart to run the new code."""
        if job.kind != "selfupdate" or job.status != "succeeded":
            return
        self.__dict__.pop("_self", None)
        if self.restart is not None:
            self.restart()
        elif selfupdate.needs_restart(self.loaded_commit):
            selfupdate.restart_daemon()


class TailscaleOwner:
    """The login of the Tailscale account that owns this server, cached five minutes."""

    TTL_SECONDS = 300.0

    def __init__(self, status: Callable[[], dict[str, Any]] | None = None):
        from ..state.system import tailscale_status

        self.status = status or tailscale_status
        self._cached: tuple[float, str | None] | None = None

    def __call__(self) -> str | None:
        now = time.monotonic()
        if self._cached is not None and now - self._cached[0] < self.TTL_SECONDS:
            return self._cached[1]
        owner = str(self.status().get("owner") or "").strip().lower() or None
        self._cached = (now, owner)
        return owner


SSO_HEADERS = {
    "authelia": ("remote-user", "remote-groups"),
    "authentik": ("x-authentik-username", "x-authentik-groups"),
}


def listener_socket(scope: Scope) -> str | None:
    """The path of the Unix socket a connection arrived on; ``None`` for TCP, or when unknown.

    uvicorn reports the listening address as ``scope["server"]``: ``(path, None)`` for a
    Unix socket, ``(address, port)`` for TCP. The peer cannot influence it.
    """
    server = scope.get("server")
    if (
        isinstance(server, tuple | list)
        and len(server) == 2
        and server[1] is None
        and isinstance(server[0], str)
        and server[0]
    ):
        return server[0]
    return None


class ProxyPeer:
    """Whether an address belongs to the reverse proxy's container, asked of Docker.

    The answer is cached; an address that is not known is looked up again, but no more
    than once every few seconds, so a restarted Traefik is recognised and a stranger
    cannot make the daemon run ``docker inspect`` in a loop.
    """

    TTL_SECONDS = 60.0
    RETRY_SECONDS = 5.0

    def __init__(
        self,
        container: str = "traefik",
        inspect: Callable[[str], set[str]] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.container = container
        self.inspect = inspect or container_addresses
        self.clock = clock
        self._addresses: set[str] = set()
        self._at: float | None = None
        self._lock = threading.Lock()

    def __call__(self, peer: str) -> bool:
        with self._lock:
            now = self.clock()
            age = None if self._at is None else now - self._at
            stale = age is None or age > self.TTL_SECONDS
            unknown = peer not in self._addresses and (age is None or age > self.RETRY_SECONDS)
            if stale or unknown:
                self._addresses, self._at = self.inspect(self.container), now
                if peer not in self._addresses:
                    # Said once per lookup, so the reason is in the journal when the web
                    # app answers 403 and it is not an intruder but a setup unlike ours.
                    log.warning(
                        "refused the proxy token from %s: Docker gives the %s container %s",
                        peer,
                        self.container,
                        ", ".join(sorted(self._addresses)) or "no address",
                    )
            return peer in self._addresses


def container_addresses(name: str) -> set[str]:
    """Every address Docker has given the container called ``name``; empty when unknown."""
    import shutil
    import subprocess

    if shutil.which("docker") is None:
        return set()
    template = "{{range .NetworkSettings.Networks}}{{.IPAddress}} {{.GlobalIPv6Address}} {{end}}"
    try:
        done = subprocess.run(
            ["docker", "inspect", "-f", template, "--", name],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return set()
    found: set[str] = set()
    for address in done.stdout.split() if done.returncode == 0 else []:
        with contextlib.suppress(ValueError):
            found.add(str(ipaddress.ip_address(address)))
    return found


# The single sign-on's own verify endpoint: (default container name, port, path). The
# daemon asks it, with the request's cookie, who is signed in, rather than trusting the
# Remote-* headers, which a Traefik router defined by a container's labels can set.
SSO_VERIFY = {
    "authelia": ("authelia", 9091, "/api/verify"),
    "authentik": ("authentik", 9000, "/outpost.goauthentik.io/auth/traefik"),
}


class VerifyUnavailable(RuntimeError):
    """The single sign-on could not be reached to confirm a session."""


class SessionVerifier:
    """Confirm who signed in by asking the sign-on, not by trusting request headers.

    A Traefik router defined by a container's own labels can reach the daemon's
    service, add the proxy token and set ``Remote-User``/``Remote-Groups`` itself,
    skipping the sign-on. So on a proxied request the daemon hands the request's
    session cookie to the provider's verify endpoint and believes only the identity
    that comes back: a router cannot produce the admin's cookie. The provider is
    reached at its container address on the Docker network (never by name, never
    over DNS), through the same guarded client the apps use. A positive answer is
    cached for a short while, keyed by the cookie, so the provider is not asked on
    every request.
    """

    TTL_SECONDS = 60.0
    MAX_CACHE = 512

    def __init__(
        self,
        provider: str,
        user_header: str,
        group_header: str,
        *,
        container: str | None = None,
        addresses: Callable[[str], set[str]] = container_addresses,
        fetch: Callable[..., Any] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.container, self.port, self.path = SSO_VERIFY[provider]
        if container:
            self.container = container
        self.user_header = user_header
        self.group_header = group_header
        self.addresses = addresses
        self._fetch = fetch
        self.clock = clock
        self._cache: dict[str, tuple[float, tuple[str, str]]] = {}
        self._lock = threading.Lock()

    def __call__(self, cookie: str | None, forwarded: dict[str, str]) -> tuple[str, str] | None:
        """The ``(user, groups)`` the provider vouches for, or ``None`` when nobody is."""
        if not cookie:
            return None
        key = hashlib.sha256(cookie.encode("utf-8", "ignore")).hexdigest()
        now = self.clock()
        with self._lock:
            hit = self._cache.get(key)
            if hit is not None and hit[0] > now:
                return hit[1]
        identity = self._ask(cookie, forwarded)  # may raise VerifyUnavailable
        if identity is not None:
            with self._lock:
                if len(self._cache) >= self.MAX_CACHE:
                    self._cache.clear()
                self._cache[key] = (now + self.TTL_SECONDS, identity)
        return identity

    def _ask(self, cookie: str, forwarded: dict[str, str]) -> tuple[str, str] | None:
        from ..appsetup.http import SetupError
        from ..appsetup.http import fetch as default_fetch

        fetch = self._fetch or default_fetch
        addresses = sorted(self.addresses(self.container))
        if not addresses:
            raise VerifyUnavailable(
                f"the {self.container} container has no address on the Docker network"
            )
        headers = {"Cookie": cookie, **forwarded}
        last: Exception | None = None
        for address in addresses:
            host = f"[{address}]" if ":" in address else address
            url = f"http://{host}:{self.port}{self.path}"
            try:
                response = fetch("GET", url, dict(headers), None)
            except SetupError as error:
                last = error
                continue
            if response.status == 200:
                user = (response.headers.get(self.user_header) or "").strip()
                if not user:
                    return None  # authorised, but a bypass rule, so nobody is signed in
                return user, response.headers.get(self.group_header) or ""
            if response.status in (401, 403):
                return None
            last = SetupError(f"{self.container} answered {response.status}")
        raise VerifyUnavailable(
            f"could not reach the {self.container} container to confirm the session"
            + (f": {last}" if last else "")
        )


def is_loopback(host: object) -> bool:
    try:
        return ipaddress.ip_address(str(host)).is_loopback
    except ValueError:
        return False


class DaemonGuard:
    """Decide who may use the API, for HTTP and WebSocket alike.

    Which listener a connection arrived on decides what is asked of it:

    * the root-only Unix socket: nothing, only root can open it;
    * the socket ``tailscale serve`` proxies to: the tailnet login of this server's owner;
    * a loopback address (127.0.0.1): the daemon token, which only root can read. It is
      for tools on the server itself;
    * any other address, which is how the reverse proxy reaches the daemon (the Docker
      bridge): the proxy token, from the proxy's own address, and a signed-in admin.
      A connection whose listener cannot be told is treated as this last kind.

    The listener is taken from ``scope["server"]`` and compared with what the daemon
    bound. It is never inferred from a missing client address: a TCP connection the
    peer has already reset has none either, and must not pass for the root socket.

    The proxy token is a different secret from the daemon token because it is not much
    of one. Traefik holds it in its configuration, and Traefik's own API shows that
    configuration to every container on its network (and to anyone on the server, and
    to anyone signed in who opens its dashboard). So presenting it proves little: it is
    only accepted from the address Docker gave the Traefik container, on the address
    Traefik uses, and never without the sign-on's word for who is asking. And it is
    never accepted where the daemon token is, so reading it out of Traefik does not
    give a container, or another account on the server, the keys to this daemon.

    Browsers also say where a request comes from. A state-changing request that another
    site started (``Sec-Fetch-Site`` other than ``same-origin`` or ``none``, or failing
    that an ``Origin`` or ``Referer`` that is not this host), or a WebSocket opened from
    another origin, is refused: a page on a sibling app's subdomain shares the single
    sign-on cookie and must not be able to start jobs here.

    Who signed in comes from the headers of the one configured single sign-on:
    ``Remote-User`` and ``Remote-Groups`` for Authelia. Traefik replaces exactly those
    with the sign-on's answer, so a browser cannot forge them; headers of a sign-on that
    is not in the route would pass through untouched, and are ignored. Only the Saltbox
    user from accounts.yml, or a member of the ``admins`` group, is let in, and a rule
    that lets people through without signing in sends no user, which is refused.
    Without this, every single sign-on user would control a daemon that runs as root.

    This is plain ASGI rather than ``BaseHTTPMiddleware``, which never sees WebSocket
    connections.
    """

    SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
    SAME_SITE = frozenset({"same-origin", "none"})
    ADMIN_GROUP = "admins"

    def __init__(
        self,
        app: ASGIApp,
        token: str,
        tailscale_socket: str | None = None,
        tailscale_owner: Callable[[], str | None] | None = None,
        admin_user: Callable[[], str | None] | None = None,
        root_socket: str | None = None,
        sso: str = "authelia",
        proxy_token: str | None = None,
        proxy_peer: Callable[[str], bool] | None = None,
        session_verify: Callable[[str | None, dict[str, str]], tuple[str, str] | None]
        | None = None,
    ):
        if sso not in SSO_HEADERS:
            raise ValueError(f"unknown single sign-on {sso!r}; one of {sorted(SSO_HEADERS)}")
        self.app = app
        self.token = token.encode()
        self.proxy_token = proxy_token.encode() if proxy_token else None
        self.proxy_peer = proxy_peer
        self.session_verify = session_verify
        self.admin_user = admin_user
        self.root_socket = root_socket
        self.tailscale_socket = tailscale_socket
        self.tailscale_owner = tailscale_owner or TailscaleOwner()
        self.user_header, self.group_header = SSO_HEADERS[sso]

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        kind = scope["type"]
        if kind not in ("http", "websocket") or not scope.get("path", "").startswith("/api/"):
            await self.app(scope, receive, send)
            return
        headers = Headers(scope=scope)
        reason = await self._refusal(scope, headers)
        if reason is None:
            await self.app(scope, receive, send)
        elif kind == "websocket":
            code = 4401 if reason == "unauthorised" else 4403
            await WebSocketClose(code=code)(scope, receive, send)
        else:
            status = 401 if reason == "unauthorised" else 403
            await JSONResponse({"error": reason}, status_code=status)(scope, receive, send)

    async def _refusal(self, scope: Scope, headers: Headers) -> str | None:
        listener = listener_socket(scope)
        if self.tailscale_socket and listener == self.tailscale_socket:
            refused = await self._tailscale_refusal(headers)
        elif self.root_socket and listener == self.root_socket:
            refused = None
        else:
            refused = await self._token_refusal(scope, headers)
        return refused or self._cross_site_refusal(scope, headers)

    async def _tailscale_refusal(self, headers: Headers) -> str | None:
        """On the socket ``tailscale serve`` proxies to, only this server's owner gets in.

        Tailscale puts the signed-in tailnet user in ``Tailscale-User-Login`` (and
        strips any copy a client sends). Tagged devices and shared-in users have
        another login or none, and are refused.
        """
        login = headers.get("tailscale-user-login", "").strip().lower()
        if not login:
            return "unauthorised"
        owner = await asyncio.to_thread(self.tailscale_owner)
        if owner is None or login != owner:
            return "only the Tailscale account that owns this server can open it"
        return None

    async def _token_refusal(self, scope: Scope, headers: Headers) -> str | None:
        header = headers.get("authorization", "")
        presented = (header[7:] if header.lower().startswith("bearer ") else "").encode()
        server = scope.get("server")
        if isinstance(server, tuple | list) and server and is_loopback(server[0]):
            # Tools on the server itself. Compared as bytes: compare_digest refuses
            # text that is not ASCII.
            if not presented or not hmac.compare_digest(presented, self.token):
                return "unauthorised"
            if self.admin_user is not None and headers.get("x-forwarded-host"):
                return self._sso_refusal(headers)  # someone's own proxy on the server
            return None
        # The address the reverse proxy reaches the daemon by.
        if self.proxy_token is None or not presented:
            return "unauthorised"
        if not hmac.compare_digest(presented, self.proxy_token):
            return "unauthorised"
        if self.proxy_peer is not None:
            client = scope.get("client")
            peer = str(client[0]) if client else ""
            if not peer or not await asyncio.to_thread(self.proxy_peer, peer):
                return "only the reverse proxy may use this address"
        if self.admin_user is None:
            return None
        if self.session_verify is not None:
            return await self._verified_refusal(headers)
        return self._sso_refusal(headers)

    async def _verified_refusal(self, headers: Headers) -> str | None:
        """Ask the sign-on itself who holds this session, and admit only the admin.

        The ``Remote-*`` headers on a proxied request are not trusted here: a router a
        container defined can set them. The provider is asked instead, with the request's
        cookie and this app's own host, and its answer decides.
        """
        forwarded = {
            "X-Forwarded-Proto": "https",
            "X-Forwarded-Host": headers.get("x-forwarded-host") or headers.get("host") or "",
            "X-Forwarded-Uri": "/",
            "X-Forwarded-Method": "GET",
        }
        try:
            identity = await asyncio.to_thread(
                self.session_verify, headers.get("cookie"), forwarded
            )
        except VerifyUnavailable as error:
            log.error("refusing a proxied request: %s", error)
            return (
                "the single sign-on could not be reached to confirm the session; if this "
                "persists, check it is running, or set SALTBOX_UI_VERIFY_SESSION=off"
            )
        if identity is None:
            return (
                "no signed-in session came with this request; this app needs single sign-on "
                "(Authelia) in front of it, without a bypass rule"
            )
        return self._admin_refusal(*identity)

    def _sso_refusal(self, headers: Headers) -> str | None:
        """For proxied requests: the signed-in person must be the Saltbox admin."""
        return self._admin_refusal(
            headers.get(self.user_header, ""), headers.get(self.group_header, "")
        )

    def _admin_refusal(self, user: str, groups_value: str) -> str | None:
        user = (user or "").strip()
        if not user:
            return (
                "no signed-in user came with this request; this app needs single sign-on "
                "(Authelia) in front of it, without a bypass rule"
            )
        groups = {g.strip().lower() for g in re.split(r"[,|]", groups_value or "")}
        admin = (self.admin_user() if self.admin_user else None) or ""
        if user.lower() == admin.lower() or self.ADMIN_GROUP in groups:
            return None
        return f"signed in as {user}, but only the Saltbox admin can use this app"

    def _cross_site_refusal(self, scope: Scope, headers: Headers) -> str | None:
        origin = headers.get("origin")
        foreign = bool(origin) and urlsplit(origin).netloc != headers.get("host", "")
        if scope["type"] == "websocket":
            return "cross-origin request refused" if foreign else None
        if scope.get("method", "GET") in self.SAFE_METHODS:
            return None
        site = headers.get("sec-fetch-site")
        if site is not None:
            return None if site in self.SAME_SITE else "cross-site request refused"
        # A browser too old to send Sec-Fetch-Site still sends Origin with a POST, and
        # one older still at least says which page the request came from.
        referer = headers.get("referer")
        if not origin and referer:
            foreign = urlsplit(referer).netloc != headers.get("host", "")
        return "cross-site request refused" if foreign else None


MAX_BODY_BYTES = 2 * 1024 * 1024  # the largest thing sent here is a config file

# Nothing is loaded from outside the server except the apps' own icons (https images).
# React sets inline styles through the DOM, which a style-src without 'unsafe-inline' allows.
CONTENT_SECURITY_POLICY = "; ".join(
    [
        "default-src 'self'",
        "script-src 'self'",
        "style-src 'self'",
        "img-src 'self' https: data:",
        "connect-src 'self'",
        "object-src 'none'",
        "base-uri 'none'",
        "form-action 'self'",
        "frame-ancestors 'none'",
    ]
)
HOST_HEADER = re.compile(r"(?:[A-Za-z0-9.-]{1,253}|\[[0-9A-Fa-f:.]{2,45}\])(?::[0-9]{1,5})?")
SECURITY_HEADERS = [
    (b"content-security-policy", CONTENT_SECURITY_POLICY.encode()),
    (b"x-frame-options", b"DENY"),
    (b"x-content-type-options", b"nosniff"),
    (b"referrer-policy", b"no-referrer"),
    (b"cross-origin-opener-policy", b"same-origin"),
    (b"cross-origin-resource-policy", b"same-origin"),
    (b"permissions-policy", b"camera=(), microphone=(), geolocation=(), payment=()"),
]


class Hardening:
    """Response headers every page and answer carries, and a limit on what is accepted.

    The page may not be framed (a page on another site could otherwise put it under the
    admin's cursor), loads scripts and styles from this server only, and API answers,
    which can hold revealed secrets, are never cached. A request body larger than
    ``MAX_BODY_BYTES`` is refused before it is read into memory.
    """

    def __init__(self, app: ASGIApp, max_body: int = MAX_BODY_BYTES):
        self.app = app
        self.max_body = max_body

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "")
        extra = list(SECURITY_HEADERS)
        # Older Safari does not take 'self' to cover the page's own WebSocket address, so
        # it is named, when the Host header is plainly a host and nothing else.
        host = Headers(scope=scope).get("host", "")
        if HOST_HEADER.fullmatch(host):
            policy = CONTENT_SECURITY_POLICY.replace(
                "connect-src 'self'", f"connect-src 'self' ws://{host} wss://{host}"
            )
            extra[0] = (b"content-security-policy", policy.encode())
        if path.startswith("/api/"):
            extra.append((b"cache-control", b"no-store"))
        elif not path.startswith("/assets/"):
            extra.append((b"cache-control", b"no-cache"))  # index.html names the current build

        async def send_with_headers(message: Any) -> None:
            if message["type"] == "http.response.start":
                present = {name.lower() for name, _ in message.get("headers", [])}
                message = dict(message)
                message["headers"] = list(message.get("headers", [])) + [
                    header for header in extra if header[0] not in present
                ]
            await send(message)

        declared = Headers(scope=scope).get("content-length", "")
        if declared and (not declared.isdigit() or int(declared) > self.max_body):
            await self._too_large(scope, receive, send_with_headers)
            return
        seen = 0

        async def limited_receive() -> Any:
            nonlocal seen
            message = await receive()
            if message["type"] == "http.request":
                seen += len(message.get("body", b""))
                if seen > self.max_body:
                    # Raised inside the route's read of the body; FastAPI passes it on as is.
                    raise HTTPException(413, "request body too large")
            return message

        await self.app(scope, limited_receive, send_with_headers)

    async def _too_large(self, scope: Scope, receive: Receive, send: Send) -> None:
        response = JSONResponse({"detail": "request body too large"}, status_code=413)
        await response(scope, receive, send)


def _admin_user(services: Services) -> str | None:
    """The Saltbox user from accounts.yml: the one person the web app is for."""
    try:
        value = services.config.get("accounts", "user.name")
    except Exception:  # missing or unreadable accounts.yml: only the admins group gets in
        return None
    return str(value) if value else None


def create_app(
    layout: Layout | None = None,
    *,
    token: str | None = None,
    web_dir: Path | None = None,
    services: Services | None = None,
    tailscale_socket: str | None = None,
    root_socket: str | None = None,
    admin_check: bool = True,
    sso: str = "authelia",
    proxy_token: str | None = None,
    proxy_peer: Callable[[str], bool] | None = None,
    verify_session: bool = True,
    sso_container: str | None = None,
    session_verify: Callable[[str | None, dict[str, str]], tuple[str, str] | None] | None = None,
) -> FastAPI:
    services = services or Services(
        layout or Layout.from_env(),
        token=token,
        proxy_token=proxy_token,
        proxy_peer=proxy_peer,
        web_dir=web_dir,
        tailscale_socket=tailscale_socket,
        root_socket=root_socket,
        admin_check=admin_check,
        sso=sso,
        verify_session=verify_session,
        sso_container=sso_container,
    )
    app = FastAPI(
        title="saltbox-ui",
        version=__version__,
        # No interactive docs page: it would run a script from a CDN in this origin.
        docs_url=None,
        redoc_url=None,
        openapi_url="/api/openapi.json",
    )
    app.state.services = services
    app.include_router(router, prefix="/api")
    app.add_exception_handler(RecursionError, _too_deep)
    app.add_exception_handler(DuplicateKeys, _duplicate_keys)
    if services.token:
        if (
            session_verify is None
            and services.admin_check
            and services.verify_session
            and services.proxy_token
        ):
            session_verify = SessionVerifier(
                services.sso, *SSO_HEADERS[services.sso], container=services.sso_container
            )
        app.add_middleware(
            DaemonGuard,
            token=services.token,
            tailscale_socket=services.tailscale_socket,
            admin_user=(lambda: _admin_user(services)) if services.admin_check else None,
            root_socket=services.root_socket,
            sso=services.sso,
            proxy_token=services.proxy_token,
            proxy_peer=services.proxy_peer,
            session_verify=session_verify,
        )
    app.add_middleware(Hardening)
    if services.web_dir and (services.web_dir / "index.html").exists():
        _mount_web(app, services.web_dir)
    else:

        @app.get("/", include_in_schema=False)
        def no_web() -> JSONResponse:
            return JSONResponse(
                {
                    "message": "saltbox-ui daemon is running; the web app is not built",
                    "api": "/api/openapi.json",
                }
            )

    return app


async def _too_deep(request: Any, error: Exception) -> JSONResponse:
    """JSON nested deeper than the parser will follow is a bad request, not a server error."""
    return JSONResponse({"detail": "request body is nested too deeply"}, status_code=400)


async def _duplicate_keys(request: Any, error: Exception) -> JSONResponse:
    """A structured save onto a file with a duplicate key is refused, not silently applied."""
    keys = ", ".join(getattr(error, "keys", []))
    return JSONResponse(
        {"detail": f"this file has a duplicate key; fix it in the editor first: {keys}"},
        status_code=422,
    )


def _mount_web(app: FastAPI, web_dir: Path) -> None:
    """Serve the built web app with a single-page fallback to index.html."""
    root = web_dir.resolve()

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str) -> FileResponse:
        try:
            candidate = (root / path).resolve() if path else root / "index.html"
            found = candidate.is_file() and candidate.is_relative_to(root)
        except (OSError, ValueError):  # a NUL byte, or a name too long for the filesystem
            found = False
        return FileResponse(candidate if found else root / "index.html")


def default_web_dir(layout: Layout) -> Path | None:
    """The built web app: the one shipped in the package, else the source tree's build."""
    package = Path(__file__).resolve().parents[1]
    candidates = [
        package / "web_dist",  # a wheel built with the web app inside
        package.parent / "web" / "dist",  # an editable install from a source checkout
    ]
    for candidate in candidates:
        if (candidate / "index.html").exists():
            return candidate
    return None


def read_token(layout: Layout, filename: str = TOKEN_FILENAME) -> str | None:
    path = layout.state_dir / filename
    try:
        return path.read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def ensure_token(layout: Layout, filename: str = TOKEN_FILENAME) -> str:
    """Return the daemon token, creating it (root only) when missing."""
    existing = read_token(layout, filename)
    return existing or rotate_token(layout, filename)


def rotate_token(layout: Layout, filename: str = TOKEN_FILENAME) -> str:
    """Replace a token with a new one; whatever held the old one no longer gets in."""
    import secrets

    token = secrets.token_urlsafe(32)
    safefs.write_private(layout.state_dir / filename, token + "\n")
    return token


def ensure_proxy_token(layout: Layout) -> str:
    """The token the reverse proxy presents (see ``DaemonGuard`` for why it is its own)."""
    return ensure_token(layout, PROXY_TOKEN_FILENAME)
