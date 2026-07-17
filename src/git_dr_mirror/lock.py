"""Single-run lock to prevent overlapping backups.

Uses ``flock(2)`` on a lockfile inside the mirror directory. The kernel
releases the lock automatically when the holding process exits — including
crashes and hard power-offs — so stale locks cannot occur: a lock either
has a live holder or is free. The PID written into the file is diagnostic
only, so a blocked run can say who holds the lock.
"""

from __future__ import annotations

import fcntl
import logging
import os
from contextlib import contextmanager
from pathlib import Path

log = logging.getLogger(__name__)

LOCKFILE_NAME = ".git-dr-mirror.lock"


class LockHeldError(Exception):
    """Another backup run is already in progress."""


@contextmanager
def run_lock(mirror_dir: Path):
    """Hold the exclusive run lock for the duration of the ``with`` block.

    Raises:
        LockHeldError: If another process currently holds the lock. The
            caller should exit cleanly — the other run is doing the work.
    """
    mirror_dir.mkdir(parents=True, exist_ok=True)
    lock_path = mirror_dir / LOCKFILE_NAME
    lock_file = open(lock_path, "a+")
    try:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            lock_file.seek(0)
            holder = lock_file.read().strip() or "unknown PID"
            raise LockHeldError(
                f"Another backup run is in progress (held by {holder}). "
                "Exiting; the running backup will finish the job."
            ) from None

        lock_file.seek(0)
        lock_file.truncate()
        lock_file.write(f"{os.getpid()}\n")
        lock_file.flush()
        log.debug("Acquired run lock at %s", lock_path)
        yield
    finally:
        # Closing the fd releases the flock; leave the file in place
        # (removing it would open a race with a concurrent acquirer).
        lock_file.close()
