"""Garmin Connect data via the official web API (garminconnect → garth).

This replaces the browser-scraping path for sleep. Garmin's ``/app/sleep`` page
is a heavy SPA that frequently serves a "Looks Like We Crashed" render error, so
scraping it is fragile. The underlying JSON API, however, is reliable — this
module logs in once (persisting the OAuth token to ``GARMIN_TOKEN_DIR`` so we
don't re-authenticate every sync and trip Garmin's rate limiter) and reads sleep
straight from ``connectapi.garmin.com``.

The returned record matches the ``sleep_nights`` table exactly, so callers can
hand it straight to ``db.upsert_sleep_night``.
"""
from __future__ import annotations

import logging
import time
from datetime import date, timedelta
from typing import Any

from . import config

logger = logging.getLogger(__name__)

# Garmin's English qualifier keys → the Greek labels the app already stores, so
# API-sourced nights read identically to the ones imported from Greek CSVs.
_QUALITY_GR = {
    "EXCELLENT": "Άριστη", "GOOD": "Καλό", "FAIR": "Μέτριο", "POOR": "Κακό",
}
_HRV_GR = {
    "BALANCED": "Ισορροπημένη", "UNBALANCED": "Μη ισορροπημένη",
    "LOW": "Χαμηλή", "POOR": "Κακή", "UNKNOWN": None,
}


def _min(seconds: Any) -> int | None:
    """Seconds → whole minutes (Garmin reports sleep stage durations in sec)."""
    if seconds is None:
        return None
    try:
        return round(float(seconds) / 60.0)
    except (TypeError, ValueError):
        return None


def _int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return None


def _float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


# ── client / auth ────────────────────────────────────────────────────────────

_client_cache: Any = None

# Garmin rate-limits LOGINS (not data calls). When a credential login returns 429
# we must stop trying for a while, or every retry extends the ban. The backoff is
# persisted to a file so it holds across separate processes (the scheduled task
# spawns a fresh process each run), not just within one.
_BACKOFF_FILE = config.GARMIN_TOKEN_DIR.parent / "garmin_login_backoff"
_LOGIN_BACKOFF_SECONDS = 60 * 60   # 1 h after a 429 before we attempt a login again


def _backoff_remaining() -> int:
    """Seconds left on the login backoff (0 if none)."""
    try:
        left = float(_BACKOFF_FILE.read_text().strip()) - time.time()
        return int(left) if left > 0 else 0
    except Exception:  # noqa: BLE001 — no/garbled file == no backoff
        return 0


def _set_backoff() -> None:
    try:
        _BACKOFF_FILE.parent.mkdir(parents=True, exist_ok=True)
        _BACKOFF_FILE.write_text(str(time.time() + _LOGIN_BACKOFF_SECONDS))
    except Exception:  # noqa: BLE001
        logger.warning("Could not write Garmin login backoff file", exc_info=True)


def _clear_backoff() -> None:
    try:
        _BACKOFF_FILE.unlink()
    except Exception:  # noqa: BLE001 — already absent
        pass


def _is_rate_limited(exc: Exception) -> bool:
    """True when Garmin is rate-limiting LOGINS (429) — back off, never retry."""
    msg = str(exc).lower()
    return "429" in msg or "rate limit" in msg or "too many requests" in msg


def _client():
    """Return a logged-in ``Garmin`` client, reusing a cached OAuth token when
    possible so most syncs make zero login calls.

    Login order: (1) resume from the saved token dir; (2) if that fails, a fresh
    credential login, then persist the token for next time.
    """
    global _client_cache
    if _client_cache is not None:
        return _client_cache

    from garminconnect import Garmin

    if not config.GARMIN_EMAIL or not config.GARMIN_PASSWORD:
        raise RuntimeError(
            "Garmin credentials missing. Set GARMIN_EMAIL and GARMIN_PASSWORD "
            "in your .env file."
        )

    token_dir = str(config.GARMIN_TOKEN_DIR)
    config.GARMIN_TOKEN_DIR.mkdir(parents=True, exist_ok=True)

    # A single login(tokenstore) call does both jobs: if the store has a valid
    # token it resumes (no network login, no rate-limit risk); otherwise it logs
    # in with the credentials and persists the token to the store for next time.
    left = _backoff_remaining()
    if left > 0:
        raise RuntimeError(
            f"Garmin login is backing off after a rate-limit (429). It will retry "
            f"automatically in ~{left // 60 + 1} min — avoid pressing sync until then."
        )

    g = Garmin(config.GARMIN_EMAIL, config.GARMIN_PASSWORD)
    # A valid saved token makes this resume with NO credential login (no 429 risk);
    # only a missing/dead token triggers a real login, which can be rate-limited.
    try:
        mfa_status, _ = g.login(token_dir)
    except Exception as exc:  # noqa: BLE001 — classify, back off on 429, re-raise
        if _is_rate_limited(exc):
            _set_backoff()
            logger.warning("Garmin login hit 429 — backing off %d min before the "
                           "next login attempt.", _LOGIN_BACKOFF_SECONDS // 60)
        raise
    if mfa_status:
        raise RuntimeError(
            "Garmin is asking for a two-factor (MFA) code, which this "
            "background sync can't enter. Log in once in the browser sync to "
            "clear it, or disable MFA for this account."
        )
    _clear_backoff()   # a clean login/resume clears any earlier backoff
    logger.info("Garmin API: session ready (token dir: %s).", token_dir)
    _client_cache = g
    return g


def reset_client() -> None:
    """Drop the cached client so the next call re-authenticates (e.g. after a
    token is rejected mid-session)."""
    global _client_cache
    _client_cache = None


def _clear_saved_token() -> None:
    """Delete the persisted token so the next login is a fresh credential login
    rather than a doomed attempt to resume a dead token."""
    try:
        for f in config.GARMIN_TOKEN_DIR.glob("*"):
            if f.is_file():
                f.unlink()
    except Exception:  # noqa: BLE001 — best-effort cleanup
        logger.warning("Could not clear saved Garmin token", exc_info=True)


def _is_auth_error(exc: Exception) -> bool:
    """True ONLY for a genuinely invalid session token (needs re-auth). Excludes
    rate-limits (429) and transient 403 / 'social profile' responses — those are
    NOT token-expiry, and treating them as such used to wipe the saved token and
    force a full login that then got 429-banned."""
    if _is_rate_limited(exc):
        return False
    try:
        from garminconnect import GarminConnectAuthenticationError
        if isinstance(exc, GarminConnectAuthenticationError):
            return True
    except Exception:  # noqa: BLE001 — library shape changed; fall back to text
        pass
    msg = str(exc).lower()
    return "401" in msg or "unauthorized" in msg


def _call(action):
    """Run ``action(client)``, re-authenticating **once** if the session token
    expired in flight. It NEVER wipes the saved token (garth refreshes the short
    OAuth2 token from the long-lived OAuth1 token on disk; wiping would force a
    full credential login that risks a 429) and NEVER retries under a rate-limit."""
    try:
        return action(_client())
    except Exception as exc:  # noqa: BLE001 — inspect, then re-raise if not auth
        if not _is_auth_error(exc):
            raise
        logger.warning("Garmin API: session expired (%s) — re-authenticating once.", exc)
        reset_client()
        return action(_client())


# ── sleep ────────────────────────────────────────────────────────────────────

def _night_record(data: dict[str, Any]) -> dict[str, Any] | None:
    """Map Garmin's ``get_sleep_data`` JSON to a ``sleep_nights`` record.

    Returns None when the night has no measured sleep (device wasn't worn).
    """
    if not data:
        return None
    dto = data.get("dailySleepDTO") or {}
    if not dto.get("calendarDate") or not dto.get("sleepTimeSeconds"):
        return None

    scores = dto.get("sleepScores") or {}
    overall = scores.get("overall") or {}
    qualifier = overall.get("qualifierKey")
    hrv_status = data.get("hrvStatus")

    return {
        "night_date":          dto.get("calendarDate"),
        "score":               _int(overall.get("value")),
        "quality":             _QUALITY_GR.get(qualifier, qualifier),
        "duration_min":        _min(dto.get("sleepTimeSeconds")),
        "deep_min":            _min(dto.get("deepSleepSeconds")),
        "light_min":           _min(dto.get("lightSleepSeconds")),
        "rem_min":             _min(dto.get("remSleepSeconds")),
        "awake_min":           _min(dto.get("awakeSleepSeconds")),
        "stress_avg":          _int(dto.get("avgSleepStress")),
        "restless_moments":    _int(data.get("restlessMomentsCount")),
        "overnight_hr":        _int(dto.get("avgHeartRate")),
        "resting_hr":          _int(data.get("restingHeartRate")),
        "body_battery_change": _int(data.get("bodyBatteryChange")),
        "spo2_avg":            _int(dto.get("averageSpO2Value")),
        "spo2_low":            _int(dto.get("lowestSpO2Value")),
        "respiration_avg":     _float(dto.get("averageRespirationValue")),
        "respiration_low":     _float(dto.get("lowestRespirationValue")),
        "hrv_ms":              _int(data.get("avgOvernightHrv")),
        "hrv_status":          _HRV_GR.get(hrv_status, hrv_status),
    }


def fetch_sleep_night(day: str | date) -> dict[str, Any] | None:
    """Fetch one night's sleep record (the night whose data lands on ``day``).

    ``day`` is an ISO date string or a ``date``. Returns the ``sleep_nights``
    record, or None if that night has no measured sleep.
    """
    day_iso = day.isoformat() if isinstance(day, date) else str(day)
    data = _call(lambda g: g.get_sleep_data(day_iso))
    return _night_record(data)


def fetch_latest_sleep_night(lookback_days: int = 3) -> dict[str, Any] | None:
    """Fetch the most recent night that actually has sleep data, starting from
    today and walking back up to ``lookback_days`` (covers not-yet-synced or
    unworn nights)."""
    today = date.today()
    for i in range(lookback_days + 1):
        rec = fetch_sleep_night(today - timedelta(days=i))
        if rec is not None:
            return rec
    return None


# ── activities (running / cycling) ───────────────────────────────────────────

# Garmin has many sport sub-types; fold them into the two families the app
# stores. Anything else (walking, strength, swim, …) is skipped.
_RUN_KEYS = {
    "running", "trail_running", "treadmill_running", "track_running",
    "indoor_running", "virtual_run", "obstacle_run", "ultra_run",
}
_RIDE_KEYS = {
    "cycling", "road_biking", "mountain_biking", "gravel_cycling",
    "indoor_cycling", "virtual_ride", "cyclocross", "bmx", "handcycling",
    "recumbent_cycling", "e_bike_fitness", "e_bike_mountain",
}


def _activity_family(type_key: str | None) -> str | None:
    tk = (type_key or "").lower()
    if tk in _RUN_KEYS or "running" in tk:
        return "running"
    if tk in _RIDE_KEYS or "cycling" in tk or "biking" in tk:
        return "cycling"
    return None


def _activity_summary(a: dict[str, Any]) -> dict[str, Any] | None:
    """Map one Garmin activity-list item to the ``activities`` upsert shape.
    Returns None for sports the app doesn't track."""
    family = _activity_family((a.get("activityType") or {}).get("typeKey"))
    if family is None or a.get("activityId") is None:
        return None

    dist_km = round(a["distance"] / 1000.0, 3) if a.get("distance") else None
    dur_min = round(a["duration"] / 60.0, 2) if a.get("duration") else None
    pace = (round(dur_min / dist_km, 3)
            if family == "running" and dist_km and dur_min else None)
    speed = (round(dist_km / (dur_min / 60.0), 2)
             if family == "cycling" and dist_km and dur_min else None)

    return {
        "id":                  a.get("activityId"),
        "name":                a.get("activityName"),
        "start_local":         a.get("startTimeLocal"),
        "distance_km":         dist_km,
        "duration_min":        dur_min,
        "avg_pace_min_per_km": pace,
        "avg_speed_kmh":       speed,
        "avg_hr":              _int(a.get("averageHR")),
        "max_hr":              _int(a.get("maxHR")),
        "vo2max":              None,   # enriched separately (see current_vo2max)
        "elevation_gain_m":    _float(a.get("elevationGain")),
        "calories":            _int(a.get("calories")),
        "activity_type":       family,
    }


def fetch_activities(limit: int = 30,
                     types: set[str] | None = None) -> list[tuple[dict, dict]]:
    """Fetch the most recent activities as ``(summary, raw)`` pairs, ready for
    ``db.upsert_activity``. ``types`` filters to a subset of {'running',
    'cycling'}; None keeps both. The raw JSON is kept for ``raw_json`` (HR
    zones, training effect, etc.)."""
    raw_list = _call(lambda g: g.get_activities(0, limit))
    out: list[tuple[dict, dict]] = []
    for a in raw_list:
        s = _activity_summary(a)
        if s is None:
            continue
        if types and s["activity_type"] not in types:
            continue
        out.append((s, a))
    return out


def current_vo2max() -> float | None:
    """Today's running VO2max from Garmin's max-metrics endpoint — the value the
    activity list omits (and the CSV never had). None if unavailable."""
    try:
        m = _call(lambda g: g.get_max_metrics(date.today().isoformat()))
    except Exception:  # noqa: BLE001
        logger.warning("VO2max fetch failed", exc_info=True)
        return None
    if not m:
        return None
    generic = (m[0] or {}).get("generic") or {}
    return _float(generic.get("vo2MaxValue"))


# ── calories ─────────────────────────────────────────────────────────────────

def fetch_calories_days(days: int = 7) -> list[dict[str, Any]]:
    """Fetch daily calories for the last ``days`` days (ending today), newest
    first, as ``calories`` upsert records. Days with no data are skipped."""
    today = date.today()
    out: list[dict[str, Any]] = []
    for i in range(days):
        d = (today - timedelta(days=i)).isoformat()
        try:
            s = _call(lambda g, d=d: g.get_stats(d))
        except Exception:  # noqa: BLE001 — skip a bad day, keep the rest
            logger.warning("Calories fetch failed for %s — skipping.", d, exc_info=True)
            continue
        total = s.get("totalKilocalories")
        active = s.get("activeKilocalories")
        if total is None and active is None:
            continue
        out.append({
            "day_date":     d,
            "activity_cal": _int(active),
            "resting_cal":  _int(s.get("bmrKilocalories")),
            "total_cal":    _int(total),
        })
    return out


def fetch_recent_sleep_nights(days: int = 30) -> list[dict[str, Any]]:
    """Fetch up to ``days`` nights ending today, newest first — for backfilling
    per-night history. Nights without measured sleep are skipped, and a single
    failed night (e.g. a transient API hiccup) never aborts the whole backfill."""
    _client()  # authenticate once up front (raises clearly if login fails)
    today = date.today()
    out: list[dict[str, Any]] = []
    for i in range(days):
        d = today - timedelta(days=i)
        try:
            rec = fetch_sleep_night(d)
        except Exception:  # noqa: BLE001 — skip a bad night, keep the rest
            logger.warning("Sleep fetch failed for %s — skipping.", d, exc_info=True)
            continue
        if rec is not None:
            out.append(rec)
    return out
