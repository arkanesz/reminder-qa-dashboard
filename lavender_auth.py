import os

import bcrypt
import pymysql
import requests
from dotenv import load_dotenv

load_dotenv()

ADMIN_EMAIL = "faris@prestisa.com"


def get_public_ip():
    try:
        resp = requests.get("https://api.ipify.org", timeout=5)
        return resp.text.strip()
    except Exception:
        return None


def get_connection():
    return pymysql.connect(
        host=os.environ["LAVHOST"],
        port=int(os.environ.get("LAVPORT", 3306)),
        database=os.environ["LAVDATABASE"],
        user=os.environ["LAVUSER"],
        password=os.environ["LAVPASSWORD"],
        connect_timeout=10,
    )


def find_user(email):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, name, email, password FROM users WHERE email=%s", (email,)
            )
            return cur.fetchone()
    finally:
        conn.close()


def verify_password(plain_password, hashed):
    try:
        return bcrypt.checkpw(plain_password.encode("utf-8"), hashed.encode("utf-8"))
    except ValueError:
        return False


def search_users(query, limit=50):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, name, email FROM users
                WHERE name LIKE %s OR email LIKE %s
                ORDER BY name LIMIT %s
                """,
                (f"%{query}%", f"%{query}%", limit),
            )
            return cur.fetchall()
    finally:
        conn.close()


def is_admin(email):
    return email.lower() == ADMIN_EMAIL
