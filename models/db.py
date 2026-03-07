import sqlite3
import json
import os
import sys

# Add parent dir to path to import config
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import DB_PATH
from models.schemas import SCHEMA_SQL

def get_conn():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_conn() as conn:
        conn.executescript(SCHEMA_SQL)
        _ensure_user_profile_columns(conn)
        conn.commit()


def _column_exists(conn, table_name, column_name):
    rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    return any(r["name"] == column_name for r in rows)


def _ensure_user_profile_columns(conn):
    # Backward-compatible migration for existing databases.
    if not _column_exists(conn, "users", "age"):
        conn.execute("ALTER TABLE users ADD COLUMN age INTEGER")
    if not _column_exists(conn, "users", "height"):
        conn.execute("ALTER TABLE users ADD COLUMN height REAL")
    if not _column_exists(conn, "users", "weight"):
        conn.execute("ALTER TABLE users ADD COLUMN weight REAL")
    if not _column_exists(conn, "users", "bmi"):
        conn.execute("ALTER TABLE users ADD COLUMN bmi REAL")
    if not _column_exists(conn, "users", "bmi_status"):
        conn.execute("ALTER TABLE users ADD COLUMN bmi_status TEXT")
    if not _column_exists(conn, "users", "profile_complete"):
        conn.execute("ALTER TABLE users ADD COLUMN profile_complete INTEGER DEFAULT 0")
    if not _column_exists(conn, "users", "chronic_conditions"):
        conn.execute("ALTER TABLE users ADD COLUMN chronic_conditions TEXT DEFAULT ''")
    if not _column_exists(conn, "users", "altitude_history"):
        conn.execute("ALTER TABLE users ADD COLUMN altitude_history TEXT DEFAULT ''")
    if not _column_exists(conn, "users", "fitness_level"):
        conn.execute("ALTER TABLE users ADD COLUMN fitness_level TEXT DEFAULT '轻度运动'")
    if not _column_exists(conn, "users", "altitude_experience"):
        conn.execute("ALTER TABLE users ADD COLUMN altitude_experience TEXT DEFAULT '无'")
    if not _column_exists(conn, "users", "hai_score"):
        conn.execute("ALTER TABLE users ADD COLUMN hai_score REAL DEFAULT NULL")

def add_user(username, password, emergency_contact=""):
    with get_conn() as conn:
        try:
            cursor = conn.cursor()
            cursor.execute("INSERT INTO users (username, password, emergency_contact) VALUES (?, ?, ?)",
                           (username, password, emergency_contact))
            conn.commit()
            return cursor.lastrowid
        except sqlite3.IntegrityError:
            return None

def get_user(identifier):
    with get_conn() as conn:
        if isinstance(identifier, int):
            row = conn.execute("SELECT * FROM users WHERE id = ?", (identifier,)).fetchone()
            return dict(row) if row else None
        row = conn.execute("SELECT * FROM users WHERE username = ?", (str(identifier),)).fetchone()
        return dict(row) if row else None


def get_user_by_username(username):
    return get_user(str(username))


def get_user_by_id(user_id):
    return get_user(int(user_id))


def update_user_profile(user_id, age, height, weight, bmi, bmi_status,
                        chronic_conditions=None, fitness_level=None, altitude_experience=None,
                        altitude_history=None, hai_score=None):
    with get_conn() as conn:
        conn.execute(
            """
            UPDATE users
            SET age = ?, height = ?, weight = ?, bmi = ?, bmi_status = ?, profile_complete = 1,
                chronic_conditions = COALESCE(?, chronic_conditions),
                fitness_level = COALESCE(?, fitness_level),
                altitude_experience = COALESCE(?, altitude_experience),
                altitude_history = COALESCE(?, altitude_history),
                hai_score = COALESCE(?, hai_score)
            WHERE id = ?
            """,
            (int(age), float(height), float(weight), float(bmi), str(bmi_status),
             chronic_conditions, fitness_level, altitude_experience, altitude_history, hai_score, int(user_id)),
        )
        conn.commit()

def create_adventure(user_id, destination, start_time=None, start_date=None):
    """
    Create an adventure record.

    Args:
        user_id: int
        destination: str
        start_time: optional start time (preferred). Can be datetime/date/str.
        start_date: legacy alias kept for backward compatibility.
    """
    with get_conn() as conn:
        cursor = conn.cursor()
        start_value = start_time if start_time is not None else start_date
        if start_value is not None:
            cursor.execute(
                "INSERT INTO adventures (user_id, destination, start_time, status) VALUES (?, ?, ?, 'planning')",
                (user_id, destination, str(start_value)),
            )
        else:
            cursor.execute(
                "INSERT INTO adventures (user_id, destination, status) VALUES (?, ?, 'planning')",
                (user_id, destination),
            )
        conn.commit()
        return cursor.lastrowid

def update_adventure_status(adventure_id, status, end_time=None):
    with get_conn() as conn:
        if status == 'archived':
            if end_time is None:
                conn.execute(
                    "UPDATE adventures SET status = ?, end_time = CURRENT_TIMESTAMP WHERE id = ?",
                    (status, adventure_id),
                )
            else:
                conn.execute(
                    "UPDATE adventures SET status = ?, end_time = ? WHERE id = ?",
                    (status, str(end_time), adventure_id),
                )
        else:
            conn.execute("UPDATE adventures SET status = ? WHERE id = ?", (status, adventure_id))
        conn.commit()

def get_current_adventure(user_id):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM adventures WHERE user_id = ? AND status = 'ongoing' ORDER BY id DESC LIMIT 1", (user_id,)).fetchone()
        return dict(row) if row else None

def get_ongoing_adventure(user_id):
    return get_current_adventure(user_id)

def get_adventures(user_id, status=None):
    with get_conn() as conn:
        if status:
            rows = conn.execute("SELECT * FROM adventures WHERE user_id = ? AND status = ? ORDER BY id DESC", (user_id, status)).fetchall()
            return [dict(row) for row in rows] if rows else []
        rows = conn.execute("SELECT * FROM adventures WHERE user_id = ? ORDER BY id DESC", (user_id,)).fetchall()
        return [dict(row) for row in rows] if rows else []

def get_user_adventures(user_id, status=None):
    return get_adventures(user_id, status=status)

def get_adventure_by_id(adventure_id):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM adventures WHERE id = ?", (adventure_id,)).fetchone()
        return dict(row) if row else None

def get_latest_adventure(user_id, status):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM adventures WHERE user_id = ? AND status = ? ORDER BY id DESC LIMIT 1",
            (user_id, status),
        ).fetchone()
        return dict(row) if row else None

def add_vitals(adventure_id, hr, spo2, temp, lat, lon, risk_score):
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO vitals (adventure_id, hr, spo2, temp, lat, lon, risk_score)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (adventure_id, hr, spo2, temp, lat, lon, risk_score))
        conn.commit()

def get_vitals_by_adventure(adventure_id):
    with get_conn() as conn:
        return conn.execute("SELECT * FROM vitals WHERE adventure_id = ? ORDER BY ts ASC", (adventure_id,)).fetchall()

def save_report(adventure_id, report_type, content):
    with get_conn() as conn:
        content_str = json.dumps(content) if isinstance(content, dict) else str(content)
        conn.execute("INSERT INTO reports (adventure_id, type, content) VALUES (?, ?, ?)",
                     (adventure_id, report_type, content_str))
        conn.commit()

def get_reports(adventure_id, report_type=None):
    with get_conn() as conn:
        if report_type:
            return conn.execute("SELECT * FROM reports WHERE adventure_id = ? AND type = ? ORDER BY generated_at DESC", (adventure_id, report_type)).fetchall()
        return conn.execute("SELECT * FROM reports WHERE adventure_id = ? ORDER BY generated_at DESC", (adventure_id,)).fetchall()
