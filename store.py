"""SQLite ledger: which recordings were processed, and mentor -> playlist routing."""
import sqlite3
import threading
from datetime import datetime, timezone

import config

_lock = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS mentors (
    mentor_key   TEXT PRIMARY KEY,   -- lower-cased Zoho host email (or host name)
    display_name TEXT NOT NULL,
    class_name   TEXT NOT NULL,
    playlist_id  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS recordings (
    erecording_id TEXT PRIMARY KEY,
    topic         TEXT,
    mentor_key    TEXT,
    recorded_at   TEXT,
    status        TEXT NOT NULL,     -- pending | needs_mapping | uploaded | done | failed
    video_id      TEXT,
    playlist_id   TEXT,
    attempts      INTEGER NOT NULL DEFAULT 0,
    last_error    TEXT,
    updated_at    TEXT NOT NULL
);
"""


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(path=None):
    path = path or config.DB_PATH
    if str(path) != ":memory:":
        config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


# ---- mentors -------------------------------------------------------------

def list_mentors(conn):
    return conn.execute("SELECT * FROM mentors ORDER BY display_name").fetchall()


def upsert_mentor(conn, mentor_key, display_name, class_name, playlist_id):
    with _lock, conn:
        conn.execute(
            "INSERT INTO mentors VALUES (?, ?, ?, ?) ON CONFLICT(mentor_key) DO UPDATE SET "
            "display_name=excluded.display_name, class_name=excluded.class_name, "
            "playlist_id=excluded.playlist_id",
            (mentor_key.strip().lower(), display_name.strip(), class_name.strip(), playlist_id.strip()),
        )
        # Recordings that were waiting for this mentor can now be processed.
        conn.execute(
            "UPDATE recordings SET status='pending', updated_at=? "
            "WHERE status='needs_mapping' AND mentor_key=?",
            (_now(), mentor_key.strip().lower()),
        )


def delete_mentor(conn, mentor_key):
    with _lock, conn:
        conn.execute("DELETE FROM mentors WHERE mentor_key=?", (mentor_key,))


def find_mentor(conn, candidate_keys):
    for key in candidate_keys:
        row = conn.execute("SELECT * FROM mentors WHERE mentor_key=?", (key,)).fetchone()
        if row:
            return row
    return None


# ---- recordings ----------------------------------------------------------

def get_recording(conn, erecording_id):
    return conn.execute(
        "SELECT * FROM recordings WHERE erecording_id=?", (erecording_id,)
    ).fetchone()


def ensure_recording(conn, erecording_id, topic, mentor_key, recorded_at):
    with _lock, conn:
        conn.execute(
            "INSERT OR IGNORE INTO recordings "
            "(erecording_id, topic, mentor_key, recorded_at, status, updated_at) "
            "VALUES (?, ?, ?, ?, 'pending', ?)",
            (erecording_id, topic, mentor_key, recorded_at, _now()),
        )
    return get_recording(conn, erecording_id)


def update_recording(conn, erecording_id, **fields):
    fields["updated_at"] = _now()
    cols = ", ".join(f"{k}=?" for k in fields)
    with _lock, conn:
        conn.execute(
            f"UPDATE recordings SET {cols} WHERE erecording_id=?",
            (*fields.values(), erecording_id),
        )


def record_failure(conn, erecording_id, error):
    with _lock, conn:
        conn.execute(
            "UPDATE recordings SET attempts=attempts+1, last_error=?, updated_at=?, "
            "status=CASE WHEN attempts+1>=? THEN 'failed' ELSE status END "
            "WHERE erecording_id=?",
            (str(error)[:1000], _now(), config.MAX_ATTEMPTS, erecording_id),
        )


def retry_recording(conn, erecording_id):
    with _lock, conn:
        conn.execute(
            "UPDATE recordings SET attempts=0, last_error=NULL, updated_at=?, "
            "status=CASE WHEN video_id IS NULL THEN 'pending' ELSE 'uploaded' END "
            "WHERE erecording_id=? AND status='failed'",
            (_now(), erecording_id),
        )


def recent_recordings(conn, limit=200):
    return conn.execute(
        "SELECT r.*, m.display_name, m.class_name FROM recordings r "
        "LEFT JOIN mentors m ON m.mentor_key = r.mentor_key "
        "ORDER BY r.updated_at DESC LIMIT ?",
        (limit,),
    ).fetchall()
