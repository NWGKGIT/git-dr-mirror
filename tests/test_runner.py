"""Tests for run orchestration: failure isolation, dry-run, exit codes."""

import pytest

from git_dr_mirror import runner
from git_dr_mirror.github import Repo
from git_dr_mirror.lock import LockHeldError
from git_dr_mirror.mirror import MirrorError
from git_dr_mirror.runner import run_backup

REPOS = [
    Repo(name="alpha", clone_url="https://github.com/me/alpha.git", fork=False),
    Repo(name="broken", clone_url="https://github.com/me/broken.git", fork=False),
    Repo(name="omega", clone_url="https://github.com/me/omega.git", fork=False),
]


@pytest.fixture
def stubbed(monkeypatch, config):
    """Stub discovery and per-repo operations; record what happened."""
    events = []
    monkeypatch.setattr(runner.github, "list_repos", lambda cfg: list(REPOS))
    monkeypatch.setattr(runner.gitlab, "make_session", lambda cfg: object())

    def fake_update(cfg, repo):
        if repo.name == "broken":
            raise MirrorError("git clone failed: network down")
        events.append(("mirror", repo.name))

    monkeypatch.setattr(runner.mirror, "update_local_mirror", fake_update)
    monkeypatch.setattr(
        runner.gitlab, "ensure_project",
        lambda cfg, name, session=None, description=None: (
            events.append(("ensure", name)) or f"https://gitlab.com/g/{name}.git"
        ),
    )
    monkeypatch.setattr(
        runner.mirror, "push_to_gitlab",
        lambda cfg, name, url: events.append(("push", name)),
    )
    return events


def test_one_failure_does_not_stop_the_rest(config, stubbed):
    result = run_backup(config)
    assert result.succeeded == ["alpha", "omega"]
    assert list(result.failed) == ["broken"]
    assert "network down" in result.failed["broken"]
    # Repos after the broken one were fully processed.
    assert ("push", "omega") in stubbed


def test_exit_codes(config, stubbed):
    assert run_backup(config).exit_code == 1  # "broken" fails
    assert run_backup(config, only_repo="alpha").exit_code == 0


def test_summary_counts(config, stubbed):
    assert run_backup(config).summary() == "2 ok, 1 failed"


def test_dry_run_changes_nothing(config, stubbed):
    result = run_backup(config, dry_run=True)
    assert stubbed == []  # no mirror/ensure/push calls at all
    # Even "broken" succeeds in dry-run: nothing is attempted.
    assert result.succeeded == ["alpha", "broken", "omega"]
    assert result.exit_code == 0


def test_only_repo_filters_by_glob(config, stubbed):
    result = run_backup(config, only_repo="a*")
    assert result.succeeded == ["alpha"]
    assert ("push", "omega") not in stubbed


def test_discovery_failure_reported_not_raised(config, stubbed, monkeypatch):
    def boom(cfg):
        raise RuntimeError("GitHub API unreachable")

    monkeypatch.setattr(runner.github, "list_repos", boom)
    result = run_backup(config)
    assert result.exit_code == 1
    assert "<discovery>" in result.failed


def test_lock_contention_propagates(config, stubbed, monkeypatch):
    import contextlib

    @contextlib.contextmanager
    def held(path):
        raise LockHeldError("held by 999")
        yield

    monkeypatch.setattr(runner, "run_lock", held)
    with pytest.raises(LockHeldError):
        run_backup(config)


def test_processing_order_per_repo(config, stubbed):
    """Each repo goes mirror -> ensure -> push before the next repo starts."""
    run_backup(config, only_repo="alpha")
    assert stubbed == [("mirror", "alpha"), ("ensure", "alpha"), ("push", "alpha")]
