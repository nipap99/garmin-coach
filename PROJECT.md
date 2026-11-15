# Garmin Coach: project knowledge

The one file to read for this project: what it is, how it's built, how it runs, how the
coaching works, and where things stand. It replaces `README.md`, `HANDOFF.md` and the old
technical docs, which are in `_archive/2026-11-15/`.

**Last updated:** 2026-11-15 (cleanup + data/coach split + coaching upgrades).

---

## 1. What it is

A **local personal running coach** for one athlete (male, 27, 174 cm, ~67 kg, ~14 % body fat,
VO2max ~58). It has four parts:

1. Pulls Garmin data (runs, rides, sleep, calories) into a local PostgreSQL database every night.
2. Shows dashboards in a local web app.
3. Writes a 7-page **weekly PDF report** with a review of the week and next week's plan.
4. Gives Claude (Desktop chat, the Sunday report task, the `run-analyst` subagent) a window into
   the data through a local **MCP server**.

**Running cost: $0.** All AI runs on the Claude Pro subscription. No code path calls the paid
API any more (the paid chat code was retired on 2026-11-15).

---

## 2. Folder map

```
garmin-coach/
├─ PROJECT.md              ← this file
├─ start_app.bat           ← open the dashboard (uvicorn :8000 + app window)
├─ daily_sync.py           ← JOB: nightly Garmin → DB            (Windows task)
├─ fitness_report.py       ← JOB: Sunday PDF                     (Claude Desktop scheduled task)
├─ mcp_coach.py            ← MCP server: Claude's window into the data
├─ requirements.txt  .env  .env.example  .gitignore  LICENSE
│
├─ backend/                ← DATA LAYER (Python, deterministic)
│   ├─ config.py           .env → settings (Garmin login, token dir, DATABASE_URL)
│   ├─ db.py               schema + every SQL query (psycopg3 pool, dict rows)
│   ├─ garmin_api.py       Garmin JSON API via garminconnect (token-cached, 429 backoff)
│   ├─ training_load.py    TRIMP load + ACWR (Monday weeks, runs + rides)
│   ├─ metrics.py          sessions, hard/easy, week vs plan, VDOT paces, readiness, long-run
│   ├─ coach_tools.py      the functions behind the MCP tools (JSON out)
│   ├─ nutrition.py        food library + food-log logic (used by MCP food tools)
│   ├─ csv_importer.py     manual Garmin CSV upload (Running tab)
│   ├─ main.py             FastAPI app
│   └─ routes/             activities (sync/import) · goals · nutrition (Food tab) · stats · weight
│
├─ coaching/               ← THE COACH'S BRAIN (plain text, edit freely)
│   ├─ coaching_rules.md   single source of truth: goals, weekly template, paces, volume, nutrition
│   ├─ speed_library.json  track-workout library (paces now computed, see §6)
│   └─ week_notes.txt      YOUR weekly notes for the Sunday report (illness, swaps, intent)
│
├─ frontend/index.html     single-page dashboard (HTMX + Chart.js)
├─ assets/                 IBM Plex fonts for the PDF, runner.ico
├─ scripts/                install_daily_sync_task.ps1 · open_when_ready.ps1 ·
│                          setup_postgres.sql · bulk_import_csvs.py
├─ data/                   machine stuff (git-ignored): logs, garmin_tokens/, report_ai.json,
│                          daily_sync_last_ok, raw/ (old Garmin CSV exports)
└─ _archive/2026-11-15/    retired code + old docs (git-ignored, safe to delete after ~a month)
```

**Outside this folder:**

| Thing | Path |
|---|---|
| Weekly PDFs (archive, keep) | `C:\Users\papad\Desktop\Fitness Report\` |
| Sunday report prompt | `C:\Users\papad\.claude\scheduled-tasks\weekly-fitness-report\SKILL.md` |
| run-analyst subagent | `C:\Users\papad\Desktop\linkedin-ai\.claude\agents\run-analyst.md` |
| Claude Desktop MCP config | `%APPDATA%\Claude\claude_desktop_config.json` → `mcpServers.garmin-coach` |
| Agent docs (Obsidian wiki) | `C:\Users\papad\Desktop\linkedin-ai\llm-wiki\wiki\ai-agents\` |

---

## 3. Who does what (the three layers)

| Layer | Job | Who | Cost |
|---|---|---|---|
| **1. Data** | Sync, store, and **compute every number**: sessions (laps merged), Mon–Sun weekly km, hard/easy classification, ACWR, plan adherence, VDOT paces, readiness light, long-run status | Python: `daily_sync.py`, `backend/metrics.py`, `training_load.py` | $0 |
| **2. Coaching** | Judgment only: was the week good, what changes, next week's plan. Reads pre-computed numbers through MCP, follows `coaching/coaching_rules.md`, writes `data/report_ai.json` | Claude on the subscription: Sunday task, `run-analyst`, Desktop chat | $0 |
| **3. Presentation** | Draw the PDF from DB + `report_ai.json`. The dashboard shows the same numbers live | Python: `fitness_report.py`, FastAPI + `frontend/` | $0 |

Rule of thumb: **Claude never counts, Python never judges.** To change the numbers, edit
`metrics.py`. To change the coaching, edit `coaching_rules.md`.

---

## 4. Automations

| What | When | How | Logs |
|---|---|---|---|
| **Daily sync** | 23:58 daily + at logon (3 h cooldown) | Windows task "GarminCoach Daily Sync" → `.venv\Scripts\pythonw.exe daily_sync.py`. Syncs activities (run + ride), 7 nights of sleep, 7 days of calories. Reinstall: `scripts\install_daily_sync_task.ps1` | `data/daily_sync.log` |
| **Weekly report** | Sunday 10:00 (cron `0 10 * * 0`) | Claude Desktop scheduled task "weekly-fitness-report". Runs **only while Claude Desktop is open** (catches up on next launch). Reads the rules + MCP data + `week_notes.txt` → writes `report_ai.json` → runs `fitness_report.py` → PDF + saves the plan to `weekly_plans` | `data/fitness_report.log` |

Retired: the "GarminCoach Weekly Schedule" task (deleted, replaced by the report) and the
Wednesday check-in task (deleted; use `run-analyst` on demand).

**Manual commands** (from `garmin-coach\`):
```
start_app.bat                                          # dashboard
.venv\Scripts\python.exe daily_sync.py                 # sync now
.venv\Scripts\python.exe fitness_report.py             # render PDF + save plan
.venv\Scripts\python.exe fitness_report.py --out X     # test render to folder X (no plan save)
```

---

## 5. MCP server (`mcp_coach.py`)

Local stdio server, registered in Claude Desktop (and available to Claude Code sessions).
**Restart Claude Desktop after adding or renaming tools.** Postgres must be running.

| Tool | Returns |
|---|---|
| `coach_snapshot` | **Start here.** ACWR, weekly running km (Mon–Sun), weekly load incl. rides + HRV/RHR, readiness light, long-run status, current paces, goals, last + this week vs plan |
| `week_summary(week_start)` | One Mon–Sun week day by day: planned vs done, km, hard days, adherence, flags |
| `coaching_rules` | Contents of `coaching/coaching_rules.md` |
| `speed_workouts` | Workout library + `current_paces` (VDOT) + predicted race times |
| `training_load` | Weekly TRIMP (runs + rides, Monday weeks), ACWR, HRV, RHR |
| `goals` | Active race goals |
| `recent_sessions(days)` | Merged + classified sessions with rep summaries |
| `recent_runs / recent_cycling / recent_sleep / recent_calories / recent_nutrition` | Raw rows (note: runs are one row **per lap**) |
| `pr_progression(distance)` | Times at 5k / 10k / half / marathon over a year |
| `find_food / add_food / log_food / food_day / remove_food_entry` | **Food logging** (the only write tools). Send a food photo or a sentence in the Claude app and it logs to the DB for $0 |

---

## 6. How the metrics work (`backend/metrics.py`)

- **Sessions:** Garmin saves each lap as its own activity row. Rows on the same day with gaps
  under 45 min are merged into one session. Leftovers under 0.5 km are dropped.
- **Classification:** a "work" rep is a lap of 0.3 km or more that is either faster than
  threshold + 12 s/km or has avg HR ≥ 168. Then:
  - 16 km or more → **long**
  - no work → **easy**
  - one dominant fast block of 2.5 km+, faster than 10k pace → **race**
  - otherwise faster than halfway between threshold and 10k pace → **intervals**, else **tempo**

  Hard = intervals / tempo / race / long.
- **Week vs plan:** the plan comes from `weekly_plans` (saved by the Sunday report), else the
  template. Flags: more than 3 hard days, hard days back to back, hard on an easy/rest day,
  Sunday run, no long run, planned X but did Y.
- **Current paces (VDOT):** taken from the best effort of 3 km or more in the last 42 days (Jack
  Daniels' formula).
  - 5k / 10k / interval / rep paces use the full VDOT.
  - Threshold / HM / marathon use an **endurance VDOT**: minus 1 point per 10 km/wk below
    60 km/wk (max −3), because a short time trial flatters endurance when volume is low.
  - Easy pace = the athlete's own recent easy runs (HR cap ~150).
- **Readiness light:** last night vs the previous 7 nights.
  - red: HRV < 80 % or resting HR +5.
  - amber: HRV < 90 %, resting HR +3, or sleep score < 60.
  - green: otherwise.

  It's shown on the dashboard's Today card.
- **Long-run status:** days since the last run of 16 km or more. Alert at 14+ days.
- **ACWR** (`training_load.py`): acute 7-day TRIMP ÷ chronic 28-day weekly average. TRIMP =
  duration × HRr × 0.64 × e^(1.92·HRr). Max HR = highest recorded; resting HR = sleep average.

**Week convention everywhere:** Monday–Sunday (changed from Sunday-start on 2026-11-15;
dashboard weekly charts too).

---

## 7. Coaching model (summary; full rules in `coaching/coaching_rules.md`)

- **Goals in priority order:** 5k → 10k → half. Physique is secondary.
- **Targets:** 10k 38:00 (Dec 2026, checkpoint) · 5k 17:40 (Mar 2027, checkpoint) · half 1:19:00
  (May 2027, the one **race**). Taper only for the half.
- **Week:** Mon easy+strides · **Tue VO2 intervals** · Wed medium-long easy · **Thu threshold** ·
  Fri easy/bike · **Sat long run + HM block** · Sun rest. Max 3 hard days. Protect the long run.
- **Volume:** build toward 65–75 km/wk, ≤10 %/wk, cutback every 4th week, ACWR < 1.4.
- **Nutrition:** performance-first. No deficit, protein 150–160 g/day, carbs scale with volume.

---

## 8. Weekly report (`fitness_report.py`)

7 pages in the dark **APEX** design: Overview (tiles, race countdown, load, HRV, verdict) →
Weekly Review (**plan vs actual table**, recovery, nutrition, efficiency trend + progression
write-up) → Plan (Mon–Sat cards) → Sleep → Nutrition.

- **AI content:** `data/report_ai.json`. The keys are listed in the SKILL.md prompt.
  `_validate_ai` and `_check_pages` log warnings when content is missing or pages overflow.
- **Fonts:** IBM Plex (Greek-capable), in `assets/fonts/`.
- **Output:** `C:\Users\papad\Desktop\Fitness Report\fitness report - week of <Mon>.pdf`.
- **Efficiency chart:** EF = speed (km/h) ÷ HR × 100, last 365 days, outliers outside 3–11 dropped.
- After rendering, the plan is saved to `weekly_plans` (status `accepted`, `source: weekly-report`).

---

## 9. Dashboard (`start_app.bat` → http://localhost:8000)

Tabs:
- **Today:** readiness light, today's planned session from the saved plan, energy balance,
  stats.
- **Running / Cycling / Sleep / Calories:** charts, plus sync buttons and CSV import.
- **Food:** form logging from your food library; photo logging via the Claude app.
- **Goals.**
- **Calibration:** weight log; checks calorie estimates against real weight change; Katch-McArdle
  BMR from body-fat %; dynamic TDEE = Garmin burn ÷ BMR.

---

## 10. Database (PostgreSQL `garmin_coach`, schema in `backend/db.py`)

| Table | Holds |
|---|---|
| `activities` | runs + rides, May 2024 → now (~1,600 rows, **one row per lap**) |
| `sleep_nights` / `sleep_weeks` | nightly sleep, HRV, RHR / old weekly CSV summaries |
| `calories` | Garmin daily burn |
| `foods`, `food_log` | food library + daily log |
| `weight_log`, `app_settings` | Calibration tab |
| `goals` | race goals; `notes` starts with `RACE` (taper) or `CHECKPOINT` (no taper) |
| `weekly_plans` | Sunday-report plans (`plan_json.source = "weekly-report"`), read by Today + plan check |
| `chat_messages` | legacy, from the retired chats (unused) |

Gotcha: `db.get_recent_activities()` defaults to `LIMIT 200`. Pass a high `limit` for history.

---

## 11. Garmin sync: rules learned the hard way

- **Garmin rate-limits logins, not data calls.** Tokens are cached in `data/garmin_tokens/`.
  Never loop logins.
- **The 2026-11-03 429 incident:** a 403 was misread as an auth error, which wiped the token.
  Then about 15 logins followed and the IP was banned. Fixes now in `garmin_api.py`:
  - Only 401 counts as auth.
  - The token is never wiped.
  - A 1-hour login backoff is persisted to `data/garmin_login_backoff`.

  If it happens again, wait the ban out, then run one sync.
- Sleep comes from the JSON API. Don't scrape the sleep web page, which crashes.
  Browser scraping (Playwright) was removed on 2026-11-15.

---

## 12. Environment

- Windows 11. Python venv at `.venv\Scripts\python.exe`. The `python` on PATH is a broken Store
  stub.
- Greek run names need `PYTHONIOENCODING=utf-8` in a console.
- Requirements: fastapi, uvicorn, python-dotenv, psycopg + pool, python-multipart, openpyxl,
  garminconnect, mcp. (anthropic, playwright and pillow were removed.)
- `.env`: GARMIN_EMAIL, GARMIN_PASSWORD, GARMIN_TOKEN_DIR, DATABASE_URL. An old
  ANTHROPIC_API_KEY line there is now unused and can be deleted.
- Git: work on `main` (the 2026-11-15 restructure was merged in). Remote `origin` on GitHub.

---

## 13. History (short)

| Date | Change |
|---|---|
| 2026-05/06 | App built: CSV import, Playwright sync, SQLite → PostgreSQL, in-app Claude coach + food agent (paid API) |
| 2026-09 | Weekly schedule PDF (`weekly_schedule.py`) |
| 2026-10 | APEX weekly report on the subscription; MCP server; speed library |
| 2026-11-03 | Sync moved to the Garmin JSON API; 429 fix; daily sync task |
| 2026-11-10 | Coaching model updated: performance-first, volume build, 3 quality days (one per energy system) |
| 2026-11-14 | Report output moved to `Desktop\Fitness Report`; Calibration tab |
| 2026-11-15 | Cleanup (~1.5 GB freed, paid paths removed); `metrics.py` + new MCP tools; Monday weeks; single rules file; plan vs actual; VDOT paces; readiness light; long-run guardrail; food logging via MCP |

---

## 14. Open items

- [x] Real goals set 2026-11-15: **10k 38:00** by end Dec 2026 (checkpoint), **5k 17:40** by end
      Mar 2027 (checkpoint), **half 1:19:00** by end May 2027 (**race**, exact date TBC: update
      the goal's date once entered). `coach_snapshot.goals` shows the VDOT/seconds gap to each.
- [ ] Restart **Claude Desktop** (and start a new Claude Code session) so the new MCP tools load.
- [x] Restructure merged into `main` (2026-11-15). Not pushed to GitHub yet.
- [ ] Empty `_archive/2026-11-15/` after about a month if nothing is missed.
- Ideas not built yet:
  - 4-week block counter (automatic cutback weeks).
  - Rides counted in the run-analyst fatigue verdict (load already includes them).
  - Body-fat % 12 vs 14 confirmation.
  - Daily-deficit tile on Calibration.
