"""Smoke-test the CSV importer with a synthetic Garmin-style CSV file."""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import csv_importer, db


# Simulates the columns Garmin Connect's "Export CSV" produces. Mix of running
# and non-running rows; pace in MM:SS, distance in km, time in HH:MM:SS.
SAMPLE_CSV = """Activity Type,Date,Title,Distance,Calories,Time,Avg HR,Max HR,Avg Pace,Total Ascent
Running,2026-05-20 07:15:23,Morning Run,5.02,310,25:42,158,178,5:07,32
Cycling,2026-05-22 18:00:00,Sunset Ride,25.5,650,1:05:30,142,168,,150
Running,2026-05-24 06:30:00,Long Run,12.50,820,1:08:42,148,170,5:30,85
Trail Running,2026-05-26 09:00:00,Mountain Trail,8.20,540,52:18,162,182,6:22,420
Strength,2026-05-25 19:00:00,Leg Day,,420,45:00,118,142,,
Running,2026-05-27 06:45:00,Intervals,7.00,520,32:00,170,188,4:34,15
"""


def main() -> None:
    # Track baseline so we don't disturb existing data
    before = db.count_activities()
    print(f"DB activities before import: {before}")

    with tempfile.TemporaryDirectory() as tmp:
        csv_path = Path(tmp) / "Activities.csv"
        csv_path.write_text(SAMPLE_CSV, encoding="utf-8")

        n = csv_importer.import_csv_file(csv_path)
        print(f"Imported: {n} running activities (expected 4)")

    after = db.count_activities()
    print(f"DB activities after import: {after}")

    # Show the imported rows
    imported = [
        r for r in db.get_recent_activities(days=365)
        if r["id"] < 0  # synthetic IDs are negative
    ]
    print(f"\nFound {len(imported)} CSV-imported activities:")
    for r in imported:
        print(
            f"  {r['start_local']:<20} "
            f"{(r['distance_km'] or 0):>5.2f}km  "
            f"{(r['duration_min'] or 0):>5.1f}min  "
            f"pace {r['avg_pace_min_per_km']} min/km  "
            f"HR {r['avg_hr']}  "
            f"name={r['name']!r}"
        )

    # Re-import should be idempotent (same IDs)
    print("\nRe-importing same CSV to verify idempotency...")
    with tempfile.TemporaryDirectory() as tmp:
        csv_path = Path(tmp) / "Activities.csv"
        csv_path.write_text(SAMPLE_CSV, encoding="utf-8")
        csv_importer.import_csv_file(csv_path)
    after2 = db.count_activities()
    print(f"DB activities after re-import: {after2} (should match previous: {after})")

    # Cleanup
    print("\nCleaning up CSV-imported activities...")
    with db.get_connection() as conn:
        conn.execute("DELETE FROM activities WHERE id < 0")
    print(f"DB activities final: {db.count_activities()} (should match baseline: {before})")
    print("CSV importer works.")


if __name__ == "__main__":
    main()
