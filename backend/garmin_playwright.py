"""Playwright-based Garmin Connect CSV exporter.

Opens a visible Chromium window so the user can log in if needed,
then automatically navigates to the running-activities page and
clicks "Export to CSV". The downloaded file is saved to data/CSVs/
with a timestamp name so it never collides with existing files.

Browser cookies are persisted in data/garmin_session.json, so
re-login is usually only needed once or after a session expires.
"""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from playwright.sync_api import TimeoutError as PWTimeoutError
from playwright.sync_api import sync_playwright

from . import config

logger = logging.getLogger(__name__)

ACTIVITIES_URL = "https://connect.garmin.com/app/activities?activityType=running"
DOWNLOAD_DIR: Path = config.PROJECT_ROOT / "data" / "CSVs"

# Export button text in supported locales
_EXPORT_LABELS = [
    "Εξαγωγή σε CSV",       # Greek
    "Export to CSV",          # English
    "Exportar para CSV",      # Portuguese
    "Als CSV exportieren",    # German
    "Exporter en CSV",        # French
    "Exportar a CSV",         # Spanish
]

# How long to wait for the user to log in (milliseconds)
_LOGIN_TIMEOUT_MS = 300_000   # 5 minutes


PROFILE_DIR: Path = config.PROJECT_ROOT / "data" / "chrome_profile"


def export_csv() -> Path:
    """Open Garmin Connect, wait for login if needed, export running CSV.

    Uses a persistent Chrome profile stored in data/chrome_profile/ so the
    browser looks like a real returning user (cookies, history) and Garmin
    does not block it as a bot. After the first successful login the session
    is reused automatically.

    Returns the local path of the saved CSV file.
    """
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M")
    dest = DOWNLOAD_DIR / f"garmin_sync_{timestamp}.csv"

    with sync_playwright() as pw:
        # launch_persistent_context keeps cookies/history between runs and
        # avoids the "new unknown device" block Garmin applies to fresh profiles
        context = _launch_persistent(pw)
        page = context.new_page()

        logger.info("Opening %s", ACTIVITIES_URL)
        page.goto(ACTIVITIES_URL, wait_until="domcontentloaded")

        _wait_for_login(page)

        # After login Garmin often redirects to the home page — go back to activities
        if "activities" not in page.url:
            logger.info("Post-login redirect detected, navigating to activities page…")
            page.goto(ACTIVITIES_URL, wait_until="domcontentloaded")

        # Wait up to 45 s for the export button to appear (Garmin SPA is slow)
        logger.info("Waiting for Export CSV button to appear…")
        export_btn = _wait_for_export_button(page, timeout=45_000)

        if export_btn is None:
            context.close()
            raise RuntimeError(
                "Could not find the Export CSV button after 45 seconds. "
                "Try clicking Sync again — the page may have been slow to load."
            )

        logger.info("Clicking export button…")
        with page.expect_download(timeout=60_000) as dl_handle:
            export_btn.click()

        download = dl_handle.value
        download.save_as(dest)
        logger.info("CSV saved → %s", dest)

        context.close()  # profile is persisted automatically by Playwright

    return dest


# ── helpers ────────────────────────────────────────────────────────────────


def _launch_persistent(pw):
    """Launch a persistent browser context using the best available browser.

    Tries Google Chrome → Microsoft Edge → explicit paths → bundled Chromium.
    The persistent profile lives in data/chrome_profile/ and survives between
    syncs, so Garmin sees a familiar browser rather than a fresh bot profile.
    """
    import os

    # --disable-blink-features=AutomationControlled stops Chrome from setting
    # navigator.webdriver = true, which Garmin's SSO checks to detect bots.
    common_kwargs = {
        "headless": False,
        "accept_downloads": True,
        "args": ["--disable-blink-features=AutomationControlled"],
    }

    # 1 & 2 — Playwright auto-locates Chrome / Edge
    for channel in ("chrome", "msedge"):
        try:
            ctx = pw.chromium.launch_persistent_context(
                str(PROFILE_DIR), channel=channel, **common_kwargs
            )
            logger.info("Launched persistent context via channel=%s", channel)
            _mask_webdriver(ctx)
            return ctx
        except Exception:
            continue

    # 3 — explicit paths for per-user Chrome / system Edge
    _PATHS = [
        os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    ]
    for exe in _PATHS:
        if Path(exe).exists():
            try:
                ctx = pw.chromium.launch_persistent_context(
                    str(PROFILE_DIR), executable_path=exe, **common_kwargs
                )
                logger.info("Launched persistent context via executable_path=%s", exe)
                _mask_webdriver(ctx)
                return ctx
            except Exception:
                continue

    # 4 — bundled Chromium (requires ``playwright install chromium``)
    logger.info("Falling back to Playwright bundled Chromium (persistent)")
    ctx = pw.chromium.launch_persistent_context(str(PROFILE_DIR), **common_kwargs)
    _mask_webdriver(ctx)
    return ctx


def _mask_webdriver(context) -> None:
    """Inject a script that hides the webdriver flag on every new page."""
    context.add_init_script(
        "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
    )


def _wait_for_login(page) -> None:
    """Block until Garmin Connect shows the app (not the login page)."""
    url = page.url
    needs_login = (
        "sso.garmin.com" in url
        or "/signin" in url
        or "login" in url.lower()
        or "connect.garmin.com/app" not in url
    )
    if not needs_login:
        return

    print()
    print("=" * 60)
    print("  Garmin sync: please log in in the browser window.")
    print("  The sync will continue automatically once you are in.")
    print("=" * 60)
    print()

    try:
        page.wait_for_url(
            "*://connect.garmin.com/app/*",
            timeout=_LOGIN_TIMEOUT_MS,
        )
    except PWTimeoutError as exc:
        raise TimeoutError(
            "Login timed out after 5 minutes. Please click Sync again."
        ) from exc

    logger.info("Login detected — resuming sync…")


def _wait_for_export_button(page, timeout: int = 45_000):
    """Wait for the Export CSV button to become visible, then return it.

    Tries each known locale label in turn, then falls back to any
    button/link containing the text "CSV".  Returns None if nothing
    appears within the timeout.
    """
    # Try each locale label — give the first one the full timeout,
    # shorter slots for the rest (they share the remaining budget).
    per_label = max(5_000, timeout // max(len(_EXPORT_LABELS), 1))
    for label in _EXPORT_LABELS:
        try:
            loc = page.get_by_text(label, exact=True)
            loc.first.wait_for(state="visible", timeout=per_label)
            logger.info("Export button found: %r", label)
            return loc.first
        except PWTimeoutError:
            continue

    # Fallback: any visible button or link containing "CSV"
    try:
        loc = page.locator("button, a").filter(has_text="CSV")
        loc.first.wait_for(state="visible", timeout=10_000)
        return loc.first
    except PWTimeoutError:
        pass

    return None
