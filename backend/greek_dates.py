"""Shared helpers for parsing Garmin's Greek-localized dates.

Garmin shows dates as a Greek month abbreviation plus a day — e.g. ``Ιούν 16``,
``Μάι.`` — on several pages. Both the sleep and the calories importers need to
turn those into real months, so the logic lives here (neither feature depends
on the other).
"""
from __future__ import annotations

import unicodedata

# Greek month abbreviations (lowercased, trailing dot stripped) → month number.
GREEK_MONTHS: dict[str, int] = {
    "ιαν": 1, "φεβ": 2, "μάρ": 3, "απρ": 4, "μάι": 5, "ιούν": 6,
    "ιούλ": 7, "αύγ": 8, "σεπ": 9, "οκτ": 10, "νοέμ": 11, "δεκ": 12,
}


def strip_accents(text: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", text)
        if unicodedata.category(c) != "Mn"
    )


# Accent-stripped fallback (e.g. "μαι" → 5) so minor variants still resolve.
_GREEK_MONTHS_PLAIN = {strip_accents(k): v for k, v in GREEK_MONTHS.items()}


def month_number(token: str) -> int | None:
    """A Greek month abbreviation ('Ιούν', 'Μάι.', 'νοεμ') → 1–12, or None."""
    t = token.rstrip(".").lower()
    return GREEK_MONTHS.get(t) or _GREEK_MONTHS_PLAIN.get(strip_accents(t))
