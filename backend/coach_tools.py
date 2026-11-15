"""Data tools Claude uses through the MCP server (``mcp_coach.py``).

Every function returns a JSON string. Numbers are computed here, in Python, so
every coach (Sunday report, run-analyst, Claude Desktop chat) reads the same
figures and never has to add up laps or guess week boundaries.

Moved out of the retired paid-API chat (``coach.py``) on 2026-11-15.
"""
from __future__ import annotations

import json
from typing import Any

from . import config, db, metrics, training_load

DISTANCE_RANGES = {
    "5k": (4.5, 5.5),
    "10k": (9.0, 11.0),
    "half_marathon": (19.0, 23.0),
    "marathon": (40.0, 44.0),
}

SPEED_LIBRARY = config.PROJECT_ROOT / "coaching" / "speed_library.json"


def _dump(obj: Any) -> str:
    return json.dumps(obj, default=str)


def recent_runs(days: int = 30) -> str:
    rows = db.get_recent_activities(days=int(days), limit=2000)
    rows = [r for r in rows if r.get("activity_type") == "running"]
    for r in rows:
        r.pop("raw_json", None)
        r.pop("synced_at", None)
    return _dump(rows)


def recent_sessions(days: int = 28) -> str:
    """Runs with laps merged into one session per workout, each classified."""
    return _dump(metrics.sessions(days=int(days)))


def recent_cycling(days: int = 30) -> str:
    return _dump(db.get_recent_cycling(days=int(days), limit=2000))


def recent_sleep(nights: int = 14) -> str:
    rows = db.get_sleep_nights(limit=int(nights))
    for r in rows:
        r.pop("synced_at", None)
    return _dump(rows)


def recent_calories(days: int = 14) -> str:
    rows = db.get_calories_days(limit=int(days))
    for r in rows:
        r.pop("synced_at", None)
    return _dump(rows)


def recent_nutrition(days: int = 14) -> str:
    return _dump(db.get_nutrition_days(limit=int(days)))


def goals() -> str:
    return _dump(db.list_goals(status="active"))


def pr_progression(distance: str) -> str:
    if distance not in DISTANCE_RANGES:
        return _dump({"error": f"unknown distance: {distance}"})
    lo, hi = DISTANCE_RANGES[distance]
    rows = db.get_recent_activities(days=365, limit=5000)
    matches = sorted(
        (
            {
                "date": (r.get("start_local") or "")[:10],
                "distance_km": r.get("distance_km"),
                "duration_min": r.get("duration_min"),
                "avg_pace_min_per_km": r.get("avg_pace_min_per_km"),
                "avg_hr": r.get("avg_hr"),
            }
            for r in rows
            if r.get("activity_type") == "running"
            and r.get("distance_km") and lo <= r["distance_km"] <= hi
        ),
        key=lambda r: r["date"],
    )
    return _dump(matches)


def training_load_summary() -> str:
    data = training_load.compute(weeks=8)
    return _dump({
        "acwr": data["acwr"],
        "weekly_load": [
            {"week_start_monday": w["week_start"], "run": w["run_load"],
             "ride": w["ride_load"], "total": w["load"], "acwr": w["acwr"],
             "hrv": w["hrv"], "resting_hr": w["resting_hr"], "partial": w["partial"]}
            for w in data["weeks"]
        ],
        "hr_params": data["params"],
        "note": ("Weeks start Monday. TRIMP is HR-based (runs + rides) and depends on "
                 "an estimated max HR, so trust the trend and the ACWR ratio."),
    })


def week_summary(week_start: str | None = None) -> str:
    return _dump(metrics.week_summary(week_start))


def coach_snapshot() -> str:
    """Everything a weekly check-in needs, pre-computed, in one call."""
    return _dump(metrics.snapshot())


def speed_workouts() -> str:
    """The workout library, with its paces replaced by today's computed paces."""
    try:
        lib = json.loads(SPEED_LIBRARY.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return _dump({"error": f"could not read speed library: {exc}"})
    lib["current_paces"] = metrics.current_paces()
    lib.pop("paces", None)          # the old hand-typed paces went stale; use current_paces
    lib.pop("paces_intro", None)
    return _dump(lib)
