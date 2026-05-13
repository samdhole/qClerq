from __future__ import annotations

from typing import Literal


def route(total: float, tier_1_max: float, tier_2_max: float) -> Literal["auto", "manager", "cfo"]:
    """Determine approval tier from invoice total and configurable thresholds.

    Pure function — no I/O, no settings access.

    Boundary semantics: total < tier_1_max → auto; total == tier_1_max routes to
    manager (strict less-than at the lower boundary, inclusive at the upper).
    """
    if total < tier_1_max:
        return "auto"
    if total <= tier_2_max:
        return "manager"
    return "cfo"
