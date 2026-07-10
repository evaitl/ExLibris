"""Exclusive lock for long-running library maintenance jobs."""

from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path

from exlibris.config import PROJECT_ROOT

DEFAULT_LOCK_PATH = PROJECT_ROOT / "data" / "library.lock"


class LibraryJobLockedError(RuntimeError):
    """Another library job already holds the lock."""


def _read_lock_pid(lock_path: Path) -> str | None:
    try:
        raw = lock_path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return raw or None


@contextmanager
def library_job_lock(
    lock_path: Path | None = None,
    *,
    block: bool = False,
    job_name: str = "library job",
):
    """Acquire an exclusive flock on ``data/library.lock``.

    Raises ``LibraryJobLockedError`` when ``block`` is false and the lock is held.
    Skips locking when ``EXLIBRIS_JOB_LOCK_HELD=1`` (set by ``scan-library.sh``).
    """
    path = (lock_path or DEFAULT_LOCK_PATH).expanduser().resolve()
    if os.environ.get("EXLIBRIS_JOB_LOCK_HELD") == "1":
        yield path
        return

    path.parent.mkdir(parents=True, exist_ok=True)

    import fcntl

    handle = path.open("a+", encoding="utf-8")
    fd = handle.fileno()
    flags = fcntl.LOCK_EX
    if not block:
        flags |= fcntl.LOCK_NB
    try:
        try:
            fcntl.flock(fd, flags)
        except BlockingIOError as exc:
            holder = _read_lock_pid(path)
            detail = f" (pid {holder})" if holder else ""
            raise LibraryJobLockedError(
                f"Another {job_name} is already running{detail}. "
                f"Lock file: {path}"
            ) from exc
        handle.seek(0)
        handle.truncate()
        handle.write(f"{os.getpid()}\n")
        handle.flush()
        yield path
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        handle.close()
