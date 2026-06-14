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

from .. import csv_importer, db, garmin_playwright

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/sync", response_class=HTMLResponse)
def trigger_sync() -> HTMLResponse:
    """Export running + cycling from Garmin (via Playwright) and import them.

    Both sports are exported in one browser session, then imported. Returns a
    status banner reporting how many new workouts were added.
    """
    try:
        count_before = db.count_activities()
        csv_paths = garmin_playwright.export_activities()  # running + cycling
        for path in csv_paths:
            csv_importer.import_csv_file(path)
        new_count = db.count_activities() - count_before
        if new_count == 0:
            msg = "Sync complete — no new workouts (all already in your database)."
        elif new_count == 1:
            msg = "Added 1 new workout from Garmin (running + cycling)."
        else:
            msg = f"Added {new_count} new workouts from Garmin (running + cycling)."
        banner = f'<div class="banner success">{msg}</div>'
    except Exception as e:  # noqa: BLE001 — we want to show any error to the user
        logger.exception("Sync failed")
        banner = f'<div class="banner error">Sync failed: {type(e).__name__}: {e}</div>'

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
