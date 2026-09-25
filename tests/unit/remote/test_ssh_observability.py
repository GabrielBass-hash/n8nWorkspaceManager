"""Tests for the bounded, redacted read-only observability helpers over SSH."""

import json
from subprocess import CompletedProcess
from unittest.mock import patch

import pytest

from n8n_launcher.core.models import ServerConfig
from n8n_launcher.remote.ssh import (
    MAX_REMOTE_HISTORY_ENTRIES,
    MAX_REMOTE_LOG_LINES,
    MAX_REMOTE_OUTPUT_BYTES,
    SshError,
    read_remote_deploy_history,
    read_remote_deploy_marker,
    read_remote_file,
    remote_execution_status,
    remote_health,
    remote_logs,
    tail_remote_file,
)

CFG = ServerConfig(
    enabled=True,
    host="prod.example.test",
    ssh_port=2222,
    user="deploy",
    key_path="/home/me/.ssh/id_ed25519",
    base_dir="n8n-launcher/abc123",
    n8n_port=5689,
)


def completed(returncode: int = 0, stdout: str = "", stderr: str = "") -> CompletedProcess:
    return CompletedProcess(["ssh"], returncode, stdout, stderr)


# -------------------------------------------------------------------------
# Bounded reads
# -------------------------------------------------------------------------


def test_read_remote_file_bounds_the_transfer() -> None:
    with patch("n8n_launcher.remote.ssh.subprocess.run", return_value=completed(0, "hello")) as run:
        assert read_remote_file(CFG, "server.log", max_bytes=1024) == "hello"

    command = run.call_args.args[0][-1]
    assert "head -c 1024" in command
    assert "server.log" in command


def test_read_remote_file_truncates_oversized_output() -> None:
    with patch(
        "n8n_launcher.remote.ssh.subprocess.run",
        return_value=completed(0, "x" * 10_000),
    ):
        assert read_remote_file(CFG, "server.log", max_bytes=64) == "x" * 64


def test_read_remote_file_returns_empty_when_missing_is_allowed() -> None:
    with patch("n8n_launcher.remote.ssh.subprocess.run", return_value=completed(44)):
        assert read_remote_file(CFG, "server.log", missing_ok=True) == ""


def test_read_remote_file_raises_when_missing() -> None:
    with (
        patch("n8n_launcher.remote.ssh.subprocess.run", return_value=completed(44)),
        pytest.raises(SshError, match="introuvable"),
    ):
        read_remote_file(CFG, "server.log")


def test_read_remote_file_raises_on_remote_failure() -> None:
    with (
        patch(
            "n8n_launcher.remote.ssh.subprocess.run",
            return_value=completed(1, "", "permission denied"),
        ),
        pytest.raises(SshError, match="permission denied"),
    ):
        read_remote_file(CFG, "server.log")


def test_read_remote_file_redacts_secrets_in_content() -> None:
    with patch(
        "n8n_launcher.remote.ssh.subprocess.run",
        return_value=completed(0, 'password="hunter2" token=abc'),
    ):
        content = read_remote_file(CFG, "server.log")

    assert "hunter2" not in content
    assert "[REDACTED]" in content


def test_read_remote_file_refuses_credential_bearing_paths() -> None:
    for path in ("base/secrets.json", "base/.env", "base/id_ed25519", "base/.env.production"):
        with pytest.raises(SshError, match="secrets"):
            read_remote_file(CFG, path)


def test_read_remote_file_can_return_raw_text_for_parsed_documents() -> None:
    with patch(
        "n8n_launcher.remote.ssh.subprocess.run",
        return_value=completed(0, '{"error": "owner_password=\\"hunter2\\""}'),
    ):
        raw = read_remote_file(CFG, "last-deploy.json", redact=False)

    # Raw transport keeps the document parseable; the caller redacts the
    # parsed structure, which pattern redaction on the raw text would break.
    assert json.loads(raw)["error"] == 'owner_password="hunter2"'


def test_read_remote_file_rejects_malformed_paths() -> None:
    with pytest.raises(ValueError, match="invalide"):
        read_remote_file(CFG, "")

    with pytest.raises(ValueError, match="invalide"):
        read_remote_file(CFG, "bad\x00path")


def test_read_remote_file_rejects_out_of_range_limits() -> None:
    with pytest.raises(ValueError, match="max_bytes"):
        read_remote_file(CFG, "server.log", max_bytes=0)

    with pytest.raises(ValueError, match="max_bytes"):
        read_remote_file(CFG, "server.log", max_bytes=MAX_REMOTE_OUTPUT_BYTES + 1)

    with pytest.raises(ValueError, match="entier"):
        read_remote_file(CFG, "server.log", max_bytes=True)  # type: ignore[arg-type]


def test_tail_remote_file_builds_a_bounded_tail() -> None:
    with patch("n8n_launcher.remote.ssh.subprocess.run", return_value=completed(0, "line")) as run:
        assert tail_remote_file(CFG, "server.log", lines=50, max_bytes=2048) == "line"

    command = run.call_args.args[0][-1]
    assert "tail -n 50" in command
    assert "head -c 2048" in command


def test_tail_remote_file_validates_limits_and_missing_flag() -> None:
    with pytest.raises(ValueError, match="lines"):
        tail_remote_file(CFG, "server.log", lines=MAX_REMOTE_LOG_LINES + 1)

    with patch("n8n_launcher.remote.ssh.subprocess.run", return_value=completed(44)):
        assert tail_remote_file(CFG, "server.log", missing_ok=True) == ""


# -------------------------------------------------------------------------
# Health
# -------------------------------------------------------------------------


def test_remote_health_reports_a_running_stack() -> None:
    payload = json.dumps(
        [
            {"Service": "n8n", "State": "running", "Health": "healthy"},
            {"Service": "postgres", "State": "running"},
        ]
    )
    with patch("n8n_launcher.remote.ssh.subprocess.run", return_value=completed(0, payload)):
        health = remote_health(CFG, "abc123")

    assert health.available is True
    assert health.healthy is True
    assert health.status == "healthy"
    assert health.services == {"n8n": "running", "postgres": "running"}
    assert health.error is None


def test_remote_health_flags_a_degraded_n8n_service() -> None:
    payload = json.dumps([{"Service": "n8n", "State": "exited", "Health": "unhealthy"}])
    with patch("n8n_launcher.remote.ssh.subprocess.run", return_value=completed(0, payload)):
        health = remote_health(CFG, "abc123")

    assert health.healthy is False
    assert health.status == "degraded"
    assert health.error is not None


def test_remote_health_parses_line_delimited_json() -> None:
    stdout = '{"Service": "n8n", "State": "running"}\nnot json\n'
    with patch("n8n_launcher.remote.ssh.subprocess.run", return_value=completed(0, stdout)):
        assert remote_health(CFG, "abc123").services == {"n8n": "running"}


def test_remote_health_reports_unavailable_without_raising() -> None:
    with patch(
        "n8n_launcher.remote.ssh.subprocess.run",
        return_value=completed(255, "", "docker: command not found"),
    ):
        health = remote_health(CFG, "abc123")

    assert health.available is False
    assert health.status == "unavailable"
    assert "docker: command not found" in (health.error or "")


def test_remote_health_uses_the_workspace_compose_project() -> None:
    with patch("n8n_launcher.remote.ssh.subprocess.run", return_value=completed(0, "[]")) as run:
        remote_health(CFG, "abc123")

    command = run.call_args.args[0][-1]
    assert "n8n-launcher/abc123/workflow/compose.yml" in command
    assert "n8n-ws-abc123" in command
    assert "ps --format json" in command


def test_remote_health_rejects_an_unsafe_workspace_id() -> None:
    with pytest.raises(ValueError, match="workspace"):
        remote_health(CFG, "../../etc")


# -------------------------------------------------------------------------
# Logs
# -------------------------------------------------------------------------


def test_remote_logs_returns_a_redacted_tail() -> None:
    with patch(
        "n8n_launcher.remote.ssh.subprocess.run",
        return_value=completed(0, "n8n started with password=hunter2"),
    ) as run:
        logs = remote_logs(CFG, "abc123", lines=25)

    command = run.call_args.args[0][-1]
    assert "logs --no-color --tail 25" in command
    assert command.endswith(" n8n")
    assert "hunter2" not in logs


def test_remote_logs_validates_service_and_limits() -> None:
    with pytest.raises(ValueError, match="Service"):
        remote_logs(CFG, "abc123", service="n8n; rm -rf /")

    with pytest.raises(ValueError, match="lines"):
        remote_logs(CFG, "abc123", lines=0)


def test_remote_logs_raises_on_remote_failure() -> None:
    with (
        patch(
            "n8n_launcher.remote.ssh.subprocess.run",
            return_value=completed(1, "", "no such service"),
        ),
        pytest.raises(SshError, match="no such service"),
    ):
        remote_logs(CFG, "abc123")


# -------------------------------------------------------------------------
# Deploy marker and history
# -------------------------------------------------------------------------


def test_read_remote_deploy_marker_returns_the_payload() -> None:
    payload = json.dumps({"sha": "abc123", "status": "ok", "at": 1})
    with patch("n8n_launcher.remote.ssh.subprocess.run", return_value=completed(0, payload)):
        marker = read_remote_deploy_marker(CFG, "abc123")

    assert marker["sha"] == "abc123"
    assert marker["status"] == "ok"


def test_read_remote_deploy_marker_returns_empty_when_absent() -> None:
    with patch("n8n_launcher.remote.ssh.subprocess.run", return_value=completed(44)):
        assert read_remote_deploy_marker(CFG, "abc123") == {}


def test_read_remote_deploy_marker_rejects_invalid_documents() -> None:
    with (
        patch("n8n_launcher.remote.ssh.subprocess.run", return_value=completed(0, "not json")),
        pytest.raises(SshError, match="JSON invalide"),
    ):
        read_remote_deploy_marker(CFG, "abc123")

    with (
        patch("n8n_launcher.remote.ssh.subprocess.run", return_value=completed(0, "[1, 2]")),
        pytest.raises(SshError, match="objet JSON"),
    ):
        read_remote_deploy_marker(CFG, "abc123")


def test_read_remote_deploy_marker_redacts_error_details() -> None:
    payload = json.dumps({"status": "error", "error": 'owner_password="hunter2"'})
    with patch("n8n_launcher.remote.ssh.subprocess.run", return_value=completed(0, payload)):
        marker = read_remote_deploy_marker(CFG, "abc123")

    assert "hunter2" not in str(marker["error"])


def test_read_remote_deploy_history_keeps_the_newest_entries() -> None:
    lines = [json.dumps({"sha": f"sha{index}", "status": "ok"}) for index in range(5)]
    with patch(
        "n8n_launcher.remote.ssh.subprocess.run", return_value=completed(0, "\n".join(lines))
    ):
        history = read_remote_deploy_history(CFG, "abc123", limit=2)

    assert [entry["sha"] for entry in history] == ["sha3", "sha4"]


def test_read_remote_deploy_history_skips_corrupted_lines() -> None:
    raw = "garbage\n" + json.dumps({"sha": "abc", "status": "ok"})
    with patch("n8n_launcher.remote.ssh.subprocess.run", return_value=completed(0, raw)):
        history = read_remote_deploy_history(CFG, "abc123")

    assert [entry["sha"] for entry in history] == ["abc"]


def test_read_remote_deploy_history_is_empty_when_absent() -> None:
    with patch("n8n_launcher.remote.ssh.subprocess.run", return_value=completed(44)):
        assert read_remote_deploy_history(CFG, "abc123") == ()


def test_read_remote_deploy_history_validates_limit() -> None:
    with pytest.raises(ValueError, match="limit"):
        read_remote_deploy_history(CFG, "abc123", limit=MAX_REMOTE_HISTORY_ENTRIES + 1)


# -------------------------------------------------------------------------
# Execution status
# -------------------------------------------------------------------------


def test_remote_execution_status_returns_supported_executions() -> None:
    payload = json.dumps(
        {
            "supported": True,
            "executions": [
                {
                    "id": "9001",
                    "status": "success",
                    "workflowName": "Import leads",
                    "startedAt": "2026-09-25T10:00:00Z",
                    "finished": True,
                },
                {"id": "9002", "status": "error", "workflowData": {"name": "Sync"}},
                {"broken": True},
            ],
        }
    )
    with patch("n8n_launcher.remote.ssh.subprocess.run", return_value=completed(0, payload)) as run:
        status = remote_execution_status(CFG, "abc123", limit=5)

    command = run.call_args.args[0][-1]
    assert "grep -F" in command
    assert "n8n-launcher/abc123/deploy.py" in command
    assert "--status --limit 5" in command
    assert status.supported is True
    assert len(status.executions) == 2
    assert status.executions[0].id == "9001"
    assert status.executions[0].workflow_name == "Import leads"
    assert status.executions[0].finished is True
    # No ``finished`` flag in the payload (n8n 2.40 reality): the status decides.
    assert status.executions[1].status == "error"
    assert status.executions[1].finished is True


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ("success", True),
        ("error", True),
        ("crashed", True),
        ("canceled", True),
        ("SUCCESS", True),
        ("running", False),
        ("new", False),
        ("waiting", False),
        ("unknown", False),
        ("brand-new-status", None),
    ],
)
def test_remote_execution_status_derives_finished_from_the_status(
    status: str, expected: bool | None
) -> None:
    """The flag is derived from n8n's own execution vocabulary when absent.

    ``waiting`` (paused) and ``unknown`` (which recovery may still rewrite to
    ``crashed``) are pending, and a status we do not know stays ``None`` rather
    than being guessed either way.
    """
    payload = json.dumps({"supported": True, "executions": [{"id": "1", "status": status}]})
    with patch("n8n_launcher.remote.ssh.subprocess.run", return_value=completed(0, payload)):
        status_result = remote_execution_status(CFG, "abc123")

    assert status_result.executions[0].finished is expected


def test_remote_execution_status_keeps_an_explicit_finished_flag() -> None:
    payload = json.dumps(
        {
            "supported": True,
            "executions": [
                {"id": "1", "status": "unknown", "finished": False},
                {"id": "2", "status": "success", "finished": False},
            ],
        }
    )
    with patch("n8n_launcher.remote.ssh.subprocess.run", return_value=completed(0, payload)):
        result = remote_execution_status(CFG, "abc123")

    # The server's explicit answer always wins over the derivation.
    assert [item.finished for item in result.executions] == [False, False]


def test_remote_execution_status_reports_an_older_server_as_unsupported() -> None:
    payload = json.dumps({"supported": False, "error": "status distant non supporté"})
    with patch("n8n_launcher.remote.ssh.subprocess.run", return_value=completed(0, payload)):
        status = remote_execution_status(CFG, "abc123")

    assert status.supported is False
    assert status.executions == ()
    assert "non supporté" in (status.error or "")


def test_remote_execution_status_handles_ssh_and_payload_failures() -> None:
    with patch(
        "n8n_launcher.remote.ssh.subprocess.run",
        return_value=completed(255, "", "connection closed"),
    ):
        assert remote_execution_status(CFG, "abc123").supported is False

    with patch("n8n_launcher.remote.ssh.subprocess.run", return_value=completed(0, "not json")):
        assert "invalides" in (remote_execution_status(CFG, "abc123").error or "")

    with patch("n8n_launcher.remote.ssh.subprocess.run", return_value=completed(0, "[1]")):
        assert "résultat invalide" in (remote_execution_status(CFG, "abc123").error or "")

    with patch(
        "n8n_launcher.remote.ssh.subprocess.run",
        return_value=completed(0, json.dumps({"supported": True})),
    ):
        assert "d'exécutions" in (remote_execution_status(CFG, "abc123").error or "")

    with patch(
        "n8n_launcher.remote.ssh.subprocess.run",
        return_value=completed(0, json.dumps({"supported": True, "executions": []})),
    ):
        assert remote_execution_status(CFG, "abc123").supported is True


def test_remote_execution_status_validates_limit_and_workspace_id() -> None:
    with pytest.raises(ValueError, match="limit"):
        remote_execution_status(CFG, "abc123", limit=0)

    with pytest.raises(ValueError, match="workspace"):
        remote_execution_status(CFG, "abc/../etc")
