"""Tests for GitLab project ensure logic."""

import pytest

from git_dr_mirror.gitlab import (
    GitLabError,
    ensure_project,
    make_session,
    project_path,
    push_url,
    sanitize_path,
)

from conftest import FakeResponse, FakeSession


def test_sanitize_valid_names_unchanged():
    for name in ("repo", "My-Repo", "a", "dot.name", "under_score", "x2.y-z_9"):
        assert sanitize_path(name) == name


def test_sanitize_invalid_names():
    # A trailing '-' is legal on GitHub but not in a GitLab path.
    assert sanitize_path("Nadhirix-") == "Nadhirix-6bff2b2"
    # Forbidden suffixes and leading punctuation.
    assert sanitize_path("repo.git").startswith("repo")
    assert sanitize_path("repo.git") != "repo.git"
    assert not sanitize_path(".hidden").startswith(".")
    # Even a name with no salvageable characters yields a usable path.
    assert sanitize_path("---")


def test_sanitize_never_collides():
    # 'foo-' must not land on top of the distinct repo 'foo'.
    assert sanitize_path("foo-") != sanitize_path("foo")
    # Deterministic across runs (idempotency depends on it).
    assert sanitize_path("foo-") == sanitize_path("foo-")


def test_invalid_name_created_with_sanitized_path(config):
    session = FakeSession(
        [
            FakeResponse(404),  # project lookup
            FakeResponse(200, json_data={"id": 7}),  # group lookup
            FakeResponse(201, json_data={"id": 99}),  # project create
        ]
    )
    url = ensure_project(config, "Nadhirix-", session=session)
    assert url == "https://gitlab.com/backup-group/Nadhirix-6bff2b2.git"
    # Lookup uses the sanitized path too, so reruns find the project.
    assert session.calls[0]["url"].endswith("/projects/backup-group%2FNadhirix-6bff2b2")
    create = session.calls[2]
    assert create["json"]["path"] == "Nadhirix-6bff2b2"
    # The display name stays as the original GitHub repo name.
    assert create["json"]["name"] == "Nadhirix-"


def test_paths_and_urls(config):
    assert project_path(config, "my-repo") == "backup-group/my-repo"
    assert push_url(config, "my-repo") == "https://gitlab.com/backup-group/my-repo.git"
    # Push URL must never contain the token.
    assert "gl-token" not in push_url(config, "my-repo")


def test_existing_project_left_untouched(config):
    session = FakeSession([FakeResponse(200, json_data={"id": 42})])
    url = ensure_project(config, "my-repo", session=session)
    assert url == "https://gitlab.com/backup-group/my-repo.git"
    # Only the lookup GET — no POST, no modification of the existing project.
    assert len(session.calls) == 1
    assert session.calls[0]["method"] == "GET"
    assert session.calls[0]["url"].endswith("/projects/backup-group%2Fmy-repo")


def test_missing_project_created_in_group(config):
    session = FakeSession(
        [
            FakeResponse(404),  # project lookup
            FakeResponse(200, json_data={"id": 7}),  # group lookup
            FakeResponse(201, json_data={"id": 99}),  # project create
        ]
    )
    url = ensure_project(config, "new-repo", session=session)
    assert url == "https://gitlab.com/backup-group/new-repo.git"

    create = session.calls[2]
    assert create["method"] == "POST"
    assert create["url"].endswith("/api/v4/projects")
    assert create["json"]["name"] == "new-repo"
    assert create["json"]["namespace_id"] == 7
    assert create["json"]["visibility"] == "private"


def test_missing_group_gives_actionable_error(config):
    session = FakeSession(
        [
            FakeResponse(404),  # project lookup
            FakeResponse(404),  # group lookup
        ]
    )
    with pytest.raises(GitLabError, match="backup-group"):
        ensure_project(config, "new-repo", session=session)
    # Nothing was created.
    assert all(c["method"] == "GET" for c in session.calls)


def test_subgroup_paths_are_url_encoded(config):
    from dataclasses import replace

    config = replace(config, gitlab_group="team/sub")
    session = FakeSession([FakeResponse(200, json_data={"id": 1})])
    ensure_project(config, "repo", session=session)
    assert session.calls[0]["url"].endswith("/projects/team%2Fsub%2Frepo")


def test_session_uses_private_token_header(config):
    session = make_session(config)
    assert session.headers["PRIVATE-TOKEN"] == "gl-token"


def test_no_delete_anywhere():
    """The DR guarantee: this codebase must never issue an HTTP DELETE."""
    import pathlib

    import git_dr_mirror

    package_dir = pathlib.Path(git_dr_mirror.__file__).parent
    for source_file in package_dir.glob("*.py"):
        source = source_file.read_text().lower()
        assert '"delete"' not in source and "'delete'" not in source, (
            f"{source_file.name} appears to issue a DELETE request"
        )
