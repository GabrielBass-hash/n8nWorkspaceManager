"""Spacing tokens for the interface.

Every ``padx``/``pady`` in the GUI reads its numbers from here, so the rhythm
is stated once. They used to be literals scattered over a hundred call sites,
which is how one panel ended up at ``pady=(16, 2)`` while its neighbour used
``pady=(14, 2)`` and neither could say why.

The ladder is two pixels wide, and the values are the ones the interface
already used: this module renames the existing spacing rather than moving it,
so a table that fitted yesterday still fits today. ``GUTTER`` is pulled out of
the ladder because it is the one number with a *role* — the horizontal measure
every dialog, panel and card insets its content by.
"""

from __future__ import annotations

# A caption sitting directly on the value it labels: no visible gap wanted.
SPACE_HAIRLINE = 2
# Tight grouping — a chip row, the inside of a single row.
SPACE_TIGHT = 4
# A small gap between two controls of the same kind (a button group).
SPACE_SM = 6
# The standard inset inside a panel or a card.
SPACE_MD = 8
# Between two blocks of a panel's body.
SPACE_LG = 10
# Between two blocks that must read as separate sections.
SPACE_XL = 12
# A section's own vertical padding.
SPACE_2XL = 14
# A dialog's top and bottom padding.
SPACE_3XL = 16
# The dialogs' horizontal measure, shared by the dialog frame, the action bar
# and the prose inside it so a dialog's text is aligned to its buttons.
GUTTER = 18

# A one pixel rule: the status bar's top border, the tooltip's frame.
HAIRLINE = 1

# A child of a bordered container (a workspace row) is inset by the border it
# sits inside plus a hairline of air. Two pixels read as flush at this size and
# the row's highlight looked like a second border, so this is off the ladder on
# purpose: it is a border compensation, not a spacing step.
BORDER_INSET = HAIRLINE + 2
