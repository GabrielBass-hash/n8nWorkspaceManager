"""SSH client for remote server deployment.

Wraps the system ``ssh`` binary (available on Windows 10+, macOS and Linux) in
batch mode: a missing key or unknown host fails fast instead of prompting.
Passphrases are intentionally not handled here — the user's SSH agent covers
them.
"""

from __future__ import annotations

import logging
import shlex
import subprocess

from ..core.models import ServerConfig

logger = logging.getLogger(__name__)


class SshError(RuntimeError):
    """Raised when a remote SSH command fails."""


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
        # Only the configured key: OpenSSH may burn its MaxAuthTries on agent
        # keys first, failing the whole connection before ``-i`` is offered.
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
    set. ``stdin`` lets a caller pipe data (e.g. ``tar`` archives).
    """
    argv = _ssh_argv(cfg, command)
    try:
        result = subprocess.run(
            argv,
            input=stdin,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError:
        raise SshError("ssh n'est pas installé ou introuvable dans le PATH") from None
    except subprocess.TimeoutExpired as exc:
        raise SshError(f"Connexion SSH à {cfg.host} expirée (>{timeout:.0f}s)") from exc
    except OSError as exc:
        raise SshError(f"ssh n'a pas pu être exécuté : {exc}") from exc
    if check and result.returncode != 0:
        detail = result.stderr.strip() or f"code {result.returncode}"
        raise SshError(f"Commande SSH sur {cfg.host} échouée : {detail}")
    return result


def test_connection(cfg: ServerConfig) -> str:
    """Probe the server prerequisites needed by the deployment listener.

    Checks that ``docker`` (with the Compose plugin), ``git`` and ``python3``
    are available, returning their combined version output. Raises
    :class:`SshError` when a prerequisite is missing or the connection fails.
    """

    def fail(tool: str) -> str:
        # command -v <tool> || { echo '<tool> introuvable'; exit 1; }
        return (
            f"command -v {tool} >/dev/null 2>&1 || "
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
    logger.info("Server prerequisites on %s: %s", cfg.host, result.stdout.strip())
    return result.stdout


# The ``test_`` prefix is only for the probes' sake; this is an application
# function, not a pytest test.
test_connection.__test__ = False  # type: ignore[attr-defined]


def _remote_quote(path: str) -> str:
    """Quote a remote path for use in the remote shell."""
    return shlex.quote(path)


def mkdir_remote(cfg: ServerConfig, path: str, *, parents: bool = True) -> None:
    """Create *path* on the server (``mkdir -p``)."""
    args = "-p" if parents else ""
    ssh_run(cfg, f"mkdir {args} {_remote_quote(path)}")


def write_remote_file(cfg: ServerConfig, path: str, content: str) -> None:
    """Write *content* verbatim to *path* on the server.

    The file is streamed through the ssh process stdin, so it accepts
    arbitrary bytes (generated scripts, JSON) without shell mangling. The
    write is atomic: the bytes land in ``<path>.tmp`` first, then ``mv``
    replaces the final file, so a reader (e.g. ``_poll_deploy`` tailing
    ``last-deploy.json``) never observes a half-written document.
    """
    quoted = _remote_quote(path)
    ssh_run(cfg, f"cat > {quoted}.tmp && mv -f {quoted}.tmp {quoted}", stdin=content, timeout=60.0)


def chmod_remote(cfg: ServerConfig, path: str, mode: str = "+x") -> None:
    """Change file modes on the server (default: make executable)."""
    ssh_run(cfg, f"chmod {mode} {_remote_quote(path)}")
