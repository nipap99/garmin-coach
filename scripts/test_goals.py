"""Smoke-test goals CRUD with no UI."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import db


def main() -> None:
    print("Before:", len(db.list_goals()), "active goals")

    g1 = db.create_goal(
        distance="5k",
        target_date="2026-09-15",
        target_time_seconds=1500,  # 25:00
        notes="Athens autumn race",
    )
    g2 = db.create_goal(
        distance="half_marathon",
        target_time_seconds=6300,  # 1:45:00
        notes=None,
    )
    print(f"Created goals: {g1}, {g2}")

    goals = db.list_goals()
    print(f"Now have {len(goals)} active goals:")
    for g in goals:
        print(f"  #{g['id']}: {g['distance']} target={g['target_time_seconds']}s "
              f"date={g['target_date']} notes={g['notes']!r}")

    print("\nCleaning up...")
    db.delete_goal(g1)
    db.delete_goal(g2)
    print(f"After cleanup: {len(db.list_goals())} active goals")
    print("Goals CRUD works.")


if __name__ == "__main__":
    main()
