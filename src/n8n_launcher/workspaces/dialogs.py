"""What a workspace-creation or Git/GitHub form collects, decided before anything is drawn.

Every modal in the launcher is a *form around a decision*: which DB, whether
git is wired and to where, whether the remote is created on GitHub, which token
to use. Those decisions are domain facts about a workspace, so they are plain
dataclasses here, decoupled from the widget that collects them. A view builds a
form, reads one of these back, and the rest of the launcher never learns which
toolkit was on screen.

The helpers are the parts of those forms that were never really about widgets
either: which DB a new workspace should default to, how a workspace name
becomes a GitHub repository name, how a port field is parsed.
"""

from __future__ import annotations

import re
import secrets
from dataclasses import dataclass
from pathlib import Path

from ..core.models import DbConfig, DbMode
from ..database import has_db_layout


@dataclass
class CreatePlan:
    """Chosen options for creating a new workspace (single-dialog workflow)."""

    name: str
    db: DbConfig
    git_enabled: bool = False
    git_url: str | None = None
    github_create: bool = False


@dataclass(frozen=True)
class GitConfigChoice:
    """Outcome of the git configuration dialog."""

    create_github: bool = False
    remote_url: str | None = None


@dataclass(frozen=True)
class GitHubTokenPlan:
    """Token entered for the GitHub API, plus whether to persist it once."""

    token: str
    remember: bool = False


@dataclass(frozen=True)
class GitHubCreatePlan:
    """Choices for creating a new repository on GitHub."""

    name: str
    private: bool = True
    token: str = ""


@dataclass(frozen=True)
class GitClonePlan:
    """URL et branche optionnelle pour un clonage."""

    url: str
    branch: str | None = None


@dataclass(frozen=True)
class GitHubRepoPick:
    """Repository selected from the linked account, plus optional branch."""

    clone_url: str
    branch: str | None = None


def fresh_managed_db_config() -> DbConfig:
    """Return a MANAGED DB config with a freshly generated password."""
    return DbConfig(
        mode=DbMode.MANAGED,
        database_name="data",
        username="n8ndata",
        password=secrets.token_hex(16),
    )


def default_creation_db(workflows_dir: Path) -> DbConfig:
    """Pick the creation-dialog DB default: managed when a DB layout exists.

    A folder that already holds a schema (or a migrations folder) is a workspace
    that has been running against a database, so its creation form offers to
    bring one up; anything else starts DB-less rather than silently provisioning
    a Postgres the user did not ask for.
    """
    if has_db_layout(workflows_dir):
        return fresh_managed_db_config()
    return DbConfig(DbMode.NONE)


def int_or(value: str, fallback: int) -> int:
    """Parse a config port from a dialog field, falling back for junk input."""
    try:
        return int(value.strip())
    except ValueError:
        return fallback


def repo_name_from(workspace_name: str) -> str:
    """Derive a GitHub-usable repository name from a workspace name.

    GitHub accepts letters, digits, ``.``, ``-`` and ``_`` only, so everything
    else becomes a dash. A name that sanitises down to nothing at all (a
    workspace called ``"..."``) falls back to ``workspace`` rather than
    producing an empty repository name.
    """
    lowered = workspace_name.lower().strip()
    cleaned = re.sub(r"[^a-z0-9_.-]+", "-", lowered)
    return cleaned.strip(".-_ ") or "workspace"


__all__ = [
    "CreatePlan",
    "GitClonePlan",
    "GitConfigChoice",
    "GitHubCreatePlan",
    "GitHubRepoPick",
    "GitHubTokenPlan",
    "default_creation_db",
    "fresh_managed_db_config",
    "int_or",
    "repo_name_from",
]
