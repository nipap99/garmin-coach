"""One-time migration: copy data from the old SQLite DB into Postgres.

We deliberately copy **only the authoritative activities** — the ones with a
real, positive Garmin activity id. The negative-id rows in SQLite were CSV
imports that duplicated those same runs (the bug we just fixed), so we drop
them. Goals and chat history are copied verbatim.

Run from the project root:

    .venv\\Scripts\\python.exe -m scripts.migrate_sqlite_to_pg
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import config, db

ACTIVITY_FIELDS = (
    "id", "name", "start_local", "distance_km", "duration_min",
    "avg_pace_min_per_km", "avg_hr", "max_hr", "vo2max",
    "elevation_gain_m", "calories", "activity_type",
)


def main() -> int:
    if not config.DATABASE_PATH.exists():
        print(f"Old SQLite DB not found at {config.DATABASE_PATH}; nothing to migrate.")
        return 0

    src = sqlite3.connect(config.DATABASE_PATH)
    src.row_factory = sqlite3.Row

    # --- Activities: only the real Garmin rows (positive id) ---
    rows = src.execute("SELECT * FROM activities WHERE id > 0").fetchall()
    migrated = 0
    for r in rows:
        summary = {k: r[k] for k in ACTIVITY_FIELDS}
        raw = None
        if r["raw_json"]:
            try:
                raw = json.loads(r["raw_json"])
            except (json.JSONDecodeError, TypeError):
                raw = {"_legacy_raw": r["raw_json"]}
        db.upsert_activity(summary, raw)
        migrated += 1
    print(f"Activities: copied {migrated} authoritative runs.")

    # --- Goals (verbatim, preserving status + created_at) ---
    goals = src.execute("SELECT * FROM goals").fetchall()
    with db.get_connection() as conn:
        for g in goals:
            conn.execute(
                """
                INSERT INTO goals
                    (distance, target_date, target_time_seconds, notes, status, created_at)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (
                    g["distance"], g["target_date"], g["target_time_seconds"],
                    g["notes"], g["status"], g["created_at"],
                ),
            )
    print(f"Goals: copied {len(goals)}.")

    # --- Chat history (verbatim, in original order) ---
    msgs = src.execute("SELECT * FROM chat_messages ORDER BY id ASC").fetchall()
    with db.get_connection() as conn:
        for m in msgs:
            conn.execute(
                "INSERT INTO chat_messages (role, content, created_at) VALUES (%s, %s, %s)",
                (m["role"], m["content"], m["created_at"]),
            )
    print(f"Chat messages: copied {len(msgs)}.")

    src.close()

    print("\n--- Postgres now contains ---")
    print(f"  activities:    {db.count_activities()}")
    print(f"  goals:         {len(db.list_goals(status=None))}")
    print(f"  chat messages: {len(db.get_chat_history(limit=1000))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
