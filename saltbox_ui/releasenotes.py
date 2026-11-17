"""What an app update brings, in the words of the app's own release notes.

An update is known from the image (see ``state.updates``): the running image's version
label and the published one's. The notes come from the GitHub releases of the project
the image packages. Which project that is cannot be read reliably from the image (hotio
and linuxserver images name their own packaging repository, not the app's), so it is
listed here by hand, for apps whose releases are published on GitHub.

GitHub answers 60 requests an hour to anyone who does not sign in, so one list of
releases per project is asked for at most every few hours, and only when someone opens
an app that has an update. Notes are text written by the app's maintainers: they are
passed on as plain text, cut to a sane length, and never as HTML.
"""

from __future__ import annotations

import re
import threading
import time
from collections.abc import Callable
from typing import Any

from .appsetup.http import Response, SetupError, Transport, fetch

# Saltbox role -> the GitHub project whose releases the role's image follows. Each was
# checked to exist under exactly this name (GitHub's redirect for a renamed project is
# not followed).
UPSTREAM: dict[str, str] = {
    "authelia": "authelia/authelia",
    "bazarr": "morpheus65535/bazarr",
    "gluetun": "qdm12/gluetun",
    "jellyfin": "jellyfin/jellyfin",
    "lidarr": "Lidarr/Lidarr",
    "nzbget": "nzbgetcom/nzbget",
    "overseerr": "sct/overseerr",
    "prowlarr": "Prowlarr/Prowlarr",
    "radarr": "Radarr/Radarr",
    "sabnzbd": "sabnzbd/sabnzbd",
    "scrutiny": "AnalogJ/scrutiny",
    "sonarr": "Sonarr/Sonarr",
    "tautulli": "Tautulli/Tautulli",
    "traefik": "traefik/traefik",
    "unpackerr": "Unpackerr/unpackerr",
}

API = "https://api.github.com/repos/{project}/releases?per_page=30"
PROJECT = re.compile(r"[A-Za-z0-9-]{1,39}/[A-Za-z0-9._-]{1,100}")
MAX_RELEASES = 10  # shown at most; the rest are a link away
MAX_BODY = 6000  # characters of one release's notes
OK_SECONDS = 6 * 3600.0
FAILED_SECONDS = 15 * 60.0

_PACKAGING = re.compile(r"^(?:release-|v(?=\d))", re.IGNORECASE)
_LS_BUILD = re.compile(r"-ls\d+$")
_NUMBERS = re.compile(r"\d+(?:\.\d+)*")
_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)


def normalize(version: str) -> str:
    """``v4.0.11.2680``, ``release-4.0.11.2680`` and ``4.0.11.2680-ls169`` all name 4.0.11.2680."""
    return _LS_BUILD.sub("", _PACKAGING.sub("", version.strip()))


def version_key(version: str) -> tuple[int, ...] | None:
    """The leading numbers of a version, to order releases; None when it has none."""
    match = _NUMBERS.match(normalize(version))
    return tuple(int(part) for part in match.group(0).split(".")) if match else None


def clean_body(text: Any) -> str:
    """A release's notes as plain text: no HTML comments, no carriage returns, not too long."""
    body = _COMMENT.sub("", str(text or "")).replace("\r\n", "\n").replace("\r", "\n").strip()
    if len(body) > MAX_BODY:
        body = body[:MAX_BODY].rstrip() + "\n…"
    return body


def between(releases: list[dict[str, Any]], current: str, target: str) -> list[dict[str, Any]]:
    """The releases an update from ``current`` to ``target`` brings, newest first.

    That is every published release after the current version up to the target. Betas
    are left out unless the target is one. When the current version is not known, only
    the target's own release is given.
    """
    want = normalize(target)
    low, high = version_key(current), version_key(target)
    chosen = []
    for release in releases:
        tag = normalize(str(release.get("tag_name", "")))
        key = version_key(tag)
        newer = (
            low is not None
            and high is not None
            and key is not None
            and not release.get("prerelease")
            and low < key <= high
        )
        if tag == want or newer:
            chosen.append(release)
    chosen.sort(key=lambda r: version_key(str(r.get("tag_name", ""))) or (), reverse=True)
    return chosen


def _shown(project: str, release: dict[str, Any]) -> dict[str, Any]:
    url = str(release.get("html_url", ""))
    # The link comes from GitHub's answer: only a page of this project's releases is kept.
    if not url.startswith(f"https://github.com/{project}/releases/"):
        url = ""
    return {
        "version": normalize(str(release.get("tag_name", ""))),
        "name": str(release.get("name") or "")[:200],
        "published_at": str(release.get("published_at") or "")[:40],
        "prerelease": bool(release.get("prerelease")),
        "url": url,
        "body": clean_body(release.get("body")),
    }


def _github_transport(
    method: str, url: str, headers: dict[str, str], body: bytes | None
) -> Response:
    # No redirects at all: an answer from GitHub that points elsewhere is not followed.
    return fetch(method, url, headers, body)


class ReleaseNotes:
    """Release notes per project, asked of GitHub at most every few hours."""

    def __init__(
        self,
        transport: Transport | None = None,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.transport = transport or _github_transport
        self.clock = clock
        self._lock = threading.Lock()
        self._cache: dict[str, tuple[float, list[dict[str, Any]], str]] = {}

    def releases(self, project: str) -> tuple[list[dict[str, Any]], str]:
        """The project's recent releases and, when they could not be read, why."""
        with self._lock:
            cached = self._cache.get(project)
            if cached is not None:
                at, found, error = cached
                if self.clock() - at < (FAILED_SECONDS if error else OK_SECONDS):
                    return found, error
            found, error = self._ask(project)
            self._cache[project] = (self.clock(), found, error)
            return found, error

    def _ask(self, project: str) -> tuple[list[dict[str, Any]], str]:
        if not PROJECT.fullmatch(project):
            return [], f"not a GitHub project: {project!r}"
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "saltbox-ui",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        try:
            response = self.transport("GET", API.format(project=project), headers, None)
        except SetupError as error:
            return [], str(error)
        if response.status in (403, 429) and response.headers.get("x-ratelimit-remaining") == "0":
            return [], "GitHub's limit on requests from this server is used up for now"
        if response.status != 200:
            return [], f"GitHub answered HTTP {response.status}"
        data = response.json()
        if not isinstance(data, list):
            return [], "GitHub's answer was not a list of releases"
        return [r for r in data if isinstance(r, dict) and not r.get("draft")], ""

    def for_update(self, role: str, current: str, target: str) -> dict[str, Any]:
        """What updating ``role`` from ``current`` to ``target`` brings, as far as GitHub says."""
        project = UPSTREAM.get(role, "")
        result: dict[str, Any] = {
            "project": project,
            "releases_url": f"https://github.com/{project}/releases" if project else "",
            "releases": [],
            "more": False,
            "error": "",
        }
        if not project or not target:
            return result
        found, error = self.releases(project)
        result["error"] = error
        chosen = between(found, current, target)
        result["more"] = len(chosen) > MAX_RELEASES
        result["releases"] = [_shown(project, r) for r in chosen[:MAX_RELEASES]]
        return result
