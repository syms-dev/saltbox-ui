"""Run git in a checkout as the checkout's owner, never as root.

The Saltbox checkouts (and this app's own, when it runs from source) belong to
the Saltbox user. A repository's own ``.git/config`` can name commands for git
to run (``uploadpack``, ``core.sshCommand``, credential helpers, hooks), so
running git there as root would run its owner's commands as root. Git refuses
to work in someone else's repository for exactly this reason; rather than
overrule it with ``safe.directory``, the daemon gives up root for the call.
"""

from __future__ import annotations

import contextlib
import os
import pwd
import stat
import subprocess
from pathlib import Path
from typing import Any

NOBODY = 65534


def owner_of(repo: Path) -> tuple[int, int] | None:
    """The user git must run as in ``repo``, as ``(uid, gid)``; ``None`` when root may.

    It is whoever could change what git does there: the first owner that is not root
    among the checkout's ``.git``, the checkout itself and every folder above it. A
    folder that is root's but that others can write to counts as nobody's. Only when
    every one of them is root's alone does git run as root. The answer never depends
    on something its subject can swap in between two looks: with any part of the path
    in their hands, the answer is them.

    Raises ``OSError`` when the checkout cannot be examined; nothing is run then.
    """
    if os.geteuid() != 0:
        return None
    repo = Path(os.path.abspath(repo))
    stats = []
    with contextlib.suppress(FileNotFoundError):
        stats.append(os.lstat(repo / ".git"))
    stats.append(os.lstat(repo))
    stats += [os.lstat(parent) for parent in repo.parents]
    for info in stats:
        if info.st_uid != 0:
            try:
                return info.st_uid, pwd.getpwuid(info.st_uid).pw_gid
            except KeyError:
                return info.st_uid, info.st_gid
        loose = info.st_mode & 0o022 and not info.st_mode & stat.S_ISVTX
        if loose and not stat.S_ISLNK(info.st_mode):
            return NOBODY, NOBODY
    return None


def identity(owner: tuple[int, int] | None) -> dict[str, Any]:
    """``subprocess`` arguments that run a command as ``owner`` (see ``owner_of``)."""
    if owner is None:
        return {}
    return {"user": owner[0], "group": owner[1], "extra_groups": []}


def environment(owner: tuple[int, int] | None) -> dict[str, str]:
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0", LC_ALL="C")
    if owner is not None:
        try:
            env["HOME"] = pwd.getpwuid(owner[0]).pw_dir
        except KeyError:
            env["HOME"] = "/nonexistent"
        for name in ("XDG_CONFIG_HOME", "GIT_CONFIG_GLOBAL", "USER", "LOGNAME"):
            env.pop(name, None)
    return env


def run(repo: Path, args: list[str], *, timeout: float = 30) -> subprocess.CompletedProcess[str]:
    """``git -C repo <args>`` with its output captured. Raises what ``subprocess.run`` raises,
    and ``OSError`` when it cannot tell whose checkout this is."""
    owner = owner_of(repo)  # decided once: the user and the environment must agree
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        env=environment(owner),
        check=False,
        **identity(owner),
    )
