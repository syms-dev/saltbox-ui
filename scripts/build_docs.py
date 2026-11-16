#!/usr/bin/env python3
"""Generate ``saltbox_ui/catalog/data/docs.json`` from a Saltbox docs repository checkout.

Keeps the part of each page that people wrote (overview, deployment, usage,
the reference and FAQ pages) and drops what is generated from the roles (the
long "Role Defaults" listing), front matter, HTML comments and button grids.
The result is small enough to ship in the package, so explanations by Claude
can be given the pages for the apps involved without a network call.

Usage: build_docs.py /path/to/saltyorg-docs [output.json]
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

SITE = "https://docs.saltbox.dev"
SECTIONS = ("apps", "sandbox/apps", "reference", "faq", "saltbox/basics", "advanced")
COMMENT = re.compile(r"<!--.*?-->", re.S)
GRID = re.compile(r'<div class="grid.*?</div>', re.S)
IMAGE = re.compile(r"^\s*!\[[^\]]*\]\([^)]*\).*$", re.M)
ATTRIBUTES = re.compile(r"\{\s*[.#][^}]*\}")
ICON = re.compile(r":(?:material|fontawesome|octicons|simple)-[a-z0-9-]+:")
BLANKS = re.compile(r"\n{3,}")
TITLE = re.compile(r"^#\s+(.+)$", re.M)


def clean(text: str) -> str:
    if text.startswith("---"):
        end = text.find("\n---", 3)
        text = text[end + 4 :] if end != -1 else text
    cut = text.find("\n## Role Defaults")
    if cut != -1:
        text = text[:cut]
    for pattern in (COMMENT, GRID, IMAGE, ATTRIBUTES, ICON):
        text = pattern.sub("", text)
    return BLANKS.sub("\n\n", text).strip() + "\n"


def build(repo: Path) -> dict:
    pages = {}
    for section in SECTIONS:
        folder = repo / "docs" / section
        if not folder.is_dir():
            continue
        for path in sorted(folder.glob("*.md")):
            if path.name == "index.md":
                continue
            key = f"{section}/{path.stem}"
            text = clean(path.read_text(encoding="utf-8"))
            if len(text) < 80:
                continue
            title = TITLE.search(text)
            pages[key] = {
                "title": title.group(1).strip() if title else path.stem,
                "url": f"{SITE}/{key}/",
                "text": text,
            }
    commit = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True, check=False
    ).stdout.strip()
    return {"source": "https://github.com/saltyorg/docs", "commit": commit, "pages": pages}


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__.strip().splitlines()[-1], file=sys.stderr)
        return 2
    out = (
        Path(argv[2])
        if len(argv) > 2
        else (Path(__file__).resolve().parents[1] / "saltbox_ui" / "catalog" / "data" / "docs.json")
    )
    data = build(Path(argv[1]))
    out.write_text(json.dumps(data, indent=0, ensure_ascii=False) + "\n", encoding="utf-8")
    size = sum(len(page["text"]) for page in data["pages"].values())
    print(f"{len(data['pages'])} pages, {size // 1024} KiB of text, from {data['commit'][:12]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
