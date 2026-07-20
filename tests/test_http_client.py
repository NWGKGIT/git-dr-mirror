"""Tests for the retrying HTTP helper."""

import pytest
import requests

from git_dr_mirror.http_client import ApiError, request

from conftest import FakeResponse, FakeSession


def no_sleep(_seconds):
    pass


def do_request(session, retries=3):
    return request(
        session,
        "GET",
        "https://api.example.com/x",
        timeout=5,
        retries=retries,
        sleep=no_sleep,
    )


def test_success_first_try():
    session = FakeSession([FakeResponse(200, json_data={"ok": True})])
    response = do_request(session)
    assert response.json() == {"ok": True}
    assert len(session.calls) == 1


def test_retries_connection_errors_then_succeeds():
    session = FakeSession(
        [
            requests.ConnectionError("reset"),
            requests.Timeout("timed out"),
            FakeResponse(200),
        ]
    )
    response = do_request(session)
    assert response.status_code == 200
    assert len(session.calls) == 3


def test_retries_5xx_and_429():
    session = FakeSession(
        [
            FakeResponse(503),
            FakeResponse(429, headers={"Retry-After": "1"}),
            FakeResponse(200),
        ]
    )
    response = do_request(session)
    assert response.status_code == 200
    assert len(session.calls) == 3


def test_exhausted_retries_raises():
    session = FakeSession([FakeResponse(500)] * 3)
    with pytest.raises(ApiError, match="after 3 attempts"):
        do_request(session, retries=2)
    assert len(session.calls) == 3


def test_non_retryable_error_raises_immediately():
    session = FakeSession([FakeResponse(403, text="forbidden")])
    with pytest.raises(ApiError) as exc:
        do_request(session)
    assert exc.value.status_code == 403
    assert len(session.calls) == 1


def test_404_carries_status_code():
    session = FakeSession([FakeResponse(404)])
    with pytest.raises(ApiError) as exc:
        do_request(session)
    assert exc.value.status_code == 404
