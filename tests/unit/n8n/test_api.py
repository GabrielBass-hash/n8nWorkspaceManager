from unittest.mock import MagicMock

from n8n_launcher.n8n.api import N8nApiClient


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
