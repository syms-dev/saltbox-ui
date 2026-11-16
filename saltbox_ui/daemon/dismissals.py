"""Issues someone has looked at and chosen to stop being told about.

A failing drive can stay in service for months and a harmless log message can
repeat for ever. Each issue the daemon reports carries a key that names what
is wrong (for a drive: which checks and attributes fail); dismissing stores
that key on the server, so it holds for every browser. When the issue changes
(another attribute starts failing) its key changes and it is shown again.

An entry is one of two kinds:

* ``dismissed``: not wanted, for good (until the issue changes);
* ``fixed``: someone fixed it at ``at``. It stays out of sight while nothing
  newer than that turns up, and comes back, saying so, if it happens again.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

from .. import safefs

MAX_KEY = 300
MAX_ENTRIES = 500
KINDS = ("dismissed", "fixed")


class Dismissals:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()

    def all(self) -> dict[str, dict[str, Any]]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def has(self, key: str) -> bool:
        return self.kind(key) == "dismissed"

    def kind(self, key: str) -> str | None:
        entry = self.all().get(key)
        if not isinstance(entry, dict):
            return None
        return "fixed" if entry.get("kind") == "fixed" else "dismissed"

    def add(self, key: str, label: str = "", kind: str = "dismissed") -> None:
        if not key or len(key) > MAX_KEY:
            raise ValueError("not an issue this server reported")
        if kind not in KINDS:
            raise ValueError(f"not a kind of dismissal: {kind}")
        with self._lock:
            entries = self.all()
            entries[key] = {"at": time.time(), "label": label[:MAX_KEY], "kind": kind}
            # Oldest out first, so the file cannot grow without end.
            kept = sorted(entries.items(), key=lambda item: item[1].get("at", 0))[-MAX_ENTRIES:]
            self._save(dict(kept))

    def remove(self, key: str) -> bool:
        with self._lock:
            entries = self.all()
            if key not in entries:
                return False
            del entries[key]
            self._save(entries)
            return True

    def _save(self, entries: dict[str, dict[str, Any]]) -> None:
        safefs.write_private(self.path, json.dumps(entries, indent=1))
