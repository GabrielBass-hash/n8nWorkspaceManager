from unittest.mock import MagicMock

import pytest
import requests

from n8n_launcher.n8n.api import N8nApiClient, N8nApiError


def response(payload, status_code=200):
    item = MagicMock()
    item.ok = status_code < 400
    item.status_code = status_code
    item.text = ""
    item.json.return_value = payload
    return item


def test_api_client_sends_key_and_lists_workflows() -> None:
    session = MagicMock()
    session.request.return_value = response({"data": [{"id": "one"}]})
    client = N8nApiClient("http://localhost:5678/api/v1", "secret", session=session)

    assert client.list_workflows() == [{"id": "one"}]
    request = session.request.call_args
    assert request.args[:2] == ("GET", "http://localhost:5678/api/v1/workflows")
    assert request.kwargs["headers"]["X-N8N-API-KEY"] == "secret"


def test_api_client_get_credential_returns_secret_data() -> None:
    """The list endpoint hides values; GET /credentials/{id} exposes them."""
    session = MagicMock()
    session.request.return_value = response(
        {"id": "c1", "name": "API", "type": "httpRequest", "data": {"user": "u", "password": "p"}}
    )
    client = N8nApiClient("http://localhost:5678/api/v1", "secret", session=session)

    detail = client.get_credential("c1")

    assert detail["data"] == {"user": "u", "password": "p"}
    request = session.request.call_args
    assert request.args[:2] == ("GET", "http://localhost:5678/api/v1/credentials/c1")


def test_get_workflow_returns_full_export() -> None:
    session = MagicMock()
    export = {"id": "w1", "name": "Export", "nodes": [], "connections": {}}
    session.request.return_value = response(export)
    client = N8nApiClient("http://localhost:5678/api/v1/", "secret", session=session)

    assert client.get_workflow("w1") == export
    request = session.request.call_args
    assert request.args == ("GET", "http://localhost:5678/api/v1/workflows/w1")


def test_create_workflow_posts_payload() -> None:
    session = MagicMock()
    workflow = {"name": "New", "nodes": [], "connections": {}, "settings": {}}
    session.request.return_value = response({"id": "w1"})
    client = N8nApiClient("http://localhost:5678/api/v1", "secret", session=session)

    assert client.create_workflow(workflow) == {"id": "w1"}
    request = session.request.call_args
    assert request.args[:2] == ("POST", "http://localhost:5678/api/v1/workflows")
    assert request.kwargs["json"] == workflow


def test_update_workflow_puts_payload() -> None:
    session = MagicMock()
    workflow = {"id": "w1", "name": "Updated", "nodes": []}
    session.request.return_value = response(workflow)
    client = N8nApiClient("http://localhost:5678/api/v1", "secret", session=session)

    assert client.update_workflow("w1", workflow) == workflow
    request = session.request.call_args
    assert request.args[:2] == ("PUT", "http://localhost:5678/api/v1/workflows/w1")
    assert request.kwargs["json"] == workflow


def test_delete_workflow_handles_204() -> None:
    session = MagicMock()
    item = response(None, status_code=204)
    session.request.return_value = item
    client = N8nApiClient("http://localhost:5678/api/v1", "secret", session=session)

    assert client.delete_workflow("w1") is None
    item.json.assert_not_called()
    assert session.request.call_args.args[:2] == (
        "DELETE",
        "http://localhost:5678/api/v1/workflows/w1",
    )


@pytest.mark.parametrize(
    ("active", "action"),
    [(True, "activate"), (False, "deactivate")],
)
def test_activate_workflow_posts_requested_action(active: bool, action: str) -> None:
    session = MagicMock()
    session.request.return_value = response({"id": "w1", "active": active})
    client = N8nApiClient("http://localhost:5678/api/v1", "secret", session=session)

    assert client.activate_workflow("w1", active=active) == {"id": "w1", "active": active}
    assert session.request.call_args.args[:2] == (
        "POST",
        f"http://localhost:5678/api/v1/workflows/w1/{action}",
    )


def test_list_credentials_returns_data() -> None:
    session = MagicMock()
    session.request.return_value = response({"data": [{"id": "c1", "name": "API"}]})
    client = N8nApiClient("http://localhost:5678/api/v1", "secret", session=session)

    assert client.list_credentials() == [{"id": "c1", "name": "API"}]
    assert session.request.call_args.args[:2] == (
        "GET",
        "http://localhost:5678/api/v1/credentials",
    )


def test_list_credentials_returns_empty_on_404() -> None:
    session = MagicMock()
    session.request.return_value = response({}, status_code=404)
    client = N8nApiClient("http://localhost:5678/api/v1", "secret", session=session)

    assert client.list_credentials() == []
    assert session.request.call_args.args[:2] == (
        "GET",
        "http://localhost:5678/api/v1/credentials",
    )


def test_list_credentials_does_not_ignore_403() -> None:
    session = MagicMock()
    item = response({}, status_code=403)
    item.text = "forbidden"
    session.request.return_value = item
    client = N8nApiClient("http://localhost:5678/api/v1", "secret", session=session)

    with pytest.raises(N8nApiError) as exc_info:
        client.list_credentials()

    assert exc_info.value.status_code == 403


def test_create_credential_posts_payload() -> None:
    session = MagicMock()
    credential = {"name": "DB", "type": "postgres", "data": {"host": "db"}}
    session.request.return_value = response({"id": "c1"})
    client = N8nApiClient("http://localhost:5678/api/v1", "secret", session=session)

    assert client.create_credential(credential) == {"id": "c1"}
    request = session.request.call_args
    assert request.args[:2] == ("POST", "http://localhost:5678/api/v1/credentials")
    assert request.kwargs["json"] == credential


def test_ensure_postgres_credential_returns_existing_by_name() -> None:
    client = N8nApiClient("http://localhost:5678/api/v1", "secret", session=MagicMock())
    existing = {"id": "c1", "name": "Workspace DB", "type": "postgres"}
    client.list_credentials = MagicMock(return_value=[{"id": "other", "name": "Other"}, existing])
    client.create_credential = MagicMock()

    result = client.ensure_postgres_credential(
        "Workspace DB",
        host="postgres",
        port=5432,
        database="data",
        user="n8ndata",
        password="secret",
    )

    assert result == existing
    client.create_credential.assert_not_called()


def test_ensure_postgres_credential_creates_when_missing() -> None:
    client = N8nApiClient("http://localhost:5678/api/v1", "secret", session=MagicMock())
    client.list_credentials = MagicMock(return_value=[{"id": "c0", "name": "Other"}])
    client.create_credential = MagicMock(return_value={"id": "c1"})

    result = client.ensure_postgres_credential(
        "Workspace DB",
        host="postgres",
        port=5432,
        database="data",
        user="n8ndata",
        password="secret",
    )

    assert result == {"id": "c1"}
    client.create_credential.assert_called_once_with(
        {
            "name": "Workspace DB",
            "type": "postgres",
            "data": {
                "host": "postgres",
                "port": 5432,
                "database": "data",
                "user": "n8ndata",
                "password": "secret",
                "ssl": "disable",
            },
        }
    )


@pytest.mark.parametrize("status_code", [401, 403, 500])
def test_request_raises_n8n_api_error_with_status_code(status_code: int) -> None:
    session = MagicMock()
    item = response({}, status_code=status_code)
    item.text = "request rejected"
    session.request.return_value = item
    client = N8nApiClient("http://localhost:5678/api/v1", "secret", session=session)

    with pytest.raises(N8nApiError) as exc_info:
        client.list_workflows()

    assert exc_info.value.status_code == status_code
    assert str(exc_info.value) == (f"n8n API returned {status_code}: request rejected")


def test_request_error_detail_is_limited_to_500_characters() -> None:
    session = MagicMock()
    item = response({}, status_code=500)
    item.text = "x" * 600
    session.request.return_value = item
    client = N8nApiClient("http://localhost:5678/api/v1", "secret", session=session)

    with pytest.raises(N8nApiError) as exc_info:
        client.list_workflows()

    assert str(exc_info.value) == f"n8n API returned 500: {'x' * 500}"


def test_request_raises_on_invalid_json() -> None:
    session = MagicMock()
    item = response(None)
    item.json.side_effect = ValueError("invalid")
    session.request.return_value = item
    client = N8nApiClient("http://localhost:5678/api/v1", "secret", session=session)

    with pytest.raises(N8nApiError, match="invalid JSON") as exc_info:
        client.list_workflows()

    assert exc_info.value.status_code is None
    assert isinstance(exc_info.value.__cause__, ValueError)


def test_request_wraps_network_errors() -> None:
    session = MagicMock()
    cause = requests.ConnectionError("offline")
    session.request.side_effect = cause
    client = N8nApiClient("http://localhost:5678/api/v1", "secret", session=session)

    with pytest.raises(N8nApiError, match="request failed: offline") as exc_info:
        client.list_workflows()

    assert exc_info.value.status_code is None
    assert exc_info.value.__cause__ is cause


def test_request_204_returns_empty_dict() -> None:
    session = MagicMock()
    item = response(None, status_code=204)
    session.request.return_value = item
    client = N8nApiClient("http://localhost:5678/api/v1", "secret", session=session)

    assert client._request("GET", "/empty") == {}
    item.json.assert_not_called()


def test_request_forwards_http_options() -> None:
    session = MagicMock()
    session.request.return_value = response({"ok": True})
    client = N8nApiClient(
        "http://localhost:5678/api/v1/",
        "secret",
        timeout=4.5,
        session=session,
    )
    payload = {"name": "New"}
    params = {"limit": 10}

    assert client._request("POST", "/query", json=payload, params=params) == {"ok": True}
    request = session.request.call_args
    assert request.args == ("POST", "http://localhost:5678/api/v1/query")
    assert request.kwargs == {
        "headers": {"X-N8N-API-KEY": "secret", "Accept": "application/json"},
        "timeout": 4.5,
        "json": payload,
        "params": params,
    }
