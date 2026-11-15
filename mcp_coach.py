"""Local MCP server: Claude's window into the garmin-coach database.

Used by (all on the Claude subscription, $0 extra):
- the Claude Desktop chat (config: claude_desktop_config.json → mcpServers)
- the Sunday "weekly-fitness-report" scheduled task
- the run-analyst subagent in linkedin-ai/.claude/agents/

Numbers are computed in Python (backend/metrics.py, backend/coach_tools.py) so
every coach sees identical figures. The only write tools are the food-logging
ones (food library + food log); everything else is read-only.

Postgres must be running. New or renamed tools need a Claude Desktop restart.

Manual test:  .venv\\Scripts\\python.exe mcp_coach.py   (waits on stdio; Ctrl-C)
"""
from __future__ import annotations

import os
import sys

# Make `import backend...` work whatever cwd Claude launches us from.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mcp.server.mcpserver import MCPServer

from backend import coach_tools as t
from backend import config, nutrition

RULES_PATH = config.PROJECT_ROOT / "coaching" / "coaching_rules.md"

INSTRUCTIONS = """This server exposes the athlete's real Garmin training data from a
local database. Ground every answer in tool output — never guess numbers.

Start with `coach_snapshot` (one call: ACWR, weekly km, readiness light, long-run
status, current paces, goals, and last/this week vs the plan). Use
`week_summary` for any specific Mon-Sun week. Runs come merged into sessions and
classified (easy / intervals / tempo / race / long), so don't re-add laps.

Before giving training advice, read `coaching_rules` — the athlete's agreed weekly
template, volume rules, pace rules and nutrition stance. It is the single source
of truth and overrides general knowledge.

Food: to log a meal (text or a photo the user sends), use find_food → (add_food
after the user confirms per-100g values for a new food) → log_food in grams."""

server = MCPServer("garmin-coach", instructions=INSTRUCTIONS)


# ── coaching (pre-computed) ─────────────────────────────────────────────
@server.tool()
def coach_snapshot() -> str:
    """Everything a check-in needs in one call: ACWR, running km per Mon-Sun week
    (last 4 + current), weekly TRIMP load (runs + rides) with HRV/resting HR, this
    morning's readiness light, days since the last long run, current training
    paces, active goals, and last week + this week compared with the plan."""
    return t.coach_snapshot()


@server.tool()
def week_summary(week_start: str = "") -> str:
    """One Mon-Sun week day by day: planned session vs what was run (merged,
    classified sessions), km, hard days, plan adherence and flags (>3 hard days,
    back-to-back hard days, hard on an easy day, Sunday run, no long run).
    `week_start` = any date in the week (YYYY-MM-DD); empty = the current week."""
    return t.week_summary(week_start or None)


@server.tool()
def coaching_rules() -> str:
    """The athlete's coaching rules (weekly template, volume, paces, recovery,
    nutrition, goals). Read before prescribing or judging training."""
    try:
        return RULES_PATH.read_text(encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        return f"(could not read {RULES_PATH}: {exc})"


@server.tool()
def speed_workouts() -> str:
    """Track/speed workout library plus `current_paces`: training paces computed
    today from the best recent effort (VDOT), with predicted race times. Rotate
    workouts week to week."""
    return t.speed_workouts()


@server.tool()
def training_load() -> str:
    """Weekly training load (Banister TRIMP, runs + rides, Monday-start weeks)
    with the acute:chronic workload ratio (ACWR) plus weekly HRV and resting HR."""
    return t.training_load_summary()


@server.tool()
def goals() -> str:
    """The athlete's active race goals (distance, target time, date)."""
    return t.goals()


# ── raw data ────────────────────────────────────────────────────────────
@server.tool()
def recent_sessions(days: int = 28) -> str:
    """Runs from the last N days with laps merged into one session per workout,
    each classified easy / intervals / tempo / race / long, with a summary of the
    work reps (e.g. '5x1.0 km @ 3:53, HR 177')."""
    return t.recent_sessions(days)


@server.tool()
def recent_runs(days: int = 30) -> str:
    """Raw running rows from the last N days. NOTE: Garmin saves each lap as its
    own row — prefer recent_sessions or week_summary."""
    return t.recent_runs(days)


@server.tool()
def recent_cycling(days: int = 30) -> str:
    """Cycling rides from the last N days: distance, duration, speed, HR, calories."""
    return t.recent_cycling(days)


@server.tool()
def recent_sleep(nights: int = 14) -> str:
    """Recent nights: sleep score, duration, stages, HRV, resting HR, stress."""
    return t.recent_sleep(nights)


@server.tool()
def recent_calories(days: int = 14) -> str:
    """Daily calories burned (active, resting, total) for the last N days."""
    return t.recent_calories(days)


@server.tool()
def recent_nutrition(days: int = 14) -> str:
    """Daily food intake for the last N days: kcal and protein/fat/carbs."""
    return t.recent_nutrition(days)


@server.tool()
def pr_progression(distance: str) -> str:
    """Times at a race distance over the past year (oldest first).
    `distance`: 5k, 10k, half_marathon or marathon."""
    return t.pr_progression(distance)


# ── food logging (writes to the food library / food log only) ────────────
@server.tool()
def find_food(name: str) -> str:
    """Look up a food in the athlete's library (per-100g kcal/protein/fat/carbs)."""
    return nutrition._run_tool("find_food", {"name": name})


@server.tool()
def add_food(name: str, kcal_per_100g: float, protein_per_100g: float = 0,
             fat_per_100g: float = 0, carbs_per_100g: float = 0) -> str:
    """Add or update a food in the library (per 100 g). Only after the user has
    confirmed the values."""
    return nutrition._run_tool("add_food", {
        "name": name, "kcal_per_100g": kcal_per_100g, "protein_per_100g": protein_per_100g,
        "fat_per_100g": fat_per_100g, "carbs_per_100g": carbs_per_100g})


@server.tool()
def log_food(food_name: str, grams: float, date: str = "") -> str:
    """Log a portion (grams) of a library food. `date` YYYY-MM-DD, empty = today.
    Returns the updated day total."""
    return nutrition._run_tool("log_food", {"food_name": food_name, "grams": grams, "date": date})


@server.tool()
def food_day(date: str = "") -> str:
    """A day's food log (items with ids) and macro total. Empty date = today."""
    return nutrition._run_tool("get_day_total", {"date": date})


@server.tool()
def remove_food_entry(entry_id: int) -> str:
    """Delete one food-log entry by id (get ids from food_day)."""
    return nutrition._run_tool("remove_log_entry", {"id": entry_id})


if __name__ == "__main__":
    server.run("stdio")
