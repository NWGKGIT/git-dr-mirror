"""Tests for the single-run lock."""

import fcntl
import os
import subprocess
import sys

import pytest

from git_dr_mirror.lock import LOCKFILE_NAME, LockHeldError, run_lock


def test_acquire_and_release(tmp_path):
    with run_lock(tmp_path):
        lock_path = tmp_path / LOCKFILE_NAME
        assert lock_path.exists()
        assert lock_path.read_text().strip() == str(os.getpid())
    # Reacquirable after release.
    with run_lock(tmp_path):
        pass


def test_creates_mirror_dir_if_missing(tmp_path):
    target = tmp_path / "not" / "yet" / "created"
    with run_lock(target):
        assert target.is_dir()


def test_contention_raises_lock_held(tmp_path):
    # Hold the lock from a separate file descriptor, as another process would.
    outer = open(tmp_path / LOCKFILE_NAME, "w")
    outer.write("12345\n")
    outer.flush()
    fcntl.flock(outer.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        with pytest.raises(LockHeldError, match="12345"):
            with run_lock(tmp_path):
                pass
    finally:
        outer.close()


def test_lock_released_when_holder_dies(tmp_path):
    """A hard-killed holder must not leave a stale lock behind."""
    holder = subprocess.Popen(
        [
            sys.executable,
            "-c",
            (
                "import fcntl, sys, time;"
                f"f = open({str(tmp_path / LOCKFILE_NAME)!r}, 'w');"
                "fcntl.flock(f.fileno(), fcntl.LOCK_EX);"
                "print('locked', flush=True);"
                "time.sleep(60)"
            ),
        ],
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert holder.stdout.readline().strip() == "locked"
        with pytest.raises(LockHeldError):
            with run_lock(tmp_path):
                pass
        holder.kill()  # simulate crash / power loss
        holder.wait(timeout=10)
        # The kernel released the flock with the process: acquirable again.
        with run_lock(tmp_path):
            pass
    finally:
        if holder.poll() is None:
            holder.kill()
