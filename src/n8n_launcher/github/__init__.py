"""GitHub API integration (repository creation for git/CI setup).

Entry: ``create_repo`` / ``list_workflow_runs`` (``api.py``),
``resolve_github_token`` (``auth.py``).
Gotcha: repo paths keep a **literal** slash (``repos/owner/repo/…``) —
URL-encoding it makes every Actions endpoint 404.
Map: ``docs/architecture.md``.
"""
