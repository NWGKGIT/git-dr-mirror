"""Tests for configuration loading and validation."""

from pathlib import Path

import pytest

from git_dr_mirror.config import ConfigError, load_config

REQUIRED = {
    "GITHUB_TOKEN": "gh-token",
    "GITLAB_TOKEN": "gl-token",
    "GITLAB_GROUP": "my-backup",
}

OPTIONAL_VARS = (
    "GITLAB_URL",
    "GITHUB_API_URL",
    "MIRROR_DIR",
    "GITHUB_AFFILIATION",
    "INCLUDE_FORKS",
    "EXCLUDE_REPOS",
    "GITLAB_VISIBILITY",
    "HTTP_TIMEOUT",
    "HTTP_RETRIES",
    "GIT_TIMEOUT",
    "LOG_LEVEL",
)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """Give every test a clean slate for all config variables."""
    for name in (*REQUIRED, *OPTIONAL_VARS):
        monkeypatch.delenv(name, raising=False)


def set_required(monkeypatch):
    for name, value in REQUIRED.items():
        monkeypatch.setenv(name, value)


def load(tmp_path):
    """Load config without picking up a developer's real .env file."""
    return load_config(env_file=tmp_path / "nonexistent.env")


def test_defaults(monkeypatch, tmp_path):
    set_required(monkeypatch)
    cfg = load(tmp_path)
    assert cfg.github_token == "gh-token"
    assert cfg.gitlab_token == "gl-token"
    assert cfg.gitlab_group == "my-backup"
    assert cfg.gitlab_url == "https://gitlab.com"
    assert cfg.github_api_url == "https://api.github.com"
    assert cfg.mirror_dir == Path.home() / "github-backup"
    assert cfg.github_affiliation == "owner"
    assert cfg.include_forks is False
    assert cfg.exclude_repos == []
    assert cfg.gitlab_visibility == "private"
    assert cfg.http_timeout == 30
    assert cfg.http_retries == 3
    assert cfg.git_timeout == 3600
    assert cfg.log_level == "INFO"


@pytest.mark.parametrize("missing", sorted(REQUIRED))
def test_missing_required_var_fails_fast(monkeypatch, tmp_path, missing):
    set_required(monkeypatch)
    monkeypatch.delenv(missing)
    with pytest.raises(ConfigError, match=missing):
        load(tmp_path)


def test_all_missing_lists_every_var(tmp_path):
    with pytest.raises(ConfigError) as exc:
        load(tmp_path)
    for name in REQUIRED:
        assert name in str(exc.value)


def test_env_file_is_read(monkeypatch, tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("GITHUB_TOKEN=file-gh\nGITLAB_TOKEN=file-gl\nGITLAB_GROUP=file-group\n")
    cfg = load_config(env_file=env_file)
    assert cfg.github_token == "file-gh"
    assert cfg.gitlab_group == "file-group"


def test_real_environment_beats_env_file(monkeypatch, tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("GITHUB_TOKEN=file-gh\nGITLAB_TOKEN=file-gl\nGITLAB_GROUP=file-group\n")
    monkeypatch.setenv("GITHUB_TOKEN", "env-gh")
    cfg = load_config(env_file=env_file)
    assert cfg.github_token == "env-gh"


def test_bool_parsing(monkeypatch, tmp_path):
    set_required(monkeypatch)
    for raw, expected in [
        ("true", True),
        ("1", True),
        ("YES", True),
        ("false", False),
        ("0", False),
        ("off", False),
    ]:
        monkeypatch.setenv("INCLUDE_FORKS", raw)
        assert load(tmp_path).include_forks is expected


def test_invalid_bool_rejected(monkeypatch, tmp_path):
    set_required(monkeypatch)
    monkeypatch.setenv("INCLUDE_FORKS", "maybe")
    with pytest.raises(ConfigError, match="INCLUDE_FORKS"):
        load(tmp_path)


def test_exclude_repos_list_parsing(monkeypatch, tmp_path):
    set_required(monkeypatch)
    monkeypatch.setenv("EXCLUDE_REPOS", "scratch-*, *-archive ,tmp,")
    assert load(tmp_path).exclude_repos == ["scratch-*", "*-archive", "tmp"]


def test_int_parsing_and_validation(monkeypatch, tmp_path):
    set_required(monkeypatch)
    monkeypatch.setenv("HTTP_TIMEOUT", "60")
    assert load(tmp_path).http_timeout == 60

    monkeypatch.setenv("HTTP_TIMEOUT", "abc")
    with pytest.raises(ConfigError, match="HTTP_TIMEOUT"):
        load(tmp_path)

    monkeypatch.setenv("HTTP_TIMEOUT", "0")
    with pytest.raises(ConfigError, match="HTTP_TIMEOUT"):
        load(tmp_path)


def test_invalid_visibility_rejected(monkeypatch, tmp_path):
    set_required(monkeypatch)
    monkeypatch.setenv("GITLAB_VISIBILITY", "hidden")
    with pytest.raises(ConfigError, match="GITLAB_VISIBILITY"):
        load(tmp_path)


def test_urls_and_group_normalized(monkeypatch, tmp_path):
    set_required(monkeypatch)
    monkeypatch.setenv("GITLAB_URL", "https://gitlab.example.com/")
    monkeypatch.setenv("GITLAB_GROUP", "/team/backup/")
    cfg = load(tmp_path)
    assert cfg.gitlab_url == "https://gitlab.example.com"
    assert cfg.gitlab_group == "team/backup"


def test_mirror_dir_expands_home(monkeypatch, tmp_path):
    set_required(monkeypatch)
    monkeypatch.setenv("MIRROR_DIR", "~/custom-backup")
    cfg = load(tmp_path)
    assert cfg.mirror_dir == Path.home() / "custom-backup"
    assert "~" not in str(cfg.mirror_dir)


def test_config_is_immutable(monkeypatch, tmp_path):
    set_required(monkeypatch)
    cfg = load(tmp_path)
    with pytest.raises(Exception):
        cfg.github_token = "other"  # type: ignore[misc]
