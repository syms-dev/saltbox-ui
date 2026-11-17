"""Update this app itself, from the web app.

How depends on how it was installed:

* from a source checkout (an editable install): pull the branch, reinstall the
  package if its dependencies changed, rebuild the web app;
* from a release wheel: install the newest wheel on the project's Releases page
  into the environment the daemon runs from.

Either way the daemon then has to restart to run the new code, which the
daemon does itself once the update job has finished (``needs_restart``).

``python -m saltbox_ui.selfupdate`` is the update job's command: it prints what
it does, line by line, and exits non-zero when a step fails.
"""

from __future__ import annotations

import hashlib
import http.client
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from importlib import metadata
from pathlib import Path
from typing import Any

from . import __version__, gitsafe, safefs
from .state.repos import RepoStatus, check_repo

PROJECT = "syms-dev/saltbox-ui"
LATEST_RELEASE = f"https://api.github.com/repos/{PROJECT}/releases/latest"
UNIT = "saltbox-ui.service"
VERSION = re.compile(r"[0-9]+(?:\.[0-9]+){1,3}")
SHA256 = re.compile(r"sha256:[0-9a-f]{64}")
MAX_WHEEL_BYTES = 50 * 1024 * 1024
MAX_JSON_BYTES = 2 * 1024 * 1024

Fetch = Callable[[str], Any]


def package_dir() -> Path:
    return Path(__file__).resolve().parent


def source_dir(package: Path | None = None) -> Path | None:
    """The source tree this runs from, when it is an editable install; else ``None``."""
    root = (package or package_dir()).parent
    if (root / "pyproject.toml").is_file() and (root / "web" / "package.json").is_file():
        return root
    return None


def checkout_of(source: Path) -> Path | None:
    """The git checkout that holds ``source`` (it may be a folder inside a larger one)."""
    for folder in (source, *source.parents):
        if (folder / ".git").exists():
            return folder
    return None


def parse_version(text: str) -> tuple[int, ...]:
    """``"v0.1.10"`` → ``(0, 1, 10)``; anything that is not numbers sorts first."""
    parts = text.strip().lstrip("vV").split(".")
    try:
        return tuple(int(part) for part in parts)
    except ValueError:
        return ()


def fetch_json(url: str) -> Any:
    request = urllib.request.Request(
        url, headers={"Accept": "application/vnd.github+json", "User-Agent": "saltbox-ui"}
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        raw = response.read(MAX_JSON_BYTES + 1)
    if len(raw) > MAX_JSON_BYTES:
        raise ValueError("the answer is too large")
    return json.loads(raw.decode("utf-8"))


def latest_release(fetch: Fetch = fetch_json) -> dict[str, str]:
    """The newest release's version and its wheel's address."""
    try:
        data = fetch(LATEST_RELEASE)
    except urllib.error.HTTPError as error:
        if error.code == 404:
            raise RuntimeError(f"no release of {PROJECT} can be read (is it public?)") from error
        raise RuntimeError(f"GitHub answered {error.code}") from error
    except (OSError, ValueError, RecursionError, http.client.HTTPException) as error:
        raise RuntimeError(f"could not reach GitHub: {error}") from error
    if not isinstance(data, dict):
        raise RuntimeError("GitHub's answer is not a release")
    version = str(data.get("tag_name") or "").lstrip("vV")
    if not VERSION.fullmatch(version):
        raise RuntimeError("the latest release has no usable version")
    assets = data.get("assets")
    for asset in assets if isinstance(assets, list) else []:
        if not isinstance(asset, dict):
            continue
        url = str(asset.get("browser_download_url") or "")
        # Only this project's own wheel, at the address a release asset has: what is
        # installed (as root) is never whatever address the answer happens to name.
        if url == wheel_url(version):
            digest = str(asset.get("digest") or "").lower()
            sha256 = digest[7:] if SHA256.fullmatch(digest) else ""
            return {"version": version, "url": url, "sha256": sha256}
    raise RuntimeError("the latest release has no wheel")


def wheel_url(version: str) -> str:
    """Where the wheel of ``version`` is, on this project's Releases page."""
    return (
        f"https://github.com/{PROJECT}/releases/download/v{version}/"
        f"saltbox_ui-{version}-py3-none-any.whl"
    )


def status(
    *,
    package: Path | None = None,
    repo_status: Callable[[Path], RepoStatus | None] = check_repo,
    fetch: Fetch = fetch_json,
    which: Callable[[str], str | None] = shutil.which,
    state_dir: Path | None = None,
) -> dict[str, Any]:
    """How this app is installed and whether a newer version is there to update to."""
    result: dict[str, Any] = {
        "version": __version__,
        "mode": "wheel",
        "source": None,
        "latest": None,
        "update_available": False,
        "can_update": True,
        "detail": "",
        "checked_at": time.time(),
    }
    source = source_dir(package)
    if source is not None:
        result["mode"] = "source"
        checkout = checkout_of(source)
        found = repo_status(checkout) if checkout is not None else None
        if found is None:
            result.update(can_update=False, detail=f"{source} is not a git checkout")
            return result
        result["source"] = {
            "path": found.path,
            "branch": found.branch,
            "commit": found.commit,
            "remote_commit": found.remote_commit,
        }
        result["update_available"] = found.status == "update"
        result["detail"] = found.detail
        if source_update_unfinished(state_dir):  # a previous update stopped after the pull
            result["update_available"] = True
            result["detail"] = "a previous update did not finish; run it again to finish it"
        if found.status == "unknown":
            result["can_update"] = False
        elif which("npm") is None:
            result.update(
                can_update=False,
                detail="npm is not installed, so the web app cannot be rebuilt here",
            )
        return result
    try:
        latest = latest_release(fetch)
    except RuntimeError as error:
        result.update(can_update=False, detail=str(error))
        return result
    result["latest"] = latest
    result["update_available"] = parse_version(latest["version"]) > parse_version(__version__)
    if installer(which=which) is None:
        result.update(
            can_update=False,
            detail="neither pip nor pipx is there to install a new version with",
        )
    return result


def needs_restart(loaded_commit: str | None, *, package: Path | None = None) -> bool:
    """Whether what is on disk is no longer what this process loaded."""
    source = source_dir(package)
    if source is not None:
        return loaded_commit is not None and head_commit(source) != loaded_commit
    try:
        return metadata.version("saltbox-ui") != __version__
    except metadata.PackageNotFoundError:
        return False


def head_commit(folder: Path) -> str | None:
    try:
        done = gitsafe.run(checkout_of(folder) or folder, ["rev-parse", "HEAD"], timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return done.stdout.strip() if done.returncode == 0 else None


def restart_daemon(delay: float = 1.5) -> None:
    """Ask systemd to restart the daemon in a moment, once the job's record is written."""
    import threading

    def restart() -> None:
        subprocess.run(["systemctl", "--no-block", "restart", UNIT], check=False, timeout=30)

    timer = threading.Timer(delay, restart)
    timer.daemon = True
    timer.start()


# -- the update itself, as the job's command -----------------------------------------------
def say(text: str) -> None:
    print(text, flush=True)


def step(
    args: list[str],
    *,
    cwd: Path | None = None,
    owner: tuple[int, int] | None = None,
    env: dict[str, str] | None = None,
) -> int:
    """Run one command, its output going to the job log. ``owner``: run it as that user."""
    say("$ " + " ".join(args))
    try:
        # Never from wherever the job happened to start: Python and pip look for code
        # in the folder they run in.
        done = subprocess.run(args, cwd=cwd or "/", check=False, env=env, **gitsafe.identity(owner))
        return done.returncode
    except OSError as error:
        say(f"could not run {args[0]}: {error}")
        return 127


def source_marker(state_dir: Path | None = None) -> Path:
    """The file that records a source update started but not finished."""
    if state_dir is None:
        from .layout import Layout

        state_dir = Layout.from_env().state_dir
    return Path(state_dir) / "source-update"


def source_update_unfinished(state_dir: Path | None = None) -> bool:
    return source_marker(state_dir).exists()


def update_source(source: Path, *, state_dir: Path | None = None) -> int:
    checkout = checkout_of(source)
    if checkout is None:
        say(f"{source} is not a git checkout")
        return 1
    marker = source_marker(state_dir)
    resuming = marker.exists()  # a previous run pulled, then pip or npm failed
    before = head_commit(source)
    # The point the running code was last consistent at: across runs, so a run that fails
    # after the pull is finished by the next one instead of being skipped as "up to date".
    baseline = marker.read_text(encoding="utf-8").strip() if resuming else before
    # git runs as the checkout's owner throughout: its config and hooks are theirs.
    try:
        owner = gitsafe.owner_of(checkout)
    except OSError as error:
        say(f"cannot tell whose checkout {checkout} is: {error}")
        return 1
    pull = step(
        ["git", "pull", "--ff-only"],
        cwd=checkout,
        owner=owner,
        env=gitsafe.environment(owner),
    )
    if pull != 0:
        say("git could not fast-forward; the checkout has changes of its own to sort out first")
        return 1
    after = head_commit(source)
    if not resuming and before == after:
        say("Already up to date.")
        return 0
    # There is work: a new commit now, or a previous update to finish. Record the baseline so
    # that if this run fails after here, the next one finishes it rather than skipping it.
    if not resuming:
        safefs.write_private(marker, str(baseline or ""))
    path = str((source / "pyproject.toml").relative_to(checkout))
    changed = gitsafe.run(
        checkout, ["diff", "--name-only", f"{baseline}..{after}", "--", path]
    ).stdout.strip()
    if changed or resuming:  # on a resume, install regardless: the last run may have failed here
        say("The package's dependencies changed; installing them.")
        if step([sys.executable, "-I", "-m", "pip", "install", "--quiet", "-e", str(source)]) != 0:
            say("pip failed. The new code may need packages that are not installed.")
            return 1
    npm = shutil.which("npm")
    if npm is None:
        say("npm is not installed, so the web app was not rebuilt")
        return 1
    for arguments in (["ci", "--no-audit", "--no-fund"], ["run", "build"]):
        if step([npm, *arguments], cwd=source / "web") != 0:
            return 1
    marker.unlink(missing_ok=True)  # finished: nothing to resume
    say(f"Updated from {str(baseline)[:7]} to {str(after)[:7]}. The daemon restarts now.")
    return 0


def installer(
    prefix: Path | None = None,
    which: Callable[[str], str | None] = shutil.which,
    has_pip: Callable[[], bool] | None = None,
) -> tuple[list[str], dict[str, str] | None] | None:
    """How to install a wheel into the environment this runs from: the command to put the
    wheel's path after, and the environment for it; ``None`` when there is no way.

    A pipx install has no pip of its own (pipx keeps one for all its apps), so there it
    is pipx that installs, into the same environment, keeping the commands it made.
    """
    prefix = Path(prefix or sys.prefix)
    if (prefix / "pipx_metadata.json").is_file() and prefix.parent.name == "venvs":
        pipx = which("pipx")
        if pipx is None:
            return None
        env = dict(os.environ, PIPX_HOME=str(prefix.parent.parent))
        return [pipx, "runpip", prefix.name, "install", "--upgrade", "--"], env
    if (has_pip or _has_pip)():
        return [sys.executable, "-I", "-m", "pip", "install", "--upgrade", "--"], None
    return None


def _has_pip() -> bool:
    import importlib.util

    return importlib.util.find_spec("pip") is not None


def update_wheel(fetch: Fetch = fetch_json) -> int:
    try:
        latest = latest_release(fetch)
    except RuntimeError as error:
        say(str(error))
        return 1
    if parse_version(latest["version"]) <= parse_version(__version__):
        say(f"Already on the latest release ({__version__}).")
        return 0
    say(f"Installing saltbox-ui {latest['version']} over {__version__}.")
    # Downloaded first, into a folder only this process can reach, and checked against the
    # checksum GitHub publishes for the file before pip is given anything.
    with tempfile.TemporaryDirectory(prefix="saltbox-ui-update-") as folder:
        wheel = Path(folder) / latest["url"].rsplit("/", 1)[1]
        try:
            found = download(latest["url"], wheel)
        except (OSError, ValueError, http.client.HTTPException) as error:
            say(f"could not download {latest['url']}: {error}")
            return 1
        expected = latest.get("sha256") or ""
        if not expected:
            # Nothing to check the file against is not the same as nothing wrong with it.
            say("GitHub published no checksum for this release's wheel. Nothing was installed.")
            say(f"To install it by hand: pipx install --force {latest['url']}")
            return 1
        if found != expected:
            say(f"The download does not match its checksum ({found} is not {expected}).")
            say("Nothing was installed.")
            return 1
        say(f"sha256 {found} (matches the release)")
        how = installer()
        if how is None:
            say("Neither pip nor pipx is there to install it with. Nothing was installed.")
            return 1
        command, env = how
        if step([*command, str(wheel)], env=env) != 0:
            return 1
    say(f"Installed {latest['version']}. The daemon restarts now.")
    return 0


class _HttpsOnly(urllib.request.HTTPRedirectHandler):
    """GitHub hands a download on to its file store; follow that only while it stays https."""

    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str):
        if not newurl.startswith("https://"):
            raise urllib.error.URLError(f"redirected away from https, to {newurl[:60]}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _https_open(request: Any, timeout: float) -> Any:
    return urllib.request.build_opener(_HttpsOnly()).open(request, timeout=timeout)


def download(url: str, target: Path, opener: Callable[..., Any] = _https_open) -> str:
    """Fetch ``url`` to ``target`` (https only, size capped); returns its SHA-256."""
    if not url.startswith("https://"):
        raise ValueError("not an https address")
    digest, size = hashlib.sha256(), 0
    request = urllib.request.Request(url, headers={"User-Agent": "saltbox-ui"})
    with opener(request, timeout=60) as response, open(target, "wb") as out:
        while chunk := response.read(65536):
            size += len(chunk)
            if size > MAX_WHEEL_BYTES:
                raise ValueError("the file is larger than a wheel of this app can be")
            digest.update(chunk)
            out.write(chunk)
    return digest.hexdigest()


def main() -> int:
    source = source_dir()
    return update_source(source) if source is not None else update_wheel()


if __name__ == "__main__":
    raise SystemExit(main())
