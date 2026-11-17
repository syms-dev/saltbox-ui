"""Find the installed apps, their addresses and their API keys.

Everything comes from what Saltbox leaves on disk: each app's config file
under the appdata path (``/opt/<instance>/config.xml`` for the *arr apps),
instance lists and port overrides from the inventory, the user's name and
password from ``accounts.yml`` (Saltbox gives qBittorrent and Autoscan the
same login), and the Plex token from ``/opt/saltbox/plex.ini``.
"""

from __future__ import annotations

import ipaddress
import re
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from .. import safefs
from ..config import ConfigService, Inventory
from ..layout import Layout
from ..plex import read_plex_facts

# Saltbox's default web ports (``<role>_role_web_port``); the inventory may override them.
DEFAULT_PORTS = {
    "sonarr": 8989,
    "radarr": 7878,
    "lidarr": 8686,
    "prowlarr": 9696,
    "qbittorrent": 8080,
    "sabnzbd": 8080,
    "autoscan": 3030,
    "plex": 32400,
    "bazarr": 6767,
    "seerr": 5055,
    "flaresolverr": 8191,
    "scrutiny": 8080,
    "unpackerr": 5656,
}
API_VERSION = {"sonarr": "v3", "radarr": "v3", "lidarr": "v1", "prowlarr": "v1"}
ARR_ROLES = ("sonarr", "radarr", "lidarr")

Resolver = Callable[[str], str | None]


@dataclass
class Service:
    role: str
    name: str  # instance and container name, e.g. "sonarr" or "sonarr4k"
    port: int
    installed: bool
    api_key: str = ""
    host: str = ""  # address the engine uses from the host

    @property
    def url(self) -> str:
        """Address other containers use: the container name on the saltbox network."""
        return f"http://{self.name}:{self.port}"

    @property
    def host_url(self) -> str:
        return f"http://{self.host or self.name}:{self.port}"


@dataclass
class Discovery:
    services: dict[str, list[Service]] = field(default_factory=dict)
    user: str = ""
    password: str = ""
    plex_token: str = ""
    qbit_vpn: str = ""  # the Gluetun container qBittorrent is routed through, if any

    def first(self, role: str) -> Service | None:
        for service in self.services.get(role, []):
            if service.installed:
                return service
        return None

    def installed(self, role: str) -> list[Service]:
        return [s for s in self.services.get(role, []) if s.installed]


# The host given to a container that has no address. Nothing is ever looked up or sent
# for a name ending in ``.invalid``; the HTTP client says the container is not running.
NOT_RUNNING = ".not-running.invalid"


def hosts_file_address(name: str, hosts: Path = Path("/etc/hosts")) -> str | None:
    """The address ``/etc/hosts`` gives ``name`` (Saltbox's sdhm keeps containers there)."""
    try:
        lines = hosts.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    for line in lines:
        fields = line.split("#", 1)[0].split()
        if len(fields) > 1 and name in fields[1:]:
            return fields[0]
    return None


def _container_address(text: str) -> str | None:
    """``text`` as an address a container on this server can have; ``None`` otherwise."""
    try:
        parsed = ipaddress.ip_address(text)
    except ValueError:
        return None
    if not parsed.is_private or parsed.is_loopback or parsed.is_link_local:
        return None
    if parsed.is_unspecified or parsed.is_multicast:
        return None
    return f"[{parsed}]" if parsed.version == 6 else str(parsed)


def default_resolver(name: str) -> str | None:
    """The container's address from /etc/hosts (sdhm), or from Docker itself.

    Always an address, never the name, and never asked of DNS: a container that is
    stopped is in neither place, and its name would then go to whatever resolver the
    server uses. Whatever answered (a search domain, a resolver that answers for
    everything, another machine on the network) would be sent the app's API key or
    the account password.
    """
    listed = _container_address(hosts_file_address(name) or "")
    if listed is not None:
        return listed
    if shutil.which("docker") is None:
        return None
    result = subprocess.run(
        [
            "docker",
            "inspect",
            "-f",
            "{{range .NetworkSettings.Networks}}{{.IPAddress}} {{end}}",
            "--",
            name,
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    for address in result.stdout.split() if result.returncode == 0 else []:
        found = _container_address(address)
        if found is not None:
            return found
    return None


# What an app's API key looks like. The files it is read from belong to the app, so
# anything else (quotes, a path, markup) is not sent anywhere or written to a file.
API_KEY = re.compile(r"[A-Za-z0-9_-]{1,128}")


def _api_key(value: str) -> str:
    return value if API_KEY.fullmatch(value) else ""


def xml_api_key(path: Path, base: Path | None = None) -> str:
    # Read without following a link: the folder is the app's own (see ``safefs``).
    text = safefs.read_text_or_none(path, base=base)
    match = re.search(r"<ApiKey>\s*([^<\s]+)\s*</ApiKey>", text) if text else None
    return _api_key(match.group(1)) if match else ""


# ``api_key = <value>`` on its own line. Anchored and linear, so a container that fills
# sabnzbd.ini with one long line (``configparser`` is quadratic on that) cannot stall the
# daemon; the value is validated by ``_api_key`` as before.
SAB_API_KEY = re.compile(r"(?mi)^[ \t]*api_key[ \t]*=[ \t]*([^\n]*)")


def sabnzbd_api_key(path: Path, base: Path | None = None) -> str:
    text = safefs.read_text_or_none(path, base=base)
    match = SAB_API_KEY.search(text) if text else None
    # ``_api_key`` rejects a value that is not one clean token (a space, a comment, markup).
    return _api_key(match.group(1).strip()) if match else ""


def config_path(layout: Layout, role: str, name: str) -> Path:
    base = layout.appdata / name
    if role == "sabnzbd":
        return base / "sabnzbd.ini"
    if role == "autoscan":
        return base / "config.yml"
    if role == "unpackerr":
        return base / "unpackerr.conf"
    if role == "seerr":
        return base / "settings.json"
    return base / "config.xml"


def discover(
    layout: Layout,
    *,
    inventory: Inventory | None = None,
    config: ConfigService | None = None,
) -> Discovery:
    inventory = inventory or Inventory(layout.inventory)
    config = config or ConfigService(layout)
    result = Discovery()

    accounts = config.load_plain("accounts") if config.exists("accounts") else {}
    user = accounts.get("user") or {}
    result.user = str(user.get("name") or "")
    result.password = str(user.get("pass") or "")

    for role, default_port in DEFAULT_PORTS.items():
        services = []
        for name in inventory.instances(role, [role]):
            port = inventory.effective(role, "_web_port", name, default_port)
            path = config_path(layout, role, name)
            installed = (layout.appdata / name).is_dir()
            service = Service(role=role, name=name, port=int(port), installed=installed)
            if installed:
                if role in API_VERSION:
                    service.api_key = xml_api_key(path, layout.appdata)
                elif role == "sabnzbd":
                    service.api_key = sabnzbd_api_key(path, layout.appdata)
            services.append(service)
        result.services[role] = services

    qbit = result.first("qbittorrent")
    if qbit is not None:
        mode = str(inventory.effective("qbittorrent", "_docker_network_mode", qbit.name, "") or "")
        if mode.startswith("container:"):
            result.qbit_vpn = mode.split(":", 1)[1]

    plex = result.first("plex")
    if plex is not None:
        result.plex_token = read_plex_facts(layout, plex.name).get("token", "")
    return result


def resolve_hosts(found: Discovery, resolver: Resolver | None = None) -> None:
    """Fill in host-side addresses for installed services."""
    resolve = resolver or default_resolver
    for services in found.services.values():
        for service in services:
            if service.installed and not service.host:
                # No address: the name is not left to DNS (see ``default_resolver``).
                service.host = resolve(service.name) or f"{service.name}{NOT_RUNNING}"
