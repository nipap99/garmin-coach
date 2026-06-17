"""Weekly training plan generation and management.

POST /plans/generate  — calls Claude to build a 7-day JSON plan, stores it.
GET  /plans           — returns current (non-archived) plans as HTML cards.
POST /plans/{id}/accept — marks a plan as accepted.
DELETE /plans/{id}    — archives a plan (removes from view).
"""
from __future__ import annotations

import json
import logging
import re
from datetime import date, timedelta
from typing import Any

from anthropic import Anthropic
from fastapi import APIRouter, Form
from fastapi.responses import HTMLResponse

from .. import config, db
from ..coach import load_persona

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/plans", tags=["plans"])

# ─── Workout type display config ─────────────────────────────────────────────

WORKOUT_BADGES: dict[str, tuple[str, str]] = {
    "easy":      ("Easy",        "#1f8a4c"),
    "tempo":     ("Tempo",       "#f5a623"),
    "intervals": ("Intervals",   "#e05252"),
    "long":      ("Long Run",    "#4f8cff"),
    "rest":      ("Rest",        "#98a2b3"),
    "cross":     ("Cross-Train", "#9b59b6"),
}


# ─── Plan generation ─────────────────────────────────────────────────────────

def _next_monday() -> date:
    today = date.today()
    days = (7 - today.weekday()) % 7
    return today + timedelta(days=days if days else 7)


def _fmt_pace(pace: float) -> str:
    m = int(pace)
    s = round((pace - m) * 60)
    if s == 60:
        m += 1; s = 0
    return f"{m}:{s:02d}/km"


def _activities_text(activities: list[dict]) -> str:
    if not activities:
        return "  No recent activities."
    lines = []
    for a in activities[:20]:
        pace = a.get("avg_pace_min_per_km")
        pace_str = _fmt_pace(float(pace)) if pace else "—"
        lines.append(
            f"  {(a.get('start_local') or '')[:10]}  "
            f"{(a.get('distance_km') or 0):.1f} km  "
            f"{(a.get('duration_min') or 0):.0f} min  "
            f"pace {pace_str}  HR {a.get('avg_hr') or '—'}"
        )
    return "\n".join(lines)


def _goals_text(goals: list[dict]) -> str:
    if not goals:
        return "  No active goals."
    lines = []
    for g in goals:
        t = g.get("target_time_seconds")
        if t:
            h, rem = divmod(int(t), 3600)
            m, s = divmod(rem, 60)
            tstr = f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"
        else:
            tstr = "no time target"
        lines.append(f"  {g['distance'].replace('_', ' ')}: target {tstr}")
    return "\n".join(lines)


def _cycling_text(rides: list[dict]) -> str:
    if not rides:
        return "  No recent rides."
    lines = []
    for a in rides[:14]:
        spd = a.get("avg_speed_kmh")
        spd_str = f"{float(spd):.1f} km/h" if spd else "—"
        lines.append(
            f"  {(a.get('start_local') or '')[:10]}  "
            f"{(a.get('distance_km') or 0):.1f} km  "
            f"{(a.get('duration_min') or 0):.0f} min  "
            f"{spd_str}  HR {a.get('avg_hr') or '—'}"
        )
    return "\n".join(lines)


def _sleep_text(nights: list[dict]) -> str:
    if not nights:
        return "  No recent sleep data."
    lines = []
    for n in nights[-10:]:  # nights are oldest-first; show the most recent 10
        dur = n.get("duration_min")
        dur_str = f"{int(dur) // 60}h{int(dur) % 60:02d}" if dur else "—"
        lines.append(
            f"  {n.get('night_date')}  score {n.get('score') or '—'}  "
            f"{dur_str}  HRV {n.get('hrv_ms') or '—'}ms  RHR {n.get('resting_hr') or '—'}"
        )
    return "\n".join(lines)


def _generate_plan_json(
    activities: list[dict],
    goals: list[dict],
    rides: list[dict] | None = None,
    nights: list[dict] | None = None,
) -> dict[str, Any]:
    """Call Claude and return the validated plan dict."""
    next_mon = _next_monday()
    days_dates = [(next_mon + timedelta(i)).isoformat() for i in range(7)]
    day_names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

    persona = load_persona()
    persona_block = (
        f"\nThe runner wrote these coaching preferences — honor them:\n{persona}\n"
        if persona else ""
    )

    prompt = f"""You are a running coach. Generate a 7-day training plan for next week.

Today: {date.today().isoformat()}
Week: {next_mon.isoformat()} (Mon) to {days_dates[6]} (Sun)
{persona_block}
Recent running (last 30 days):
{_activities_text(activities)}

Recent cycling (cross-training — adds aerobic load/fatigue, counts toward the week):
{_cycling_text(rides or [])}

Recent sleep (recovery — bias intensity to recent sleep score / HRV / resting HR):
{_sleep_text(nights or [])}

Runner's goals:
{_goals_text(goals)}

Return ONLY a raw JSON object — no markdown fences, no explanation, just the JSON:
{{
  "week_start": "{next_mon.isoformat()}",
  "summary": "One sentence describing the week's training focus",
  "weekly_km": 0.0,
  "days": [
    {{
      "day": "Monday",
      "date": "{days_dates[0]}",
      "workout_type": "easy",
      "title": "Short workout title",
      "description": "Specific details — target pace, HR zone, intervals, etc.",
      "distance_km": 0.0,
      "duration_min": 0,
      "intensity": "easy"
    }}
  ]
}}

Rules:
- Include all 7 days in order: {", ".join(f"{n} ({d})" for n, d in zip(day_names, days_dates))}
- workout_type: one of easy, tempo, intervals, long, rest, cross
- intensity: one of easy, moderate, hard, rest
- Base the plan on the runner's recent weekly mileage — don't jump more than 10%
- Account for cycling load: if cycling volume is high, leave less room for hard running and avoid stacking a hard run right after a long/hard ride
- Account for recovery: if recent sleep is short or HRV is trending down / resting HR is up, bias toward easier sessions and place hard workouts on better-recovered days
- For rest days: distance_km = 0, duration_min = 0
- Be specific with targets (e.g. "5:30–5:45/km", "HR below 148")"""

    client = Anthropic(api_key=config.ANTHROPIC_API_KEY)
    response = client.messages.create(
        model="claude-opus-4-7",
        max_tokens=2000,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = response.content[0].text.strip()
    # Strip markdown code fences if present
    match = re.search(r"\{[\s\S]*\}", raw)
    if not match:
        raise ValueError("No JSON block found in Claude's response.")
    plan = json.loads(match.group())
    if "days" not in plan or len(plan["days"]) < 7:
        raise ValueError(f"Plan has only {len(plan.get('days', []))} days (expected 7).")
    return plan


def _parse_plan_text(text: str) -> dict[str, Any]:
    """Convert a free-text training plan (pasted from the AI chat) into the
    structured plan JSON used by the calendar view. Calls Claude to do the
    extraction so it tolerates any formatting the coach happened to use.
    """
    next_mon = _next_monday()
    days_dates = [(next_mon + timedelta(i)).isoformat() for i in range(7)]
    day_names = ["Monday", "Tuesday", "Wednesday", "Thursday",
                 "Friday", "Saturday", "Sunday"]

    prompt = f"""You are given a running training plan written in free-form text
(it was produced by a coaching assistant in a chat). Convert it into a
structured 7-day week. If the text covers fewer than 7 days, fill the missing
days as rest. If it covers more than a week, use only the first 7 days.

Assume the week starts Monday {next_mon.isoformat()} unless the text says otherwise.

--- PLAN TEXT START ---
{text.strip()}
--- PLAN TEXT END ---

Return ONLY a raw JSON object — no markdown fences, no explanation:
{{
  "week_start": "{next_mon.isoformat()}",
  "summary": "One sentence describing the week's focus (infer it from the plan)",
  "weekly_km": 0.0,
  "source": "chat",
  "days": [
    {{
      "day": "Monday",
      "date": "{days_dates[0]}",
      "workout_type": "easy",
      "title": "Short workout title",
      "description": "The details from the plan text for this day",
      "distance_km": 0.0,
      "duration_min": 0,
      "intensity": "easy"
    }}
  ]
}}

Rules:
- Include all 7 days in order: {", ".join(f"{n} ({d})" for n, d in zip(day_names, days_dates))}
- workout_type: one of easy, tempo, intervals, long, rest, cross
- intensity: one of easy, moderate, hard, rest
- For rest days: distance_km = 0, duration_min = 0
- Keep the description faithful to what the plan text actually says — do not invent workouts
- If distance or duration is not stated for a day, set it to 0
- weekly_km = sum of all daily distances"""

    client = Anthropic(api_key=config.ANTHROPIC_API_KEY)
    response = client.messages.create(
        model="claude-opus-4-7",
        max_tokens=2000,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = response.content[0].text.strip()
    match = re.search(r"\{[\s\S]*\}", raw)
    if not match:
        raise ValueError("Could not read a plan out of that text.")
    plan = json.loads(match.group())
    if "days" not in plan or len(plan["days"]) < 7:
        raise ValueError(
            f"Only found {len(plan.get('days', []))} days in that text (need 7)."
        )
    plan["source"] = "chat"
    return plan


# ─── HTML rendering ──────────────────────────────────────────────────────────

def _render_day_card(day: dict) -> str:
    wtype = day.get("workout_type", "easy")
    badge_label, badge_color = WORKOUT_BADGES.get(wtype, ("Run", "#4f8cff"))
    dist = float(day.get("distance_km") or 0)
    dur = int(day.get("duration_min") or 0)
    meta_parts = []
    if dist > 0:
        meta_parts.append(f"{dist:.1f} km")
    if dur > 0:
        meta_parts.append(f"{dur} min")
    meta_html = (
        f'<div class="day-meta">{" · ".join(meta_parts)}</div>'
        if meta_parts else ""
    )
    return (
        f'<div class="day-card">'
        f'<div class="day-header">'
        f'<span class="day-name">{day.get("day", "")}</span>'
        f'<span class="day-date">{(day.get("date") or "")[:10]}</span>'
        f'</div>'
        f'<span class="workout-badge" style="background:{badge_color}22;color:{badge_color};'
        f'border:1px solid {badge_color}44">{badge_label}</span>'
        f'<div class="day-title">{day.get("title", "")}</div>'
        f'<div class="day-desc">{day.get("description", "")}</div>'
        f'{meta_html}'
        f'</div>'
    )


def _render_plan_card(plan: dict) -> str:
    pj = plan.get("plan_json") or {}
    if isinstance(pj, str):
        try:
            pj = json.loads(pj)
        except Exception:
            pj = {}

    plan_id = plan["id"]
    is_accepted = plan.get("status") == "accepted"
    days_html = "".join(_render_day_card(d) for d in pj.get("days", []))
    weekly_km = float(pj.get("weekly_km") or 0)

    status_badge = (
        '<span class="status-badge status-accepted">&#10003; Active Plan</span>'
        if is_accepted else
        '<span class="status-badge status-proposed">Proposed</span>'
    )
    accept_btn = "" if is_accepted else (
        f'<button class="btn btn-success btn-small"'
        f' hx-post="/plans/{plan_id}/accept"'
        f' hx-target="#plans-panel"'
        f' hx-confirm="Set this as your active training plan?">Accept</button>'
    )
    return (
        f'<div class="plan-card{"  plan-accepted" if is_accepted else ""}">'
        f'<div class="plan-header">'
        f'<div class="plan-meta">'
        f'<div class="plan-week">Week of {pj.get("week_start", "")}</div>'
        f'<div class="plan-summary">{pj.get("summary", "")}</div>'
        f'<div class="plan-total">{weekly_km:.1f} km planned this week</div>'
        f'</div>'
        f'<div class="plan-actions">'
        f'{status_badge}'
        f'{accept_btn}'
        f'<button class="btn btn-outline btn-small"'
        f' hx-post="/plans/generate"'
        f' hx-target="#plans-panel"'
        f' hx-indicator="#plan-spinner">Regenerate</button>'
        f'<button class="btn btn-ghost btn-small"'
        f' hx-delete="/plans/{plan_id}"'
        f' hx-target="#plans-panel"'
        f' hx-confirm="Archive this plan?">Archive</button>'
        f'</div>'
        f'</div>'
        f'<div class="days-grid">{days_html}</div>'
        f'</div>'
    )


def _render_plans(plans: list[dict]) -> str:
    if not plans:
        return (
            '<div class="empty">No training plan yet. '
            'Click <strong>Generate My Week</strong> to create one.</div>'
        )
    return "".join(_render_plan_card(p) for p in plans)


def _render_calendar_cell(day: dict) -> str:
    """One day column in the Mon–Sun calendar grid."""
    wtype = day.get("workout_type", "easy")
    badge_label, color = WORKOUT_BADGES.get(wtype, ("Run", "#4f8cff"))
    dist = float(day.get("distance_km") or 0)
    dur = int(day.get("duration_min") or 0)
    is_rest = wtype == "rest" or (dist == 0 and dur == 0)

    meta_parts = []
    if dist > 0:
        meta_parts.append(f"{dist:.1f} km")
    if dur > 0:
        meta_parts.append(f"{dur} min")
    meta_html = (
        f'<div class="cal-meta">{" · ".join(meta_parts)}</div>' if meta_parts else ""
    )

    body = (
        '<div class="cal-rest">Rest</div>'
        if is_rest else
        f'<div class="cal-title">{day.get("title", "")}</div>'
        f'<div class="cal-desc">{day.get("description", "")}</div>'
        f'{meta_html}'
    )

    return (
        f'<div class="cal-cell{" cal-cell-rest" if is_rest else ""}" '
        f'style="border-top:3px solid {color}">'
        f'<div class="cal-day">{day.get("day", "")[:3]}</div>'
        f'<div class="cal-date">{(day.get("date") or "")[5:10]}</div>'
        f'<span class="cal-badge" style="background:{color}22;color:{color};'
        f'border:1px solid {color}44">{badge_label}</span>'
        f'{body}'
        f'</div>'
    )


def _render_calendar(plan: dict | None) -> str:
    """Render the latest pasted plan as a 7-day calendar, or an empty prompt."""
    if not plan:
        return (
            '<div class="empty">No plan saved yet. Paste a training plan from your '
            'coach above and click <strong>Visualize &amp; Save</strong>.</div>'
        )

    pj = plan.get("plan_json") or {}
    if isinstance(pj, str):
        try:
            pj = json.loads(pj)
        except Exception:
            pj = {}

    plan_id = plan["id"]
    days = pj.get("days", [])[:7]
    cells = "".join(_render_calendar_cell(d) for d in days)
    weekly_km = float(pj.get("weekly_km") or 0)

    return (
        '<div class="cal-wrap">'
        '<div class="cal-head">'
        f'<div>'
        f'<div class="cal-week">Week of {pj.get("week_start", "")}</div>'
        f'<div class="cal-summary">{pj.get("summary", "")}</div>'
        f'</div>'
        f'<div class="cal-head-right">'
        f'<span class="cal-total">{weekly_km:.1f} km</span>'
        f'<button class="btn btn-ghost btn-small" '
        f'hx-delete="/plans/{plan_id}/calendar" hx-target="#plan-calendar" '
        f'hx-confirm="Remove this saved plan?">Clear</button>'
        f'</div>'
        f'</div>'
        f'<div class="cal-grid">{cells}</div>'
        f'</div>'
    )


# ─── Routes ──────────────────────────────────────────────────────────────────

@router.get("", response_class=HTMLResponse)
def list_plans() -> HTMLResponse:
    return HTMLResponse(_render_plans(db.list_weekly_plans()))


@router.post("/generate", response_class=HTMLResponse)
def generate_plan() -> HTMLResponse:
    try:
        activities = db.get_recent_activities(days=30)
        goals = db.list_goals(status="active")
        rides = db.get_recent_cycling(days=14)
        nights = db.get_sleep_nights(limit=14)
        plan_json = _generate_plan_json(activities, goals, rides, nights)
        week_start = plan_json.get("week_start", _next_monday().isoformat())
        db.create_weekly_plan(week_start, plan_json)
    except Exception as exc:
        logger.exception("Plan generation failed")
        return HTMLResponse(
            f'<div class="banner error">Could not generate plan: {exc}</div>'
            + _render_plans(db.list_weekly_plans())
        )
    return HTMLResponse(_render_plans(db.list_weekly_plans()))


@router.post("/{plan_id}/accept", response_class=HTMLResponse)
def accept_plan(plan_id: int) -> HTMLResponse:
    db.update_plan_status(plan_id, "accepted")
    return HTMLResponse(_render_plans(db.list_weekly_plans()))


@router.delete("/{plan_id}", response_class=HTMLResponse)
def archive_plan(plan_id: int) -> HTMLResponse:
    db.update_plan_status(plan_id, "archived")
    return HTMLResponse(_render_plans(db.list_weekly_plans()))


# ─── Pasted-from-chat plan (Activities page calendar) ────────────────────────

@router.get("/calendar", response_class=HTMLResponse)
def plan_calendar() -> HTMLResponse:
    """Return the latest chat-pasted plan rendered as a calendar grid."""
    return HTMLResponse(_render_calendar(db.get_latest_weekly_plan(source="chat")))


@router.post("/parse", response_class=HTMLResponse)
def parse_plan(plan_text: str = Form(...)) -> HTMLResponse:
    """Take free-text plan pasted from the AI chat, structure it, save it,
    and return the calendar view.
    """
    text = (plan_text or "").strip()
    if len(text) < 20:
        return HTMLResponse(
            '<div class="banner error">That doesn\'t look like a plan — paste the '
            'full text your coach gave you.</div>'
            + _render_calendar(db.get_latest_weekly_plan(source="chat"))
        )
    try:
        plan_json = _parse_plan_text(text)
        week_start = plan_json.get("week_start", _next_monday().isoformat())
        # Archive any previous chat plan so the calendar always shows the newest
        prev = db.get_latest_weekly_plan(source="chat")
        if prev:
            db.update_plan_status(prev["id"], "archived")
        db.create_weekly_plan(week_start, plan_json)
    except Exception as exc:
        logger.exception("Plan parse failed")
        return HTMLResponse(
            f'<div class="banner error">Could not read that plan: {exc}</div>'
            + _render_calendar(db.get_latest_weekly_plan(source="chat"))
        )
    return HTMLResponse(_render_calendar(db.get_latest_weekly_plan(source="chat")))


@router.delete("/{plan_id}/calendar", response_class=HTMLResponse)
def clear_calendar_plan(plan_id: int) -> HTMLResponse:
    """Archive a chat-pasted plan and return the (now empty) calendar."""
    db.update_plan_status(plan_id, "archived")
    return HTMLResponse(_render_calendar(db.get_latest_weekly_plan(source="chat")))
