"""Routes for the coach chat UI."""
from __future__ import annotations

import logging
from html import escape

from fastapi import APIRouter, Form
from fastapi.responses import HTMLResponse

from .. import coach, db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chat", tags=["chat"])


def _render_message(role: str, content: str) -> str:
    cls = "msg-user" if role == "user" else "msg-assistant"
    label = "You" if role == "user" else "Coach"
    safe = escape(content).replace("\n", "<br>")
    return f'<div class="msg {cls}"><div class="msg-role">{label}</div><div class="msg-body">{safe}</div></div>'


def _render_history(rows: list[dict]) -> str:
    if not rows:
        return (
            '<div class="empty">Say hi to the coach below. Try: '
            '<em>"What does my training look like so far?"</em> or '
            '<em>"Build me a weekly plan."</em></div>'
        )
    return "".join(_render_message(r["role"], r["content"]) for r in rows)


@router.get("/history", response_class=HTMLResponse)
def get_history() -> HTMLResponse:
    rows = db.get_chat_history(limit=40)
    return HTMLResponse(_render_history(rows))


@router.post("", response_class=HTMLResponse)
def post_message(message: str = Form(...)) -> HTMLResponse:
    message = (message or "").strip()
    if not message:
        return HTMLResponse("")

    db.append_chat_message("user", message)

    history_rows = db.get_chat_history(limit=20)
    # Drop the just-saved user message from history (it will be passed as the
    # current turn instead of as history).
    if history_rows and history_rows[-1]["role"] == "user":
        history_rows = history_rows[:-1]
    history = [{"role": r["role"], "content": r["content"]} for r in history_rows]

    try:
        reply = coach.chat(message, history=history)
    except Exception as e:  # noqa: BLE001
        logger.exception("Coach call failed")
        reply = f"(coach error: {type(e).__name__}: {e})"

    db.append_chat_message("assistant", reply)

    # Return both the user message and the assistant reply so HTMX appends them
    # to the history panel in one go.
    return HTMLResponse(_render_message("user", message) + _render_message("assistant", reply))


@router.post("/clear", response_class=HTMLResponse)
def clear_history() -> HTMLResponse:
    db.clear_chat_history()
    return HTMLResponse(_render_history([]))
