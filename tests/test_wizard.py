"""Tests for the setup wizard's non-interactive pieces."""

from __future__ import annotations

import pytest

from git_dr_mirror import wizard
from git_dr_mirror.config import Config
from git_dr_mirror.preflight import CheckResult


def feed_input(monkeypatch, answers):
    """Make input() and getpass() consume the given answers in order.

    When the answers run out, further reads raise EOFError, exactly like a
    closed stdin.
    """
    answers = iter(answers)

    def read(prompt_text=""):
        try:
            return next(answers)
        except StopIteration:
            raise EOFError from None

    monkeypatch.setattr("builtins.input", read)
    monkeypatch.setattr(wizard.getpass, "getpass", read)


# ---------------------------------------------------------------------------
# Environment preflight
# ---------------------------------------------------------------------------


def test_windows_is_rejected(monkeypatch):
    monkeypatch.setattr(wizard.sys, "platform", "win32")
    msg = wizard._check_environment()
    assert msg is not None
    assert "Unix" in msg


def test_missing_git_is_reported(monkeypatch):
    monkeypatch.setattr(wizard.sys, "platform", "linux")
    monkeypatch.setattr(wizard.shutil, "which", lambda name: None)
    msg = wizard._check_environment()
    assert msg is not None
    assert "git" in msg.lower()


def test_healthy_environment_returns_none(monkeypatch):
    monkeypatch.setattr(wizard.sys, "platform", "linux")
    monkeypatch.setattr(wizard.shutil, "which", lambda name: "/usr/bin/git")
    assert wizard._check_environment() is None


# ---------------------------------------------------------------------------
# .env read / write round-trip
# ---------------------------------------------------------------------------


def test_write_env_preserves_comments_and_other_keys(tmp_path):
    env = tmp_path / ".env"
    env.write_text("# a comment\nGITHUB_TOKEN=old\n\n# keep me\nMIRROR_DIR=~/backup\n")
    wizard._write_env(env, {"GITHUB_TOKEN": "new", "GITLAB_TOKEN": "added"})
    text = env.read_text()
    assert "# a comment" in text
    assert "# keep me" in text
    assert "GITHUB_TOKEN=new" in text
    assert "MIRROR_DIR=~/backup" in text
    assert "GITLAB_TOKEN=added" in text


def test_write_env_sets_mode_600(tmp_path):
    env = tmp_path / ".env"
    wizard._write_env(env, {"GITHUB_TOKEN": "x"})
    assert (env.stat().st_mode & 0o777) == 0o600


def test_read_env_parses_keys(tmp_path):
    env = tmp_path / ".env"
    env.write_text("# c\nA=1\nB = two \n\nbad line\n")
    values = wizard._read_env(env)
    assert values["A"] == "1"
    assert values["B"] == "two"
    assert "bad line" not in values


def test_mask_hides_secret():
    assert wizard._mask("abcdef123") == "…f123"
    assert wizard._mask("") == "(empty)"
    assert wizard._mask("ab") == "****"


# ---------------------------------------------------------------------------
# Prompt validation loops
# ---------------------------------------------------------------------------


def test_required_field_reprompts_on_empty(monkeypatch, capsys):
    feed_input(monkeypatch, ["", "", "ghp_token"])
    value = wizard._prompt_field("GITHUB_TOKEN", current="")
    assert value == "ghp_token"
    assert "required" in capsys.readouterr().out


def test_token_with_spaces_reprompts(monkeypatch, capsys):
    feed_input(monkeypatch, ["bad token", "goodtoken"])
    value = wizard._prompt_field("GITLAB_TOKEN", current="")
    assert value == "goodtoken"
    assert "spaces" in capsys.readouterr().out


def test_invalid_url_reprompts(monkeypatch, capsys):
    feed_input(monkeypatch, ["not a url", "https://gitlab.example.com"])
    value = wizard._prompt_field("GITLAB_URL", current="")
    assert value == "https://gitlab.example.com"
    assert "full URL" in capsys.readouterr().out


def test_invalid_visibility_reprompts(monkeypatch, capsys):
    feed_input(monkeypatch, ["hidden", "Public"])
    value = wizard._prompt_field("GITLAB_VISIBILITY", current="")
    assert value == "public"  # also lower-cased
    assert "private, internal, public" in capsys.readouterr().out


def test_pasted_group_url_is_reduced_to_path(monkeypatch):
    feed_input(monkeypatch, ["https://gitlab.com/my-team/backup/"])
    value = wizard._prompt_field("GITLAB_GROUP", current="")
    assert value == "my-team/backup"


def test_enter_keeps_current_value(monkeypatch):
    feed_input(monkeypatch, [""])
    value = wizard._prompt_field("GITHUB_TOKEN", current="existing")
    assert value == "existing"


def test_optional_field_enter_accepts_default(monkeypatch):
    feed_input(monkeypatch, [""])
    assert wizard._prompt_field("MIRROR_DIR", current="") == "~/github-backup"


def test_eof_during_required_prompt_raises_abort(monkeypatch):
    feed_input(monkeypatch, [])  # stdin exhausted immediately
    with pytest.raises(wizard.WizardAbort):
        wizard._prompt_field("GITHUB_TOKEN", current="")


# ---------------------------------------------------------------------------
# Verify-until-ok loop
# ---------------------------------------------------------------------------


def _fake_config():
    return Config(github_token="g", gitlab_token="l", gitlab_group="grp")


@pytest.fixture()
def env_path(tmp_path, monkeypatch):
    # Keep the wizard's os.environ.update() and load_config from leaking or
    # inheriting real values.
    for name in ("GITHUB_TOKEN", "GITLAB_TOKEN", "GITLAB_GROUP"):
        monkeypatch.delenv(name, raising=False)
    return tmp_path / ".env"


def test_verify_loop_retries_failing_fields(monkeypatch, env_path, capsys):
    """First check fails on the GitHub token; user re-enters it; second passes."""
    calls = {"n": 0}

    def fake_run_all(config):
        calls["n"] += 1
        if calls["n"] == 1:
            return [
                CheckResult(
                    False,
                    "GitHub token",
                    "authentication failed",
                    hint="bad token",
                    keys=("GITHUB_TOKEN",),
                ),
                CheckResult(
                    True, "GitLab token + group 'grp'", "ok", keys=("GITLAB_TOKEN", "GITLAB_GROUP")
                ),
            ]
        return [
            CheckResult(True, "GitHub token", "ok", keys=("GITHUB_TOKEN",)),
            CheckResult(
                True, "GitLab token + group 'grp'", "ok", keys=("GITLAB_TOKEN", "GITLAB_GROUP")
            ),
        ]

    monkeypatch.setattr("git_dr_mirror.preflight.run_all", fake_run_all)
    # Answers: "retry?" -> yes (default), then the corrected GitHub token only.
    feed_input(monkeypatch, ["", "corrected-token"])

    config = wizard._verify_until_ok(
        env_path,
        {"GITHUB_TOKEN": "bad", "GITLAB_TOKEN": "l", "GITLAB_GROUP": "grp"},
    )
    assert config is not None
    assert calls["n"] == 2
    assert "GITHUB_TOKEN=corrected-token" in env_path.read_text()
    # The passing GitLab values were not re-prompted (only 2 answers consumed).


def test_verify_loop_declining_retry_returns_none(monkeypatch, env_path, capsys):
    def fake_run_all(config):
        return [
            CheckResult(
                False,
                "GitHub token",
                "authentication failed",
                hint="bad token",
                keys=("GITHUB_TOKEN",),
            ),
        ]

    monkeypatch.setattr("git_dr_mirror.preflight.run_all", fake_run_all)
    feed_input(monkeypatch, ["n"])  # decline the retry

    config = wizard._verify_until_ok(
        env_path,
        {"GITHUB_TOKEN": "bad", "GITLAB_TOKEN": "l", "GITLAB_GROUP": "grp"},
    )
    assert config is None
    assert "setup" in capsys.readouterr().out  # resume instructions printed


def test_verify_loop_passes_first_try(monkeypatch, env_path):
    def fake_run_all(config):
        return [CheckResult(True, "GitHub token", "ok", keys=("GITHUB_TOKEN",))]

    monkeypatch.setattr("git_dr_mirror.preflight.run_all", fake_run_all)
    feed_input(monkeypatch, [])  # no input should be needed

    config = wizard._verify_until_ok(
        env_path,
        {"GITHUB_TOKEN": "g", "GITLAB_TOKEN": "l", "GITLAB_GROUP": "grp"},
    )
    assert config is not None
    assert config.gitlab_group == "grp"
