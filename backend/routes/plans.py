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
from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from .. import config, db

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


def _generate_plan_json(activities: list[dict], goals: list[dict]) -> dict[str, Any]:
    """Call Claude and return the validated plan dict."""
    next_mon = _next_monday()
    days_dates = [(next_mon + timedelta(i)).isoformat() for i in range(7)]
    day_names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

    prompt = f"""You are a running coach. Generate a 7-day training plan for next week.

Today: {date.today().isoformat()}
Week: {next_mon.isoformat()} (Mon) to {days_dates[6]} (Sun)

Recent training (last 30 days):
{_activities_text(activities)}

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


# ─── Routes ──────────────────────────────────────────────────────────────────

@router.get("", response_class=HTMLResponse)
def list_plans() -> HTMLResponse:
    return HTMLResponse(_render_plans(db.list_weekly_plans()))


@router.post("/generate", response_class=HTMLResponse)
def generate_plan() -> HTMLResponse:
    try:
        activities = db.get_recent_activities(days=30)
        goals = db.list_goals(status="active")
        plan_json = _generate_plan_json(activities, goals)
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
