"""Shared test fixtures."""

from pathlib import Path

import pytest

from git_dr_mirror.config import Config


@pytest.fixture
def config(tmp_path) -> Config:
    """A valid Config pointing at a temp mirror dir, retries disabled-ish."""
    return Config(
        github_token="gh-token",
        gitlab_token="gl-token",
        gitlab_group="backup-group",
        mirror_dir=tmp_path / "mirrors",
        http_retries=2,
        http_timeout=5,
        git_timeout=60,
    )


class FakeResponse:
    """Minimal stand-in for requests.Response."""

    def __init__(self, status_code=200, json_data=None, links=None, headers=None, text=""):
        self.status_code = status_code
        self._json = json_data if json_data is not None else {}
        self.links = links or {}
        self.headers = headers or {}
        self.text = text

    def json(self):
        return self._json


class FakeSession:
    """Records requests and replays a scripted list of responses.

    Each entry in ``responses`` is either a FakeResponse or an Exception
    instance (raised instead of returning).
    """

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls: list[dict] = []

    def request(self, method, url, **kwargs):
        self.calls.append({"method": method, "url": url, **kwargs})
        result = self.responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result
