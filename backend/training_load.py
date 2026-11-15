"""Training load (Banister TRIMP) and the acute:chronic workload ratio (ACWR).

Both KPIs are **computed on demand** from stored raw data — activities that have
a heart rate, plus resting HR / HRV from sleep. Nothing here is persisted: TRIMP
and ACWR are *derived* numbers, recomputed each call from the source rows. See
the documentation's "Scientific basis" section for the why.

Shared by the Today chart (``routes/stats.py``) and the running coach
(``coach.py``) so both read identical numbers.
"""
from __future__ import annotations

import math
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any

from . import db


def _sunday(d: datetime) -> str:
    """Sunday (YYYY-MM-DD) that begins the week of a datetime — matches db._week_start."""
    return (d - timedelta(days=(d.weekday() + 1) % 7)).strftime("%Y-%m-%d")


def compute(weeks: int = 14, sport: str | None = None) -> dict[str, Any]:
    """Weekly TRIMP load, ACWR, and the HRV/resting-HR recovery overlay.

    TRIMP per session = duration · HRr · 0.64 · e^(1.92·HRr), where
    HRr = (avg_hr − resting_hr) / (max_hr − resting_hr). Resting HR is averaged
    from recent sleep nights; max HR is the highest ever recorded.

    ``sport`` filters the sessions: None = both (the systemic load the body
    actually feels — the default for the Today view), "running" or "cycling" for
    a single-sport view (the per-sport tabs). Each returned week also carries a
    rolling ``acwr`` (that week's load ÷ the mean of the previous 4 weeks) so the
    ratio can be charted as a trend, alongside the single current ``acwr``.
    """
    span_days = weeks * 7 + 28          # extra history for the chronic baseline
    sessions = db.get_hr_sessions(days=span_days)
    if sport in ("running", "cycling"):
        want = "running" if sport == "running" else "cycling"
        sessions = [s for s in sessions if s["activity_type"] == want]

    nights = db.get_sleep_nights(limit=120)
    rhrs = [n["resting_hr"] for n in nights if n.get("resting_hr")]
    rest_hr = round(sum(rhrs) / len(rhrs)) if rhrs else 60
    max_hr = db.get_observed_max_hr() or 190
    if max_hr - rest_hr < 30:           # sanity guard against bad HR data
        max_hr = rest_hr + 100

    def trimp(duration_min: float, avg_hr: float) -> float:
        hrr = (avg_hr - rest_hr) / (max_hr - rest_hr)
        hrr = max(0.0, min(1.0, hrr))
        return duration_min * hrr * 0.64 * math.exp(1.92 * hrr)

    # ── daily load by sport ───────────────────────────────────────────────
    daily: dict[str, dict[str, float]] = defaultdict(lambda: {"run": 0.0, "ride": 0.0})
    for s in sessions:
        day = s["start_local"][:10]
        key = "run" if s["activity_type"] == "running" else "ride"
        daily[day][key] += trimp(float(s["duration_min"]), float(s["avg_hr"]))

    # ── weekly load (Sunday weeks) ────────────────────────────────────────
    weekly: dict[str, dict[str, float]] = defaultdict(lambda: {"run": 0.0, "ride": 0.0})
    for day, v in daily.items():
        wk = _sunday(datetime.fromisoformat(day))
        weekly[wk]["run"] += v["run"]
        weekly[wk]["ride"] += v["ride"]

    # ── recovery overlay per week (avg HRV + resting HR from nights) ───────
    wk_hrv: dict[str, list] = defaultdict(list)
    wk_rhr: dict[str, list] = defaultdict(list)
    for n in nights:
        nd = n.get("night_date")
        if not nd:
            continue
        wk = _sunday(datetime.fromisoformat(nd))
        if n.get("hrv_ms"):
            wk_hrv[wk].append(n["hrv_ms"])
        if n.get("resting_hr"):
            wk_rhr[wk].append(n["resting_hr"])

    # ── chronic baseline + rolling ACWR per week ──────────────────────────
    # baseline = mean load of the previous 4 weeks (what the body is adapted to);
    # ACWR     = this week's load ÷ that baseline.
    ordered = sorted(weekly.keys())
    load_of = {w: round(weekly[w]["run"] + weekly[w]["ride"]) for w in ordered}
    acwr_of: dict[str, float | None] = {}
    base_of: dict[str, float | None] = {}
    for i, w in enumerate(ordered):
        prior = ordered[max(0, i - 4):i]
        if len(prior) < 4:
            acwr_of[w] = base_of[w] = None   # not enough history for a baseline yet
            continue
        base = sum(load_of[p] for p in prior) / 4.0
        base_of[w] = base
        acwr_of[w] = round(load_of[w] / base, 2) if base > 0 else None

    # The week containing today is still accruing — flag it so the UI can show
    # it as "in progress" rather than as a sudden drop.
    this_week = _sunday(datetime.now())

    weeks_out = []
    for wk in ordered[-weeks:]:
        run = round(weekly[wk]["run"])
        ride = round(weekly[wk]["ride"])
        hrv = round(sum(wk_hrv[wk]) / len(wk_hrv[wk])) if wk_hrv.get(wk) else None
        rhr = round(sum(wk_rhr[wk]) / len(wk_rhr[wk])) if wk_rhr.get(wk) else None
        base = base_of.get(wk)
        weeks_out.append({"week_start": wk, "run_load": run, "ride_load": ride,
                          "load": run + ride, "hrv": hrv, "resting_hr": rhr,
                          "acwr": acwr_of.get(wk),
                          "baseline": round(base) if base else None,
                          "partial": wk == this_week})

    # ── ACWR (acute 7-day load ÷ chronic 28-day weekly average) ────────────
    today = date.today()

    def load_since(n: int) -> float:
        floor = today - timedelta(days=n - 1)
        return sum(v["run"] + v["ride"] for day, v in daily.items()
                   if floor <= date.fromisoformat(day) <= today)

    acute = load_since(7)
    chronic = load_since(28) / 4.0
    acwr = round(acute / chronic, 2) if chronic > 0 else None
    if acwr is None:
        status = "no data"
    elif acwr < 0.8:
        status = "low"
    elif acwr <= 1.3:
        status = "optimal"
    elif acwr <= 1.5:
        status = "high"
    else:
        status = "very high"

    return {
        "weeks": weeks_out,
        "acwr": {"value": acwr, "acute": round(acute), "chronic": round(chronic), "status": status},
        "params": {"max_hr": max_hr, "resting_hr": rest_hr},
    }
