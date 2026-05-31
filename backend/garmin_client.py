"""Garmin Connect client wrapper.

Wraps python-garminconnect with:
- session-token caching (so we only do a real login once)
- a simple ``fetch_runs(days)`` helper
- ``summarize_activity()`` to project Garmin's raw blob down to the fields we use

Run directly as a smoke test:
    .venv\\Scripts\\python.exe -m backend.garmin_client
"""
from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any

from garminconnect import (
    Garmin,
    GarminConnectAuthenticationError,
    GarminConnectConnectionError,
)

from . import config

logger = logging.getLogger(__name__)


def _mfa_prompt() -> str:
    """Called by garminconnect if Garmin requires a verification code."""
    print("\n>>> Garmin asked for a verification code.")
    print(">>> Check your email (or authenticator app) for the code.")
    return input(">>> Enter the code: ").strip()


def get_client() -> Garmin:
    """Return a logged-in Garmin client.

    On first call, performs a real login and may prompt for an MFA code.
    On subsequent calls, resumes from the cached session tokens.
    """
    config.GARMIN_TOKEN_DIR.mkdir(parents=True, exist_ok=True)
    token_dir = str(config.GARMIN_TOKEN_DIR)

    client = Garmin(
        email=config.GARMIN_EMAIL,
        password=config.GARMIN_PASSWORD,
        prompt_mfa=_mfa_prompt,
    )

    try:
        client.login(tokenstore=token_dir)
    except (GarminConnectAuthenticationError, GarminConnectConnectionError) as e:
        logger.error("Garmin login failed: %s", e)
        raise

    logger.info("Logged in to Garmin (token dir: %s)", token_dir)
    return client


def fetch_runs(days: int = 30) -> list[dict[str, Any]]:
    """Fetch running activities from the last ``days`` days.

    Returns the raw activity list from Garmin's endpoint. Use
    :func:`summarize_activity` to project each entry into our schema.
    """
    client = get_client()
    end = date.today()
    start = end - timedelta(days=days)
    activities = client.get_activities_by_date(
        startdate=start.isoformat(),
        enddate=end.isoformat(),
        activitytype="running",
    )
    logger.info(
        "Fetched %d running activities from %s to %s",
        len(activities),
        start,
        end,
    )
    return activities


def summarize_activity(a: dict[str, Any]) -> dict[str, Any]:
    """Project a raw Garmin activity dict into the fields we persist + display."""
    return {
        "id": a.get("activityId"),
        "name": a.get("activityName"),
        "start_local": a.get("startTimeLocal"),
        "distance_km": round((a.get("distance") or 0) / 1000, 2),
        "duration_min": round((a.get("duration") or 0) / 60, 1),
        "avg_pace_min_per_km": _pace_min_per_km(a.get("averageSpeed")),
        "avg_hr": a.get("averageHR"),
        "max_hr": a.get("maxHR"),
        "vo2max": a.get("vO2MaxValue"),
        "elevation_gain_m": a.get("elevationGain"),
        "calories": a.get("calories"),
        "activity_type": (a.get("activityType") or {}).get("typeKey"),
    }


def _pace_min_per_km(speed_m_per_s: float | None) -> float | None:
    if not speed_m_per_s or speed_m_per_s <= 0:
        return None
    seconds_per_km = 1000 / speed_m_per_s
    return round(seconds_per_km / 60, 2)


def _format_pace(p: float | None) -> str:
    """Format min/km as M:SS (e.g. 5.5 -> '5:30')."""
    if p is None:
        return "?"
    minutes = int(p)
    seconds = round((p - minutes) * 60)
    if seconds == 60:
        minutes += 1
        seconds = 0
    return f"{minutes}:{seconds:02d}"


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    runs = fetch_runs(days=30)
    print(f"\nFound {len(runs)} running activities in the last 30 days:\n")
    if not runs:
        print("  (no runs found — try increasing days or check that you have runs in Garmin Connect)")
    for a in runs:
        s = summarize_activity(a)
        pace = _format_pace(s["avg_pace_min_per_km"])
        hr = s["avg_hr"] or "?"
        print(
            f"  {str(s['start_local'])[:16]}  "
            f"{s['distance_km']:>5.2f} km  "
            f"{s['duration_min']:>5.1f} min  "
            f"pace {pace} min/km  "
            f"HR {hr}"
        )
