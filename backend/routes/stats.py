"""JSON endpoints that supply data to the Chart.js charts on the dashboard.

All routes are under /stats and return plain JSON (not HTML fragments).
The frontend fetches these with the native fetch() API and hands the
data to Chart.js.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta

from fastapi import APIRouter, Query

from .. import db

router = APIRouter(prefix="/stats", tags=["stats"])


def _fmt_pace(pace_dec: float) -> str:
    """Convert decimal minutes to M:SS/km string."""
    mins = int(pace_dec)
    secs = round((pace_dec - mins) * 60)
    if secs == 60:
        mins += 1
        secs = 0
    return f"{mins}:{secs:02d}/km"


def _fmt_duration(duration_min: float) -> str:
    """Convert decimal minutes to H:MM:SS or M:SS string."""
    total = int(round(duration_min * 60))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


@router.get("/summary")
def summary():
    """Totals for the four dashboard stat cards."""
    return db.get_stats_summary()


@router.get("/weekly-mileage")
def weekly_mileage(weeks: int = Query(16, ge=4, le=52)):
    """Weekly km totals for the last N weeks."""
    rows = db.get_weekly_mileage(weeks=weeks)
    return {
        "labels": [r["week_start"] for r in rows],
        "values": [float(r["total_km"]) for r in rows],
        "counts": [int(r["run_count"]) for r in rows],
    }


@router.get("/pace-trend")
def pace_trend(limit: int = Query(30, ge=5, le=100)):
    """Recent run paces for the trend line chart."""
    rows = db.get_pace_trend(limit=limit)
    return {
        "labels":    [r["start_local"][:10] for r in rows],
        "values":    [round(float(r["avg_pace_min_per_km"]), 3) for r in rows],
        "distances": [round(float(r["distance_km"]), 1) for r in rows],
    }


@router.get("/hr-trend")
def hr_trend(limit: int = Query(30, ge=5, le=100)):
    """Average heart rate per run for the trend chart."""
    rows = db.get_hr_trend(limit=limit)
    return {
        "labels":    [r["start_local"][:10] for r in rows],
        "values":    [int(r["avg_hr"]) for r in rows],
        "distances": [round(float(r["distance_km"]), 1) for r in rows],
        "paces":     [round(float(r["avg_pace_min_per_km"]), 3)
                      if r.get("avg_pace_min_per_km") else None
                      for r in rows],
    }


@router.get("/prs")
def personal_records():
    """Best (fastest) run in each standard distance bracket."""
    prs = db.get_personal_records()
    result = {}
    for dist, row in prs.items():
        pace = row.get("avg_pace_min_per_km")
        result[dist] = {
            "date":               row["start_local"][:10],
            "distance_km":        round(float(row["distance_km"]), 2),
            "duration_formatted": _fmt_duration(float(row["duration_min"])),
            "pace_formatted":     _fmt_pace(float(pace)) if pace else "—",
        }
    return result


@router.get("/cycling/summary")
def cycling_summary():
    """Totals for the four cycling stat cards."""
    return db.get_cycling_summary()


@router.get("/cycling/weekly-distance")
def cycling_weekly_distance(weeks: int = Query(16, ge=4, le=52)):
    """Weekly km totals for cycling (bar chart)."""
    rows = db.get_cycling_weekly_distance(weeks=weeks)
    return {
        "labels": [r["week_start"] for r in rows],
        "values": [float(r["total_km"]) for r in rows],
        "counts": [int(r["ride_count"]) for r in rows],
    }


@router.get("/cycling/speed-trend")
def cycling_speed_trend(limit: int = Query(30, ge=5, le=100)):
    """Recent ride average speeds for the trend line (higher = faster)."""
    rows = db.get_cycling_speed_trend(limit=limit)
    return {
        "labels":    [r["start_local"][:10] for r in rows],
        "values":    [round(float(r["avg_speed_kmh"]), 1) for r in rows],
        "distances": [round(float(r["distance_km"]), 1) for r in rows],
    }


@router.get("/cycling/hr-trend")
def cycling_hr_trend(limit: int = Query(30, ge=5, le=100)):
    """Average heart rate per ride for the trend chart."""
    rows = db.get_cycling_hr_trend(limit=limit)
    return {
        "labels":    [r["start_local"][:10] for r in rows],
        "values":    [int(r["avg_hr"]) for r in rows],
        "distances": [round(float(r["distance_km"]), 1) for r in rows],
        "speeds":    [round(float(r["avg_speed_kmh"]), 1)
                      if r.get("avg_speed_kmh") else None
                      for r in rows],
    }


def _fmt_hm(minutes) -> str:
    if not minutes:
        return "—"
    m = int(round(float(minutes)))
    return f"{m // 60}h {m % 60:02d}m"


def _time_to_plot(hhmm: str | None, night: bool) -> float | None:
    """Convert 'HH:MM' to a decimal hour for plotting. Night (bedtime) values
    after midnight get +24 so 00:10 sits continuously after 23:50."""
    if not hhmm or ":" not in hhmm:
        return None
    h, m = hhmm.split(":")
    val = int(h) + int(m) / 60
    if night and val < 12:
        val += 24
    return round(val, 2)


@router.get("/sleep/summary")
def sleep_summary():
    """Totals for the four sleep stat cards."""
    s = db.get_sleep_summary()
    return {
        "weeks_tracked":    int(s["weeks_tracked"]) if s.get("weeks_tracked") else 0,
        "avg_score":        int(s["avg_score"]) if s.get("avg_score") else 0,
        "avg_duration_fmt": _fmt_hm(s.get("avg_duration_min")),
        "avg_need_fmt":     _fmt_hm(s.get("avg_need_min")),
    }


@router.get("/sleep/trend")
def sleep_trend(weeks: int = Query(52, ge=4, le=260)):
    """Weekly sleep series for the Sleep-tab charts."""
    rows = db.get_sleep_weeks(limit=weeks)

    def dur_h(v):
        return round(float(v) / 60, 2) if v else None

    return {
        "labels":        [r["week_start"] for r in rows],
        "scores":        [int(r["avg_score"]) if r["avg_score"] is not None else None for r in rows],
        "duration_h":    [dur_h(r["avg_duration_min"]) for r in rows],
        "need_h":        [dur_h(r["avg_need_min"]) for r in rows],
        "duration_fmt":  [_fmt_hm(r["avg_duration_min"]) for r in rows],
        "need_fmt":      [_fmt_hm(r["avg_need_min"]) for r in rows],
        "bedtime":       [r["avg_bedtime"] for r in rows],
        "wake":          [r["avg_wake_time"] for r in rows],
        "bedtime_plot":  [_time_to_plot(r["avg_bedtime"], night=True) for r in rows],
        "wake_plot":     [_time_to_plot(r["avg_wake_time"], night=False) for r in rows],
    }


@router.get("/sleep/nights/summary")
def sleep_nights_summary():
    """Averages over the last 30 nights for the nightly stat cards."""
    s = db.get_sleep_nights_summary()
    return {
        "nights_tracked":   int(s["nights_tracked"]) if s.get("nights_tracked") else 0,
        "avg_score":        int(s["avg_score"]) if s.get("avg_score") else 0,
        "avg_duration_fmt": _fmt_hm(s.get("avg_duration_min")),
        "avg_deep_fmt":     _fmt_hm(s.get("avg_deep_min")),
        "avg_hrv_ms":       int(s["avg_hrv_ms"]) if s.get("avg_hrv_ms") else 0,
    }


@router.get("/sleep/nights")
def sleep_nights(limit: int = Query(60, ge=7, le=400)):
    """Per-night series for the nightly Sleep charts (stages, score, HRV)."""
    rows = db.get_sleep_nights(limit=limit)

    def h(v):
        return round(float(v) / 60, 2) if v else 0

    return {
        "labels":       [r["night_date"] for r in rows],
        "deep_h":       [h(r["deep_min"]) for r in rows],
        "light_h":      [h(r["light_min"]) for r in rows],
        "rem_h":        [h(r["rem_min"]) for r in rows],
        "scores":       [r["score"] for r in rows],
        "hrv":          [r["hrv_ms"] for r in rows],
        "duration_fmt": [_fmt_hm(r["duration_min"]) for r in rows],
        "deep_fmt":     [_fmt_hm(r["deep_min"]) for r in rows],
        "light_fmt":    [_fmt_hm(r["light_min"]) for r in rows],
        "rem_fmt":      [_fmt_hm(r["rem_min"]) for r in rows],
    }


@router.get("/calories/summary")
def calories_summary():
    """Totals for the four calories stat cards (last 30 days)."""
    s = db.get_calories_summary()
    return {
        "days_tracked": int(s["days_tracked"]) if s.get("days_tracked") else 0,
        "avg_total":    int(s["avg_total"]) if s.get("avg_total") else 0,
        "avg_active":   int(s["avg_active"]) if s.get("avg_active") else 0,
        "avg_resting":  int(s["avg_resting"]) if s.get("avg_resting") else 0,
    }


@router.get("/calories/trend")
def calories_trend(days: int = Query(60, ge=7, le=400)):
    """Per-day series for the Calories charts (resting + active, total)."""
    rows = db.get_calories_days(limit=days)
    return {
        "labels":  [r["day_date"] for r in rows],
        "active":  [r["activity_cal"] for r in rows],
        "resting": [r["resting_cal"] for r in rows],
        "total":   [r["total_cal"] for r in rows],
    }


@router.get("/nutrition/trend")
def nutrition_trend(days: int = Query(14, ge=3, le=120)):
    """Per-day macro grams + total calories for the Food-tab bar chart."""
    rows = db.get_nutrition_days(limit=days)
    return {
        "labels":    [r["log_date"] for r in rows],
        "protein_g": [float(r["protein_g"] or 0) for r in rows],
        "carbs_g":   [float(r["carbs_g"] or 0) for r in rows],
        "fat_g":     [float(r["fat_g"] or 0) for r in rows],
        "kcal":      [int(r["kcal"] or 0) for r in rows],
    }


@router.get("/aerobic-efficiency")
def aerobic_efficiency():
    """Aerobic efficiency score for each run + weekly averages for the trend line.

    Formula per run:  efficiency = (speed_km_h / avg_hr) * 100
                                 = (60 / avg_pace_min_per_km) / avg_hr * 100

    A higher score means the runner is moving faster for the same cardiac effort.
    Returns:
      runs  – individual data points (date, efficiency) for the scatter dots
      trend – weekly averages (date = week start Monday, efficiency) for the line
    """
    rows = db.get_aerobic_efficiency_runs()
    if not rows:
        return {"runs": [], "trend": []}

    # ── individual run scores ─────────────────────────────────────────────────
    runs: list[dict] = []
    for r in rows:
        pace = float(r["avg_pace_min_per_km"])
        hr   = float(r["avg_hr"])
        if pace <= 0 or hr <= 0:
            continue
        eff = round((60.0 / pace) / hr * 100, 3)
        runs.append({"date": r["start_local"][:10], "efficiency": eff})

    # ── weekly averages (trend line) ──────────────────────────────────────────
    weeks: dict[str, list[float]] = defaultdict(list)
    for run in runs:
        d = datetime.fromisoformat(run["date"])
        monday = (d - timedelta(days=d.weekday())).strftime("%Y-%m-%d")
        weeks[monday].append(run["efficiency"])

    trend = [
        {"date": week, "efficiency": round(sum(effs) / len(effs), 3)}
        for week, effs in sorted(weeks.items())
    ]

    return {"runs": runs, "trend": trend}
