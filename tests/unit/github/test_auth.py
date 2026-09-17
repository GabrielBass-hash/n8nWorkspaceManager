"""Unit tests for GitHub token resolution (github/auth.py)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from n8n_launcher.github import auth


def _completed(returncode: int = 0, stdout: str = "") -> SimpleNamespace:
    """Minimal subprocess.CompletedProcess stand-in."""
    return SimpleNamespace(returncode=returncode, stdout=stdout)


# --- gh CLI ------------------------------------------------------------------


def test_token_from_gh_cli_returns_trimmed_token() -> None:
    with patch("n8n_launcher.github.auth.shutil.which", return_value="/usr/bin/gh"), patch(
        "n8n_launcher.github.auth.subprocess.run", return_value=_completed(stdout="ghp_x\n")
    ) as run:
        assert auth.token_from_gh_cli() == "ghp_x"

    assert run.call_args.args[0] == ["gh", "auth", "token"]


def test_token_from_gh_cli_absent_returns_none() -> None:
    with patch("n8n_launcher.github.auth.shutil.which", return_value=None):
        assert auth.token_from_gh_cli() is None


def test_token_from_gh_cli_failure_returns_none() -> None:
    with patch("n8n_launcher.github.auth.shutil.which", return_value="/usr/bin/gh"), patch(
        "n8n_launcher.github.auth.subprocess.run",
        return_value=_completed(returncode=1, stdout=""),
    ):
        assert auth.token_from_gh_cli() is None


def test_token_from_gh_cli_oserror_returns_none() -> None:
    with patch("n8n_launcher.github.auth.shutil.which", return_value="/usr/bin/gh"), patch(
        "n8n_launcher.github.auth.subprocess.run", side_effect=OSError("boom")
    ):
        assert auth.token_from_gh_cli() is None


# --- Git credential helper ---------------------------------------------------


def test_token_from_git_credential_parses_password() -> None:
    output = "protocol=https\nhost=github.com\nusername=octo\npassword=ghp_git\n\n"
    with patch(
        "n8n_launcher.github.auth.subprocess.run", return_value=_completed(stdout=output)
    ) as run:
        assert auth.token_from_git_credential() == "ghp_git"

    call = run.call_args
    assert call.args[0] == ["git", "credential", "fill"]
    assert call.kwargs["input"] == "protocol=https\nhost=github.com\n\n"
    assert call.kwargs["env"]["GIT_TERMINAL_PROMPT"] == "0"
    assert call.kwargs["env"]["GCM_INTERACTIVE"] == "never"


def test_token_from_git_credential_custom_host() -> None:
    with patch(
        "n8n_launcher.github.auth.subprocess.run",
        return_value=_completed(stdout="password=ghp_e\n"),
    ) as run:
        assert auth.token_from_git_credential("ghe.example.com") == "ghp_e"

    assert run.call_args.kwargs["input"] == "protocol=https\nhost=ghe.example.com\n\n"


def test_token_from_git_credential_without_password_returns_none() -> None:
    output = "protocol=https\nhost=github.com\nusername=octo\n\n"
    with patch(
        "n8n_launcher.github.auth.subprocess.run", return_value=_completed(stdout=output)
    ):
        assert auth.token_from_git_credential() is None


def test_token_from_git_credential_empty_password_returns_none() -> None:
    with patch(
        "n8n_launcher.github.auth.subprocess.run",
        return_value=_completed(stdout="password=   \n"),
    ):
        assert auth.token_from_git_credential() is None


def test_token_from_git_credential_failure_returns_none() -> None:
    with patch(
        "n8n_launcher.github.auth.subprocess.run",
        return_value=_completed(returncode=128),
    ):
        assert auth.token_from_git_credential() is None


def test_token_from_git_credential_oserror_returns_none() -> None:
    with patch(
        "n8n_launcher.github.auth.subprocess.run", side_effect=OSError("no git")
    ):
        assert auth.token_from_git_credential() is None


# --- resolve_github_token ----------------------------------------------------


def test_resolve_prefers_configured_override() -> None:
    with patch("n8n_launcher.github.auth.token_from_gh_cli") as gh, patch(
        "n8n_launcher.github.auth.token_from_git_credential"
    ) as credential:
        assert auth.resolve_github_token("  ghp_override  ") == "ghp_override"

    gh.assert_not_called()
    credential.assert_not_called()


def test_resolve_blank_override_falls_through_to_gh() -> None:
    with patch(
        "n8n_launcher.github.auth.token_from_gh_cli", return_value="ghp_gh"
    ), patch("n8n_launcher.github.auth.token_from_git_credential") as credential:
        assert auth.resolve_github_token("   ") == "ghp_gh"

    credential.assert_not_called()


def test_resolve_falls_back_to_git_credential() -> None:
    with patch(
        "n8n_launcher.github.auth.token_from_gh_cli", return_value=None
    ), patch(
        "n8n_launcher.github.auth.token_from_git_credential", return_value="ghp_git"
    ):
        assert auth.resolve_github_token() == "ghp_git"


def test_resolve_returns_none_when_every_source_is_empty() -> None:
    with patch(
        "n8n_launcher.github.auth.token_from_gh_cli", return_value=None
    ), patch("n8n_launcher.github.auth.token_from_git_credential", return_value=None):
        assert auth.resolve_github_token() is None
