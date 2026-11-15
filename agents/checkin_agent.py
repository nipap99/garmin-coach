"""Weekly check-in agent: a standalone Claude agent over the local Garmin DB.

Learning project (step 2 of the AI-agents learning path). It's the same idea as
the `run-analyst` subagent, but as a plain Python program using the Anthropic
SDK's Tool Runner, so it can run anywhere: a terminal, a scheduled task, or
inside the web app.

How it works:
  1. Each @beta_tool function below is a tool. Claude reads its name, docstring
     and arguments to decide when to call it.
  2. tool_runner() runs the agent loop: Claude asks for a tool, the SDK calls
     our Python function, sends the result back, and repeats until Claude
     writes its final answer.

Run (from the garmin-coach folder; uses ANTHROPIC_API_KEY from .env, paid API):
    .venv\\Scripts\\python.exe agents\\checkin_agent.py
    .venv\\Scripts\\python.exe agents\\checkin_agent.py "Am I ready for a hard track session tomorrow?"

Scheduled mode (step 3): `--save` writes the answer to a Markdown file in
OUTPUT_DIR instead of only printing it. scripts/install_checkin_task.ps1 runs
it that way every Wednesday morning.
    .venv\\Scripts\\python.exe agents\\checkin_agent.py --save "question..."
"""
from __future__ import annotations

import datetime as dt
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from anthropic import Anthropic, beta_tool

from backend import coach, config

MODEL = "claude-opus-5-5"
OUTPUT_DIR = r"C:\Users\papad\Desktop\Fitness Report\check-ins"
LOG_PATH = os.path.join(config.PROJECT_ROOT, "data", "checkin_agent.log")

SYSTEM = """You are a running coach. The athlete's real Garmin data is available
through your tools. Never guess numbers: every figure you report must come from
a tool call.

The athlete runs Monday-Saturday (Saturday = long run) and rests on Sunday.

How to read the numbers:
- ACWR 0.8-1.3: safe building zone. Above ~1.5: load spiked past the 4-week
  base, high injury risk, pull back. Below 0.8: detraining.
- Rising load while HRV trends down or resting HR rises is the clearest
  "back off" signal.
- TRIMP depends on an estimated max HR, so trust the trend and the ratio more
  than the absolute load.
- If `get_goals` returns nothing, use the goal paces from `get_speed_workouts`.

Answer in under 200 words. For a weekly check-in use: Status, Load,
Recovery, Toward your goal, Do this next."""


@beta_tool
def get_goals() -> str:
    """The athlete's active running goals (target distance, date and time)."""
    return coach._run_tool("get_goals", {})


@beta_tool
def get_training_load() -> str:
    """Weekly training load (TRIMP, runs + rides) with the acute:chronic
    workload ratio (ACWR), plus weekly HRV and resting HR."""
    return coach._run_tool("get_training_load", {})


@beta_tool
def get_recent_runs(days: int = 28) -> str:
    """Running activities from the last N days: date, distance (km), duration,
    pace, average and max HR, VO2max.

    Args:
        days: How many days back to look.
    """
    return coach._run_tool("get_recent_activities", {"days": days})


@beta_tool
def get_recent_sleep(nights: int = 14) -> str:
    """Recent nights of sleep: score, duration, HRV, resting HR, stress.

    Args:
        nights: How many nights back to look.
    """
    return coach._run_tool("get_recent_sleep", {"nights": nights})


@beta_tool
def get_speed_workouts() -> str:
    """The athlete's track workout library and goal/current-fitness paces."""
    path = os.path.join(config.PROJECT_ROOT, "data", "speed_library.json")
    with open(path, encoding="utf-8") as fh:
        return fh.read()


TOOLS = [get_goals, get_training_load, get_recent_runs, get_recent_sleep, get_speed_workouts]


def run(question: str) -> str:
    client = Anthropic(api_key=config.ANTHROPIC_API_KEY)
    runner = client.beta.messages.tool_runner(
        model=MODEL,
        max_tokens=16000,
        system=SYSTEM,
        tools=TOOLS,
        # The model doesn't know today's date; without it, it can't tell
        # "no run yet this week" from "data missing".
        messages=[{"role": "user", "content": f"Today is {dt.date.today():%A %d %B %Y}.\n\n{question}"}],
    )

    # Each loop turn is one Claude response. Printing the tool calls makes the
    # agent loop visible: you see what Claude decided to look up, and in what order.
    final = ""
    for message in runner:
        for block in message.content:
            if block.type == "tool_use":
                print(f"  -> calling {block.name}({block.input})")
            elif block.type == "text":
                final = block.text
        if message.stop_reason == "refusal":
            return "The model declined this request."
    return final


def save(question: str, answer: str) -> str:
    """Write the check-in to a dated Markdown file and return its path."""
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    today = dt.date.today().isoformat()
    path = os.path.join(OUTPUT_DIR, f"check-in {today}.md")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(f"# Check-in {today}\n\n**Question:** {question}\n\n---\n\n{answer}\n")
    return path


if __name__ == "__main__":
    args = sys.argv[1:]
    save_mode = "--save" in args
    args = [a for a in args if a != "--save"]
    question = " ".join(args) or (
        "Give me my weekly running check-in: how is my training going and "
        "should I push or back off this coming week?"
    )

    # Scheduled runs are windowless (pythonw), so nobody sees errors on screen:
    # log every run, success or failure, to data/checkin_agent.log.
    logging.basicConfig(filename=LOG_PATH, level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    try:
        print(f"Question: {question}\n")
        answer = run(question)
        print(answer)
        if save_mode:
            logging.info("saved check-in to %s", save(question, answer))
    except Exception:
        logging.exception("check-in failed")
        raise
