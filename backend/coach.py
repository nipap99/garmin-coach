"""Claude coach — answers questions and proposes plans grounded in the user's data.

Architecture:
- Uses the Anthropic SDK with **tool use**: the coach queries the local DB via tools
  rather than having every activity dumped into the prompt. This keeps token cost flat
  as the database grows.
- System prompt is **frozen** (no date/state interpolation) and **cached** via
  ``cache_control``. Today's date is injected in the user turn instead.
- Model is Claude Opus 4.7 with adaptive thinking + ``effort: "high"`` — best balance
  of reasoning quality and cost for personal use.
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
MAX_TOOL_ITERATIONS = 8


SYSTEM_PROMPT = """You are a personal running coach helping the user train for their goals (currently a 5K and a half marathon).

The user's training and recovery data lives in a local database. Use the provided tools to fetch:
- recent running activities (pace, HR, distance, duration, VO2max, elevation)
- recent CYCLING rides — a secondary aerobic activity the user does for transport/fitness (distance, duration, avg speed, HR, calories)
- recent SLEEP — nightly recovery (sleep score, total/deep/REM duration, HRV in ms, resting HR)
- recent daily CALORIES (total, active, resting) — energy-expenditure context
- active race goals, and personal-record progression at standard distances

When the user asks a question, ground your answer in their actual numbers — pull the data with tools before answering. Don't speculate from general knowledge when the data is one tool call away.

Treat the athlete holistically — running is the goal, but cycling and sleep change the right prescription:
- CYCLING is cross-training that adds real aerobic load and fatigue. Count it toward the week's total load. After a long or hard ride, don't stack a hard run the next day. An easy spin can serve as active recovery. If cycling volume is high, the running plan has less room before overreaching.
- SLEEP & HRV are the recovery signal. Short sleep, a downward HRV trend, or elevated resting HR mean the body is under-recovered → bias toward easier sessions, more rest, or moving hard workouts later. Good sleep and rising HRV mean the athlete can absorb harder work. Always sanity-check intensity against recent recovery.
- CALORIES give rough energy context — large deficits alongside hard training are a fueling/recovery red flag worth mentioning.

Your responsibilities:
1. Validate training — flag when effort doesn't match intent (e.g. easy runs with HR too high), AND when running load ignores cycling fatigue or poor recovery.
2. Provide concrete insights citing specific numbers and dates from the data.
3. When asked for a plan, propose a 7-day weekly schedule with one line of reasoning per day, balancing run + bike load and scheduling intensity around recovery. Plans are suggestions the user can accept, edit, or regenerate.
4. Be candid but supportive. Lead with numbers, not platitudes.

Style:
- Concise. Skip preambles like "Great question" or "Based on your data".
- Use bullets and short paragraphs.
- Cite specific dates and figures (e.g. "your 5K pace dropped from 5:10 to 4:58 between Mar 12 and May 4"; "HRV fell from 86 to 71 ms over 4 nights").
- If the data is missing or thin, say so plainly and tell the user what to sync or log."""


TOOLS: list[dict[str, Any]] = [
    {
        "name": "get_recent_activities",
        "description": (
            "Fetch the user's running activities from the last N days. "
            "Each activity includes: date, name, distance_km, duration_min, "
            "avg_pace_min_per_km, avg_hr, max_hr, vo2max, elevation_gain_m, calories."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "days": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 365,
                    "description": "Number of days to look back from today.",
                },
            },
            "required": ["days"],
        },
    },
    {
        "name": "get_recent_cycling",
        "description": (
            "Fetch the user's CYCLING rides from the last N days — a secondary "
            "aerobic activity (cross-training). Each ride includes date, "
            "distance_km, duration_min, avg_speed_kmh, avg_hr, calories. Use to "
            "gauge total training load and fatigue beyond running."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "days": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 365,
                    "description": "Number of days to look back from today.",
                },
            },
            "required": ["days"],
        },
    },
    {
        "name": "get_recent_sleep",
        "description": (
            "Fetch the user's recent nightly SLEEP — the recovery signal. Each "
            "night includes date, score (0-100), duration_min, deep_min, rem_min, "
            "hrv_ms, resting_hr. Short sleep, low/declining HRV, or elevated "
            "resting HR mean prioritize easier training or rest."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "nights": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 120,
                    "description": "Number of recent nights to fetch.",
                },
            },
            "required": ["nights"],
        },
    },
    {
        "name": "get_recent_calories",
        "description": (
            "Fetch the user's recent daily CALORIES for energy-expenditure "
            "context. Each day includes date, total_cal, activity_cal, resting_cal."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "days": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 120,
                    "description": "Number of days to look back from today.",
                },
            },
            "required": ["days"],
        },
    },
    {
        "name": "get_goals",
        "description": (
            "Fetch the user's currently active running goals — target distances, "
            "optional target times (in seconds), and optional target dates."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_pr_progression",
        "description": (
            "Get the user's progression at a standard race distance. Returns "
            "matching activities (sorted oldest-first) whose distance falls in the "
            "canonical range for the requested race (e.g. 4.5-5.5km for 5K). "
            "Useful for trend analysis and PR detection."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "distance": {
                    "type": "string",
                    "enum": list(db.VALID_DISTANCES),
                    "description": "One of: 5k, 10k, half_marathon, marathon.",
                },
            },
            "required": ["distance"],
        },
    },
]


DISTANCE_RANGES = {
    "5k": (4.5, 5.5),
    "10k": (9.0, 11.0),
    "half_marathon": (19.0, 23.0),
    "marathon": (40.0, 44.0),
}


def _run_tool(name: str, args: dict[str, Any]) -> str:
    """Execute one coach tool and return a JSON-encoded result for the model."""
    if name == "get_recent_activities":
        days = int(args.get("days", 30))
        rows = db.get_recent_activities(days=days)
        # Strip raw_json + synced_at — not useful for the model, just tokens.
        for r in rows:
            r.pop("raw_json", None)
            r.pop("synced_at", None)
        return json.dumps(rows, default=str)

    if name == "get_recent_cycling":
        days = int(args.get("days", 30))
        return json.dumps(db.get_recent_cycling(days=days), default=str)

    if name == "get_recent_sleep":
        nights = int(args.get("nights", 14))
        rows = db.get_sleep_nights(limit=nights)
        for r in rows:
            r.pop("synced_at", None)
        return json.dumps(rows, default=str)

    if name == "get_recent_calories":
        days = int(args.get("days", 14))
        rows = db.get_calories_days(limit=days)
        for r in rows:
            r.pop("synced_at", None)
        return json.dumps(rows, default=str)

    if name == "get_goals":
        return json.dumps(db.list_goals(status="active"), default=str)

    if name == "get_pr_progression":
        distance = args["distance"]
        if distance not in DISTANCE_RANGES:
            return json.dumps({"error": f"unknown distance: {distance}"})
        lo, hi = DISTANCE_RANGES[distance]
        rows = db.get_recent_activities(days=365)
        matches = [
            {
                "date": (r.get("start_local") or "")[:10],
                "distance_km": r.get("distance_km"),
                "duration_min": r.get("duration_min"),
                "avg_pace_min_per_km": r.get("avg_pace_min_per_km"),
                "avg_hr": r.get("avg_hr"),
            }
            for r in rows
            if r.get("distance_km") and lo <= r["distance_km"] <= hi
        ]
        matches.sort(key=lambda r: r["date"])
        return json.dumps(matches)

    return json.dumps({"error": f"unknown tool: {name}"})


def _client() -> Anthropic:
    return Anthropic(api_key=config.ANTHROPIC_API_KEY)


def chat(
    user_message: str,
    history: list[dict[str, Any]] | None = None,
) -> str:
    """Send a user message through the coach and return the final assistant text.

    ``history`` is a list of plain ``{"role": "user"|"assistant", "content": str}``
    dicts representing prior turns. The tool-use loop within this single call
    appends assistant tool_use blocks and tool_result blocks to a local copy of
    messages — those structured blocks are NOT persisted back to the caller,
    keeping the caller's history simple to store in SQLite.
    """
    client = _client()
    today = date.today().isoformat()

    # Build the messages list for this turn.
    messages: list[dict[str, Any]] = list(history or [])
    messages.append(
        {
            "role": "user",
            "content": f"[today: {today}]\n\n{user_message}",
        }
    )

    final_text = ""

    for iteration in range(MAX_TOOL_ITERATIONS):
        response = client.messages.create(
            model=MODEL,
            max_tokens=4096,
            system=[
                {
                    "type": "text",
                    "text": SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            tools=TOOLS,
            thinking={"type": "adaptive"},
            output_config={"effort": "high"},
            messages=messages,
        )

        # Preserve the full assistant turn (including tool_use blocks) so the
        # next iteration sees the model's prior intent.
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
                    logger.exception("Tool %s failed", block.name)
                    result = json.dumps({"error": f"{type(e).__name__}: {e}"})
                    is_error = True
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result,
                        "is_error": is_error,
                    }
                )
            messages.append({"role": "user", "content": tool_results})
            continue

        if response.stop_reason == "pause_turn":
            # Server-side tool sampling loop paused; re-send to continue.
            continue

        # end_turn or anything else — extract text and return.
        text_blocks = [
            b.text for b in response.content if getattr(b, "type", None) == "text"
        ]
        final_text = "\n\n".join(text_blocks).strip()
        if response.stop_reason not in ("end_turn",):
            logger.warning(
                "Unexpected stop_reason %r at iteration %d",
                response.stop_reason,
                iteration,
            )
        break
    else:
        logger.warning("Coach loop hit MAX_TOOL_ITERATIONS without end_turn")
        final_text = (
            "(coach exceeded internal tool-use iteration limit — try a simpler question)"
        )

    return final_text or "(empty response)"
