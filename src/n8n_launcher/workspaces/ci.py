"""GitHub Actions CI harness for testing n8n pipelines (workspace-level).

This module owns everything the launcher needs to turn a workspace's own
git repository into a CI test-suite:

* mapping a remote URL to an ``owner/repo`` path (and its Actions URL),
* discovering candidate workflow exports,
* deciding which pipelines are eligible (runnable from a trigger) and why not,
* persisting the user's selection in ``.n8n-tests/tests.json`` — a
  machine-managed file that is never hand-edited,
* rendering the generated harness (workflow YAML, static validator,
  container runner) as marker-comment templates.

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


def actions_url(repo_path: str) -> str:
    """Return the GitHub Actions page URL for an ``owner/repo`` path."""
    return f"https://github.com/{repo_path}/actions"


def collect_workflows(workflows_dir: Path) -> list[str]:
    """Return POSIX relative paths of every workflow export in the workspace.

    Mirrors the launcher's own import behaviour: ``n8nPipelines/*.json`` plus
    root-level ``*.json`` files. JSON files that are manifest/lock files are
    excluded.
    """
    found: list[str] = []
    pipelines_dir = workflows_dir / "n8nPipelines"
    if pipelines_dir.is_dir():
        for path in sorted(pipelines_dir.glob("*.json")):
            if path.is_file():
                found.append(f"n8nPipelines/{path.name}")
    for path in sorted(workflows_dir.glob("*.json")):
        if path.is_file() and path.name not in ROOT_BLOCKLIST:
            found.append(path.name)
    return sorted(set(found))


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


def provided_credentials(credentials: list[dict[str, str]]) -> set[str]:
    """Build the set of ``type/name`` keys from locally recorded metadata."""
    keys: set[str] = set()
    for item in credentials:
        name = str(item.get("name") or "").strip()
        ctype = str(item.get("type") or "").strip()
        if name and ctype:
            keys.add(f"{ctype}/{name}")
    return keys


def _is_trigger_type(node_type: str) -> bool:
    """Return True when the n8n node type is a trigger (type ends with Trigger)."""
    return str(node_type).rsplit(".", 1)[-1].endswith("Trigger")


def missing_credentials(
    export: dict[str, Any], provided: set[str]
) -> list[str]:
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


def workflow_eligibility(
    export: dict[str, Any], provided: set[str]
) -> tuple[bool, str]:
    """Return (eligible, reason) for a pipeline.

    A pipeline is eligible for CI when it has a trigger n8n can start from
    without external input (manual, schedule, or a pinned webhook/chat
    trigger) *and* every non-pinned node's credentials are covered by the
    locally recorded credential set. Otherwise a French, user-facing reason
    explains why the pipeline is greyed out.
    """
    raw_nodes = export.get("nodes", [])
    nodes = [node for node in raw_nodes if isinstance(node, dict)] if isinstance(raw_nodes, list) else []
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
    missing = missing_credentials(export, provided)
    if missing:
        return False, f"credentials manquantes : {', '.join(missing)}"
    return True, ""


def start_description(export: dict[str, Any]) -> str:
    """Return a short French description of how the pipeline will be started."""
    nodes = export.get("nodes") or []
    pin_data = export.get("pinData") or {}
    for node in nodes:
        if isinstance(node, dict) and node.get("type") in MANUAL_TRIGGER_TYPES:
            return f"déclencheur manuel « {node.get('name')} »"
    for node in nodes:
        if isinstance(node, dict) and node.get("type") in SCHEDULE_TRIGGER_TYPES:
            return "déclencheur programmé"
    for node in nodes:
        if (
            isinstance(node, dict)
            and _is_trigger_type(str(node.get("type", "")))
            and node.get("name") in pin_data
        ):
            return f"déclencheur épinglé « {node.get('name')} »"
    return "aucun déclencheur testable"


def node_detail(export: dict[str, Any], node: dict[str, Any]) -> tuple[str, str]:
    """Return (subtitle, style) describing a node row in the CI dialog.

    The subtitle reports the node type plus a pinned badge; style is used by
    the dialog to tint missing-credential or pinned rows.
    """
    parts = [str(node.get("type", ""))]
    pin_data = export.get("pinData") or {}
    if node.get("name") in pin_data:
        parts.append("épinglé")
    return ", ".join(parts), ("muted" if parts[-1] == "épinglé" else "warn")


def ci_counts(workflows_dir: Path, provided: set[str]) -> dict[str, int]:
    """Return selection counters for chips and dialogs.

    ``eligible`` counts pipelines that can be run, ``selected`` the number of
    lines in tests.json, and ``selected_eligible`` those that are both.
    """
    selected = read_selection(workflows_dir)
    eligible = 0
    selected_eligible = 0
    for rel in collect_workflows(workflows_dir):
        export = load_export(workflows_dir, rel)
        if export is None:
            continue
        ok, _ = workflow_eligibility(export, provided)
        if ok:
            eligible += 1
            if rel in selected:
                selected_eligible += 1
    return {"eligible": eligible, "selected": len(selected), "selected_eligible": selected_eligible}


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
    return (
        TEMPLATE_RUNNER.replace(_MARKER_TOKEN, GENERATED_MARKER)
        .replace(_IMAGE_TOKEN, image)
    )  # noqa: E501


TEMPLATE_WORKFLOW = """# __MARKER__
# GitHub Actions CI pour ce workspace n8n : exécute les pipelines sélectionnées
# dans un conteneur n8n isolé et jetable (voir .n8n-tests/runner.py).
name: n8n-launcher CI

on:
  push:
    branches: ["main", "master"]
  pull_request:
  workflow_dispatch:

concurrency:
  group: "ci-${{ github.ref }}"
  cancel-in-progress: true

permissions:
  contents: read

jobs:
  validate:
    name: Valider les exports
    runs-on: ubuntu-latest
    timeout-minutes: 5
    steps:
      - uses: actions/checkout@v4
      - name: Validation statique des exports
        run: python .n8n-tests/validate.py

  test:
    name: Exécuter les pipelines
    needs: validate
    runs-on: ubuntu-latest
    timeout-minutes: 20
    steps:
      - uses: actions/checkout@v4
      - name: Compter les pipelines sélectionnées
        id: selection
        run: |
          count=$(python -c "import json;print(len(json.load(open('.n8n-tests/tests.json', encoding='utf-8'))['selected']))" 2>/dev/null || echo 0)
          echo "count=$count" >> "$GITHUB_OUTPUT"
      - name: Lancer le runner de tests n8n
        # Une sélection vide (aucun test coché) ne lance rien : le job passe
        # « vert » sans démarrer de conteneur, au lieu d'exécuter l'ancienne
        # sélection encore présente sur GitHub.
        if: steps.selection.outputs.count != '0'
        env:
          N8N_IMAGE: ${{ vars.N8N_IMAGE }}
          N8N_CI_CREDENTIALS: ${{ secrets.N8N_CI_CREDENTIALS }}
        run: python .n8n-tests/runner.py
"""


TEMPLATE_VALIDATE = """# __MARKER__
# Validation statique des exports de workflows et de la sélection de tests.
# Reste volontairement en bibliothèque standard, sans Docker.
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SELECTION_FILE = ROOT / ".n8n-tests" / "tests.json"

# Fichiers de manifest manifestement non-workflow (miroir du launcher).
BLOCKLIST_ROOT = {
    "package.json", "package-lock.json", "tsconfig.json", "composer.json",
    "Pipfile", "Pipfile.lock", "deno.json", "deno.lock",
}


def collect_workflows():
    found = []
    pipelines = ROOT / "n8nPipelines"
    if pipelines.is_dir():
        for path in sorted(pipelines.glob("*.json")):
            if path.is_file():
                found.append(f"n8nPipelines/{path.name}")
    for path in sorted(ROOT.glob("*.json")):
        if path.is_file() and path.name not in BLOCKLIST_ROOT:
            found.append(path.name)
    return sorted(set(found))


def main():
    errors = []
    seen_names = set()
    exports = {}
    for rel in collect_workflows():
        path = ROOT / rel
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            errors.append(f"{rel} : fichier JSON illisible ({exc})")
            continue
        if not isinstance(data, dict) or not isinstance(data.get("nodes"), list):
            errors.append(f"{rel} : export invalide (\\"nodes\\" manquant)")
            continue
        name = str(data.get("name") or "").strip()
        if not name:
            errors.append(f"{rel} : nom de workflow manquant")
        if name in seen_names:
            errors.append(f"{rel} : nom de workflow dupliqué (« {name} »)")
        seen_names.add(name)
        if not isinstance(data.get("connections"), dict):
            errors.append(f"{rel} : connexions invalides")
        for index, node in enumerate(data.get("nodes")):
            if not isinstance(node, dict) or not str(node.get("name") or "").strip() or not str(node.get("type") or "").strip():
                errors.append(f"{rel} : nœud n°{index} invalide (nom/type manquants)")
        exports[rel] = data
    try:
        selection = json.loads(SELECTION_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        selection = {}
    selected = set()
    if isinstance(selection, dict):
        selected = {str(item) for item in selection.get("selected", []) if isinstance(item, str)}
    for rel in sorted(selected):
        if rel not in exports:
            errors.append(f"tests.json : « {rel} » ne correspond à aucun export connu")
    if not errors:
        print(f"OK : {len(exports)} export(s) valide(s), {len(selected)} pipeline(s) sélectionnée(s).")
        return 0
    for error in errors:
        print(error, file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
"""


TEMPLATE_RUNNER = """# __MARKER__
# Runner de tests : exécute les pipelines sélectionnées dans un conteneur n8n
# jetable. Bibliothèque standard uniquement, aucun paquet à installer.
# Usage : python .n8n-tests/runner.py
# Variables gérées par GitHub Actions : N8N_CI_CREDENTIALS (secret) et
# N8N_IMAGE (variable, optionnelle — la version pinnée par le launcher est
# utilisée par défaut).
from __future__ import annotations

import http.cookiejar
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
SELECTION_FILE = ROOT / ".n8n-tests" / "tests.json"

# Image pinnée par le launcher (surritable via le variable N8N_IMAGE).
N8N_IMAGE = os.environ.get("N8N_IMAGE") or "__N8N_IMAGE__"
SECRET_NAME = "N8N_CI_CREDENTIALS"

OWNER_EMAIL = "ci@n8n-launcher.test"
OWNER_PASSWORD = "CiPassw0rd-n8n9"  # conforme à la politique de mot de passe n8n
API_KEY_LABEL = "n8n-ci"

# Délais (secondes) : démarrage du conteneur / exécution d'une pipeline.
START_TIMEOUT = 240.0
RUN_TIMEOUT = 300.0
POLL_INTERVAL = 2.0

MANUAL_TRIGGERS = {
    "n8n-nodes-base.manualTrigger",
    "@n8n/n8n-nodes-langchain.manualChatTrigger",
}
SCHEDULE_TRIGGERS = {"n8n-nodes-base.scheduleTrigger"}

BLOCKLIST_ROOT = {
    "package.json", "package-lock.json", "tsconfig.json", "composer.json",
    "Pipfile", "Pipfile.lock", "deno.json", "deno.lock",
}

# Champ acceptés par le schéma public de création de workflows (strict).
CREATE_WHITELIST = {
    "name", "nodes", "connections", "settings", "staticData", "pinData",
    "nodeGroups", "projectId", "parentFolderId",
}


def _verbose(message: str) -> None:
    print(f"[runner] {message}", flush=True)


def find_free_port() -> int:
    \"\"\"Laisse l'OS choisir un port local libre pour publier n8n.\"\"\"
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class Http:
    \"\"\"Client HTTP JSON minimaliste avec gestion des cookies (stdlib only).\"\"\"

    def __init__(self, base_url: str, *, timeout: float = 15.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.cookies = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.cookies)
        )

    def request(
        self,
        method: str,
        path: str,
        payload: Any = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, Any]:
        \"\"\"Exécute la requête ; rend (code HTTP, corps JSON ou texte).\"\"\"
        url = self.base_url + path
        data = None
        request_headers = dict(headers or {})
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            request_headers.setdefault("Content-Type", "application/json")
        request = urllib.request.Request(
            url, data=data, headers=request_headers, method=method
        )
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                raw = response.read().decode("utf-8", "replace")
                code = response.status
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", "replace")
            code = exc.code
        except OSError as exc:
            return 0, str(exc)
        text = raw.strip()
        if not text:
            return code, None
        try:
            return code, json.loads(text)
        except ValueError:
            return code, text


def collect_workflows():
    found = []
    pipelines = ROOT / "n8nPipelines"
    if pipelines.is_dir():
        for path in sorted(pipelines.glob("*.json")):
            if path.is_file():
                found.append(f"n8nPipelines/{path.name}")
    for path in sorted(ROOT.glob("*.json")):
        if path.is_file() and path.name not in BLOCKLIST_ROOT:
            found.append(path.name)
    return sorted(set(found))


def read_selection():
    try:
        data = json.loads(SELECTION_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    if not isinstance(data, dict):
        return set()
    return {str(item) for item in data.get("selected", []) if isinstance(item, str)}


def wait_ready(http: Http) -> None:
    \"\"\"Attend que n8n réponde en JSON (owner/setup sert de sonde).\"\"\"
    deadline = time.monotonic() + START_TIMEOUT
    payload = {
        "firstName": "CI",
        "lastName": "Runner",
        "email": OWNER_EMAIL,
        "password": OWNER_PASSWORD,
    }
    while True:
        try:
            code, body = http.request("POST", "/rest/owner/setup", payload)
        except Exception as exc:
            code, body = 0, str(exc)
        if not isinstance(body, str):
            print(f"[runner] n8n répond (HTTP {code}) — prêt", flush=True)
            return
        if time.monotonic() >= deadline:
            raise RuntimeError("n8n ne répond pas en JSON dans le délai imparti")
        time.sleep(2.0)


def login(http: Http) -> None:
    \"\"\"Connecte le propriétaire ; l'auth des endpoints /rest passe par le
    cookie de session ``n8n-auth`` posé par la réponse (jamais par un jeton
    dans le corps de réponse sur n8n 2.33+). Le jeton éventuel du corps n'est
    accepté que comme signal de secours pour de futures versions.\"\"\"
    code, body = http.request(
        "POST",
        "/rest/login",
        {"emailOrLdapLoginId": OWNER_EMAIL, "password": OWNER_PASSWORD},
    )
    body_token = isinstance(body, dict) and bool((body.get("data") or {}).get("token"))
    has_session = any(cookie.name == "n8n-auth" for cookie in http.cookies)
    if code not in (200, 201) or not (body_token or has_session):
        raise RuntimeError(f"connexion au propriétaire impossible (HTTP {code}) : {body}")


def create_api_key(http: Http) -> str:
    code, body = http.request(
        "POST",
        "/rest/api-keys",
        {
            "label": API_KEY_LABEL,
            "scopes": [
                "workflow:list",
                "workflow:create",
                "workflow:read",
                "credential:read",
                "credential:create",
            ],
            "expiresAt": 0,
        },
    )
    if not isinstance(body, dict):
        raise RuntimeError(f"n8n n'a pas répondu en JSON pour la clé API (HTTP {code})")
    secret = body.get("data", {}).get("rawApiKey") or body.get("rawApiKey")
    if not secret:
        raise RuntimeError(f"n8n n'a pas renvoyé de clé API (HTTP {code}) : {body}")
    return str(secret)


def _public_get(http: Http, api_key: str, path: str):
    code, body = http.request(
        "GET",
        path,
        headers={"X-N8N-API-KEY": api_key, "Accept": "application/json"},
    )
    if code not in (200, 201):
        raise RuntimeError(f"n8n a refusé {path} (HTTP {code}) : {body}")
    if isinstance(body, list):
        return [item for item in body if isinstance(item, dict)]
    if isinstance(body, dict):
        data = body.get("data", [])
        return [item for item in data if isinstance(item, dict)] if isinstance(data, list) else []
    return []


def import_workflows(http: Http, api_key: str):
    \"\"\"Importe tous les exports ; renvoie {chemin relatif: id n8n}.\"\"\"
    existing = _public_get(http, api_key, "/api/v1/workflows")
    by_name = {str(item.get("name")): str(item.get("id")) for item in existing if item.get("id")}
    known_names = set(by_name)
    mapping = {}
    headers = {"X-N8N-API-KEY": api_key, "Accept": "application/json"}
    for rel in collect_workflows():
        try:
            data = json.loads((ROOT / rel).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise RuntimeError(f"export « {rel} » illisible : {exc}") from exc
        name = str(data.get("name") or "").strip()
        if name in known_names:
            mapping[rel] = by_name[name]
            continue
        payload = {key: value for key, value in data.items() if key in CREATE_WHITELIST}
        payload.setdefault("settings", {})
        code, body = http.request("POST", "/api/v1/workflows", payload, headers)
        if code not in (200, 201) or not isinstance(body, dict):
            raise RuntimeError(f"import de « {rel} » refusé (HTTP {code}) : {body}")
        created = body if isinstance(body, dict) else {}
        workflow_id = created.get("id") or created.get("data", {}).get("id")
        if not workflow_id:
            raise RuntimeError(f"n8n n'a pas renvoyé d'identifiant pour « {rel} »")
        mapping[rel] = str(workflow_id)
        known_names.add(name)
        by_name[name] = str(workflow_id)
    print(f"[runner] {len(mapping)} workflow(s) disponible(s) dans le conteneur", flush=True)
    return mapping


def configure_credentials(http: Http, api_key: str) -> int:
    \"\"\"Reproduit dans le conteneur les credentials du secret ; 0 par défaut.\"\"\"
    raw = os.environ.get(SECRET_NAME, "")
    if not raw:
        _verbose(
            f"secret {SECRET_NAME} absent — les pipelines qui utilisent des "
            "credentials échoueront à l'exécution"
        )
        return 0
    try:
        items = json.loads(raw)
    except ValueError as exc:
        raise RuntimeError(f"le secret {SECRET_NAME} n'est pas un JSON valide") from exc
    if not isinstance(items, list):
        raise RuntimeError(f"le secret {SECRET_NAME} doit être une liste de credentials")
    existing = _public_get(http, api_key, "/api/v1/credentials")
    known = {
        (str(item.get("name")), str(item.get("type")))
        for item in existing
        if item.get("name") and item.get("type")
    }
    headers = {"X-N8N-API-KEY": api_key, "Accept": "application/json"}
    created = 0
    for item in items:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        ctype = str(item.get("type") or "").strip()
        data = item.get("data") or {}
        if not name or not ctype:
            _verbose(f"credential ignorée (nom/type manquants) : {item!r}")
            continue
        if (name, ctype) in known:
            continue
        code, body = http.request(
            "POST",
            "/api/v1/credentials",
            {"name": name, "type": ctype, "data": data},
            headers,
        )
        if code not in (200, 201):
            raise RuntimeError(f"création de la credential « {name} » refusée (HTTP {code}) : {body}")
        created += 1
        known.add((name, ctype))
    _verbose(f"{created} credential(s) créée(s) dans le conteneur")
    return created


def run_payload(export: dict):
    \"\"\"Choisit le payload de /rest/workflows/{id}/run.

    Renvoie (kind, payload) : « trigger » pour un run depuis un nœud de
    déclenchement, « destination » pour un run complet depuis un déclencheur
    inconnu (programmé), ou (None, None) quand rien n'est exécutable.
    \"\"\"
    nodes = [n for n in export.get("nodes", []) if isinstance(n, dict)]
    pin_data = export.get("pinData") or {}
    manual = next((n for n in nodes if n.get("type") in MANUAL_TRIGGERS), None)
    if manual:
        return "trigger", {"triggerToStartFrom": {"name": manual.get("name")}}
    schedule = next((n for n in nodes if n.get("type") in SCHEDULE_TRIGGERS), None)
    if schedule:
        destination = next((n.get("name") for n in reversed(nodes)), None)
        if not destination:
            return None, None
        return "destination", {
            "destinationNode": {"nodeName": destination, "mode": "inclusive"}
        }
    pinned = next(
        (
            n
            for n in nodes
            if str(n.get("type", "")).rsplit(".", 1)[-1].endswith("Trigger")
            and n.get("name") in pin_data
        ),
        None,
    )
    if pinned:
        return "trigger", {"triggerToStartFrom": {"name": pinned.get("name")}}
    return None, None


def trigger_run(http: Http, workflow_id: str, payload: dict) -> str | None:
    \"\"\"Lance le run ; renvoie l'id d'exécution ou None si en attente webhook.

    La réponse est soit ``{"executionId": ...}`` soit, selon les versions,
    ``{"data": {"executionId": ...}}`` — les deux sont acceptées.\"\"\"
    code, body = http.request(
        "POST", f"/rest/workflows/{workflow_id}/run", payload
    )
    if not isinstance(body, dict):
        raise RuntimeError(f"n8n a refusé le run de {workflow_id} (HTTP {code}) : {body}")
    data = body.get("data")
    if body.get("waitingForWebhook") or (
        isinstance(data, dict) and data.get("waitingForWebhook")
    ):
        return None
    execution_id = body.get("executionId")
    if not execution_id and isinstance(data, dict):
        execution_id = data.get("executionId")
    if execution_id:
        return str(execution_id)
    raise RuntimeError(f"n8n a refusé le run de {workflow_id} (HTTP {code}) : {body}")


def _execution_detail(data: Any) -> str:
    if not isinstance(data, dict):
        return ""
    result = data.get("resultData") or {}
    error = result.get("error") if isinstance(result, dict) else None
    message = error.get("message") if isinstance(error, dict) else None
    node = data.get("lastNodeExecuted") or data.get("stoppedAt") or ""
    if message:
        return f"{node} : {message}" if node else str(message)
    return f"dernier nœud : {node}" if node else ""


def wait_execution(http: Http, execution_id: str):
    \"\"\"Sonde une exécution ; renvoie (statut, détail).\"\"\"
    deadline = time.monotonic() + RUN_TIMEOUT
    while True:
        code, body = http.request(
            "GET", f"/rest/executions/{execution_id}"
        )
        if not isinstance(body, dict):
            if time.monotonic() >= deadline:
                raise RuntimeError(f"n8n n'a pas renvoyé l'exécution {execution_id} (HTTP {code})")
            time.sleep(POLL_INTERVAL)
            continue
        data = body.get("data") or body
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except ValueError:
                data = {}
        if not isinstance(data, dict):
            time.sleep(POLL_INTERVAL)
            continue
        status = str(data.get("status") or "")
        if status == "waiting":
            return "waiting", "exécution en attente d'une requête (webhook)"
        if status in ("running", "new", "started"):
            if time.monotonic() >= deadline:
                raise RuntimeError(f"exécution {execution_id} toujours en cours après {RUN_TIMEOUT:.0f}s")
            time.sleep(POLL_INTERVAL)
            continue
        if not status and not data.get("finished"):
            if time.monotonic() >= deadline:
                raise RuntimeError(f"exécution {execution_id} sans statut après {RUN_TIMEOUT:.0f}s")
            time.sleep(POLL_INTERVAL)
            continue
        return status or ("error" if data.get("finished") else "running"), _execution_detail(data)


STATUS_LABELS = {
    "success": "réussie",
    "error": "en échec",
    "crashed": "crash",
    "failed": "en échec",
    "timeout": "délai dépassé",
    "cancelled": "annulée",
    "waiting": "en attente (webhook)",
}


def summarize(results):
    \"\"\"Affiche le bilan ; 1 si au moins une pipeline en échec.\"\"\"
    passed = [rel for rel, status in results if status == "success"]
    failed = [(rel, status) for rel, status in results if status not in ("success", "waiting")]
    waiting = [rel for rel, status in results if status == "waiting"]
    print()
    print("Résultats des pipelines :")
    for rel, status in results:
        label = STATUS_LABELS.get(status, status)
        mark = "✔" if status == "success" else ("◻" if status == "waiting" else "✘")
        print(f"  - {mark} {rel} ({label})")
    print()
    print(f"réussies : {len(passed)} | en attente : {len(waiting)} | en échec : {len(failed)}")
    for rel, status in failed:
        print(f"échec : {rel} ({STATUS_LABELS.get(status, status)})", file=sys.stderr)
    return 1 if failed else 0


def main():
    selected = read_selection()
    if not selected:
        _verbose("aucune pipeline sélectionnée dans tests.json — rien à exécuter")
        return 0
    port = find_free_port()
    http = Http(f"http://127.0.0.1:{port}")
    name = f"n8n-ci-{os.getpid()}"
    _verbose(f"démarrage du conteneur {N8N_IMAGE} ({name})")
    docker = os.environ.get("DOCKER") or "docker"
    try:
        subprocess.run(
            [
                docker,
                "run",
                "--name", name,
                "-d",
                "-p", f"127.0.0.1:{port}:5678",
                "-e", "N8N_DIAGNOSTICS_ENABLED=false",
                "-e", "N8N_DISABLE_TELEMETRY=true",
                "-e", "N8N_VERSION_NOTIFICATIONS_ENABLED=false",
                # Le conteneur est local et jetable : sans ce flag le cookie
                # de session n8n-auth est marqué « Secure » et ne serait pas
                # rejoué en HTTP plain par le client standard, d'où des 401
                # sur /rest/api-keys. Même réglage que les conteneurs gérés.
                "-e", "N8N_SECURE_COOKIE=false",
                "-e", f"N8N_ENCRYPTION_KEY={os.environ.get('N8N_ENCRYPTION_KEY') or 'n8n-ci-encryption-key-0123456789abcdef'}",
                "-e", "EXECUTIONS_TIMEOUT=300",
                "-e", "EXECUTIONS_TIMEOUT_MAX=300",
                N8N_IMAGE,
            ],
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(
            f"docker run a échoué : {exc.stderr.strip() or exc.stdout.strip() or exc}"
        ) from exc
    try:
        wait_ready(http)
        login(http)
        api_key = create_api_key(http)
        mapping = import_workflows(http, api_key)
        configure_credentials(http, api_key)

        results = []
        for rel in sorted(selected):
            if rel not in mapping:
                raise RuntimeError(f"pipeline « {rel} » n'a pas pu être importée dans n8n")
            export = json.loads((ROOT / rel).read_text(encoding="utf-8"))
            _, payload = run_payload(export)
            if payload is None:
                raise RuntimeError(f"pipeline « {rel} » n'a aucun déclencheur exécutable")
            execution_id = trigger_run(http, mapping[rel], payload)
            if execution_id is None:
                results.append((rel, "waiting"))
                continue
            status, detail = wait_execution(http, execution_id)
            results.append((rel, status))
            suffix = f" ({detail})" if detail else ""
            _verbose(f"{rel} : {status}{suffix}")
        return summarize(results)
    finally:
        subprocess.run(
            [docker, "rm", "-f", name],
            check=False,
            capture_output=True,
            text=True,
        )


if __name__ == "__main__":
    sys.exit(main())
"""