"""What other apps still say about an app that is being removed, and taking it out of them.

Saltbox writes some apps' settings once, from the apps installed at the time,
and never edits them again. Unpackerr's ``unpackerr.conf`` is one: it gets a
``[[sonarr]]``, ``[[radarr]]`` or ``[[lidarr]]`` section for each instance that
was installed when Unpackerr was. Remove Lidarr later and Unpackerr keeps
trying to reach it. Connect apps adds each Sonarr, Radarr and Lidarr to
Prowlarr's apps, and Prowlarr likewise keeps syncing to one that is gone.

``plan`` says what would be taken out, read before anything is removed (the
app's API key is in its own folder, which an uninstall may delete). ``apply``
takes it out. Each step stands on its own: one that fails is reported and the
others still happen; none of them can stop the uninstall itself.
"""

from __future__ import annotations

import os
import re
import urllib.parse
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from typing import Any

from . import safefs
from .appsetup.discover import Discovery, Service
from .appsetup.http import Client, SetupError, Transport
from .layout import Layout

# The apps Unpackerr has a section for, and Prowlarr an app for.
UNPACKERR_ROLES = ("sonarr", "radarr", "lidarr")
PROWLARR_ROLES = ("sonarr", "radarr", "lidarr")
TABLE = re.compile(r"^\s*\[")
SECTION = re.compile(r"^\s*\[\[\s*([A-Za-z0-9_-]+)\s*\]\]\s*(?:#.*)?$")
SETTING = re.compile(r'^\s*(url|api_key)\s*=\s*"([^"]*)"')


@dataclass
class Reference:
    """One place another app names the app being removed."""

    app: str  # the other app, as a person knows it: "Unpackerr"
    container: str  # its container
    what: str  # what is taken out, in a few words
    kind: str  # "unpackerr" or "prowlarr"
    data: dict[str, Any] = field(default_factory=dict)

    def view(self) -> dict[str, Any]:
        found = asdict(self)
        found.pop("data")
        return found


# -- Unpackerr ------------------------------------------------------------------------------
def unpackerr_sections(text: str) -> list[dict[str, Any]]:
    """The ``[[app]]`` sections of an Unpackerr config: where each starts and ends, and its
    ``url`` and ``api_key``. A section runs to the next table, or to the next comment at
    the start of a line (where Saltbox's template starts the next part of the file)."""
    lines = text.split("\n")
    found = []
    index = 0
    while index < len(lines):
        header = SECTION.match(lines[index])
        if header is None:
            index += 1
            continue
        end = index + 1
        values: dict[str, str] = {}
        while end < len(lines) and not TABLE.match(lines[end]) and not lines[end].startswith("#"):
            setting = SETTING.match(lines[end])
            if setting:
                values[setting.group(1)] = setting.group(2)
            end += 1
        while end > index + 1 and not lines[end - 1].strip():
            end -= 1  # blank lines after it belong to what follows
        found.append(
            {
                "role": header.group(1).lower(),
                "start": index,
                "end": end,
                "url": values.get("url", ""),
                "api_key": values.get("api_key", ""),
            }
        )
        index = end
    return found


def _names(service: Service, url: str) -> bool:
    """Whether ``url`` is an address of ``service``: its container name, or a subdomain of
    that name (Saltbox writes the public ``https://lidarr.example.com`` there)."""
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    return host == service.name.lower() or host.split(".", 1)[0] == service.name.lower()


def _same(section: dict[str, Any], service: Service) -> bool:
    if section["role"] != service.role:
        return False
    if service.api_key and section["api_key"] == service.api_key:
        return True
    return _names(service, section["url"])


def unpackerr_references(
    layout: Layout, found: Discovery, removed: list[Service]
) -> list[Reference]:
    references = []
    for unpackerr in found.installed("unpackerr"):
        path = layout.appdata / unpackerr.name / "unpackerr.conf"
        text = safefs.read_text_or_none(path, base=layout.appdata)
        if text is None:
            continue
        for service in removed:
            for section in unpackerr_sections(text):
                if _same(section, service):
                    references.append(
                        Reference(
                            "Unpackerr",
                            unpackerr.name,
                            f"the [[{section['role']}]] section for {service.name}",
                            "unpackerr",
                            {"path": str(path), "role": section["role"], "service": service.name},
                        )
                    )
    return references


def remove_unpackerr_sections(text: str, remove: Callable[[dict[str, Any]], bool]) -> str:
    """``text`` without the sections ``remove`` picks."""
    lines = text.split("\n")
    for section in reversed(unpackerr_sections(text)):
        if remove(section):
            end = section["end"]
            while end < len(lines) and not lines[end].strip():
                end += 1  # and the blank lines that set it apart
            del lines[section["start"] : end]
    return "\n".join(lines)


def _apply_unpackerr(layout: Layout, references: list[Reference], removed: list[Service]) -> None:
    by_name = {service.name: service for service in removed}
    for path in sorted({reference.data["path"] for reference in references}):
        folder_path = os.path.dirname(path)
        with safefs.open_dir(_checked(layout, folder_path)) as folder:
            text = safefs.read_text("unpackerr.conf", dir_fd=folder)
            names = [r.data["service"] for r in references if r.data["path"] == path]

            def named(section: dict[str, Any], names: list[str] = names) -> bool:
                return any(_same(section, by_name[name]) for name in names)

            changed = remove_unpackerr_sections(text, named)
            if changed != text:
                info = os.stat("unpackerr.conf", dir_fd=folder, follow_symlinks=False)
                safefs.replace_file(
                    folder,
                    "unpackerr.conf",
                    changed.encode("utf-8"),
                    mode=info.st_mode & 0o777,
                    owner=(info.st_uid, info.st_gid),
                )


def _checked(layout: Layout, folder: str) -> Any:
    from pathlib import Path

    resolved = Path(folder)
    if resolved.parent != layout.appdata:
        raise SetupError(f"{folder} is not an app's folder")
    return resolved


# -- Prowlarr -------------------------------------------------------------------------------
def _prowlarr(service: Service, transport: Transport | None) -> Client:
    return Client(
        f"{service.host_url}/api/v1",
        headers={"X-Api-Key": service.api_key},
        transport=transport,
        name=service.name,
    )


def _base_url(item: dict[str, Any]) -> str:
    for entry in item.get("fields") or []:
        if isinstance(entry, dict) and entry.get("name") == "baseUrl":
            return str(entry.get("value") or "")
    return ""


def prowlarr_references(
    found: Discovery, removed: list[Service], transport: Transport | None
) -> list[Reference]:
    references = []
    for prowlarr in found.installed("prowlarr"):
        if not prowlarr.api_key:
            continue
        try:
            apps = _prowlarr(prowlarr, transport).get("/applications") or []
        except SetupError:
            continue  # Prowlarr is not answering: there is nothing to say about it here
        for item in apps if isinstance(apps, list) else []:
            if not isinstance(item, dict) or not isinstance(item.get("id"), int):
                continue
            for service in removed:
                kind = str(item.get("implementation") or "").lower()
                if kind == service.role and _names(service, _base_url(item)):
                    references.append(
                        Reference(
                            "Prowlarr",
                            prowlarr.name,
                            f"the app {item.get('name') or service.name} it syncs indexers to",
                            "prowlarr",
                            {"prowlarr": prowlarr.name, "id": item["id"]},
                        )
                    )
    return references


# -- both -----------------------------------------------------------------------------------
def plan(
    layout: Layout, found: Discovery, role: str, instances: list[str], transport: Transport | None
) -> list[Reference]:
    """Where other apps name the instances of ``role`` that are about to be removed."""
    removed = [s for s in found.services.get(role, []) if s.name in instances]
    if not removed:
        return []
    references: list[Reference] = []
    if role in UNPACKERR_ROLES:
        references += unpackerr_references(layout, found, removed)
    if role in PROWLARR_ROLES:
        references += prowlarr_references(found, removed, transport)
    return references


def apply(
    layout: Layout,
    found: Discovery,
    role: str,
    references: list[Reference],
    transport: Transport | None,
    restart: Callable[[str], None],
) -> tuple[list[str], list[str]]:
    """Take ``references`` out. Returns what was done and what could not be, in words."""
    removed = [s for s in found.services.get(role, [])]
    done: list[str] = []
    failed: list[str] = []
    unpackerr = [r for r in references if r.kind == "unpackerr"]
    if unpackerr:
        try:
            _apply_unpackerr(layout, unpackerr, removed)
            for container in sorted({r.container for r in unpackerr}):
                restart(container)  # it reads its config when it starts
            done += [f"{r.app}: removed {r.what}" for r in unpackerr]
        except (OSError, SetupError, RuntimeError) as error:
            failed += [f"{r.app}: {r.what} ({error})" for r in unpackerr]
    prowlarrs = {s.name: s for s in found.installed("prowlarr")}
    for reference in (r for r in references if r.kind == "prowlarr"):
        prowlarr = prowlarrs.get(reference.data["prowlarr"])
        try:
            if prowlarr is None:
                raise SetupError("Prowlarr is not installed any more")
            client = _prowlarr(prowlarr, transport)
            client.request("DELETE", f"/applications/{int(reference.data['id'])}")
            done.append(f"{reference.app}: removed {reference.what}")
        except SetupError as error:
            failed.append(f"{reference.app}: {reference.what} ({error})")
    return done, failed
