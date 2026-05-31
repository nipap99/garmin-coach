"""FastAPI app entry point.

Mounts route modules, serves the single-page HTML shell, and exposes
JSON-ish HTML fragments for HTMX to swap into the page.
"""
from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from . import config
from .routes import activities as activities_routes
from .routes import chat as chat_routes
from .routes import goals as goals_routes

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


app = FastAPI(title="Garmin Coach")

# Serve the frontend folder as static (single-page index.html)
app.mount(
    "/static",
    StaticFiles(directory=str(config.PROJECT_ROOT / "frontend")),
    name="static",
)

# Mount feature routes
app.include_router(activities_routes.router)
app.include_router(goals_routes.router)
app.include_router(chat_routes.router)


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    """Serve the single-page UI."""
    html_path = config.PROJECT_ROOT / "frontend" / "index.html"
    return HTMLResponse(html_path.read_text(encoding="utf-8"))


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}
