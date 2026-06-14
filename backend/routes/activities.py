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

from .. import csv_importer, db, garmin_playwright, sleep_importer

logger = logging.getLogger(__name__)

router = APIRouter()


def _sync_and_import(sports: list[str]) -> int:
    """Export the given sports from Garmin, import each CSV, return new-row count."""
    count_before = db.count_activities()
    for path in garmin_playwright.export_activities(sports):
        csv_importer.import_csv_file(path)
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
    """Export the most recent night's sleep CSV from Garmin and import it.

    Used by the 'Sync Sleep' button on the Sleep tab.
    """
    try:
        before = db.count_sleep_nights()
        path = garmin_playwright.export_sleep_night()
        night = sleep_importer.import_sleep_day_csv(path)
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
