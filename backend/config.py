"""Centralized configuration loaded from .env."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent
# override=True so values in .env always win over anything pre-set in the OS
# environment. This is the right behavior for a local single-user app where
# .env is the source of truth.
load_dotenv(PROJECT_ROOT / ".env", override=True)


def _resolve(path_str: str) -> Path:
    """Resolve a path from .env relative to PROJECT_ROOT if it's relative."""
    p = Path(path_str)
    if not p.is_absolute():
        p = PROJECT_ROOT / p
    return p.resolve()


GARMIN_EMAIL: str = os.environ.get("GARMIN_EMAIL", "")
GARMIN_PASSWORD: str = os.environ.get("GARMIN_PASSWORD", "")
ANTHROPIC_API_KEY: str = os.environ.get("ANTHROPIC_API_KEY", "")

GARMIN_TOKEN_DIR: Path = _resolve(os.environ.get("GARMIN_TOKEN_DIR", "./data/garmin_tokens"))

# Postgres connection string for the app's data. Format:
#   postgresql://USER:PASSWORD@HOST:PORT/DBNAME
# The default points at the local "coach" login + "garmin_coach" database
# created by scripts/setup_postgres.sql.
DATABASE_URL: str = os.environ.get(
    "DATABASE_URL",
    "postgresql://coach:coach_local_dev@localhost:5432/garmin_coach",
)

# Legacy SQLite path — kept only so the one-time migration script can read the
# old data out of it. The app no longer uses SQLite at runtime.
DATABASE_PATH: Path = _resolve(os.environ.get("DATABASE_PATH", "./data/coach.db"))
DATA_DIR: Path = PROJECT_ROOT / "data"
