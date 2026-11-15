"""Body-weight log + calorie-model validation.

The idea: the app's "weekly deficit" (calories eaten - calories burned) is only an
estimate - both sides drift. Actual body weight doesn't. So we log weight daily,
take a 7-day average (which smooths out water / food-in-gut noise), compare this
week's average to last week's to get the REAL weight change, and check it against
what the calorie numbers predicted. ~7700 kcal is about 1 kg of body mass, so:

    predicted change (kg)  =  (sum of daily eaten - burned over the week) / 7700

The gap between measured and predicted tells us how far off, and in which
direction, the calorie tracking is - so the coach can trust it or correct it.
"""
from __future__ import annotations

from datetime import date, timedelta
from html import escape

from fastapi import APIRouter, Form
from fastapi.responses import HTMLResponse

from .. import db

router = APIRouter(prefix="/weight", tags=["weight"])

KCAL_PER_KG = 7700.0   # approx energy in 1 kg of body mass


@router.post("/log", response_class=HTMLResponse)
def log_weight(day: str = Form(...), weight_kg: float = Form(...)) -> HTMLResponse:
    """Record (or overwrite) one day's real body weight in kg."""
    day = (day or "").strip()
    try:
        date.fromisoformat(day)
    except ValueError:
        return HTMLResponse('<div class="banner error">Pick a valid date.</div>')
    if not (20 < weight_kg < 400):
        return HTMLResponse('<div class="banner error">Enter a realistic weight in kg.</div>')
    db.upsert_weight(day, round(float(weight_kg), 1))
    return HTMLResponse(
        f'<div class="banner success">Logged <strong>{weight_kg:.1f}&nbsp;kg</strong> '
        f'for {escape(day)}.</div>',
        headers={"HX-Trigger": "weightChanged"},
    )


@router.post("/bodyfat", response_class=HTMLResponse)
def set_bodyfat(bodyfat: float = Form(...)) -> HTMLResponse:
    """Store the current body-fat %, which feeds LBM and BMR."""
    if not (1 <= bodyfat <= 60):
        return HTMLResponse('<div class="banner error">Enter a realistic body-fat % (1-60).</div>')
    db.set_setting("bodyfat_pct", str(round(float(bodyfat), 1)))
    return HTMLResponse(
        f'<div class="banner success">Body fat set to <strong>{bodyfat:.1f}%</strong> '
        '- LBM &amp; BMR updated.</div>',
        headers={"HX-Trigger": "weightChanged"},
    )


@router.post("/activity", response_class=HTMLResponse)
def set_activity(activity: float = Form(...)) -> HTMLResponse:
    """Store the activity factor used for maintenance = BMR x factor."""
    if not (1.2 <= activity <= 2.2):
        return HTMLResponse('<div class="banner error">Activity factor is usually 1.2-2.0 '
                            '(sedentary to extremely active).</div>')
    db.set_setting("activity_factor", str(round(float(activity), 3)))
    db.set_setting("activity_mode", "manual")   # typing a value opts into manual
    return HTMLResponse(
        f'<div class="banner success">Manual activity factor set to <strong>x{activity:.3g}</strong> '
        '- maintenance updated.</div>',
        headers={"HX-Trigger": "weightChanged"},
    )


@router.post("/activity-mode", response_class=HTMLResponse)
def set_activity_mode(mode: str = Form(...)) -> HTMLResponse:
    """Switch the activity factor between 'dynamic' (derived from Garmin's measured
    burn) and 'manual' (the fixed number)."""
    mode = (mode or "").strip().lower()
    if mode not in ("dynamic", "manual"):
        return HTMLResponse('<div class="banner error">Unknown mode.</div>')
    db.set_setting("activity_mode", mode)
    label = "Auto (from Garmin active calories)" if mode == "dynamic" else "Manual"
    return HTMLResponse(
        f'<div class="banner success">Activity factor: <strong>{label}</strong>.</div>',
        headers={"HX-Trigger": "weightChanged"},
    )


def _window_avg(wmap: dict[str, float], end: date, n: int = 7):
    """Mean of the logged weights in the n-day window ending on `end` (inclusive).
    Returns (avg|None, days_logged)."""
    vals = [wmap[d] for i in range(n)
            if (d := (end - timedelta(days=i)).isoformat()) in wmap]
    return (sum(vals) / len(vals), len(vals)) if vals else (None, 0)


@router.get("/balance")
def weight_balance():
    """7-day-average weight, week-over-week change, and the calorie-model check."""
    weights = db.get_weights(limit=90)                       # oldest first
    wmap = {w["day_date"]: float(w["weight_kg"]) for w in weights}

    # daily series + a 7-day trailing average line for the chart
    series = []
    for w in weights:
        d = date.fromisoformat(w["day_date"])
        avg, _ = _window_avg(wmap, d)
        series.append({"date": w["day_date"], "kg": float(w["weight_kg"]),
                       "avg7": round(avg, 2) if avg is not None else None})

    today = date.today()
    cur_avg, cur_n = _window_avg(wmap, today)
    prev_avg, prev_n = _window_avg(wmap, today - timedelta(days=7))
    measured = (cur_avg - prev_avg) if (cur_avg is not None and prev_avg is not None) else None

    # calorie model over the current 7 days (complete days only: both sides present
    # and a full day's burn is always > ~1200 kcal of resting metabolism)
    nut = {r["log_date"]: r for r in db.get_nutrition_days(limit=40)}
    cal = {r["day_date"]: r for r in db.get_calories_days(limit=40)}
    net_sum = 0.0
    net_days = 0
    for i in range(7):
        d = (today - timedelta(days=i)).isoformat()
        nrow, crow = nut.get(d), cal.get(d)
        eaten = float(nrow["kcal"]) if nrow and nrow.get("kcal") not in (None, "") else None
        burned = (float(crow["total_cal"]) if crow and crow.get("total_cal") not in (None, "")
                  and float(crow["total_cal"]) > 1200 else None)
        if eaten is not None and burned is not None:
            net_sum += eaten - burned
            net_days += 1
    predicted = (net_sum / KCAL_PER_KG) if net_days else None

    discrepancy = daily_cal_error = verdict = status = None
    if measured is not None and predicted is not None:
        discrepancy = measured - predicted               # +ve: lost less / gained more than predicted
        daily_cal_error = round(discrepancy * KCAL_PER_KG / 7)
        if abs(discrepancy) <= 0.2:
            status = "ok"
            verdict = ("The calorie numbers check out - your real change is within "
                       "~0.2 kg of what the deficit predicted.")
        elif discrepancy > 0:
            status = "over"
            verdict = (f"The app OVER-estimates your deficit by about {abs(daily_cal_error):,} kcal/day "
                       "- you lost less (or gained more) than the numbers said, so your true "
                       "intake is higher or your burn is lower than tracked.")
        else:
            status = "under"
            verdict = (f"The app UNDER-estimates your deficit by about {abs(daily_cal_error):,} kcal/day "
                       "- you lost more than the numbers said, so you are eating less or "
                       "burning more than tracked.")

    # ── Body composition (Katch-McArdle): LBM = kg x (1 - bf%); BMR = 370 + 21.6 x LBM.
    # Keyed on the 7-day average weight so it moves with the trend, not a noisy daily number.
    bodyfat = float(db.get_setting("bodyfat_pct", "14") or 14)
    basis_kg = cur_avg if cur_avg is not None else (
        float(weights[-1]["weight_kg"]) if weights else None)
    lbm = bmr = None
    if basis_kg is not None:
        lbm = basis_kg * (1 - bodyfat / 100.0)
        bmr = 370 + 21.6 * lbm

    # Garmin-measured burn: avg of complete days' total_cal over the current 7 days.
    g_burns = [float(cal[d]["total_cal"]) for i in range(7)
               if (d := (today - timedelta(days=i)).isoformat()) in cal
               and cal[d].get("total_cal") not in (None, "")
               and float(cal[d]["total_cal"]) > 1200]
    garmin_tdee = round(sum(g_burns) / len(g_burns)) if g_burns else None

    # Activity factor: 'dynamic' derives it from Garmin (TDEE / BMR) so it self-adjusts
    # to the week's real load (taper weeks fall, peak weeks rise); 'manual' is a fixed
    # fallback for weeks with sparse Garmin data. Maintenance = BMR x factor.
    mode = db.get_setting("activity_mode", "dynamic")
    manual_factor = float(db.get_setting("activity_factor", "1.725") or 1.725)
    dynamic_factor = (round(garmin_tdee / bmr, 3)
                      if (garmin_tdee is not None and bmr) else None)
    if mode == "dynamic" and dynamic_factor is not None:
        activity, maintenance = dynamic_factor, garmin_tdee
    else:
        mode = "manual"
        activity = manual_factor
        maintenance = round(bmr * manual_factor) if bmr is not None else None

    return {
        "latest": ({"date": weights[-1]["day_date"], "kg": float(weights[-1]["weight_kg"])}
                   if weights else None),
        "bodyfat": bodyfat,
        "lbm": round(lbm, 1) if lbm is not None else None,
        "bmr": round(bmr) if bmr is not None else None,
        "activity": activity,
        "activity_mode": mode,
        "dynamic_factor": dynamic_factor,
        "manual_factor": manual_factor,
        "maintenance": maintenance,
        "garmin_tdee": garmin_tdee,
        "comp_basis": ("7-day avg" if cur_avg is not None else
                       ("latest" if basis_kg is not None else None)),
        "cur_avg": round(cur_avg, 2) if cur_avg is not None else None,
        "prev_avg": round(prev_avg, 2) if prev_avg is not None else None,
        "cur_n": cur_n, "prev_n": prev_n,
        "measured": round(measured, 2) if measured is not None else None,
        "net_sum": round(net_sum) if net_days else None,
        "net_days": net_days,
        "predicted": round(predicted, 2) if predicted is not None else None,
        "discrepancy": round(discrepancy, 2) if discrepancy is not None else None,
        "daily_cal_error": daily_cal_error,
        "status": status, "verdict": verdict,
        "series": series,
    }
