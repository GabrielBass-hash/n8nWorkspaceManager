"""The spacing scale is a ladder, not a bag of numbers.

``gui.tokens`` renamed the interface's existing padding rather than moving it,
so these tests guard the two properties the rename relies on: the ladder really
is monotonic and even, and ``GUTTER`` is a step of it rather than a rival scale
of its own. A duplicate or an out-of-order value would make a name lie about how
much space it buys, and nothing else in the suite would notice.
"""

from __future__ import annotations

from n8n_launcher.gui import tokens

# The ladder, smallest first, with the name that must sit on each step.
LADDER = [
    tokens.SPACE_HAIRLINE,
    tokens.SPACE_TIGHT,
    tokens.SPACE_SM,
    tokens.SPACE_MD,
    tokens.SPACE_LG,
    tokens.SPACE_XL,
    tokens.SPACE_2XL,
    tokens.SPACE_3XL,
    tokens.GUTTER,
]

STEP = 2


def test_the_ladder_rises_by_one_step_at_a_time() -> None:
    """Every rung is 2px above the one before it, so the rhythm is legible."""
    assert [2 + STEP * i for i in range(len(LADDER))] == LADDER


def test_no_two_names_buy_the_same_space() -> None:
    """Two names on one value would let a panel pick either and mean neither."""
    assert len(set(LADDER)) == len(LADDER)


def test_the_gutter_is_a_rung_of_the_ladder() -> None:
    """The dialog measure is the ladder's last step, not a second scale.

    It is the one number with a role of its own — every dialog insets by it — so
    it is pulled out by name, but it must stay one step above ``SPACE_3XL`` or
    the two names would be an arbitrary pair rather than a scale.
    """
    assert tokens.GUTTER - tokens.SPACE_3XL == STEP


def test_a_hairline_is_one_pixel_and_sits_below_the_ladder() -> None:
    """A border is not a spacing step: it is the thinnest line Tk will draw."""
    assert tokens.HAIRLINE == 1
    assert tokens.HAIRLINE < tokens.SPACE_HAIRLINE


def test_the_border_inset_clears_the_border_it_sits_inside() -> None:
    """A bordered row is inset by its own 1px highlight plus a hairline of air.

    Expressed in terms of the rule so the reason travels with the number.
    """
    assert tokens.BORDER_INSET == tokens.HAIRLINE + 2
    assert tokens.BORDER_INSET < tokens.SPACE_TIGHT
