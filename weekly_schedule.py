"""Weekly running schedule → PDF in Downloads. Runs headless every Sunday 10:00
via Windows Task Scheduler (see scripts/install_weekly_task.ps1).

Reads the local garmin-coach database directly (no API, no network), builds:
  1. a review of the week just finished, and
  2. a plan for the week ahead — shaped by training load / ACWR, recovery and
     the athlete's recent volume,
then writes "running schedule - week of <Sunday>.pdf" to the Downloads folder.

Manual run / test:
    .venv\\Scripts\\python.exe weekly_schedule.py
"""
from __future__ import annotations

import logging
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fpdf import FPDF

from backend import db, training_load

# ── logging (headless runs leave a trail) ────────────────────────────────────
LOG_PATH = Path(os.path.dirname(os.path.abspath(__file__))) / "data" / "weekly_schedule.log"
LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s: %(message)s",
    handlers=[logging.FileHandler(LOG_PATH, encoding="utf-8"), logging.StreamHandler()],
)
logger = logging.getLogger("weekly_schedule")

DOWNLOADS = Path(os.path.expandvars(r"%USERPROFILE%")) / "Downloads"

DAY_NAMES = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]

GOAL_LABELS = {"5k": "5K", "10k": "10K", "half_marathon": "Half Marathon", "marathon": "Marathon"}

# session type → (display colour, short label) — matches the app's chart family
SESSION_COLORS = {
    "long":      ((47, 111, 224),  "Long"),
    "easy":      ((31, 138, 76),   "Easy"),
    "tempo":     ((245, 166, 35),  "Tempo"),
    "intervals": ((224, 82, 82),   "Intervals"),
    "rest":      ((152, 162, 179), "Rest"),
}


def _session_type(label: str) -> str:
    l = label.lower()
    if "long" in l:
        return "long"
    if "tempo" in l:
        return "tempo"
    if "interval" in l:
        return "intervals"
    if "easy" in l or "jog" in l:
        return "easy"
    return "rest"


def _sunday(d: date) -> date:
    """The Sunday on/just before d (weeks start Sunday, matching the watch)."""
    return d - timedelta(days=(d.weekday() + 1) % 7)


def _fmt_pace(p: float | None) -> str:
    if not p:
        return "—"
    m = int(p)
    s = round((p - m) * 60)
    if s == 60:
        m, s = m + 1, 0
    return f"{m}:{s:02d}/km"


# ── data gathering ───────────────────────────────────────────────────────────

def _runs_between(runs: list[dict], start: date, end: date) -> list[dict]:
    out = []
    for r in runs:
        sl = (r.get("start_local") or "")[:10]
        if not sl:
            continue
        try:
            d = date.fromisoformat(sl)
        except ValueError:
            continue
        if start <= d <= end and r.get("activity_type") == "running":
            out.append(r)
    return out


def gather() -> dict:
    today = date.today()
    this_week = _sunday(today)               # current week starts this Sunday
    last_week_start = this_week - timedelta(days=7)
    last_week_end = this_week - timedelta(days=1)

    runs = db.get_recent_activities(days=90)
    last_week_runs = _runs_between(runs, last_week_start, last_week_end)

    # recent typical weekly volume: mean km/week over the last 4 completed weeks
    four_wk = _runs_between(runs, this_week - timedelta(days=28), last_week_end)
    recent_weekly_km = round(sum(r.get("distance_km") or 0 for r in four_wk) / 4, 1)
    recent_longest = round(max((r.get("distance_km") or 0 for r in four_wk), default=0), 1)

    # easy-pace reference = median pace of recent easy-ish runs (>=3 km)
    paces = sorted(r["avg_pace_min_per_km"] for r in runs
                   if r.get("avg_pace_min_per_km") and (r.get("distance_km") or 0) >= 3)
    easy_pace = paces[len(paces) // 2] if paces else None

    load = training_load.compute(weeks=8)

    sleep = db.get_sleep_nights(limit=30)
    last_week_sleep = [s for s in sleep
                       if last_week_start.isoformat() <= (s.get("night_date") or "") <= last_week_end.isoformat()]

    goals = [g for g in db.list_goals(status="active")
             if g.get("target_date") and g["target_date"] >= today.isoformat()]
    goals.sort(key=lambda g: g["target_date"])

    return {
        "today": today, "this_week": this_week,
        "last_week_start": last_week_start, "last_week_end": last_week_end,
        "last_week_runs": last_week_runs,
        "recent_weekly_km": recent_weekly_km,
        "recent_longest": recent_longest,
        "easy_pace": easy_pace,
        "acwr": load["acwr"], "weeks": load["weeks"],
        "last_week_sleep": last_week_sleep,
        "goal": goals[0] if goals else None,
    }


# ── the plan (rule-based, driven by ACWR + recovery + recent volume) ─────────

# day index (0=Sun … 6=Sat) → (session label, share of weekly km, intensity note)
_TEMPLATES = {
    "deload": [   # ACWR high — pull back, mostly easy + rest
        ("Rest", 0.0, "Full rest"),
        ("Easy", 0.22, "Very easy, conversational"),
        ("Rest", 0.0, "Rest or light cross-training"),
        ("Easy", 0.26, "Easy"),
        ("Rest", 0.0, "Full rest"),
        ("Easy", 0.30, "Easy, relaxed"),
        ("Rest / short jog", 0.22, "Optional easy shake-out"),
    ],
    "maintain": [  # ACWR optimal — steady week with one quality + a long run
        ("Long run", 0.32, "Easy, steady — time on feet"),
        ("Easy", 0.14, "Easy recovery"),
        ("Tempo", 0.18, "20–30 min at comfortably-hard"),
        ("Easy", 0.14, "Easy"),
        ("Rest", 0.0, "Rest or cross-training"),
        ("Intervals", 0.16, "e.g. 5–6 × 3 min hard / 2 min jog"),
        ("Easy", 0.06, "Short easy or rest"),
    ],
    "build": [    # ACWR low — add a little volume, keep one hard session
        ("Long run", 0.34, "Easy, extend by ~1–2 km vs last week"),
        ("Easy", 0.14, "Easy"),
        ("Tempo", 0.18, "25–35 min comfortably-hard"),
        ("Easy", 0.16, "Easy"),
        ("Rest", 0.0, "Rest or cross-training"),
        ("Intervals", 0.14, "e.g. 6 × 3 min hard / 2 min jog"),
        ("Easy", 0.04, "Short easy or rest"),
    ],
}


def choose_template(acwr_value: float | None) -> tuple[str, str]:
    """Return (template_key, one-line rationale) from the ACWR band."""
    if acwr_value is None:
        return "maintain", "No load ratio available — holding a balanced week."
    if acwr_value > 1.5:
        return "deload", f"ACWR {acwr_value} is high (>1.5) — recovery week to shed injury risk."
    if acwr_value < 0.8:
        return "build", f"ACWR {acwr_value} is low (<0.8) — room to rebuild volume gradually."
    return "maintain", f"ACWR {acwr_value} is in the optimal 0.8–1.3 zone — steady build."


def build_plan(data: dict) -> dict:
    acwr_value = data["acwr"].get("value")
    key, rationale = choose_template(acwr_value)

    # Anchor volume to what the athlete has ACTUALLY been doing: blend last
    # week's km with the 4-week average, then adjust by band. This keeps the
    # week's jump inside the ~10% safe-ramp rather than chasing a stale average.
    last_km = sum(r.get("distance_km") or 0 for r in data["last_week_runs"])
    avg_km = data["recent_weekly_km"] or 20.0
    anchor = (last_km + avg_km) / 2 if last_km else avg_km
    factor = {"deload": 0.7, "maintain": 1.0, "build": 1.08}[key]
    target_km = round(anchor * factor, 1)
    # never ramp more than ~12% over last week (injury-risk guard)
    if last_km and key != "deload":
        target_km = round(min(target_km, last_km * 1.12), 1)
    # cap the long run to recent longest + 2.5 km
    long_cap = round((data["recent_longest"] or 0) + 2.5, 1) if data["recent_longest"] else None

    # recovery caveat: HRV falling or resting HR rising over recent weeks → ease off
    weeks = [w for w in data["weeks"] if w.get("hrv")]
    recovery_note = ""
    if len(weeks) >= 3:
        recent_hrv = [w["hrv"] for w in weeks[-3:]]
        if recent_hrv[-1] < recent_hrv[0] * 0.93:
            recovery_note = ("Heads-up: overnight HRV has trended down lately — if you feel "
                             "flat, swap a quality session for easy running.")

    template = _TEMPLATES[key]
    days = []
    for i in range(7):
        label, share, note = template[i]
        d = data["this_week"] + timedelta(days=i)
        km = round(target_km * share, 1)
        if label.startswith("Long") and long_cap and km > long_cap:
            km = long_cap   # safe-progression cap on the long run
        detail = note
        if km > 0:
            if label.startswith(("Easy", "Long")):
                detail = f"{km:.1f} km · {note}" + (
                    f" · ~{_fmt_pace(data['easy_pace'])}" if data["easy_pace"] and label.startswith("Easy") else "")
            else:
                detail = f"~{km:.1f} km total · {note}"
        days.append({"day": DAY_NAMES[i], "date": d, "session": label,
                     "detail": detail, "km": km, "type": _session_type(label)})

    return {"template": key, "rationale": rationale, "recovery_note": recovery_note,
            "target_km": target_km, "days": days}


# ── PDF ──────────────────────────────────────────────────────────────────────

BLUE = (47, 111, 224)
DARK = (26, 29, 35)
MUTED = (102, 112, 133)
LIGHT = (244, 246, 248)


class SchedulePDF(FPDF):
    def header(self):
        pass

    def footer(self):
        self.set_y(-15)
        self.set_font(self.body, "", 8)
        self.set_text_color(*MUTED)
        self.cell(0, 8, f"Generated {datetime.now():%Y-%m-%d %H:%M} · Garmin Coach", align="C")


def _register_fonts(pdf: FPDF) -> tuple[str, str]:
    """Register a Unicode font pair (so Greek run names render). Returns
    (regular_family, bold_family)."""
    candidates = [
        (r"C:\Windows\Fonts\segoeui.ttf", r"C:\Windows\Fonts\segoeuib.ttf", "Seg"),
        (r"C:\Windows\Fonts\arial.ttf", r"C:\Windows\Fonts\arialbd.ttf", "Arial2"),
    ]
    for reg, bold, fam in candidates:
        if os.path.exists(reg) and os.path.exists(bold):
            pdf.add_font(fam, "", reg)
            pdf.add_font(fam, "B", bold)
            return fam, fam
    return "Helvetica", "Helvetica"   # ASCII fallback


# ── native vector chart helpers (no image dependency) ────────────────────────

def _panel_title(pdf, fam, x, y, txt):
    pdf.set_xy(x, y)
    pdf.set_font(fam, "B", 9)
    pdf.set_text_color(*DARK)
    pdf.cell(80, 5, txt)


def _legend(pdf, fam, x, y, items):
    pdf.set_font(fam, "", 6.5)
    cx = x
    for color, label in items:
        pdf.set_fill_color(*color)
        pdf.rect(cx, y, 3, 3, "F")
        pdf.set_xy(cx + 3.6, y - 0.7)
        pdf.set_text_color(*MUTED)
        pdf.cell(pdf.get_string_width(label) + 2, 4, label)
        cx += 7 + pdf.get_string_width(label)


def _tiles(pdf, fam, x, y, w, tiles, h=22.0, gap=4.0):
    """A row of KPI cards: (value, label, accent_rgb)."""
    n = len(tiles)
    tw = (w - gap * (n - 1)) / n
    for i, (value, label, accent) in enumerate(tiles):
        tx = x + i * (tw + gap)
        pdf.set_fill_color(*LIGHT)
        pdf.rect(tx, y, tw, h, "F")
        pdf.set_fill_color(*accent)
        pdf.rect(tx, y, tw, 2.6, "F")
        pdf.set_xy(tx, y + 5.5)
        pdf.set_font(fam, "B", 16)
        pdf.set_text_color(*DARK)
        pdf.cell(tw, 8, value, align="C")
        pdf.set_xy(tx, y + 15)
        pdf.set_font(fam, "", 7.5)
        pdf.set_text_color(*MUTED)
        pdf.cell(tw, 4, label, align="C")
    return y + h


def _weekly_load_panel(pdf, fam, x, y, w, h, weeks):
    _panel_title(pdf, fam, x, y, "8-week load (run + ride)")
    px, py = x + 2, y + 8
    pw, ph = w - 4, h - 16
    base = py + ph
    max_load = max((wk.get("load") or 0 for wk in weeks), default=0) or 1
    n = max(len(weeks), 1)
    slot = pw / n
    bw = slot * 0.58
    for i, wk in enumerate(weeks):
        cx = px + i * slot + (slot - bw) / 2
        run_h = (wk.get("run_load") or 0) / max_load * ph
        ride_h = (wk.get("ride_load") or 0) / max_load * ph
        pdf.set_fill_color(47, 111, 224)
        pdf.rect(cx, base - run_h, bw, run_h, "F")
        pdf.set_fill_color(155, 89, 182)
        pdf.rect(cx, base - run_h - ride_h, bw, ride_h, "F")
        if i % 2 == 0:
            pdf.set_xy(px + i * slot, base + 1)
            pdf.set_font(fam, "", 5.5)
            pdf.set_text_color(*MUTED)
            pdf.cell(slot, 3, wk["week_start"][5:], align="C")
    pdf.set_draw_color(*MUTED)
    pdf.set_line_width(0.2)
    pdf.line(px, base, px + pw, base)
    _legend(pdf, fam, px, y + h - 3, [((47, 111, 224), "Run"), ((155, 89, 182), "Ride")])


def _recovery_panel(pdf, fam, x, y, w, h, weeks):
    """Overnight HRV trend (recovery) — the companion to the load bars."""
    _panel_title(pdf, fam, x, y, "Overnight HRV (recovery, ms)")
    px, py = x + 2, y + 8
    pw, ph = w - 4, h - 16
    base = py + ph
    vals = [(i, wk.get("hrv")) for i, wk in enumerate(weeks) if wk.get("hrv")]
    if not vals:
        pdf.set_xy(px, py + ph / 2)
        pdf.set_font(fam, "", 7)
        pdf.set_text_color(*MUTED)
        pdf.cell(pw, 4, "No HRV data", align="C")
        return
    lo = min(v for _, v in vals) * 0.92
    hi = max(v for _, v in vals) * 1.05
    span = (hi - lo) or 1

    def yp(v):
        return base - (v - lo) / span * ph

    n = max(len(weeks) - 1, 1)
    slot = pw / n
    pts = [(px + i * slot, yp(v)) for i, v in vals]
    pdf.set_draw_color(23, 162, 184)
    pdf.set_line_width(0.6)
    for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
        pdf.line(x1, y1, x2, y2)
    pdf.set_fill_color(23, 162, 184)
    for cx, cy in pts:
        pdf.ellipse(cx - 0.8, cy - 0.8, 1.6, 1.6, "F")
    lx, ly = pts[-1]
    pdf.set_xy(lx - 11, ly - 5)
    pdf.set_font(fam, "B", 7)
    pdf.set_text_color(23, 162, 184)
    pdf.cell(11, 3, f"{vals[-1][1]:.0f} ms", align="C")
    pdf.set_draw_color(*MUTED)
    pdf.set_line_width(0.2)
    pdf.line(px, base, px + pw, base)


def _plan_bars(pdf, fam, x, y, w, h, days):
    px, py = x + 2, y + 4
    pw, ph = w - 4, h - 13
    base = py + ph
    max_km = max((d["km"] for d in days), default=0) or 1
    slot = pw / 7
    bw = slot * 0.6
    for i, d in enumerate(days):
        cx = px + i * slot + (slot - bw) / 2
        color, _ = SESSION_COLORS[d["type"]]
        bh = (d["km"] / max_km) * ph if d["km"] else 0
        if bh > 0:
            pdf.set_fill_color(*color)
            pdf.rect(cx, base - bh, bw, bh, "F")
            pdf.set_xy(px + i * slot, base - bh - 4)
            pdf.set_font(fam, "B", 7)
            pdf.set_text_color(*DARK)
            pdf.cell(slot, 3, f"{d['km']:.0f}", align="C")
        else:
            pdf.set_xy(px + i * slot, base - 4)
            pdf.set_font(fam, "", 6)
            pdf.set_text_color(*MUTED)
            pdf.cell(slot, 3, "rest", align="C")
        pdf.set_xy(px + i * slot, base + 1.5)
        pdf.set_font(fam, "B", 7.5)
        pdf.set_text_color(*DARK)
        pdf.cell(slot, 3, d["day"][:3], align="C")
    pdf.set_draw_color(*MUTED)
    pdf.set_line_width(0.2)
    pdf.line(px, base, px + pw, base)
    seen, used = set(), []
    for d in days:
        if d["type"] != "rest" and d["type"] not in seen:
            seen.add(d["type"])
            used.append(SESSION_COLORS[d["type"]])
    _legend(pdf, fam, px, y + h - 2.5, used)


def render_pdf(data: dict, plan: dict) -> Path:
    pdf = SchedulePDF()
    fam, _ = _register_fonts(pdf)
    pdf.body = fam
    pdf.set_auto_page_break(True, margin=14)
    pdf.add_page()
    ML = pdf.l_margin
    CW = pdf.w - pdf.l_margin - pdf.r_margin
    wk = data["this_week"]

    # Title
    pdf.set_font(fam, "B", 20)
    pdf.set_text_color(*DARK)
    pdf.cell(0, 10, "Weekly Running Schedule", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font(fam, "", 11)
    pdf.set_text_color(*MUTED)
    pdf.cell(0, 7, f"Week of {wk:%a %d %b %Y}  ·  prepared {data['today']:%d %b %Y}",
             new_x="LMARGIN", new_y="NEXT")
    if data["goal"]:
        g = data["goal"]
        secs = g.get("target_time_seconds")
        ttxt = f" in {secs // 60}:{secs % 60:02d}" if secs else ""
        gd = date.fromisoformat(g["target_date"])
        wks = max(0, (gd - data["today"]).days // 7)
        pdf.set_text_color(*BLUE)
        pdf.cell(0, 7, f"Goal: {GOAL_LABELS.get(g['distance'], g['distance'])}{ttxt} "
                        f"on {gd:%d %b} ({wks} weeks out)", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(2)

    # ── Section 1: last week in review — KPI tiles + charts
    runs = data["last_week_runs"]
    total_km = sum(r.get("distance_km") or 0 for r in runs)
    total_min = sum(r.get("duration_min") or 0 for r in runs)
    longest = max((r.get("distance_km") or 0 for r in runs), default=0)
    a = data["acwr"]
    sleep = data["last_week_sleep"]
    avg_score = round(sum(s.get("score") or 0 for s in sleep) / len(sleep)) if sleep else None
    avg_hrv = round(sum(s.get("hrv_ms") or 0 for s in sleep) / len(sleep)) if sleep else None

    _section(pdf, fam, "Last week in review",
             f"{data['last_week_start']:%d %b} – {data['last_week_end']:%d %b}")

    status_color = {"optimal": (31, 138, 76), "low": (245, 166, 35),
                    "high": (224, 82, 82), "caution": (245, 166, 35)}.get(
        a.get("status"), (47, 111, 224))
    tiles = [
        (f"{total_km:.0f} km", "Distance", (47, 111, 224)),
        (str(len(runs)), "Runs", (47, 111, 224)),
        (f"{a.get('value')}", f"ACWR · {a.get('status')}", status_color),
        (str(avg_score) if avg_score is not None else "—", "Avg sleep", (108, 92, 231)),
    ]
    y2 = _tiles(pdf, fam, ML, pdf.get_y(), CW, tiles)
    pdf.set_y(y2 + 1.5)
    pdf.set_font(fam, "", 8.5)
    pdf.set_text_color(*MUTED)
    cap = f"Longest run {longest:.1f} km   ·   total time {int(total_min)} min"
    if avg_hrv:
        cap += f"   ·   avg overnight HRV {avg_hrv} ms"
    pdf.cell(0, 5, cap, new_x="LMARGIN", new_y="NEXT")
    pdf.ln(2)

    # two trend charts side by side — completed weeks only (drop the in-progress
    # week, whose partial data would otherwise dip the last bar/point misleadingly)
    hist_weeks = [w for w in data["weeks"] if not w.get("partial")] or data["weeks"]
    ct = pdf.get_y()
    ch = 44
    half = (CW - 8) / 2
    _weekly_load_panel(pdf, fam, ML, ct, half, ch, hist_weeks)
    _recovery_panel(pdf, fam, ML + half + 8, ct, half, ch, hist_weeks)
    pdf.set_y(ct + ch + 3)

    # ── Section 2: this week's plan — bar chart + compact table
    _section(pdf, fam, "This week's plan", plan["rationale"])
    if plan["recovery_note"]:
        pdf.set_font(fam, "", 9)
        pdf.set_text_color(*BLUE)
        pdf.multi_cell(0, 5.5, plan["recovery_note"], new_x="LMARGIN", new_y="NEXT")
    pdf.set_font(fam, "", 9.5)
    pdf.set_text_color(*MUTED)
    pdf.cell(0, 5.5, f"Target volume this week: ~{plan['target_km']:.0f} km",
             new_x="LMARGIN", new_y="NEXT")
    pdf.ln(1)

    pt = pdf.get_y()
    phh = 38
    _plan_bars(pdf, fam, ML, pt, CW, phh, plan["days"])
    pdf.set_y(pt + phh + 2)

    # compact detail table
    col_day, col_sess, col_det = 34, 30, CW - 64
    pdf.set_font(fam, "B", 9)
    pdf.set_fill_color(*BLUE)
    pdf.set_text_color(255, 255, 255)
    pdf.cell(col_day, 7, "  Day", fill=True)
    pdf.cell(col_sess, 7, "Session", fill=True)
    pdf.cell(col_det, 7, "Details", fill=True, new_x="LMARGIN", new_y="NEXT")
    pdf.set_font(fam, "", 9)
    for i, d in enumerate(plan["days"]):
        fill = i % 2 == 0
        if fill:
            pdf.set_fill_color(*LIGHT)
        y0 = pdf.get_y()
        color = SESSION_COLORS[d["type"]][0]
        pdf.set_text_color(*DARK)
        pdf.set_font(fam, "B", 9)
        pdf.multi_cell(col_day, 6.5, f"  {d['day'][:3]} {d['date']:%d %b}", border=0,
                       fill=fill, new_x="RIGHT", new_y="TOP", max_line_height=6.5)
        pdf.set_xy(ML + col_day, y0)
        pdf.set_text_color(*color)
        pdf.multi_cell(col_sess, 6.5, d["session"], border=0, fill=fill,
                       new_x="RIGHT", new_y="TOP", max_line_height=6.5)
        pdf.set_xy(ML + col_day + col_sess, y0)
        pdf.set_font(fam, "", 9)
        pdf.set_text_color(*MUTED)
        pdf.multi_cell(col_det, 6.5, d["detail"], border=0, fill=fill,
                       new_x="LMARGIN", new_y="NEXT", max_line_height=6.5)

    out = DOWNLOADS / f"running schedule - week of {wk:%Y-%m-%d}.pdf"
    pdf.output(str(out))
    return out


def _section(pdf: FPDF, fam: str, title: str, subtitle: str = "") -> None:
    pdf.set_font(fam, "B", 13)
    pdf.set_text_color(*BLUE)
    pdf.cell(0, 8, title, new_x="LMARGIN", new_y="NEXT")
    if subtitle:
        pdf.set_font(fam, "", 9.5)
        pdf.set_text_color(*MUTED)
        pdf.cell(0, 5.5, subtitle, new_x="LMARGIN", new_y="NEXT")
    pdf.ln(1)


def main() -> int:
    try:
        DOWNLOADS.mkdir(parents=True, exist_ok=True)
        data = gather()
        plan = build_plan(data)
        out = render_pdf(data, plan)
        logger.info("Wrote weekly schedule → %s", out)
        print(out)
        return 0
    except Exception:
        logger.exception("Weekly schedule generation failed")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
