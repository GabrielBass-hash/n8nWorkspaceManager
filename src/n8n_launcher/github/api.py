"""GitHub REST API helpers used from the launcher (repos, CI runs and logs)."""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

import requests


class GitHubError(RuntimeError):
    """Raised when the GitHub API returns an unsuccessful response."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


def workflow_name(workflow_file: str) -> str:
    """Reduce a ``.github/workflows/x.yml`` path to its bare file name.

    GitHub's workflow endpoints match on the file name (or the numeric id);
    a full path is rejected, so callers may pass either form safely.
    """
    return workflow_file.rsplit("/", 1)[-1]


def repo_url_path(repo_path: str) -> str:
    """Build the ``owner/repo`` URL path GitHub expects.

    The slash between owner and repository must stay literal: encoding it as
    ``%2F`` makes every Actions endpoint answer 404. Only the two segments are
    percent-encoded, so unusual characters survive without breaking the path.
    """
    owner, _, repo = repo_path.partition("/")
    return f"{quote(owner, safe='')}/{quote(repo, safe='')}"


class GitHubClient:
    """Token-authenticated client for the GitHub REST API.

    The client covers three concerns: repository creation (:meth:`create_repo`),
    read-only CI inspection (:meth:`list_workflow_runs`, :meth:`list_run_jobs`,
    :meth:`fetch_job_logs`) and triggering a run (:meth:`dispatch_workflow`).
    The token is never persisted by this class nor by its callers — it lives
    only in memory for the lifetime of the session. Errors are wrapped in
    :class:`GitHubError` with the HTTP status preserved so the GUI can explain
    401/403/422.
    """

    BASE_URL = "https://api.github.com"

    def __init__(
        self,
        token: str,
        *,
        timeout: float = 15.0,
        session: requests.Session | None = None,
    ) -> None:
        self.token = token
        self.timeout = timeout
        self.session = session or requests.Session()

    def github_owner(self) -> str:
        """Return the authenticated account's login (validates the token)."""
        payload = self._request("GET", "/user")
        login = payload.get("login")
        if not isinstance(login, str) or not login:
            raise GitHubError("GitHub n'a pas renvoyé l'identifiant du compte")
        return login

    def create_repo(
        self,
        name: str,
        *,
        private: bool = True,
        description: str | None = None,
        owner: str | None = None,
    ) -> str:
        """Create a repository and return its clean HTTPS clone URL.

        The repository is created under the authenticated account, or under an
        organization when ``owner`` is provided (``POST /orgs/{owner}/repos``).
        The returned ``https://github.com/<owner>/<name>.git`` URL holds no
        token and is meant to be stored as the git remote.
        """
        account = owner or self.github_owner()
        payload: dict[str, Any] = {"name": name, "private": private}
        if description:
            payload["description"] = description
        if owner:
            self._request("POST", f"/orgs/{owner}/repos", json=payload)
        else:
            self._request("POST", "/user/repos", json=payload)
        return f"https://github.com/{account}/{name}.git"

    def list_workflow_runs(
        self,
        repo_path: str,
        *,
        workflow_file: str = "n8n-ci.yml",
        per_page: int = 20,
    ) -> list[dict[str, Any]]:
        """Return the most recent runs of a workflow file (newest first)."""
        workflow_id = quote(workflow_name(workflow_file), safe="")
        payload = self._request(
            "GET",
            f"/repos/{repo_url_path(repo_path)}"
            f"/actions/workflows/{workflow_id}/runs",
            params={"per_page": str(per_page)},
        )
        runs = payload.get("workflow_runs")
        if not isinstance(runs, list):
            raise GitHubError("GitHub n'a pas renvoyé la liste des runs")
        return runs

    def list_run_jobs(
        self, repo_path: str, run_id: int | str
    ) -> list[dict[str, Any]]:
        """Return the jobs of a workflow run (``per_page`` capped at 100)."""
        payload = self._request(
            "GET",
            f"/repos/{repo_url_path(repo_path)}/actions/runs/{run_id}/jobs",
            params={"per_page": "100"},
        )
        jobs = payload.get("jobs")
        if not isinstance(jobs, list):
            raise GitHubError("GitHub n'a pas renvoyé la liste des jobs")
        return jobs

    def fetch_job_logs(self, repo_path: str, job_id: int | str) -> str:
        """Download the raw (text/plain) log of a completed job."""
        return self._request_text(
            "GET",
            f"/repos/{repo_url_path(repo_path)}/actions/jobs/{job_id}/logs",
        )

    def dispatch_workflow(
        self,
        repo_path: str,
        workflow_file: str,
        *,
        ref: str,
        inputs: dict[str, Any] | None = None,
    ) -> None:
        """Trigger a ``workflow_dispatch`` run of *workflow_file* on *ref*.

        ``workflow_file`` may be a full repository path (``.github/workflows/
        n8n-ci.yml``); only the bare file name is sent because the dispatch
        endpoint matches on the file name or the numeric workflow id — a path
        with slashes is rejected. The workflow must declare a
        ``workflow_dispatch`` trigger (the launcher's generated harness does);
        otherwise GitHub replies 422.
        """
        workflow_id = quote(workflow_name(workflow_file), safe="")
        payload: dict[str, Any] = {"ref": ref}
        if inputs:
            payload["inputs"] = inputs
        self._request_action(
            "POST",
            f"/repos/{repo_url_path(repo_path)}"
            f"/actions/workflows/{workflow_id}/dispatches",
            json=payload,
        )

    def _headers(self) -> dict[str, str]:
        """Headers sent with every GitHub request (auth + API version)."""
        return {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "n8n-launcher",
            "Authorization": f"Bearer {self.token}",
        }

    def _request_text(self, method: str, path: str, **kwargs: Any) -> str:
        url = self.BASE_URL + path
        try:
            response = self.session.request(
                method, url, headers=self._headers(), timeout=self.timeout, **kwargs
            )
        except requests.RequestException as exc:
            raise GitHubError(f"requête GitHub impossible : {exc}") from exc
        if response.status_code not in (200, 201):
            raise GitHubError(
                _error_message(response), status_code=response.status_code
            )
        return response.text

    def _request_action(self, method: str, path: str, **kwargs: Any) -> None:
        """Send a request whose success carries no body (e.g. a dispatch)."""
        url = self.BASE_URL + path
        try:
            response = self.session.request(
                method, url, headers=self._headers(), timeout=self.timeout, **kwargs
            )
        except requests.RequestException as exc:
            raise GitHubError(f"requête GitHub impossible : {exc}") from exc
        if response.status_code not in (200, 201, 202, 204):
            raise GitHubError(
                _error_message(response), status_code=response.status_code
            )

    def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        url = self.BASE_URL + path
        try:
            response = self.session.request(
                method, url, headers=self._headers(), timeout=self.timeout, **kwargs
            )
        except requests.RequestException as exc:
            raise GitHubError(f"requête GitHub impossible : {exc}") from exc
        if response.status_code not in (200, 201):
            raise GitHubError(
                _error_message(response), status_code=response.status_code
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise GitHubError("GitHub a renvoyé un JSON illisible") from exc
        if not isinstance(payload, dict):
            raise GitHubError("GitHub a renvoyé une réponse inattendue")
        return payload


def _error_message(response: requests.Response) -> str:
    """Build a human-readable message from a non-2xx GitHub response."""
    base = f"GitHub a refusé la requête (HTTP {response.status_code})"
    try:
        payload = response.json()
    except ValueError:
        return base
    message = payload.get("message") if isinstance(payload, dict) else None
    if isinstance(message, str) and message:
        return f"{base} : {message}"
    return base
