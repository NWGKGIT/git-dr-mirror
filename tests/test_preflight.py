"""Tests for read-only preflight credential checks."""

from __future__ import annotations

import pytest
import requests

from git_dr_mirror import preflight
from git_dr_mirror.http_client import ApiError
from conftest import FakeResponse, FakeSession


# ---------------------------------------------------------------------------
# check_github
# ---------------------------------------------------------------------------


def test_github_ok(config, monkeypatch):
    session = FakeSession([FakeResponse(200, json_data=[], links={})])
    monkeypatch.setattr("git_dr_mirror.github.make_session", lambda c: session)
    result = preflight.check_github(config)
    assert result.ok
    assert result.hint is None


def test_github_401(config, monkeypatch):
    session = FakeSession([FakeResponse(401, text="Unauthorized")])
    monkeypatch.setattr("git_dr_mirror.github.make_session", lambda c: session)
    result = preflight.check_github(config)
    assert not result.ok
    assert "invalid or expired" in result.hint
    assert result.hint is not None


def test_github_403(config, monkeypatch):
    session = FakeSession([FakeResponse(403, text="Forbidden")])
    monkeypatch.setattr("git_dr_mirror.github.make_session", lambda c: session)
    result = preflight.check_github(config)
    assert not result.ok
    assert "scope" in result.hint.lower()


def test_github_network_error(config, monkeypatch):
    session = FakeSession([requests.ConnectionError("timeout")])
    monkeypatch.setattr("git_dr_mirror.github.make_session", lambda c: session)
    result = preflight.check_github(config)
    assert not result.ok
    assert result.hint is not None


def test_github_unexpected_exception(config, monkeypatch):
    def boom(c):
        raise RuntimeError("unexpected")
    monkeypatch.setattr("git_dr_mirror.github.make_session", boom)
    result = preflight.check_github(config)
    assert not result.ok
    assert "unexpected" in result.hint


# ---------------------------------------------------------------------------
# check_gitlab
# ---------------------------------------------------------------------------


def _gl_session(responses):
    """Patch gitlab.make_session to return a FakeSession."""
    return FakeSession(responses)


def test_gitlab_ok(config, monkeypatch):
    session = FakeSession([
        FakeResponse(200, json_data={"id": 1, "username": "user"}),   # /user
        FakeResponse(200, json_data={"id": 42, "name": "backup"}),    # /groups/...
    ])
    monkeypatch.setattr("git_dr_mirror.gitlab.make_session", lambda c: session)
    result = preflight.check_gitlab(config)
    assert result.ok
    assert "42" in result.detail


def test_gitlab_token_invalid(config, monkeypatch):
    session = FakeSession([FakeResponse(401, text="Unauthorized")])
    monkeypatch.setattr("git_dr_mirror.gitlab.make_session", lambda c: session)
    result = preflight.check_gitlab(config)
    assert not result.ok
    assert "invalid or expired" in result.hint


def test_gitlab_group_not_found(config, monkeypatch):
    session = FakeSession([
        FakeResponse(200, json_data={"id": 1}),   # /user succeeds
        FakeResponse(404, text="Not Found"),       # /groups/... 404
    ])
    monkeypatch.setattr("git_dr_mirror.gitlab.make_session", lambda c: session)
    result = preflight.check_gitlab(config)
    assert not result.ok
    assert "not found" in result.detail.lower() or "does not exist" in result.hint.lower()


def test_gitlab_group_forbidden(config, monkeypatch):
    session = FakeSession([
        FakeResponse(200, json_data={"id": 1}),   # /user succeeds
        FakeResponse(403, text="Forbidden"),       # /groups/... 403
    ])
    monkeypatch.setattr("git_dr_mirror.gitlab.make_session", lambda c: session)
    result = preflight.check_gitlab(config)
    assert not result.ok
    assert "forbidden" in result.detail.lower()


def test_gitlab_network_error(config, monkeypatch):
    session = FakeSession([requests.ConnectionError("timeout")])
    monkeypatch.setattr("git_dr_mirror.gitlab.make_session", lambda c: session)
    result = preflight.check_gitlab(config)
    assert not result.ok
    assert result.hint is not None


# ---------------------------------------------------------------------------
# run_all
# ---------------------------------------------------------------------------


def test_run_all_returns_two_results(config, monkeypatch):
    gh_session = FakeSession([FakeResponse(200, json_data=[], links={})])
    gl_session = FakeSession([
        FakeResponse(200, json_data={"id": 1}),
        FakeResponse(200, json_data={"id": 42}),
    ])
    monkeypatch.setattr("git_dr_mirror.github.make_session", lambda c: gh_session)
    monkeypatch.setattr("git_dr_mirror.gitlab.make_session", lambda c: gl_session)
    results = preflight.run_all(config)
    assert len(results) == 2
    assert all(r.ok for r in results)
