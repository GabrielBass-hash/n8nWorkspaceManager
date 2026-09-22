"""Small HTTP client for the n8n public API."""

from __future__ import annotations

from typing import Any

import requests


class N8nApiError(RuntimeError):
    """Raised when n8n returns an unsuccessful API response."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class N8nApiClient:
    """Authenticated HTTP client for the n8n public REST API.

    Every request carries the ``X-N8N-API-KEY`` header; errors are wrapped in
    :class:`N8nApiError` with the HTTP status preserved for 401/403 handling.
    """

    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        timeout: float = 10.0,
        session: requests.Session | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self.session = session or requests.Session()

    def list_workflows(self) -> list[dict[str, Any]]:
        """Return all workflows visible to the API key."""
        payload = self._request("GET", "/workflows")
        return list(payload.get("data", []))

    def get_workflow(self, workflow_id: str) -> dict[str, Any]:
        """Return the full export of a single workflow."""
        return self._request("GET", f"/workflows/{workflow_id}")

    def create_workflow(self, workflow: dict[str, Any]) -> dict[str, Any]:
        """Create a workflow from an export payload."""
        return self._request("POST", "/workflows", json=workflow)

    def update_workflow(self, workflow_id: str, workflow: dict[str, Any]) -> dict[str, Any]:
        """Replace an existing workflow by its id."""
        return self._request("PUT", f"/workflows/{workflow_id}", json=workflow)

    def delete_workflow(self, workflow_id: str) -> None:
        """Delete a workflow by its id."""
        self._request("DELETE", f"/workflows/{workflow_id}")

    def activate_workflow(self, workflow_id: str, active: bool = True) -> dict[str, Any]:
        """Activate or deactivate a workflow's triggers."""
        action = "activate" if active else "deactivate"
        return self._request("POST", f"/workflows/{workflow_id}/{action}")

    def list_credentials(self) -> list[dict[str, Any]]:
        """Return all credentials; a missing list is treated as empty."""
        payload = self._request("GET", "/credentials", ignore_not_found=True)
        return list(payload.get("data", []))

    def get_credential(self, credential_id: str) -> dict[str, Any]:
        """Return a single credential, including its secret ``data``.

        The public list endpoint deliberately omits the decrypted ``data``; the
        CI credentials flow needs it, so it is fetched per credential here.
        """
        return self._request("GET", f"/credentials/{credential_id}")

    def create_credential(self, credential: dict[str, Any]) -> dict[str, Any]:
        """Create a new credential."""
        return self._request("POST", "/credentials", json=credential)

    def ensure_postgres_credential(
        self,
        name: str,
        *,
        host: str,
        port: int,
        database: str,
        user: str,
        password: str,
    ) -> dict[str, Any]:
        """Return an existing credential by name or create a Postgres one."""
        for credential in self.list_credentials():
            if credential.get("name") == name:
                return credential
        return self.create_credential(
            {
                "name": name,
                "type": "postgres",
                "data": {
                    "host": host,
                    "port": port,
                    "database": database,
                    "user": user,
                    "password": password,
                    "ssl": "disable",
                },
            }
        )

    def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        ignore_not_found: bool = False,
    ) -> dict[str, Any]:
        """Perform an authenticated request and decode the JSON response.
        The n8n internal endpoints return objects, never bare arrays (the
        public list endpoints wrap arrays in a ``data`` key); ``dict`` is
        therefore the accurate return type.

        ``ignore_not_found=True`` turns ``404`` into an empty ``{}`` instead of
        raising, which several callers rely on.
        """
        headers = {"X-N8N-API-KEY": self.api_key, "Accept": "application/json"}
        try:
            response = self.session.request(
                method,
                f"{self.base_url}{path}",
                headers=headers,
                timeout=self.timeout,
                json=json,
                params=params,
            )
        except requests.RequestException as exc:
            raise N8nApiError(f"n8n request failed: {exc}") from exc
        if not response.ok:
            if ignore_not_found and response.status_code == 404:
                return {}
            detail = response.text[:500]
            raise N8nApiError(
                f"n8n API returned {response.status_code}: {detail}",
                status_code=response.status_code,
            )
        if response.status_code == 204:
            return {}
        try:
            return response.json()
        except ValueError as exc:
            raise N8nApiError("n8n API returned invalid JSON") from exc
