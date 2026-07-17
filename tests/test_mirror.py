"""Tests for local mirror operations (subprocess mocked throughout)."""

import subprocess

import pytest

from git_dr_mirror.github import Repo
from git_dr_mirror.mirror import (
    MirrorError,
    local_mirror_path,
    push_to_gitlab,
    update_local_mirror,
)

REPO = Repo(name="proj", clone_url="https://github.com/me/proj.git", fork=False)


class FakeGit:
    """Replaces subprocess.run; scripts results per git subcommand.

    ``script`` maps a subcommand ("clone", "push", "rev-parse", ...) to a
    list of (returncode, stdout, stderr) tuples consumed in order; an
    exhausted or missing entry means success.
    """

    def __init__(self, script=None):
        self.script = script or {}
        self.calls = []

    def __call__(self, command, **kwargs):
        self.calls.append({"command": command, **kwargs})
        for subcommand, queue in self.script.items():
            if subcommand in command and queue:
                returncode, stdout, stderr = queue.pop(0)
                return subprocess.CompletedProcess(command, returncode, stdout, stderr)
        return subprocess.CompletedProcess(command, 0, "", "")


@pytest.fixture
def fake_git(monkeypatch):
    fake = FakeGit()
    monkeypatch.setattr("git_dr_mirror.mirror.subprocess.run", fake)
    return fake


def calls_for(fake, subcommand):
    return [c for c in fake.calls if subcommand in c["command"]]


def test_missing_mirror_is_cloned(config, fake_git):
    path = update_local_mirror(config, REPO)
    assert path == config.mirror_dir / "proj.git"

    (clone,) = calls_for(fake_git, "clone")
    assert "--mirror" in clone["command"]
    assert REPO.clone_url in clone["command"]
    # Auth comes from the environment, never the command line.
    assert clone["env"]["GIT_DR_TOKEN"] == "gh-token"
    assert clone["env"]["GIT_DR_USER"] == "x-access-token"
    assert clone["env"]["GIT_TERMINAL_PROMPT"] == "0"
    assert not any("gh-token" in arg for arg in clone["command"])
    assert clone["timeout"] == config.git_timeout


def test_existing_valid_mirror_is_updated(config, fake_git):
    path = local_mirror_path(config, "proj")
    path.mkdir(parents=True)
    fake_git.script = {"rev-parse": [(0, "true\n", "")]}

    update_local_mirror(config, REPO)

    assert not calls_for(fake_git, "clone")
    (update,) = calls_for(fake_git, "update")
    assert update["command"][-3:] == ["remote", "update", "--prune"]
    assert update["cwd"] == path


def test_invalid_mirror_quarantined_and_recloned(config, fake_git):
    path = local_mirror_path(config, "proj")
    path.mkdir(parents=True)
    (path / "junk").write_text("half a clone")
    fake_git.script = {"rev-parse": [(128, "", "not a git repository")]}

    update_local_mirror(config, REPO)

    quarantined = config.mirror_dir / "proj.git.corrupt"
    assert quarantined.is_dir()
    assert (quarantined / "junk").exists()
    assert calls_for(fake_git, "clone")


def test_quarantine_never_overwrites_previous_quarantine(config, fake_git):
    path = local_mirror_path(config, "proj")
    path.mkdir(parents=True)
    (config.mirror_dir / "proj.git.corrupt").mkdir()
    fake_git.script = {"rev-parse": [(128, "", "boom")]}

    update_local_mirror(config, REPO)

    assert (config.mirror_dir / "proj.git.corrupt.1").is_dir()


def test_failed_clone_quarantines_partial_dir(config, monkeypatch):
    def clone_leaves_partial_dir(command, **kwargs):
        if "clone" in command:
            # Simulate git dying mid-clone with the directory half-written.
            local_mirror_path(config, "proj").mkdir(parents=True, exist_ok=True)
            return subprocess.CompletedProcess(command, 128, "", "network down")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr("git_dr_mirror.mirror.subprocess.run", clone_leaves_partial_dir)

    with pytest.raises(MirrorError, match="network down"):
        update_local_mirror(config, REPO)
    assert (config.mirror_dir / "proj.git.corrupt").is_dir()
    assert not local_mirror_path(config, "proj").exists()


def test_transient_update_failure_retried_once(config, fake_git):
    path = local_mirror_path(config, "proj")
    path.mkdir(parents=True)
    fake_git.script = {
        "rev-parse": [(0, "true\n", "")],
        "update": [(1, "", "connection reset"), (0, "", "")],
    }
    update_local_mirror(config, REPO)
    assert len(calls_for(fake_git, "update")) == 2


def test_persistent_failure_raises_mirror_error(config, fake_git):
    path = local_mirror_path(config, "proj")
    path.mkdir(parents=True)
    fake_git.script = {
        "rev-parse": [(0, "true\n", "")],
        "update": [(1, "", "auth failed"), (1, "", "auth failed")],
    }
    with pytest.raises(MirrorError, match="auth failed"):
        update_local_mirror(config, REPO)


def test_push_uses_gitlab_token_and_safe_refspecs(config, fake_git):
    path = local_mirror_path(config, "proj")
    path.mkdir(parents=True)

    push_to_gitlab(config, "proj", "https://gitlab.com/backup-group/proj.git")

    (push,) = calls_for(fake_git, "push")
    assert push["env"]["GIT_DR_USER"] == "oauth2"
    assert push["env"]["GIT_DR_TOKEN"] == "gl-token"
    assert "https://gitlab.com/backup-group/proj.git" in push["command"]
    assert "+refs/heads/*:refs/heads/*" in push["command"]
    assert "+refs/tags/*:refs/tags/*" in push["command"]
    # Never --mirror to GitLab: refs/pull/* would be rejected as hidden refs.
    assert "--mirror" not in push["command"]
    assert not any("gl-token" in arg for arg in push["command"])


def test_push_url_not_stored_in_remote_config(config, fake_git):
    """The push URL is passed as an argument, not persisted via `git remote`."""
    path = local_mirror_path(config, "proj")
    path.mkdir(parents=True)
    push_to_gitlab(config, "proj", "https://gitlab.com/backup-group/proj.git")
    assert not any(
        "set-url" in c["command"] or "remote" == c["command"][-2:-1]
        for c in fake_git.calls
    )
