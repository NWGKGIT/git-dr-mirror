"""Read-only credential and permission checks.

Confirms — without writing anything — that the GitHub token can read repos,
the GitLab token is valid, and the configured GitLab group exists and is
visible to the token. Every check returns a :class:`CheckResult` instead of
raising, so callers (the setup wizard and the ``check`` command) can render
friendly output rather than a traceback.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import quote

import requests

from . import github, gitlab
from .config import Config
from .http_client import ApiError, request


@dataclass(frozen=True)
class CheckResult:
    """Outcome of a single preflight check."""

    ok: bool
    title: str
    detail: str
    hint: str | None = None  # remediation shown only when ok is False


def _network_hint(exc: Exception) -> str:
    return (
        f"Could not reach the server ({type(exc).__name__}). Check your "
        "network connection and the *_URL settings, then try again."
    )


def check_github(config: Config) -> CheckResult:
    """Confirm the GitHub token can list repositories."""
    title = "GitHub token"
    try:
        session = github.make_session(config)
        response = request(
            session,
            "GET",
            f"{config.github_api_url}/user/repos",
            timeout=config.http_timeout,
            retries=config.http_retries,
            params={"affiliation": config.github_affiliation, "per_page": 1},
        )
        # A valid, correctly-scoped token returns a (possibly empty) JSON list.
        count = len(response.json())
        more = response.links.get("next")
        detail = (
            "authenticated; can read repositories"
            + (" (more than one visible)" if more else f" ({count} visible on first page)")
        )
        return CheckResult(True, title, detail)
    except ApiError as exc:
        if exc.status_code == 401:
            return CheckResult(
                False, title, "authentication failed (HTTP 401)",
                "The GitHub token is invalid or expired. Create a new token at "
                "https://github.com/settings/personal-access-tokens",
            )
        if exc.status_code == 403:
            return CheckResult(
                False, title, "access forbidden (HTTP 403)",
                "The token is missing repository read access. A fine-grained "
                "token needs Contents: read-only + Metadata: read-only; a "
                "classic token needs the 'repo' scope.",
            )
        return CheckResult(
            False, title, f"GitHub API error (HTTP {exc.status_code})",
            str(exc),
        )
    except requests.RequestException as exc:
        return CheckResult(False, title, "network error", _network_hint(exc))
    except Exception as exc:  # never let the wizard explode
        return CheckResult(False, title, "unexpected error", str(exc))


def check_gitlab(config: Config) -> CheckResult:
    """Confirm the GitLab token is valid and the target group is visible."""
    title = f"GitLab token + group {config.gitlab_group!r}"
    try:
        session = gitlab.make_session(config)

        # 1. Token identity — distinguishes "bad token" from "group not visible".
        try:
            request(
                session, "GET", f"{config.gitlab_url}/api/v4/user",
                timeout=config.http_timeout, retries=config.http_retries,
            )
        except ApiError as exc:
            if exc.status_code == 401:
                return CheckResult(
                    False, title, "authentication failed (HTTP 401)",
                    "The GitLab token is invalid or expired. Create a new one "
                    "under GitLab → Preferences → Access tokens with the 'api' "
                    "and 'write_repository' scopes.",
                )
            raise

        # 2. Group exists and the token can see it.
        encoded = quote(config.gitlab_group, safe="")
        try:
            group = request(
                session, "GET", f"{config.gitlab_url}/api/v4/groups/{encoded}",
                timeout=config.http_timeout, retries=config.http_retries,
            ).json()
        except ApiError as exc:
            if exc.status_code == 404:
                return CheckResult(
                    False, title, "group not found (HTTP 404)",
                    f"The group {config.gitlab_group!r} does not exist, or the "
                    "token cannot see it. Create the group in the GitLab UI "
                    "first (top-level group creation is restricted), then set "
                    "GITLAB_GROUP to its path.",
                )
            if exc.status_code == 403:
                return CheckResult(
                    False, title, "access forbidden (HTTP 403)",
                    "The token is valid but cannot access this group. Make sure "
                    "the token owner is a member with at least Maintainer role.",
                )
            raise

        detail = f"authenticated; group visible (id {group.get('id', '?')})"
        return CheckResult(True, title, detail)
    except ApiError as exc:
        return CheckResult(
            False, title, f"GitLab API error (HTTP {exc.status_code})", str(exc),
        )
    except requests.RequestException as exc:
        return CheckResult(False, title, "network error", _network_hint(exc))
    except Exception as exc:  # never let the wizard explode
        return CheckResult(False, title, "unexpected error", str(exc))


def run_all(config: Config) -> list[CheckResult]:
    """Run every preflight check and return the results in display order."""
    return [check_github(config), check_gitlab(config)]
