"""Connect the installed apps to each other: the post-install setup step.

``AppSetup`` finds what is installed, builds the list of connections the
Saltbox docs describe, checks each one against the app's API and applies the
missing ones. The TUI, ``sbx setup`` and the daemon all use it.
"""

from __future__ import annotations

import os
import pwd
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

from ..layout import Layout
from .discover import Discovery, Resolver, Service, discover, resolve_hosts
from .http import SetupError, Transport
from .tasks import (
    SetupContext,
    Task,
    apply_all,
    build_tasks,
    check_all,
    qbit_vpn_port_task,
    summarize,
)

__all__ = [
    "AppSetup",
    "Discovery",
    "Service",
    "SetupError",
    "Task",
    "summarize",
]


def owned_dir_maker(user: str) -> Callable[[Path], None]:
    """mkdir -p that hands every folder it creates to the Saltbox user.

    The media folders are written by containers. Each part of the path is opened
    through the one before it, without following a link, and a new folder is given
    away through that open folder: a link left where a folder should be is refused,
    not followed to somewhere root then creates and gives away folders.
    """

    def make(path: Path) -> None:
        try:
            entry = pwd.getpwnam(user) if os.geteuid() == 0 else None
        except KeyError:
            entry = None
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
        parts = Path(os.path.abspath(path)).parts
        fd = os.open(parts[0], os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
        try:
            for part in parts[1:]:
                created = False
                try:
                    os.mkdir(part, 0o775, dir_fd=fd)
                    created = True
                except FileExistsError:
                    pass
                try:
                    deeper = os.open(part, flags, dir_fd=fd)
                except OSError as error:
                    raise OSError(
                        f"{part} in {path} is not a plain folder (a link, perhaps); "
                        "create the folder by hand"
                    ) from error
                os.close(fd)
                fd = deeper
                if created and entry is not None:
                    os.fchown(fd, entry.pw_uid, entry.pw_gid)
        finally:
            os.close(fd)

    return make


def docker_logs(name: str) -> str:
    """The recent log of a container, or an empty string when Docker is not available."""
    if shutil.which("docker") is None:
        return ""
    try:
        result = subprocess.run(
            ["docker", "logs", "--tail", "300", "--", name],
            capture_output=True,
            text=True,
            check=False,
            timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return result.stdout + result.stderr


def _mark_running(found: Discovery) -> None:
    """Apps that keep no folder under /opt (FlareSolverr) count as installed when running."""
    try:
        from ..state.containers import list_containers

        running = {c["name"] for c in list_containers() if c.get("status") == "running"}
    except Exception:  # no Docker: the folders are all there is to go on
        return
    for services in found.services.values():
        for service in services:
            if not service.installed and service.name in running:
                service.installed = True


class AppSetup:
    def __init__(
        self,
        layout: Layout,
        *,
        transport: Transport | None = None,
        resolver: Resolver | None = None,
        ensure_dir: Callable[[Path], None] | None = None,
        log_reader: Callable[[str], str] | None = None,
        container_reader: Callable[[str, str], str] | None = None,
        unit_dir: Path | None = None,
        systemctl: Callable[..., tuple[int, str]] | None = None,
    ):
        self.layout = layout
        self.log_reader = log_reader or docker_logs
        self.container_reader = container_reader
        self.unit_dir = unit_dir or Path("/etc/systemd/system")
        self.systemctl = systemctl
        self.transport = transport
        self.resolver = resolver
        self.ensure_dir = ensure_dir

    def discover(self) -> Discovery:
        found = discover(self.layout)
        _mark_running(found)
        resolve_hosts(found, self.resolver)
        return found

    def context(self) -> SetupContext:
        found = self.discover()
        return SetupContext(
            found,
            transport=self.transport,
            ensure_dir=self.ensure_dir or owned_dir_maker(found.user),
            log_reader=self.log_reader,
            container_reader=self.container_reader,
            unit_dir=self.unit_dir,
            systemctl=self.systemctl,
            appdata=self.layout.appdata,
        )

    def tasks(self) -> list[Task]:
        return build_tasks(self.context())

    def sync_port(self) -> Task | None:
        """Just the VPN port step, for the timer. None when qBittorrent is not behind a VPN."""
        ctx = self.context()
        qbit = ctx.found.first("qbittorrent")
        if qbit is None or not ctx.found.qbit_vpn:
            return None
        return apply_all([qbit_vpn_port_task(ctx, qbit)])[0]

    def status(self) -> list[Task]:
        return check_all(self.tasks())

    def apply(self, on_task: Callable[[Task], None] | None = None) -> list[Task]:
        return apply_all(self.tasks(), on_task)
