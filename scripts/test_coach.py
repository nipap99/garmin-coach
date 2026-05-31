"""Smoke-test the Claude coach end-to-end.

This makes a REAL Anthropic API call (a few cents). It seeds the DB with a
handful of fake activities + one goal, asks the coach a simple question, and
prints the response. Then cleans up.
"""
from __future__ import annotations

import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import coach, db


FAKE_ACTIVITY_IDS = [10001, 10002, 10003, 10004]


def _seed() -> tuple[int, list[int]]:
    today = date.today()
    fake_runs = [
        {
            "id": 10001,
            "name": "Easy run",
            "start_local": (datetime.combine(today - timedelta(days=2), datetime.min.time())
                            .replace(hour=7)).isoformat(sep=" "),
            "distance_km": 6.0,
            "duration_min": 36.0,
            "avg_pace_min_per_km": 6.0,
            "avg_hr": 145,
            "max_hr": 160,
            "vo2max": 52.0,
            "elevation_gain_m": 25.0,
            "calories": 380,
            "activity_type": "running",
        },
        {
            "id": 10002,
            "name": "Tempo run",
            "start_local": (datetime.combine(today - timedelta(days=4), datetime.min.time())
                            .replace(hour=18)).isoformat(sep=" "),
            "distance_km": 8.0,
            "duration_min": 38.4,
            "avg_pace_min_per_km": 4.8,
            "avg_hr": 168,
            "max_hr": 182,
            "vo2max": 52.5,
            "elevation_gain_m": 40.0,
            "calories": 520,
            "activity_type": "running",
        },
        {
            "id": 10003,
            "name": "Long run",
            "start_local": (datetime.combine(today - timedelta(days=7), datetime.min.time())
                            .replace(hour=8)).isoformat(sep=" "),
            "distance_km": 15.0,
            "duration_min": 84.0,
            "avg_pace_min_per_km": 5.6,
            "avg_hr": 155,
            "max_hr": 172,
            "vo2max": 53.0,
            "elevation_gain_m": 120.0,
            "calories": 950,
            "activity_type": "running",
        },
        {
            "id": 10004,
            "name": "5K hard effort",
            "start_local": (datetime.combine(today - timedelta(days=10), datetime.min.time())
                            .replace(hour=19)).isoformat(sep=" "),
            "distance_km": 5.0,
            "duration_min": 23.5,
            "avg_pace_min_per_km": 4.7,
            "avg_hr": 175,
            "max_hr": 188,
            "vo2max": 53.5,
            "elevation_gain_m": 30.0,
            "calories": 340,
            "activity_type": "running",
        },
    ]
    for r in fake_runs:
        db.upsert_activity(r, {"_fake": True})

    goal_id = db.create_goal(
        distance="5k",
        target_date=(today + timedelta(days=120)).isoformat(),
        target_time_seconds=1380,  # 23:00
        notes="Smoke-test seed goal",
    )
    return goal_id, FAKE_ACTIVITY_IDS


def _cleanup(goal_id: int, activity_ids: list[int]) -> None:
    db.delete_goal(goal_id)
    with db.get_connection() as conn:
        for aid in activity_ids:
            conn.execute("DELETE FROM activities WHERE id = ?", (aid,))


def main() -> None:
    print("Seeding fake activities + goal...")
    goal_id, activity_ids = _seed()
    try:
        print("Asking the coach: 'What does my training look like recently?'")
        print("(this will hit the Anthropic API)\n")
        reply = coach.chat("What does my training look like recently? Be brief.")
        print("=" * 70)
        print("COACH REPLY:")
        print("=" * 70)
        print(reply)
        print("=" * 70)
    finally:
        print("\nCleaning up fake data...")
        _cleanup(goal_id, activity_ids)
        print("Done.")


if __name__ == "__main__":
    main()
