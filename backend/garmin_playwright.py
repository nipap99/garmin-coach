"""Playwright-based Garmin Connect CSV exporter.

Opens a visible Chromium window so the user can log in if needed, then — in
one session — navigates to each sport's filtered activities view (running,
cycling) and clicks "Export to CSV". Each download is saved to data/CSVs/
with a sport + timestamp name so files never collide.

Browser cookies/history are persisted in data/chrome_profile/, so re-login is
usually only needed once or after a session expires.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime
from pathlib import Path

from playwright.sync_api import TimeoutError as PWTimeoutError
from playwright.sync_api import sync_playwright

from . import config

logger = logging.getLogger(__name__)

_ACTIVITIES_BASE = "https://connect.garmin.com/app/activities"

# Each sport is its own top-level filter tab on the Garmin activities page,
# selected via the activityType URL param. Exporting from each filtered view
# gives a sport-specific CSV with the right columns (running has pace,
# cycling has speed).
SPORT_URLS: dict[str, str] = {
    "running": f"{_ACTIVITIES_BASE}?activityType=running",
    "cycling": f"{_ACTIVITIES_BASE}?activityType=cycling",
}

SLEEP_URL = "https://connect.garmin.com/app/sleep"

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


def export_activities(sports: list[str] | None = None) -> list[Path]:
    """Open Garmin Connect once, wait for login if needed, and export a CSV for
    each requested sport (default: running then cycling) in the same session.

    Uses a persistent Chrome profile stored in data/chrome_profile/ so the
    browser looks like a real returning user (cookies, history) and Garmin
    does not block it as a bot. After the first successful login the session
    is reused automatically.

    Returns the list of saved CSV paths (one per sport that exported OK).
    """
    sports = sports or ["running", "cycling"]
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)

    saved: list[Path] = []
    with sync_playwright() as pw:
        # launch_persistent_context keeps cookies/history between runs and
        # avoids the "new unknown device" block Garmin applies to fresh profiles
        context = _launch_persistent(pw)
        page = context.new_page()

        first_url = SPORT_URLS[sports[0]]
        logger.info("Opening %s", first_url)
        page.goto(first_url, wait_until="domcontentloaded")
        _wait_for_login(page)

        # After login Garmin may redirect to the home page — make sure we land
        # back on the first sport's activities view before exporting.
        if "activities" not in page.url:
            logger.info("Post-login redirect detected, returning to activities…")
            page.goto(first_url, wait_until="domcontentloaded")

        for i, sport in enumerate(sports):
            try:
                # We're already on the first sport's page (fast path — no reload).
                # Only navigate when switching to a later sport's filter.
                saved.append(_export_one_sport(page, sport, navigate=i > 0))
            except Exception:  # noqa: BLE001 — keep going so one bad sport
                logger.exception("Export failed for %s", sport)

        context.close()  # profile is persisted automatically by Playwright

    if not saved:
        raise RuntimeError(
            "No CSVs were exported. Try Sync again — the page may have been "
            "slow to load, or you may need to log in."
        )
    return saved


def _export_one_sport(page, sport: str, navigate: bool = True) -> Path:
    """Export one sport's CSV.

    ``navigate`` controls the slow path: when True (switching to a different
    sport) we reload the filtered view and wait for the network to settle so
    the activity list actually changes before we export. For the first sport
    we're already on the page, so we skip both — keeping single-sport syncs
    as fast as the original running-only export.
    """
    url = SPORT_URLS[sport]
    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    dest = DOWNLOAD_DIR / f"garmin_sync_{sport}_{timestamp}.csv"

    if navigate:
        logger.info("Switching to %s view…", sport)
        page.goto(url, wait_until="domcontentloaded")
        # Let the SPA reload the filtered activity list before exporting
        try:
            page.wait_for_load_state("networkidle", timeout=20_000)
        except PWTimeoutError:
            pass  # networkidle is unreliable on SPAs; carry on

    logger.info("Waiting for Export CSV button (%s)…", sport)
    export_btn = _wait_for_export_button(page, timeout=45_000)
    if export_btn is None:
        raise RuntimeError(f"Export CSV button not found for {sport}.")

    logger.info("Exporting %s…", sport)
    with page.expect_download(timeout=60_000) as dl_handle:
        export_btn.click()
    dl_handle.value.save_as(dest)
    logger.info("%s CSV saved → %s", sport, dest)
    return dest


def export_sleep() -> Path:
    """Open the Garmin sleep page, ensure the 7-day view, and export its weekly CSV.

    The sleep export lives behind a "⋮" (more) menu rather than a visible button,
    so we select the 7-day view, open that menu, then click "Εξαγωγή σε CSV".
    Returns the saved CSV path.
    """
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    dest = DOWNLOAD_DIR / f"garmin_sync_sleep_{timestamp}.csv"

    with sync_playwright() as pw:
        context = _launch_persistent(pw)
        page = context.new_page()

        logger.info("Opening %s", SLEEP_URL)
        page.goto(SLEEP_URL, wait_until="domcontentloaded")
        _wait_for_login(page)
        if "sleep" not in page.url:
            logger.info("Post-login redirect — returning to sleep page…")
            page.goto(SLEEP_URL, wait_until="domcontentloaded")

        try:
            page.wait_for_load_state("networkidle", timeout=20_000)
        except PWTimeoutError:
            pass

        _select_seven_day_view(page)
        export_item = _open_sleep_export_menu(page)
        if export_item is None:
            context.close()
            raise RuntimeError(
                "Could not find the sleep 'Εξαγωγή σε CSV' option. The page may "
                "have been slow, or its layout changed."
            )

        logger.info("Exporting sleep…")
        with page.expect_download(timeout=60_000) as dl_handle:
            export_item.click()
        dl_handle.value.save_as(dest)
        logger.info("Sleep CSV saved → %s", dest)

        context.close()

    return dest


_SEVEN_DAY_LABELS = ["7 ημέρες", "7 days", "7 Days"]


def _select_seven_day_view(page) -> bool:
    """Click the 7-day range tab. The page defaults to the 1-day view, whose
    export is a different per-night format we can't parse — so this must
    succeed. Returns True if the tab was clicked."""
    for label in _SEVEN_DAY_LABELS:
        try:
            loc = page.get_by_text(label, exact=True)
            if loc.count() > 0:
                loc.first.click(timeout=5_000)
                page.wait_for_timeout(2_000)  # let the weekly view load
                logger.info("Selected 7-day view (%r)", label)
                return True
        except Exception:
            continue
    logger.warning("Could not find the 7-day view tab — export may be 1-day data")
    return False


def _find_export_item(page, timeout: int = 1_500):
    """Return the visible 'Export to CSV' menu item, or None."""
    for label in _EXPORT_LABELS:
        loc = page.get_by_text(label, exact=True)
        if loc.count() > 0:
            try:
                loc.first.wait_for(state="visible", timeout=timeout)
                return loc.first
            except PWTimeoutError:
                continue
    return None


def _open_sleep_export_menu(page):
    """Open the ⋮ (more) menu and return the visible 'Export to CSV' item.

    The kebab is an icon button in the top-right of the page header (just after
    the "?" help icon). We can't target it by text, so we collect the small
    icon-only buttons in the top region and click them rightmost-first (the ⋮
    is the right-most one), checking after each click for the export item.
    """
    # Maybe it's already open / visible
    item = _find_export_item(page, timeout=1_500)
    if item is not None:
        return item

    # First try the explicit "more menu" attributes, if present
    for sel in (
        "button[aria-haspopup='menu']",
        "button[aria-haspopup='true']",
        "button[aria-label*='more' i]",
        "button[aria-label*='menu' i]",
        "button[aria-label*='επιλογ' i]",
        "button[aria-label*='περισσότ' i]",
    ):
        loc = page.locator(sel)
        for i in range(min(loc.count(), 3)):
            try:
                loc.nth(i).click(timeout=1_500)
            except Exception:
                continue
            item = _find_export_item(page)
            if item is not None:
                return item
            _dismiss(page)

    # Fallback: small icon buttons in the CONTENT header, clicked right-to-left.
    # The y-floor excludes the global top app bar (cloud / bell / watch / avatar);
    # the ⋮ sits in the page header just below it, to the right of the "?" icon.
    buttons = page.locator("button, [role='button']")
    candidates: list[tuple[float, int]] = []
    for i in range(min(buttons.count(), 80)):
        b = buttons.nth(i)
        try:
            if not b.is_visible():
                continue
            box = b.bounding_box()
            if not box or box["y"] < 90 or box["y"] > 450:   # content header band
                continue
            text = (b.inner_text() or "").strip()
            if len(text) > 3:                    # skip labelled buttons (e.g. "7 ημέρες")
                continue
            candidates.append((box["x"], i))
        except Exception:
            continue

    candidates.sort(key=lambda c: -c[0])         # right-most (the ⋮) first
    for _, i in candidates:
        try:
            buttons.nth(i).click(timeout=1_500)
        except Exception:
            continue
        item = _find_export_item(page)
        if item is not None:
            return item
        _dismiss(page)

    return None


def _dismiss(page) -> None:
    """Close any open menu/popover before trying the next candidate."""
    try:
        page.keyboard.press("Escape")
    except Exception:
        pass


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
