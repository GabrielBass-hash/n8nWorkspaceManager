"""The API-key scopes the launcher asks n8n for.

n8n validates the requested scopes against the owner's role and answers
``400 Invalid scopes for user role`` for anything it does not grant, so the
list is a contract with the n8n version, not a preference. It lives here — a
stdlib-only module — because *two* very different callers need the exact same
list: the local owner bootstrap (:mod:`n8n_launcher.n8n.owner`) and the
generated ``deploy.py`` shipped to a deployment server
(:func:`n8n_launcher.remote.deploy.render_deploy_script`, which receives it
through the ``__API_KEY_SCOPES__`` token). Duplicating it is how the remote
deploy drifted out of sync with the local one and started failing its publish.
"""

from __future__ import annotations

# workflow + credential CRUD, because the deploy (and the local import) lists,
# creates, updates and activates workflows and recreates credentials by name.
REQUIRED_WORKFLOW_SCOPES = [
    "workflow:list",
    "workflow:read",
    "workflow:create",
    "workflow:update",
    "workflow:delete",
    "workflow:activate",
    "credential:list",
    "credential:read",
    "credential:create",
    "credential:update",
    "credential:delete",
]
