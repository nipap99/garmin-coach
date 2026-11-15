"""JSON endpoints that supply data to the Chart.js charts on the dashboard.

All routes are under /stats and return plain JSON (not HTML fragments).
The frontend fetches these with the native fetch() API and hands the
data to Chart.js.
"""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Query

from .. import db, metrics, training_load as training_load_mod

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


@router.get("/sleep/hrv-trend")
def sleep_hrv_trend():
    """Weekly-averaged overnight HRV — the long-term recovery baseline."""
    rows = db.get_weekly_hrv()
    return {
        "labels": [r["week_start"] for r in rows],
        "hrv":    [int(r["hrv"]) if r["hrv"] is not None else None for r in rows],
        "nights": [int(r["nights"]) for r in rows],
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
    """Burned-calorie averages (last 30 days) + average daily food intake."""
    s = db.get_calories_summary()
    days = db.get_nutrition_days(limit=30)
    intakes = [int(d["kcal"] or 0) for d in days if d.get("kcal")]
    avg_intake = round(sum(intakes) / len(intakes)) if intakes else 0
    return {
        "days_tracked": int(s["days_tracked"]) if s.get("days_tracked") else 0,
        "avg_total":    int(s["avg_total"]) if s.get("avg_total") else 0,
        "avg_active":   int(s["avg_active"]) if s.get("avg_active") else 0,
        "avg_resting":  int(s["avg_resting"]) if s.get("avg_resting") else 0,
        "avg_intake":   avg_intake,
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


def _plan_today(today_iso: str, weekday_name: str) -> dict:
    """Today's session from the plan the Sunday report saved (weekly_plans,
    source "weekly-report"), else the agreed template day."""
    today_d = date.fromisoformat(today_iso)
    source, plan = metrics.planned_days(metrics.monday(today_d))
    day = plan.get(today_d.weekday())
    if not day:
        return {"has": False}
    full = {}
    if source == "weekly report":
        for p in db.list_weekly_plans(limit=50):
            pj = p.get("plan_json") or {}
            if pj.get("source") == "weekly-report":
                full = next((d for d in pj.get("days", []) if d.get("date") == today_iso), {})
                if full:
                    break
    wtype = day["type"]
    return {
        "has": True,
        "source": source,
        "workout_type": wtype,
        "title": day.get("title") or wtype.capitalize(),
        "target": full.get("target") or "",
        "main": full.get("main") or "",
        "is_rest": wtype == "rest",
    }


@router.get("/today")
def today_overview():
    """Cross-domain summary for the Today landing tab: readiness, planned
    workout, energy balance, and a quick stats strip."""
    today = date.today()
    today_iso = today.isoformat()

    # ── Readiness — last night's sleep ───────────────────────────────────
    nights = db.get_sleep_nights(limit=1)
    night = nights[-1] if nights else None
    if night:
        readiness = {
            "has": True,
            "score": int(night["score"]) if night.get("score") is not None else None,
            "sleep_fmt": _fmt_hm(night.get("duration_min")),
            "hrv_ms": int(night["hrv_ms"]) if night.get("hrv_ms") is not None else None,
            "resting_hr": int(night["resting_hr"]) if night.get("resting_hr") is not None else None,
            "night_date": night.get("night_date"),
        }
        light = metrics.readiness()
        readiness.update({"light": light.get("light"), "light_reason": light.get("reason"),
                          "light_advice": light.get("advice")})
    else:
        readiness = {"has": False}

    # ── Energy balance — eaten (food log) vs burned (Garmin) ─────────────
    nut = db.get_nutrition_day(today_iso)
    eaten = int(nut.get("kcal") or 0)
    has_eaten = int(nut.get("items") or 0) > 0

    cal_rows = db.get_calories_days(limit=14)
    by_date = {r["day_date"]: r for r in cal_rows}
    crow = by_date.get(today_iso) or (cal_rows[-1] if cal_rows else None)
    burned = int(crow["total_cal"]) if crow and crow.get("total_cal") is not None else 0
    has_burned = crow is not None
    energy = {
        "eaten": eaten,
        "burned": burned,
        "balance": (eaten - burned) if (has_eaten and has_burned) else None,
        "has_eaten": has_eaten,
        "has_burned": has_burned,
        "burned_date": crow["day_date"] if crow else None,
    }

    # ── Planned session today ────────────────────────────────────────────
    plan = _plan_today(today_iso, today.strftime("%A"))

    # ── Quick stats strip ────────────────────────────────────────────────
    summary = db.get_stats_summary()
    acts = db.get_recent_activities(days=200, limit=200)
    vo2 = next((a.get("vo2max") for a in acts if a.get("vo2max")), None)
    stats = {
        "week_km": round(float(summary.get("week_km") or 0), 1),
        "protein_g": round(float(nut.get("protein_g") or 0)) if has_eaten else None,
        "resting_hr": readiness.get("resting_hr") if readiness.get("has") else None,
        "vo2max": round(float(vo2), 1) if vo2 else None,
    }

    return {
        "date": today_iso,
        "date_fmt": f"{today.strftime('%A')}, {today.day} {today.strftime('%B %Y')}",
        "readiness": readiness,
        "energy": energy,
        "plan": plan,
        "stats": stats,
    }


@router.get("/energy-balance")
def energy_balance(days: int = Query(14, ge=3, le=120)):
    """Per-day net energy: calories eaten (food log) − calories burned (Garmin).

    The day list is anchored on days you actually logged food (a balance is only
    meaningful then). Burned comes from the Calories table; if a day hasn't been
    synced, ``burned``/``net`` are null so the UI can show 'pending sync' rather
    than a misleading number. ``net_sum`` totals only the days where both exist.
    """
    nut = {r["log_date"]: r for r in db.get_nutrition_days(limit=days)}
    cal = {r["day_date"]: r for r in db.get_calories_days(limit=days + 7)}

    out: list[dict] = []
    net_sum = 0
    for d in sorted(nut.keys())[-days:]:
        eaten = int(nut[d]["kcal"] or 0)
        crow = cal.get(d)
        burned = int(crow["total_cal"]) if crow and crow.get("total_cal") is not None else None
        net = (eaten - burned) if burned is not None else None
        if net is not None:
            net_sum += net
        out.append({"date": d, "eaten": eaten, "burned": burned, "net": net})

    return {"days": out, "net_sum": net_sum}


@router.get("/training-load")
def training_load(
    weeks: int = Query(14, ge=4, le=52),
    sport: str | None = Query(None, pattern="^(running|cycling)$"),
):
    """Weekly HR-based training load (Banister TRIMP), the acute:chronic workload
    ratio (ACWR), and the HRV / resting-HR recovery overlay.

    ``sport`` omitted = combined run + ride (the systemic load, used by Today);
    ``running`` / ``cycling`` = a single-sport view for the per-sport tabs. The
    maths lives in ``backend/training_load.py`` so the coach reads the same
    numbers — see the docs' Scientific basis section."""
    return training_load_mod.compute(weeks, sport=sport)


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
        wk = (d - timedelta(days=(d.weekday() + 1) % 7)).strftime("%Y-%m-%d")
        weeks[wk].append(run["efficiency"])

    trend = [
        {"date": week, "efficiency": round(sum(effs) / len(effs), 3)}
        for week, effs in sorted(weeks.items())
    ]

    return {"runs": runs, "trend": trend}


@router.get("/cycling/aerobic-efficiency")
def cycling_aerobic_efficiency():
    """Aerobic efficiency per ride (avg_speed_kmh / avg_hr * 100) + weekly trend.

    The cycling analogue of the running chart: speed is recorded directly, so
    efficiency is simply speed ÷ heart rate. Higher = faster for the same effort.
    Same {runs, trend} shape as ``/aerobic-efficiency`` so the frontend reuses it.
    """
    rows = db.get_cycling_aerobic_efficiency_rides()
    if not rows:
        return {"runs": [], "trend": []}

    runs: list[dict] = []
    for r in rows:
        spd = float(r["avg_speed_kmh"])
        hr = float(r["avg_hr"])
        if spd <= 0 or hr <= 0:
            continue
        runs.append({"date": r["start_local"][:10], "efficiency": round(spd / hr * 100, 3)})

    weeks: dict[str, list[float]] = defaultdict(list)
    for run in runs:
        d = datetime.fromisoformat(run["date"])
        wk = (d - timedelta(days=(d.weekday() + 1) % 7)).strftime("%Y-%m-%d")
        weeks[wk].append(run["efficiency"])

    trend = [
        {"date": week, "efficiency": round(sum(effs) / len(effs), 3)}
        for week, effs in sorted(weeks.items())
    ]

    return {"runs": runs, "trend": trend}
