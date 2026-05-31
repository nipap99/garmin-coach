"""Generate a PDF development log for the Postgres migration session.

Run from the project root:
    .venv\\Scripts\\python.exe -m scripts.make_docs_pdf
"""
from __future__ import annotations

from pathlib import Path

from fpdf import FPDF
from fpdf.fonts import FontFace

ACCENT = (37, 99, 235)     # blue
DARK = (17, 24, 39)        # near-black text
GRAY = (107, 114, 128)     # muted
CODEBG = (243, 244, 246)   # light gray code background
WHITE = (255, 255, 255)

OUT = Path.home() / "Desktop" / "Garmin Coach - Session 2 (PostgreSQL Migration).pdf"


class Doc(FPDF):
    def footer(self) -> None:
        self.set_y(-15)
        self.set_font("Helvetica", "", 8)
        self.set_text_color(*GRAY)
        self.cell(
            0, 10,
            f"Garmin Coach  -  Development Log  -  Page {self.page_no()}",
            align="C",
        )


def h1(pdf: Doc, text: str) -> None:
    pdf.ln(3)
    pdf.set_font("Helvetica", "B", 15)
    pdf.set_text_color(*ACCENT)
    pdf.multi_cell(0, 8, text)
    pdf.set_draw_color(*ACCENT)
    pdf.set_line_width(0.5)
    y = pdf.get_y() + 1
    pdf.line(pdf.l_margin, y, pdf.w - pdf.r_margin, y)
    pdf.ln(4)


def h2(pdf: Doc, text: str) -> None:
    pdf.ln(2)
    pdf.set_font("Helvetica", "B", 12)
    pdf.set_text_color(*DARK)
    pdf.multi_cell(0, 6, text)
    pdf.ln(1)


def body(pdf: Doc, text: str) -> None:
    pdf.set_font("Helvetica", "", 11)
    pdf.set_text_color(*DARK)
    pdf.multi_cell(0, 5.6, text)
    pdf.ln(1.5)


def bullet(pdf: Doc, text: str, bold_lead: str | None = None) -> None:
    pdf.set_text_color(*DARK)
    x = pdf.l_margin + 4
    pdf.set_x(x)
    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(4, 5.6, chr(149))  # bullet dot (Latin-1 0x95)
    pdf.set_x(x + 5)
    if bold_lead:
        pdf.set_font("Helvetica", "B", 11)
        pdf.write(5.6, bold_lead)
        pdf.set_font("Helvetica", "", 11)
        pdf.write(5.6, text)
        pdf.ln(5.6)
    else:
        pdf.set_font("Helvetica", "", 11)
        pdf.multi_cell(pdf.epw - 9, 5.6, text)
    pdf.ln(0.5)


def code(pdf: Doc, text: str) -> None:
    pdf.ln(1)
    pdf.set_font("Courier", "", 9)
    pdf.set_text_color(*DARK)
    pdf.set_fill_color(*CODEBG)
    pdf.multi_cell(0, 5, text, fill=True, border=0)
    pdf.ln(2)


def table(pdf: Doc, headings: list[str], rows: list[list[str]], widths) -> None:
    pdf.set_font("Helvetica", "", 9.5)
    pdf.set_text_color(*DARK)
    pdf.set_draw_color(*(229, 231, 235))
    head_style = FontFace(emphasis="BOLD", color=WHITE, fill_color=ACCENT)
    with pdf.table(
        col_widths=widths,
        first_row_as_headings=True,
        headings_style=head_style,
        line_height=5.5,
        cell_fill_color=(249, 250, 251),
        cell_fill_mode="ROWS",
    ) as t:
        t.row(headings)
        for r in rows:
            t.row(r)
    pdf.ln(3)


def build() -> Path:
    pdf = Doc(orientation="P", unit="mm", format="A4")
    pdf.set_margins(18, 18, 18)
    pdf.set_auto_page_break(auto=True, margin=20)
    pdf.add_page()

    # ---- Title block ----
    pdf.set_fill_color(*ACCENT)
    pdf.rect(0, 0, pdf.w, 34, style="F")
    pdf.set_xy(18, 9)
    pdf.set_font("Helvetica", "B", 20)
    pdf.set_text_color(*WHITE)
    pdf.cell(0, 9, "Garmin Coach - Development Log")
    pdf.set_xy(18, 20)
    pdf.set_font("Helvetica", "", 12)
    pdf.cell(0, 7, "Session 2: Migrating from SQLite to PostgreSQL")
    pdf.ln(24)
    pdf.set_font("Helvetica", "", 10)
    pdf.set_text_color(*GRAY)
    pdf.multi_cell(0, 5, "Date: 31 May 2026    |    Personal project: a local running-coach app\n"
                          "Prepared as a plain-language record of what was changed, why, and the tools used.")
    pdf.ln(3)

    # ---- 1. Summary ----
    h1(pdf, "1. Summary (the short version)")
    body(pdf, "Today we moved the app's data storage from SQLite (a single file) to "
              "PostgreSQL (a real database server) - mainly as a learning exercise. While "
              "doing that, we also fixed a bug that had been silently duplicating your "
              "activity data every time a CSV was re-imported.")
    bullet(pdf, "Your app now runs on PostgreSQL 18 instead of a SQLite file.")
    bullet(pdf, "Your data was cleaned up: 64 messy rows (24 real runs that had been "
                "tripled) became 24 correct runs, plus your goal and chat history.")
    bullet(pdf, "Re-importing a CSV can no longer create duplicates - the database itself "
                "now enforces 'one run per minute'.")
    bullet(pdf, "Nothing about how you use the app changed - same screens, same buttons.")

    # ---- 2. Goals ----
    h1(pdf, "2. What we set out to do")
    bullet(pdf, "Move the data into PostgreSQL.", bold_lead="Primary goal: ")
    body(pdf, "    You asked for this specifically to get hands-on experience with a real "
              "database - connections, a schema, a server process, and a GUI to inspect data.")
    bullet(pdf, "Fix the duplicated-data problem.", bold_lead="Secondary goal: ")
    body(pdf, "    Re-importing the same CSV had doubled (actually tripled) your runs. This "
              "needed fixing regardless of which database we used.")

    # ---- 3. Why ----
    h1(pdf, "3. Why we did it this way")
    body(pdf, "An honest note: for a single-user app on your own laptop, SQLite was already "
              "a perfectly good choice. We moved to PostgreSQL because your goal is to learn "
              "it - not because anything was broken. That distinction matters, so you know "
              "the trade-off you chose.")
    body(pdf, "Key design decisions:")
    bullet(pdf, "We used a dedicated database login ('coach') instead of the all-powerful "
                "master account. Apps should never run as the superuser - that is standard "
                "good practice.")
    bullet(pdf, "We isolated all database code in one file (backend/db.py). Because every "
                "other part of the app talks to the database only through that file, swapping "
                "SQLite for Postgres touched almost nothing else.")
    bullet(pdf, "We fixed deduplication at the database level (a UNIQUE rule the database "
                "enforces) rather than in application code - so the rule can never be "
                "accidentally bypassed.")

    # ---- 4. Step by step ----
    h1(pdf, "4. What we did, step by step")

    h2(pdf, "Phase 1 - Getting PostgreSQL ready")
    bullet(pdf, "Discovered PostgreSQL 18 was already installed and running on your machine "
                "(Windows service 'postgresql-x64-18', port 5432), along with pgAdmin 4.")
    bullet(pdf, "We needed to create a database for the app and a login for it. The master "
                "'postgres' password was not remembered.")
    bullet(pdf, "Rather than guess, we used the official password-recovery technique: "
                "temporarily allow password-free local logins, create what we needed, then "
                "immediately restore security. This was automated in a script with a safety "
                "net that always restores the secure settings, even if something fails.")
    bullet(pdf, "Result: a login role named 'coach' and a database named 'garmin_coach'.")

    h2(pdf, "Phase 2 - Teaching the app to speak PostgreSQL")
    bullet(pdf, "Rewrote backend/db.py to use 'psycopg' (the modern Python-to-Postgres "
                "driver) with a connection pool, instead of Python's built-in sqlite3.")
    bullet(pdf, "Added the connection string (DATABASE_URL) to config.py and the .env file, "
                "and added psycopg to requirements.txt.")
    bullet(pdf, "Small but important code differences from SQLite: parameters are written as "
                "%s instead of ?; new row IDs are returned with 'RETURNING id' instead of "
                "lastrowid; and the original activity payloads are stored as JSONB "
                "(Postgres's native JSON type).")

    h2(pdf, "Phase 3 - Fixing the duplicate-data bug")
    body(pdf, "Root cause: the old system decided whether two rows were 'the same run' by "
              "hashing the date, distance and duration together. Different CSV exports (and "
              "Garmin-vs-CSV) produced slightly different numbers, so the hashes differed, so "
              "the system thought they were different runs and stored copies.")
    body(pdf, "The fix: identify a run by its start time trimmed to the minute. We added a "
              "'start_minute' column with a UNIQUE constraint, and inserts now update the "
              "existing row on conflict instead of adding a duplicate. We also kept the real "
              "Garmin ID over a synthetic one, and preserved richer data (like VO2max) when a "
              "sparser import arrives later.")

    h2(pdf, "Phase 4 - Migrating your data and verifying")
    bullet(pdf, "Your old SQLite file held 64 activity rows - which were really 24 runs, each "
                "stored 2-3 times. 24 of those rows had genuine Garmin IDs (the authoritative "
                "copies).")
    bullet(pdf, "A one-time migration script copied only those 24 clean runs - plus your 1 "
                "goal and 4 chat messages - into PostgreSQL, dropping the 40 duplicate rows.")
    bullet(pdf, "Verified three ways: the count held at exactly 24; a simulated re-import of "
                "the same run did NOT create a duplicate; and your browser showed the clean "
                "list served live from Postgres.")

    # ---- 5. The bug, before/after ----
    h1(pdf, "5. The duplicate bug: before and after")
    table(
        pdf,
        ["", "Before", "After"],
        [
            ["How a run is identified",
             "Hash of date + distance + duration",
             "Start time to the minute (UNIQUE)"],
            ["Re-importing a CSV",
             "Added duplicate rows",
             "Updates the existing row"],
            ["Rows in your data",
             "64 (24 runs tripled)",
             "24 (one per run)"],
            ["Enforced by",
             "Application code (fragile)",
             "The database itself (reliable)"],
        ],
        widths=(40, 38, 38),
    )

    # ---- 6. Tools ----
    h1(pdf, "6. Tools and technologies used")
    table(
        pdf,
        ["Tool", "What it is / why we used it"],
        [
            ["PostgreSQL 18", "The new database server that stores all your data."],
            ["psycopg (v3)", "Python library that lets the app talk to PostgreSQL."],
            ["psycopg_pool", "Keeps a few database connections open and reuses them (faster)."],
            ["pgAdmin 4", "Graphical tool to browse and query the database by hand."],
            ["psql", "Command-line tool for running SQL against the database."],
            ["FastAPI", "The web framework serving the app (unchanged today)."],
            ["HTMX", "Powers the web page interactivity (unchanged today)."],
            ["Python 3.11 + venv", "The language and isolated environment the app runs in."],
            ["PowerShell", "Used to run the Windows setup scripts."],
            ["SQLite", "The OLD database we migrated away from (file kept as a backup)."],
            ["fpdf2", "The library that generated this PDF."],
        ],
        widths=(38, 78),
    )

    # ---- 7. Files ----
    h1(pdf, "7. Files created or changed")
    table(
        pdf,
        ["File", "Change"],
        [
            ["backend/db.py", "Rewritten for PostgreSQL; contains the dedup fix."],
            ["backend/config.py", "Added DATABASE_URL setting."],
            [".env", "Added the DATABASE_URL connection string."],
            ["requirements.txt", "Added the psycopg dependency."],
            ["scripts/setup_postgres.sql", "Creates the 'coach' login + 'garmin_coach' DB."],
            ["scripts/reset_pg_and_setup.ps1", "Safe one-time DB bootstrap (auto-restores security)."],
            ["scripts/migrate_sqlite_to_pg.py", "One-time copy of clean data into Postgres."],
            ["scripts/make_docs_pdf.py", "Generates this document."],
            ["data/coach.db", "Old SQLite database - left untouched as a backup."],
        ],
        widths=(58, 58),
    )

    # ---- 8. How to run ----
    h1(pdf, "8. How to run the app")
    body(pdf, "PostgreSQL starts automatically with Windows. To start the app, open "
              "PowerShell, go to the project folder, and run the server:")
    code(pdf, "cd C:\\Users\\papad\\Desktop\\garmin-coach\n"
              ".\\.venv\\Scripts\\python.exe -m uvicorn backend.main:app --reload")
    body(pdf, "Then open http://localhost:8000 in your browser. Leave the PowerShell window "
              "open while using the app; press Ctrl+C in it to stop the server.")

    # ---- 9. Glossary ----
    h1(pdf, "9. Glossary (plain language)")
    glossary = [
        ("Database server", "A program that stores data and answers questions about it. "
         "PostgreSQL is one; it runs in the background and many programs can talk to it."),
        ("SQLite vs PostgreSQL", "SQLite is a single file - simple, great for one user. "
         "PostgreSQL is a full server - more setup, but the 'real' option for bigger or "
         "shared systems."),
        ("Role / login", "A database user account. Ours is called 'coach'."),
        ("Connection pool", "A small set of ready-to-use connections the app reuses, instead "
         "of opening a fresh one for every query."),
        ("Upsert", "Insert a row, but if it already exists, update it instead. One operation."),
        ("ON CONFLICT", "The PostgreSQL feature that makes upserts work - it says what to do "
         "when an inserted row clashes with an existing one."),
        ("UNIQUE constraint", "A rule the database enforces saying a value can appear only "
         "once - this is what now blocks duplicate runs."),
        ("Generated column", "A column the database fills in automatically from other "
         "columns. Ours ('start_minute') is the start time trimmed to the minute."),
        ("JSONB", "PostgreSQL's native format for storing JSON data efficiently."),
    ]
    for term, desc in glossary:
        bullet(pdf, desc, bold_lead=f"{term}: ")

    # ---- 10. Next ----
    h1(pdf, "10. What's next")
    bullet(pdf, "Learn to query your own data in pgAdmin (the immediate next step).")
    bullet(pdf, "Optionally continue building app features: the AI coach, weekly training "
                "plans, and eventually cross-training/gym awareness.")

    pdf.output(str(OUT))
    return OUT


if __name__ == "__main__":
    out = build()
    print(f"Wrote: {out}")
