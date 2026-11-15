"""Routes for triggering a Garmin sync and importing CSV files.

Both routes return a small HTML status banner that HTMX swaps into the
dashboard's #sync-status slot (see frontend/index.html).
"""
from __future__ import annotations

import logging
import tempfile
from pathlib import Path

from fastapi import APIRouter, File, UploadFile
from fastapi.responses import HTMLResponse

from .. import csv_importer, db, garmin_api

logger = logging.getLogger(__name__)

router = APIRouter()


def _sync_and_import(sports: list[str]) -> int:
    """Fetch the given sports from Garmin's API and upsert them, returning the
    new-row count. Enriches the most recent run with today's VO2max (the value
    the activity list omits), which COALESCE folds into the existing row."""
    types = set(sports)
    items = garmin_api.fetch_activities(limit=30, types=types)

    if "running" in types:
        runs = [s for s, _ in items if s["activity_type"] == "running"]
        if runs:
            vo2 = garmin_api.current_vo2max()
            if vo2 is not None:
                newest = max(runs, key=lambda s: s.get("start_local") or "")
                newest["vo2max"] = vo2

    count_before = db.count_activities()
    for summary, raw in items:
        db.upsert_activity(summary, raw)
    return db.count_activities() - count_before


@router.post("/sync", response_class=HTMLResponse)
def trigger_sync() -> HTMLResponse:
    """Sync both running and cycling in one browser session (slower).

    Used by the 'Sync from Garmin' button on the Cycling tab.
    """
    try:
        n = _sync_and_import(["running", "cycling"])
        if n == 0:
            msg = "Sync complete — no new workouts (all already in your database)."
        elif n == 1:
            msg = "Added 1 new workout from Garmin (running + cycling)."
        else:
            msg = f"Added {n} new workouts from Garmin (running + cycling)."
        banner = f'<div class="banner success">{msg}</div>'
    except Exception as e:  # noqa: BLE001 — we want to show any error to the user
        logger.exception("Sync failed")
        banner = f'<div class="banner error">Sync failed: {type(e).__name__}: {e}</div>'

    return HTMLResponse(banner)


@router.post("/sync/running", response_class=HTMLResponse)
def trigger_sync_running() -> HTMLResponse:
    """Sync running only — faster, skips the cycling export.

    Used by the 'Sync Running' button on the Running tab.
    """
    try:
        n = _sync_and_import(["running"])
        if n == 0:
            msg = "Sync complete — no new runs (all already in your database)."
        elif n == 1:
            msg = "Added 1 new run from Garmin."
        else:
            msg = f"Added {n} new runs from Garmin."
        banner = f'<div class="banner success">{msg}</div>'
    except Exception as e:  # noqa: BLE001
        logger.exception("Running sync failed")
        banner = f'<div class="banner error">Sync failed: {type(e).__name__}: {e}</div>'

    return HTMLResponse(banner)


@router.post("/sync/sleep", response_class=HTMLResponse)
def trigger_sync_sleep() -> HTMLResponse:
    """Fetch the most recent night's sleep from Garmin's JSON API and store it.

    Reads straight from ``connectapi.garmin.com`` (not the browser), so it works
    even when Garmin's sleep web page is throwing its "Looks Like We Crashed"
    render error. Used by the 'Sync Sleep' button on the Sleep tab.
    """
    try:
        before = db.count_sleep_nights()
        rec = garmin_api.fetch_latest_sleep_night()
        if rec is None:
            return HTMLResponse(
                '<div class="banner error">No recent sleep found on Garmin — '
                'your watch may not have synced its latest night yet.</div>'
            )
        db.upsert_sleep_night(rec)
        night = rec["night_date"]
        added = db.count_sleep_nights() - before
        if added >= 1:
            msg = f"Added sleep for {night}."
        else:
            msg = f"Sleep for {night} refreshed (already tracked)."
        banner = f'<div class="banner success">{msg}</div>'
    except Exception as e:  # noqa: BLE001
        logger.exception("Sleep sync failed")
        banner = f'<div class="banner error">Sleep sync failed: {type(e).__name__}: {e}</div>'

    return HTMLResponse(banner)


@router.post("/sync/sleep/weekly", response_class=HTMLResponse)
def trigger_sync_sleep_weekly() -> HTMLResponse:
    """Backfill recent per-night sleep history from Garmin's JSON API.

    Pulls the last ~60 nights straight from ``connectapi.garmin.com`` and upserts
    each into ``sleep_nights`` — reliable, and richer than the old weekly CSV.
    Used by the 'Sync Sleep History' button on the Sleep tab.
    """
    try:
        before = db.count_sleep_nights()
        nights = garmin_api.fetch_recent_sleep_nights(days=60)
        for rec in nights:
            db.upsert_sleep_night(rec)
        added = db.count_sleep_nights() - before
        msg = f"Synced sleep history — {len(nights)} night(s) read, {added} new."
        banner = f'<div class="banner success">{msg}</div>'
    except Exception as e:  # noqa: BLE001
        logger.exception("Sleep history sync failed")
        banner = f'<div class="banner error">Sleep history sync failed: {type(e).__name__}: {e}</div>'

    return HTMLResponse(banner)


@router.post("/sync/calories", response_class=HTMLResponse)
def trigger_sync_calories() -> HTMLResponse:
    """Scrape the last 7 days of calories from Garmin and store them.

    Used by the 'Sync Calories' button on the Calories tab.
    """
    try:
        existing = set(db.get_calorie_dates())
        days = garmin_api.fetch_calories_days(days=7)
        new = updated = 0
        for rec in days:
            db.upsert_calories_day(rec)
            if rec["day_date"] in existing:
                updated += 1
            else:
                new += 1
        msg = f"Synced calories — {new} new day(s), {updated} refreshed."
        banner = f'<div class="banner success">{msg}</div>'
    except Exception as e:  # noqa: BLE001
        logger.exception("Calories sync failed")
        banner = f'<div class="banner error">Calories sync failed: {type(e).__name__}: {e}</div>'

    return HTMLResponse(banner)


@router.post("/import/csv", response_class=HTMLResponse)
async def import_csv(file: UploadFile = File(...)) -> HTMLResponse:
    """Upload a Garmin CSV export and import the running activities into the DB.

    Returns a status banner reporting how many new workouts were added.
    """
    if not file.filename or not file.filename.lower().endswith(".csv"):
        return HTMLResponse(
            '<div class="banner error">Please upload a .csv file.</div>'
        )

    content = await file.read()
    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            suffix=".csv",
            delete=False,
        ) as tmp:
            tmp.write(content)
            tmp_path = Path(tmp.name)

        count_before = db.count_activities()
        csv_importer.import_csv_file(tmp_path)
        new_count = db.count_activities() - count_before
        if new_count == 0:
            msg = f"No new workouts — all runs from {file.filename} already in your database."
        elif new_count == 1:
            msg = f"Added 1 new workout from {file.filename}."
        else:
            msg = f"Added {new_count} new workouts from {file.filename}."
        banner = f'<div class="banner success">{msg}</div>'
    except Exception as e:  # noqa: BLE001
        logger.exception("CSV import failed")
        banner = (
            f'<div class="banner error">Import failed: '
            f"{type(e).__name__}: {e}</div>"
        )
    finally:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)

    return HTMLResponse(banner)
