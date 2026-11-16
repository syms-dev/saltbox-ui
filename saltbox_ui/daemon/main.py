"""``saltbox-uid``: run the daemon.

Listens on 127.0.0.1 for local tools, on the Docker bridge address so
Traefik can reach it from its container, and on a Unix socket for root-only
access without a token. Every TCP request must carry a bearer token: the
daemon's own on 127.0.0.1, and on the bridge address the one the Traefik
route adds after single sign-on (see ``DaemonGuard`` for why they differ).
"""

from __future__ import annotations

import argparse
import logging
import os
import socket
import sys
import threading
from pathlib import Path

from ..layout import Layout
from .app import (
    ProxyPeer,
    create_app,
    default_web_dir,
    ensure_proxy_token,
    ensure_token,
    rotate_token,
)

DEFAULT_PORT = 8701
DEFAULT_SOCKET = "/run/saltbox-ui/api.sock"
DOCKER_BRIDGE_IP = "172.19.0.1"  # the saltbox network gateway, host.docker.internal in Traefik


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="saltbox-uid", description="Saltbox UI daemon")
    parser.add_argument("--host", action="append", help="address to listen on (repeatable)")
    parser.add_argument("--port", type=int, default=int(os.environ.get("SALTBOX_UI_PORT", 0)))
    parser.add_argument("--uds", default=os.environ.get("SALTBOX_UI_SOCKET", DEFAULT_SOCKET))
    parser.add_argument("--no-uds", action="store_true", help="do not listen on a Unix socket")
    parser.add_argument("--no-token", action="store_true", help="development only: no auth")
    parser.add_argument(
        "--no-admin-check",
        action="store_true",
        default=os.environ.get("SALTBOX_UI_ADMIN_CHECK", "").lower() in {"off", "0", "false"},
        help="let every single-sign-on user in, not only the Saltbox admin (not recommended)",
    )
    parser.add_argument(
        "--sso",
        choices=["authelia", "authentik"],
        default=os.environ.get("SALTBOX_UI_SSO", "authelia"),
        help="the single sign-on in the Traefik route; only its headers say who signed in",
    )
    parser.add_argument(
        "--no-proxy-peer-check",
        action="store_true",
        default=os.environ.get("SALTBOX_UI_PROXY_PEER", "").lower() in {"off", "0", "false"},
        help="accept the proxy token from any address, not only Traefik's (not recommended)",
    )
    parser.add_argument(
        "--web", default=os.environ.get("SALTBOX_UI_WEB"), help="directory with the built web app"
    )
    parser.add_argument("--log-level", default="info")
    return parser


def default_hosts() -> list[str]:
    hosts = ["127.0.0.1"]
    if _address_present(DOCKER_BRIDGE_IP):
        hosts.append(DOCKER_BRIDGE_IP)
    return hosts


def _address_present(address: str) -> bool:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind((address, 0))
    except OSError:
        return False
    return True


def bind_sockets(
    hosts: list[str], port: int, uds: str | None, extra_uds: tuple[str, ...] = ()
) -> list[socket.socket]:
    sockets: list[socket.socket] = []
    for host in hosts:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((host, port))
        sock.listen(128)
        sock.set_inheritable(True)
        sockets.append(sock)
    for path in [uds, *extra_uds] if uds else []:
        sockets.append(_bind_unix(Path(path)))
    return sockets


def _bind_unix(path: Path) -> socket.socket:
    """A root-only Unix socket (0600 in the 0700 runtime directory)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.bind(str(path))
    path.chmod(0o600)
    sock.listen(128)
    sock.set_inheritable(True)
    return sock


def tailscale_socket_for(uds: str) -> str:
    """The socket ``tailscale serve`` proxies to, next to the daemon's own."""
    return str(Path(uds).with_name("tailscale.sock"))


def web_dir(explicit: str | None, layout: Layout) -> Path | None:
    if explicit:
        return Path(explicit)
    return default_web_dir(layout)


def code_not_roots() -> Path | None:
    """The first folder holding this app's code that someone other than root can change.

    The daemon runs as root, so whoever can replace its code is root too. A source
    checkout owned by the Saltbox user is the usual case, and a known one (the README
    says so); a wheel installed under ``/opt`` is the one to move.
    """
    if os.geteuid() != 0:
        return None
    folder = Path(__file__).resolve().parent
    for current in (folder, *folder.parents):
        try:
            info = current.stat()
        except OSError:
            continue
        if info.st_uid != 0 or info.st_mode & 0o022:
            return current
    return None


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=args.log_level.upper(), format="%(asctime)s %(levelname)s %(message)s"
    )
    import uvicorn

    loose = code_not_roots()
    if loose is not None:
        logging.warning(
            "%s can be changed by someone other than root, and this daemon runs its code "
            "as root: whoever owns it is root on this server",
            loose,
        )
    if args.no_token:
        logging.warning("running without a token: anyone who can connect controls this server")
    layout = Layout.from_env()
    token = proxy_token = None
    if not args.no_token:
        token, proxy_token = ensure_token(layout), ensure_proxy_token(layout)
        from .install import refresh_route

        try:
            if refresh_route(layout, proxy_token, sso=args.sso):
                # The route held the daemon token until now, where Traefik showed it to
                # every container. Traefik has its own now, and the old one is retired.
                token = rotate_token(layout)
                logging.warning("the Traefik route was rewritten with a token of its own")
        except (OSError, ValueError) as error:
            logging.error("could not bring the Traefik route up to date: %s", error)
    # The same spelling the socket is bound with: the guard compares the two.
    uds = None if args.no_uds else str(Path(args.uds))
    tailscale_socket = None if uds is None else tailscale_socket_for(uds)
    app = create_app(
        layout,
        token=token,
        proxy_token=proxy_token,
        proxy_peer=None if args.no_proxy_peer_check else ProxyPeer(),
        web_dir=web_dir(args.web, layout),
        tailscale_socket=tailscale_socket,
        root_socket=uds,
        admin_check=not args.no_admin_check,
        sso=args.sso,
    )
    app.state.services.automation.start()
    # Parse every role's defaults once in the background, so the first Apps page is quick.
    warm = threading.Thread(
        target=app.state.services.catalog.apps, name="warm-catalog", daemon=True
    )
    warm.start()
    hosts = args.host or default_hosts()
    port = args.port or DEFAULT_PORT
    try:
        sockets = bind_sockets(
            hosts,
            port,
            uds,
            (tailscale_socket,) if tailscale_socket else (),
        )
    except OSError as error:
        print(f"cannot listen: {error}", file=sys.stderr)
        return 1
    for host in hosts:
        logging.info("listening on http://%s:%s", host, port)
    if uds is not None:
        logging.info("listening on unix socket %s", uds)
    # No proxy headers: the address a connection came from is the peer's own, which is
    # what the guard checks, and not what a header says it is.
    config = uvicorn.Config(app, log_level=args.log_level, proxy_headers=False, ws="websockets")
    server = uvicorn.Server(config)
    server.run(sockets=sockets)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
