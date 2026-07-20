"""Tests for the setup wizard's non-interactive pieces."""

from __future__ import annotations

from git_dr_mirror import wizard


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
    env.write_text(
        "# a comment\n"
        "GITHUB_TOKEN=old\n"
        "\n"
        "# keep me\n"
        "MIRROR_DIR=~/backup\n"
    )
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
