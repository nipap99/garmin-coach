"""The weekly training plan — the one you agree to in the Coach conversation.

GET    /plans/calendar      — the agreed plan as a Mon–Sun calendar.
POST   /plans/from-coach    — structure the coach's latest message into the plan.
DELETE /plans/{id}/calendar — clear the saved plan.
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


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _next_monday() -> date:
    today = date.today()
    days = (7 - today.weekday()) % 7
    return today + timedelta(days=days if days else 7)


def _parse_plan_text(text: str) -> dict[str, Any]:
    """Convert a free-text training plan (pasted from the AI chat) into the
    structured plan JSON used by the calendar view. Calls Claude to do the
    extraction so it tolerates any formatting the coach happened to use.
    """
    today = date.today().isoformat()
    next_mon = _next_monday().isoformat()

    prompt = f"""You are given a running training plan written in free-form text
(produced by a coaching assistant in a chat). Convert it into a structured
Monday–Sunday week of exactly 7 days.

Today's date is {today}. Use it to resolve any dates or day names in the plan
into real ISO dates (infer the year from today).

CRUCIAL — use the plan's OWN week, do not move it:
- Work out which calendar week the plan is for from the dates or day names in the
  text (e.g. "Tue Jul 21" → that Tuesday's Monday–Sunday week). Use THAT week.
- Do NOT shift the plan to a different week, and do NOT assume "next week".
- Only if the text gives no dates AND no day names at all, fall back to the week
  starting Monday {next_mon}.

If some days of that week have no session in the text, mark them as rest. If the
plan spans more than 7 days, use only its first Monday–Sunday week.

--- PLAN TEXT START ---
{text.strip()}
--- PLAN TEXT END ---

Return ONLY a raw JSON object — no markdown fences, no explanation:
{{
  "week_start": "YYYY-MM-DD",   // the Monday of the plan's own week
  "summary": "One sentence describing the week's focus — infer ONLY from the plan text; do not invent goals, races, or a theme that isn't there",
  "weekly_km": 0.0,
  "source": "chat",
  "days": [
    {{
      "day": "Monday",
      "date": "YYYY-MM-DD",
      "workout_type": "easy",
      "title": "Short workout title",
      "description": "The details from the plan text for this day",
      "distance_km": 0.0,
      "duration_min": 0,
      "intensity": "easy"
    }}
    // ... all 7 days, Monday → Sunday, each with its REAL date in that week
  ]
}}

Rules:
- Exactly 7 days, in order Monday → Sunday, each carrying its real ISO date for the plan's week.
- workout_type: one of easy, tempo, intervals, long, rest, cross
- intensity: one of easy, moderate, hard, rest
- Rest days: distance_km = 0, duration_min = 0
- Be faithful to the text — do NOT invent workouts, races, or a rest week. A day not mentioned in the text is a rest day.
- If a day's distance or duration isn't stated, set it to 0
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
            '<div class="empty">No plan saved yet. When the coach lays out your week, '
            'click <strong>Save this week\'s plan</strong> and it will appear here.</div>'
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
        f'hx-delete="/plans/{plan_id}/calendar" hx-target="#coach-plan" '
        f'hx-confirm="Remove this saved plan?">Clear</button>'
        f'</div>'
        f'</div>'
        f'<div class="cal-grid">{cells}</div>'
        f'</div>'
    )


# ─── Routes — the agreed plan lives under the Coach conversation ──────────────

@router.get("/calendar", response_class=HTMLResponse)
def plan_calendar() -> HTMLResponse:
    """Return the latest chat-pasted plan rendered as a calendar grid."""
    return HTMLResponse(_render_calendar(db.get_latest_weekly_plan(source="chat")))


@router.post("/from-coach", response_class=HTMLResponse)
def plan_from_coach() -> HTMLResponse:
    """Read the coach's most recent message, structure it into the weekly plan,
    save it as the agreed plan, and return the calendar — so the plan you just
    agreed on in the conversation is visualised right below it."""
    history = db.get_chat_history(limit=20, channel="coach")
    last_assistant = next(
        (m["content"] for m in reversed(history) if m["role"] == "assistant"),
        None,
    )
    if not last_assistant or len(last_assistant.strip()) < 20:
        return HTMLResponse(
            '<div class="banner error">No coach plan to read yet — ask the coach for a '
            'weekly program first, then save it.</div>'
            + _render_calendar(db.get_latest_weekly_plan(source="chat"))
        )
    try:
        plan_json = _parse_plan_text(last_assistant)
        week_start = plan_json.get("week_start", _next_monday().isoformat())
        # Only one agreed plan lives at a time — archive the previous one.
        prev = db.get_latest_weekly_plan(source="chat")
        if prev:
            db.update_plan_status(prev["id"], "archived")
        plan_id = db.create_weekly_plan(week_start, plan_json)
        db.update_plan_status(plan_id, "accepted")   # this is the plan you agreed to
    except Exception as exc:
        logger.exception("Plan from coach failed")
        return HTMLResponse(
            f'<div class="banner error">Couldn\'t find a full week in the coach\'s last '
            f'message. Ask for a Monday–Sunday plan, then save again. ({exc})</div>'
            + _render_calendar(db.get_latest_weekly_plan(source="chat"))
        )
    return HTMLResponse(_render_calendar(db.get_latest_weekly_plan(source="chat")))


@router.delete("/{plan_id}/calendar", response_class=HTMLResponse)
def clear_calendar_plan(plan_id: int) -> HTMLResponse:
    """Archive the agreed plan and return the (now empty) calendar."""
    db.update_plan_status(plan_id, "archived")
    return HTMLResponse(_render_calendar(db.get_latest_weekly_plan(source="chat")))
