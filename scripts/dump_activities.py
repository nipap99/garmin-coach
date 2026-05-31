"""Print all stored activities for verification."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import db


def main() -> None:
    rows = db.get_recent_activities(days=365)
    print(f"{len(rows)} activities in DB\n")
    header = f"{'date':<20} {'name':<30} {'distance':>10} {'duration':>10} {'pace':>8} {'hr':>5}"
    print(header)
    print("-" * len(header))
    for r in sorted(rows, key=lambda x: x["start_local"]):
        pace = f"{r['avg_pace_min_per_km']:.2f}" if r["avg_pace_min_per_km"] else "?"
        dist = f"{r['distance_km']:.2f} km" if r["distance_km"] is not None else "?"
        dur = f"{r['duration_min']:.1f} min" if r["duration_min"] is not None else "?"
        name = (r["name"] or "")[:30]
        print(f"{r['start_local']:<20} {name:<30} {dist:>10} {dur:>10} {pace:>8} {r['avg_hr'] or '?':>5}")


if __name__ == "__main__":
    main()
