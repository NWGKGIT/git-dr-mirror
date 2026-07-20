"""Tests for the command-line interface."""

import pytest

from git_dr_mirror import cli
from git_dr_mirror.config import ConfigError
from git_dr_mirror.lock import LockHeldError
from git_dr_mirror.preflight import CheckResult
from git_dr_mirror.runner import RunResult


@pytest.fixture(autouse=True)
def quiet_env(monkeypatch, tmp_path):
    """Point config at a guaranteed-empty env file and required vars."""
    for name in ("GITHUB_TOKEN", "GITLAB_TOKEN", "GITLAB_GROUP"):
        monkeypatch.setenv(name, "x")
    monkeypatch.setenv("MIRROR_DIR", str(tmp_path / "mirrors"))


def run_cli(monkeypatch, argv, result=None, raises=None):
    def fake_run_backup(config, dry_run=False, only_repo=None):
        if raises:
            raise raises
        return result if result is not None else RunResult(succeeded=["a"])

    monkeypatch.setattr(cli, "run_backup", fake_run_backup)
    return cli.main(argv)


def test_success_exits_zero(monkeypatch):
    assert run_cli(monkeypatch, []) == 0


def test_failures_exit_nonzero(monkeypatch):
    result = RunResult(succeeded=["a"], failed={"b": "boom"})
    assert run_cli(monkeypatch, [], result=result) == 1


def test_missing_config_exits_two(monkeypatch, capsys):
    monkeypatch.delenv("GITHUB_TOKEN")
    code = cli.main(["--env-file", "/nonexistent/.env"])
    assert code == 2
    assert "GITHUB_TOKEN" in capsys.readouterr().err


def test_lock_held_is_clean_exit(monkeypatch):
    assert run_cli(monkeypatch, [], raises=LockHeldError("busy")) == 0


def test_keyboard_interrupt_exit_code(monkeypatch):
    assert run_cli(monkeypatch, [], raises=KeyboardInterrupt()) == 130


def test_flags_are_passed_through(monkeypatch):
    seen = {}

    def fake_run_backup(config, dry_run=False, only_repo=None):
        seen.update(dry_run=dry_run, only_repo=only_repo)
        return RunResult()

    monkeypatch.setattr(cli, "run_backup", fake_run_backup)
    cli.main(["--dry-run", "--repo", "my-*"])
    assert seen == {"dry_run": True, "only_repo": "my-*"}


# ---------------------------------------------------------------------------
# setup subcommand
# ---------------------------------------------------------------------------


def test_setup_dispatches_to_wizard(monkeypatch):
    called = {}

    def fake_wizard(env_file=None):
        called["env_file"] = env_file
        return 0

    monkeypatch.setattr("git_dr_mirror.wizard.run_wizard", fake_wizard)
    code = cli.main(["setup"])
    assert code == 0
    assert called["env_file"] is None


def test_setup_passes_env_file(monkeypatch):
    called = {}

    def fake_wizard(env_file=None):
        called["env_file"] = env_file
        return 0

    monkeypatch.setattr("git_dr_mirror.wizard.run_wizard", fake_wizard)
    cli.main(["setup", "--env-file", "/tmp/my.env"])
    assert called["env_file"] == "/tmp/my.env"


def test_setup_propagates_exit_code(monkeypatch):
    monkeypatch.setattr("git_dr_mirror.wizard.run_wizard", lambda env_file=None: 2)
    assert cli.main(["setup"]) == 2


# ---------------------------------------------------------------------------
# check subcommand
# ---------------------------------------------------------------------------


def test_check_all_ok_exits_zero(monkeypatch, capsys):
    results = [
        CheckResult(True, "GitHub token", "authenticated"),
        CheckResult(True, "GitLab token + group 'x'", "authenticated; group visible (id 1)"),
    ]
    monkeypatch.setattr("git_dr_mirror.preflight.run_all", lambda config: results)
    code = cli.main(["check"])
    assert code == 0
    out = capsys.readouterr().out
    assert "[OK  ]" in out


def test_check_failure_exits_one(monkeypatch, capsys):
    results = [
        CheckResult(True, "GitHub token", "authenticated"),
        CheckResult(False, "GitLab token + group 'x'", "group not found (HTTP 404)",
                    hint="Create the group first."),
    ]
    monkeypatch.setattr("git_dr_mirror.preflight.run_all", lambda config: results)
    code = cli.main(["check"])
    assert code == 1
    out = capsys.readouterr().out
    assert "[FAIL]" in out
    assert "Create the group first." in out


def test_check_bad_config_exits_two(monkeypatch, capsys):
    monkeypatch.delenv("GITHUB_TOKEN")
    code = cli.main(["check", "--env-file", "/nonexistent/.env"])
    assert code == 2

