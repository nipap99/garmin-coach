"""Claude nutrition agent — logs what the user ate and tracks daily macros.

Same tool-use design as the running coach (``coach.py``): the model calls tools
to read/write the local food library and food log, rather than holding all data
in the prompt. Separate agent, separate chat channel.
"""
from __future__ import annotations

import json
import logging
from datetime import date
from typing import Any

from anthropic import Anthropic

from . import config, db

logger = logging.getLogger(__name__)

MODEL = "claude-opus-4-7"
MAX_TOOL_ITERATIONS = 10


SYSTEM_PROMPT = """You are a nutrition-logging assistant. The user tells you what they ate (food + amount); you log it and keep a running daily total of calories, protein, fat, and carbs.

The user's food data lives in a local database. Use the tools:
- find_food: check the user's food library for a food's per-100g nutrition.
- add_food: save a new food's per-100g nutrition (kcal, protein, fat, carbs) to the library.
- log_food: log a portion in GRAMS (the food must already be in the library). It computes the macros for that portion, stores it, and returns the updated day total.
- get_day_total: the running total for today plus the items logged so far.
- remove_log_entry: delete one logged item by id (for corrections).

For each food the user mentions:
1. Convert the portion to grams. If they give a count or a vague amount ("2 eggs", "a bowl of rice"), estimate the grams and state the assumption you made.
2. find_food. If it exists, call log_food(food_name, grams).
3. If it does NOT exist, estimate typical per-100g values from your nutrition knowledge, show the user your estimate (e.g. "chicken breast ≈ 165 kcal, 31g protein, 3.6g fat, 0g carbs per 100g"), and ask them to confirm or correct. Only after they confirm (or give exact numbers) do add_food, then log_food.
4. After logging, report what you logged and the new running daily total.

If the user sends a PHOTO of food:
- Identify each food in the image and estimate its portion in grams from visual cues (plate size, typical servings). State your gram estimates and that they're approximate.
- Then proceed exactly as above (find_food / add_food / log_food). If you're unsure what a food is, ask before logging.

Dates — the user may log food for a PAST day:
- If they mention a day ("yesterday", "on Monday", "June 15", "2 days ago"), resolve it to a YYYY-MM-DD date using today's date (given in the message) and pass it as the `date` argument to log_food (and get_day_total).
- If no day is mentioned, log against today (omit `date`).
- When confirming, state which day you logged to if it isn't today.

Rules:
- Always log in grams; be explicit about the gram amount you used.
- Use the user's exact figures when they give them; otherwise estimate and clearly label it an estimate.
- Be concise — a line or two per food, then the daily total.
- Today's date is provided in the user's message."""


TOOLS: list[dict[str, Any]] = [
    {
        "name": "find_food",
        "description": "Look up a food in the user's library and return its per-100g nutrition, or report it is not found.",
        "input_schema": {
            "type": "object",
            "properties": {"name": {"type": "string", "description": "Food name."}},
            "required": ["name"],
        },
    },
    {
        "name": "add_food",
        "description": "Add or update a food in the user's library with its per-100g nutrition. Do this only after the user confirms the figures.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "kcal_per_100g": {"type": "number"},
                "protein_per_100g": {"type": "number"},
                "fat_per_100g": {"type": "number"},
                "carbs_per_100g": {"type": "number"},
            },
            "required": ["name", "kcal_per_100g"],
        },
    },
    {
        "name": "log_food",
        "description": "Log a portion of a food that is already in the library. Computes and stores the macros for the given grams, and returns the updated daily total.",
        "input_schema": {
            "type": "object",
            "properties": {
                "food_name": {"type": "string"},
                "grams": {"type": "number", "description": "Portion size in grams."},
                "date": {
                    "type": "string",
                    "description": "Day eaten as YYYY-MM-DD. Omit for today. Use this to log a past day the user refers to (e.g. 'yesterday').",
                },
            },
            "required": ["food_name", "grams"],
        },
    },
    {
        "name": "get_day_total",
        "description": "Get a day's running macro total and the list of items logged (with their ids). Defaults to today; pass a date to check a past day.",
        "input_schema": {
            "type": "object",
            "properties": {
                "date": {"type": "string", "description": "Day as YYYY-MM-DD. Omit for today."},
            },
        },
    },
    {
        "name": "remove_log_entry",
        "description": "Delete one logged item by its id (use get_day_total to find ids).",
        "input_schema": {
            "type": "object",
            "properties": {"id": {"type": "integer"}},
            "required": ["id"],
        },
    },
]


def _resolve_date(args: dict[str, Any]) -> str:
    """Use the agent-supplied YYYY-MM-DD date if valid, else today."""
    d = (args.get("date") or "").strip()
    if d:
        try:
            return date.fromisoformat(d).isoformat()
        except ValueError:
            pass
    return date.today().isoformat()


def _run_tool(name: str, args: dict[str, Any]) -> str:
    today = date.today().isoformat()

    if name == "find_food":
        food = db.find_food(args["name"])
        return json.dumps(food or {"found": False, "name": args["name"]}, default=str)

    if name == "add_food":
        db.upsert_food(
            args["name"],
            float(args["kcal_per_100g"]),
            args.get("protein_per_100g"),
            args.get("fat_per_100g"),
            args.get("carbs_per_100g"),
        )
        return json.dumps({"saved": args["name"].strip().lower()})

    if name == "log_food":
        food = db.find_food(args["food_name"])
        if not food:
            return json.dumps({"error": f"'{args['food_name']}' is not in the library — add_food first."})
        log_date = _resolve_date(args)
        grams = float(args["grams"])
        k = grams / 100.0
        entry = {
            "kcal":      round((food["kcal_per_100g"] or 0) * k, 1),
            "protein_g": round((food["protein_per_100g"] or 0) * k, 1),
            "fat_g":     round((food["fat_per_100g"] or 0) * k, 1),
            "carbs_g":   round((food["carbs_per_100g"] or 0) * k, 1),
        }
        db.add_food_log(log_date, food["name"], grams,
                        entry["kcal"], entry["protein_g"], entry["fat_g"], entry["carbs_g"])
        return json.dumps({
            "logged": {"food": food["name"], "grams": grams, "date": log_date, **entry},
            "day_total": db.get_nutrition_day(log_date),
        }, default=str)

    if name == "get_day_total":
        d = _resolve_date(args)
        return json.dumps({
            "date": d,
            "total": db.get_nutrition_day(d),
            "items": db.get_food_log(d),
        }, default=str)

    if name == "remove_log_entry":
        ok = db.delete_food_log(int(args["id"]))
        return json.dumps({"deleted": ok, "day_total": db.get_nutrition_day(today)}, default=str)

    return json.dumps({"error": f"unknown tool: {name}"})


def chat(
    user_message: str,
    history: list[dict[str, Any]] | None = None,
    image: dict[str, str] | None = None,
) -> str:
    """Send a user message (and optional food photo) through the nutrition agent.

    ``image`` is ``{"media_type": "image/jpeg", "data": "<base64>"}`` when the
    user attached a photo; it's included as an image block in the current turn.
    """
    client = Anthropic(api_key=config.ANTHROPIC_API_KEY)
    today = date.today().isoformat()

    text_block = {"type": "text", "text": f"[today: {today}]\n\n{user_message}"}
    if image:
        content: Any = [
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": image["media_type"],
                    "data": image["data"],
                },
            },
            text_block,
        ]
    else:
        content = f"[today: {today}]\n\n{user_message}"

    messages: list[dict[str, Any]] = list(history or [])
    messages.append({"role": "user", "content": content})

    final_text = ""
    for _ in range(MAX_TOOL_ITERATIONS):
        response = client.messages.create(
            model=MODEL,
            max_tokens=4096,
            system=[{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
            tools=TOOLS,
            messages=messages,
        )
        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason == "tool_use":
            tool_results = []
            for block in response.content:
                if getattr(block, "type", None) != "tool_use":
                    continue
                try:
                    result = _run_tool(block.name, dict(block.input or {}))
                    is_error = False
                except Exception as e:  # noqa: BLE001
                    logger.exception("Nutrition tool %s failed", block.name)
                    result = json.dumps({"error": f"{type(e).__name__}: {e}"})
                    is_error = True
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": result,
                    "is_error": is_error,
                })
            messages.append({"role": "user", "content": tool_results})
            continue

        if response.stop_reason == "pause_turn":
            continue

        text_blocks = [b.text for b in response.content if getattr(b, "type", None) == "text"]
        final_text = "\n\n".join(text_blocks).strip()
        break
    else:
        final_text = "(nutrition agent hit its tool-use limit — try rephrasing)"

    return final_text or "(empty response)"
