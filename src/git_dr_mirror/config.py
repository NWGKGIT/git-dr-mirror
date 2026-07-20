"""Configuration loading and validation.

All settings come from environment variables, optionally seeded from a `.env`
file in the working directory (real environment variables win). Required
settings are validated up front so a misconfigured run fails fast with a
clear message instead of half-way through a backup.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

#: Environment variables that must be set for a run to proceed.
REQUIRED_VARS = ("GITHUB_TOKEN", "GITLAB_TOKEN", "GITLAB_GROUP")

_TRUE_VALUES = {"1", "true", "yes", "on"}
_FALSE_VALUES = {"0", "false", "no", "off"}

_VALID_VISIBILITIES = {"private", "internal", "public"}


class ConfigError(Exception):
    """Raised when configuration is missing or invalid."""


def _get_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    if raw in _TRUE_VALUES:
        return True
    if raw in _FALSE_VALUES:
        return False
    raise ConfigError(f"{name} must be a boolean (true/false), got {raw!r}")


def _get_int(name: str, default: int, *, minimum: int = 0) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        raise ConfigError(f"{name} must be an integer, got {raw!r}") from None
    if value < minimum:
        raise ConfigError(f"{name} must be >= {minimum}, got {value}")
    return value


def _get_list(name: str) -> list[str]:
    raw = os.environ.get(name, "")
    return [item.strip() for item in raw.split(",") if item.strip()]


@dataclass(frozen=True)
class Config:
    """Validated runtime configuration for one backup run."""

    github_token: str
    gitlab_token: str
    gitlab_group: str
    gitlab_url: str = "https://gitlab.com"
    github_api_url: str = "https://api.github.com"
    mirror_dir: Path = field(default_factory=lambda: Path.home() / "github-backup")
    github_affiliation: str = "owner"
    include_forks: bool = False
    exclude_repos: list[str] = field(default_factory=list)
    gitlab_visibility: str = "private"
    http_timeout: int = 30
    http_retries: int = 3
    git_timeout: int = 3600
    log_level: str = "INFO"


def load_config(env_file: str | os.PathLike[str] | None = None) -> Config:
    """Load configuration from the environment (and optionally a .env file).

    Args:
        env_file: Path to a dotenv file. ``None`` means "look for `.env` in
            the current directory", which is python-dotenv's default.

    Raises:
        ConfigError: If a required variable is missing or a value is invalid.
    """
    # override=False: real environment variables beat .env file contents.
    load_dotenv(dotenv_path=env_file, override=False)

    missing = [name for name in REQUIRED_VARS if not os.environ.get(name, "").strip()]
    if missing:
        raise ConfigError(
            "Missing required configuration: "
            + ", ".join(missing)
            + ". Set them in the environment or in a .env file "
            "(see .env.example)."
        )

    visibility = os.environ.get("GITLAB_VISIBILITY", "").strip() or "private"
    if visibility not in _VALID_VISIBILITIES:
        raise ConfigError(
            f"GITLAB_VISIBILITY must be one of {sorted(_VALID_VISIBILITIES)}, got {visibility!r}"
        )

    mirror_dir_raw = os.environ.get("MIRROR_DIR", "").strip() or "~/github-backup"

    return Config(
        github_token=os.environ["GITHUB_TOKEN"].strip(),
        gitlab_token=os.environ["GITLAB_TOKEN"].strip(),
        gitlab_group=os.environ["GITLAB_GROUP"].strip().strip("/"),
        gitlab_url=(os.environ.get("GITLAB_URL", "").strip() or "https://gitlab.com").rstrip("/"),
        github_api_url=(
            os.environ.get("GITHUB_API_URL", "").strip() or "https://api.github.com"
        ).rstrip("/"),
        mirror_dir=Path(mirror_dir_raw).expanduser(),
        github_affiliation=os.environ.get("GITHUB_AFFILIATION", "").strip() or "owner",
        include_forks=_get_bool("INCLUDE_FORKS", default=False),
        exclude_repos=_get_list("EXCLUDE_REPOS"),
        gitlab_visibility=visibility,
        http_timeout=_get_int("HTTP_TIMEOUT", default=30, minimum=1),
        http_retries=_get_int("HTTP_RETRIES", default=3, minimum=0),
        git_timeout=_get_int("GIT_TIMEOUT", default=3600, minimum=1),
        log_level=(os.environ.get("LOG_LEVEL", "").strip() or "INFO").upper(),
    )
