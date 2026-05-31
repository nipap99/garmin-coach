"""Routes for listing activities, triggering a Garmin sync, and importing CSV files.

The HTML fragment routes are designed to be swapped into the page by HTMX
(see frontend/index.html). They return raw HTML, not JSON.
"""
from __future__ import annotations

import logging
import tempfile
from pathlib import Path

from fastapi import APIRouter, File, Query, UploadFile
from fastapi.responses import HTMLResponse

from .. import csv_importer, db, sync as sync_module
from ..garmin_client import _format_pace

logger = logging.getLogger(__name__)

router = APIRouter()


def _render_activities_table(rows: list[dict]) -> str:
    """Render the activities table as an HTML fragment."""
    if not rows:
        return (
            '<div class="empty">'
            "No activities yet. Click <strong>Sync from Garmin</strong> "
            "above to pull your recent runs."
            "</div>"
        )

    header = (
        "<table>"
        "<thead><tr>"
        "<th>Date</th><th>Name</th><th>Distance</th>"
        "<th>Duration</th><th>Pace</th><th>Avg HR</th><th>VO2max</th>"
        "</tr></thead><tbody>"
    )
    body_rows = []
    for r in rows:
        pace = _format_pace(r.get("avg_pace_min_per_km"))
        body_rows.append(
            "<tr>"
            f"<td>{(r.get('start_local') or '')[:16]}</td>"
            f"<td>{r.get('name') or ''}</td>"
            f"<td>{r.get('distance_km') or 0:.2f} km</td>"
            f"<td>{r.get('duration_min') or 0:.1f} min</td>"
            f"<td>{pace} /km</td>"
            f"<td>{r.get('avg_hr') or '—'}</td>"
            f"<td>{r.get('vo2max') or '—'}</td>"
            "</tr>"
        )
    return header + "".join(body_rows) + "</tbody></table>"


@router.get("/activities", response_class=HTMLResponse)
def list_activities(days: int = Query(30, ge=1, le=365)) -> HTMLResponse:
    """Return an HTML fragment with recent activities."""
    rows = db.get_recent_activities(days=days)
    return HTMLResponse(_render_activities_table(rows))


@router.post("/sync", response_class=HTMLResponse)
def trigger_sync(days: int = Query(30, ge=1, le=365)) -> HTMLResponse:
    """Pull recent runs from Garmin into the local DB, then return the refreshed table.

    Returns a status banner + the refreshed activities table.
    """
    try:
        n = sync_module.sync_recent_runs(days=days)
        banner = (
            f'<div class="banner success">Synced {n} runs from Garmin '
            f"(last {days} days).</div>"
        )
    except Exception as e:  # noqa: BLE001 — we want to show any error to the user
        logger.exception("Sync failed")
        banner = (
            f'<div class="banner error">Sync failed: '
            f"{type(e).__name__}: {e}</div>"
        )

    rows = db.get_recent_activities(days=days)
    return HTMLResponse(banner + _render_activities_table(rows))


@router.post("/import/csv", response_class=HTMLResponse)
async def import_csv(file: UploadFile = File(...)) -> HTMLResponse:
    """Upload a Garmin CSV export and import the running activities into the DB.

    Returns a status banner + the refreshed activities table.
    """
    if not file.filename or not file.filename.lower().endswith(".csv"):
        return HTMLResponse(
            '<div class="banner error">Please upload a .csv file.</div>'
            + _render_activities_table(db.get_recent_activities(days=365))
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

        n = csv_importer.import_csv_file(tmp_path)
        banner = (
            f'<div class="banner success">Imported {n} running activities '
            f"from {file.filename}.</div>"
        )
    except Exception as e:  # noqa: BLE001
        logger.exception("CSV import failed")
        banner = (
            f'<div class="banner error">Import failed: '
            f"{type(e).__name__}: {e}</div>"
        )
    finally:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)

    rows = db.get_recent_activities(days=365)
    return HTMLResponse(banner + _render_activities_table(rows))
