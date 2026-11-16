"""File work in folders that someone other than root can write to.

The daemon runs as root, and reads and writes files in places that belong to
somebody else: an app's folder under ``/opt`` (the app's container writes
there), the media folders, the Saltbox checkout. Whoever owns such a folder can
swap a file for a symbolic link, or for a named pipe, at any moment. Opening a
file there by its path would then read or overwrite whatever the link points
at, as root.

Everything here works on file descriptors instead: the folder is opened once
(refusing a link in its place), files are opened relative to it with
``O_NOFOLLOW``, and ownership and mode are set on the open descriptor. A link
planted before or during the call is refused, never followed.
"""

from __future__ import annotations

import contextlib
import errno
import os
import secrets
import stat
from collections.abc import Iterator
from pathlib import Path

MAX_READ_BYTES = 1024 * 1024

_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
_READ_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
_CREATE_FLAGS = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC


class UnsafeFile(OSError):
    """The file is a link, not a regular file, or too large to be what it should be."""


@contextlib.contextmanager
def open_dir(path: Path) -> Iterator[int]:
    """A descriptor for the folder at ``path``; a symbolic link in its place is refused."""
    fd = os.open(path, _DIR_FLAGS)
    try:
        yield fd
    finally:
        os.close(fd)


def read_bytes(
    path: Path | str,
    *,
    limit: int = MAX_READ_BYTES,
    dir_fd: int | None = None,
    base: Path | None = None,
) -> bytes:
    """The content of a regular file, at most ``limit`` bytes.

    Raises ``UnsafeFile`` for a symbolic link, a pipe, a device, a file with a second
    name (a hard link to something else) or a larger file, and ``OSError`` when it
    cannot be opened. With ``dir_fd``, ``path`` is a name inside that folder. With
    ``base``, every folder between ``base`` and the file is opened without following
    a link either: an app that owns ``base/app`` cannot point ``base/app/config`` at
    someone else's folder.
    """
    try:
        if base is not None:
            fd = _open_under(Path(base), Path(path))
        else:
            fd = os.open(path, _READ_FLAGS, dir_fd=dir_fd)
    except OSError as error:
        if error.errno in (errno.ELOOP, errno.ENOTDIR):
            raise UnsafeFile(f"{path} is, or is behind, a symbolic link") from error
        raise
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise UnsafeFile(f"{path} is not a regular file")
        if info.st_nlink != 1:
            raise UnsafeFile(f"{path} has more than one name")
        if info.st_size > limit:
            raise UnsafeFile(f"{path} is larger than {limit} bytes")
        chunks, total = [], 0
        while total <= limit:
            chunk = os.read(fd, min(65536, limit + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
        if total > limit:
            raise UnsafeFile(f"{path} is larger than {limit} bytes")
        return b"".join(chunks)
    finally:
        os.close(fd)


def _open_under(base: Path, path: Path) -> int:
    """Open ``path`` for reading, following no link in any part of it below ``base``."""
    try:
        parts = path.relative_to(base).parts
    except ValueError as error:
        raise UnsafeFile(f"{path} is not under {base}") from error
    if not parts or ".." in parts:
        raise UnsafeFile(f"{path} is not a file under {base}")
    fd = os.open(base, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        for part in parts[:-1]:
            deeper = os.open(part, _DIR_FLAGS, dir_fd=fd)
            os.close(fd)
            fd = deeper
        return os.open(parts[-1], _READ_FLAGS, dir_fd=fd)
    finally:
        os.close(fd)


def read_text(
    path: Path | str,
    *,
    limit: int = MAX_READ_BYTES,
    dir_fd: int | None = None,
    base: Path | None = None,
) -> str:
    data = read_bytes(path, limit=limit, dir_fd=dir_fd, base=base)
    return data.decode("utf-8", errors="replace")


def read_text_or_none(
    path: Path, *, limit: int = MAX_READ_BYTES, base: Path | None = None
) -> str | None:
    """``read_text``, or ``None`` when the file is missing, unreadable or not safe to read."""
    try:
        return read_text(path, limit=limit, base=base)
    except OSError:
        return None


def create_file(
    dir_fd: int,
    name: str,
    data: bytes,
    *,
    mode: int = 0o600,
    owner: tuple[int, int] | None = None,
) -> None:
    """Create ``name`` in the folder; fails if anything, a link included, is already there."""
    fd = os.open(name, _CREATE_FLAGS, mode, dir_fd=dir_fd)
    try:
        os.fchmod(fd, mode)
        if owner is not None and os.geteuid() == 0:
            os.fchown(fd, *owner)
        _write_all(fd, data)
        os.fsync(fd)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(name, dir_fd=dir_fd)
        raise
    finally:
        os.close(fd)


def replace_file(
    dir_fd: int,
    name: str,
    data: bytes,
    *,
    mode: int = 0o600,
    owner: tuple[int, int] | None = None,
) -> None:
    """Write ``name`` in the folder atomically, replacing a file (or a link) of that name.

    The content goes to a new file with a random name, created exclusively,
    which then takes the name. A link at ``name`` is replaced, not followed.
    """
    temporary = f".{name}.{secrets.token_hex(8)}.tmp"
    create_file(dir_fd, temporary, data, mode=mode, owner=owner)
    try:
        os.rename(temporary, name, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(temporary, dir_fd=dir_fd)
        raise


def write_private(path: Path, text: str, *, mode: int = 0o600) -> None:
    """Write a file of the daemon's own (a token, settings) that is never readable by
    others, not even between being created and being finished."""
    private_dir(path.parent)
    with open_dir(path.parent) as folder:
        replace_file(folder, path.name, text.encode("utf-8"), mode=mode)


def private_dir(path: Path) -> None:
    """Create ``path`` for its owner only, and tighten one an older version left open."""
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.stat()
    if info.st_mode & 0o077 and info.st_uid == os.geteuid():
        path.chmod(0o700)


def owner_of(dir_fd: int) -> tuple[int, int]:
    info = os.fstat(dir_fd)
    return info.st_uid, info.st_gid


def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        view = view[os.write(fd, view) :]
