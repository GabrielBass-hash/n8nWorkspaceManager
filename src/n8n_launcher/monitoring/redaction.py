"""Helpers for removing secret-shaped values from monitoring payloads."""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import date, datetime
from pathlib import Path

_SENSITIVE_KEY_PARTS = (
    "password",
    "passwd",
    "pwd",
    "secret",
    "token",
    "apikey",
    "accesskey",
    "privatekey",
    "clientsecret",
    "authorization",
    "credential",
    "cookie",
    "passphrase",
)

_AUTHORIZATION_RE = re.compile(
    r"(?i)(?P<prefix>[\"']?\bauthorization\b[\"']?\s*[:=]\s*)(?P<value>[^,\r\n]+)"
)
# The key may be quoted because the most common carrier is a JSON body
# (``{"api_key": "..."}``), so the closing quote sits between the key and the
# separator. A non-capturing quote pair keeps the substitution prefix intact.
_ASSIGNMENT_RE = re.compile(
    r"(?i)(?P<prefix>[\"']?\b(?:password|passwd|pwd|secret|token|api[_-]?key|"
    r"access[_-]?key|client[_-]?secret|cookie|passphrase)\b[\"']?\s*[:=]\s*)"
    r"(?:(?P<quote>['\"])(?P<quoted>.*?)(?P=quote)|"
    r"(?P<value>[^\s,;}\]\r\n]+))"
)
_BEARER_RE = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")
_URL_PASSWORD_RE = re.compile(r"(?i)(?P<prefix>[a-z][\w+.-]*://[^/\s:@]+:)[^@\s]+(?P<suffix>@)")
_TOKEN_RE = re.compile(
    r"\b(?:gh[pousr]_[A-Za-z0-9_]+|github_pat_[A-Za-z0-9_]+|sk-[A-Za-z0-9_-]{8,})\b"
)
_NORMALIZE_KEY_RE = re.compile(r"[^a-z0-9]")


def _redact_text(value: str, replacement: str) -> str:
    """Redact common secret forms from a string without changing ordinary text."""
    value = _AUTHORIZATION_RE.sub(lambda match: f"{match.group('prefix')}{replacement}", value)
    value = _ASSIGNMENT_RE.sub(
        lambda match: (
            f"{match.group('prefix')}{match.group('quote') or ''}{replacement}"
            f"{match.group('quote') or ''}"
        ),
        value,
    )
    value = _BEARER_RE.sub(f"Bearer {replacement}", value)
    value = _URL_PASSWORD_RE.sub(
        lambda match: f"{match.group('prefix')}{replacement}{match.group('suffix')}",
        value,
    )
    return _TOKEN_RE.sub(replacement, value)


def _is_sensitive_key(key: str) -> bool:
    """Return whether a mapping key names a value that must never be retained."""
    normalized = _NORMALIZE_KEY_RE.sub("", key.casefold())
    return any(part in normalized for part in _SENSITIVE_KEY_PARTS)


def redact_secrets(value: object, replacement: str = "[REDACTED]") -> object:
    """Return a recursively redacted, JSON-friendly copy of *value*.

    Mapping keys that identify credentials, passwords, tokens, or session
    material are replaced as a whole. Strings are also scanned for common
    assignment, bearer-token, GitHub-token, and OpenAI-style key forms so a
    secret cannot escape through a log message or exception text.
    """
    if isinstance(value, Mapping):
        redacted: dict[str, object] = {}
        for key, item in value.items():
            key_text = _redact_text(str(key), replacement)
            if _is_sensitive_key(str(key)):
                redacted[key_text] = replacement
            else:
                redacted[key_text] = redact_secrets(item, replacement)
        return redacted
    if isinstance(value, str):
        return _redact_text(value, replacement)
    if isinstance(value, (list, tuple)):
        values = [redact_secrets(item, replacement) for item in value]
        return tuple(values) if isinstance(value, tuple) else values
    if isinstance(value, set):
        return [redact_secrets(item, replacement) for item in value]
    if isinstance(value, (Path, datetime, date)):
        return _redact_text(str(value), replacement)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, bytes):
        return _redact_text(value.decode("utf-8", errors="replace"), replacement)
    return _redact_text(str(value), replacement)


def _jsonable(value: object) -> object:
    """Return *value* converted to structures accepted by the JSON encoder."""
    redacted = redact_secrets(value)
    if isinstance(redacted, Mapping):
        return {str(key): _jsonable(item) for key, item in redacted.items()}
    if isinstance(redacted, list):
        return [_jsonable(item) for item in redacted]
    if isinstance(redacted, tuple):
        return [_jsonable(item) for item in redacted]
    if redacted is None or isinstance(redacted, (str, int, float, bool)):
        return redacted
    return str(redacted)
