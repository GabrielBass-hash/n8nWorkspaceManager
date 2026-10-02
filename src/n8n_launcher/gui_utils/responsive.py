"""Toolkit-free responsive layout rules shared by GUI views and tests.

The rules describe the supported desktop contract in device-independent pixels.
Widgets consume the values, while tests can exercise the same decisions without
constructing a Qt application.
"""

from __future__ import annotations

from dataclasses import dataclass

MIN_WINDOW_WIDTH = 1024
MIN_WINDOW_HEIGHT = 640
PREFERRED_WINDOW_WIDTH = 1200
PREFERRED_WINDOW_HEIGHT = 760


@dataclass(frozen=True)
class Viewport:
    """Describe the available device-independent viewport size."""

    width: int
    height: int

    def __post_init__(self) -> None:
        """Reject impossible viewport dimensions early."""
        if self.width <= 0 or self.height <= 0:
            raise ValueError("Une viewport doit avoir des dimensions positives")


@dataclass(frozen=True)
class Rect:
    """Represent a rectangle in a parent widget's coordinates."""

    x: int
    y: int
    width: int
    height: int

    @property
    def right(self) -> int:
        """Return the exclusive right edge."""
        return self.x + self.width

    @property
    def bottom(self) -> int:
        """Return the exclusive bottom edge."""
        return self.y + self.height

    def contains(self, other: Rect) -> bool:
        """Return whether *other* is fully contained by this rectangle."""
        return (
            other.x >= self.x
            and other.y >= self.y
            and other.right <= self.right
            and other.bottom <= self.bottom
        )

    def intersects(self, other: Rect) -> bool:
        """Return whether *other* overlaps this rectangle with positive area."""
        return not (
            other.x >= self.right
            or other.right <= self.x
            or other.y >= self.bottom
            or other.bottom <= self.y
        )


def initial_window_size(available: Viewport) -> tuple[int, int]:
    """Return a preferred window size that never exceeds available space.

    The minimum supported window size remains a contract enforced by the
    top-level widget. A display smaller than that contract is reported by Qt's
    normal minimum-size handling instead of being hidden by a special case.
    """
    return (
        min(PREFERRED_WINDOW_WIDTH, available.width),
        min(PREFERRED_WINDOW_HEIGHT, available.height),
    )


def responsive_failure(
    component: str,
    viewport: Viewport,
    reason: str,
    *,
    dpi: float | None = None,
) -> AssertionError:
    """Build the standard blocking error for a responsive violation."""
    dpi_line = f"DPI: {dpi:g}%\n" if dpi is not None else ""
    return AssertionError(
        "FAIL - Responsive layout violation\n"
        f"Component: {component}\n"
        f"Viewport: {viewport.width}x{viewport.height}\n"
        f"{dpi_line}"
        f"Reason: {reason}"
    )


def assert_rect_within(
    component: str,
    rect: Rect,
    bounds: Rect,
    viewport: Viewport,
    *,
    dpi: float | None = None,
) -> None:
    """Raise a blocking failure when a component leaves its parent bounds."""
    if not bounds.contains(rect):
        raise responsive_failure(
            component,
            viewport,
            f"rectangle {rect!r} is outside bounds {bounds!r}",
            dpi=dpi,
        )


__all__ = [
    "MIN_WINDOW_HEIGHT",
    "MIN_WINDOW_WIDTH",
    "PREFERRED_WINDOW_HEIGHT",
    "PREFERRED_WINDOW_WIDTH",
    "Rect",
    "Viewport",
    "assert_rect_within",
    "initial_window_size",
    "responsive_failure",
]
