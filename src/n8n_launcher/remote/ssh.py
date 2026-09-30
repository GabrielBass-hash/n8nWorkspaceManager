"""Safe SSH primitives for remote deployment and observability.

The launcher talks to a production server through the system ``ssh`` binary in
batch mode. Read helpers quote every path, cap returned data and redact common
credential forms before values cross the process boundary. Query helpers keep
raw Docker or n8n payloads out of their public result objects.
"""

from __future__ import annotations

import json
import logging
import re
import shlex
import subprocess
from dataclasses import dataclass, field
from typing import Any

from ..core.models import ServerConfig
from .deploy import (
    PENDING_EXECUTION_STATUSES,
    REMOTE_STATUS_CAPABILITY,
    TERMINAL_EXECUTION_STATUSES,
    checkout_dir,
    deploy_script_path,
    history_path,
    marker_path,
    resolve_base,
)

logger = logging.getLogger(__name__)

MAX_REMOTE_OUTPUT_BYTES = 4 * 1024 * 1024
MAX_REMOTE_LOG_LINES = 5_000
MAX_REMOTE_HISTORY_ENTRIES = 100
_MAX_ERROR_CHARS = 1_000
_SAFE_SERVICE_NAME = re.compile(r"^[A-Za-z0-9_.-]+$")
_SAFE_WORKSPACE_ID = re.compile(r"^[A-Za-z0-9_-]+$")
_SENSITIVE_BASENAMES = frozenset(
    {
        "secrets.json",
        ".env",
        "credentials.json",
        "id_rsa",
        "id_dsa",
        "id_ecdsa",
        "id_ed25519",
    }
)
_QUOTED_SECRET = re.compile(
    r"(?i)(?P<prefix>[\"']?(?:owner_)?(?:password|passwd|api[_-]?key|token|secret|"
    r"authorization|cookie|private[_-]?key|encryption[_-]?key|credentials)[\"']?"
    r"\s*[:=]\s*)(?P<quote>[\"'])(?P<value>.*?)(?P=quote)"
)
_UNQUOTED_SECRET = re.compile(
    r"(?i)(?P<prefix>\b(?:owner_)?(?:password|passwd|api[_-]?key|token|secret|"
    r"authorization|cookie|private[_-]?key|encryption[_-]?key|credentials)\b"
    r"\s*[:=]\s*)(?P<value>[^\s,;}\]]+)"
)
_BEARER_SECRET = re.compile(r"(?i)(?P<prefix>\bBearer\s+)[^\s,;]+")
_URL_PASSWORD = re.compile(r"(?i)(?P<prefix>https?://[^/\s:@]+:)[^@\s]+(?P<suffix>@)")


class SshError(RuntimeError):
    """Raised when a remote SSH command fails."""


@dataclass(frozen=True)
class RemoteHealth:
    """Sanitized health information for a remote Compose project."""

    available: bool
    healthy: bool
    services: dict[str, str] = field(default_factory=dict)
    health: dict[str, str] = field(default_factory=dict)
    error: str | None = None

    @property
    def status(self) -> str:
        """Return a compact human-readable status label."""
        if not self.available:
            return "unavailable"
        return "healthy" if self.healthy else "degraded"


@dataclass(frozen=True)
class RemoteExecution:
    """One redacted n8n execution summary returned by the remote status command."""

    execution_id: str
    status: str
    workflow_name: str | None = None
    started_at: str | None = None
    stopped_at: str | None = None
    finished: bool | None = None

    @property
    def id(self) -> str:
        """Return the execution identifier under the short API-friendly name."""
        return self.execution_id


@dataclass(frozen=True)
class RemoteExecutionStatus:
    """Result of a read-only remote execution-status query."""

    supported: bool
    executions: tuple[RemoteExecution, ...] = ()
    error: str | None = None


def _redact_text(value: object, *, max_chars: int = MAX_REMOTE_OUTPUT_BYTES) -> str:
    """Return bounded text with common credential forms removed."""
    text = str(value)
    text = _QUOTED_SECRET.sub(
        lambda match: (
            f"{match.group('prefix')}{match.group('quote')}[REDACTED]{match.group('quote')}"
        ),
        text,
    )
    text = _UNQUOTED_SECRET.sub(
        lambda match: f"{match.group('prefix')}[REDACTED]",
        text,
    )
    text = _BEARER_SECRET.sub(lambda match: f"{match.group('prefix')}[REDACTED]", text)
    text = _URL_PASSWORD.sub(
        lambda match: f"{match.group('prefix')}[REDACTED]{match.group('suffix')}",
        text,
    )
    if len(text) > max_chars:
        return text[:max_chars] + "…"
    return text


def _bounded_text(value: str, max_bytes: int) -> str:
    """Bound *value* by UTF-8 bytes without returning invalid text."""
    encoded = value.encode("utf-8", "replace")
    if len(encoded) <= max_bytes:
        return value
    return encoded[:max_bytes].decode("utf-8", "ignore")


def _redact_value(value: object) -> object:
    """Recursively redact strings in a JSON-compatible value."""
    if isinstance(value, str):
        return _redact_text(value, max_chars=_MAX_ERROR_CHARS)
    if isinstance(value, list):
        return [_redact_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _redact_value(item) for key, item in value.items()}
    return value


def _remote_quote(path: str) -> str:
    """Quote a remote path for use in the remote shell."""
    return shlex.quote(path)


def _validate_remote_path(path: str) -> None:
    """Reject malformed remote paths before they reach a shell command."""
    if not isinstance(path, str) or not path or "\x00" in path:
        raise ValueError("Chemin distant invalide")


def _refuse_secret_read(path: str) -> None:
    """Refuse to read back a file whose name identifies credential material.

    Writing and chmod'ing those files is legitimate (the launcher ships
    ``secrets.json`` and locks it down), so the refusal only guards the read
    helpers, which must never pull secrets into the launcher's memory or GUI.
    """
    basename = path.replace("\\", "/").rsplit("/", 1)[-1].casefold()
    if basename in _SENSITIVE_BASENAMES or basename.startswith(".env."):
        raise SshError("Refus de lire un fichier de secrets distant")


def _validate_workspace_id(workspace_id: str) -> None:
    """Validate the generated workspace identifier used in remote paths."""
    if not isinstance(workspace_id, str) or not _SAFE_WORKSPACE_ID.fullmatch(workspace_id):
        raise ValueError("Identifiant de workspace distant invalide")


def _bounded_value(value: int, *, name: str, minimum: int, maximum: int) -> int:
    """Validate and bound a caller-provided numeric limit."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} doit être un entier")
    if value < minimum or value > maximum:
        raise ValueError(f"{name} doit être compris entre {minimum} et {maximum}")
    return value


def _safe_remote_error(result: subprocess.CompletedProcess[str], fallback: str) -> str:
    """Build a bounded, redacted SSH error message without command arguments."""
    detail = result.stderr.strip() or result.stdout.strip() or fallback
    return _redact_text(detail, max_chars=_MAX_ERROR_CHARS) or fallback


def _ssh_argv(cfg: ServerConfig, command: str | None = None) -> list[str]:
    """Return the ``ssh`` argument vector for *cfg*."""
    argv = [
        "ssh",
        "-p",
        str(cfg.ssh_port),
        "-o",
        "BatchMode=yes",
        "-o",
        "StrictHostKeyChecking=accept-new",
    ]
    if cfg.key_path:
        argv += ["-o", "IdentitiesOnly=yes", "-i", cfg.key_path]
    argv.append(f"{cfg.user}@{cfg.host}")
    if command:
        argv.append(command)
    return argv


def ssh_run(
    cfg: ServerConfig,
    command: str,
    *,
    stdin: str | None = None,
    timeout: float = 30.0,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    """Run *command* over SSH and return the completed process.

    Raises :class:`SshError` when the command exits non-zero and ``check`` is
    set. ``stdin`` lets a caller pipe data such as a generated script without
    putting its contents in the process arguments.
    """
    argv = _ssh_argv(cfg, command)
    try:
        result = subprocess.run(
            argv,
            input=stdin,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=timeout,
        )
    except FileNotFoundError:
        raise SshError("ssh n'est pas installé ou introuvable dans le PATH") from None
    except subprocess.TimeoutExpired as exc:
        raise SshError(f"Connexion SSH à {cfg.host} expirée (>{timeout:.0f}s)") from exc
    except OSError as exc:
        raise SshError(f"ssh n'a pas pu être exécuté : {_redact_text(exc)}") from exc
    if check and result.returncode != 0:
        detail = _safe_remote_error(result, f"code {result.returncode}")
        raise SshError(f"Commande SSH sur {cfg.host} échouée : {detail}")
    return result


def test_connection(cfg: ServerConfig) -> str:
    """Probe the server prerequisites needed by the deployment listener.

    Checks that ``docker`` (with the Compose plugin), ``git`` and ``python3``
    are available, returning their combined version output. Raises
    :class:`SshError` when a prerequisite is missing or the connection fails.
    """

    def fail(tool: str) -> str:
        """Return a shell probe that fails when *tool* is absent."""
        quoted = _remote_quote(tool)
        return (
            f"command -v {quoted} >/dev/null 2>&1 || "
            f"{{ echo '{tool} introuvable sur le serveur'; exit 1; }}"
        )

    probe = "\n".join(
        (
            fail("docker"),
            "docker compose version",
            fail("git"),
            "git --version",
            fail("python3"),
            "python3 --version",
        )
    )
    result = ssh_run(cfg, probe, timeout=20.0)
    output = _redact_text(result.stdout)
    logger.info("Server prerequisites on %s: %s", cfg.host, output.strip())
    return output


# pytest would otherwise collect this public helper as a test function and
# report a missing ``cfg`` fixture as a collection error.
test_connection.__test__ = False  # type: ignore[reportFunctionMemberAccess]


def mkdir_remote(cfg: ServerConfig, path: str, *, parents: bool = True) -> None:
    """Create *path* on the server with ``mkdir``."""
    _validate_remote_path(path)
    args = "-p" if parents else ""
    ssh_run(cfg, f"mkdir {args} {_remote_quote(path)}")


def write_remote_file(cfg: ServerConfig, path: str, content: str) -> None:
    """Atomically stream *content* to *path* on the server.

    The bytes land in ``<path>.tmp`` first and an atomic rename publishes the
    complete file, so a remote reader never observes a partial document.
    """
    _validate_remote_path(path)
    quoted = _remote_quote(path)
    ssh_run(
        cfg,
        f"cat > {quoted}.tmp && mv -f {quoted}.tmp {quoted}",
        stdin=content,
        timeout=60.0,
    )


def chmod_remote(cfg: ServerConfig, path: str, mode: str = "+x") -> None:
    """Change a remote file mode after validating the path and mode."""
    _validate_remote_path(path)
    if not re.fullmatch(r"[0-7]{3,4}|[+=-][A-Za-z]+", mode):
        raise ValueError("Mode de fichier distant invalide")
    ssh_run(cfg, f"chmod {mode} {_remote_quote(path)}")


def read_remote_file(
    cfg: ServerConfig,
    path: str,
    *,
    max_bytes: int = 64 * 1024,
    timeout: float = 30.0,
    missing_ok: bool = False,
    redact: bool = True,
) -> str:
    """Read a bounded remote text file, redacted unless the caller opts out.

    ``max_bytes`` is enforced on both sides of the SSH boundary. A missing
    file raises :class:`SshError` unless ``missing_ok`` is true. Files whose
    names identify credential material are refused before opening SSH.

    ``redact=False`` returns the bounded raw text and is reserved for callers
    that parse the document themselves: pattern redaction rewrites quoted
    values in place, which would corrupt a JSON payload before it can be
    parsed. Such callers must redact the parsed structure instead.
    """
    _validate_remote_path(path)
    _refuse_secret_read(path)
    limit = _bounded_value(
        max_bytes,
        name="max_bytes",
        minimum=1,
        maximum=MAX_REMOTE_OUTPUT_BYTES,
    )
    quoted = _remote_quote(path)
    result = ssh_run(
        cfg,
        f"if [ -f {quoted} ]; then head -c {limit} -- {quoted}; else exit 44; fi",
        timeout=timeout,
        check=False,
    )
    if result.returncode == 44:
        if missing_ok:
            return ""
        raise SshError(f"Fichier distant introuvable : {path}")
    if result.returncode != 0:
        raise SshError(
            f"Lecture distante impossible : {_safe_remote_error(result, 'commande refusée')}"
        )
    if not redact:
        return _bounded_text(result.stdout, limit)
    return _bounded_text(_redact_text(result.stdout, max_chars=limit * 2), limit)


def tail_remote_file(
    cfg: ServerConfig,
    path: str,
    *,
    lines: int = 200,
    max_bytes: int = 64 * 1024,
    timeout: float = 30.0,
    missing_ok: bool = False,
) -> str:
    """Return a bounded tail of a redacted remote text file.

    The line count and byte count are validated before interpolation into the
    remote command, so neither can become shell syntax. A missing file follows
    the same ``missing_ok`` contract as :func:`read_remote_file`.
    """
    _validate_remote_path(path)
    _refuse_secret_read(path)
    line_count = _bounded_value(
        lines,
        name="lines",
        minimum=1,
        maximum=MAX_REMOTE_LOG_LINES,
    )
    limit = _bounded_value(
        max_bytes,
        name="max_bytes",
        minimum=1,
        maximum=MAX_REMOTE_OUTPUT_BYTES,
    )
    quoted = _remote_quote(path)
    result = ssh_run(
        cfg,
        (
            f"if [ -f {quoted} ]; then "
            f"tail -n {line_count} -- {quoted} | head -c {limit} --; "
            "else exit 44; fi"
        ),
        timeout=timeout,
        check=False,
    )
    if result.returncode == 44:
        if missing_ok:
            return ""
        raise SshError(f"Fichier distant introuvable : {path}")
    if result.returncode != 0:
        raise SshError(
            f"Lecture distante impossible : {_safe_remote_error(result, 'commande refusée')}"
        )
    return _bounded_text(_redact_text(result.stdout, max_chars=limit * 2), limit)


def _compose_parts(cfg: ServerConfig, workspace_id: str) -> tuple[str, str]:
    """Return the remote Compose file and project for *workspace_id*."""
    _validate_workspace_id(workspace_id)
    compose = f"{checkout_dir(cfg, workspace_id)}/compose.yml"
    project = f"n8n-ws-{workspace_id}"
    return compose, project


def _compose_rows(raw: str) -> list[dict[str, Any]]:
    """Decode Docker Compose JSON output across line and array formats."""
    try:
        payload = json.loads(raw)
    except ValueError:
        rows: list[dict[str, Any]] = []
        for line in raw.splitlines():
            try:
                item = json.loads(line)
            except ValueError:
                continue
            if isinstance(item, dict):
                rows.append(item)
        return rows
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        data = payload.get("data")
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
        return [payload]
    return []


def remote_health(
    cfg: ServerConfig,
    workspace_id: str,
    *,
    timeout: float = 30.0,
) -> RemoteHealth:
    """Return a sanitized Compose health snapshot for a remote workspace.

    The query only runs ``docker compose ps`` and returns service names,
    states and health values. Docker's raw command output is never exposed.
    """
    compose, project = _compose_parts(cfg, workspace_id)
    command = (
        f"docker compose -f {_remote_quote(compose)} -p {_remote_quote(project)} ps --format json"
    )
    result = ssh_run(cfg, command, timeout=timeout, check=False)
    if result.returncode != 0:
        return RemoteHealth(
            available=False,
            healthy=False,
            error=_safe_remote_error(result, "docker compose ps a échoué"),
        )
    services: dict[str, str] = {}
    health: dict[str, str] = {}
    for row in _compose_rows(result.stdout):
        service = row.get("Service") or row.get("service")
        state = row.get("State") or row.get("state")
        if not isinstance(service, str) or not isinstance(state, str):
            continue
        services[service] = state
        value = row.get("Health") or row.get("health")
        if isinstance(value, str) and value:
            health[service] = value
    n8n_state = services.get("n8n")
    n8n_health = health.get("n8n")
    healthy = n8n_state == "running" and n8n_health in (None, "", "healthy")
    error = None if healthy else "Le service n8n n'est pas sain"
    return RemoteHealth(
        available=True,
        healthy=healthy,
        services=services,
        health=health,
        error=error,
    )


def remote_logs(
    cfg: ServerConfig,
    workspace_id: str,
    *,
    lines: int = 200,
    service: str = "n8n",
    max_bytes: int = 256 * 1024,
    timeout: float = 30.0,
) -> str:
    """Return a bounded, redacted tail of a remote Compose service log."""
    compose, project = _compose_parts(cfg, workspace_id)
    if not _SAFE_SERVICE_NAME.fullmatch(service):
        raise ValueError("Service distant invalide")
    line_count = _bounded_value(
        lines,
        name="lines",
        minimum=1,
        maximum=MAX_REMOTE_LOG_LINES,
    )
    limit = _bounded_value(
        max_bytes,
        name="max_bytes",
        minimum=1,
        maximum=MAX_REMOTE_OUTPUT_BYTES,
    )
    command = (
        f"docker compose -f {_remote_quote(compose)} -p {_remote_quote(project)} "
        f"logs --no-color --tail {line_count} {_remote_quote(service)}"
    )
    result = ssh_run(cfg, command, timeout=timeout, check=False)
    if result.returncode != 0:
        detail = _safe_remote_error(result, "docker compose logs a échoué")
        raise SshError(f"Logs distants indisponibles : {detail}")
    return _bounded_text(_redact_text(result.stdout, max_chars=limit * 2), limit)


def read_remote_deploy_marker(
    cfg: ServerConfig,
    workspace_id: str,
    *,
    timeout: float = 30.0,
) -> dict[str, object]:
    """Read and validate the compatible ``last-deploy.json`` marker."""
    path = marker_path(cfg, workspace_id)
    raw = read_remote_file(
        cfg,
        path,
        max_bytes=16 * 1024,
        timeout=timeout,
        missing_ok=True,
        redact=False,
    )
    if not raw:
        return {}
    try:
        payload = json.loads(raw)
    except ValueError as exc:
        raise SshError("last-deploy.json contient du JSON invalide") from exc
    if not isinstance(payload, dict):
        raise SshError("last-deploy.json ne contient pas un objet JSON")
    return {str(key): _redact_value(value) for key, value in payload.items()}


def read_remote_deploy_history(
    cfg: ServerConfig,
    workspace_id: str,
    *,
    limit: int = 20,
    timeout: float = 30.0,
) -> tuple[dict[str, object], ...]:
    """Return the newest bounded entries from the JSONL deployment history."""
    _validate_workspace_id(workspace_id)
    entry_limit = _bounded_value(
        limit,
        name="limit",
        minimum=1,
        maximum=MAX_REMOTE_HISTORY_ENTRIES,
    )
    raw = read_remote_file(
        cfg,
        history_path(cfg, workspace_id),
        max_bytes=MAX_REMOTE_OUTPUT_BYTES,
        timeout=timeout,
        missing_ok=True,
        redact=False,
    )
    entries: list[dict[str, object]] = []
    for line in raw.splitlines():
        try:
            payload = json.loads(line)
        except ValueError:
            continue
        if isinstance(payload, dict):
            entries.append({str(key): _redact_value(value) for key, value in payload.items()})
    return tuple(entries[-entry_limit:])


def _derived_finished(status: str) -> bool | None:
    """Return whether *status* proves the execution is over, or ``None``.

    n8n 2.40's ``/rest/executions`` sends no ``finished`` flag (verified against
    a real 2.x instance), so the status is the only evidence. The vocabulary is
    n8n's own and is shared with the generated script through
    :mod:`n8n_launcher.remote.deploy`, so a remote server and the launcher can
    never disagree on what "terminé" means. ``waiting`` (paused) and
    ``unknown`` (possibly rewritten to ``crashed`` by recovery) stay pending,
    and a status we do not know yields ``None`` instead of a guess.
    """
    normalized = status.strip().lower()
    if normalized in TERMINAL_EXECUTION_STATUSES:
        return True
    if normalized in PENDING_EXECUTION_STATUSES:
        return False
    return None


def _execution_from_payload(payload: object) -> RemoteExecution | None:
    """Convert one whitelisted execution object into the public model."""
    if not isinstance(payload, dict):
        return None
    identifier = payload.get("id") or payload.get("executionId")
    status = payload.get("status")
    if not isinstance(identifier, (str, int)) or not isinstance(status, str):
        return None

    def optional_text(key: str) -> str | None:
        """Return a bounded optional text field from an execution payload."""
        value = payload.get(key)
        return _redact_text(value, max_chars=300) if isinstance(value, str) and value else None

    finished = payload.get("finished")
    return RemoteExecution(
        execution_id=_redact_text(identifier, max_chars=100),
        status=_redact_text(status, max_chars=100),
        workflow_name=optional_text("workflowName") or optional_text("workflow_name"),
        started_at=optional_text("startedAt") or optional_text("started_at"),
        stopped_at=optional_text("stoppedAt") or optional_text("stopped_at"),
        finished=finished if isinstance(finished, bool) else _derived_finished(status),
    )


def remote_execution_status(
    cfg: ServerConfig,
    workspace_id: str,
    *,
    limit: int = 20,
    timeout: float = 30.0,
) -> RemoteExecutionStatus:
    """Query read-only n8n execution status through the generated command.

    The capability check is performed in the same remote shell invocation as
    the status call. A server carrying an older generated script receives an
    explicit ``supported=False`` result and is never asked to deploy with the
    status argument.
    """
    _validate_workspace_id(workspace_id)
    entry_limit = _bounded_value(
        limit,
        name="limit",
        minimum=1,
        maximum=100,
    )
    base = resolve_base(cfg, workspace_id)
    script = deploy_script_path(cfg, workspace_id)
    capability = _remote_quote(REMOTE_STATUS_CAPABILITY)
    quoted_script = _remote_quote(script)
    quoted_base = _remote_quote(base)
    command = (
        f"if command -v grep >/dev/null 2>&1 && "
        f"grep -F -- {capability} {quoted_script} >/dev/null 2>&1; then "
        f"DEPLOY_BASE={quoted_base} DEPLOY_N8N_PORT={int(cfg.n8n_port)} "
        f"python3 {quoted_script} --status --limit {entry_limit}; "
        "else "
        'printf \'%s\\n\' \'{"supported":false,"error":"status distant non supporté"}\'; '
        "fi"
    )
    result = ssh_run(cfg, command, timeout=timeout, check=False)
    if result.returncode != 0:
        return RemoteExecutionStatus(
            supported=False,
            error=_safe_remote_error(result, "commande de statut distante refusée"),
        )
    try:
        payload = json.loads(result.stdout)
    except ValueError:
        return RemoteExecutionStatus(
            supported=False,
            error="la commande de statut distante a renvoyé des données invalides",
        )
    if not isinstance(payload, dict):
        return RemoteExecutionStatus(
            supported=False,
            error="la commande de statut distante a renvoyé un résultat invalide",
        )
    if payload.get("supported") is not True:
        error = payload.get("error")
        return RemoteExecutionStatus(
            supported=False,
            error=_redact_text(error, max_chars=_MAX_ERROR_CHARS)
            if isinstance(error, str)
            else "le statut des exécutions distantes est indisponible",
        )
    raw_executions = payload.get("executions")
    if not isinstance(raw_executions, list):
        return RemoteExecutionStatus(
            supported=False,
            error="la commande de statut distante n'a pas renvoyé d'exécutions",
        )
    executions = tuple(
        execution
        for item in raw_executions
        if (execution := _execution_from_payload(item)) is not None
    )
    return RemoteExecutionStatus(supported=True, executions=executions)


__all__ = [
    "MAX_REMOTE_HISTORY_ENTRIES",
    "MAX_REMOTE_LOG_LINES",
    "MAX_REMOTE_OUTPUT_BYTES",
    "RemoteExecution",
    "RemoteExecutionStatus",
    "RemoteHealth",
    "SshError",
    "chmod_remote",
    "mkdir_remote",
    "read_remote_deploy_history",
    "read_remote_deploy_marker",
    "read_remote_file",
    "remote_execution_status",
    "remote_health",
    "remote_logs",
    "ssh_run",
    "tail_remote_file",
    "test_connection",
    "write_remote_file",
]
