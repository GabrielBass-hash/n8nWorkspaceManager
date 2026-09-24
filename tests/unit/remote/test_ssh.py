from subprocess import CompletedProcess, TimeoutExpired
from unittest.mock import patch

import pytest

from n8n_launcher.core.models import ServerConfig
from n8n_launcher.remote.ssh import (
    SshError,
    chmod_remote,
    mkdir_remote,
    ssh_run,
    test_connection,
    write_remote_file,
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


def completed(returncode: int = 0, stdout: str = "", stderr: str = ""):
    return CompletedProcess(["ssh"], returncode, stdout, stderr)


def test_ssh_run_builds_batch_ssh_command(tmp_path) -> None:
    with patch("n8n_launcher.remote.ssh.subprocess.run", return_value=completed()) as run:
        ssh_run(CFG, "echo hello")

    argv = run.call_args.args[0]
    assert argv[0] == "ssh"
    assert "-p" in argv and "2222" in argv
    assert "-i" in argv and "/home/me/.ssh/id_ed25519" in argv
    assert "-o" in argv and "BatchMode=yes" in argv
    assert "StrictHostKeyChecking=accept-new" in argv
    assert "IdentitiesOnly=yes" in argv
    assert "deploy@prod.example.test" in argv
    assert argv[-1] == "echo hello"
    assert run.call_args.kwargs["timeout"] == 30


def test_ssh_run_forwards_stdin(tmp_path) -> None:
    with patch("n8n_launcher.remote.ssh.subprocess.run", return_value=completed()) as run:
        ssh_run(CFG, "tar xzf -", stdin="payload")

    assert run.call_args.kwargs["input"] == "payload"


def test_ssh_run_returns_stdout() -> None:
    with patch(
        "n8n_launcher.remote.ssh.subprocess.run",
        return_value=completed(0, "Docker version 27\n"),
    ):
        result = ssh_run(CFG, "docker --version")

    assert result.stdout == "Docker version 27\n"


def test_ssh_run_raises_with_stderr_on_failure() -> None:
    with (
        patch(
            "n8n_launcher.remote.ssh.subprocess.run",
            return_value=completed(255, "", "Permission denied (publickey)"),
        ),
        pytest.raises(SshError) as excinfo,
    ):
        ssh_run(CFG, "true")

    assert "Permission denied" in str(excinfo.value)


def test_ssh_run_check_false_does_not_raise() -> None:
    with patch(
        "n8n_launcher.remote.ssh.subprocess.run",
        return_value=completed(255, "", "auth failed"),
    ):
        result = ssh_run(CFG, "true", check=False)

    assert result.returncode == 255


def test_ssh_run_raises_when_ssh_missing() -> None:
    with (
        patch("n8n_launcher.remote.ssh.subprocess.run", side_effect=FileNotFoundError()),
        pytest.raises(SshError, match="ssh"),
    ):
        ssh_run(CFG, "true")


def test_ssh_run_raises_on_timeout() -> None:
    with (
        patch(
            "n8n_launcher.remote.ssh.subprocess.run",
            side_effect=TimeoutExpired("ssh", 30),
        ),
        pytest.raises(SshError, match="expir"),
    ):
        ssh_run(CFG, "true")


def test_test_connection_probes_docker_git_python() -> None:
    with patch(
        "n8n_launcher.remote.ssh.subprocess.run",
        return_value=completed(0, "Docker version 27\ngit version 2.43\nPython 3.12\n"),
    ) as run:
        output = test_connection(CFG)

    probing = run.call_args.args[0][-1]
    assert "docker compose version" in probing
    assert "git --version" in probing
    assert "python3 --version" in probing
    assert "Docker version" in output


def test_test_connection_raises_when_docker_missing() -> None:
    with (
        patch(
            "n8n_launcher.remote.ssh.subprocess.run",
            return_value=completed(1, "", "docker introuvable"),
        ),
        pytest.raises(SshError, match="docker introuvable"),
    ):
        test_connection(CFG)


def test_mkdir_remote_builds_command() -> None:
    with patch("n8n_launcher.remote.ssh.subprocess.run", return_value=completed()) as run:
        mkdir_remote(CFG, "n8n launcher/abc123")

    assert run.call_args.args[0][-1] == "mkdir -p 'n8n launcher/abc123'"


def test_write_remote_file_streams_content_via_stdin_and_moves_atomically() -> None:
    with patch("n8n_launcher.remote.ssh.subprocess.run", return_value=completed()) as run:
        write_remote_file(CFG, "dir with spaces/deploy.py", "#!/usr/bin/env python3\nprint(1)\n")

    command = run.call_args.args[0][-1]
    assert command.startswith("cat > 'dir with spaces/deploy.py'.tmp && mv -f")
    assert "deploy.py'.tmp 'dir with spaces/deploy.py'" in command
    assert run.call_args.kwargs["input"] == "#!/usr/bin/env python3\nprint(1)\n"


def test_chmod_remote_accepts_mode() -> None:
    with patch("n8n_launcher.remote.ssh.subprocess.run", return_value=completed()) as run:
        chmod_remote(CFG, "dir with spaces/secrets.json", mode="600")

    assert run.call_args.args[0][-1] == "chmod 600 'dir with spaces/secrets.json'"
