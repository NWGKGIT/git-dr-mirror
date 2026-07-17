"""GitHub repository discovery.

Queries the GitHub API for the complete list of accessible repositories on
every run, so newly created repositories are picked up automatically and the
backup never depends on what happens to be cloned locally.
"""

from __future__ import annotations

import fnmatch
import logging
from dataclasses import dataclass

import requests

from .config import Config
from .http_client import request

log = logging.getLogger(__name__)

PER_PAGE = 100


@dataclass(frozen=True)
class Repo:
    """The subset of GitHub repository metadata this tool needs."""

    name: str
    clone_url: str  # tokenless HTTPS URL; auth is injected at git run time
    fork: bool
    description: str | None = None


def make_session(config: Config) -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "Authorization": f"Bearer {config.github_token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
    )
    return session


def _excluded(name: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(name, pattern) for pattern in patterns)


def list_repos(config: Config, session: requests.Session | None = None) -> list[Repo]:
    """Return all repositories to back up, after scope filtering.

    Paginates ``GET /user/repos`` with the configured affiliation filter,
    then drops forks (unless ``INCLUDE_FORKS``) and anything matching an
    ``EXCLUDE_REPOS`` glob.
    """
    session = session or make_session(config)
    repos: list[Repo] = []
    url = f"{config.github_api_url}/user/repos"
    params: dict[str, str | int] | None = {
        "affiliation": config.github_affiliation,
        "per_page": PER_PAGE,
        "sort": "full_name",
    }

    while url:
        response = request(
            session,
            "GET",
            url,
            timeout=config.http_timeout,
            retries=config.http_retries,
            params=params,
        )
        for item in response.json():
            repo = Repo(
                name=item["name"],
                clone_url=item["clone_url"],
                fork=item.get("fork", False),
                description=item.get("description"),
            )
            if repo.fork and not config.include_forks:
                log.debug("Skipping fork: %s", repo.name)
                continue
            if _excluded(repo.name, config.exclude_repos):
                log.info("Skipping excluded repo: %s", repo.name)
                continue
            repos.append(repo)

        # Follow RFC 5988 pagination; params are baked into the next link.
        url = response.links.get("next", {}).get("url")
        params = None

    log.info("Discovered %d repositories to back up", len(repos))
    return repos
