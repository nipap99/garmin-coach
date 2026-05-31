# Garmin Coach

A **locally-hosted, AI-powered running coach.** It pulls your activities from Garmin Connect, stores them in PostgreSQL, and lets an AI coach (Anthropic's Claude) analyze your training and propose workouts toward your race goals — all running on your own machine, with your data staying local.

> Built as a personal project by a runner who wanted automated coaching from their own training data — owned locally, not locked inside a third-party app.

---

## Features

- **Sync from Garmin Connect** — fetches your runs automatically via the [`python-garminconnect`](https://github.com/cyberjunky/python-garminconnect) library, with session-token caching so you don't re-authenticate every time.
- **CSV import fallback** — import a standard Garmin Connect CSV export when you'd rather not use the API.
- **Smart de-duplication** — re-syncing or re-importing the same run never creates duplicates (enforced at the database level, not in fragile app code).
- **Activity dashboard** — pace, heart rate, distance, VO2max and more for every run.
- **Goals** — set target distances (5k / 10k / half / marathon) with optional target times and dates.
- **AI coach chat** — ask Claude about your training. The coach uses *tool-use* to query your activity database directly, so its advice is grounded in your real data rather than guesses.
- **No build step on the frontend** — a single HTMX page, no JavaScript bundler.

---

## Tech stack

| Layer | Choice |
|---|---|
| Backend | Python + [FastAPI](https://fastapi.tiangolo.com/) |
| Database | [PostgreSQL](https://www.postgresql.org/) via [psycopg 3](https://www.psycopg.org/psycopg3/) + a connection pool |
| Frontend | [HTMX](https://htmx.org/) (server-rendered HTML fragments, no JS build) |
| AI coach | [Anthropic Claude API](https://docs.anthropic.com/) with tool-use |
| Garmin data | [`python-garminconnect`](https://github.com/cyberjunky/python-garminconnect) (unofficial) |

---

## How it works (data flow)

```
   Browser (HTMX)
        |  HTTP request  (e.g. GET /activities)
        v
   FastAPI route         backend/routes/*.py        — knows about the web
        |  calls a function
        v
   db.py                 query functions            — knows about the database
        |  borrows a pooled connection
        v
   Connection pool       psycopg_pool.ConnectionPool — reuses live connections
        |  SQL over TCP (port 5432, authenticates as the "coach" role)
        v
   PostgreSQL server      database "garmin_coach"    — runs SQL, reads/writes disk
        |
        '--> rows travel back up the same chain, get rendered to an HTML
             fragment, and HTMX swaps them into the page.
```

The route layer never touches SQL and the database layer never touches HTTP — all database access funnels through `backend/db.py`, which is why the storage engine can change without the rest of the app noticing.

---

## Project structure

```
garmin-coach/
├── backend/
│   ├── main.py            FastAPI entry point (mounts routes, serves the UI)
│   ├── config.py          Loads settings from .env
│   ├── db.py              PostgreSQL access (psycopg 3 + connection pool)
│   ├── garmin_client.py   python-garminconnect wrapper + token caching
│   ├── sync.py            Garmin -> database glue
│   ├── csv_importer.py    Garmin CSV export parser
│   ├── coach.py           Claude API client (tool-use, prompt caching)
│   └── routes/
│       ├── activities.py  GET /activities, POST /sync, POST /import/csv
│       ├── goals.py       GET/POST/DELETE /goals
│       └── chat.py        coach chat endpoints
├── frontend/
│   └── index.html         single-page HTMX UI
├── scripts/
│   ├── setup_postgres.sql Creates the app role + database
│   └── test_*.py          standalone smoke tests
├── .env.example           template for your local config
├── requirements.txt
└── README.md
```

---

## Getting started

### Prerequisites

- **Python 3.11+**
- **PostgreSQL 14+** running locally (this project was built against PostgreSQL 18)
- A **Garmin Connect** account (for syncing) and/or a Garmin CSV export
- An **Anthropic API key** (for the AI coach) — get one at [console.anthropic.com](https://console.anthropic.com/)

### 1. Clone and install

```bash
git clone https://github.com/<your-username>/garmin-coach.git
cd garmin-coach

python -m venv .venv
# Windows:
.venv\Scripts\Activate.ps1
# macOS/Linux:
# source .venv/bin/activate

pip install -r requirements.txt
```

### 2. Create the database

Create a PostgreSQL database and a login role for the app. The simplest way is to run the included SQL script as a Postgres superuser:

```bash
psql -U postgres -f scripts/setup_postgres.sql
```

This creates:
- a login role **`coach`** with a local development password, and
- a database **`garmin_coach`** owned by that role.

> The `coach` / password combo is a **local-only development credential** — it never touches the public internet. Still, if you like, change it in `scripts/setup_postgres.sql` and in your `.env` to whatever you prefer.

### 3. Configure your secrets

Copy the template and fill in your real values:

```bash
cp .env.example .env       # Windows: copy .env.example .env
```

Then edit `.env`:

```ini
GARMIN_EMAIL=you@example.com
GARMIN_PASSWORD=your-garmin-password
ANTHROPIC_API_KEY=sk-ant-...
DATABASE_URL=postgresql://coach:coach_local_dev@localhost:5432/garmin_coach
```

⚠️ **`.env` is git-ignored and must never be committed** — it holds your real Garmin password and API key. See [Security](#security) below.

### 4. Run

```bash
uvicorn backend.main:app --reload
```

Open **http://localhost:8000** in your browser.

The database tables are created automatically on first run.

---

## Usage

- **Sync from Garmin** — click *Sync* in the UI (or run the sync from the activities page). The first login may prompt for multi-factor auth via email.
- **Import a CSV** — export your activities from Garmin Connect and upload the CSV from the UI.
- **Set a goal** — pick a distance and (optionally) a target time/date.
- **Chat with the coach** — ask things like *"How's my pace trending?"* or *"What should this week's workouts look like?"* The coach reads your real activity history before answering.

---

## API reference

All endpoints return HTML fragments for HTMX (except `/health`):

| Method | Path | Purpose |
|---|---|---|
| `GET`  | `/` | The single-page UI |
| `GET`  | `/health` | Liveness check (`{"status": "ok"}`) |
| `GET`  | `/activities` | List recent activities |
| `POST` | `/sync` | Sync new runs from Garmin Connect |
| `POST` | `/import/csv` | Import a Garmin CSV export |
| `GET`  | `/goals` | List goals |
| `POST` | `/goals` | Create a goal |
| `DELETE` | `/goals/{id}` | Delete a goal |
| `GET`  | `/chat/history` | Chat history |
| `POST` | `/chat` | Send a message to the coach |
| `POST` | `/chat/clear` | Clear chat history |

---

## Security

This app handles real credentials, so a few rules matter when sharing the code:

- **Never commit `.env`.** It contains your Garmin password and Anthropic API key. It is already listed in `.gitignore`.
- **Never commit `data/`.** It holds your local database files and cached Garmin session tokens. Also git-ignored.
- The `coach` database password in the examples is a **local development credential only** — fine to keep local, but don't reuse it anywhere public-facing.
- Before your first push, double-check nothing sensitive is staged:
  ```bash
  git status            # .env and data/ should NOT appear
  git ls-files | grep -E "\.env$|^data/"   # should print nothing
  ```

---

## Roadmap

- [ ] Structured weekly training-plan cards (Accept / Edit / Regenerate)
- [ ] Hybrid-athlete awareness — factor in strength sessions alongside runs
- [ ] Cycling and other activity types
- [ ] Richer trend charts (VO2max, weekly volume, PR history)

---

## Disclaimer

This project uses the **unofficial** `python-garminconnect` library, which is not affiliated with or endorsed by Garmin. Garmin may change or rate-limit their endpoints at any time. Use responsibly and avoid repeated rapid login attempts. This tool is for personal use only.

The AI coach offers **advisory** suggestions based on your data — it is not a substitute for professional coaching or medical advice.

---

## License

No license file is included yet. If you want others to freely use and build on this, consider adding an [MIT License](https://choosealicense.com/licenses/mit/). Until a license is added, default copyright applies (all rights reserved).
