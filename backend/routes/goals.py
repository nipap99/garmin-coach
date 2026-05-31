"""Routes for goals CRUD.

HTML-fragment endpoints designed for HTMX swap-in. Distances are one of
``5k``, ``10k``, ``half_marathon``, ``marathon``.
"""
from __future__ import annotations

from fastapi import APIRouter, Form, HTTPException
from fastapi.responses import HTMLResponse

from .. import db

router = APIRouter(prefix="/goals", tags=["goals"])


DISTANCE_LABELS = {
    "5k": "5K",
    "10k": "10K",
    "half_marathon": "Half marathon",
    "marathon": "Marathon",
}


def _format_target_time(seconds: int | None) -> str:
    """Format target time as H:MM:SS or M:SS, '—' if None."""
    if not seconds:
        return "—"
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def _parse_target_time(text: str | None) -> int | None:
    """Parse 'MM:SS' or 'H:MM:SS' or 'NNm' or empty into seconds (or None)."""
    if not text:
        return None
    text = text.strip()
    if not text:
        return None
    if ":" in text:
        parts = [int(p) for p in text.split(":")]
        if len(parts) == 2:
            return parts[0] * 60 + parts[1]
        if len(parts) == 3:
            return parts[0] * 3600 + parts[1] * 60 + parts[2]
    # bare number = minutes
    try:
        return int(float(text) * 60)
    except ValueError:
        return None


def _render_goals(rows: list[dict]) -> str:
    if not rows:
        return (
            '<div class="empty">No active goals yet. Add one above '
            "(e.g. a 5K or half marathon target).</div>"
        )
    items = []
    for g in rows:
        label = DISTANCE_LABELS.get(g["distance"], g["distance"])
        target_date = g.get("target_date") or "no date set"
        target_time = _format_target_time(g.get("target_time_seconds"))
        notes = g.get("notes") or ""
        notes_html = f'<div class="goal-notes">{notes}</div>' if notes else ""
        goal_id = g["id"]
        items.append(
            '<div class="goal-row">'
            f'<div class="goal-main"><strong>{label}</strong> '
            f'<span class="goal-meta">target {target_time}'
            f' &middot; {target_date}</span>'
            f'{notes_html}'
            '</div>'
            f'<button class="btn-small btn-danger" '
            f'hx-delete="/goals/{goal_id}" '
            f'hx-target="#goals-panel" '
            f'hx-confirm="Delete this goal?">Delete</button>'
            '</div>'
        )
    return '<div class="goal-list">' + "".join(items) + "</div>"


@router.get("", response_class=HTMLResponse)
def list_goals_html() -> HTMLResponse:
    rows = db.list_goals(status="active")
    return HTMLResponse(_render_goals(rows))


@router.post("", response_class=HTMLResponse)
def create_goal_html(
    distance: str = Form(...),
    target_date: str | None = Form(None),
    target_time: str | None = Form(None),
    notes: str | None = Form(None),
) -> HTMLResponse:
    if distance not in db.VALID_DISTANCES:
        raise HTTPException(status_code=400, detail=f"invalid distance: {distance}")
    seconds = _parse_target_time(target_time)
    target_date_clean = (target_date or "").strip() or None
    notes_clean = (notes or "").strip() or None
    db.create_goal(
        distance=distance,
        target_date=target_date_clean,
        target_time_seconds=seconds,
        notes=notes_clean,
    )
    rows = db.list_goals(status="active")
    return HTMLResponse(_render_goals(rows))


@router.delete("/{goal_id}", response_class=HTMLResponse)
def delete_goal_html(goal_id: int) -> HTMLResponse:
    db.delete_goal(goal_id)
    rows = db.list_goals(status="active")
    return HTMLResponse(_render_goals(rows))
