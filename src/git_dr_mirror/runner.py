"""Backup run orchestration.

One run: acquire the lock, discover repositories on GitHub, then for each
repository update the local mirror and push it to GitLab. A failure in one
repository is logged and never stops the others; the run's exit status
reflects whether everything succeeded so schedulers can alert on failures.
"""

from __future__ import annotations

import fnmatch
import logging
from dataclasses import dataclass, field

from . import github, gitlab, mirror
from .config import Config
from .lock import LockHeldError, run_lock

log = logging.getLogger(__name__)


@dataclass
class RunResult:
    """Outcome of one backup run."""

    succeeded: list[str] = field(default_factory=list)
    failed: dict[str, str] = field(default_factory=dict)  # repo -> error message

    @property
    def exit_code(self) -> int:
        return 1 if self.failed else 0

    def summary(self) -> str:
        parts = [f"{len(self.succeeded)} ok", f"{len(self.failed)} failed"]
        return ", ".join(parts)


def backup_repo(config: Config, repo: github.Repo,
                gitlab_session, dry_run: bool = False) -> None:
    """Back up a single repository: local mirror, GitLab project, push."""
    if dry_run:
        log.info("[dry-run] Would mirror %s and push to %s",
                 repo.name, gitlab.push_url(config, repo.name))
        return
    mirror.update_local_mirror(config, repo)
    url = gitlab.ensure_project(
        config, repo.name, session=gitlab_session, description=repo.description
    )
    mirror.push_to_gitlab(config, repo.name, url)


def run_backup(config: Config, *, dry_run: bool = False,
               only_repo: str | None = None) -> RunResult:
    """Execute one full backup run. Returns per-repo results.

    Args:
        dry_run: Discover and report, but change nothing anywhere.
        only_repo: Glob pattern; process only matching repositories
            (useful for testing a single repo end-to-end).

    Raises:
        LockHeldError: If another run is already in progress.
    """
    result = RunResult()
    with run_lock(config.mirror_dir):
        try:
            repos = github.list_repos(config)
        except Exception as exc:
            # Discovery failing means nothing can proceed — report clearly.
            log.error("Repository discovery failed: %s", exc)
            result.failed["<discovery>"] = str(exc)
            return result

        if only_repo:
            repos = [r for r in repos if fnmatch.fnmatch(r.name, only_repo)]
            if not repos:
                log.warning("No repositories match --repo %s", only_repo)

        gitlab_session = gitlab.make_session(config)
        for repo in repos:
            try:
                backup_repo(config, repo, gitlab_session, dry_run=dry_run)
                result.succeeded.append(repo.name)
            except Exception as exc:
                # Isolate failures: log and keep going with the next repo.
                log.error("Backup failed for %s: %s", repo.name, exc)
                result.failed[repo.name] = str(exc)

    log.info("Run complete: %s", result.summary())
    if result.failed:
        for name, error in result.failed.items():
            log.error("FAILED %s: %s", name, error.splitlines()[0] if error else "")
    return result
