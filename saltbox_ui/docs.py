"""Saltbox's own documentation, shipped with this package, for Claude to read.

``catalog/data/docs.json`` is built from the Saltbox docs repository by
``scripts/build_docs.py``: the written part of every app, reference and FAQ
page. It is too much to send with every question (several hundred kilobytes),
so ``pages_for`` picks what an error is about: the page of the app that logged
it, then the pages of other apps its log names, then Saltbox's paths and
multiple-instance pages when the log mentions those, within ``LIMIT``.
"""

from __future__ import annotations

import functools
import json
import re
from dataclasses import dataclass
from importlib import resources
from typing import Any

LIMIT = 24_000  # characters of documentation sent with one question
PER_PAGE = 12_000
WORD = re.compile(r"[a-z0-9][a-z0-9_-]{2,}")


@dataclass
class Page:
    key: str
    title: str
    url: str
    text: str


@functools.cache
def _data() -> dict[str, Any]:
    try:
        text = resources.files("saltbox_ui.catalog.data").joinpath("docs.json").read_text()
        data = json.loads(text)
    except (OSError, ValueError):
        return {"pages": {}}
    return data if isinstance(data, dict) else {"pages": {}}


def commit() -> str:
    return str(_data().get("commit") or "")


def page(key: str) -> Page | None:
    found = (_data().get("pages") or {}).get(key)
    if not isinstance(found, dict):
        return None
    return Page(key, str(found.get("title", key)), str(found.get("url", "")), str(found["text"]))


def app_page(name: str) -> Page | None:
    """The page of an app by its role or container name (``sonarr4k`` is Sonarr's)."""
    name = name.lower()
    candidates = [name, re.sub(r"\d+k?$", "", name), name.split("-", 1)[0]]
    for candidate in candidates:
        for section in ("apps", "sandbox/apps"):
            found = page(f"{section}/{candidate}")
            if found is not None:
                return found
    return None


def pages_for(app: str, text: str, limit: int = LIMIT) -> list[Page]:
    """The pages that bear on an error from ``app`` whose log reads ``text``."""
    chosen: list[Page] = []
    own = app_page(app)
    if own is not None:
        chosen.append(own)
    words = set(WORD.findall(text.lower()))
    for word in sorted(words):
        if own is not None and word == own.key.rsplit("/", 1)[1]:
            continue
        other = page(f"apps/{word}")
        if other is not None and other not in chosen:
            chosen.append(other)
    extras = {
        "reference/multiple-instances": ("instance", "instances"),
        "saltbox/basics/paths": ("/mnt/unionfs", "/mnt/local", "/opt/"),
        "reference/local-storage": ("mergerfs", "/mnt/local"),
    }
    lowered = text.lower()
    for key, hints in extras.items():
        if any(hint in lowered for hint in hints):
            found = page(key)
            if found is not None:
                chosen.append(found)
    kept, size = [], 0
    for found in chosen:
        body = found.text[:PER_PAGE]
        if size + len(body) > limit:
            continue
        kept.append(Page(found.key, found.title, found.url, body))
        size += len(body)
    return kept


def as_prompt(pages: list[Page]) -> str:
    """The pages, set apart so Claude can tell them from the log."""
    if not pages:
        return ""
    parts = [f'<page title="{p.title}" url="{p.url}">\n{p.text.strip()}\n</page>' for p in pages]
    return (
        "Saltbox's documentation for what this is about:\n\n<docs>\n"
        + "\n".join(parts)
        + "\n</docs>\n"
    )
