"""CSV importer for Garmin Connect's activity export.

Garmin Connect's activities page has an "Export CSV" button that downloads
one row per activity. This module parses that export and upserts running
activities into the local DB.

**Multi-locale support.** Garmin localizes the CSV: column headers, activity
type names, decimal separators, delimiters, and time formats all change with
the user's Garmin Connect language setting. This importer recognizes:

- English exports (commas, English headers)
- Greek exports (semicolons, Greek headers, M:SS-as-time-of-day with πμ/μμ
  AM/PM markers, distance values where the comma decimal got stripped to a
  bare integer encoding 0.01 km units)

**Limitations vs the Garmin API path:**
- No ``activityId`` in CSV — we synthesize stable negative IDs by hashing
  (date, distance, duration). Re-importing is idempotent.
- Synthetic IDs are negative to distinguish from real Garmin activityIds.
- No HR zone data, no per-split metrics — only the activity-list view.
- VO2max is usually absent from CSV exports.

**CLI usage:**

    .venv\\Scripts\\python.exe -m backend.csv_importer path\\to\\Activities.csv

**Programmatic usage:**

    from backend.csv_importer import import_csv_file
    n = import_csv_file("Activities.csv")
"""
from __future__ import annotations

import csv
import hashlib
import io
import logging
import re
import sys
from pathlib import Path
from typing import Any

from . import db

logger = logging.getLogger(__name__)


# ----------------------------- Header aliases -----------------------------

# Normalized header name -> internal field name. Normalization strips
# parenthesized units, ® symbols, lowercases, and collapses whitespace.
HEADER_ALIASES: dict[str, str] = {
    # English (Garmin Connect default)
    "activity type": "activity_type",
    "date": "start_local",
    "title": "name",
    "distance": "distance_raw",
    "time": "duration_raw",
    "moving time": "moving_time_raw",
    "elapsed time": "elapsed_time_raw",
    "avg hr": "avg_hr",
    "average hr": "avg_hr",
    "avg heart rate": "avg_hr",
    "max hr": "max_hr",
    "max heart rate": "max_hr",
    "avg pace": "avg_pace_raw",
    "average pace": "avg_pace_raw",
    "avg speed": "avg_speed_raw",
    "average speed": "avg_speed_raw",
    "calories": "calories",
    "total ascent": "elevation_gain_m",
    "ascent": "elevation_gain_m",
    "elevation gain": "elevation_gain_m",
    # Greek (Ελληνικά)
    "τύπος δραστηριότητας": "activity_type",
    "ημερομηνία": "start_local",
    "τίτλος": "name",
    "απόσταση": "distance_raw",
    "ώρα": "duration_raw",
    "χρόνος μετακίνησης": "moving_time_raw",
    "χρόνος που πέρασε": "elapsed_time_raw",
    "μέσοι κπ": "avg_hr",
    "μέγιστοι κπ": "max_hr",
    "μέσος ρυθμός": "avg_pace_raw",
    "μέση ταχύτητα": "avg_speed_raw",
    "θερμίδες": "calories",
    "συνολική άνοδος": "elevation_gain_m",
}


# Activity type values across locales. Normalized (lowercase) match.
RUNNING_TYPES = frozenset(
    {
        # English
        "running",
        "treadmill running",
        "trail running",
        "track running",
        "indoor running",
        "street running",
        "virtual running",
        # Greek
        "τρέξιμο",
        "τρέξιμο σε διάδρομο",
        "τρέξιμο σε δάπεδο",
        "τρέξιμο σε εξωτερικό χώρο",
        "τρέξιμο σε στίβο",
        "τρέξιμο σε μονοπάτι",
    }
)

CYCLING_TYPES = frozenset(
    {
        # English
        "cycling",
        "road cycling",
        "indoor cycling",
        "virtual cycling",
        "mountain biking",
        "gravel/unpaved cycling",
        "cyclocross",
        "track cycling",
        "e-biking",
        "bmx",
        # Greek
        "ποδηλασία",
        "ποδηλασία σε εσωτερικό χώρο",
        "ποδηλασία δρόμου",
        "ορεινή ποδηλασία",
        "εικονική ποδηλασία",
    }
)


def _activity_category(activity_type: str | None) -> str | None:
    """Map a raw activity-type label to ``'running'``, ``'cycling'``, or None.

    Tries an exact (normalized) match first, then a keyword fallback so we
    catch localized variants we did not enumerate (e.g. "Ποδηλασία σε ...").
    Returns None for activity types we do not track (swims, walks, etc.).
    """
    if not activity_type:
        return None
    t = activity_type.strip().lower()
    if t in RUNNING_TYPES:
        return "running"
    if t in CYCLING_TYPES:
        return "cycling"
    # Keyword fallback for unlisted localized variants
    if "τρέξιμο" in t or "running" in t or "run" == t:
        return "running"
    if "ποδηλασ" in t or "cycling" in t or "biking" in t or "bike" in t:
        return "cycling"
    return None


# ------------------------------ Detection ---------------------------------


def _detect_delimiter(sample: str) -> str:
    """Detect ``,`` vs ``;`` from a small sample. Defaults to ``,``."""
    try:
        return csv.Sniffer().sniff(sample, delimiters=",;\t").delimiter
    except csv.Error:
        # Fallback: pick whichever appears more on the first line
        first_line = sample.split("\n", 1)[0]
        return ";" if first_line.count(";") > first_line.count(",") else ","


def _is_greek_locale(headers: list[str]) -> bool:
    """True if any header contains a Greek character."""
    return any(
        any("Ͱ" <= ch <= "Ͽ" for ch in h) for h in headers
    )


def _detect_excel_greek(
    rows: list[dict[str, str]],
    header_info: dict[str, tuple[str, str | None]],
) -> bool:
    """Detect the *number/date* format from the actual data, not the headers.

    Garmin exports come in two shapes:

    * **Raw export** — period decimals (``14.32``), ISO dates
      (``2026-06-11 17:37:51``), clean ``HH:MM:SS`` times. Headers may still be
      Greek, so header language is NOT a reliable signal.
    * **Greek-Excel re-save** — comma decimals (``14,32``), ``d/m/yyyy`` dates,
      and times reformatted as time-of-day with ``πμ/μμ`` markers.

    Returns True only for the Excel-re-saved shape.
    """
    date_hdr = next(
        (h for h, (k, _) in header_info.items() if k == "start_local"), None
    )
    time_hdrs = [
        h for h, (k, _) in header_info.items()
        if k in ("duration_raw", "avg_pace_raw", "moving_time_raw", "elapsed_time_raw")
    ]
    for row in rows:
        # Greek letters in a time/pace field ⇒ Excel reformatted it (πμ/μμ)
        for h in time_hdrs:
            v = row.get(h) or ""
            if any(ch in v for ch in ("π", "μ", "Π", "Μ")):
                return True
        if date_hdr:
            d = (row.get(date_hdr) or "").strip()
            if re.match(r"^\d{4}-\d{2}-\d{2}", d):
                return False   # ISO date ⇒ raw export
            if re.match(r"^\d{1,2}/\d{1,2}/\d{4}", d):
                return True    # d/m/yyyy ⇒ Excel re-save
    return False               # default: assume raw/period format


# --------------------------- Parsing helpers ------------------------------


def _normalize_header(h: str) -> str:
    """Lowercase + strip parenthesized units + drop ® + collapse whitespace."""
    h = re.sub(r"\([^)]*\)", "", h)
    h = h.replace("®", "").replace("™", "")
    h = h.strip().lower()
    h = re.sub(r"\s+", " ", h)
    return h


def _header_unit(h: str) -> str | None:
    """Extract ``km`` / ``mi`` from a header like ``Distance (km)``."""
    m = re.search(r"\(([^)]+)\)", h)
    return m.group(1).strip().lower() if m else None


def _parse_float(text: str | None, *, greek: bool = False) -> float | None:
    """Parse a number. In Greek mode, comma is the decimal separator."""
    if not text:
        return None
    text = text.strip()
    if text in ("--", "-", ""):
        return None
    if greek:
        # In Greek locale, comma = decimal, dot = thousands separator
        text = text.replace(".", "").replace(",", ".")
    else:
        text = text.replace(",", "")
    try:
        return float(text)
    except ValueError:
        return None


def _parse_int(text: str | None, *, greek: bool = False) -> int | None:
    v = _parse_float(text, greek=greek)
    return int(v) if v is not None else None


def _parse_duration_english(text: str | None) -> float | None:
    """Parse ``HH:MM:SS`` / ``MM:SS`` / seconds into minutes (English locale)."""
    if not text:
        return None
    text = text.strip().replace(",", ".")
    if text in ("--", "-", ""):
        return None
    if ":" in text:
        try:
            parts = [float(p) for p in text.split(":")]
        except ValueError:
            return None
        if len(parts) == 2:
            seconds = parts[0] * 60 + parts[1]
        elif len(parts) == 3:
            seconds = parts[0] * 3600 + parts[1] * 60 + parts[2]
        else:
            return None
        return round(seconds / 60, 3)
    try:
        return round(float(text) / 60, 3)
    except ValueError:
        return None


def _parse_duration_greek(text: str | None) -> float | None:
    """Parse Greek-localized duration where Excel reformatted M:SS as time-of-day.

    Examples:
      ``12:05:05 πμ``  → 12 → 0 in 24h → 0h 5m 5s  = 5.083 min
      ``1:23:43 πμ``   → 1h 23m 43s                = 83.717 min
      ``12:27:19 πμ``  → 12 → 0 → 0h 27m 19s       = 27.317 min
    """
    if not text:
        return None
    text = text.strip()
    if text in ("--", "-", ""):
        return None
    # Strip the πμ/μμ AM/PM suffix
    is_pm = False
    is_am = False
    if text.endswith(" πμ") or text.endswith(" Πμ") or text.endswith(" ΠΜ"):
        is_am = True
        text = text[:-3].strip()
    elif text.endswith(" μμ") or text.endswith(" Μμ") or text.endswith(" ΜΜ"):
        is_pm = True
        text = text[:-3].strip()
    parts = text.split(":")
    if len(parts) < 2 or len(parts) > 3:
        return _parse_duration_english(text)  # fall back
    try:
        nums = [int(p) for p in parts]
    except ValueError:
        return None
    if len(nums) == 2:
        h, m, s = nums[0], nums[1], 0
    else:
        h, m, s = nums
    # 12-hour clock interpretation:
    # 12 πμ = 0 (midnight); 1-11 πμ = 1-11; 12 μμ = 12 (noon); 1-11 μμ = 13-23.
    if is_am and h == 12:
        h = 0
    elif is_pm and h != 12:
        h = h + 12
    seconds = h * 3600 + m * 60 + s
    return round(seconds / 60, 3)


def _parse_pace_english(text: str | None) -> float | None:
    """Parse ``M:SS`` pace into float minutes."""
    if not text:
        return None
    text = text.strip()
    if text in ("--", "-", ""):
        return None
    if ":" in text:
        parts = text.split(":")
        try:
            nums = [float(p) for p in parts]
        except ValueError:
            return None
        if len(nums) == 2:
            return round(nums[0] + nums[1] / 60, 3)
    try:
        return float(text)
    except ValueError:
        return None


def _parse_pace_greek(text: str | None) -> float | None:
    """Parse Greek pace where Excel reformatted M:SS as ``M:SS:00 πμ``.

    The first two numeric fields are minutes:seconds; the third ``:00`` and
    the πμ/μμ suffix are Excel artifacts.
    """
    if not text:
        return None
    text = text.strip()
    if text in ("--", "-", ""):
        return None
    for suf in (" πμ", " Πμ", " ΠΜ", " μμ", " Μμ", " ΜΜ"):
        if text.endswith(suf):
            text = text[: -len(suf)].strip()
            break
    parts = text.split(":")
    try:
        nums = [int(p) for p in parts[:2]]
    except (ValueError, IndexError):
        return None
    if len(nums) < 2:
        return None
    return round(nums[0] + nums[1] / 60, 3)


def _parse_greek_date(text: str | None) -> str:
    """Convert ``d/m/yyyy h:mm`` to ISO ``yyyy-mm-dd hh:mm:ss``.

    Returns the original text if parsing fails (so we don't lose the row).
    """
    if not text:
        return ""
    text = text.strip()
    m = re.match(
        r"^\s*(\d{1,2})/(\d{1,2})/(\d{4})\s+(\d{1,2}):(\d{2})(?::(\d{2}))?\s*$",
        text,
    )
    if not m:
        return text
    day, month, year, hour, minute, second = m.groups()
    return (
        f"{year}-{int(month):02d}-{int(day):02d} "
        f"{int(hour):02d}:{int(minute):02d}:{int(second or 0):02d}"
    )


def _synthetic_id(
    date_str: str,
    distance_km: float | None,
    duration_min: float | None,
) -> int:
    """Deterministic negative id from row content."""
    payload = f"{date_str}|{distance_km}|{duration_min}".encode()
    h = hashlib.sha256(payload).hexdigest()[:15]  # 60 bits, fits in signed 64-bit
    return -int(h, 16)


# --------------------------------- Core ------------------------------------


def parse_rows(
    rows: list[dict[str, str]],
    headers: list[str],
) -> list[dict[str, Any]]:
    """Convert raw CSV rows into our activity summary dicts.

    Handles both Garmin export shapes (raw period-decimal/ISO and the
    Greek-Excel comma-decimal re-save) by detecting the number/date format
    from the actual data. Returns running and cycling activities, each tagged
    with its ``activity_type``; other sports are silently skipped.
    """
    # Map raw header → (normalized field name, unit hint)
    header_info: dict[str, tuple[str, str | None]] = {}
    for h in headers:
        norm = _normalize_header(h)
        if norm in HEADER_ALIASES:
            header_info[h] = (HEADER_ALIASES[norm], _header_unit(h))

    # Detect the number/date format from the data, not the header language.
    excel_greek = _detect_excel_greek(rows, header_info)

    distance_header = next(
        (h for h, (k, _) in header_info.items() if k == "distance_raw"),
        None,
    )
    distance_unit = (header_info[distance_header][1] if distance_header else None) or "km"
    distance_is_km = "km" in distance_unit

    parse_duration = _parse_duration_greek if excel_greek else _parse_duration_english
    parse_pace = _parse_pace_greek if excel_greek else _parse_pace_english

    results: list[dict[str, Any]] = []
    skipped_other = 0
    skipped_invalid = 0

    for row in rows:
        norm_row: dict[str, str] = {}
        for raw_header, value in row.items():
            info = header_info.get(raw_header)
            if info:
                norm_row[info[0]] = (value or "").strip()

        category = _activity_category(norm_row.get("activity_type"))
        if category is None:
            skipped_other += 1
            continue

        raw_date = norm_row.get("start_local") or ""
        if not raw_date:
            skipped_invalid += 1
            continue
        start_local = _parse_greek_date(raw_date) if excel_greek else raw_date

        distance_raw = _parse_float(norm_row.get("distance_raw"), greek=excel_greek)
        duration_min = (
            parse_duration(norm_row.get("duration_raw"))
            or parse_duration(norm_row.get("moving_time_raw"))
            or parse_duration(norm_row.get("elapsed_time_raw"))
        )
        pace = parse_pace(norm_row.get("avg_pace_raw"))
        speed = _parse_float(norm_row.get("avg_speed_raw"), greek=excel_greek)

        # Distance unit resolution
        if distance_raw is None:
            distance_km: float | None = None
        elif excel_greek and "," not in (norm_row.get("distance_raw") or ""):
            # Greek export with stripped decimals: cross-check pace × duration.
            # If raw / 100 matches expected_km better, use that (most common).
            if pace and duration_min:
                expected_km = duration_min / pace
                cand_div100 = distance_raw / 100
                if expected_km > 0:
                    err_raw = abs(distance_raw - expected_km) / expected_km
                    err_div100 = abs(cand_div100 - expected_km) / expected_km
                    distance_km = (
                        cand_div100 if err_div100 < err_raw else distance_raw
                    )
                else:
                    distance_km = cand_div100
            else:
                # No pace/duration to cross-check; assume ÷100 (matches the
                # Greek-locale Excel-stripped format we've seen)
                distance_km = distance_raw / 100
            distance_km = round(distance_km, 3)
        elif distance_is_km:
            distance_km = round(distance_raw, 3)
        else:
            distance_km = round(distance_raw * 1.609344, 3)

        # Convert imperial units to metric when the export is in miles/mph
        if pace is not None and not distance_is_km and not excel_greek:
            pace = round(pace / 1.609344, 3)        # min/mi → min/km
        if speed is not None:
            if not distance_is_km:
                speed = round(speed * 1.609344, 2)  # mph → km/h
            else:
                speed = round(speed, 2)

        summary = {
            "id": _synthetic_id(start_local, distance_km, duration_min),
            "name": norm_row.get("name") or f"Imported {category}",
            "start_local": start_local,
            "distance_km": distance_km,
            "duration_min": duration_min,
            "avg_pace_min_per_km": pace,    # running metric (None for cycling)
            "avg_speed_kmh": speed,         # cycling metric (None for running)
            "avg_hr": _parse_int(norm_row.get("avg_hr"), greek=excel_greek),
            "max_hr": _parse_int(norm_row.get("max_hr"), greek=excel_greek),
            "vo2max": None,
            "elevation_gain_m": _parse_float(norm_row.get("elevation_gain_m"), greek=excel_greek),
            "calories": _parse_int(norm_row.get("calories"), greek=excel_greek),
            "activity_type": category,
        }
        results.append(summary)

    if skipped_other:
        logger.info("Skipped %d non-running/cycling activities", skipped_other)
    if skipped_invalid:
        logger.info("Skipped %d rows missing required fields", skipped_invalid)
    return results


# ---------------------------- File reading --------------------------------

_CSV_ENCODINGS = (
    "utf-8-sig",
    "utf-8",
    "utf-16",
    "utf-16-le",
    "windows-1253",  # Greek
    "cp1252",        # Western European
    "iso-8859-7",    # older Greek
    "latin-1",       # never fails to decode
)


def _read_csv(path: Path) -> tuple[list[str], list[dict[str, str]], str]:
    """Read a CSV, trying encodings + delimiters. Returns headers, rows, encoding."""
    last_err: Exception | None = None
    for enc in _CSV_ENCODINGS:
        try:
            with path.open("r", encoding=enc, newline="") as f:
                content = f.read()
        except (UnicodeDecodeError, UnicodeError) as e:
            last_err = e
            continue

        # Detect delimiter from a sample of the content
        sample = content[:4096]
        delimiter = _detect_delimiter(sample)
        reader = csv.DictReader(io.StringIO(content), delimiter=delimiter)
        headers = reader.fieldnames or []
        rows = list(reader)
        if headers and len(headers) > 1:
            logger.info(
                "Read %s as %s, delimiter=%r, %d rows",
                path.name,
                enc,
                delimiter,
                len(rows),
            )
            return headers, rows, enc

    raise UnicodeDecodeError(
        "csv",
        b"",
        0,
        1,
        f"Could not decode {path} with any of: {', '.join(_CSV_ENCODINGS)}. "
        f"Last error: {last_err}",
    )


def import_csv_file(path: str | Path) -> int:
    """Parse a Garmin CSV file and upsert running/cycling activities into the DB.

    Returns the number of activities written. Re-importing the same CSV is
    idempotent.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"CSV file not found: {path}")

    headers, rows, encoding = _read_csv(path)
    if not headers:
        raise ValueError(f"No headers found in {path}")

    summaries = parse_rows(rows, headers)
    for s in summaries:
        db.upsert_activity(
            s,
            {"_source": "csv_import", "_file": path.name, "_encoding": encoding},
        )

    logger.info("Imported %d activities from %s", len(summaries), path)
    return len(summaries)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )
    argv = list(argv if argv is not None else sys.argv[1:])
    if not argv:
        print(
            "Usage: python -m backend.csv_importer <path-to-garmin-csv>",
            file=sys.stderr,
        )
        return 2
    csv_path = argv[0]
    try:
        n = import_csv_file(csv_path)
        total = db.count_activities()
    except Exception as e:  # noqa: BLE001
        logger.exception("Import failed")
        print(f"ERROR: {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    print(f"\nImported {n} activities from {csv_path}")
    print(f"Database now contains {total} total activities.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
