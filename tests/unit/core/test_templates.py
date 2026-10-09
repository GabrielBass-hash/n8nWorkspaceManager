"""Tests for the package template loader (:mod:`n8n_launcher.core.templates`)."""

import pytest

from n8n_launcher.core.templates import read_template

SHIPPED = [
    ("n8n_launcher.remote", "post-receive.sh.tmpl"),
    ("n8n_launcher.remote", "deploy.py.tmpl"),
    ("n8n_launcher.workspaces", "n8n-ci.yml.tmpl"),
    ("n8n_launcher.workspaces", "validate.py.tmpl"),
    ("n8n_launcher.workspaces", "runner.py.tmpl"),
]


@pytest.mark.parametrize(("package", "name"), SHIPPED)
def test_read_template_returns_the_shipped_template(package: str, name: str) -> None:
    content = read_template(package, name)

    assert content.strip()
    # Every template is a token document: the rendered artefact is assembled by
    # __TOKEN__ substitution, so an empty substitution surface means the wrong
    # file was read.
    assert "__MARKER__" in content


def test_read_template_missing_template_raises() -> None:
    with pytest.raises(FileNotFoundError):
        read_template("n8n_launcher.remote", "does-not-exist.tmpl")
