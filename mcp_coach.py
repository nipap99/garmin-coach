"""Local MCP server exposing your Garmin running data to the Claude Desktop app.

Run inside Claude Desktop (Settings → Developer → Edit Config → mcpServers), it
lets you chat with Claude — on your subscription, NOT the paid API — while it
reads your local garmin-coach database. It reuses the exact same data tools the
in-app coach uses (backend.coach._run_tool), so the two never drift apart.

Nothing leaves your machine: the server talks to your local PostgreSQL only.
Postgres must be running (it's independent of whether the coach web app is up).

Manual test (acts like Claude would):
    .venv\\Scripts\\python.exe mcp_coach.py    # then Ctrl-C; it waits on stdio
"""
from __future__ import annotations

import os
import sys

# Make `import backend...` work no matter what working directory Claude Desktop
# launches us from (config.py derives all paths from its own location, so the
# database URL and .env resolve correctly regardless of cwd).
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mcp.server.mcpserver import MCPServer

from backend import coach

INSTRUCTIONS = """This server exposes the user's real Garmin training data from
their local database. When they ask about their running, training, recovery, or
nutrition, call the tools and ground every answer in the actual numbers — don't
guess from general knowledge when the data is one tool call away.

Reading the key metric — ACWR (acute:chronic workload ratio) from training_load:
- 0.8-1.3 is the safe building zone; above ~1.5 means weekly load has spiked far
  past the athlete's 4-week base → high injury/overtraining risk (pull back);
  below 0.8 is detraining.
- Read load together with recovery: rising load while HRV trends down (or resting
  HR rises) is the clearest "back off" signal.
TRIMP load is HR-based and depends on an estimated max HR, so trust the trend and
the ACWR ratio more than the absolute load number."""

server = MCPServer("garmin-coach", instructions=INSTRUCTIONS)


@server.tool()
def recent_runs(days: int = 30) -> str:
    """Running activities from the last N days: date, distance (km), duration,
    average pace (min/km), average & max HR, VO2max, calories."""
    return coach._run_tool("get_recent_activities", {"days": days})


@server.tool()
def recent_cycling(days: int = 30) -> str:
    """Cycling activities from the last N days: date, distance, duration, average
    speed (km/h), HR, calories."""
    return coach._run_tool("get_recent_cycling", {"days": days})


@server.tool()
def recent_sleep(nights: int = 14) -> str:
    """Recent nights of sleep: score, duration, deep/light/REM minutes, HRV,
    resting HR, respiration, stress."""
    return coach._run_tool("get_recent_sleep", {"nights": nights})


@server.tool()
def recent_calories(days: int = 14) -> str:
    """Daily calories burned for the last N days: active, resting (BMR), total."""
    return coach._run_tool("get_recent_calories", {"days": days})


@server.tool()
def recent_nutrition(days: int = 14) -> str:
    """Daily food intake for the last N days: total kcal and protein/fat/carbs."""
    return coach._run_tool("get_recent_nutrition", {"days": days})


@server.tool()
def goals() -> str:
    """The user's active running goals (target distance, date, and time)."""
    return coach._run_tool("get_goals", {})


@server.tool()
def pr_progression(distance: str) -> str:
    """How the user's times at a distance have progressed over the past year.
    `distance` must be one of: 5k, 10k, half_marathon, marathon."""
    return coach._run_tool("get_pr_progression", {"distance": distance})


@server.tool()
def training_load() -> str:
    """Weekly training load (Banister TRIMP, runs + rides) with the acute:chronic
    workload ratio (ACWR) overtraining signal, plus weekly HRV and resting HR."""
    return coach._run_tool("get_training_load", {})


@server.tool()
def speed_workouts() -> str:
    """The athlete's track & speed workout library with paces set from their
    current fitness. Use it to prescribe VARIETY in track/interval sessions —
    rotate different workouts week to week rather than repeating the same one."""
    import json
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "speed_library.json")
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    except Exception as exc:  # noqa: BLE001
        return json.dumps({"error": f"could not read speed library: {exc}"})


if __name__ == "__main__":
    server.run("stdio")
