"""Update checks for the daemon (app images and the Saltbox checkouts), cached on disk."""

from __future__ import annotations

import contextlib
import json
import threading
import time
from collections.abc import Callable
from typing import Any

from .. import safefs
from ..layout import Layout
from ..state.containers import list_containers
from ..state.repos import RepoStatus, check_repos
from ..state.updates import ImageStatus, check_updates, local_digests

CACHE_FILE = "updates.json"
CACHE_VERSION = 2  # 2: added the Saltbox checkouts
MAX_AGE = 6 * 3600


class UpdateService:
    def __init__(
        self,
        layout: Layout,
        *,
        containers: Callable[[], list[dict[str, Any]]] | None = None,
        checker: Callable[[list[dict[str, Any]]], dict[str, ImageStatus]] | None = None,
        repos: Callable[[], list[RepoStatus]] | None = None,
        clock: Callable[[], float] = time.time,
        max_age: float = MAX_AGE,
        digests: Callable[..., dict[str, list[str]]] | None = None,
    ):
        self.path = layout.state_dir / CACHE_FILE
        self.containers = containers or list_containers
        self.checker = checker or check_updates
        self.repos = repos or (
            lambda: check_repos([layout.repo, layout.sandbox_repo, layout.saltbox_mod_repo])
        )
        self.clock = clock
        self.max_age = max_age
        self.digests = digests or local_digests
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._again = False  # a check was asked for while one ran: run once more after it
        self.error = ""

    # -- cache -----------------------------------------------------------------
    def cached(self) -> dict[str, Any]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {"checked_at": None, "images": {}}
        data.setdefault("repos", [])
        data["running"] = self.running
        data["error"] = self.error
        return data

    def _write(
        self,
        images: dict[str, Any],
        repos: list[dict[str, Any]],
        checked_at: float | None = None,
    ) -> None:
        payload = {
            "version": CACHE_VERSION,
            "checked_at": self.clock() if checked_at is None else checked_at,
            "images": images,
            "repos": repos,
        }
        safefs.write_private(self.path, json.dumps(payload, indent=1))

    # -- refresh ---------------------------------------------------------------
    @property
    def running(self) -> bool:
        thread = self._thread
        return bool(thread and thread.is_alive())

    def refresh(self, *, wait: bool = False) -> bool:
        """Start a check unless one is running; returns whether one was started."""
        with self._lock:
            if self.running:
                # It may have read the containers before a job changed them: check again after.
                self._again = True
                return False
            self._thread = threading.Thread(target=self._run, name="update-check", daemon=True)
            self._thread.start()
        if wait:
            self.wait()
        return True

    def wait(self, timeout: float | None = None) -> None:
        thread = self._thread
        if thread is not None:
            thread.join(timeout)

    def _run(self) -> None:
        self._check()
        while True:
            with self._lock:
                if not self._again:
                    return
                self._again = False
            self._check()

    def reconcile(self) -> None:
        """Mark images current that now match what the registry offered, from Docker alone.

        After a job pulls an app's new image, the full check (a request per image to each
        registry) takes a while, and until it ends the app still showed "update available"
        with its Update button. The local image's digests are enough to see that it now is
        the published one; nothing is marked current that the registry did not offer.
        """
        cached = self.cached()
        images = cached.get("images", {})
        waiting = {
            ref: status
            for ref, status in images.items()
            if status.get("status") == "update" and status.get("remote_digest")
        }
        if not waiting:
            return
        try:
            ids: dict[str, str] = {}
            for container in self.containers():
                ref = container.get("image_ref") or container.get("image") or ""
                if ref in waiting and container.get("image_id"):
                    ids.setdefault(ref, str(container["image_id"]))
            local = self.digests(ids.values())
        except Exception:  # the full check that follows will say what is wrong
            return
        changed = False
        for ref, status in waiting.items():
            if status["remote_digest"] in local.get(ids.get(ref, ""), []):
                status.update(status="current", detail="")
                changed = True
        if changed:
            with contextlib.suppress(OSError):
                self._write(images, cached.get("repos", []), checked_at=cached.get("checked_at"))

    def _check(self) -> None:
        """Check images and checkouts; a part that fails keeps its last good result."""
        previous = self.cached()
        errors: list[str] = []
        images, repos = previous.get("images", {}), previous.get("repos", [])
        try:
            images = {ref: s.as_dict() for ref, s in self.checker(self.containers()).items()}
        except Exception as error:
            errors.append(f"images: {error}")
        try:
            repos = [status.as_dict() for status in self.repos()]
        except Exception as error:
            errors.append(f"saltbox: {error}")
        try:
            self._write(images, repos)
        except OSError as error:
            errors.append(f"cache: {error}")
        self.error = "; ".join(errors)

    def ensure_fresh(self) -> None:
        cached = self.cached()
        checked = cached.get("checked_at")
        stale = not checked or self.clock() - float(checked) > self.max_age
        if stale or cached.get("version") != CACHE_VERSION:
            self.refresh()

    # -- lookups -----------------------------------------------------------------
    def status_by_container(self) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        for status in self.cached().get("images", {}).values():
            for name in status.get("containers", []):
                result[name] = status
        return result
