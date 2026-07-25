"""Shared HTTP helper: requests with timeout and exponential-backoff retries.

Used by both the GitHub and GitLab clients so transient network problems
(connection resets, 5xx responses, rate-limit pauses) don't fail a backup run.
"""

from __future__ import annotations

import logging
import random
import time
from typing import Any

import requests

log = logging.getLogger(__name__)

#: HTTP status codes worth retrying: server errors and rate limiting.
RETRYABLE_STATUS = {429, 500, 502, 503, 504}

#: Base delay (seconds) for exponential backoff.
BACKOFF_BASE = 1.0

#: Maximum cap (seconds) for the jittered backoff delay.
BACKOFF_MAX_CAP = 60.0


class ApiError(Exception):
    """A non-retryable API failure, or retries exhausted."""

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


def request(
    session: requests.Session,
    method: str,
    url: str,
    *,
    timeout: int,
    retries: int,
    sleep=time.sleep,
    **kwargs: Any,
) -> requests.Response:
    """Perform an HTTP request, retrying transient failures.

    Retries connection errors, timeouts, and retryable status codes
    (:data:`RETRYABLE_STATUS`) up to ``retries`` times with full-jitter
    exponential backoff (AWS-style: ``uniform(0, min(cap, base * 2^attempt))``).
    Honors the ``Retry-After`` header when present — when a server dictates
    the wait time, the regular backoff is skipped for that retry to avoid
    sleeping twice. Any other error status is raised immediately as
    :class:`ApiError`.

    Args:
        sleep: Injection point for tests; defaults to :func:`time.sleep`.
    """
    last_error: str = "unknown error"
    skip_backoff = False  # set True after honoring a Retry-After header
    for attempt in range(retries + 1):
        if attempt and not skip_backoff:
            cap = min(BACKOFF_MAX_CAP, BACKOFF_BASE * (2 ** (attempt - 1)))
            delay = random.uniform(0, cap)
            log.debug("Retrying %s %s in %.1fs (attempt %d)", method, url, delay, attempt + 1)
            sleep(delay)
        skip_backoff = False

        try:
            response = session.request(method, url, timeout=timeout, **kwargs)
        except requests.RequestException as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            log.warning("%s %s failed: %s", method, url, last_error)
            continue

        if response.status_code in RETRYABLE_STATUS:
            last_error = f"HTTP {response.status_code}"
            retry_after = response.headers.get("Retry-After")
            if retry_after and retry_after.isdigit():
                sleep(int(retry_after))
                skip_backoff = True  # already waited; skip jitter on next attempt

            log.warning("%s %s returned %s", method, url, last_error)
            continue

        if response.status_code >= 400:
            raise ApiError(
                f"{method} {url} failed with HTTP {response.status_code}: {response.text[:500]}",
                status_code=response.status_code,
            )
        return response

    raise ApiError(f"{method} {url} failed after {retries + 1} attempts ({last_error})")
