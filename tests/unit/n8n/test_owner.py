from unittest.mock import MagicMock

from n8n_launcher.n8n.owner import (
    REQUIRED_WORKFLOW_SCOPES,
    ApiCredentials,
    OwnerSetup,
    hash_owner_password,
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
