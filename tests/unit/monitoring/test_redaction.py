"""Tests for secret redaction in monitoring payloads."""

from datetime import UTC, datetime
from pathlib import Path

from n8n_launcher.monitoring.redaction import redact_secrets


def test_redacts_sensitive_mapping_keys_as_a_whole() -> None:
    redacted = redact_secrets({"password": "hunter2", "workspace": "ws1"})

    assert redacted == {"password": "[REDACTED]", "workspace": "ws1"}


def test_redacts_nested_and_varied_sensitive_keys() -> None:
    redacted = redact_secrets(
        {
            "db": {"username": "n8n", "api_key": "abc", "clientSecret": "s3cr3t"},
            "items": [{"token": "t0ken"}],
        }
    )

    assert redacted == {
        "db": {"username": "n8n", "api_key": "[REDACTED]", "clientSecret": "[REDACTED]"},
        "items": [{"token": "[REDACTED]"}],
    }


def test_redacts_assignment_in_plain_text() -> None:
    assert redact_secrets("login password=hunter2 ok") == "login password=[REDACTED] ok"


def test_redacts_quoted_assignment_in_plain_text() -> None:
    redacted = redact_secrets('secrets.json: {"api_key": "abcd1234"}')

    assert "abcd1234" not in redacted
    assert '"api_key": "[REDACTED]"' in redacted


def test_redacts_authorization_header() -> None:
    redacted = redact_secrets("Authorization: Bearer eyJhbGciOi.J9.sig")

    assert "eyJhbGciOi" not in redacted
    assert redacted.startswith("Authorization: ")


def test_redacts_bearer_and_known_token_shapes() -> None:
    redacted = redact_secrets("Bearer abc.def-ghi ghp_abcdefghijklmnop sk-abcdefgh1234")

    assert "abc.def-ghi" not in redacted
    assert "ghp_abcdefghijklmnop" not in redacted
    assert "sk-abcdefgh1234" not in redacted


def test_redacts_password_embedded_in_url() -> None:
    redacted = redact_secrets("ssh://user:pw@host/repo.git")

    assert "user:pw@" not in redacted
    assert "user:[REDACTED]@host" in redacted


def test_keeps_ordinary_text_untouched() -> None:
    assert redact_secrets("workspace ws1 démarré sur le port 5678") == (
        "workspace ws1 démarré sur le port 5678"
    )


def test_honors_custom_replacement() -> None:
    assert redact_secrets("token=abc", "***") == "token=***"


def test_converts_containers_to_json_friendly_values() -> None:
    redacted = redact_secrets(
        {
            "tuple": ("a", "b"),
            "set": {"only"},
            "path": Path("/tmp/ws"),
            "when": datetime(2026, 1, 1, tzinfo=UTC),
            "blob": b"password=bytes-secret",
        }
    )

    # Tuples and sets stay containers here; ``Event`` normalizes them to JSON
    # lists through the module's private ``_jsonable`` step.
    assert redacted["tuple"] == ("a", "b")
    assert redacted["set"] == ["only"]
    assert redacted["path"] == "/tmp/ws"
    assert redacted["when"].startswith("2026-01-01")
    assert "bytes-secret" not in redacted["blob"]


def test_keeps_scalars_and_none() -> None:
    redacted = redact_secrets({"count": 3, "ratio": 1.5, "ok": True, "value": None})

    assert redacted == {"count": 3, "ratio": 1.5, "ok": True, "value": None}


def test_redacts_sensitive_key_names_case_and_separators_insensitively() -> None:
    redacted = redact_secrets({"API-KEY": "x", "Client_Secret": "y", "PWD": "z"})

    assert redacted == {"API-KEY": "[REDACTED]", "Client_Secret": "[REDACTED]", "PWD": "[REDACTED]"}
