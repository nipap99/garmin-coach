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
from datetime import date, datetime
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
CALORIES_URL_TMPL = "https://connect.garmin.com/app/calories/{date}/1"
_SEVEN_DAY_LABELS = ["7 ημέρες", "7 days", "7 Days"]

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
    """Export one sport's CSV, tolerating a slow or blank-loading activities page.

    Garmin's activities view is a heavy SPA that sometimes renders blank on the
    first paint (empty list, just a spinner). The Export CSV button only exists
    once the list has loaded, so we (a) wait for the activity list to actually
    appear before hunting the button, and (b) if the button never shows, reload
    the filtered view and try again a couple of times rather than giving up.
    """
    url = SPORT_URLS[sport]
    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    dest = DOWNLOAD_DIR / f"garmin_sync_{sport}_{timestamp}.csv"

    export_btn = None
    max_tries = 3
    for attempt in range(1, max_tries + 1):
        # Reload the filtered view when switching sports, or on any retry.
        if navigate or attempt > 1:
            logger.info("Loading %s view (attempt %d/%d)…", sport, attempt, max_tries)
            page.goto(url, wait_until="domcontentloaded")

        _wait_for_activities_ready(page)      # let the list render first
        export_btn = _wait_for_export_button(page, timeout=30_000)
        if export_btn is not None:
            break
        logger.warning("Export button not found for %s (attempt %d/%d) — reloading…",
                       sport, attempt, max_tries)
        page.wait_for_timeout(1_500)

    if export_btn is None:
        dump = _dump_debug(page, sport)
        raise RuntimeError(
            f"Export CSV button never appeared for {sport} after {max_tries} tries — "
            "Garmin's page was slow, stuck loading, or its layout changed. "
            f"Saved a screenshot + page HTML to {dump} for diagnosis. Try Sync again."
        )

    logger.info("Exporting %s…", sport)
    with page.expect_download(timeout=60_000) as dl_handle:
        export_btn.click()
    dl_handle.value.save_as(dest)
    logger.info("%s CSV saved → %s", sport, dest)
    return dest


def _dump_debug(page, sport: str) -> Path:
    """Save a screenshot + page HTML when export fails, so a blind failure
    becomes something we can actually look at. Returns the debug directory."""
    debug_dir = DOWNLOAD_DIR / "debug"
    debug_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    try:
        page.screenshot(path=str(debug_dir / f"{sport}_{stamp}.png"), full_page=True)
    except Exception:  # noqa: BLE001
        logger.warning("Could not save debug screenshot", exc_info=True)
    try:
        (debug_dir / f"{sport}_{stamp}.html").write_text(
            page.content(), encoding="utf-8")
    except Exception:  # noqa: BLE001
        logger.warning("Could not save debug HTML", exc_info=True)
    logger.warning("Saved export-failure debug for %s → %s", sport, debug_dir)
    return debug_dir


def _wait_for_activities_ready(page, timeout: int = 25_000) -> None:
    """Wait for the activity list to render, so the Export control is present.

    Each list row links to an individual activity; once one is visible the SPA
    has finished loading. This is best-effort: if the list never appears we do
    NOT fail here — the caller's export-button hunt (with its reload-retry) takes
    over — so a wrong/changed selector degrades gracefully instead of breaking.
    """
    try:
        page.locator("a[href*='/activity/']").first.wait_for(
            state="visible", timeout=timeout)
        logger.info("Activities list loaded.")
    except PWTimeoutError:
        logger.warning("Activities list slow to load; proceeding to export hunt.")
        page.wait_for_timeout(2_000)


_YEAR_VIEW_LABELS = ["1 έτος", "1 year", "1 Year"]
_DAY_VIEW_LABELS = ["1 ημέρα", "1 day", "1 Day"]

# view key → (range-tab labels, log name, download filename prefix)
_SLEEP_VIEWS: dict[str, tuple[list[str], str, str]] = {
    "night":  (_DAY_VIEW_LABELS,  "1-day",  "garmin_sync_sleep_night"),
    "weekly": (_YEAR_VIEW_LABELS, "1-year", "garmin_sync_sleep"),
}


# Text Garmin shows on its transient server-error page.
_GARMIN_CRASH_MARKERS = ["Looks Like We Crashed", "pick ourselves up",
                         "Κάτι πήγε στραβά"]
# Any range tab proves the real sleep page (not the crash page) has rendered.
_SLEEP_RANGE_LABELS = _DAY_VIEW_LABELS + _YEAR_VIEW_LABELS


def _is_garmin_crash_page(page) -> bool:
    try:
        body = page.inner_text("body", timeout=3_000)
    except Exception:  # noqa: BLE001
        return False
    return any(marker in body for marker in _GARMIN_CRASH_MARKERS)


def _wait_for_sleep_ready(page, max_tries: int = 4) -> bool:
    """Reload past Garmin's transient crash page until the real sleep page —
    with its range tabs — is showing. Returns False if it never recovers."""
    for attempt in range(1, max_tries + 1):
        if _is_garmin_crash_page(page):
            logger.warning("Garmin sleep page crashed (attempt %d/%d) — waiting "
                           "then reloading…", attempt, max_tries)
        else:
            # Not the crash page — is a range tab actually present yet?
            for label in _SLEEP_RANGE_LABELS:
                loc = page.get_by_text(label, exact=True)
                try:
                    if loc.count() and loc.first.is_visible():
                        logger.info("Sleep page ready (found %r).", label)
                        return True
                except Exception:  # noqa: BLE001
                    continue
            logger.info("Sleep page loaded but range tabs not ready yet "
                        "(attempt %d/%d)…", attempt, max_tries)
        if attempt < max_tries:
            page.wait_for_timeout(3_000 * attempt)   # back off: 3s, 6s, 9s
            page.goto(SLEEP_URL, wait_until="domcontentloaded")
    return False


def export_sleep_views(views: list[str]) -> dict[str, Path]:
    """Export one or more sleep views in a single browser session.

    ``views`` is a subset of ``("night", "weekly")``. Opens the page once, logs
    in once, then for each requested view flips to its range tab and exports.
    Each view is wrapped independently, so one failing never aborts the other.
    Returns ``{view: saved_path}`` for the views that exported OK.
    """
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)

    saved: dict[str, Path] = {}
    with sync_playwright() as pw:
        context = _launch_persistent(pw)
        page = context.new_page()

        logger.info("Opening %s", SLEEP_URL)
        page.goto(SLEEP_URL, wait_until="domcontentloaded")
        _wait_for_login(page)
        if "sleep" not in page.url:
            logger.info("Post-login redirect — returning to sleep page…")
            page.goto(SLEEP_URL, wait_until="domcontentloaded")

        # Garmin's sleep endpoint sometimes serves a "Looks Like We Crashed"
        # page (a transient server error). Reload past it before exporting,
        # otherwise the range tabs / export menu simply aren't there.
        if not _wait_for_sleep_ready(page):
            _dump_debug(page, "sleep_crash")
            context.close()
            raise RuntimeError(
                "Garmin's sleep page is temporarily down (it served a "
                "\"Looks Like We Crashed\" error). This is on Garmin's side, "
                "not the app — wait a minute and try Sync Sleep again."
            )

        for view in views:
            labels, name, prefix = _SLEEP_VIEWS[view]
            try:
                ts = datetime.now().strftime("%Y-%m-%d_%H%M%S")
                dest = DOWNLOAD_DIR / f"{prefix}_{ts}.csv"
                _select_range_view(page, labels, name)
                item = _open_sleep_export_menu(page)
                if item is None:
                    raise RuntimeError(f"Export menu not found for {name}.")
                logger.info("Exporting sleep (%s)…", name)
                with page.expect_download(timeout=60_000) as dl_handle:
                    item.click()
                dl_handle.value.save_as(dest)
                saved[view] = dest
                logger.info("Sleep %s CSV saved → %s", name, dest)
            except Exception:  # noqa: BLE001 — keep going so one bad view
                logger.exception("Sleep export failed for %s", view)
                _dump_debug(page, f"sleep_{view}")

        context.close()

    if not saved:
        raise RuntimeError(
            "No sleep CSVs were exported. Garmin's sleep page was slow, its "
            "export menu changed, or you need to log in. Saved a screenshot + "
            f"page HTML to {DOWNLOAD_DIR / 'debug'} for diagnosis. Try again."
        )
    return saved


def export_sleep_night() -> Path:
    """Export the per-night (1-day view) sleep CSV — rich nightly detail."""
    return export_sleep_views(["night"])["night"]


def export_sleep() -> Path:
    """Export the weekly (1-year view) sleep CSV — the long-term backdrop."""
    return export_sleep_views(["weekly"])["weekly"]


def scrape_calories() -> list[list[str]]:
    """Scrape the calories page's 7-day table (it has no CSV export).

    Opens the page, switches to the 7-day view, and reads the daily table —
    the one whose header includes "Ημερομηνία" / "Date". Returns its raw data
    rows as lists of cell strings: [date, activity_cal, resting_cal, total_cal].
    """
    url = CALORIES_URL_TMPL.format(date=date.today().isoformat())
    with sync_playwright() as pw:
        context = _launch_persistent(pw)
        page = context.new_page()

        logger.info("Opening %s", url)
        page.goto(url, wait_until="domcontentloaded")
        _wait_for_login(page)
        if "calories" not in page.url:
            page.goto(url, wait_until="domcontentloaded")

        _select_range_view(page, _SEVEN_DAY_LABELS, "7-day")
        page.wait_for_timeout(2_500)  # let the table render

        rows = page.evaluate(
            """() => {
                for (const t of document.querySelectorAll("table")) {
                    const head = t.innerText || "";
                    if (head.includes("Ημερομηνία") || head.toLowerCase().includes("date")) {
                        const out = [];
                        t.querySelectorAll("tr").forEach(r => {
                            const cells = [...r.querySelectorAll("td")]
                                .map(c => c.innerText.trim());
                            if (cells.length >= 4 && cells[0]) out.push(cells.slice(0, 4));
                        });
                        return out;
                    }
                }
                return [];
            }"""
        )
        context.close()

    if not rows:
        raise RuntimeError(
            "Could not read the calories table. The page may have been slow, or "
            "you may need to log in."
        )
    logger.info("Scraped %d days of calories", len(rows))
    return rows


def _select_range_view(page, labels: list[str], name: str) -> bool:
    """Click a range tab (1-day / 1-year). The export *format* depends on the
    selected range, so this must succeed. The persistent profile remembers the
    last range, so we always click explicitly. Waits for the tab to render,
    then clicks. Returns True if clicked."""
    for label in labels:
        loc = page.get_by_text(label, exact=True)
        try:
            loc.first.wait_for(state="visible", timeout=25_000)
            loc.first.click(timeout=5_000)
            page.wait_for_timeout(1_500)  # let the range apply
            logger.info("Selected %s view (%r)", name, label)
            return True
        except PWTimeoutError:
            continue
    logger.warning("Could not find the %s view tab", name)
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

    The kebab is precisely identifiable: ``<button title="Μενού">`` (Greek) /
    ``title="Menu"`` (English). We click it directly; only if Garmin changes
    that marker do we fall back to the positional sweep.
    """
    for sel in ('button[title="Μενού"]', 'button[title="Menu"]', 'button[title*="ενού"]'):
        loc = page.locator(sel)
        try:
            loc.first.wait_for(state="visible", timeout=10_000)
        except PWTimeoutError:
            continue
        try:
            loc.first.click(timeout=5_000)
        except Exception:
            break
        item = _find_export_item(page, timeout=5_000)
        if item is not None:
            logger.info("Opened sleep menu via title selector")
            return item
        _dismiss(page)
        break  # found the menu button but no export item — go to fallback

    return _open_sleep_export_menu_fallback(page)


def _open_sleep_export_menu_fallback(page):
    """Positional fallback if the ⋮ can't be found by its title marker."""
    item = _find_export_item(page, timeout=1_500)
    if item is not None:
        return item

    # Small icon buttons in the CONTENT header, clicked right-to-left.
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

    def _try(desc: str, **launch_kwargs):
        """Launch, then PROVE the context is usable by opening a page.

        System Chrome/Edge can "succeed" at launch but immediately hand the
        profile off to an already-open browser of the same kind, killing the
        automation target — so new_page() throws TargetClosedError a moment
        later. We probe here and reject such a dead context, letting the caller
        fall through to the next (ultimately the isolated bundled Chromium)."""
        try:
            ctx = pw.chromium.launch_persistent_context(
                str(PROFILE_DIR), **common_kwargs, **launch_kwargs)
        except Exception as exc:  # noqa: BLE001 — browser not installed, etc.
            logger.info("Launch failed (%s): %s", desc, exc)
            return None
        try:
            ctx.new_page().close()   # probe: dead contexts raise here
        except Exception as exc:  # noqa: BLE001 — the hand-off death
            logger.warning("Context via %s closed on probe (%s) — trying next.",
                           desc, exc)
            try:
                ctx.close()
            except Exception:  # noqa: BLE001
                pass
            return None
        logger.info("Launched persistent context via %s", desc)
        _mask_webdriver(ctx)
        return ctx

    # 1 & 2 — Playwright auto-locates Chrome / Edge
    for channel in ("chrome", "msedge"):
        ctx = _try(f"channel={channel}", channel=channel)
        if ctx is not None:
            return ctx

    # 3 — explicit paths for per-user Chrome / system Edge
    _PATHS = [
        os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    ]
    for exe in _PATHS:
        if Path(exe).exists():
            ctx = _try(f"executable_path={exe}", executable_path=exe)
            if ctx is not None:
                return ctx

    # 4 — bundled Chromium: a separate binary that never collides with the
    # user's everyday Chrome, so it can't be killed by the hand-off above.
    logger.info("Falling back to Playwright bundled Chromium (persistent)")
    ctx = _try("bundled Chromium")
    if ctx is not None:
        return ctx
    raise RuntimeError(
        "Could not open a browser for Garmin sync. If your everyday Chrome is "
        "open, the bundled browser is used automatically — but it must be "
        "installed once with: python -m playwright install chromium")


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


# Controls that open Garmin's activity-list overflow / settings menu, which is
# where "Export to CSV" lives. Garmin hides the export item until this is opened,
# so wait_for(state="visible") on the export text alone times out forever.
_MENU_TRIGGER_LABELS = [
    "More", "Options", "Settings", "Actions", "Menu",
    "Περισσότερα", "Επιλογές", "Ρυθμίσεις",   # Greek
]


def _try_open_export_menu(page) -> None:
    """Best-effort: click the overflow/settings control so a hidden Export item
    becomes visible. Safe to call even when the button is already on screen."""
    for label in _MENU_TRIGGER_LABELS:
        try:
            loc = page.get_by_role("button", name=label)
            if loc.count() and loc.first.is_visible():
                loc.first.click(timeout=2_000)
                page.wait_for_timeout(400)
                logger.info("Opened activity menu via %r", label)
                return
        except Exception:  # noqa: BLE001 — never let the probe break the sync
            continue


def _find_export_locator(page):
    """Return a locator for the Export CSV item if one exists in the DOM, else
    None. Matches on the known locale labels, then any element mentioning CSV."""
    for label in _EXPORT_LABELS:
        loc = page.get_by_text(label, exact=True)
        if loc.count():
            return loc.first
    loc = page.locator("button, a, [role='menuitem']").filter(has_text="CSV")
    if loc.count():
        return loc.first
    return None


def _wait_for_export_button(page, timeout: int = 45_000):
    """Wait for the Export CSV control to become clickable, then return it.

    Garmin puts "Export to CSV" inside an overflow/settings menu that is hidden
    until opened, so we (1) look for a visible export item directly, and if none
    is visible, (2) open the menu and look again. Returns None if nothing
    clickable appears within the timeout.
    """
    deadline = timeout
    step = max(3_000, timeout // 6)
    waited = 0
    while waited < deadline:
        loc = _find_export_locator(page)
        if loc is not None:
            try:
                loc.wait_for(state="visible", timeout=1_000)
                logger.info("Export button visible.")
                return loc
            except PWTimeoutError:
                # Exists in the DOM but hidden — it's behind a closed menu.
                _try_open_export_menu(page)
                loc = _find_export_locator(page)
                if loc is not None:
                    try:
                        loc.wait_for(state="visible", timeout=2_000)
                        logger.info("Export button visible after opening menu.")
                        return loc
                    except PWTimeoutError:
                        pass
        # Not there yet (list still rendering) — open any menu and wait a bit.
        _try_open_export_menu(page)
        page.wait_for_timeout(step)
        waited += step

    return None
