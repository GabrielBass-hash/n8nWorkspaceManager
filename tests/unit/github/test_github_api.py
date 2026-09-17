"""Unit tests for the GitHub REST client (github/api.py)."""

from __future__ import annotations

import pytest
import requests

from n8n_launcher.github.api import GitHubClient, GitHubError


class FakeResponse:
    """Minimal requests-like response for canned API payloads."""

    def __init__(self, status_code: int, payload: object):
        self.status_code = status_code
        self._payload = payload

    def json(self) -> object:
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class FakeSession:
    """Records requests and returns canned responses keyed by (method, URL)."""

    def __init__(self, responses: dict[tuple[str, str], FakeResponse]):
        self.responses = responses
        self.calls: list[tuple[str, str, object]] = []

    def request(self, method: str, url: str, **kwargs: object):
        self.calls.append((method, url, kwargs))
        return self.responses[(method, url)]


def test_github_owner_returns_login() -> None:
    session = FakeSession({("GET", "https://api.github.com/user"): FakeResponse(200, {"login": "octo"})})
    client = GitHubClient("ghp_token", session=session)

    assert client.github_owner() == "octo"
    assert "Authorization" in session.calls[0][2]["headers"]


def test_create_repo_private_default_posts_user_repos() -> None:
    session = FakeSession(
        {
            ("GET", "https://api.github.com/user"): FakeResponse(200, {"login": "octo"}),
            ("POST", "https://api.github.com/user/repos"): FakeResponse(201, {"html_url": "x"}),
        }
    )
    client = GitHubClient("ghp_token", session=session)

    url = client.create_repo("flows")

    assert url == "https://github.com/octo/flows.git"
    _method, _url, kwargs = session.calls[1]
    assert kwargs["json"] == {"name": "flows", "private": True}


def test_create_repo_public_and_description() -> None:
    session = FakeSession(
        {
            ("GET", "https://api.github.com/user"): FakeResponse(200, {"login": "octo"}),
            ("POST", "https://api.github.com/user/repos"): FakeResponse(201, {}),
        }
    )
    client = GitHubClient("ghp_token", session=session)

    client.create_repo("flows", private=False, description="Workspace n8n")

    _method, _url, kwargs = session.calls[1]
    assert kwargs["json"] == {
        "name": "flows",
        "private": False,
        "description": "Workspace n8n",
    }


def test_create_repo_under_organization() -> None:
    session = FakeSession({("POST", "https://api.github.com/orgs/acme/repos"): FakeResponse(201, {})})
    client = GitHubClient("ghp_token", session=session)

    url = client.create_repo("flows", owner="acme")

    assert url == "https://github.com/acme/flows.git"
    assert session.calls[0][0:2] == ("POST", "https://api.github.com/orgs/acme/repos")


def test_create_repo_raises_with_status_on_401() -> None:
    session = FakeSession(
        {
            ("GET", "https://api.github.com/user"): FakeResponse(200, {"login": "octo"}),
            ("POST", "https://api.github.com/user/repos"): FakeResponse(
                401, {"message": "Bad credentials"}
            ),
        }
    )
    client = GitHubClient("ghp_token", session=session)

    with pytest.raises(GitHubError) as excinfo:
        client.create_repo("flows")

    assert excinfo.value.status_code == 401
    assert "Bad credentials" in str(excinfo.value)


def test_create_repo_raises_with_status_on_422() -> None:
    session = FakeSession(
        {
            ("POST", "https://api.github.com/orgs/acme/repos"): FakeResponse(
                422, {"message": "Repository creation failed."}
            ),
        }
    )
    client = GitHubClient("ghp_token", session=session)

    with pytest.raises(GitHubError) as excinfo:
        client.create_repo("flows", owner="acme")

    assert excinfo.value.status_code == 422
    assert "Repository creation failed" in str(excinfo.value)


def test_request_wraps_network_errors() -> None:
    session = FakeSession({("GET", "https://api.github.com/user"): FakeResponse(0, None)})
    client = GitHubClient("ghp_token", session=session)

    def boom(*_args, **_kwargs):
        raise requests.ConnectionError("down")

    session.request = boom

    with pytest.raises(GitHubError):
        client.github_owner()