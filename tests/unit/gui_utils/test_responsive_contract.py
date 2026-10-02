"""The shared responsive contract fails loudly and remains toolkit-free."""

from __future__ import annotations

import pytest

from n8n_launcher.gui_utils.responsive import (
    MIN_WINDOW_HEIGHT,
    MIN_WINDOW_WIDTH,
    PREFERRED_WINDOW_HEIGHT,
    PREFERRED_WINDOW_WIDTH,
    Rect,
    Viewport,
    assert_rect_within,
    initial_window_size,
    responsive_failure,
)


def test_initial_window_size_is_bounded_by_available_space() -> None:
    assert initial_window_size(Viewport(1600, 1000)) == (
        PREFERRED_WINDOW_WIDTH,
        PREFERRED_WINDOW_HEIGHT,
    )
    assert initial_window_size(Viewport(1100, 700)) == (1100, 700)


def test_viewport_rejects_non_positive_dimensions() -> None:
    with pytest.raises(ValueError, match="dimensions positives"):
        Viewport(0, MIN_WINDOW_HEIGHT)


def test_responsive_failure_contains_the_blocking_context() -> None:
    error = responsive_failure(
        "Dashboard",
        Viewport(MIN_WINDOW_WIDTH, MIN_WINDOW_HEIGHT),
        "button 'Save' is outside the visible bounds",
        dpi=150,
    )

    assert str(error) == (
        "FAIL - Responsive layout violation\n"
        "Component: Dashboard\n"
        "Viewport: 1024x640\n"
        "DPI: 150%\n"
        "Reason: button 'Save' is outside the visible bounds"
    )


def test_rect_bounds_violation_is_blocking_and_descriptive() -> None:
    with pytest.raises(AssertionError, match="FAIL - Responsive layout violation") as failure:
        assert_rect_within(
            "UserSettingsPanel",
            Rect(90, 0, 20, 20),
            Rect(0, 0, 100, 100),
            Viewport(800, 600),
        )

    assert "outside bounds" in str(failure.value)
