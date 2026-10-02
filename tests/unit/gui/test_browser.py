"""Standalone browser-app launching for local n8n instances."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
import requests

from n8n_launcher.gui import browser as browser_module
from n8n_launcher.gui.browser import (
    WebAppLaunchError,
    WebAppOutcome,
    _browser_candidates,
    open_web_app,
    workspace_url,
)

_ORIGIN = "http://127.0.0.1:5678"


def _page(target_id: str, url: str) -> dict[str, str]:
    return {"id": target_id, "type": "page", "url": url}


def _chrome(candidate: str) -> str | None:
    """Stand in for ``shutil.which`` on whatever browser list this host probes.

    The candidates are per-OS (``chromium`` on Linux, an ``.app`` binary on macOS,
    ``chrome.exe`` on Windows), so a fake pinned to one of the three reports "no
    Chromium installed" on the other two instead of testing them.
    """
    return f"/opt/{candidate}" if candidate in _browser_candidates() else None


def _session(pages: list[dict[str, str]] | None = None, *, activate_ok: bool = True) -> MagicMock:
    session = MagicMock()

    def get(url: str, **_kwargs: object) -> MagicMock:
        response = MagicMock()
        response.ok = True
        if url.endswith("/json/list"):
            response.json.return_value = pages or []
        else:
            response.ok = activate_ok
        return response

    session.get.side_effect = get
    return session


def _profile(directory: Path, port: int | None = None) -> Path:
    if port is not None:
        directory.joinpath("DevToolsActivePort").write_text(
            f"{port}\n/devtools/browser/abc\n", encoding="utf-8"
        )
    return directory


def _candidates(monkeypatch: pytest.MonkeyPatch, os_name: str, sysname: str) -> tuple[str, ...]:
    """Return the browser names the launcher probes on *os_name*."""
    monkeypatch.setattr(browser_module.os, "name", os_name)
    # ``os.uname`` does not exist on Windows, so the Windows case must not need it.
    monkeypatch.setattr(
        browser_module.os, "uname", lambda: SimpleNamespace(sysname=sysname), raising=False
    )
    return _browser_candidates()


def test_workspace_url_points_at_the_local_instance() -> None:
    assert workspace_url(5679) == "http://127.0.0.1:5679"


def test_browser_candidates_are_executable_names_on_every_platform(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each OS probes its own names, so each list must be usable on its OS."""
    linux = _candidates(monkeypatch, "posix", "Linux")
    assert linux and all("/" not in candidate for candidate in linux)

    macos = _candidates(monkeypatch, "posix", "Darwin")
    assert macos and all(candidate.startswith("/Applications/") for candidate in macos)

    windows = _candidates(monkeypatch, "nt", "Windows")
    assert windows and all(candidate.endswith(".exe") for candidate in windows)


def test_open_web_app_launches_a_window_with_the_launcher_profile(tmp_path: Path) -> None:
    commands: list[tuple[list[str], dict[str, object]]] = []

    def popen(command: list[str], **kwargs: object) -> None:
        commands.append((command, kwargs))

    outcome = open_web_app(
        _ORIGIN,
        which=_chrome,
        popen=popen,
        profile=lambda: _profile(tmp_path),
        session=_session(),
    )

    command, kwargs = commands[0]
    # ``which`` only probes presence: the first candidate the host resolves is
    # what is launched, so the order of the per-OS list is what is pinned here.
    assert command[0] == _browser_candidates()[0]
    assert f"--app={_ORIGIN}" in command
    assert f"--user-data-dir={tmp_path}" in command
    # A fixed port would be fought over between two launchers and would have to
    # be firewall-friendly; 0 makes Chromium publish the one it chose.
    assert "--remote-debugging-port=0" in command
    # The window must outlive the launcher: that is a POSIX session, which
    # Windows has no equivalent for and is therefore left unset.
    assert kwargs["start_new_session"] is (os.name != "nt")
    # Chromium's own start-up chatter must not land in the launcher's journal.
    assert kwargs["stdout"] is subprocess.DEVNULL
    assert kwargs["stderr"] is subprocess.DEVNULL
    assert outcome is WebAppOutcome.OPENED


def test_open_web_app_raises_the_window_the_instance_is_already_in(tmp_path: Path) -> None:
    """The SPA navigates client-side, so the match is on the origin."""
    commands: list[list[str]] = []
    session = _session([_page("abc", f"{_ORIGIN}/workflow/12?x=1"), _page("def", "http://x/")])

    outcome = open_web_app(
        _ORIGIN,
        which=_chrome,
        popen=lambda command, **_kwargs: commands.append(command),
        profile=lambda: _profile(tmp_path, 9333),
        session=session,
    )

    assert outcome is WebAppOutcome.RAISED
    assert commands == []
    session.get.assert_any_call("http://127.0.0.1:9333/json/activate/abc", timeout=3.0)


def test_open_web_app_opens_a_window_when_the_browser_refuses_to_raise(
    tmp_path: Path,
) -> None:
    commands: list[list[str]] = []

    outcome = open_web_app(
        _ORIGIN,
        which=_chrome,
        popen=lambda command, **_kwargs: commands.append(command),
        profile=lambda: _profile(tmp_path, 9333),
        session=_session([_page("abc", _ORIGIN)], activate_ok=False),
    )

    assert outcome is WebAppOutcome.OPENED
    assert len(commands) == 1


def test_open_web_app_replaces_the_dead_window_of_a_just_started_instance(
    tmp_path: Path,
) -> None:
    """After a start, the window from the previous life shows only the failure."""
    commands: list[list[str]] = []
    session = _session([_page("abc", _ORIGIN)])

    outcome = open_web_app(
        _ORIGIN,
        reuse=False,
        which=_chrome,
        popen=lambda command, **_kwargs: commands.append(command),
        profile=lambda: _profile(tmp_path, 9333),
        session=session,
    )

    assert outcome is WebAppOutcome.OPENED
    session.get.assert_any_call("http://127.0.0.1:9333/json/close/abc", timeout=3.0)
    assert len(commands) == 1


def test_open_web_app_launches_when_no_browser_is_running_yet(tmp_path: Path) -> None:
    commands: list[list[str]] = []

    outcome = open_web_app(
        _ORIGIN,
        which=_chrome,
        popen=lambda command, **_kwargs: commands.append(command),
        profile=lambda: tmp_path / "absent",
        session=_session(),
    )

    assert outcome is WebAppOutcome.OPENED
    assert len(commands) == 1


def test_open_web_app_launches_when_the_published_port_is_nonsense(tmp_path: Path) -> None:
    tmp_path.joinpath("DevToolsActivePort").write_text("not-a-port\n", encoding="utf-8")
    commands: list[list[str]] = []
    session = _session([_page("abc", _ORIGIN)])

    outcome = open_web_app(
        _ORIGIN,
        which=_chrome,
        popen=lambda command, **_kwargs: commands.append(command),
        profile=lambda: tmp_path,
        session=session,
    )

    assert outcome is WebAppOutcome.OPENED
    assert len(commands) == 1


def test_open_web_app_launches_when_the_endpoint_is_unreachable(tmp_path: Path) -> None:
    """The port file appears before the endpoint accepts; that must not block."""
    commands: list[list[str]] = []
    session = MagicMock()
    session.get.side_effect = requests.ConnectionError("refused")

    outcome = open_web_app(
        _ORIGIN,
        which=_chrome,
        popen=lambda command, **_kwargs: commands.append(command),
        profile=lambda: _profile(tmp_path, 9333),
        session=session,
    )

    assert outcome is WebAppOutcome.OPENED
    assert len(commands) == 1


def test_open_web_app_refuses_to_fallback_to_a_tabbed_browser() -> None:
    with pytest.raises(WebAppLaunchError, match="Chromium"):
        open_web_app(_ORIGIN, which=lambda _candidate: None)


def test_open_web_app_rejects_non_http_urls() -> None:
    with pytest.raises(ValueError, match="URL web invalide"):
        open_web_app("file:///tmp/n8n", which=_chrome)


def test_open_web_app_reports_a_browser_that_cannot_start(tmp_path: Path) -> None:
    def refuse(_command: list[str], **_kwargs: object) -> None:
        raise OSError("no such binary")

    with pytest.raises(WebAppLaunchError, match="Impossible d'ouvrir"):
        open_web_app(
            _ORIGIN,
            which=_chrome,
            popen=refuse,
            profile=lambda: tmp_path,
            session=_session(),
        )
