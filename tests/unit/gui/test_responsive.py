"""Blocking responsive checks for the real launcher window."""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import QSize
from PySide6.QtWidgets import QWidget

from n8n_launcher.core.models import DbConfig, DbMode, Workspace, WorkspaceState
from n8n_launcher.gui.window import MainWindow
from n8n_launcher.gui_utils.responsive import Rect, Viewport, assert_rect_within


class _Manager:
    """Provide the observer and list surface consumed by ``MainWindow``."""

    def __init__(self, workspaces: list[Workspace]) -> None:
        self._workspaces = workspaces
        self.observers: list[object] = []

    def list(self) -> list[Workspace]:
        return list(self._workspaces)

    def add_observer(self, observer: object) -> None:
        self.observers.append(observer)

    def remove_observer(self, observer: object) -> None:
        if observer in self.observers:
            self.observers.remove(observer)


def _workspaces(count: int) -> list[Workspace]:
    return [
        Workspace(
            id=f"ws-{index}",
            name=f"Workspace {index} with a deliberately long title",
            workflows_dir=Path("/tmp") / f"workspace-{index}",
            port=5678 + index,
            db=DbConfig(mode=DbMode.NONE),
            state=WorkspaceState.RUNNING if index % 2 else WorkspaceState.STOPPED,
        )
        for index in range(count)
    ]


def _rect(widget: QWidget) -> Rect:
    geometry = widget.geometry()
    return Rect(geometry.x(), geometry.y(), geometry.width(), geometry.height())


@pytest.mark.responsive
@pytest.mark.parametrize("size", [QSize(1024, 640), QSize(1280, 720), QSize(1024, 1024)])
def test_main_window_remains_contained_across_viewports(qt_app, qtbot, size: QSize) -> None:
    window = MainWindow(_Manager(_workspaces(40)))
    qtbot.addWidget(window)
    window.resize(size)
    window.show()
    qtbot.wait(20)

    viewport = Viewport(window.width(), window.height())
    central = window.centralWidget()
    assert central is not None
    central_bounds = _rect(central)

    header = window.findChild(QWidget, "header")
    assert header is not None
    assert_rect_within("MainWindow.header", _rect(header), central_bounds, viewport)

    control = window.findChild(QWidget, "new")
    assert control is not None
    assert_rect_within(
        "MainWindow.new",
        _rect(control),
        _rect(header),
        viewport,
    )

    assert_rect_within("MainWindow.board", _rect(window.board), central_bounds, viewport)
    assert window.minimumSize() == QSize(1024, 640)

    window.resize(QSize(1600, 900))
    qtbot.wait(20)
    assert_rect_within(
        "MainWindow.board",
        _rect(window.board),
        _rect(central),
        Viewport(window.width(), window.height()),
    )
