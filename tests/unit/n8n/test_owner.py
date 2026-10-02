from unittest.mock import MagicMock

import pytest

from n8n_launcher.n8n.owner import (
    REQUIRED_WORKFLOW_SCOPES,
    ApiCredentials,
    OwnerSetup,
    OwnerSetupError,
    hash_owner_password,
    wait_for_n8n,
)


def response(payload, status_code=200):
    item = MagicMock()
    item.ok = status_code < 400
    item.status_code = status_code
    item.text = ""
    item.json.return_value = payload
    return item


def test_owner_password_hash_is_bcrypt() -> None:
    hashed = hash_owner_password("s3cret with $ dollar signs")
    assert hashed.startswith("$2")


def test_owner_setup_returns_api_key() -> None:
    session = MagicMock()
    session.request.side_effect = [
        response({"data": {"id": "owner"}}),
        response({"data": {"token": "session"}}),
        response({"data": {"items": []}}),
        response({"data": {"rawApiKey": "api-key"}}),
    ]

    credentials = OwnerSetup(session=session).bootstrap("http://n8n:5678", "owner@test", "password")

    assert credentials == ApiCredentials("api-key")
    calls = session.request.call_args_list
    assert [call.args[0] for call in calls] == ["POST", "POST", "GET", "POST"]
    assert [call.args[1] for call in calls] == [
        "http://n8n:5678/rest/owner/setup",
        "http://n8n:5678/rest/login",
        "http://n8n:5678/rest/api-keys",
        "http://n8n:5678/rest/api-keys",
    ]
    assert calls[0].kwargs["json"] == {
        "firstName": "n8n",
        "lastName": "Launcher",
        "email": "owner@test",
        "password": "password",
    }
    assert calls[1].kwargs["json"] == {"emailOrLdapLoginId": "owner@test", "password": "password"}
    assert calls[3].kwargs["json"]["scopes"] == REQUIRED_WORKFLOW_SCOPES
    assert calls[3].kwargs["json"]["expiresAt"] == 0


def test_owner_setup_replaces_stale_launcher_keys() -> None:
    session = MagicMock()
    session.request.side_effect = [
        response({"data": {"id": "owner"}}),
        response({"data": {"token": "session"}}),
        response({"data": {"items": [{"id": "old-key", "label": "n8n-launcher"}]}}),
        response({"data": {"success": True}}),
        response({"data": {"rawApiKey": "api-key"}}),
    ]

    credentials = OwnerSetup(session=session).bootstrap("http://n8n:5678", "owner@test", "password")

    assert credentials == ApiCredentials("api-key")
    calls = session.request.call_args_list
    assert [call.args[0] for call in calls] == ["POST", "POST", "GET", "DELETE", "POST"]
    assert calls[3].args[1] == "http://n8n:5678/rest/api-keys/old-key"


def test_owner_setup_skips_when_owner_already_created() -> None:
    already = MagicMock()
    already.ok = False
    already.status_code = 400
    already.reason = "Bad Request"
    already.text = "Instance owner already setup"
    already.json.return_value = {"code": 400, "message": "Instance owner already setup"}
    session = MagicMock()
    session.request.side_effect = [
        already,
        response({"data": {"token": "session"}}),
        response({"data": {"items": []}}),
        response({"data": {"rawApiKey": "api-key"}}),
    ]

    credentials = OwnerSetup(session=session).bootstrap("http://n8n:5678", "owner@test", "password")

    assert credentials == ApiCredentials("api-key")
    assert session.request.call_count == 4


def test_owner_setup_retries_while_n8n_is_starting() -> None:
    session = MagicMock()
    setup_attempts = []

    def fake_request(method, url, **kwargs):
        if url.endswith("/rest/owner/setup"):
            setup_attempts.append(url)
            if len(setup_attempts) <= 2:
                starting = MagicMock()
                starting.ok = True
                starting.status_code = 200
                starting.text = "n8n is starting up. Please wait"
                starting.json.side_effect = ValueError()
                return starting
            return response({"data": {"id": "owner"}})
        if url.endswith("/rest/login"):
            return response({"data": {"token": "session"}})
        if url.endswith("/rest/api-keys"):
            return response({"data": {"rawApiKey": "api-key"}})
        raise AssertionError(url)

    session.request.side_effect = fake_request

    credentials = OwnerSetup(session=session).bootstrap(
        "http://n8n:5678", "owner@test", "password", ready_timeout=10.0
    )

    assert credentials == ApiCredentials("api-key")
    assert len(setup_attempts) == 3


def test_wait_for_n8n_returns_when_readiness_endpoint_is_ready() -> None:
    session = MagicMock()
    session.get.return_value = response({"status": "ok"})

    wait_for_n8n("http://n8n:5678", session=session)

    session.get.assert_called_once_with("http://n8n:5678/healthz/readiness", timeout=10.0)


def test_wait_for_n8n_keeps_waiting_while_readiness_is_503() -> None:
    """A 503 is n8n still migrating; the UI root would answer ``Cannot GET /``."""
    session = MagicMock()
    session.get.side_effect = [
        response({"status": "error"}, status_code=503),
        response({"status": "ok"}),
    ]

    wait_for_n8n("http://n8n:5678", interval=0, session=session)

    assert [call.args[0] for call in session.get.call_args_list] == [
        "http://n8n:5678/healthz/readiness",
        "http://n8n:5678/healthz/readiness",
    ]


def test_wait_for_n8n_falls_back_to_ui_root_without_the_readiness_route() -> None:
    """An n8n without ``/healthz/readiness`` is gated on the page itself."""
    session = MagicMock()
    session.get.side_effect = [
        response({"message": "not found"}, status_code=404),
        response("<!DOCTYPE html>", status_code=200),
    ]

    wait_for_n8n("http://n8n:5678", session=session)

    assert [call.args[0] for call in session.get.call_args_list] == [
        "http://n8n:5678/healthz/readiness",
        "http://n8n:5678/",
    ]


def test_wait_for_n8n_fails_when_timeout_expires() -> None:
    session = MagicMock()
    session.get.side_effect = RuntimeError("unexpected")

    with pytest.raises(OwnerSetupError, match="n'est pas prêt"):
        wait_for_n8n("http://n8n:5678", timeout=0, session=session)
