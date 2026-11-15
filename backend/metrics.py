"""Coaching metrics — every number the coaches need, computed in Python.

Why this exists: Garmin stores each lap of a workout as its own activity row, so
an interval session is ~10 rows. Before this module Claude had to merge laps,
guess which days were hard, and line up Sunday-start load weeks with the
athlete's Monday-start week. Now it's done once, here, the same way every time.

Conventions (match coaching/coaching_rules.md):
- Weeks run Monday → Sunday. Mon–Sat are running days, Sunday is rest.
- A "session" = consecutive running rows on the same day with < 45 min gaps.
- Paces are min/km floats internally and "m:ss" strings in output.

Public functions:
    sessions(days | start, end)   merged + classified runs
    week_summary(week_start)      one Mon–Sun week vs the plan
    current_paces()               training paces from the best recent effort (VDOT)
    readiness()                   green / amber / red from last night's HRV + resting HR
    long_run_status()             days since the last long run
    weekly_km(weeks)              running km per Mon–Sun week
    snapshot()                    all of the above for a check-in, in one dict
"""
from __future__ import annotations

import math
from datetime import date, datetime, timedelta
from typing import Any

from . import db, training_load

# ── Template (the athlete's agreed week; see coaching/coaching_rules.md) ────
TEMPLATE = {
    0: ("easy", "Easy + strides"),
    1: ("intervals", "VO2 / 5k intervals"),
    2: ("easy", "Medium-long easy"),
    3: ("tempo", "Threshold / tempo"),
    4: ("easy", "Easy (or bike) + strides"),
    5: ("long", "Long run + HM-pace block"),
    6: ("rest", "Rest"),
}
HARD_TYPES = {"intervals", "tempo", "long", "race"}
LONG_RUN_KM = 16.0          # a session this long counts as the long run
LONG_RUN_ALERT_DAYS = 14    # flag when no long run for this many days
MAX_HARD_DAYS = 3
SESSION_GAP_MIN = 45        # laps closer than this belong to one workout
WORK_MIN_KM = 0.3           # shorter fast bits are strides, not reps
HARD_HR = 168               # a rep with avg HR at/above this is a hard effort
VDOT_LOOKBACK_DAYS = 42

WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


# ── small helpers ───────────────────────────────────────────────────────
def fmt_pace(p: float | None) -> str | None:
    if not p or p <= 0 or p > 20:
        return None
    m = int(p)
    s = int(round((p - m) * 60))
    if s == 60:
        m, s = m + 1, 0
    return f"{m}:{s:02d}"


def monday(d: date) -> date:
    return d - timedelta(days=d.weekday())


def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts[:19])


def _runs_between(start: date, end: date) -> list[dict[str, Any]]:
    days = (date.today() - start).days + 2
    rows = db.get_recent_activities(days=max(days, 1), limit=5000)
    out = [r for r in rows
           if r.get("activity_type") == "running" and r.get("start_local")
           and start <= _parse(r["start_local"]).date() <= end
           and (r.get("distance_km") or 0) > 0 and (r.get("duration_min") or 0) > 0]
    out.sort(key=lambda r: r["start_local"])
    return out


# ── VDOT (Jack Daniels' running formula) ────────────────────────────────
def _vo2(v_m_min: float) -> float:
    return -4.60 + 0.182258 * v_m_min + 0.000104 * v_m_min ** 2


def _pct_max(t_min: float) -> float:
    return 0.8 + 0.1894393 * math.exp(-0.012778 * t_min) + 0.2989558 * math.exp(-0.1932605 * t_min)


def vdot_from(distance_km: float, time_min: float) -> float:
    v = distance_km * 1000 / time_min
    return _vo2(v) / _pct_max(time_min)


def _race_time(vdot: float, distance_km: float) -> float:
    """Predicted race time (min) for a distance at this VDOT (bisection)."""
    lo, hi = 1.0, 600.0
    for _ in range(80):
        mid = (lo + hi) / 2
        if vdot_from(distance_km, mid) > vdot:
            lo = mid          # too fast → need more time
        else:
            hi = mid
    return (lo + hi) / 2


def _pace_at_fraction(vdot: float, frac: float) -> float:
    """min/km at which VO2 cost = frac × VDOT."""
    target = vdot * frac
    a, b, c = 0.000104, 0.182258, -4.60 - target
    v = (-b + math.sqrt(b * b - 4 * a * c)) / (2 * a)   # m/min
    return 1000.0 / v


def _best_effort():
    """(vdot, endurance_vdot, discount, avg_km, row) from the best 3 km+ effort, or None."""
    start = date.today() - timedelta(days=VDOT_LOOKBACK_DAYS)
    best = None
    for r in _runs_between(start, date.today()):
        km, t = float(r["distance_km"]), float(r["duration_min"])
        if km < 3.0 or km > 43 or t <= 0 or t / km < 2.8:   # 2.8 = GPS-glitch guard
            continue
        v = vdot_from(km, t)
        if best is None or v > best[0]:
            best = (v, r)
    if best is None:
        return None
    vdot, r = best
    # Endurance discount: a short time trial over-predicts long-distance pace
    # when volume is low. Take 1 VDOT point off per 10 km/wk below 60 (max 3).
    wk = [w["km"] for w in weekly_km(4) if not w["partial"]]
    avg_km = sum(wk) / len(wk) if wk else 0.0
    discount = round(min(3.0, max(0.0, (60.0 - avg_km) / 10.0)), 1)
    return vdot, vdot - discount, discount, avg_km, r


def current_paces() -> dict[str, Any]:
    """Training paces from the best effort ≥ 3 km in the last 6 weeks.

    Uses single activity rows (a time-trial lap or a whole race), so a 3 km
    block inside a workout counts. Goal paces are NOT here — they live in the
    goals table / coaching rules.
    """
    best = _best_effort()
    if best is None:
        return {"error": f"no run of 3 km or more in the last {VDOT_LOOKBACK_DAYS} days"}
    vdot, evdot, discount, avg_km, r = best
    preds = {k: _race_time(vdot, d) for k, d in (("5k", 5.0), ("10k", 10.0))}
    preds.update({k: _race_time(evdot, d) for k, d in
                  (("half_marathon", 21.0975), ("marathon", 42.195))})

    # Easy pace from the athlete's own easy runs (HR-capped), not a formula.
    easy = [s for s in sessions(days=28) if s["type"] == "easy" and s["km"] >= 4 and s["avg_hr"]]
    easy_paces = sorted(s["duration_min"] / s["km"] for s in easy)
    easy_txt = (f"{fmt_pace(easy_paces[len(easy_paces) // 4])}-"
                f"{fmt_pace(easy_paces[(3 * len(easy_paces)) // 4])}"
                if len(easy_paces) >= 3 else None)

    def hms(m: float) -> str:
        s = int(round(m * 60))
        h, rem = divmod(s, 3600)
        mm, ss = divmod(rem, 60)
        return f"{h}:{mm:02d}:{ss:02d}" if h else f"{mm}:{ss:02d}"

    return {
        "vdot": round(vdot, 1),
        "endurance_vdot": round(evdot, 1),
        "endurance_discount": (f"-{discount} VDOT: avg {avg_km:.0f} km/wk over the last 4 weeks "
                               "is below 60" if discount else "none (volume >= 60 km/wk)"),
        "based_on": {
            "date": r["start_local"][:10],
            "distance_km": round(float(r["distance_km"]), 2),
            "time": hms(float(r["duration_min"])),
            "pace": fmt_pace(float(r["duration_min"]) / float(r["distance_km"])),
            "avg_hr": r.get("avg_hr"),
        },
        "predicted_race_times": {k: hms(v) for k, v in preds.items()},
        "training_paces_per_km": {
            "easy": easy_txt or "by feel, HR under ~150",
            "easy_hr_cap": 150,
            "marathon": fmt_pace(preds["marathon"] / 42.195),
            "half_marathon": fmt_pace(preds["half_marathon"] / 21.0975),
            "threshold": fmt_pace(_pace_at_fraction(evdot, 0.88)),
            "10k": fmt_pace(preds["10k"] / 10.0),
            "5k": fmt_pace(preds["5k"] / 5.0),
            "interval_vo2": fmt_pace(_pace_at_fraction(vdot, 0.975)),
            "repetition": fmt_pace(_pace_at_fraction(vdot, 1.05)),
        },
        "note": ("Daniels VDOT from the best effort of 3 km+ in the last "
                 f"{VDOT_LOOKBACK_DAYS} days. 5k/10k/interval/rep paces use the full VDOT; "
                 "threshold/half/marathon use the endurance VDOT (discounted while volume is "
                 "low). Easy = the athlete's own recent easy-run paces (keep HR under the cap)."),
    }


def _pace_refs() -> dict[str, float]:
    """Numeric reference paces (min/km) used for classification."""
    b = _best_effort()
    if b is not None:
        v, ev = b[0], b[1]
        return {"threshold": _pace_at_fraction(ev, 0.88),
                "tenk": _race_time(v, 10.0) / 10.0,
                "hm": _race_time(ev, 21.0975) / 21.0975}
    return {"threshold": 4.3, "tenk": 4.15, "hm": 4.35}   # fallback if no recent effort


# ── sessions ────────────────────────────────────────────────────────────
def _classify(sess: dict[str, Any], refs: dict[str, float]) -> None:
    fast_cut = refs["threshold"] + 0.20          # ~12 s/km slower than threshold
    work = [s for s in sess["segments"]
            if s["km"] >= WORK_MIN_KM and (s["pace"] <= fast_cut or (s["avg_hr"] or 0) >= HARD_HR)]
    sess["work"] = work

    if sess["km"] >= LONG_RUN_KM:
        kind = "long"
    elif not work:
        kind = "easy"
    elif (max(s["km"] for s in work) >= 2.5
          and max(s["km"] for s in work) >= 0.75 * sum(s["km"] for s in work)
          and max(work, key=lambda s: s["km"])["pace"] < refs["tenk"] - 0.05):
        kind = "race"            # one dominant continuous block faster than 10k pace = time trial / race
    else:
        wkm = sum(s["km"] for s in work)
        wpace = sum(s["min"] for s in work) / wkm
        kind = "intervals" if wpace < (refs["threshold"] + refs["tenk"]) / 2 else "tempo"
    sess["type"] = kind
    sess["hard"] = kind in HARD_TYPES


def _work_text(work: list[dict[str, Any]]) -> str | None:
    if not work:
        return None
    groups: list[list[dict[str, Any]]] = []
    for s in work:
        if groups and abs(groups[-1][0]["km"] - s["km"]) < 0.15:
            groups[-1].append(s)
        else:
            groups.append([s])
    parts = []
    for g in groups:
        km = sum(x["km"] for x in g) / len(g)
        pace = sum(x["min"] for x in g) / sum(x["km"] for x in g)
        hrs = [x["avg_hr"] for x in g if x["avg_hr"]]
        hr = f", HR {round(sum(hrs) / len(hrs))}" if hrs else ""
        dist = f"{km:.1f} km" if km >= 1 else f"{round(km * 1000, -1):.0f} m"
        parts.append(f"{len(g)}x{dist} @ {fmt_pace(pace)}{hr}" if len(g) > 1
                     else f"{dist} @ {fmt_pace(pace)}{hr}")
    return " + ".join(parts)


def sessions(days: int | None = 28, start: date | None = None,
             end: date | None = None) -> list[dict[str, Any]]:
    """Runs merged into workouts and classified easy / intervals / tempo / race / long."""
    end = end or date.today()
    start = start or (end - timedelta(days=(days or 28) - 1))
    rows = _runs_between(start, end)
    refs = _pace_refs()

    out: list[dict[str, Any]] = []
    cur: dict[str, Any] | None = None
    for r in rows:
        t0 = _parse(r["start_local"])
        seg = {"km": float(r["distance_km"]), "min": float(r["duration_min"]),
               "pace": float(r["duration_min"]) / float(r["distance_km"]),
               "avg_hr": r.get("avg_hr"), "max_hr": r.get("max_hr")}
        if (cur is not None and t0.date() == cur["_end"].date()
                and (t0 - cur["_end"]).total_seconds() / 60 <= SESSION_GAP_MIN):
            cur["segments"].append(seg)
        else:
            cur = {"_start": t0, "segments": [seg], "name": r.get("name")}
            out.append(cur)
        cur["_end"] = t0 + timedelta(minutes=seg["min"])

    result = []
    for s in out:
        segs = s["segments"]
        km = sum(x["km"] for x in segs)
        if km < 0.5:             # a stray stride/cool-down file saved on its own
            continue
        mins = sum(x["min"] for x in segs)
        hr_w = [(x["avg_hr"], x["min"]) for x in segs if x["avg_hr"]]
        s["km"] = km
        _classify(s, refs)
        d = s["_start"].date()
        result.append({
            "date": d.isoformat(),
            "weekday": WEEKDAYS[d.weekday()],
            "start": s["_start"].strftime("%H:%M"),
            "type": s["type"],
            "hard": s["hard"],
            "km": round(km, 2),
            "duration_min": round(mins, 1),
            "avg_pace": fmt_pace(mins / km),
            "avg_hr": round(sum(h * m for h, m in hr_w) / sum(m for _, m in hr_w)) if hr_w else None,
            "max_hr": max((x["max_hr"] or 0) for x in segs) or None,
            "laps": len(segs),
            "work": _work_text(s["work"]),
        })
    return result


# ── plan (saved by the Sunday report, else the template) ────────────────
def planned_days(week_start: date) -> tuple[str, dict[int, dict[str, str]]]:
    """Planned type per weekday (0=Mon) for a week, and where it came from."""
    wk = week_start.isoformat()
    for p in db.list_weekly_plans(limit=50):
        pj = p.get("plan_json") or {}
        if p.get("week_start") == wk and pj.get("source") == "weekly-report":
            plan = {}
            for day in pj.get("days", []):
                try:
                    wd = date.fromisoformat(day["date"]).weekday()
                except (KeyError, ValueError):
                    continue
                plan[wd] = {"type": (day.get("type") or "easy").lower(),
                            "title": day.get("title") or ""}
            plan.setdefault(6, {"type": "rest", "title": "Rest"})
            return "weekly report", plan
    return "template", {wd: {"type": t, "title": title} for wd, (t, title) in TEMPLATE.items()}


def _matches(planned: str, actual: str | None) -> bool:
    if planned == "rest":
        return actual is None
    if actual is None:
        return False
    if planned == "easy":
        return actual == "easy"
    if planned == "intervals":
        return actual in ("intervals", "race")
    return planned == actual


def week_summary(week_start: str | None = None) -> dict[str, Any]:
    """One Mon–Sun week, day by day, against the plan. Default: the current week."""
    ws = monday(date.fromisoformat(week_start)) if week_start else monday(date.today())
    we = ws + timedelta(days=6)
    sess = sessions(start=ws, end=min(we, date.today()))
    source, plan = planned_days(ws)
    today = date.today()

    days, hard_days, matched, scored = [], [], 0, 0
    for wd in range(7):
        d = ws + timedelta(days=wd)
        todays = [s for s in sess if s["date"] == d.isoformat()]
        hardest = next((s for s in todays if s["hard"]), todays[0] if todays else None)
        actual = hardest["type"] if hardest else None
        p = plan.get(wd, {"type": "easy", "title": ""})
        done = d <= today
        ok = _matches(p["type"], actual) if done else None
        if done:
            scored += 1
            matched += int(bool(ok))
        if any(s["hard"] for s in todays):
            hard_days.append(wd)
        days.append({"date": d.isoformat(), "weekday": WEEKDAYS[wd],
                     "planned": p, "actual": actual, "matches_plan": ok,
                     "sessions": todays})

    flags = []
    if len(hard_days) > MAX_HARD_DAYS:
        flags.append(f"{len(hard_days)} hard days (max {MAX_HARD_DAYS})")
    for a, b in zip(hard_days, hard_days[1:]):
        if b - a == 1:
            flags.append(f"hard days back to back: {WEEKDAYS[a]} + {WEEKDAYS[b]}")
    for wd in hard_days:
        if plan.get(wd, {}).get("type") in ("easy", "rest"):
            flags.append(f"hard session on a planned {plan[wd]['type']} day ({WEEKDAYS[wd]})")
    if days[6]["sessions"]:
        flags.append("ran on Sunday (rest day)")
    if (we <= today or today.weekday() >= 5) and not any(
            s["type"] == "long" for s in sess):
        if plan.get(5, {}).get("type") == "long" and (ws + timedelta(days=5)) <= today:
            flags.append("no long run this week")
    for dd in days:
        if dd["matches_plan"] is False and dd["planned"]["type"] not in ("rest",) \
                and dd["actual"] and dd["planned"]["type"] != dd["actual"]:
            flags.append(f"{dd['weekday']}: planned {dd['planned']['type']}, did {dd['actual']}")

    return {
        "week": f"{ws.isoformat()} to {we.isoformat()}",
        "complete": we < today,
        "plan_source": source,
        "running_km": round(sum(s["km"] for s in sess), 1),
        "sessions": len(sess),
        "hard_days": [WEEKDAYS[w] for w in hard_days],
        "plan_adherence": f"{matched}/{scored} days" if scored else None,
        "flags": flags,
        "days": days,
    }


# ── recovery ────────────────────────────────────────────────────────────
def readiness() -> dict[str, Any]:
    """Morning light from last night vs the previous 7 nights.

    red   = HRV < 80% of baseline, or resting HR ≥ 5 bpm above it
    amber = HRV < 90%, resting HR ≥ 3 above, or sleep score < 60
    green = otherwise
    """
    nights = [n for n in db.get_sleep_nights(limit=8) if n.get("night_date")]
    nights.sort(key=lambda n: n["night_date"])
    if not nights:
        return {"light": None, "reason": "no sleep data"}
    last, prev = nights[-1], nights[:-1]
    hrv_b = [n["hrv_ms"] for n in prev if n.get("hrv_ms")]
    rhr_b = [n["resting_hr"] for n in prev if n.get("resting_hr")]
    hrv_base = sum(hrv_b) / len(hrv_b) if hrv_b else None
    rhr_base = sum(rhr_b) / len(rhr_b) if rhr_b else None
    hrv, rhr, score = last.get("hrv_ms"), last.get("resting_hr"), last.get("score")

    ratio = (hrv / hrv_base) if (hrv and hrv_base) else None
    delta = (rhr - rhr_base) if (rhr and rhr_base) else None
    reasons = []
    light = "green"
    if (ratio is not None and ratio < 0.80) or (delta is not None and delta >= 5):
        light = "red"
    elif (ratio is not None and ratio < 0.90) or (delta is not None and delta >= 3) \
            or (score is not None and score < 60):
        light = "amber"
    if ratio is not None:
        reasons.append(f"HRV {hrv} ms vs 7-night avg {hrv_base:.0f} ({ratio * 100:.0f}%)")
    if delta is not None:
        reasons.append(f"resting HR {rhr} vs {rhr_base:.0f} ({delta:+.0f})")
    if score is not None:
        reasons.append(f"sleep score {score}")
    advice = {"green": "go as planned",
              "amber": "do the session but keep it controlled (cap reps / effort)",
              "red": "swap a hard session for easy running or rest"}[light]
    return {"night": last["night_date"], "light": light, "advice": advice,
            "reason": "; ".join(reasons)}


def long_run_status() -> dict[str, Any]:
    recent = [s for s in sessions(days=120) if s["km"] >= LONG_RUN_KM]
    if not recent:
        return {"last_long_run": None, "days_since": None,
                "alert": f"no run of {LONG_RUN_KM:.0f} km+ in 120 days"}
    last = recent[-1]
    since = (date.today() - date.fromisoformat(last["date"])).days
    return {"last_long_run": {"date": last["date"], "km": last["km"]},
            "days_since": since,
            "alert": (f"no long run ({LONG_RUN_KM:.0f} km+) for {since} days — protect Saturday"
                      if since >= LONG_RUN_ALERT_DAYS else None)}


def weekly_km(weeks: int = 4) -> list[dict[str, Any]]:
    this_mon = monday(date.today())
    out = []
    for i in range(weeks, -1, -1):
        ws = this_mon - timedelta(weeks=i)
        rows = _runs_between(ws, ws + timedelta(days=6))
        out.append({"week_start": ws.isoformat(),
                    "km": round(sum(float(r["distance_km"]) for r in rows), 1),
                    "partial": i == 0})
    return out


def snapshot() -> dict[str, Any]:
    """One call with everything a check-in needs."""
    tl = training_load.compute(weeks=6)
    this_mon = monday(date.today())
    return {
        "today": date.today().isoformat(),
        "acwr": tl["acwr"],
        "weekly_running_km": weekly_km(4),
        "weekly_load": [{"week_start": w["week_start"], "run": w["run_load"],
                         "ride": w["ride_load"], "total": w["load"],
                         "hrv": w["hrv"], "resting_hr": w["resting_hr"]}
                        for w in tl["weeks"][-5:]],
        "readiness": readiness(),
        "long_run": long_run_status(),
        "current_paces": current_paces(),
        "goals": db.list_goals(status="active"),
        "last_week": week_summary((this_mon - timedelta(weeks=1)).isoformat()),
        "this_week": week_summary(this_mon.isoformat()),
    }
