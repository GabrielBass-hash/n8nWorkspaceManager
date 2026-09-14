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
        payload = self._request("GET", "/workflows")
        if isinstance(payload, list):
            return list(payload)
        return list(payload.get("data", []))

    def get_workflow(self, workflow_id: str) -> dict[str, Any]:
        return self._request("GET", f"/workflows/{workflow_id}")

    def create_workflow(self, workflow: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", "/workflows", json=workflow)

    def update_workflow(self, workflow_id: str, workflow: dict[str, Any]) -> dict[str, Any]:
        return self._request("PUT", f"/workflows/{workflow_id}", json=workflow)

    def delete_workflow(self, workflow_id: str) -> None:
        self._request("DELETE", f"/workflows/{workflow_id}")

    def activate_workflow(self, workflow_id: str, active: bool = True) -> dict[str, Any]:
        action = "activate" if active else "deactivate"
        return self._request("POST", f"/workflows/{workflow_id}/{action}")

    def list_credentials(self) -> list[dict[str, Any]]:
        payload = self._request("GET", "/credentials", ignore_not_found=True)
        if isinstance(payload, list):
            return list(payload)
        return list(payload.get("data", []))

    def create_credential(self, credential: dict[str, Any]) -> dict[str, Any]:
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

    def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any] | list[Any]:
        headers = {"X-N8N-API-KEY": self.api_key, "Accept": "application/json"}
        ignore_not_found = kwargs.pop("ignore_not_found", False)
        try:
            response = self.session.request(
                method,
                f"{self.base_url}{path}",
                headers=headers,
                timeout=self.timeout,
                **kwargs,
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
