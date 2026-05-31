"""Glue between Garmin fetching and DB storage.

Run directly as a smoke test (requires successful Garmin login):
    .venv\\Scripts\\python.exe -m backend.sync
"""
from __future__ import annotations

import logging

from . import db, garmin_client

logger = logging.getLogger(__name__)


def sync_recent_runs(days: int = 30) -> int:
    """Fetch runs from Garmin and upsert them into the local DB.

    Returns the number of activities written.
    """
    raw_activities = garmin_client.fetch_runs(days=days)
    items: list[tuple[dict, dict]] = []
    for a in raw_activities:
        summary = garmin_client.summarize_activity(a)
        if summary.get("id") is not None:
            items.append((summary, a))
    count = db.upsert_many(items)
    logger.info("Synced %d activities into %s", count, db.config.DATABASE_PATH)
    return count


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    n = sync_recent_runs(days=30)
    total = db.count_activities()
    print(f"\nSynced {n} runs. Database now contains {total} total activity records.")
