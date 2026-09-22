"""Resolve a GitHub token without asking the user to configure one by hand.

The launcher needs a token for the two things the Git protocol cannot do
itself: reading Actions runs and creating repositories through the REST API.
Rather than requesting a personal access token, it reuses the credential Git
already stores for ``github.com`` (macOS Keychain, Windows Credential
Manager, libsecret, ...) — the very same username/password pair the OS Git
credential helper hands back for a normal ``git push``. The ``gh`` CLI and an
explicit, optional override (persisted once in the launcher config) are only
fallbacks for when no Git credential is available.

Nothing here logs or persists a token; callers keep the result in memory.
"""

from __future__ import annotations

import os
import shutil
import subprocess

# Bound every helper call: a credential helper must never freeze the launcher.
_CREDENTIAL_TIMEOUT = 10.0


def token_from_gh_cli() -> str | None:
    """Return a GitHub token via ``gh auth token``, or ``None``."""
    if shutil.which("gh") is None:
        return None
    try:
        result = subprocess.run(
            ["gh", "auth", "token"],
            capture_output=True,
            text=True,
            timeout=_CREDENTIAL_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    token = result.stdout.strip()
    return token if result.returncode == 0 and token else None


def token_from_git_credential(host: str = "github.com") -> str | None:
    """Return the password Git stores for *host* (a GitHub token), or ``None``.

    ``git credential fill`` reads the configured credential helper, so this
    yields exactly what a ``git push`` over HTTPS would use. Interactive
    prompts are disabled (``GIT_TERMINAL_PROMPT=0``) and the Git Credential
    Manager is told not to show a GUI (``GCM_INTERACTIVE=never``): a missing
    entry fails fast instead of blocking the launcher on a dialog.
    """
    request = f"protocol=https\nhost={host}\n\n"
    environment = {
        **os.environ,
        "GIT_TERMINAL_PROMPT": "0",
        "GCM_INTERACTIVE": "never",
    }
    try:
        result = subprocess.run(
            ["git", "credential", "fill"],
            input=request,
            capture_output=True,
            text=True,
            timeout=_CREDENTIAL_TIMEOUT,
            env=environment,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    for line in result.stdout.splitlines():
        if line.startswith("password="):
            token = line[len("password=") :].strip()
            return token or None
    return None


def resolve_github_token(configured: str | None = None) -> str | None:
    """Resolve a usable GitHub token from the first available source.

    Order: the explicit *configured* override, the ``gh`` CLI, then the OS Git
    credential helper. Returns ``None`` when every source comes up empty.
    """
    if configured and configured.strip():
        return configured.strip()
    return token_from_gh_cli() or token_from_git_credential()
