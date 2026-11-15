# Garmin Coach — Project Handoff

A transferable reference for the `garmin-coach` app, its automations, and the
athlete's coaching plan. Written 2026-11-15. Everything below reflects the
current state after an extended build/coaching session.

---

## 1. What this is

A **local personal running-coach app** at `C:\Users\papad\Desktop\garmin-coach`.
It pulls the athlete's Garmin data into a local database, shows dashboards,
auto-generates a weekly PDF coaching report, and (new) validates calorie/weight
numbers. The athlete is **Nikanoras Papadopoulos** (male, 27, 174 cm,
~67 kg, ~14% body fat; email papadopoulos46doc@gmail.com).

**Stack:** FastAPI + PostgreSQL (psycopg3, `dict_row`, connection pool) + HTMX +
Chart.js (single-file frontend) + Playwright + `garminconnect` + the Claude API.
Runs on **uvicorn port 8000** (`start_app.bat` uses `--reload`;
`scripts/open_when_ready.ps1` polls the port then opens an Edge/Chrome app window).
Python venv at `.venv\Scripts\python.exe` (the `python` on PATH is a broken Store
stub). Greek run names need `PYTHONIOENCODING=utf-8`.

---

## 2. Database (PostgreSQL)

Schema + pool + all queries live in `backend/db.py` (`SCHEMA` string,
`_ensure_schema`, `get_connection`). Tables:

| Table | Purpose |
|---|---|
| `activities` | runs + rides (full history **May 2024 → present**, ~1,583 rows) |
| `goals` | race goals (5k / 10k / half / marathon) |
| `calories` | Garmin daily burn (activity/resting/total) |
| `foods`, `food_log` | manual food library + daily log |
| `sleep_nights`, `sleep_weeks` | sleep data |
| `weight_log` | **(new)** daily body weight in kg |
| `app_settings` | **(new)** key/value store (body-fat %, activity factor, activity mode) |
| `chat_messages`, `weekly_plans` | legacy (chat removed — see §8) |

**Critical gotcha:** `db.get_recent_activities()` defaults to **`LIMIT 200`**.
This silently truncated history in callers. Fixed in `fitness_report.py` by
calling `get_recent_activities(days=1200, limit=10000)`. If you add a feature
that needs full history, pass an explicit high `limit`.

Key new functions: `upsert_weight`, `get_weights`, `delete_weight`,
`get_setting(key, default)`, `set_setting(key, value)`.

---

## 3. Garmin data sync

`backend/garmin_api.py` wraps `garminconnect` (which wraps `garth`).

- **Token caching:** `_client()` resumes from `data/garmin_tokens/garmin_tokens.json`.
  **Garmin rate-limits LOGINS, not data calls.** Reuse the token; never loop logins.
- Functions: `fetch_activities`, `fetch_recent_sleep_nights`, `fetch_calories_days`,
  `current_vo2max`. Sleep/calories use Garmin's JSON API (the sleep **web page**
  serves a "Looks Like We Crashed" error — don't scrape it).
- Activity + cycling sync via `backend/routes/activities.py::_sync_and_import(["running","cycling"])`.

### 429 login-storm incident + fix (2026-11-03) — important
A transient **403 / "Failed to retrieve social profile"** was misclassified as an
auth error, which **wiped the saved token**; with no token, every sync did a full
login, and the multi-trigger daily task + manual presses produced ~15 logins →
Garmin **429-banned the IP**. Fixes in `garmin_api.py`:
- `_is_auth_error` now matches **only 401/unauthorized** (not 403, 429, "social profile").
- `_call` **never wipes the token** and never retries under a rate-limit.
- A **persisted 1-hour login backoff** (`data/garmin_login_backoff`) set on any 429
  (`_is_rate_limited`, `_set_backoff`, `_backoff_remaining`).
- Recovery if it ever recurs: wait out the ban, then run one sync to re-cache the token.

### Daily auto-sync
`daily_sync.py` (root) + Windows Scheduled Task **"GarminCoach Daily Sync"**:
- Triggers: **daily 23:58** + **at logon** (the earlier resume-from-sleep trigger was
  removed — it fired on every wake and amplified logins).
- **3-hour cooldown** (`data/daily_sync_last_ok`) so the two triggers can't double-run.
- Syncs activities + sleep (7 nights) + calories (7 days), each step isolated.
- Install/update: `scripts\install_daily_sync_task.ps1` (non-admin, pythonw, no console).
- Logs: `data/daily_sync.log`.

---

## 4. Weekly report (`fitness_report.py`) — the main deliverable

A multi-page PDF (~7 pages) in the **"APEX" dark design**.

- **Pages:** `page_overview` (hero + 6 KPI tiles + race countdown + 8-week load + HRV),
  `page_review` (Recovery · Nutrition · Running progression with the efficiency chart
  + written commentary + "Toward your goals"), `page_plan` (Mon–Sat plan cards),
  `page_sleep`, `page_nutrition`.
- **Design:** ink `#0e1116` canvas, one orange accent `#ff5a1f`, cyan `#39e0c8` =
  positive, amber = watch, violet = sleep. Fonts **bundled in `assets/fonts/`**
  (IBM Plex Sans Condensed for display/numbers, IBM Plex Mono for data — Greek-capable;
  Segoe UI fallback). `ReportPDF.header()` paints the full-bleed dark bg + brand bar.
- **Weeks are Mon–Sat** (`gather()`: `rev_start`=Mon, `rev_end`=Sat, `plan_start`=next Mon).
- **Efficiency chart** (`_efficiency_scatter`): EF = speed(km/h)/HR×100, faint per-run
  dots + bold orange weekly-average line. Shows **last ~365 days** (`window_days=365`)
  for readability while `gather()` loads the full history. Outlier guard drops EF
  outside 3–11 (kills bad GPS points). The comparison table + runs table were
  **removed** from the review page at the athlete's request.
- **Output folder: `C:\Users\papad\Desktop\Fitness Report`** (`OUTPUT_DIR`, moved from
  Downloads 2026-11-14). Filename `fitness report - week of <review-Monday>.pdf`.
  That folder is the athlete's report archive — leave past reports there.
- **AI content** lives in `data/report_ai.json` (schema: `week_review_start/end`,
  `plan_week_start`, `overall_verdict`, `recovery_comment`, `nutrition_comment`,
  `progression_commentary`, `goal_outlook`, `plan_focus`, `plan_days` (6, Mon–Sat),
  `training_feedback`, `sleep_feedback`, `nutrition_feedback`). `_validate_ai` +
  `_check_pages` log warnings. `_plan_day_block` is measured + kept-together so a day
  never splits across a page (that caused earlier blank pages).
- Dev-only verification tools installed in the venv: `pymupdf`, `pypdf` (NOT runtime deps).

### Report automation (subscription, no API cost)
A **Claude Desktop scheduled task "weekly-fitness-report"** (cron `0 10 * * 0`,
Sundays ~10:00) runs a subscription session that: pulls MCP data, reads
`data/week_notes.txt`, writes `report_ai.json`, runs `fitness_report.py`.
- Prompt: `C:\Users\papad\.claude\scheduled-tasks\weekly-fitness-report\SKILL.md`
  (encodes the full training template + nutrition stance + output folder).
- Runs **only while Claude Desktop is open** at 10:00 (catches up on next launch).
- **`data/week_notes.txt`** is the athlete's per-week context channel: date-tagged
  blocks (`[Week of START to END]`); the Sunday session uses only the note matching
  the review week and ignores the rest (self-expiring). Use it for things the data
  can't show (illness, a session swapped, a calorie forecast, race-vs-self-test status).

### MCP server for the Claude Desktop chat
`mcp_coach.py` (local stdio, read-only): tools `recent_runs/cycling/sleep/calories/
nutrition`, `goals`, `pr_progression`, `training_load`, `speed_workouts`. Registered
in `claude_desktop_config.json`. Reuses `backend.coach._run_tool`. New MCP tools need
a Desktop restart.

---

## 5. The coaching model (current — as of 2026-11-10)

**Goals, in priority order:** (1) get faster at **5k**, then (2) **10k**, then (3)
**half (21.1k)**. Leaner/athletic physique is now **secondary** (performance-first).

**Weekly structure (Mon–Sat, Sunday = only rest):** three quality days, **one per
energy system**:
- **Mon** easy + 6×100 m strides
- **Tue** VO₂ / 5k intervals + a LIGHT lower-body strength block
- **Wed** easy medium-long (10–14 km) + strides — a volume day
- **Thu** **THRESHOLD / tempo** (the centerpiece for 10k–half; ~5–6 km cruise intervals
  @ ~4:15–4:20) + a LIGHT upper/posterior strength block
- **Fri** easy or bike + strides (no lifting)
- **Sat** **long run** + a progressing HM-pace block (build toward 24–28 km)

**Strength is secondary** — 2 light maintenance sessions on the hard-run days; cut
before cutting running if tired.

**Volume:** the athlete is **fully injury-free** (proven 70–73 km/wk in 2024), so build
toward **~65–75 km/wk**. Levers in order: grow the long run → lengthen easy runs →
**doubles** (2nd short easy run, used in 2024) → strides. Ramp ≤~10%/wk, cutback
−25–30% every 4th week, keep **ACWR < 1.4**. Cycling is bonus aerobic, not a substitute
for running-specific volume.

**Nutrition = PERFORMANCE-FIRST** (changed 2026-11-10, superseding an earlier
fat-loss/recomp deficit stance): fuel the work fully, no deliberate deficit, protein
~1.8–2.0 g/kg (~150–160 g/day), carbs scale with volume. Fat loss is a slow background
effect, not a target.

**Races:** the **Dec 20 half (1:19) is a PLACEHOLDER** (entered as an example, not real)
and the **Nov 15 5k is a self-test/marker** (not a committed race). **Do not build a
peak/taper for placeholders** — the plan is a steady sustained volume build; taper only
for races the athlete actually commits to.

Speed library (`data/speed_library.json`): track-workout library + current-fitness paces,
exposed to the Desktop coach via the `speed_workouts` MCP tool (NOT printed in the report).

---

## 6. Training history + current fitness

Full detail is in memory `training_history_2024_2026.md`. Key facts:
- **DB holds the full history (May 2024 → present, ~1,583 activities).**
- **2024 is the athlete's "big volume = best times" reference:** peak **70–73 km/wk**
  (Jul–Aug) marathon block, long runs to 42 km → **~3:12 marathon (Sep 29 2024)**, plus
  5k ~18:35, 10k ~41:30, half ~1:33.
- **Running-only km/year:** 2024 ~1,565; 2025 ~1,847 (~35/wk + heavy cycling ~1,800 km);
  2026 ~1,471 so far. (Earlier a figure conflated running+cycling — the 70 km/wk was a
  2024 **peak**, not a 2-year norm; running norm is ~35–50 km/wk.)
- **Current fitness:** VO₂max **58**; 3 km time-trial **10:47 (3:35/km, HR 176) on Nov 8
  → predicts ~18:30 5k**; recent track: Nov 3 5×1km @ 3:52 (5k), Nov 5 5×1000 @ 4:10 (10k),
  Nov 10 3×1km @ 3:32. HRV peaked **100–105** in early Nov; ACWR ~1.0. Aerobic efficiency
  ~7.4 (oscillates 6.5–8.7, peaks in cool months / volume blocks).

---

## 7. Calibration tab (the weight + calorie-validation tool)

Formerly the **"Coach" tab**; renamed **"Calibration"** (icon `ti-scale`; internal
`data-tab`/panel id stay `"coach"`). Code: `backend/routes/weight.py`, frontend section
in `frontend/index.html` (`#weight-panel`, `loadWeight()` / `initCoach()`).

**Purpose:** the app's calorie "deficit" is an estimate; actual weight change is the
ground truth. ~7,700 kcal ≈ 1 kg.

**Features (all keyed to the 7-day AVERAGE weight, which smooths daily noise):**
- **Log daily weight** → `weight_log`. Shows latest, 7-day avg, Δ vs last week (losing/
  gaining/holding).
- **Calorie validation:** app's 7-day net (eaten − burned) → predicted kg (÷7700) vs the
  measured Δ. Verdict: ✅ checks out / 🟠 over-estimates deficit / 🔵 under-estimates,
  with the implied daily kcal error.
- **Body composition (Katch-McArdle):** editable **body-fat %** (14%, in `app_settings`)
  → **LBM = avg_kg × (1 − bf%)**, **BMR = 370 + 21.6 × LBM**. (Current: LBM 57.8 kg,
  BMR 1,618 kcal. Note the athlete's other chat used 12% → BMR ~1,663.)
- **Maintenance (TDEE) = BMR × activity factor.** The factor is **dynamic by default**:
  `= Garmin 7-day-avg burn ÷ BMR`, so maintenance ≈ Garmin's measured burn and the
  multiplier self-adjusts to the week's load (taper↓, peak↑). A **Manual** override mode
  exists for sparse-data weeks. Current: dynamic **×1.81**, maintenance **~2,931 kcal**
  (Garmin measured). At ~2,650 intake that's a ~280 kcal/day deficit ≈ 0.26 kg/wk.

**Routes:** `/weight/log`, `/weight/balance` (GET JSON — the engine), `/weight/bodyfat`,
`/weight/activity` (manual factor), `/weight/activity-mode` (dynamic|manual). Settings
keys: `bodyfat_pct`, `activity_factor`, `activity_mode`.

---

## 8. Known issues / gotchas

- **In-app chat was removed** (Coach→Calibration). Root cause of its `400 BadRequestError`:
  the installed **`anthropic` SDK is 0.104.1 (old 0.x)** and can't send the modern
  `thinking:{type:"adaptive"}` / `output_config:{effort}` params that `backend/coach.py`
  uses (model `claude-opus-4-7` is itself valid). Fix if ever revived: `pip install -U anthropic`.
- `backend/routes/chat.py` + `plans.py` and the chat-coupled "this week's plan" UI are
  now **unused** (left in place, harmless; can be deleted with `coach.py`/`agent.py`).
- `superseded`: `weekly_schedule.py` + its "GarminCoach Weekly Schedule" Windows task are
  disabled (replaced by `fitness_report.py` + the Desktop scheduled task).
- Automations run on the **subscription** → need Claude Desktop open at the scheduled time.

---

## 9. Design directions (for reference)

Three sellable report designs were explored as mockups: **APEX** (dark, performance —
**chosen and built into the real report**), **MERIDIAN** (editorial/print), **VITALS**
(clinical/lab). Only APEX is in the codebase.

---

## 10. Memory files (persistent coach context)

`C:\Users\papad\.claude\projects\C--Users-papad-Desktop-linkedin-ai\memory\`:
`MEMORY.md` (index), `project_garmin_coach.md`, `garmin_sync_browser_launch.md`,
`garmin_sleep_api_sync.md`, `garmin_weekly_schedule_automation.md` (the report + full
training template), `garmin_daily_sync.md`, `training_history_2024_2026.md`. (Also
unrelated: `project_nutritionist_app.md`, `project_our_cookbook.md`.)

---

## 11. Immediate state / next steps

- **Tomorrow is handled:** the Sunday report task is armed; it will review Mon Nov 9 –
  Sat Nov 14 (a building week; `week_notes` tells it Nov 15 is a self-test, not a race),
  plan Nov 16–21 (continuing the volume build), and write to `C:\Users\papad\Desktop\Fitness Report`.
- Open threads the athlete may pick up: confirm **12% vs 14% body fat**; a **daily-deficit
  + projected-weekly-loss tile** on the Calibration tab; deleting the dead chat/plans
  backend; keep `docs/technical_overview.html` current (last at v5.6).
- Per this repo's `CLAUDE.md`, agents built here are documented in the Obsidian wiki
  (`llm-wiki/wiki/ai-agents/`) — relevant if the coach logic is formalized as an agent.
