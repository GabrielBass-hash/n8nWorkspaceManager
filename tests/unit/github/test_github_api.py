"""Unit tests for the GitHub REST client (github/api.py)."""

from __future__ import annotations

import pytest
import requests

from n8n_launcher.github.api import (
    GitHubClient,
    GitHubError,
    repo_url_path,
    workflow_name,
)


class FakeResponse:
    """Minimal requests-like response for canned API payloads."""

    def __init__(self, status_code: int, payload: object, text: str = ""):
        self.status_code = status_code
        self._payload = payload
        self.text = text

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


# --- CI inspection (runs / jobs / logs) --------------------------------------


def test_list_workflow_runs_url_and_params() -> None:
    url = (
        "https://api.github.com/repos/octo/repo/actions/workflows/"
        "n8n-ci.yml/runs"
    )
    session = FakeSession(
        {("GET", url): FakeResponse(200, {"workflow_runs": [{"id": 11}]})}
    )
    client = GitHubClient("ghp_token", session=session)

    runs = client.list_workflow_runs("octo/repo")

    assert runs == [{"id": 11}]
    _method, _url, kwargs = session.calls[0]
    assert kwargs["params"] == {"per_page": "20"}
    assert kwargs["headers"]["Authorization"] == "Bearer ghp_token"


def test_list_workflow_runs_rejects_non_list_payload() -> None:
    url = (
        "https://api.github.com/repos/octo/repo/actions/workflows/"
        "n8n-ci.yml/runs"
    )
    session = FakeSession({("GET", url): FakeResponse(200, {"workflow_runs": {}})})
    client = GitHubClient("ghp_token", session=session)

    with pytest.raises(GitHubError):
        client.list_workflow_runs("octo/repo")


def test_list_run_jobs_url_and_params() -> None:
    url = "https://api.github.com/repos/octo/repo/actions/runs/11/jobs"
    session = FakeSession({("GET", url): FakeResponse(200, {"jobs": [{"id": 21}]})})
    client = GitHubClient("ghp_token", session=session)

    jobs = client.list_run_jobs("octo/repo", 11)

    assert jobs == [{"id": 21}]
    _method, _url, kwargs = session.calls[0]
    assert kwargs["params"] == {"per_page": "100"}


def test_list_run_jobs_rejects_non_list_payload() -> None:
    url = "https://api.github.com/repos/octo/repo/actions/runs/11/jobs"
    session = FakeSession({("GET", url): FakeResponse(200, {"jobs": None})})
    client = GitHubClient("ghp_token", session=session)

    with pytest.raises(GitHubError):
        client.list_run_jobs("octo/repo", 11)


def test_fetch_job_logs_returns_response_text() -> None:
    url = "https://api.github.com/repos/octo/repo/actions/jobs/21/logs"
    session = FakeSession(
        {("GET", url): FakeResponse(200, {}, text="[runner] a.json : success")}
    )
    client = GitHubClient("ghp_token", session=session)

    assert client.fetch_job_logs("octo/repo", 21) == "[runner] a.json : success"


def test_fetch_job_logs_raises_with_status_on_error() -> None:
    url = "https://api.github.com/repos/octo/repo/actions/jobs/21/logs"
    session = FakeSession(
        {("GET", url): FakeResponse(404, {"message": "Not Found"})}
    )
    client = GitHubClient("ghp_token", session=session)

    with pytest.raises(GitHubError) as excinfo:
        client.fetch_job_logs("octo/repo", 21)

    assert excinfo.value.status_code == 404
    assert "Not Found" in str(excinfo.value)


def test_error_message_without_json_body_is_still_readable() -> None:
    url = "https://api.github.com/repos/octo/repo/actions/jobs/21/logs"
    session = FakeSession(
        {("GET", url): FakeResponse(500, ValueError("not json"))}
    )
    client = GitHubClient("ghp_token", session=session)

    with pytest.raises(GitHubError) as excinfo:
        client.fetch_job_logs("octo/repo", 21)

    assert str(excinfo.value) == "GitHub a refusé la requête (HTTP 500)"


def test_fetch_job_logs_wraps_network_errors() -> None:
    session = FakeSession({})
    client = GitHubClient("ghp_token", session=session)

    def boom(*_args, **_kwargs):
        raise requests.Timeout("slow")

    session.request = boom

    with pytest.raises(GitHubError) as excinfo:
        client.fetch_job_logs("octo/repo", 21)

    assert "requête GitHub impossible" in str(excinfo.value)


def test_workflow_name_strips_github_actions_path() -> None:
    assert workflow_name(".github/workflows/n8n-ci.yml") == "n8n-ci.yml"
    assert workflow_name("n8n-ci.yml") == "n8n-ci.yml"


def test_repo_url_path_keeps_literal_slash() -> None:
    assert repo_url_path("octo/repo") == "octo/repo"
    assert repo_url_path("octo/re po") == "octo/re%20po"


def test_dispatch_workflow_posts_bare_file_name_for_full_path() -> None:
    url = (
        "https://api.github.com/repos/octo/repo"
        "/actions/workflows/n8n-ci.yml/dispatches"
    )
    session = FakeSession({("POST", url): FakeResponse(200, {})})
    client = GitHubClient("ghp_token", session=session)

    client.dispatch_workflow(
        "octo/repo", ".github/workflows/n8n-ci.yml", ref="release"
    )

    method, _url, kwargs = session.calls[0]
    assert method == "POST"
    assert kwargs["json"] == {"ref": "release"}


def test_list_workflow_runs_accepts_full_path() -> None:
    url = (
        "https://api.github.com/repos/octo/repo"
        "/actions/workflows/n8n-ci.yml/runs"
    )
    session = FakeSession({("GET", url): FakeResponse(200, {"workflow_runs": []})})
    client = GitHubClient("ghp_token", session=session)

    assert client.list_workflow_runs("octo/repo", workflow_file=".github/workflows/n8n-ci.yml") == []


def test_dispatch_workflow_forwards_inputs() -> None:
    url = (
        "https://api.github.com/repos/octo/repo"
        "/actions/workflows/wf.yml/dispatches"
    )
    session = FakeSession({("POST", url): FakeResponse(200, {})})
    client = GitHubClient("ghp_token", session=session)

    client.dispatch_workflow("octo/repo", "wf.yml", ref="main", inputs={"a": 1})

    assert session.calls[0][2]["json"] == {"ref": "main", "inputs": {"a": 1}}


def test_dispatch_workflow_raises_with_status_on_422() -> None:
    url = (
        "https://api.github.com/repos/octo/repo"
        "/actions/workflows/wf.yml/dispatches"
    )
    session = FakeSession(
        {("POST", url): FakeResponse(422, {"message": "Workflow does not exist"})}
    )
    client = GitHubClient("ghp_token", session=session)

    with pytest.raises(GitHubError) as excinfo:
        client.dispatch_workflow("octo/repo", "wf.yml", ref="main")

    assert excinfo.value.status_code == 422
    assert "Workflow does not exist" in str(excinfo.value)


def test_dispatch_workflow_wraps_network_errors() -> None:
    session = FakeSession({})
    client = GitHubClient("ghp_token", session=session)

    def boom(*_args, **_kwargs):
        raise requests.Timeout("slow")

    session.request = boom

    with pytest.raises(GitHubError) as excinfo:
        client.dispatch_workflow("octo/repo", "wf.yml", ref="main")

    assert "requête GitHub impossible" in str(excinfo.value)