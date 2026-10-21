import os

import psycopg2
import requests
from dotenv import load_dotenv

load_dotenv()


def get_db_connection():
    return psycopg2.connect(
        host=os.environ["PGHOST"],
        port=os.environ["PGPORT"],
        dbname=os.environ["PGDATABASE"],
        user=os.environ["PGUSER"],
        password=os.environ["PGPASSWORD"],
    )


def run_query(query, params=None):
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(query, params)
            rows = cur.fetchall()
    finally:
        conn.close()
    return rows


def send_telegram(message):
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat_id = os.environ["TELEGRAM_CHAT_ID"]
    url = f"https://api.telegram.org/bot{token}/sendMessage"

    # Telegram caps messages at 4096 chars; split on blank-line boundaries if needed.
    chunks = []
    current = ""
    for block in message.split("\n\n"):
        candidate = f"{current}\n\n{block}" if current else block
        if len(candidate) > 4000:
            chunks.append(current)
            current = block
        else:
            current = candidate
    if current:
        chunks.append(current)

    for chunk in chunks:
        resp = requests.post(
            url,
            data={
                "chat_id": chat_id,
                "text": chunk,
                "disable_web_page_preview": True,
            },
            timeout=15,
        )
        resp.raise_for_status()
