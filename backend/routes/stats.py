"""JSON endpoints that supply data to the Chart.js charts on the dashboard.

All routes are under /stats and return plain JSON (not HTML fragments).
The frontend fetches these with the native fetch() API and hands the
data to Chart.js.
"""
from __future__ import annotations

from fastapi import APIRouter, Query

from .. import db

router = APIRouter(prefix="/stats", tags=["stats"])


def _fmt_pace(pace_dec: float) -> str:
    """Convert decimal minutes to M:SS/km string."""
    mins = int(pace_dec)
    secs = round((pace_dec - mins) * 60)
    if secs == 60:
        mins += 1
        secs = 0
    return f"{mins}:{secs:02d}/km"


def _fmt_duration(duration_min: float) -> str:
    """Convert decimal minutes to H:MM:SS or M:SS string."""
    total = int(round(duration_min * 60))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


@router.get("/summary")
def summary():
    """Totals for the four dashboard stat cards."""
    return db.get_stats_summary()


@router.get("/weekly-mileage")
def weekly_mileage(weeks: int = Query(16, ge=4, le=52)):
    """Weekly km totals for the last N weeks."""
    rows = db.get_weekly_mileage(weeks=weeks)
    return {
        "labels": [r["week_start"] for r in rows],
        "values": [float(r["total_km"]) for r in rows],
        "counts": [int(r["run_count"]) for r in rows],
    }


@router.get("/pace-trend")
def pace_trend(limit: int = Query(30, ge=5, le=100)):
    """Recent run paces for the trend line chart."""
    rows = db.get_pace_trend(limit=limit)
    return {
        "labels":    [r["start_local"][:10] for r in rows],
        "values":    [round(float(r["avg_pace_min_per_km"]), 3) for r in rows],
        "distances": [round(float(r["distance_km"]), 1) for r in rows],
    }


@router.get("/vo2max")
def vo2max_trend():
    """VO2max readings over time."""
    rows = db.get_vo2max_trend()
    return {
        "labels": [r["start_local"][:10] for r in rows],
        "values": [float(r["vo2max"]) for r in rows],
    }


@router.get("/prs")
def personal_records():
    """Best (fastest) run in each standard distance bracket."""
    prs = db.get_personal_records()
    result = {}
    for dist, row in prs.items():
        pace = row.get("avg_pace_min_per_km")
        result[dist] = {
            "date":               row["start_local"][:10],
            "distance_km":        round(float(row["distance_km"]), 2),
            "duration_formatted": _fmt_duration(float(row["duration_min"])),
            "pace_formatted":     _fmt_pace(float(pace)) if pace else "—",
        }
    return result
