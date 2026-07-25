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


def test_jitter_produces_variable_delays():
    """Backoff delays should vary across retries (full-jitter, not constant)."""
    from git_dr_mirror.http_client import BACKOFF_MAX_CAP

    delays: list[float] = []

    def record_sleep(seconds: float) -> None:
        delays.append(seconds)

    session = FakeSession([FakeResponse(500), FakeResponse(500), FakeResponse(200)])
    request(
        session, "GET", "https://api.example.com/x",
        timeout=5, retries=3, sleep=record_sleep,
    )
    assert len(delays) == 2, "expected one sleep per retry"
    # Full-jitter: delay must be in [0, cap] — cap for attempt 1 is BACKOFF_BASE
    assert all(0.0 <= d <= BACKOFF_MAX_CAP for d in delays)


def test_retry_after_does_not_double_sleep():
    """When Retry-After is honored, backoff sleep must be skipped on the next attempt."""
    sleeps: list[float] = []

    def record_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    # First response: 429 with Retry-After: 5
    # Second response: success
    session = FakeSession([
        FakeResponse(429, headers={"Retry-After": "5"}),
        FakeResponse(200),
    ])
    request(
        session, "GET", "https://api.example.com/x",
        timeout=5, retries=2, sleep=record_sleep,
    )
    # Exactly one sleep call: the Retry-After value (5s). No extra jitter sleep.
    assert sleeps == [5], f"expected [5], got {sleeps}"
