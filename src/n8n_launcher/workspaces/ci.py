"""GitHub Actions CI harness for testing n8n pipelines (workspace-level).

This module owns everything the launcher needs to turn a workspace's own
git repository into a CI test-suite:

* mapping a remote URL to an ``owner/repo`` path (and its Actions URL),
* discovering candidate workflow exports,
* deciding which pipelines are eligible (runnable from a trigger) and why not,
* persisting the user's selection in ``.n8n-tests/tests.json`` — a
  machine-managed file that is never hand-edited,
* rendering the generated harness (workflow YAML, static validator,
  container runner) from the template documents in ``workspaces/templates/``.

The n8n instance is never assumed to be running for the CI feature itself:
eligibility works purely from the export files on disk plus the credential
*metadata* the launcher records locally (name/type, never values). Actual
credential values only travel once, from the running workspace's n8n into the
``N8N_CI_CREDENTIALS`` GitHub secret, pasted by the user.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from ..core.templates import read_template

# Files managed by the launcher inside the workspace repository. They are all
# generated (``GENERATED_MARKER`` header) and safe to delete on disable.
CI_DIR = ".n8n-tests"
SELECTION_FILE = "tests.json"
WORKFLOW_FILE = ".github/workflows/n8n-ci.yml"
VALIDATE_FILE = f"{CI_DIR}/validate.py"
RUNNER_FILE = f"{CI_DIR}/runner.py"

# Marker written at the top of every generated file.
GENERATED_MARKER = "n8n-launcher : généré — ne pas modifier à la main."
SECRET_NAME = "N8N_CI_CREDENTIALS"

# Files removed when CI is disabled; tests.json (the selection) is kept so it
# survives a disable/enable cycle.
DISABLE_FILES = (WORKFLOW_FILE, VALIDATE_FILE, RUNNER_FILE)

# n8n trigger types whose workflow can be started from the editor's run route.
MANUAL_TRIGGER_TYPES = {
    "n8n-nodes-base.manualTrigger",
    "@n8n/n8n-nodes-langchain.manualChatTrigger",
}
SCHEDULE_TRIGGER_TYPES = {"n8n-nodes-base.scheduleTrigger"}

# Root-level JSON files that are clearly not workflow exports (package.json
# and friends would only produce import noise in a CI run).
ROOT_BLOCKLIST = {
    "package.json",
    "package-lock.json",
    "tsconfig.json",
    "composer.json",
    "Pipfile",
    "Pipfile.lock",
    "deno.json",
    "deno.lock",
}

# Placeholder tokens substituted at render time.
_MARKER_TOKEN = "__MARKER__"
_IMAGE_TOKEN = "__N8N_IMAGE__"

# Remote URL shapes GitHub accepts; all normalize to ``owner/repo``.
_HTTPS_REPO = re.compile(r"^https?://github\.com/([^/]+/[^/]+?)(?:\.git)?/?$")
_SSH_REPO = re.compile(r"^git@github\.com:([^/]+/[^/]+?)(?:\.git)?/?$")
_GIT_SSH_REPO = re.compile(r"^ssh://git@github\.com/([^/]+/[^/]+?)(?:\.git)?/?$")
_IMAGE_TAG = re.compile(r"^[A-Za-z0-9._-]+$")


def github_repo_path(remote_url: str | None) -> str | None:
    """Extract ``owner/repo`` from a GitHub remote URL in any syntax.

    Returns ``None`` for empty URLs, GitLab URLs, local paths, or anything
    that is not a GitHub remote.
    """
    if not remote_url:
        return None
    candidate = remote_url.strip()
    for pattern in (_HTTPS_REPO, _SSH_REPO, _GIT_SSH_REPO):
        match = pattern.match(candidate)
        if match:
            return match.group(1)
    return None


def collect_workflows(workflows_dir: Path) -> list[str]:
    """Return POSIX relative paths of every workflow export in the workspace.

    Mirrors the launcher's own import behaviour: ``n8nPipelines/*.json`` plus
    root-level ``*.json`` files. JSON files that are manifest/lock files are
    excluded.

    Root-level exports that duplicate a pipeline already found under
    ``n8nPipelines/`` (same basename) are dropped — the mirror is only for
    freshness, the canonical copies are the ones exported to the pipeline
    folder.
    """
    found_paths: list[str] = []
    seen_basename: set[str] = set()
    pipelines_dir = workflows_dir / "n8nPipelines"
    if pipelines_dir.is_dir():
        for path in sorted(pipelines_dir.glob("*.json")):
            if path.is_file():
                rel = f"n8nPipelines/{path.name}"
                found_paths.append(rel)
                seen_basename.add(path.name)
    for path in sorted(workflows_dir.glob("*.json")):
        if path.is_file() and path.name not in ROOT_BLOCKLIST:
            if path.name in seen_basename:
                continue
            found_paths.append(path.name)
    return sorted(found_paths)


def load_export(workflows_dir: Path, rel: str) -> dict[str, Any] | None:
    """Load and sanity-check a workflow export; ``None`` when unreadable."""
    path = workflows_dir / rel
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or "nodes" not in data:
        return None
    return data


def selection_path(workflows_dir: Path) -> Path:
    """Return the path of the workspace's ``tests.json`` selection file."""
    return workflows_dir / CI_DIR / SELECTION_FILE


def read_selection(workflows_dir: Path) -> set[str]:
    """Return the persisted pipeline selection as a set of relative paths."""
    path = selection_path(workflows_dir)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    selected = data.get("selected", []) if isinstance(data, dict) else []
    return {str(item) for item in selected if isinstance(item, str)}


def write_selection(workflows_dir: Path, selected: set[str]) -> None:
    """Persist the pipeline selection (sorted, stable output)."""
    path = selection_path(workflows_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {"selected": sorted(selected)}
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _is_trigger_type(node_type: str) -> bool:
    """Return True when the n8n node type is a trigger (type ends with Trigger)."""
    return str(node_type).rsplit(".", 1)[-1].endswith("Trigger")


def missing_credentials(export: dict[str, Any], provided: set[str]) -> list[str]:
    """Return the display names of credentials absent from *provided*.

    Nodes carrying pinned data are excluded: a pinned node runs on its pinned
    input and does not need its credentials at execution time.
    """
    pin_data = export.get("pinData") or {}
    missing: set[str] = set()
    for node in export.get("nodes", []):
        if not isinstance(node, dict):
            continue
        if node.get("name") in pin_data:
            continue
        credentials = node.get("credentials")
        if not isinstance(credentials, dict):
            continue
        for credential in credentials.values():
            if not isinstance(credential, dict):
                continue
            name = str(credential.get("name") or "").strip()
            ctype = str(credential.get("type") or "").strip()
            if not name and not ctype:
                continue
            if f"{ctype}/{name}" not in provided:
                missing.add(f"{name} ({ctype})" if name else f"({ctype})")
    return sorted(missing)


def default_start_trigger(export: dict[str, Any]) -> str | None:
    """Return the trigger to start from, or ``None`` for destination-node runs.

    The first manual trigger wins; a pinned webhook/chat trigger is the
    fallback. Schedule-only workflows (and nothing pinning webhooks) return
    ``None`` — the runner then starts them through a destination node.
    """
    nodes = export.get("nodes") or []
    pin_data = export.get("pinData") or {}
    for node in nodes:
        if isinstance(node, dict) and node.get("type") in MANUAL_TRIGGER_TYPES:
            return node.get("name")
    for node in nodes:
        if (
            isinstance(node, dict)
            and _is_trigger_type(str(node.get("type", "")))
            and node.get("name") in pin_data
        ):
            return node.get("name")
    return None


def _credential_keys(credentials: list[dict[str, str]]) -> set[str]:
    """Build the ``type/name`` set from recorded ``(name, type)`` metadata."""
    keys: set[str] = set()
    for item in credentials:
        name = str(item.get("name") or "").strip()
        ctype = str(item.get("type") or "").strip()
        if name and ctype:
            keys.add(f"{ctype}/{name}")
    return keys


def workflow_eligibility(
    export: dict[str, Any], credentials: list[dict[str, str]]
) -> tuple[bool, str]:
    """Return (eligible, reason) for a pipeline.

    A pipeline is eligible for CI when it has a trigger n8n can start from
    without external input (manual, schedule, or a pinned webhook/chat
    trigger) *and* every non-pinned node's credentials are covered by
    *credentials* — the ``(name, type)`` metadata ``set_ci_credentials``
    persisted. Otherwise a French, user-facing reason explains the refusal.

    Takes the recorded metadata rather than a pre-built key set so the rule
    answers the question a caller actually has, and cannot be handed a set
    built from something other than what the workspace really has.
    """
    raw_nodes = export.get("nodes", [])
    nodes = (
        [node for node in raw_nodes if isinstance(node, dict)]
        if isinstance(raw_nodes, list)
        else []
    )
    triggers = [node for node in nodes if _is_trigger_type(str(node.get("type", "")))]
    if not triggers:
        return False, "aucun déclencheur"
    node_types = {str(node.get("type", "")) for node in triggers}
    runnable = bool(
        node_types & MANUAL_TRIGGER_TYPES
        or node_types & SCHEDULE_TRIGGER_TYPES
        or default_start_trigger(export) is not None
    )
    if not runnable:
        return False, "déclencheur webhook/chat non épinglé (épinglez-le dans l'éditeur)"
    missing = missing_credentials(export, _credential_keys(credentials))
    if missing:
        return False, f"credentials manquantes : {', '.join(missing)}"
    return True, ""


def _safe_image_tag(version: str) -> str:
    """Validate a workspace n8n version so it cannot leak into the Docker tag."""
    tag = str(version or "").strip()
    return tag if _IMAGE_TAG.fullmatch(tag) else "2.40.0"


def render_harness(n8n_version: str) -> dict[str, str]:
    """Return every generated file as ``relative_path -> content``.

    Calling this is idempotent; the caller decides whether existing files
    should keep their content (the selection in ``tests.json`` in particular
    must survive re-enabling CI).
    """
    image = f"docker.n8n.io/n8nio/n8n:{_safe_image_tag(n8n_version)}"
    return {
        WORKFLOW_FILE: _workflow_content(),
        VALIDATE_FILE: _validate_content(),
        RUNNER_FILE: _runner_content(image),
        f"{CI_DIR}/{SELECTION_FILE}": _empty_selection(),
    }


def _empty_selection() -> str:
    """Return the default (empty) selection file content."""
    return '{\n  "selected": []\n}\n'


def _workflow_content() -> str:
    """Render the GitHub Actions workflow definition."""
    return TEMPLATE_WORKFLOW.replace(_MARKER_TOKEN, GENERATED_MARKER)


def _validate_content() -> str:
    """Render the static validator script."""
    return TEMPLATE_VALIDATE.replace(_MARKER_TOKEN, GENERATED_MARKER)


def _runner_content(image: str) -> str:
    """Render the CI runner script with the pinned n8n image."""
    return TEMPLATE_RUNNER.replace(_MARKER_TOKEN, GENERATED_MARKER).replace(_IMAGE_TOKEN, image)


TEMPLATE_WORKFLOW = read_template("n8n_launcher.workspaces", "n8n-ci.yml.tmpl")


TEMPLATE_VALIDATE = read_template("n8n_launcher.workspaces", "validate.py.tmpl")


TEMPLATE_RUNNER = read_template("n8n_launcher.workspaces", "runner.py.tmpl")
