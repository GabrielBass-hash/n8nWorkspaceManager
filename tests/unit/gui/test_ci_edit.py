"""Tests for the CI dialogs (gui/ci_edit.py)."""

from __future__ import annotations

from contextlib import ExitStack, contextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from n8n_launcher.gui.ci_edit import prompt_ci_credentials, prompt_ci_workflows

from helpers import FakeRoot, FakeTk, FakeTtk, make_workspace  # noqa: E402


def ci_mocks(workspace, credentials=None):
    """Build a fake manager whose n8n reports the given credentials."""
    launcher = MagicMock()
    api = MagicMock()
    api.list_credentials.return_value = credentials or []
    launcher.api_factory.return_value = api
    workspace.api_key = "key"
    return launcher, api


class _ReturnTk(FakeTk):
    """Same fakes, but Toplevel.wait_window submits with <Return> when armed."""

    class Toplevel(FakeTk.Toplevel):
        press_return = False

        def wait_window(self) -> None:
            handler = self._bindings.get("<Return>")
            if handler is not None and self.press_return:
                handler(None)
                return
            super().wait_window()


@contextmanager
def _patch_ci_editor(tk_fake):
    """Patch ci_edit's tk/ttk/messagebox and yield them, then restore."""

    stack = ExitStack()
    tk_patch = stack.enter_context(patch("n8n_launcher.gui.ci_edit.tk", tk_fake))
    ttk_patch = stack.enter_context(patch("n8n_launcher.gui.ci_edit.ttk", FakeTtk()))
    messagebox = stack.enter_context(
        patch(
            "n8n_launcher.gui.ci_edit.messagebox",
            MagicMock(
                askyesno=MagicMock(return_value=False),
                showwarning=MagicMock(),
                showerror=MagicMock(),
            ),
        )
    )
    with stack:
        yield tk_patch, ttk_patch, messagebox


def flat_children(widget):
    """Yield a widget's children, recursing into container frames."""
    for child in widget.children:
        yield child
        if hasattr(child, "children"):
            yield from flat_children(child)


_MANUAL = (
    '{"name": "M", "nodes": [{"name": "Bouton",'
    ' "type": "n8n-nodes-base.manualTrigger", "typeVersion": 1}],'
    ' "connections": {}, "settings": {}}'
)
_HOOK = (
    '{"name": "H", "nodes": [{"name": "Webhook",'
    ' "type": "n8n-nodes-base.webhookTrigger", "typeVersion": 1}],'
    ' "connections": {}, "settings": {}}'
)


def test_prompt_ci_workflows_escape_returns_none(tmp_path) -> None:
    root = tmp_path / "ws"
    (root / "n8nPipelines").mkdir(parents=True)
    (root / "n8nPipelines" / "manual.json").write_text(_MANUAL, encoding="utf-8")
    workspace = make_workspace(tmp_path, "CI", 5678)
    workspace.workflows_dir = root

    with _patch_ci_editor(FakeTk()) as (_, _, _):
        result = prompt_ci_workflows(FakeRoot(), workspace)

    assert result is None


def test_prompt_ci_workflows_save_returns_selection_and_push(tmp_path) -> None:
    root = tmp_path / "ws"
    (root / "n8nPipelines").mkdir(parents=True)
    (root / "n8nPipelines" / "manual.json").write_text(_MANUAL, encoding="utf-8")
    selection = root / ".n8n-tests" / "tests.json"
    selection.parent.mkdir(parents=True)
    selection.write_text('{"selected": ["n8nPipelines/manual.json"]}', encoding="utf-8")
    workspace = make_workspace(tmp_path, "CI", 5678)
    workspace.workflows_dir = root

    tk_fake = _ReturnTk()
    _ReturnTk.Toplevel.press_return = True
    try:
        with _patch_ci_editor(tk_fake) as (_, _, messagebox):
            messagebox.askyesno = lambda *args, **_kwargs: True
            result = prompt_ci_workflows(FakeRoot(), workspace)
    finally:
        _ReturnTk.Toplevel.press_return = False

    assert result == ({"n8nPipelines/manual.json"}, True)


def test_prompt_ci_workflows_greys_ineligible_and_toggles(tmp_path) -> None:
    root = tmp_path / "ws"
    (root / "n8nPipelines").mkdir(parents=True)
    # Sort key controls which row identify_row() reports: 'a' before 'z'.
    (root / "n8nPipelines" / "aa-manual.json").write_text(_MANUAL, encoding="utf-8")
    (root / "n8nPipelines" / "zz-hook.json").write_text(_HOOK, encoding="utf-8")
    workspace = make_workspace(tmp_path, "CI", 5678)
    workspace.workflows_dir = root

    FakeTtk.Treeview.instances.clear()
    with _patch_ci_editor(FakeTk()):
        prompt_ci_workflows(FakeRoot(), workspace)

    tree = FakeTtk.Treeview.instances[0]
    eligible = tree.item("n8nPipelines/aa-manual.json")
    blocked = tree.item("n8nPipelines/zz-hook.json")
    assert "disabled" not in eligible.get("tags", [])
    assert "disabled" in blocked.get("tags", [])
    assert blocked["text"].startswith("\u2013")

    click = tree._bindings["<Button-1>"]
    assert tree.item("n8nPipelines/aa-manual.json")["text"].startswith("\u2610")
    click(SimpleNamespace(x=5, y=5))
    assert tree.item("n8nPipelines/aa-manual.json")["text"].startswith("\u2611")
    click(SimpleNamespace(x=5, y=5))
    assert tree.item("n8nPipelines/aa-manual.json")["text"].startswith("\u2610")


def test_prompt_ci_credentials_warns_when_never_started(tmp_path) -> None:
    workspace = make_workspace(tmp_path, "CI", 5678)
    launcher, _ = ci_mocks(workspace)
    workspace.api_key = None

    with _patch_ci_editor(FakeTk()) as (_, _, messagebox):
        prompt_ci_credentials(FakeRoot(), launcher, workspace)

    assert "Démarrez ce workspace" in messagebox.showwarning.call_args.args[1]
    launcher.api_factory.assert_not_called()


def test_prompt_ci_credentials_copies_json_then_records_metadata(tmp_path) -> None:
    workspace = make_workspace(tmp_path, "CI", 5678)
    launcher, _ = ci_mocks(
        workspace,
        credentials=[
            {"id": "c1", "name": "API", "type": "httpRequest"},
            {"id": "c2", "name": "DB", "type": "postgres"},
        ],
    )
    launcher.ci_credentials_payload.return_value = '{"ok": true}'

    with _patch_ci_editor(FakeTk()) as (tk_fake, _, _):
        prompt_ci_credentials(FakeRoot(), launcher, workspace)

    dialog = tk_fake.Toplevel.instances[-1]
    children = list(flat_children(dialog))
    buttons = [child for child in children if isinstance(child, tk_fake.Button)]
    copy = next(button for button in buttons if "Copier le JSON" in (button.text or ""))
    copy.command()

    assert dialog._clipboard == '{"ok": true}'
    status = next(
        child
        for child in children
        if isinstance(child, tk_fake.Label)
        and "JSON copié" in child._options.get("text", "")
    )
    assert "N8N_CI_CREDENTIALS" in status._options["text"]
    launcher.set_ci_credentials.assert_not_called()

    pasted = next(button for button in buttons if "J'ai collé" in (button.text or ""))
    pasted.command()
    launcher.set_ci_credentials.assert_called_once_with(
        workspace, [{"name": "API", "type": "httpRequest"}, {"name": "DB", "type": "postgres"}]
    )


def test_prompt_ci_credentials_skips_empty_names(tmp_path) -> None:
    workspace = make_workspace(tmp_path, "CI", 5678)
    launcher, _ = ci_mocks(
        workspace,
        credentials=[{"id": "c1", "name": "  ", "type": "httpRequest"}],
    )

    with _patch_ci_editor(FakeTk()) as (tk_fake, _, _):
        prompt_ci_credentials(FakeRoot(), launcher, workspace)

    dialog = tk_fake.Toplevel.instances[-1]
    checkboxes = [
        child for child in flat_children(dialog) if isinstance(child, tk_fake.Checkbutton)
    ]
    assert checkboxes == []


def test_prompt_ci_credentials_excludes_unticked_from_payload(tmp_path) -> None:
    workspace = make_workspace(tmp_path, "CI", 5678)
    launcher, _ = ci_mocks(
        workspace,
        credentials=[
            {"id": "c1", "name": "API", "type": "httpRequest"},
            {"id": "c2", "name": "DB", "type": "postgres"},
        ],
    )
    launcher.ci_credentials_payload.return_value = "[]"

    with _patch_ci_editor(FakeTk()) as (tk_fake, _, _):
        prompt_ci_credentials(FakeRoot(), launcher, workspace)

    dialog = tk_fake.Toplevel.instances[-1]
    checkboxes = [
        child for child in flat_children(dialog) if isinstance(child, tk_fake.Checkbutton)
    ]
    assert len(checkboxes) == 2
    checkboxes[0].deselect()  # user unticks "API"
    copy = next(
        button
        for button in flat_children(dialog)
        if isinstance(button, tk_fake.Button) and "Copier le JSON" in (button.text or "")
    )
    copy.command()

    launcher.ci_credentials_payload.assert_called_once_with(
        workspace, [{"name": "DB", "type": "postgres"}]
    )
    assert dialog._clipboard == "[]"