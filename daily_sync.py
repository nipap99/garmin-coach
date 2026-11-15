"""Daily Garmin data refresh -> local DB.

Scheduled via Windows Task Scheduler ("GarminCoach Daily Sync") at 23:58 every
day, so sleep, runs, rides and calories stay current WITHOUT pressing the app's
sync buttons. It reuses the cached Garmin OAuth token (one login at most), so a
daily run does not trip Garmin's login rate limit.

Each step is isolated: if one fails (e.g. a transient Garmin outage) the others
still run, and the failure is logged. Writes data/daily_sync.log.

Manual run:  .venv\\Scripts\\python.exe daily_sync.py
"""
from __future__ import annotations

import logging
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

ROOT = Path(os.path.dirname(os.path.abspath(__file__)))
LOG_PATH = ROOT / "data" / "daily_sync.log"
LAST_OK_PATH = ROOT / "data" / "daily_sync_last_ok"
COOLDOWN_HOURS = 3   # the logon/daily triggers can both fire; don't re-sync within this window
LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s: %(message)s",
    handlers=[logging.FileHandler(LOG_PATH, encoding="utf-8"), logging.StreamHandler()],
)
logger = logging.getLogger("daily_sync")

from backend import db, garmin_api                       # noqa: E402
from backend.routes.activities import _sync_and_import   # noqa: E402


def _activities():
    """Runs + rides — the same path as the app's 'Sync from Garmin' button."""
    n = _sync_and_import(["running", "cycling"])
    logger.info("Activities (run + ride): %d new.", n)


def _sleep():
    """Last 7 nights via Garmin's JSON API (upsert catches late-finalised data)."""
    before = db.count_sleep_nights()
    nights = garmin_api.fetch_recent_sleep_nights(days=7)
    for rec in nights:
        db.upsert_sleep_night(rec)
    logger.info("Sleep: %d night(s) read, %d new.", len(nights), db.count_sleep_nights() - before)


def _calories():
    """Last 7 days of daily calories burned."""
    days = garmin_api.fetch_calories_days(days=7)
    for rec in days:
        db.upsert_calories_day(rec)
    logger.info("Calories: %d day(s) upserted.", len(days))


def _recent_success() -> bool:
    try:
        return (time.time() - float(LAST_OK_PATH.read_text().strip())) < COOLDOWN_HOURS * 3600
    except Exception:
        return False


def main():
    if _recent_success():
        logger.info("Daily Garmin sync skipped — already synced within the last %d h.", COOLDOWN_HOURS)
        return 0
    logger.info("=== Daily Garmin sync starting ===")
    ok = True
    for name, fn in (("activities", _activities), ("sleep", _sleep), ("calories", _calories)):
        try:
            fn()
        except Exception:
            ok = False
            logger.exception("Daily sync step failed: %s", name)
    if ok:
        try:
            LAST_OK_PATH.write_text(str(time.time()))
        except Exception:
            pass
    logger.info("=== Daily Garmin sync %s ===", "complete" if ok else "finished WITH ERRORS")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
