import sqlite3
from contextlib import closing

DB_PATH = "dashboard_data.db"


def get_conn():
    return sqlite3.connect(DB_PATH)


def init_db():
    with closing(get_conn()) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS activated_users (
                email TEXT PRIMARY KEY,
                name TEXT,
                activated INTEGER NOT NULL DEFAULT 0,
                activated_by TEXT,
                activated_at TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS followup_proof (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                issue_code TEXT NOT NULL,
                screenshot_path TEXT,
                whatsapp_number TEXT,
                note TEXT,
                uploaded_by TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.commit()


def is_activated(email):
    with closing(get_conn()) as conn:
        row = conn.execute(
            "SELECT activated FROM activated_users WHERE email=?", (email,)
        ).fetchone()
        return bool(row and row[0])


def set_activation(email, name, activated, admin_email):
    with closing(get_conn()) as conn:
        conn.execute(
            """
            INSERT INTO activated_users (email, name, activated, activated_by, activated_at)
            VALUES (?, ?, ?, ?, datetime('now'))
            ON CONFLICT(email) DO UPDATE SET
                name=excluded.name,
                activated=excluded.activated,
                activated_by=excluded.activated_by,
                activated_at=excluded.activated_at
            """,
            (email, name, int(activated), admin_email),
        )
        conn.commit()


def list_activations():
    with closing(get_conn()) as conn:
        return conn.execute(
            "SELECT email, name, activated FROM activated_users ORDER BY name"
        ).fetchall()


def add_followup(issue_code, screenshot_path, whatsapp_number, note, uploaded_by):
    with closing(get_conn()) as conn:
        conn.execute(
            """
            INSERT INTO followup_proof
                (issue_code, screenshot_path, whatsapp_number, note, uploaded_by, created_at)
            VALUES (?, ?, ?, ?, ?, datetime('now'))
            """,
            (issue_code, screenshot_path, whatsapp_number, note, uploaded_by),
        )
        conn.commit()


def get_followups(issue_code):
    with closing(get_conn()) as conn:
        return conn.execute(
            """
            SELECT id, screenshot_path, whatsapp_number, note, uploaded_by, created_at
            FROM followup_proof
            WHERE issue_code=?
            ORDER BY created_at DESC
            """,
            (issue_code,),
        ).fetchall()
