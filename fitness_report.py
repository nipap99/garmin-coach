"""Complete weekly Fitness Report -> multi-page PDF in the Fitness Report folder (OUTPUT_DIR).

APEX edition — a dark, performance-styled report. Five pages, each built from
the local garmin-coach database with the same infographics as the app:
  1. Overview      — KPI tiles, race countdown, 8-week load, HRV recovery, verdict
  2. Weekly Review — plan vs actual, recovery, nutrition, running progression
  3. This week     — the detailed Mon-Sat plan (warm-up / main / cool-down + gates)
  4. Sleep         — score + stage breakdown, HRV / resting HR, with feedback
  5. Nutrition     — calories burned vs eaten, energy balance, avg macros, feedback

The written plan + feedback come from data/report_ai.json (produced by a Claude
session on your subscription). If that file is missing, a rule-based fallback is
used so the report still generates.

Visual identity (APEX): near-black canvas, one signal-orange accent, cyan for
positive movement and amber for "watch"; condensed IBM Plex for display/data.
Fonts are bundled in assets/fonts (IBM Plex — Greek supported).

Weeks run Monday-Saturday. After writing the PDF, the week's plan_days are saved
to the weekly_plans table so the dashboard's Today card and next week's
plan-vs-actual check can read them. Test render elsewhere (no plan save):
    .venv\Scripts\python.exe fitness_report.py --out <folder>
Manual run:
    .venv\\Scripts\\python.exe fitness_report.py
"""
from __future__ import annotations

import json
import logging
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fpdf import FPDF

from backend import db, metrics, training_load

ROOT = Path(os.path.dirname(os.path.abspath(__file__)))
AI_PATH = ROOT / "data" / "report_ai.json"
FONT_DIR = ROOT / "assets" / "fonts"
LOG_PATH = ROOT / "data" / "fitness_report.log"
LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s",
                    handlers=[logging.FileHandler(LOG_PATH, encoding="utf-8"), logging.StreamHandler()])
logger = logging.getLogger("fitness_report")
logging.getLogger("fontTools").setLevel(logging.ERROR)   # silence "MERG NOT subset" noise

# Report output folder — moved from Downloads to a dedicated Desktop folder (2026-11-14).
OUTPUT_DIR = Path(r"C:\Users\papad\Desktop\Fitness Report")

# ── APEX palette (dark) ──────────────────────────────────────────────────────
INK    = (14, 17, 22)        # page canvas
PANEL  = (22, 27, 34)        # card surface
PANEL2 = (28, 34, 43)        # raised surface
LINE   = (40, 47, 57)        # hairlines / borders
LINE2  = (52, 60, 71)
TEXT   = (233, 239, 245)     # primary text
MUTED  = (150, 163, 176)     # secondary text
FAINT  = (112, 123, 135)     # labels / axes
ACCENT = (255, 90, 31)       # signal orange — the brand
CYAN   = (57, 224, 200)      # positive movement
WARN   = (255, 176, 32)      # watch
DOWN   = (255, 93, 93)       # regression
VIOLET = (139, 124, 255)     # sleep
STEEL  = (92, 104, 118)      # neutral chart series
DOTCOL = (74, 86, 99)        # faint scatter dots

SESSION_COLORS = {"long": CYAN, "easy": MUTED, "tempo": ACCENT, "intervals": ACCENT,
                  "rest": FAINT}
SESSION_LABEL = {"long": "Long", "easy": "Easy", "tempo": "Quality",
                 "intervals": "Quality", "rest": "Rest"}
GOAL_LABELS = {"5k": "5K", "10k": "10K", "half_marathon": "HALF", "marathon": "MARATHON"}


# ── helpers ──────────────────────────────────────────────────────────────────

def _f(v, d=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


def _fmt_pace(p):
    if not p:
        return "—"
    m = int(p); s = round((p - m) * 60)
    if s == 60:
        m, s = m + 1, 0
    return f"{m}:{s:02d}"


def _fmt_time(sec):
    sec = int(sec or 0)
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _runs_between(runs, a, b):
    out = []
    for r in runs:
        sl = (r.get("start_local") or "")[:10]
        try:
            d = date.fromisoformat(sl)
        except ValueError:
            continue
        if a <= d <= b and r.get("activity_type") == "running":
            out.append(r)
    out.sort(key=lambda r: r["start_local"])
    return out


def _in_range(rows, key, a, b):
    return [r for r in rows if a.isoformat() <= (r.get(key) or "") <= b.isoformat()]


# ── PDF scaffolding ──────────────────────────────────────────────────────────

class ReportPDF(FPDF):
    fam = "Helvetica"      # body        (IBM Plex Sans Condensed)
    disp = "Helvetica"     # display     (condensed, heavier)
    mono = "Courier"       # data/labels (IBM Plex Mono)
    meta_week = ""

    def header(self):
        # full-bleed dark canvas on every page
        self.set_fill_color(*INK)
        self.rect(0, 0, self.w, self.h, "F")
        # slim brand bar
        self.set_xy(self.l_margin, 6.5)
        self.set_font(self.disp, "B", 13)
        self.set_text_color(*TEXT); self.cell(self.get_string_width("A"), 6, "A")
        self.set_text_color(*ACCENT); self.cell(self.get_string_width("P"), 6, "P")
        self.set_text_color(*TEXT); self.cell(self.get_string_width("EX"), 6, "EX")
        self.set_font(self.mono, "", 6.4); self.set_text_color(*FAINT)
        self.set_xy(self.w - self.r_margin - 70, 7.6)
        self.cell(70, 4, f"WEEKLY PERFORMANCE REPORT  ·  {self.meta_week}", align="R")
        self.set_draw_color(*LINE); self.set_line_width(0.3)
        self.line(self.l_margin, 15, self.w - self.r_margin, 15)
        self.set_xy(self.l_margin, 21)

    def footer(self):
        self.set_draw_color(*LINE); self.set_line_width(0.2)
        self.line(self.l_margin, self.h - 13, self.w - self.r_margin, self.h - 13)
        self.set_y(-11)
        self.set_font(self.mono, "", 6.6)
        self.set_text_color(*FAINT)
        self.cell(0, 6, f"APEX  ·  GARMIN COACH  ·  {self.page_no():02d}", align="C")


def _fonts(pdf):
    """Register the bundled IBM Plex families; fall back to Segoe UI for any
    glyph Plex lacks. Returns True if the Plex faces loaded."""
    def p(name):
        return str(FONT_DIR / name)
    try:
        pdf.add_font("APXB", "", p("IBMPlexSansCondensed-Regular.ttf"))
        pdf.add_font("APXB", "B", p("IBMPlexSansCondensed-SemiBold.ttf"))
        pdf.add_font("APXD", "", p("IBMPlexSansCondensed-SemiBold.ttf"))
        pdf.add_font("APXD", "B", p("IBMPlexSansCondensed-Bold.ttf"))
        pdf.add_font("APXM", "", p("IBMPlexMono-Regular.ttf"))
        pdf.add_font("APXM", "B", p("IBMPlexMono-Medium.ttf"))
        pdf.fam, pdf.disp, pdf.mono = "APXB", "APXD", "APXM"
    except Exception:
        logger.warning("Plex fonts missing in %s — using core fonts.", FONT_DIR, exc_info=True)
        pdf.fam = pdf.disp = "Helvetica"; pdf.mono = "Courier"
        return False
    # Greek / symbol fallback
    for reg in (r"C:\Windows\Fonts\segoeui.ttf", r"C:\Windows\Fonts\arial.ttf"):
        if os.path.exists(reg):
            try:
                pdf.add_font("FB", "", reg)
                pdf.set_fallback_fonts(["FB"])
            except Exception:
                pass
            break
    return True


# ── primitive drawing ────────────────────────────────────────────────────────

def _card(pdf, x, y, w, h, fill=PANEL, border=LINE):
    pdf.set_fill_color(*fill)
    if border:
        pdf.set_draw_color(*border); pdf.set_line_width(0.3)
        pdf.rect(x, y, w, h, "DF")
    else:
        pdf.rect(x, y, w, h, "F")


def _page_header(pdf, title, subtitle=""):
    pdf.set_font(pdf.disp, "B", 21)
    pdf.set_text_color(*TEXT)
    pdf.cell(0, 9, title, new_x="LMARGIN", new_y="NEXT")
    y = pdf.get_y() + 1
    pdf.set_draw_color(*ACCENT); pdf.set_line_width(1.1)
    pdf.line(pdf.l_margin, y, pdf.l_margin + 16, y)      # short orange rule
    pdf.ln(2.4)
    if subtitle:
        pdf.set_font(pdf.mono, "", 7.2)
        pdf.set_text_color(*FAINT)
        pdf.cell(0, 5, subtitle.upper(), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(2)


def _eyebrow(pdf, txt):
    """Mono orange section label with a trailing hairline."""
    y = pdf.get_y()
    pdf.set_font(pdf.mono, "B", 7)
    pdf.set_text_color(*ACCENT)
    label = txt.upper()
    pdf.cell(pdf.get_string_width(label) + 3, 5, label)
    lx = pdf.get_x() + 1
    pdf.set_draw_color(*LINE); pdf.set_line_width(0.3)
    pdf.line(lx, y + 2.6, pdf.w - pdf.r_margin, y + 2.6)
    pdf.ln(7)


def _h3(pdf, txt, color=TEXT):
    pdf.set_font(pdf.disp, "B", 12)
    pdf.set_text_color(*color)
    pdf.cell(0, 7, txt, new_x="LMARGIN", new_y="NEXT")
    pdf.ln(0.5)


def _para(pdf, txt, size=9.2, color=MUTED, lh=5.0):
    pdf.set_font(pdf.fam, "", size)
    pdf.set_text_color(*color)
    pdf.multi_cell(0, lh, txt, new_x="LMARGIN", new_y="NEXT")


def _pill(pdf, x, y, label, color):
    pdf.set_font(pdf.mono, "B", 6.6)
    w = pdf.get_string_width(label.upper()) + 6
    # faint tinted background via the accent at low opacity is not available in
    # core PDF fills, so use a solid muted-dark chip with coloured text.
    pdf.set_fill_color(*PANEL2); pdf.set_draw_color(*color); pdf.set_line_width(0.25)
    pdf.rect(x, y, w, 5.2, "DF")
    pdf.set_xy(x, y + 0.5); pdf.set_text_color(*color)
    pdf.cell(w, 4.2, label.upper(), align="C")
    return w


def _tiles(pdf, x, y, w, tiles, cols=None, h=20.0, gap=4.0):
    """Dark metric tiles. Each tile = (value, label, number_color). Wraps into
    rows of `cols`. Returns the bottom y."""
    n = len(tiles); cols = cols or n
    tw = (w - gap * (cols - 1)) / cols
    rows = (n + cols - 1) // cols
    for i, (value, label, col) in enumerate(tiles):
        r, c = divmod(i, cols)
        tx = x + c * (tw + gap); ty = y + r * (h + gap)
        _card(pdf, tx, ty, tw, h)
        pdf.set_xy(tx + 4, ty + 3.4)
        pdf.set_font(pdf.disp, "B", 17); pdf.set_text_color(*col)
        pdf.cell(tw - 8, 8, value)
        pdf.set_xy(tx + 4, ty + h - 6.2)
        pdf.set_font(pdf.mono, "", 6.4); pdf.set_text_color(*FAINT)
        pdf.cell(tw - 8, 4, label.upper())
    return y + rows * h + (rows - 1) * gap


def _feedback_box(pdf, title, txt):
    """A coach-feedback callout on the raised surface with an orange rail."""
    pdf.ln(1)
    x, y = pdf.l_margin, pdf.get_y()
    w = pdf.w - pdf.l_margin - pdf.r_margin
    pdf.set_font(pdf.fam, "", 9.2)
    txt_h = pdf.multi_cell(w - 14, 4.9, txt, dry_run=True, output="HEIGHT",
                           new_x="LMARGIN", new_y="NEXT")
    h = txt_h + 12
    if y + h > pdf.h - pdf.b_margin:
        pdf.add_page(); y = pdf.get_y()
    _card(pdf, x, y, w, h, fill=PANEL2)
    pdf.set_fill_color(*ACCENT); pdf.rect(x, y, 2.3, h, "F")
    pdf.set_xy(x + 7, y + 3)
    pdf.set_font(pdf.mono, "B", 7); pdf.set_text_color(*ACCENT)
    pdf.cell(0, 4, title.upper(), new_x="LMARGIN", new_y="NEXT")
    pdf.set_xy(x + 7, y + 8)
    pdf.set_font(pdf.fam, "", 9.2); pdf.set_text_color(*MUTED)
    pdf.multi_cell(w - 14, 4.9, txt, new_x="LMARGIN", new_y="NEXT")
    pdf.set_y(y + h + 2)


def _keep(pdf, h):
    if pdf.get_y() + h > pdf.h - pdf.b_margin:
        pdf.add_page()


def _legend(pdf, x, y, items):
    pdf.set_font(pdf.mono, "", 6.2); cx = x
    for color, label in items:
        pdf.set_fill_color(*color); pdf.rect(cx, y, 2.6, 2.6, "F")
        pdf.set_xy(cx + 3.4, y - 0.9); pdf.set_text_color(*FAINT)
        label = label.upper()
        pdf.cell(pdf.get_string_width(label) + 2, 4, label)
        cx += 7 + pdf.get_string_width(label)


def _axis(pdf, px, base, pw):
    pdf.set_draw_color(*LINE2); pdf.set_line_width(0.2); pdf.line(px, base, px + pw, base)


def _panel_title(pdf, x, y, txt):
    pdf.set_xy(x, y); pdf.set_font(pdf.mono, "B", 7); pdf.set_text_color(*MUTED)
    pdf.cell(120, 5, txt.upper())


def _chart_card(pdf, x, y, w, h):
    _card(pdf, x, y, w, h, fill=PANEL)


# ── charts ───────────────────────────────────────────────────────────────────

def _load_panel(pdf, x, y, w, h, weeks):
    _chart_card(pdf, x, y, w, h)
    _panel_title(pdf, x + 5, y + 4, "8-week load · run + ride")
    px, py, pw, ph = x + 7, y + 14, w - 14, h - 24
    base = py + ph
    mx = max((wk.get("load") or 0 for wk in weeks), default=0) or 1
    slot = pw / max(len(weeks), 1); bw = slot * 0.56
    for i, wk in enumerate(weeks):
        cx = px + i * slot + (slot - bw) / 2
        rh = (wk.get("run_load") or 0) / mx * ph
        dh = (wk.get("ride_load") or 0) / mx * ph
        pdf.set_fill_color(*CYAN); pdf.rect(cx, base - rh, bw, rh, "F")
        pdf.set_fill_color(*STEEL); pdf.rect(cx, base - rh - dh, bw, dh, "F")
        if i % 2 == 0:
            pdf.set_xy(px + i * slot, base + 1); pdf.set_font(pdf.mono, "", 5.2)
            pdf.set_text_color(*FAINT); pdf.cell(slot, 3, wk["week_start"][5:], align="C")
    _axis(pdf, px, base, pw)
    _legend(pdf, px, y + h - 4.5, [(CYAN, "Run"), (STEEL, "Ride")])


def _line_panel(pdf, x, y, w, h, title, series, color, unit="", fmt="{:.0f}"):
    _chart_card(pdf, x, y, w, h)
    _panel_title(pdf, x + 5, y + 4, title)
    px, py, pw, ph = x + 7, y + 14, w - 14, h - 24
    base = py + ph
    vals = [(i, v) for i, (_, v) in enumerate(series) if v is not None]
    if not vals:
        pdf.set_xy(px, py + ph / 2); pdf.set_font(pdf.mono, "", 6.5); pdf.set_text_color(*FAINT)
        pdf.cell(pw, 4, "NO DATA", align="C"); return
    lo = min(v for _, v in vals) * 0.9
    hi = max(v for _, v in vals) * 1.08
    span = (hi - lo) or 1
    n = max(len(series) - 1, 1); slot = pw / n

    def yp(v):
        return base - (v - lo) / span * ph
    pts = [(px + i * slot, yp(v)) for i, v in vals]
    pdf.set_draw_color(*color); pdf.set_line_width(0.7)
    for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
        pdf.line(x1, y1, x2, y2)
    pdf.set_fill_color(*color)
    for cx, cy in pts:
        pdf.ellipse(cx - 0.8, cy - 0.8, 1.6, 1.6, "F")
    lx, ly = pts[-1]
    pdf.set_xy(lx - 14, ly - 5); pdf.set_font(pdf.mono, "B", 7); pdf.set_text_color(*color)
    pdf.cell(14, 3, (fmt.format(vals[-1][1]) + unit), align="R")
    _axis(pdf, px, base, pw)


def _grouped_bars(pdf, x, y, w, h, title, labels, seriesA, seriesB, colA, colB, legendA, legendB):
    _chart_card(pdf, x, y, w, h)
    _panel_title(pdf, x + 5, y + 4, title)
    px, py, pw, ph = x + 7, y + 14, w - 14, h - 25
    base = py + ph
    mx = max(max(seriesA, default=0), max(seriesB, default=0)) or 1
    n = max(len(labels), 1); slot = pw / n; bw = slot * 0.32
    for i in range(len(labels)):
        gx = px + i * slot + (slot - 2 * bw - 1) / 2
        ha = (seriesA[i] or 0) / mx * ph
        hb = (seriesB[i] or 0) / mx * ph
        pdf.set_fill_color(*colA); pdf.rect(gx, base - ha, bw, ha, "F")
        pdf.set_fill_color(*colB); pdf.rect(gx + bw + 1, base - hb, bw, hb, "F")
        pdf.set_xy(px + i * slot, base + 1); pdf.set_font(pdf.mono, "", 5.2)
        pdf.set_text_color(*FAINT); pdf.cell(slot, 3, labels[i], align="C")
    _axis(pdf, px, base, pw)
    _legend(pdf, px, y + h - 4.5, [(colA, legendA), (colB, legendB)])


def _stage_bars(pdf, x, y, w, h, title, nights):
    _chart_card(pdf, x, y, w, h)
    _panel_title(pdf, x + 5, y + 4, title)
    px, py, pw, ph = x + 7, y + 14, w - 14, h - 25
    base = py + ph
    totals = [(_f(n.get("deep_min")) + _f(n.get("light_min")) + _f(n.get("rem_min"))) for n in nights]
    mx = max(totals, default=0) or 1
    slot = pw / max(len(nights), 1); bw = slot * 0.58
    for i, n in enumerate(nights):
        cx = px + i * slot + (slot - bw) / 2
        deep, light, rem = _f(n.get("deep_min")), _f(n.get("light_min")), _f(n.get("rem_min"))
        yb = base
        for val, col in [(deep, VIOLET), (light, STEEL), (rem, (90, 200, 185))]:
            seg = val / mx * ph
            pdf.set_fill_color(*col); pdf.rect(cx, yb - seg, bw, seg, "F"); yb -= seg
        pdf.set_xy(px + i * slot, base + 1); pdf.set_font(pdf.mono, "", 5.0)
        pdf.set_text_color(*FAINT); pdf.cell(slot, 3, (n.get("night_date") or "")[5:], align="C")
    _axis(pdf, px, base, pw)
    _legend(pdf, px, y + h - 4.5, [(VIOLET, "Deep"), (STEEL, "Light"), ((90, 200, 185), "REM")])


def _macro_bars(pdf, x, y, w, h, title, p, f, c):
    _chart_card(pdf, x, y, w, h)
    _panel_title(pdf, x + 5, y + 4, title)
    px, py, pw, ph = x + 7, y + 16, w - 14, h - 27
    base = py + ph
    vals = [("Protein", p, CYAN), ("Carbs", c, WARN), ("Fat", f, STEEL)]
    mx = max(v for _, v, _ in vals) or 1
    slot = pw / 3; bw = slot * 0.46
    for i, (name, val, col) in enumerate(vals):
        cx = px + i * slot + (slot - bw) / 2
        bh = val / mx * ph
        pdf.set_fill_color(*col); pdf.rect(cx, base - bh, bw, bh, "F")
        pdf.set_xy(px + i * slot, base - bh - 4.2); pdf.set_font(pdf.disp, "B", 8.5)
        pdf.set_text_color(*TEXT); pdf.cell(slot, 3, f"{val:.0f} g", align="C")
        pdf.set_xy(px + i * slot, base + 1.5); pdf.set_font(pdf.mono, "", 6)
        pdf.set_text_color(*FAINT); pdf.cell(slot, 3, name.upper(), align="C")
    _axis(pdf, px, base, pw)


def _efficiency_scatter(pdf, x, y, w, h, runs, window_days=None):
    """Aerobic-efficiency trend, matching the app: each run is a faint dot, a
    bold orange line is the weekly average, over the full run history.
    Efficiency = speed (km/h) / avg HR x 100 — higher = fitter. Runs < 3 km
    (and non-running activities) are excluded."""
    import math
    from collections import OrderedDict

    pts = []
    for r in runs:
        if r.get("activity_type") != "running":
            continue
        km, mins, hr = r.get("distance_km") or 0, r.get("duration_min") or 0, r.get("avg_hr")
        if km < 3 or not hr or mins <= 0:
            continue
        try:
            dt = date.fromisoformat((r.get("start_local") or "")[:10])
        except ValueError:
            continue
        ef = (km / (mins / 60.0)) / hr * 100.0
        if not (3.0 <= ef <= 11.0):     # drop GPS/HR glitches (e.g. a 3:18/km @ HR 129)
            continue
        pts.append((dt, ef))
    if len(pts) < 3:
        pdf.set_xy(x, y + h / 2); pdf.set_font(pdf.mono, "", 7); pdf.set_text_color(*FAINT)
        pdf.cell(w, 4, "NOT ENOUGH RUNS FOR A TREND YET.", align="C")
        return
    pts.sort(key=lambda p: p[0])

    # Optionally show only the last `window_days` (the report uses ~12 months for
    # readability); the caller still holds the full history for other uses.
    if window_days and pts:
        cut = pts[-1][0] - timedelta(days=window_days)
        recent = [p for p in pts if p[0] >= cut]
        if len(recent) >= 5:
            pts = recent

    px, py, pw, ph = x + 12, y + 3, w - 15, h - 12
    base = py + ph
    d0, d1 = pts[0][0], pts[-1][0]
    span = max((d1 - d0).days, 1)
    efs = [e for _, e in pts]
    lo, hi = min(efs), max(efs)
    pad = (hi - lo) * 0.08 or 0.5
    lo, hi = lo - pad, hi + pad

    def X(dt):
        return px + (dt - d0).days / span * pw

    def Y(e):
        return base - (e - lo) / (hi - lo) * ph

    pdf.set_font(pdf.mono, "", 5.8)
    for gy in range(int(math.ceil(lo)), int(math.floor(hi)) + 1):
        yy = Y(gy)
        pdf.set_draw_color(*LINE); pdf.set_line_width(0.15); pdf.line(px, yy, px + pw, yy)
        pdf.set_xy(x - 1, yy - 2); pdf.set_text_color(*FAINT); pdf.cell(11, 4, str(gy), align="R")

    pdf.set_fill_color(*DOTCOL)
    for dt, e in pts:
        pdf.ellipse(X(dt) - 0.7, Y(e) - 0.7, 1.4, 1.4, "F")

    wk = OrderedDict()
    for dt, e in pts:
        wk.setdefault(dt.isocalendar()[:2], []).append((dt, e))
    line = []
    for _key, items in wk.items():
        avg = sum(e for _, e in items) / len(items)
        mid = items[len(items) // 2][0]
        line.append((mid, avg))
    line.sort(key=lambda p: p[0])
    pdf.set_draw_color(*ACCENT); pdf.set_line_width(0.8)
    for (a_d, a_e), (b_d, b_e) in zip(line, line[1:]):
        pdf.line(X(a_d), Y(a_e), X(b_d), Y(b_e))
    lx, le = line[-1]
    pdf.set_fill_color(*ACCENT)
    pdf.ellipse(X(lx) - 1.3, Y(le) - 1.3, 2.6, 2.6, "F")
    pdf.set_xy(X(lx) - 15, Y(le) - 5); pdf.set_font(pdf.mono, "B", 7); pdf.set_text_color(*ACCENT)
    pdf.cell(14, 3, f"{le:.1f}", align="R")

    fmt = "%d %b" if span < 400 else "%b %y"
    pdf.set_font(pdf.mono, "", 5.8); pdf.set_text_color(*FAINT)
    for i in range(7):
        dd = d0 + timedelta(days=int(span * i / 6))
        pdf.set_xy(X(dd) - 9, base + 1.4); pdf.cell(18, 3, dd.strftime(fmt).upper(), align="C")
    pdf.set_draw_color(*LINE2); pdf.set_line_width(0.2); pdf.line(px, base, px + pw, base)


# ── data ─────────────────────────────────────────────────────────────────────

def gather():
    today = date.today()
    last_sat = today - timedelta(days=(today.weekday() - 5) % 7)
    rev_start = last_sat - timedelta(days=5)
    rev_end = last_sat
    plan_start = last_sat + timedelta(days=2)

    # Full history for the aerobic-efficiency trend. get_recent_activities caps at
    # LIMIT 200 by default, which silently truncated the trend to the last ~200
    # runs; pass a high limit + wide window so it reaches the record's start
    # (data begins May 2024). The week views filter down from this.
    all_runs = db.get_recent_activities(days=1200, limit=10000)
    week_runs = _runs_between(all_runs, rev_start, rev_end)
    load = training_load.compute(weeks=8)
    sleep = db.get_sleep_nights(limit=40)
    week_sleep = _in_range(sleep, "night_date", rev_start, rev_end)
    week_sleep.sort(key=lambda s: s.get("night_date") or "")
    cals = db.get_calories_days(limit=40)
    week_cals = _in_range(cals, "day_date", rev_start, rev_end)
    week_cals.sort(key=lambda c: c.get("day_date") or "")
    nutr = db.get_nutrition_days(limit=40)
    week_nutr = _in_range(nutr, "log_date", rev_start, rev_end)
    week_nutr.sort(key=lambda n: n.get("log_date") or "")
    goals = [g for g in db.list_goals(status="active")
             if g.get("target_date") and g["target_date"] >= today.isoformat()]
    goals.sort(key=lambda g: g["target_date"])

    week = metrics.week_summary(rev_start.isoformat())

    return {"today": today, "rev_start": rev_start, "rev_end": rev_end, "plan_start": plan_start,
            "week": week,
            "week_runs": week_runs, "load": load, "week_sleep": week_sleep,
            "week_cals": week_cals, "week_nutr": week_nutr,
            "goal": goals[0] if goals else None, "goals": goals, "all_runs": all_runs}


def load_ai():
    try:
        return json.loads(AI_PATH.read_text(encoding="utf-8"))
    except Exception:
        logger.warning("No AI content at %s — using fallback text.", AI_PATH)
        return {}


# ── pages ────────────────────────────────────────────────────────────────────

def _latest_vo2(runs):
    for r in sorted(runs, key=lambda r: r.get("start_local") or "", reverse=True):
        if r.get("vo2max"):
            return int(_f(r.get("vo2max")))
    return None


def _goal_band(pdf, x, y, w, goals, today):
    """Up to two race-countdown cards side by side."""
    gs = goals[:2]
    if not gs:
        return y
    gap = 4.0; n = len(gs); cw = (w - gap * (n - 1)) / n
    for i, g in enumerate(gs):
        gx = x + i * (cw + gap)
        _card(pdf, gx, y, cw, 17, fill=PANEL2)
        pdf.set_fill_color(*ACCENT); pdf.rect(gx, y, 2.0, 17, "F")
        try:
            gd = date.fromisoformat(g["target_date"]); days = max(0, (gd - today).days)
        except Exception:
            gd = None; days = 0
        race = GOAL_LABELS.get(g.get("distance"), str(g.get("distance", "")).upper())
        when = gd.strftime("%d %b").upper() if gd else ""
        pdf.set_xy(gx + 6, y + 3); pdf.set_font(pdf.mono, "", 6.2); pdf.set_text_color(*FAINT)
        pdf.cell(cw - 40, 4, f"{race} · {when}")
        pdf.set_xy(gx + 6, y + 7.2); pdf.set_font(pdf.disp, "B", 15); pdf.set_text_color(*TEXT)
        pdf.cell(cw - 40, 7, _fmt_time(g.get("target_time_seconds")))
        pdf.set_xy(gx + cw - 34, y + 4.2); pdf.set_font(pdf.disp, "B", 16); pdf.set_text_color(*ACCENT)
        pdf.cell(28, 7, str(days), align="R")
        pdf.set_xy(gx + cw - 34, y + 11.4); pdf.set_font(pdf.mono, "", 5.8); pdf.set_text_color(*FAINT)
        pdf.cell(28, 3, "DAYS OUT", align="R")
    return y + 17


def page_overview(pdf, d, ai):
    sub = f"Week of {d['rev_start']:%d %b} – {d['rev_end']:%d %b %Y}  ·  Mon–Sat"
    _page_header(pdf, "Weekly Performance Report", sub)

    runs = d["week_runs"]
    tot_km = sum(_f(r.get("distance_km")) for r in runs)
    a = d["load"]["acwr"]
    sl = d["week_sleep"]
    avg_sleep = round(sum(_f(s.get("score")) for s in sl) / len(sl)) if sl else None
    hrvs = [_f(s.get("hrv_ms")) for s in sl if s.get("hrv_ms")]
    avg_hrv = round(sum(hrvs) / len(hrvs)) if hrvs else None
    vo2 = _latest_vo2(d["all_runs"])
    burned = [_f(c.get("total_cal")) for c in d["week_cals"] if _f(c.get("total_cal")) > 1200]
    eaten = [_f(n.get("kcal")) for n in d["week_nutr"]]
    net = round((sum(eaten) / len(eaten) if eaten else 0) - (sum(burned) / len(burned) if burned else 0))

    sc = {"optimal": CYAN, "low": WARN, "high": DOWN, "caution": WARN}.get(a.get("status"), TEXT)
    CW = pdf.w - pdf.l_margin - pdf.r_margin
    y = _tiles(pdf, pdf.l_margin, pdf.get_y(), CW, [
        (f"{tot_km:.0f} km", f"{len(runs)} runs", ACCENT),
        (str(vo2) if vo2 else "—", "VO2 max", TEXT),
        (f"{avg_hrv} ms" if avg_hrv else "—", "Avg HRV", CYAN),
        (f"{a.get('value')}", f"ACWR · {a.get('status')}", sc),
        (str(avg_sleep) if avg_sleep else "—", "Avg sleep", VIOLET),
        (f"{net:+,}", "Net kcal / day", CYAN if net >= 0 else WARN),
    ], cols=3, h=20)
    pdf.set_y(y + 6)

    _eyebrow(pdf, "Race countdown")
    y = _goal_band(pdf, pdf.l_margin, pdf.get_y(), CW, d.get("goals") or [], d["today"])
    pdf.set_y(y + 6)

    _eyebrow(pdf, "Training load & recovery")
    ct = pdf.get_y(); ch = 46; half = (CW - 8) / 2
    hist = [w for w in d["load"]["weeks"] if not w.get("partial")] or d["load"]["weeks"]
    _load_panel(pdf, pdf.l_margin, ct, half, ch, hist)
    hrv_series = [(w["week_start"][5:], w.get("hrv")) for w in hist]
    _line_panel(pdf, pdf.l_margin + half + 8, ct, half, ch,
                "Overnight HRV · ms", hrv_series, CYAN, unit=" ms")
    pdf.set_y(ct + ch + 7)

    _eyebrow(pdf, "The week in a sentence")
    _para(pdf, ai.get("overall_verdict") or _fallback_verdict(d, a, net), size=9.4, color=TEXT)


def _plan_vs_actual(pdf, wk, CW):
    """Planned vs done, one row per day, from metrics.week_summary()."""
    days = wk.get("days") or []
    if not days:
        return
    _eyebrow(pdf, "00 · Plan vs actual")
    x0, y = pdf.l_margin, pdf.get_y()
    rh = 5.2
    _card(pdf, x0, y, CW, rh * len(days) + 7, fill=PANEL)
    cols = (x0 + 4, x0 + 18, x0 + 62)
    pdf.set_font(pdf.mono, "", 5.8); pdf.set_text_color(*FAINT)
    pdf.set_xy(cols[0], y + 1.5); pdf.cell(14, 3.5, "DAY")
    pdf.set_xy(cols[1], y + 1.5); pdf.cell(44, 3.5, "PLANNED")
    pdf.set_xy(cols[2], y + 1.5); pdf.cell(100, 3.5, "DONE")
    yy = y + 6
    for dd in days:
        ok = dd.get("matches_plan")
        mark, col = ("OK", CYAN) if ok else (("MISS", WARN) if ok is False else ("", FAINT))
        sess = dd.get("sessions") or []
        if sess:
            main = max(sess, key=lambda s: (s["hard"], s["km"]))
            done = f"{main['type']} {sum(s['km'] for s in sess):.1f} km"
            if main.get("work"):
                done += f" - {main['work']}"
        else:
            done = "rest"
        pdf.set_font(pdf.mono, "B", 6.6); pdf.set_text_color(*TEXT)
        pdf.set_xy(cols[0], yy); pdf.cell(14, rh, dd["weekday"].upper())
        pdf.set_font(pdf.mono, "", 6.6); pdf.set_text_color(*MUTED)
        pdf.set_xy(cols[1], yy); pdf.cell(44, rh, dd["planned"]["type"])
        pdf.set_text_color(*TEXT)
        pdf.set_xy(cols[2], yy); pdf.cell(CW - 62 - 20, rh, done[:95])
        pdf.set_font(pdf.mono, "B", 6.6); pdf.set_text_color(*col)
        pdf.set_xy(x0 + CW - 18, yy); pdf.cell(14, rh, mark, align="R")
        yy += rh
    pdf.set_y(y + rh * len(days) + 9)
    summary = (f"{wk.get('running_km', 0)} km - hard days: {', '.join(wk.get('hard_days') or []) or 'none'}"
               f" - adherence {wk.get('plan_adherence') or '-'} (plan: {wk.get('plan_source')})")
    flags = wk.get("flags") or []
    _para(pdf, summary + (" - FLAGS: " + "; ".join(flags) if flags else ""), size=8.4)
    pdf.ln(1.5)


def page_review(pdf, d, ai):
    _page_header(pdf, "Weekly Review",
                 f"{d['rev_start']:%d %b} – {d['rev_end']:%d %b}: recovery · nutrition · progression")
    CW = pdf.w - pdf.l_margin - pdf.r_margin
    sl = d["week_sleep"]

    _plan_vs_actual(pdf, d.get("week") or {}, CW)

    _eyebrow(pdf, "01 · Recovery")
    hrvs = [_f(s.get("hrv_ms")) for s in sl if s.get("hrv_ms")]
    rhrs = [_f(s.get("resting_hr")) for s in sl if s.get("resting_hr")]
    scores = [_f(s.get("score")) for s in sl if s.get("score")]
    avg_hrv = round(sum(hrvs) / len(hrvs)) if hrvs else 0
    end_hrv = round(hrvs[-1]) if hrvs else 0
    avg_rhr = round(sum(rhrs) / len(rhrs)) if rhrs else 0
    avg_sc = round(sum(scores) / len(scores)) if scores else 0
    y = _tiles(pdf, pdf.l_margin, pdf.get_y(), CW, [
        (f"{avg_hrv} ms", "Avg HRV", CYAN), (f"{end_hrv} ms", "Latest HRV", CYAN),
        (f"{avg_rhr}", "Avg resting HR", TEXT), (str(avg_sc), "Avg sleep", VIOLET),
    ], h=17)
    pdf.set_y(y + 2.5)
    _para(pdf, ai.get("recovery_comment") or "", size=9)
    pdf.ln(2)

    _eyebrow(pdf, "02 · Nutrition")
    nutr = d["week_nutr"]
    eaten = [_f(n.get("kcal")) for n in nutr]
    burned = [_f(c.get("total_cal")) for c in d["week_cals"] if _f(c.get("total_cal")) > 1200]
    avg_e = round(sum(eaten) / len(eaten)) if eaten else 0
    avg_b = round(sum(burned) / len(burned)) if burned else 0
    carbs = round(sum(_f(n.get("carbs_g")) for n in nutr) / len(nutr)) if nutr else 0
    prot = round(sum(_f(n.get("protein_g")) for n in nutr) / len(nutr)) if nutr else 0
    net = avg_e - avg_b
    y = _tiles(pdf, pdf.l_margin, pdf.get_y(), CW, [
        (f"{net:+,}", "Net / day", DOWN if net < 0 else CYAN),
        (f"{carbs}g", "Avg carbs", WARN), (f"{prot}g", "Avg protein", CYAN),
        (f"{avg_e:,}", "Avg eaten", ACCENT),
    ], h=17)
    pdf.set_y(y + 2.5)
    _para(pdf, ai.get("nutrition_comment") or "", size=9)
    pdf.ln(2)

    _eyebrow(pdf, "03 · Running progression")
    _card(pdf, pdf.l_margin, pdf.get_y(), CW, 58, fill=PANEL)
    inner = pdf.get_y()
    pdf.set_xy(pdf.l_margin + 5, inner + 4)
    pdf.set_font(pdf.mono, "B", 7); pdf.set_text_color(*MUTED)
    pdf.cell(80, 4, "AEROBIC EFFICIENCY TREND")
    pdf.set_xy(pdf.l_margin + 5, inner + 8.5)
    pdf.set_font(pdf.mono, "", 5.8); pdf.set_text_color(*FAINT)
    pdf.cell(0, 3.5, "SPEED / HR x 100 — HIGHER = FITTER. DOT = ONE RUN, LINE = WEEKLY AVG. RUNS < 3 KM EXCLUDED.")
    _efficiency_scatter(pdf, pdf.l_margin + 2, inner + 13, CW - 4, 42, d["all_runs"],
                        window_days=365)   # report shows the last ~12 months
    pdf.set_y(inner + 58 + 3)

    # Keep the written analysis together: if the commentary + goal outlook won't
    # fit in the space left, move the whole block to the next page so it reads as
    # a full progression write-up rather than orphaning onto a near-empty page.
    comm = ai.get("progression_commentary") or ""
    outlook = ai.get("goal_outlook") or ""
    pdf.set_font(pdf.fam, "", 9.1)
    need = (pdf.multi_cell(0, 5.0, comm, dry_run=True, output="HEIGHT",
                           new_x="LMARGIN", new_y="NEXT") if comm else 0)
    if outlook:
        need += 10 + pdf.multi_cell(0, 5.0, outlook, dry_run=True, output="HEIGHT",
                                    new_x="LMARGIN", new_y="NEXT")
    if comm and pdf.get_y() + need > pdf.h - pdf.b_margin:
        pdf.add_page()
    if comm:
        _para(pdf, comm, size=9.1)
    if outlook:
        pdf.ln(2)
        _h3(pdf, "Toward your goals", ACCENT)
        _para(pdf, outlook, size=9.1)


def page_plan(pdf, d, ai):
    plan_end = d["plan_start"] + timedelta(days=6)
    _page_header(pdf, "This Week's Plan",
                 f"{d['plan_start']:%a %d %b} – {plan_end:%a %d %b}  ·  part of this month's block")
    if ai.get("plan_focus"):
        x, y = pdf.l_margin, pdf.get_y()
        w = pdf.w - pdf.l_margin - pdf.r_margin
        pdf.set_font(pdf.fam, "", 9.2)
        th = pdf.multi_cell(w - 14, 4.8, ai["plan_focus"], dry_run=True, output="HEIGHT",
                            new_x="LMARGIN", new_y="NEXT")
        _card(pdf, x, y, w, th + 8, fill=PANEL2)
        pdf.set_fill_color(*ACCENT); pdf.rect(x, y, 2.3, th + 8, "F")
        pdf.set_xy(x + 7, y + 4); pdf.set_text_color(*MUTED); pdf.set_font(pdf.fam, "", 9.2)
        pdf.multi_cell(w - 14, 4.8, ai["plan_focus"], new_x="LMARGIN", new_y="NEXT")
        pdf.set_y(y + th + 8 + 3)

    days = ai.get("plan_days") or _fallback_plan(d)
    CW = pdf.w - pdf.l_margin - pdf.r_margin
    for day in days:
        _plan_day_block(pdf, day, CW)
    pdf.ln(1)


def _plan_day_block(pdf, day, w):
    typ = day.get("type", "easy")
    col = SESSION_COLORS.get(typ, MUTED)
    head_h = 9.0

    rows = []
    for lbl, key, lblcol, size, lh in [
        ("WARM-UP", "warmup", CYAN, 8.8, 4.5),
        ("MAIN", "main", col, 8.8, 4.5),
        ("COOL-DOWN", "cooldown", CYAN, 8.8, 4.5),
        ("GATE", "gate", WARN, 8.4, 4.4),
    ]:
        txt = day.get(key)
        if not txt:
            continue
        pdf.set_font(pdf.fam, "", size)
        h = pdf.multi_cell(w - 30, lh, txt, dry_run=True, output="HEIGHT",
                           new_x="LMARGIN", new_y="NEXT")
        rows.append((lbl, txt, lblcol, size, lh, h))
    block_h = head_h + 1.5 + sum(r[5] for r in rows) + 3.5

    if pdf.get_y() + block_h > pdf.h - pdf.b_margin:
        pdf.add_page()

    x, y = pdf.l_margin, pdf.get_y()
    _card(pdf, x, y, w, block_h, fill=PANEL)
    pdf.set_fill_color(*PANEL2); pdf.rect(x, y, w, head_h, "F")
    pdf.set_fill_color(*col); pdf.rect(x, y, 2.6, head_h, "F")
    pdf.set_xy(x + 5, y + 1.8)
    pdf.set_font(pdf.disp, "B", 10); pdf.set_text_color(*TEXT)
    try:
        dt = date.fromisoformat(day["date"]).strftime("%a %d %b").upper()
    except Exception:
        dt = (day.get("day", "") or "").upper()
    pdf.cell(32, 5, dt)
    bw = _pill(pdf, x + 34, y + 1.9, SESSION_LABEL.get(typ, "Run"), col)
    pdf.set_xy(x + 34 + bw + 4, y + 1.8)
    pdf.set_font(pdf.disp, "B", 10); pdf.set_text_color(*TEXT)
    pdf.cell(w - (34 + bw + 4) - 34, 5, day.get("title", ""))
    pdf.set_font(pdf.mono, "B", 8); pdf.set_text_color(*col)
    pdf.set_xy(x + w - 36, y + 2.2); pdf.cell(33, 5, (day.get("target", "") or "").upper(), align="R")
    pdf.set_y(y + head_h + 1.5)

    for lbl, txt, lblcol, size, lh, _h in rows:
        yy = pdf.get_y()
        pdf.set_font(pdf.mono, "B", 6.6); pdf.set_text_color(*lblcol)
        pdf.set_xy(x + 5, yy + 0.4); pdf.cell(22, lh, lbl)
        pdf.set_font(pdf.fam, "", size)
        pdf.set_text_color(*(MUTED if lbl == "GATE" else TEXT))
        pdf.set_xy(x + 26, yy)
        pdf.multi_cell(w - 30, lh, txt, new_x="LMARGIN", new_y="NEXT")
    pdf.set_y(y + block_h + 2.5)


def page_sleep(pdf, d, ai):
    _page_header(pdf, "Sleep & Recovery", f"{d['rev_start']:%d %b} – {d['rev_end']:%d %b}")
    sl = d["week_sleep"]
    if sl:
        avg_score = round(sum(_f(s.get("score")) for s in sl) / len(sl))
        avg_dur = sum(_f(s.get("duration_min")) for s in sl) / len(sl)
        avg_hrv = round(sum(_f(s.get("hrv_ms")) for s in sl) / len(sl))
        avg_rhr = round(sum(_f(s.get("resting_hr")) for s in sl) / len(sl))
    else:
        avg_score = avg_dur = avg_hrv = avg_rhr = 0
    _tiles(pdf, pdf.l_margin, pdf.get_y(), pdf.w - pdf.l_margin - pdf.r_margin, [
        (str(avg_score), "Avg score", VIOLET),
        (f"{int(avg_dur // 60)}h {int(avg_dur % 60):02d}m", "Avg duration", TEXT),
        (f"{avg_hrv} ms", "Avg HRV", CYAN),
        (f"{avg_rhr}", "Avg resting HR", TEXT),
    ], h=20)
    pdf.set_y(pdf.get_y() + 24)

    CW = pdf.w - pdf.l_margin - pdf.r_margin
    ct = pdf.get_y(); ch = 48; half = (CW - 8) / 2
    _stage_bars(pdf, pdf.l_margin, ct, half, ch, "Sleep stages / night · min", sl)
    hrv_series = [((s.get("night_date") or "")[5:], _f(s.get("hrv_ms")) or None) for s in sl]
    _line_panel(pdf, pdf.l_margin + half + 8, ct, half, ch, "Overnight HRV · ms", hrv_series, CYAN, unit="")
    pdf.set_y(ct + ch + 5)

    _feedback_box(pdf, "Coach feedback — sleep",
                  ai.get("sleep_feedback") or "Protect 8 hours; consistency drives recovery.")


def page_nutrition(pdf, d, ai):
    _page_header(pdf, "Calories & Nutrition", f"{d['rev_start']:%d %b} – {d['rev_end']:%d %b}")
    cals = d["week_cals"]; nutr = d["week_nutr"]
    burned = [_f(c.get("total_cal")) for c in cals]
    eaten = [_f(n.get("kcal")) for n in nutr]
    burned_full = [b for b in burned if b > 1200]
    avg_b = round(sum(burned_full) / len(burned_full)) if burned_full else 0
    avg_e = round(sum(eaten) / len(eaten)) if eaten else 0
    p = sum(_f(n.get("protein_g")) for n in nutr) / len(nutr) if nutr else 0
    f = sum(_f(n.get("fat_g")) for n in nutr) / len(nutr) if nutr else 0
    c = sum(_f(n.get("carbs_g")) for n in nutr) / len(nutr) if nutr else 0

    net = avg_e - avg_b
    _tiles(pdf, pdf.l_margin, pdf.get_y(), pdf.w - pdf.l_margin - pdf.r_margin, [
        (f"{avg_b:,}", "Avg burned / day", WARN),
        (f"{avg_e:,}", "Avg eaten / day", ACCENT),
        (f"{net:+,}", "Net / day", CYAN if net >= 0 else DOWN),
        (f"{p:.0f}g", "Avg protein", CYAN),
    ], h=20)
    pdf.set_y(pdf.get_y() + 24)

    CW = pdf.w - pdf.l_margin - pdf.r_margin
    ct = pdf.get_y(); ch = 48; half = (CW - 8) / 2
    days_lbl = [(c.get("day_date") or "")[5:] for c in cals]
    eaten_by_date = {(n.get("log_date") or ""): _f(n.get("kcal")) for n in nutr}
    eaten_aligned = [eaten_by_date.get(c.get("day_date"), 0) for c in cals]
    _grouped_bars(pdf, pdf.l_margin, ct, half, ch, "Burned vs eaten",
                  days_lbl, burned, eaten_aligned, STEEL, ACCENT, "Burned", "Eaten")
    _macro_bars(pdf, pdf.l_margin + half + 8, ct, half, ch, "Average daily macros", p, f, c)
    pdf.set_y(ct + ch + 5)

    _feedback_box(pdf, "Coach feedback — nutrition",
                  ai.get("nutrition_feedback") or "Match intake to training load; prioritise carbs around hard sessions.")


# ── fallbacks (used only when AI content is missing) ─────────────────────────

def _fallback_verdict(d, a, net):
    return (f"ACWR {a.get('value')} ({a.get('status')}). Net energy balance {net:+} kcal/day. "
            "Keep load in the 0.8-1.3 zone and match intake to training.")


def _fallback_plan(d):
    base = [("easy", "Easy recovery", "6-8 km easy"),
            ("intervals", "Track — intervals", "5k-pace reps"), ("easy", "Easy aerobic", "8 km easy"),
            ("intervals", "Track — threshold", "10k-pace reps"), ("easy", "Cross-train", "active recovery"),
            ("long", "Long run", "16-18 km easy")]
    out = []
    for i, (typ, title, main) in enumerate(base):
        dd = d["plan_start"] + timedelta(days=i)
        out.append({"day": dd.strftime("%A"), "date": dd.isoformat(), "type": typ,
                    "title": title, "main": main, "target": main.split(" ", 1)[0] if main else "Rest",
                    "warmup": "", "cooldown": "", "gate": ""})
    return out


def _save_plan(ai, d):
    """Store this week's plan so the Today card + next week's review can use it."""
    days = ai.get("plan_days") or []
    if not days:
        return
    week_start = ai.get("plan_week_start") or d["plan_start"].isoformat()
    for p in db.list_weekly_plans(limit=50):
        pj = p.get("plan_json") or {}
        if p.get("week_start") == week_start and pj.get("source") == "weekly-report":
            db.update_plan_status(p["id"], "archived")
    pid = db.create_weekly_plan(week_start, {"source": "weekly-report", "focus": ai.get("plan_focus"),
                                             "days": days})
    db.update_plan_status(pid, "accepted")
    logger.info("Saved plan for week of %s (weekly_plans id %s)", week_start, pid)


def main():
    out_dir, save = OUTPUT_DIR, True
    if "--out" in sys.argv:
        out_dir, save = Path(sys.argv[sys.argv.index("--out") + 1]), False
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        d = gather(); ai = load_ai()
        _validate_ai(ai)
        pdf = ReportPDF()
        _fonts(pdf)
        pdf.set_margins(14, 21, 14)
        pdf.set_auto_page_break(True, margin=16)
        pdf.meta_week = f"{d['rev_start']:%d}–{d['rev_end']:%d %b %Y}".upper()
        builders = (page_overview, page_review, page_plan, page_sleep, page_nutrition)
        for builder in builders:
            pdf.add_page()
            builder(pdf, d, ai)
        _check_pages(pdf, len(builders))
        out = out_dir / f"fitness report - week of {d['rev_start']:%Y-%m-%d}.pdf"
        pdf.output(str(out))
        logger.info("Wrote fitness report -> %s (%d pages)", out, pdf.pages_count)
        if save:
            _save_plan(ai, d)
        print(out)
        return 0
    except Exception:
        logger.exception("Fitness report failed")
        return 1


def _validate_ai(ai):
    """Sanity-check the AI content so a malformed report_ai.json can't silently
    produce a sparse report. Logs warnings (never fatal — the renderer falls back)."""
    required = ["overall_verdict", "recovery_comment", "nutrition_comment",
                "progression_commentary", "goal_outlook", "plan_focus", "plan_days",
                "training_feedback", "sleep_feedback", "nutrition_feedback"]
    missing = [k for k in required if not ai.get(k)]
    if missing:
        logger.warning("report_ai.json missing/empty keys: %s", ", ".join(missing))
    days = ai.get("plan_days") or []
    if len(days) not in (6, 7):
        logger.warning("plan_days has %d entries (expected 6 for Mon–Sat).", len(days))
    for day in days:
        if day.get("type") not in ("rest",) and not (day.get("main") or "").strip():
            logger.warning("plan day %s has an empty MAIN session.", day.get("date"))


def _check_pages(pdf, n_sections):
    pages = pdf.pages_count
    if not (n_sections <= pages <= n_sections + 3):
        logger.warning("Report has %d pages (expected %d-%d) — layout may have "
                       "overflowed; check for near-empty pages.", pages,
                       n_sections, n_sections + 3)


if __name__ == "__main__":
    raise SystemExit(main())
