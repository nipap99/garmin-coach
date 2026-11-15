"""Routes for the nutrition (food) agent chat + today's totals."""
from __future__ import annotations

import base64
import io
import logging
from collections import OrderedDict
from datetime import date
from html import escape
from urllib.parse import quote

from fastapi import APIRouter, File, Form, Query, UploadFile
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


def _day_arg(day: str | None) -> str:
    """Validate a YYYY-MM-DD day, or fall back to today."""
    if day:
        try:
            return date.fromisoformat(day).isoformat()
        except ValueError:
            pass
    return date.today().isoformat()


def _day_spans(day: str) -> str:
    """Inner content of the totals bar for a given day."""
    t = db.get_nutrition_day(day)
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
        f'<span class="tm-items">{items} item{"s" if items != 1 else ""}</span>'
    )


@router.get("/today", response_class=HTMLResponse)
def get_today(day: str | None = Query(None)) -> HTMLResponse:
    return HTMLResponse(_day_spans(_day_arg(day)))


@router.get("/history", response_class=HTMLResponse)
def get_history() -> HTMLResponse:
    rows = db.get_chat_history(limit=40, channel=CHANNEL)
    return HTMLResponse(_render_history(rows))


# ─── Deterministic form-based logging (no API cost) ──────────────────────────

def _totals_oob(day: str) -> str:
    """Out-of-band refresh of the totals bar for a day after a log/delete."""
    return (f'<div id="food-today" class="today-macros" hx-swap-oob="true">'
            f'{_day_spans(day)}</div>')


def _render_items(day: str) -> str:
    """A day's entries grouped by food — one line per food with summed grams and
    macros. The delete button removes every portion of that food for the day."""
    items = db.get_food_log(day)
    if not items:
        return ('<div class="empty">Nothing logged for this day — pick a food above, '
                'enter grams, and Add.</div>')

    groups: "OrderedDict[str, dict]" = OrderedDict()
    for it in items:
        g = groups.setdefault(it["food_name"],
                              {"grams": 0.0, "kcal": 0.0, "protein": 0.0, "fat": 0.0, "carbs": 0.0})
        g["grams"] += float(it["grams"] or 0)
        g["kcal"] += float(it["kcal"] or 0)
        g["protein"] += float(it["protein_g"] or 0)
        g["fat"] += float(it["fat_g"] or 0)
        g["carbs"] += float(it["carbs_g"] or 0)

    out = []
    for name, g in groups.items():
        url = f"/food/remove?day={day}&name={quote(name, safe='')}"
        out.append(
            f'<div class="food-item">'
            f'<span class="fi-name">{escape(name)}</span>'
            f'<span class="fi-grams">{g["grams"]:.0f} g</span>'
            f'<span class="fi-kcal">{g["kcal"]:.0f} kcal</span>'
            f'<span class="fi-macros">P {g["protein"]:.0f}'
            f' · F {g["fat"]:.0f}'
            f' · C {g["carbs"]:.0f}</span>'
            f'<button class="fi-del" title="Remove all of this food today" '
            f'hx-post="{url}" hx-target="#food-items">&times;</button>'
            f'</div>'
        )
    return "".join(out)


@router.get("/foods")
def list_foods():
    """The food library for the dropdown (name + per-100g macros)."""
    return [
        {"name": f["name"],
         "kcal": float(f["kcal_per_100g"] or 0),
         "protein": float(f["protein_per_100g"] or 0),
         "fat": float(f["fat_per_100g"] or 0),
         "carbs": float(f["carbs_per_100g"] or 0)}
        for f in db.list_foods(limit=1000)
    ]


@router.get("/items", response_class=HTMLResponse)
def get_items(day: str | None = Query(None)) -> HTMLResponse:
    return HTMLResponse(_render_items(_day_arg(day)))


@router.post("/new", response_class=HTMLResponse)
def add_new_food(
    name: str = Form(...),
    kcal: float = Form(...),
    protein: float = Form(0.0),
    fat: float = Form(0.0),
    carbs: float = Form(0.0),
) -> HTMLResponse:
    """Add a new ingredient to the food library by hand — name + per-100g macros.
    No AI, no API cost. Triggers a dropdown refresh so it's usable immediately."""
    name = (name or "").strip()
    if not name:
        return HTMLResponse('<div class="banner error">Enter a name.</div>')
    if kcal <= 0:
        return HTMLResponse('<div class="banner error">Enter calories per 100&nbsp;g '
                            '(greater than 0).</div>')
    for label, val in (("Protein", protein), ("Fat", fat), ("Carbs", carbs)):
        if val < 0:
            return HTMLResponse(f'<div class="banner error">{label} can\'t be '
                                'negative.</div>')

    existed = db.find_food(name) is not None
    db.upsert_food(name, kcal, protein, fat, carbs)

    disp = name[:1].upper() + name[1:]
    verb = "Updated" if existed else "Added"
    return HTMLResponse(
        f'<div class="banner success">{verb} <strong>{escape(disp)}</strong> — '
        f'{kcal:.0f} kcal/100&nbsp;g · P{protein:.0f} F{fat:.0f} C{carbs:.0f}. '
        f'It\'s in the dropdown now.</div>',
        headers={"HX-Trigger": "foodLibraryChanged"},
    )


@router.post("/library/delete", response_class=HTMLResponse)
def delete_library_food(name: str = Form(...)) -> HTMLResponse:
    """Delete one food from the personal library (the 'remove ingredient' form).
    Does not touch anything already logged. Refreshes the dropdowns."""
    name = (name or "").strip()
    if not name:
        return HTMLResponse('<div class="banner error">Pick a food to remove.</div>')
    disp = name[:1].upper() + name[1:]
    if db.delete_food(name):
        return HTMLResponse(
            f'<div class="banner success">Removed <strong>{escape(disp)}</strong> '
            f'from your food list.</div>',
            headers={"HX-Trigger": "foodLibraryChanged"},
        )
    return HTMLResponse(
        f'<div class="banner error">"{escape(disp)}" isn\'t in your list.</div>')


@router.post("/log", response_class=HTMLResponse)
def log_food(
    food_name: str = Form(...),
    grams: float = Form(...),
    day: str | None = Form(None),
) -> HTMLResponse:
    """Log a portion (food + grams) to a given day — computed locally, no AI."""
    d = _day_arg(day)
    food = db.find_food(food_name)
    if not food:
        return HTMLResponse(
            f'<div class="banner error">"{escape(food_name)}" isn\'t in your list.</div>'
            + _render_items(d))
    if grams <= 0:
        return HTMLResponse('<div class="banner error">Enter grams above 0.</div>'
                            + _render_items(d))

    k = grams / 100.0
    db.add_food_log(
        d, food["name"], grams,
        round((food["kcal_per_100g"] or 0) * k, 1),
        round((food["protein_per_100g"] or 0) * k, 1),
        round((food["fat_per_100g"] or 0) * k, 1),
        round((food["carbs_per_100g"] or 0) * k, 1),
    )
    return HTMLResponse(_render_items(d) + _totals_oob(d),
                        headers={"HX-Trigger": "foodChanged"})


@router.post("/item/{entry_id}/delete", response_class=HTMLResponse)
def delete_item(entry_id: int, day: str | None = Query(None)) -> HTMLResponse:
    db.delete_food_log(entry_id)
    d = _day_arg(day)
    return HTMLResponse(_render_items(d) + _totals_oob(d),
                        headers={"HX-Trigger": "foodChanged"})


@router.post("/remove", response_class=HTMLResponse)
def remove_food(name: str = Query(...), day: str | None = Query(None)) -> HTMLResponse:
    """Remove every portion of one food on a day (the grouped-row delete)."""
    d = _day_arg(day)
    db.delete_food_log_for(d, name)
    return HTMLResponse(_render_items(d) + _totals_oob(d),
                        headers={"HX-Trigger": "foodChanged"})


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

    # Refresh today's totals bar out-of-band after each turn.
    return HTMLResponse(
        _render_message("user", user_display) + _render_message("assistant", reply)
        + _totals_oob(date.today().isoformat())
    )


@router.post("/clear", response_class=HTMLResponse)
def clear_history() -> HTMLResponse:
    db.clear_chat_history(channel=CHANNEL)
    return HTMLResponse(_render_history([]))
