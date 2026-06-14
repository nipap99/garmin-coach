"""Automated daily sleep sync — run by Windows at logon (HKCU\\...\\Run).

Two cadences, one browser session:
- Nightly detail (1-day view): at most once per calendar day.
- Weekly trend  (1-year view): refreshed every WEEKLY_INTERVAL_DAYS, because the
  nightly export lacks sleep-need and bed/wake times, so the weekly file is the
  only way to keep those charts current.

State (data/sleep_sync_state.json) remembers the last nightly date and the last
weekly refresh date. A view is only marked done once its import succeeds, so a
failed run simply retries next time. Logs to data/sleep_sync.log.
"""
from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path

from backend import config, db, garmin_playwright, sleep_importer

WEEKLY_INTERVAL_DAYS = 14

STATE_FILE: Path = config.PROJECT_ROOT / "data" / "sleep_sync_state.json"
LOG_FILE: Path = config.PROJECT_ROOT / "data" / "sleep_sync.log"

logging.basicConfig(
    filename=str(LOG_FILE),
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger("daily_sleep_sync")


def _load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            return {}
    return {}


def _save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state))


def _weekly_due(state: dict) -> bool:
    last = state.get("last_weekly_sync")
    if not last:
        return True
    try:
        return (date.today() - date.fromisoformat(last)).days >= WEEKLY_INTERVAL_DAYS
    except Exception:
        return True


def main() -> int:
    state = _load_state()
    today = date.today().isoformat()

    night_due = state.get("last_sync_date") != today
    weekly_due = _weekly_due(state)

    views: list[str] = []
    if night_due:
        views.append("night")
    if weekly_due:
        views.append("weekly")

    if not views:
        logger.info("Nothing due today — skipping.")
        return 0

    try:
        saved = garmin_playwright.export_sleep_views(views)
    except Exception as exc:  # noqa: BLE001 — log and retry next trigger
        logger.exception("Sleep export failed: %s", exc)
        return 1

    if "night" in saved:
        try:
            night = sleep_importer.import_sleep_day_csv(saved["night"])
            state["last_sync_date"] = today
            state["last_night"] = night
            logger.info("Synced sleep night %s (nights: %d)", night, db.count_sleep_nights())
        except Exception:
            logger.exception("Nightly import failed")

    if "weekly" in saved:
        try:
            n = sleep_importer.import_sleep_csv(saved["weekly"])
            state["last_weekly_sync"] = today
            logger.info("Refreshed weekly sleep (%d weeks, total: %d)",
                        n, db.count_sleep_weeks())
        except Exception:
            logger.exception("Weekly import failed")

    _save_state(state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
