"""Automated daily sleep sync — run by Windows Task Scheduler at logon / unlock.

Guards so it runs at most once per calendar day: the first trigger of the day
that succeeds records the date; later triggers that day exit immediately. A
failed run is NOT recorded, so it retries on the next trigger (e.g. once your
Garmin session is valid again). Logs to data/sleep_sync.log.
"""
from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path

from backend import config, db, garmin_playwright, sleep_importer

STATE_FILE: Path = config.PROJECT_ROOT / "data" / "sleep_sync_state.json"
LOG_FILE: Path = config.PROJECT_ROOT / "data" / "sleep_sync.log"

logging.basicConfig(
    filename=str(LOG_FILE),
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger("daily_sleep_sync")


def _already_synced_today() -> bool:
    if not STATE_FILE.exists():
        return False
    try:
        last = json.loads(STATE_FILE.read_text()).get("last_sync_date")
    except Exception:
        return False
    return last == date.today().isoformat()


def _mark_synced(night: str) -> None:
    STATE_FILE.write_text(json.dumps({
        "last_sync_date": date.today().isoformat(),
        "last_night": night,
    }))


def main() -> int:
    if _already_synced_today():
        logger.info("Already synced today — skipping.")
        return 0
    try:
        path = garmin_playwright.export_sleep_night()
        night = sleep_importer.import_sleep_day_csv(path)
        _mark_synced(night)
        logger.info("Synced sleep night %s (total nights: %d)",
                    night, db.count_sleep_nights())
        return 0
    except Exception as exc:  # noqa: BLE001 — log and exit non-fatally; will retry
        logger.exception("Daily sleep sync failed: %s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
