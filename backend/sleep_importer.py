"""Importer for Garmin Connect's weekly sleep CSV export (the 7-day view).

The file is one row per week, e.g.::

    Ημερομηνία,Μέσ.βαθμ.,Μέση τιμή ποιότητας,Μέση διάρκεια,Μέση ανάγκη ύπνου,...
    Μάι. 18-24,84,Καλό,7ώ 39λεπ.,8ώ 24λεπ.,11:44 μμ,7:27 πμ

Parsing quirks handled here:
- Greek week-range dates with optional / cross-year markers
  ("Μάι. 18-24"  →  this-year May 18 ;  "Δεκ. 29, 2025 - Ιαν. 4, 2026")
- Greek durations  "7ώ 39λεπ."  →  459 minutes
- Greek 12-hour times  "11:44 μμ" → "23:44",  "7:27 πμ" → "07:27"

CLI:  .venv\\Scripts\\python.exe -m backend.sleep_importer path\\to\\Ύπνος.csv
"""
from __future__ import annotations

import logging
import re
import sys
import unicodedata
from datetime import date, datetime
from pathlib import Path
from typing import Any

from . import db

logger = logging.getLogger(__name__)

_ENCODINGS = (
    "utf-8-sig", "utf-8", "utf-16", "windows-1253", "cp1252", "iso-8859-7", "latin-1",
)


# Greek month abbreviations (lowercased, trailing dot stripped) → month number.
GREEK_MONTHS: dict[str, int] = {
    "ιαν": 1, "φεβ": 2, "μάρ": 3, "απρ": 4, "μάι": 5, "ιούν": 6,
    "ιούλ": 7, "αύγ": 8, "σεπ": 9, "οκτ": 10, "νοέμ": 11, "δεκ": 12,
}


def _strip_accents(text: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", text)
        if unicodedata.category(c) != "Mn"
    )


# Accent-stripped fallback (e.g. "μαι" → 5) so minor variants still resolve.
_GREEK_MONTHS_PLAIN = {_strip_accents(k): v for k, v in GREEK_MONTHS.items()}


# ── field parsers ────────────────────────────────────────────────────────────


def _parse_int(text: str | None) -> int | None:
    if not text:
        return None
    m = re.search(r"-?\d+", text)
    return int(m.group()) if m else None


def _parse_duration(text: str | None) -> float | None:
    """'7ώ 39λεπ.' → 459 minutes. Handles 'λεπ.' and the shorter 'λ'."""
    if not text:
        return None
    h = re.search(r"(\d+)\s*ώ", text)
    m = re.search(r"(\d+)\s*λ", text)
    if not h and not m:
        return None
    return (int(h.group(1)) if h else 0) * 60 + (int(m.group(1)) if m else 0)


def _parse_time(text: str | None) -> str | None:
    """'11:44 μμ' → '23:44' ; '7:27 πμ' → '07:27' ; '12:10 πμ' → '00:10'."""
    if not text:
        return None
    m = re.search(r"(\d{1,2}):(\d{2})", text)
    if not m:
        return None
    h, mn = int(m.group(1)), int(m.group(2))
    if "πμ" in text and h == 12:
        h = 0
    elif "μμ" in text and h != 12:
        h += 12
    return f"{h:02d}:{mn:02d}"


def _parse_week_label(label: str) -> tuple[int, int, int | None] | None:
    """Return (start_month, start_day, explicit_start_year | None) from a label.

    The start of the week is everything before the ' - ' range separator (if any).
    The year may be written only once at the end ("Μάι. 26 - Ιούν 1, 2025"), so if
    the start part has no year we fall back to a year anywhere in the label — except
    a cross-year range ("Δεκ. 29, 2025 - Ιαν. 4, 2026") where the start part's own
    year wins.
    """
    start_part = label.split(" - ", 1)[0] if " - " in label else label
    year_m = re.search(r",\s*(\d{4})", start_part) or re.search(r",\s*(\d{4})", label)
    explicit_year = int(year_m.group(1)) if year_m else None

    m = re.match(r"\s*([^\d\s]+)\.?\s+(\d{1,2})", start_part)
    if not m:
        return None
    token = m.group(1).rstrip(".").lower()
    month = GREEK_MONTHS.get(token) or _GREEK_MONTHS_PLAIN.get(_strip_accents(token))
    if month is None:
        return None
    return month, int(m.group(2)), explicit_year


# ── reading & header mapping ─────────────────────────────────────────────────


def _read_sleep_lines(path: Path) -> tuple[list[str], list[str], str]:
    """Read the sleep CSV, trying encodings. Returns (header_cols, data_lines, enc).

    The header row is comma-clean (7 columns); data rows are parsed later with a
    split-from-the-right so an unquoted comma in the date label can't shift fields.
    """
    last_err: Exception | None = None
    for enc in _ENCODINGS:
        try:
            text = path.read_text(encoding=enc)
        except (UnicodeDecodeError, UnicodeError) as e:
            last_err = e
            continue
        lines = [ln for ln in text.splitlines() if ln.strip()]
        if len(lines) >= 2 and lines[0].count(",") >= 3:
            return lines[0].split(","), lines[1:], enc
    raise UnicodeDecodeError(
        "csv", b"", 0, 1,
        f"Could not read {path} with any known encoding. Last error: {last_err}",
    )


def _field_indices(header: list[str]) -> dict[str, int]:
    """Map field names to their position within the *data* columns (header[1:])."""
    fm: dict[str, int] = {}
    for i, h in enumerate(header[1:]):
        hl = h.lower()
        if "ποιότητ" in hl or "ποιοτητ" in hl:
            fm["quality"] = i
        elif "βαθμ" in hl:
            fm["score"] = i
        elif "διάρκει" in hl or "διαρκει" in hl:
            fm["duration"] = i
        elif "ανάγκη" in hl or "αναγκη" in hl:
            fm["need"] = i
        elif ("ώρας" in hl or "ωρας" in hl) and "ύπνου" in hl:
            fm["bedtime"] = i
        elif "ξυπνήματ" in hl or "ξυπνηματ" in hl:
            fm["wake"] = i
    return fm


# ── core ─────────────────────────────────────────────────────────────────────


def _assign_years(parsed: list[dict[str, Any]]) -> None:
    """Fill ``week_start`` (ISO) on each row. ``parsed`` is newest-first (file
    order); we walk oldest→newest, carrying the year and bumping it whenever the
    month wraps backwards (Dec→Jan)."""
    year: int | None = None
    prev_month: int | None = None
    for p in reversed(parsed):
        if p["explicit_year"] is not None:
            year = p["explicit_year"]
        elif year is None:
            year = datetime.now().year  # no anchor yet — assume current year
        elif prev_month is not None and p["month_idx"] < prev_month:
            year += 1
        try:
            p["week_start"] = date(year, p["month_idx"], p["start_day"]).isoformat()
        except ValueError:
            p["week_start"] = None
        prev_month = p["month_idx"]


def parse_sleep_rows(header: list[str], data_lines: list[str]) -> list[dict[str, Any]]:
    n_data = len(header) - 1            # number of data columns after the date
    fi = _field_indices(header)

    def val(data: list[str], field: str) -> str | None:
        idx = fi.get(field)
        return data[idx] if idx is not None and idx < len(data) else None

    parsed: list[dict[str, Any]] = []
    for line in data_lines:
        parts = line.split(",")
        if len(parts) < n_data + 1:
            continue
        # Split from the right: the last n_data fields are comma-clean data;
        # everything before them is the (possibly comma-bearing) date label.
        data = parts[-n_data:]
        label = ",".join(parts[:-n_data]).strip().strip('"')
        if not label:
            continue
        wk = _parse_week_label(label)
        if wk is None:
            logger.info("Skipping unparseable week label: %r", label)
            continue
        month_idx, start_day, explicit_year = wk
        parsed.append({
            "label": label,
            "month_idx": month_idx,
            "start_day": start_day,
            "explicit_year": explicit_year,
            "avg_score":        _parse_int(val(data, "score")),
            "quality":          (val(data, "quality") or "").strip() or None,
            "avg_duration_min": _parse_duration(val(data, "duration")),
            "avg_need_min":     _parse_duration(val(data, "need")),
            "avg_bedtime":      _parse_time(val(data, "bedtime")),
            "avg_wake_time":    _parse_time(val(data, "wake")),
        })

    _assign_years(parsed)
    return parsed


def import_sleep_csv(path: str | Path) -> int:
    """Parse a Garmin weekly sleep CSV and upsert each week. Returns rows written."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Sleep CSV not found: {path}")

    header, data_lines, encoding = _read_sleep_lines(path)
    parsed = parse_sleep_rows(header, data_lines)

    n = 0
    for p in parsed:
        if not p.get("week_start"):
            continue
        db.upsert_sleep_week({
            "week_start":       p["week_start"],
            "week_label":       p["label"],
            "avg_score":        p["avg_score"],
            "quality":          p["quality"],
            "avg_duration_min": p["avg_duration_min"],
            "avg_need_min":     p["avg_need_min"],
            "avg_bedtime":      p["avg_bedtime"],
            "avg_wake_time":    p["avg_wake_time"],
        })
        n += 1

    logger.info("Imported %d sleep weeks from %s (%s)", n, path.name, encoding)
    return n


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    argv = list(argv if argv is not None else sys.argv[1:])
    if not argv:
        print("Usage: python -m backend.sleep_importer <path-to-sleep-csv>", file=sys.stderr)
        return 2
    try:
        n = import_sleep_csv(argv[0])
        total = db.count_sleep_weeks()
    except Exception as e:  # noqa: BLE001
        logger.exception("Sleep import failed")
        print(f"ERROR: {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    print(f"\nImported {n} sleep weeks from {argv[0]}")
    print(f"Database now contains {total} sleep weeks.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
