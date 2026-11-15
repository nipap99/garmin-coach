"""Food logging logic — the food library and daily food log.

Used by the MCP server (``mcp_coach.py``: find_food / add_food / log_food /
food_day / remove_food_entry), so food can be logged — including from a photo —
through the Claude app on the subscription. The old paid-API food chat was
retired on 2026-11-15; the app's Food tab keeps the free form-based logging.
"""
from __future__ import annotations

import json
from datetime import date
from typing import Any

from . import db


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
