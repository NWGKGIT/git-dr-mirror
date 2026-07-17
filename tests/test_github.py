"""Tests for GitHub repository discovery."""

from dataclasses import replace

from git_dr_mirror.github import Repo, list_repos, make_session

from conftest import FakeResponse, FakeSession


def gh_repo(name, fork=False):
    return {
        "name": name,
        "clone_url": f"https://github.com/me/{name}.git",
        "fork": fork,
        "description": None,
    }


def test_single_page_discovery(config):
    session = FakeSession([
        FakeResponse(200, json_data=[gh_repo("alpha"), gh_repo("beta")]),
    ])
    repos = list_repos(config, session=session)
    assert [r.name for r in repos] == ["alpha", "beta"]
    assert repos[0].clone_url == "https://github.com/me/alpha.git"

    call = session.calls[0]
    assert call["url"] == "https://api.github.com/user/repos"
    assert call["params"]["affiliation"] == "owner"
    assert call["params"]["per_page"] == 100


def test_pagination_follows_link_header(config):
    session = FakeSession([
        FakeResponse(
            200,
            json_data=[gh_repo("page1-repo")],
            links={"next": {"url": "https://api.github.com/user/repos?page=2"}},
        ),
        FakeResponse(200, json_data=[gh_repo("page2-repo")]),
    ])
    repos = list_repos(config, session=session)
    assert [r.name for r in repos] == ["page1-repo", "page2-repo"]
    # Second call follows the next-link verbatim, without re-sending params.
    assert session.calls[1]["url"] == "https://api.github.com/user/repos?page=2"
    assert session.calls[1]["params"] is None


def test_forks_skipped_by_default(config):
    session = FakeSession([
        FakeResponse(200, json_data=[gh_repo("mine"), gh_repo("a-fork", fork=True)]),
    ])
    repos = list_repos(config, session=session)
    assert [r.name for r in repos] == ["mine"]


def test_forks_included_when_configured(config):
    config = replace(config, include_forks=True)
    session = FakeSession([
        FakeResponse(200, json_data=[gh_repo("mine"), gh_repo("a-fork", fork=True)]),
    ])
    repos = list_repos(config, session=session)
    assert [r.name for r in repos] == ["mine", "a-fork"]


def test_exclude_glob_patterns(config):
    config = replace(config, exclude_repos=["scratch-*", "tmp"])
    session = FakeSession([
        FakeResponse(200, json_data=[
            gh_repo("keeper"), gh_repo("scratch-notes"), gh_repo("tmp"),
        ]),
    ])
    repos = list_repos(config, session=session)
    assert [r.name for r in repos] == ["keeper"]


def test_custom_affiliation_passed_through(config):
    config = replace(config, github_affiliation="owner,organization_member")
    session = FakeSession([FakeResponse(200, json_data=[])])
    list_repos(config, session=session)
    assert session.calls[0]["params"]["affiliation"] == "owner,organization_member"


def test_session_carries_auth_header(config):
    session = make_session(config)
    assert session.headers["Authorization"] == "Bearer gh-token"
    assert "github" in session.headers["Accept"]


def test_repo_is_frozen():
    repo = Repo(name="x", clone_url="u", fork=False)
    try:
        repo.name = "y"  # type: ignore[misc]
        raised = False
    except Exception:
        raised = True
    assert raised
