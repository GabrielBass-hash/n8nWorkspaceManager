"""First-run n8n owner and API-key bootstrap."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import bcrypt
import requests

REQUIRED_WORKFLOW_SCOPES = [
    "workflow:list",
    "workflow:read",
    "workflow:create",
    "workflow:update",
    "workflow:delete",
    "workflow:activate",
    "credential:list",
    "credential:read",
    "credential:create",
    "credential:update",
    "credential:delete",
]


class OwnerSetupError(RuntimeError):
    """Raised when the n8n owner setup cannot complete."""


def hash_owner_password(password: str) -> str:
    """Hash an owner password with bcrypt for N8N_INSTANCE_OWNER_PASSWORD."""
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("ascii")


@dataclass(frozen=True)
class ApiCredentials:
    api_key: str


class OwnerSetup:
    def __init__(self, *, timeout: float = 10.0, session: requests.Session | None = None) -> None:
        self.timeout = timeout
        self.session = session or requests.Session()

    def bootstrap(
        self,
        base_url: str,
        email: str,
        password: str,
        *,
        first_name: str = "n8n",
        last_name: str = "Launcher",
        ready_timeout: float = 180.0,
    ) -> ApiCredentials:
        root = base_url.rstrip("/")
        self._ensure_owner(root, email, password, first_name, last_name, ready_timeout)
        login = self._request(
            "POST",
            f"{root}/rest/login",
            {"emailOrLdapLoginId": email, "password": password},
        )
        token = login.get("data", {}).get("token")
        if not token and "n8n-auth" not in self.session.cookies:
            raise OwnerSetupError("n8n login did not return a session token")
        self._remove_launcher_keys(root)
        response = self._request(
            "POST",
            f"{root}/rest/api-keys",
            {"label": "n8n-launcher", "scopes": REQUIRED_WORKFLOW_SCOPES, "expiresAt": 0},
        )
        api_key = response.get("data", {}).get("rawApiKey") or response.get("rawApiKey")
        if not api_key:
            raise OwnerSetupError("n8n did not return an API key")
        return ApiCredentials(api_key)

    def _remove_launcher_keys(self, root: str) -> None:
        try:
            response = self._request("GET", f"{root}/rest/api-keys", {})
        except OwnerSetupError:
            return
        for item in response.get("data", {}).get("items", []):
            if item.get("label") == "n8n-launcher":
                self._request("DELETE", f"{root}/rest/api-keys/{item['id']}", {})

    def _ensure_owner(
        self,
        root: str,
        email: str,
        password: str,
        first_name: str,
        last_name: str,
        ready_timeout: float,
    ) -> None:
        deadline = time.monotonic() + ready_timeout
        payload = {
            "firstName": first_name,
            "lastName": last_name,
            "email": email,
            "password": password,
        }
        last_error: OwnerSetupError | None = None
        while time.monotonic() < deadline:
            try:
                self._request("POST", f"{root}/rest/owner/setup", payload)
                return
            except OwnerSetupError as exc:
                message = str(exc).lower()
                if "already" in message:
                    return
                if any(marker in message for marker in ("must be", "invalid_type", "expected", "not allowed")):
                    raise
                last_error = exc
            except requests.RequestException as exc:
                last_error = OwnerSetupError(f"n8n owner setup request failed: {exc}")
            time.sleep(2.0)
        raise last_error or OwnerSetupError("n8n did not become ready for owner setup")

    def _request(self, method: str, url: str, payload: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        response = self.session.request(method, url, json=payload, timeout=self.timeout, **kwargs)
        if not response.ok:
            detail = response.text[:300].strip() or response.reason
            raise OwnerSetupError(f"n8n returned HTTP {response.status_code}: {detail}")
        try:
            return response.json()
        except ValueError:
            raise OwnerSetupError(
                f"n8n returned a non-JSON response: {response.text[:120].strip()!r}"
            )