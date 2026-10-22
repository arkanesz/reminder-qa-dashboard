import json
import os
import secrets
from datetime import datetime, timezone

import psycopg2
import requests
from dotenv import load_dotenv

load_dotenv()

BUCKET = "wa-followup-proof"


def get_pg_connection():
    return psycopg2.connect(
        host=os.environ["SUPAHOST"],
        port=os.environ["SUPAPORT"],
        dbname=os.environ["SUPADATABASE"],
        user=os.environ["SUPAUSER"],
        password=os.environ["SUPAPASSWORD"],
        connect_timeout=10,
    )


def init_db():
    conn = get_pg_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS qadash_activated_users (
                    email TEXT PRIMARY KEY,
                    name TEXT,
                    activated BOOLEAN NOT NULL DEFAULT FALSE,
                    activated_by TEXT,
                    activated_at TIMESTAMPTZ
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS qadash_followup_proof (
                    id SERIAL PRIMARY KEY,
                    issue_code TEXT NOT NULL,
                    storage_path TEXT,
                    whatsapp_number TEXT,
                    note TEXT,
                    uploaded_by TEXT,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS qadash_sessions (
                    token TEXT PRIMARY KEY,
                    email TEXT NOT NULL,
                    name TEXT,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS qadash_reporter_phone (
                    reporter_name TEXT PRIMARY KEY,
                    phone TEXT NOT NULL,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS qadash_wa_log (
                    id SERIAL PRIMARY KEY,
                    issue_code TEXT NOT NULL,
                    reporter TEXT,
                    phone TEXT,
                    message TEXT,
                    sent_by TEXT,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )
        conn.commit()
    finally:
        conn.close()


def log_wa_sent(issue_code, reporter, phone, message, sent_by):
    conn = get_pg_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO qadash_wa_log (issue_code, reporter, phone, message, sent_by)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (issue_code, reporter, phone, message, sent_by),
            )
        conn.commit()
    finally:
        conn.close()


def get_wa_log_batch(issue_codes):
    issue_codes = list(issue_codes)
    if not issue_codes:
        return {}
    conn = get_pg_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT issue_code, reporter, phone, message, sent_by, created_at
                FROM qadash_wa_log
                WHERE issue_code = ANY(%s)
                ORDER BY created_at DESC
                """,
                (issue_codes,),
            )
            rows = cur.fetchall()
    finally:
        conn.close()

    result = {}
    for issue_code, reporter, phone, message, sent_by, created_at in rows:
        result.setdefault(issue_code, []).append(
            (reporter, phone, message, sent_by, created_at)
        )
    return result


def get_all_reporter_phones():
    conn = get_pg_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT reporter_name, phone FROM qadash_reporter_phone")
            return dict(cur.fetchall())
    finally:
        conn.close()


def save_reporter_phone(reporter_name, phone):
    conn = get_pg_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO qadash_reporter_phone (reporter_name, phone, updated_at)
                VALUES (%s, %s, now())
                ON CONFLICT (reporter_name) DO UPDATE SET
                    phone = EXCLUDED.phone,
                    updated_at = EXCLUDED.updated_at
                """,
                (reporter_name, phone),
            )
        conn.commit()
    finally:
        conn.close()


def create_session(email, name):
    token = secrets.token_urlsafe(32)
    conn = get_pg_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO qadash_sessions (token, email, name) VALUES (%s, %s, %s)",
                (token, email, name),
            )
        conn.commit()
    finally:
        conn.close()
    return token


def get_session(token):
    if not token:
        return None
    conn = get_pg_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT email, name FROM qadash_sessions WHERE token=%s", (token,)
            )
            return cur.fetchone()
    finally:
        conn.close()


def delete_session(token):
    if not token:
        return
    conn = get_pg_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM qadash_sessions WHERE token=%s", (token,))
        conn.commit()
    finally:
        conn.close()


def is_activated(email):
    conn = get_pg_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT activated FROM qadash_activated_users WHERE email=%s", (email,)
            )
            row = cur.fetchone()
            return bool(row and row[0])
    finally:
        conn.close()


def set_activation(email, name, activated, admin_email):
    conn = get_pg_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO qadash_activated_users (email, name, activated, activated_by, activated_at)
                VALUES (%s, %s, %s, %s, now())
                ON CONFLICT (email) DO UPDATE SET
                    name = EXCLUDED.name,
                    activated = EXCLUDED.activated,
                    activated_by = EXCLUDED.activated_by,
                    activated_at = EXCLUDED.activated_at
                """,
                (email, name, activated, admin_email),
            )
        conn.commit()
    finally:
        conn.close()


def list_activations():
    conn = get_pg_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT email, name, activated FROM qadash_activated_users ORDER BY name"
            )
            return cur.fetchall()
    finally:
        conn.close()


def add_followup(issue_code, storage_path, whatsapp_number, note, uploaded_by):
    conn = get_pg_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO qadash_followup_proof
                    (issue_code, storage_path, whatsapp_number, note, uploaded_by)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (issue_code, storage_path, whatsapp_number, note, uploaded_by),
            )
        conn.commit()
    finally:
        conn.close()


def get_followups(issue_code):
    conn = get_pg_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, storage_path, whatsapp_number, note, uploaded_by, created_at
                FROM qadash_followup_proof
                WHERE issue_code=%s
                ORDER BY created_at DESC
                """,
                (issue_code,),
            )
            return cur.fetchall()
    finally:
        conn.close()


def get_followups_batch(issue_codes):
    issue_codes = list(issue_codes)
    if not issue_codes:
        return {}
    conn = get_pg_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, issue_code, storage_path, whatsapp_number, note, uploaded_by, created_at
                FROM qadash_followup_proof
                WHERE issue_code = ANY(%s)
                ORDER BY created_at DESC
                """,
                (issue_codes,),
            )
            rows = cur.fetchall()
    finally:
        conn.close()

    result = {}
    for fid, issue_code, storage_path, wa_number, note, uploaded_by, created_at in rows:
        result.setdefault(issue_code, []).append(
            (fid, storage_path, wa_number, note, uploaded_by, created_at)
        )
    return result


def get_issue_codes_with_followups():
    conn = get_pg_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT issue_code, MAX(created_at) AS latest FROM (
                    SELECT issue_code, created_at FROM qadash_followup_proof
                    UNION ALL
                    SELECT issue_code, created_at FROM qadash_wa_log
                ) combined
                GROUP BY issue_code
                ORDER BY latest DESC
                """
            )
            return [row[0] for row in cur.fetchall()]
    finally:
        conn.close()


def delete_followup_record(followup_id):
    conn = get_pg_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM qadash_followup_proof WHERE id=%s", (followup_id,)
            )
        conn.commit()
    finally:
        conn.close()


# --- Storage (Supabase Storage REST API) ---


def _storage_headers(content_type=None):
    headers = {"Authorization": f"Bearer {os.environ['SUPABASE_SERVICE_KEY']}"}
    if content_type:
        headers["Content-Type"] = content_type
    return headers


def upload_screenshot(issue_code, filename, file_bytes, content_type):
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    safe_name = filename.replace(" ", "_")
    path = f"{issue_code}/{ts}_{safe_name}"
    url = f"{os.environ['SUPABASE_URL']}/storage/v1/object/{BUCKET}/{path}"
    resp = requests.post(
        url, headers=_storage_headers(content_type), data=bytes(file_bytes), timeout=30
    )
    resp.raise_for_status()
    return path


def get_signed_url(storage_path, expires_in=3600):
    url = f"{os.environ['SUPABASE_URL']}/storage/v1/object/sign/{BUCKET}/{storage_path}"
    resp = requests.post(
        url,
        headers=_storage_headers("application/json"),
        data=json.dumps({"expiresIn": expires_in}),
        timeout=15,
    )
    resp.raise_for_status()
    signed = resp.json()["signedURL"]
    return f"{os.environ['SUPABASE_URL']}/storage/v1{signed}"


def get_signed_urls_batch(storage_paths, expires_in=3600):
    paths = [p for p in storage_paths if p]
    if not paths:
        return {}
    url = f"{os.environ['SUPABASE_URL']}/storage/v1/object/sign/{BUCKET}"
    resp = requests.post(
        url,
        headers=_storage_headers("application/json"),
        data=json.dumps({"expiresIn": expires_in, "paths": paths}),
        timeout=20,
    )
    resp.raise_for_status()
    result = {}
    for item in resp.json():
        signed = item.get("signedURL")
        path = item.get("path")
        if signed and path:
            result[path] = f"{os.environ['SUPABASE_URL']}/storage/v1{signed}"
    return result


def delete_screenshot(storage_path):
    if not storage_path:
        return
    url = f"{os.environ['SUPABASE_URL']}/storage/v1/object/{BUCKET}/{storage_path}"
    resp = requests.delete(url, headers=_storage_headers(), timeout=15)
    resp.raise_for_status()


def delete_followup(followup_id, storage_path):
    delete_screenshot(storage_path)
    delete_followup_record(followup_id)
