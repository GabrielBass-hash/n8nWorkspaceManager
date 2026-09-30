"""Tests for the creation/Git/GitHub plans and helpers (:mod:`n8n_launcher.workspaces.dialogs`).

These are the answers a form collects, not the widgets that collect them, so
none of these need a display: what is asserted is which DB a new workspace
defaults to, how a workspace name becomes a repository name, and that a plan
carries exactly the fields its caller reads back.
"""

from __future__ import annotations

from unittest.mock import patch

from n8n_launcher.core.models import DbConfig, DbMode
from n8n_launcher.workspaces.dialogs import (
    CreatePlan,
    GitClonePlan,
    GitConfigChoice,
    GitHubCreatePlan,
    GitHubRepoPick,
    GitHubTokenPlan,
    default_creation_db,
    fresh_managed_db_config,
    int_or,
    repo_name_from,
)


def test_a_fresh_managed_db_carries_a_generated_password() -> None:
    with patch("n8n_launcher.workspaces.dialogs.secrets.token_hex", return_value="a" * 32):
        db = fresh_managed_db_config()

    assert db == DbConfig(
        DbMode.MANAGED,
        database_name="data",
        username="n8ndata",
        password="a" * 32,
    )


def test_two_fresh_managed_dbs_do_not_share_a_password() -> None:
    # The password is the workspace's own DB credential; sharing one across
    # workspaces would put two stacks behind the same secret.
    assert fresh_managed_db_config().password != fresh_managed_db_config().password


def test_an_existing_migrations_folder_defaults_to_a_managed_db(tmp_path) -> None:
    folder = tmp_path / "wf"
    (folder / "db" / "migrations").mkdir(parents=True)
    (folder / "db" / "migrations" / "001.sql").write_text("select 1;")

    db = default_creation_db(folder)
    assert db.mode is DbMode.MANAGED
    assert db.password


def test_a_folder_without_migrations_defaults_to_no_db(tmp_path) -> None:
    folder = tmp_path / "wf-nomig"
    folder.mkdir()

    assert default_creation_db(folder).mode is DbMode.NONE


def test_a_port_field_is_parsed_with_its_fallback() -> None:
    assert int_or("2222", 22) == 2222
    assert int_or("junk", 22) == 22
    assert int_or(" 5678 ", 22) == 5678


def test_a_workspace_name_becomes_a_usable_repository_name() -> None:
    assert repo_name_from("Mon Workspace!") == "mon-workspace"
    assert repo_name_from("  BAZ_2.0  ") == "baz_2.0"
    # A name that sanitises down to nothing still yields a name GitHub accepts.
    assert repo_name_from("!!!") == "workspace"


def test_a_create_plan_defaults_to_a_local_workspace_without_git() -> None:
    plan = CreatePlan(name="Demo", db=DbConfig(DbMode.NONE))
    assert (plan.name, plan.db.mode) == ("Demo", DbMode.NONE)
    assert plan.git_enabled is False
    assert plan.git_url is None
    assert plan.github_create is False


def test_the_choices_each_form_returns_are_distinct() -> None:
    # Two forms can both mean "no remote" and must not collapse into one type:
    # the git form may create the repo, the token form carries a token.
    assert GitConfigChoice() == GitConfigChoice(create_github=False, remote_url=None)
    assert GitConfigChoice(create_github=True).remote_url is None
    assert GitHubTokenPlan(token="abc").remember is False
    assert GitClonePlan(url="https://example.test/r.git").branch is None
    assert GitHubRepoPick(clone_url="https://example.test/r.git").branch is None
    assert GitHubCreatePlan(name="r").private is True
