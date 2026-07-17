"""GitLab project management: ensure backup projects exist.

Everything here is ensure-style (GET first, POST only when missing) so runs
are idempotent and safe to interrupt. This module never issues a DELETE —
the disaster-recovery guarantee is that GitLab only ever gains data.
"""

from __future__ import annotations

import hashlib
import logging
import re
from urllib.parse import quote

import requests

from .config import Config
from .http_client import ApiError, request

log = logging.getLogger(__name__)


class GitLabError(Exception):
    """A GitLab operation failed."""


# GitLab project paths: non-accented letters, digits, '_', '-', '.'; must
# start and end with a letter or digit; must not end in '.git' or '.atom'.
_VALID_PATH = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9_.-]*[A-Za-z0-9])?$")


def sanitize_path(repo_name: str) -> str:
    """GitLab project path for a GitHub repo name.

    GitHub allows names GitLab paths reject (e.g. a trailing '-'). Valid
    names are used unchanged. Invalid ones are cleaned up and a short hash
    of the original name is appended, so two different GitHub names can
    never map to the same GitLab project.
    """
    if _VALID_PATH.match(repo_name) and not repo_name.endswith((".git", ".atom")):
        return repo_name
    cleaned = re.sub(r"[^A-Za-z0-9_.-]", "-", repo_name).strip("-_.")
    digest = hashlib.sha1(repo_name.encode()).hexdigest()[:7]
    return f"{cleaned}-{digest}" if cleaned else digest


def make_session(config: Config) -> requests.Session:
    session = requests.Session()
    session.headers.update({"PRIVATE-TOKEN": config.gitlab_token})
    return session


def project_path(config: Config, repo_name: str) -> str:
    """Full GitLab path of the backup project for a GitHub repo."""
    return f"{config.gitlab_group}/{sanitize_path(repo_name)}"


def push_url(config: Config, repo_name: str) -> str:
    """Tokenless HTTPS URL to push the mirror to (auth injected at git time)."""
    return f"{config.gitlab_url}/{project_path(config, repo_name)}.git"


def _get_json(config, session, url, **kwargs):
    response = request(
        session, "GET", url,
        timeout=config.http_timeout, retries=config.http_retries, **kwargs,
    )
    return response.json()


def _lookup_project(config: Config, session: requests.Session, path: str) -> dict | None:
    """Return the GitLab project at ``path``, or None if it doesn't exist."""
    encoded = quote(path, safe="")
    try:
        return _get_json(config, session, f"{config.gitlab_url}/api/v4/projects/{encoded}")
    except ApiError as exc:
        if exc.status_code == 404:
            return None
        raise


def _group_id(config: Config, session: requests.Session) -> int:
    """Resolve the configured backup group to its numeric ID.

    The group must already exist — creating top-level groups is restricted
    on gitlab.com, and requiring it up front keeps the token's blast radius
    small.
    """
    encoded = quote(config.gitlab_group, safe="")
    try:
        group = _get_json(config, session, f"{config.gitlab_url}/api/v4/groups/{encoded}")
    except ApiError as exc:
        if exc.status_code == 404:
            raise GitLabError(
                f"GitLab group {config.gitlab_group!r} does not exist (or the "
                "token cannot see it). Create the group in the GitLab UI and "
                "make sure the token has access to it."
            ) from exc
        raise
    return group["id"]


def ensure_project(config: Config, repo_name: str,
                   session: requests.Session | None = None,
                   description: str | None = None) -> str:
    """Make sure the backup project for ``repo_name`` exists on GitLab.

    Returns the tokenless HTTPS push URL for the project. Idempotent:
    an existing project is left completely untouched.
    """
    session = session or make_session(config)
    path = project_path(config, repo_name)

    existing = _lookup_project(config, session, path)
    if existing is not None:
        log.debug("GitLab project exists: %s", path)
        return push_url(config, repo_name)

    namespace_id = _group_id(config, session)
    log.info("Creating GitLab project: %s", path)
    request(
        session,
        "POST",
        f"{config.gitlab_url}/api/v4/projects",
        timeout=config.http_timeout,
        retries=config.http_retries,
        json={
            # Display name keeps the original GitHub repo name; the URL
            # path is sanitized to satisfy GitLab's stricter rules.
            "name": repo_name,
            "path": sanitize_path(repo_name),
            "namespace_id": namespace_id,
            "visibility": config.gitlab_visibility,
            "description": (description or "Disaster-recovery mirror (managed by git-dr-mirror)")[:250],
            # Backups don't need CI pipelines running on every push.
            "jobs_enabled": False,
        },
    )
    return push_url(config, repo_name)
