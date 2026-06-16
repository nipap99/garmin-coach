"""Parse + store the daily calories scraped from Garmin's calories page.

The calories page has no CSV export, so ``garmin_playwright.scrape_calories``
reads the 7-day table's rows straight from the page. This module turns those
raw cells into rows and upserts them into the ``calories`` table (keyed on the
day, so the same date is overwritten on re-scrape).

It also reports which days were brand new vs. already-saved-and-overwritten —
the "verify what's already there" check.

CLI:  .venv\\Scripts\\python.exe -m backend.calories_importer
"""
from __future__ import annotations

import logging
import re
import sys
from datetime import date
from typing import Any

from . import db
from .greek_dates import month_number

logger = logging.getLogger(__name__)


def _parse_int(text: str | None) -> int | None:
    """'1,941' → 1941. Calories are whole numbers, so drop any thousands sep."""
    if not text:
        return None
    cleaned = text.strip().replace(",", "").replace(".", "")
    m = re.search(r"-?\d+", cleaned)
    return int(m.group()) if m else None


def _parse_date(text: str, ref: date) -> str | None:
    """'Ιούν 16' → ISO date. No year is shown, so infer it: pick the year that
    puts the date on/just-before the reference (today), rolling back across the
    new year if needed (e.g. a 'Δεκ' row seen in early January)."""
    m = re.match(r"\s*([^\d\s]+)\.?\s+(\d{1,2})", text)
    if not m:
        return None
    month = month_number(m.group(1))
    if month is None:
        return None
    day = int(m.group(2))
    try:
        d = date(ref.year, month, day)
    except ValueError:
        return None
    if (d - ref).days > 2:          # would be in the future → previous year
        try:
            d = date(ref.year - 1, month, day)
        except ValueError:
            return None
    return d.isoformat()


def import_calories_rows(rows: list[list[str]]) -> dict[str, Any]:
    """Upsert scraped rows. Returns {new, updated, skipped} for verification."""
    ref = date.today()
    existing = set(db.get_calorie_dates())
    new: list[str] = []
    updated: list[str] = []
    skipped = 0

    for cells in rows:
        if len(cells) < 4:
            skipped += 1
            continue
        d = _parse_date(cells[0], ref)
        if not d:
            logger.info("Skipping unparseable calories date: %r", cells[0])
            skipped += 1
            continue
        db.upsert_calories_day({
            "day_date":     d,
            "activity_cal": _parse_int(cells[1]),
            "resting_cal":  _parse_int(cells[2]),
            "total_cal":    _parse_int(cells[3]),
        })
        (updated if d in existing else new).append(d)

    return {"new": sorted(new), "updated": sorted(updated), "skipped": skipped}


def scrape_and_import() -> dict[str, Any]:
    """Scrape the calories page and store the result. Returns the summary."""
    from . import garmin_playwright
    rows = garmin_playwright.scrape_calories()
    return import_calories_rows(rows)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    try:
        summary = scrape_and_import()
    except Exception as e:  # noqa: BLE001
        logger.exception("Calories scrape failed")
        print(f"ERROR: {type(e).__name__}: {e}", file=sys.stderr)
        return 1

    print(f"\nNew days added:        {summary['new']}")
    print(f"Existing days updated: {summary['updated']}  (same date overwritten)")
    print(f"Rows skipped:          {summary['skipped']}")
    print(f"\nAll days now stored in 'calories' ({db.count_calories_days()} total):")
    for d in db.get_calorie_dates():
        print(f"  {d}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
