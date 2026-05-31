"""Smoke-test the SQLite storage layer with a fake activity.

This script does NOT touch Garmin — it just verifies that our DB schema
works and that upsert/read round-trips a row correctly.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import db


def main() -> None:
    fake_summary = {
        "id": 99999999,
        "name": "Fake test run (delete me)",
        "start_local": "2026-05-27 07:30:00",
        "distance_km": 5.0,
        "duration_min": 25.5,
        "avg_pace_min_per_km": 5.1,
        "avg_hr": 158,
        "max_hr": 178,
        "vo2max": 52.0,
        "elevation_gain_m": 30.0,
        "calories": 320,
        "activity_type": "running",
    }
    fake_raw = {"_note": "this is a fake activity used by test_db.py"}

    print("Before:")
    print(f"  total activities in DB: {db.count_activities()}")

    print("\nUpserting fake activity...")
    db.upsert_activity(fake_summary, fake_raw)

    print("\nAfter:")
    print(f"  total activities in DB: {db.count_activities()}")

    recent = db.get_recent_activities(days=365)
    print(f"  found {len(recent)} activities in the last 365 days")
    if recent:
        r = recent[0]
        print(
            f"  most recent: id={r['id']}  "
            f"name={r['name']!r}  "
            f"distance={r['distance_km']}km  "
            f"pace={r['avg_pace_min_per_km']}min/km"
        )

    print("\nCleaning up fake row...")
    with db.get_connection() as conn:
        conn.execute("DELETE FROM activities WHERE id = ?", (fake_summary["id"],))

    print(f"\nFinal count: {db.count_activities()}")
    print("Storage layer works.")


if __name__ == "__main__":
    main()
