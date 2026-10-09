"""Loader for the template files that generate the remote and CI artefacts.

The post-receive hook, the server-side ``deploy.py`` and the CI harness are
shipped as template files next to the code that renders them, not as string
literals: the tree-sitter index of :mod:`n8n_launcher.remote.deploy` and
:mod:`n8n_launcher.workspaces.ci` then describes the launcher's own helpers
instead of several hundred lines of bash and generated Python hidden inside
strings.
"""

from importlib.resources import files


def read_template(package: str, name: str) -> str:
    """Return the text of template *name* inside *package*'s ``templates/`` dir.

    Reading happens at import time of the calling module, so a template lost
    from the distribution (a missing ``--add-data`` in the PyInstaller bundle,
    a packaging omission) fails loudly at startup rather than at the first
    publish.
    """
    return files(package).joinpath("templates", name).read_text(encoding="utf-8")
