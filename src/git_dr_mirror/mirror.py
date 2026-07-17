"""Local bare-mirror cache and git transfer operations.

Layout: one bare mirror per repository at ``MIRROR_DIR/<repo>.git``.
New repositories are cloned with ``git clone --mirror``; existing ones are
updated with ``git remote update --prune``. The cache mirrors GitHub exactly
and doubles as an offline backup.

Security model: remotes store tokenless HTTPS URLs. Credentials are injected
per-invocation through an ephemeral git credential helper that reads
environment variables, so tokens never appear in command lines, remote
config, or the mirror directory.
"""

from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path

from .config import Config
from .github import Repo

log = logging.getLogger(__name__)

#: Credential helper that answers with credentials from the environment.
#: The leading `credential.helper=` (empty) clears any system-configured
#: helpers (keychains, credential managers) so runs are non-interactive
#: and never touch the user's stored credentials.
_CREDENTIAL_ARGS = (
    "-c", "credential.helper=",
    "-c",
    'credential.helper=!f() { echo "username=${GIT_DR_USER}"; echo "password=${GIT_DR_TOKEN}"; }; f',
)

#: Mirror refspecs for pushing: branches and tags, with deletions propagated
#: within those namespaces only. GitHub's read-only refs/pull/* refs are not
#: pushed — GitLab rejects writes to hidden refs.
PUSH_REFSPECS = ("+refs/heads/*:refs/heads/*", "+refs/tags/*:refs/tags/*")


class MirrorError(Exception):
    """A git operation failed."""


def _run_git(
    args: list[str],
    *,
    config: Config,
    username: str,
    token: str,
    cwd: Path | None = None,
    retries: int = 1,
) -> subprocess.CompletedProcess[str]:
    """Run a git command with injected credentials and a hard timeout.

    Transient failures get one retry by default (``retries``); a repeated
    failure raises :class:`MirrorError` with the captured stderr.
    """
    command = ["git", *_CREDENTIAL_ARGS, *args]
    env = {
        **os.environ,
        "GIT_DR_USER": username,
        "GIT_DR_TOKEN": token,
        # Never hang waiting for input on a headless run.
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_ASKPASS": "",
    }

    last_stderr = ""
    for attempt in range(retries + 1):
        if attempt:
            log.warning("Retrying: git %s", args[0])
        try:
            result = subprocess.run(
                command,
                cwd=cwd,
                env=env,
                capture_output=True,
                text=True,
                timeout=config.git_timeout,
            )
        except subprocess.TimeoutExpired:
            last_stderr = f"timed out after {config.git_timeout}s"
            continue
        if result.returncode == 0:
            return result
        last_stderr = result.stderr.strip()
        log.warning("git %s failed (exit %d): %s", args[0], result.returncode, last_stderr[-500:])

    raise MirrorError(f"git {args[0]} failed: {last_stderr[-1000:]}")


def local_mirror_path(config: Config, repo_name: str) -> Path:
    return config.mirror_dir / f"{repo_name}.git"


def _is_valid_bare_repo(path: Path) -> bool:
    """Cheap health check: does git recognize this directory as a bare repo?"""
    if not path.is_dir():
        return False
    result = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "--is-bare-repository"],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0 and result.stdout.strip() == "true"


def _quarantine(path: Path) -> Path:
    """Move a broken mirror aside (never delete — it may still hold data)."""
    target = path.with_name(path.name + ".corrupt")
    counter = 0
    while target.exists():
        counter += 1
        target = path.with_name(f"{path.name}.corrupt.{counter}")
    path.rename(target)
    log.warning("Quarantined invalid mirror %s -> %s", path.name, target.name)
    return target


def update_local_mirror(config: Config, repo: Repo) -> Path:
    """Clone or update the local bare mirror of a GitHub repository.

    Handles interrupted previous runs: a directory that isn't a valid bare
    repository (e.g. a clone killed by a power-off) is quarantined and the
    repository is recloned from scratch.
    """
    path = local_mirror_path(config, repo.name)
    config.mirror_dir.mkdir(parents=True, exist_ok=True)

    if path.exists() and not _is_valid_bare_repo(path):
        _quarantine(path)

    if not path.exists():
        log.info("Cloning mirror: %s", repo.name)
        try:
            _run_git(
                ["clone", "--mirror", repo.clone_url, str(path)],
                config=config,
                username="x-access-token",
                token=config.github_token,
                retries=0,
            )
        except MirrorError:
            # A partial clone directory would poison the next run's
            # validity check less confusingly if quarantined now.
            if path.exists():
                _quarantine(path)
            raise
    else:
        log.info("Updating mirror: %s", repo.name)
        _run_git(
            ["remote", "update", "--prune"],
            config=config,
            username="x-access-token",
            token=config.github_token,
            cwd=path,
        )
    return path


def push_to_gitlab(config: Config, repo_name: str, gitlab_push_url: str) -> None:
    """Push the local mirror's branches and tags to the GitLab backup project.

    The push URL is passed per-invocation and never stored in the mirror's
    git config, so the cache stays portable and credential-free.
    """
    path = local_mirror_path(config, repo_name)
    log.info("Pushing to GitLab: %s", repo_name)
    _run_git(
        ["push", "--prune", gitlab_push_url, *PUSH_REFSPECS],
        config=config,
        username="oauth2",
        token=config.gitlab_token,
        cwd=path,
    )
