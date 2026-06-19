"""Routes for the nutrition (food) agent chat + today's totals."""
from __future__ import annotations

import base64
import io
import logging
from datetime import date
from html import escape

from fastapi import APIRouter, File, Form, UploadFile
from fastapi.responses import HTMLResponse
from PIL import Image

from .. import db, nutrition

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/food", tags=["food"])

CHANNEL = "food"


def _render_message(role: str, content: str) -> str:
    cls = "msg-user" if role == "user" else "msg-assistant"
    label = "You" if role == "user" else "Nutrition"
    safe = escape(content).replace("\n", "<br>")
    return f'<div class="msg {cls}"><div class="msg-role">{label}</div><div class="msg-body">{safe}</div></div>'


def _render_history(rows: list[dict]) -> str:
    if not rows:
        return (
            '<div class="empty">Tell me what you ate. Try: '
            "<em>\"200g chicken breast and 150g rice\"</em> or "
            "<em>\"2 eggs and a banana\"</em>.</div>"
        )
    return "".join(_render_message(r["role"], r["content"]) for r in rows)


def _today_spans() -> str:
    """Inner content of the today's-totals bar."""
    t = db.get_nutrition_day(date.today().isoformat())
    kcal = int(t.get("kcal") or 0)
    p = float(t.get("protein_g") or 0)
    f = float(t.get("fat_g") or 0)
    c = float(t.get("carbs_g") or 0)
    items = int(t.get("items") or 0)
    return (
        f'<span class="tm-kcal">{kcal:,} kcal</span>'
        f'<span class="tm-macro">P {p:.0f}g</span>'
        f'<span class="tm-macro">F {f:.0f}g</span>'
        f'<span class="tm-macro">C {c:.0f}g</span>'
        f'<span class="tm-items">{items} item{"s" if items != 1 else ""} today</span>'
    )


@router.get("/today", response_class=HTMLResponse)
def get_today() -> HTMLResponse:
    return HTMLResponse(_today_spans())


@router.get("/history", response_class=HTMLResponse)
def get_history() -> HTMLResponse:
    rows = db.get_chat_history(limit=40, channel=CHANNEL)
    return HTMLResponse(_render_history(rows))


def _process_image(photo: UploadFile) -> dict[str, str]:
    """Downscale an uploaded food photo and return a base64 JPEG for the model."""
    raw = photo.file.read()
    img = Image.open(io.BytesIO(raw)).convert("RGB")
    img.thumbnail((1568, 1568))  # Anthropic's recommended max long side
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return {
        "media_type": "image/jpeg",
        "data": base64.standard_b64encode(buf.getvalue()).decode(),
    }


@router.post("", response_class=HTMLResponse)
def post_message(
    message: str = Form(""),
    photo: UploadFile | None = File(None),
) -> HTMLResponse:
    message = (message or "").strip()

    image: dict[str, str] | None = None
    if photo is not None and photo.filename:
        try:
            image = _process_image(photo)
        except Exception:  # noqa: BLE001
            logger.exception("Could not process food photo")
            return HTMLResponse(_render_message(
                "assistant", "(couldn't read that image — try a JPEG or PNG)"))

    if not message and image is None:
        return HTMLResponse("")

    # What we store/show as the user's turn (the image itself isn't kept).
    if image is not None:
        user_display = ("📷 " + message) if message else "📷 (food photo)"
    else:
        user_display = message
    db.append_chat_message("user", user_display, channel=CHANNEL)

    history_rows = db.get_chat_history(limit=20, channel=CHANNEL)
    if history_rows and history_rows[-1]["role"] == "user":
        history_rows = history_rows[:-1]
    history = [{"role": r["role"], "content": r["content"]} for r in history_rows]

    agent_message = message or (
        "Identify the food in this photo, estimate the portions in grams, and log them."
    )
    try:
        reply = nutrition.chat(agent_message, history=history, image=image)
    except Exception as e:  # noqa: BLE001
        logger.exception("Nutrition agent call failed")
        reply = f"(nutrition error: {type(e).__name__}: {e})"

    db.append_chat_message("assistant", reply, channel=CHANNEL)

    # Refresh the totals bar out-of-band after each turn.
    oob = (
        f'<div id="food-today" class="today-macros" hx-swap-oob="true">'
        f'{_today_spans()}</div>'
    )
    return HTMLResponse(
        _render_message("user", user_display) + _render_message("assistant", reply) + oob
    )


@router.post("/clear", response_class=HTMLResponse)
def clear_history() -> HTMLResponse:
    db.clear_chat_history(channel=CHANNEL)
    return HTMLResponse(_render_history([]))
