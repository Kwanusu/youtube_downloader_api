"""SQLite ledger: cohort -> YouTube playlist, and per-recording progress."""
import sqlite3
import threading
from datetime import datetime, timezone

import config

_lock = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS cohorts (
    cohort_id   INTEGER PRIMARY KEY,  -- LMS cohort id
    name        TEXT NOT NULL,
    type        TEXT NOT NULL,        -- full-time | part-time
    playlist_id TEXT                  -- created on first upload, or set by an admin
);
CREATE TABLE IF NOT EXISTS recordings (
    erecording_id TEXT PRIMARY KEY,
    topic         TEXT,
    mentor_key    TEXT,               -- Zoho host (e-mail or name)
    recorded_at   TEXT,
    status        TEXT NOT NULL,      -- pending | needs_review | uploaded | done | failed
    video_id      TEXT,
    playlist_id   TEXT,
    attempts      INTEGER NOT NULL DEFAULT 0,
    last_error    TEXT,
    updated_at    TEXT NOT NULL
);
"""

# Columns added for the LMS integration; ALTERed into older databases.
RECORDING_COLUMNS = {
    "cohort_id": "INTEGER",
    "session_id": "INTEGER",
    "match_reason": "TEXT",           # how the cohort was chosen, or 'manual'
    "lms_recording_id": "TEXT",       # set once the link is posted to the LMS session
}


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(path=None):
    path = path or config.DB_PATH
    if str(path) != ":memory:":
        config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    _migrate(conn)
    return conn


def _migrate(conn):
    existing = {row["name"] for row in conn.execute("PRAGMA table_info(recordings)")}
    with conn:
        for column, kind in RECORDING_COLUMNS.items():
            if column not in existing:
                conn.execute(f"ALTER TABLE recordings ADD COLUMN {column} {kind}")
        conn.execute("UPDATE recordings SET status='needs_review' WHERE status='needs_mapping'")


# ---- cohorts -------------------------------------------------------------

def list_cohorts(conn):
    return conn.execute("SELECT * FROM cohorts ORDER BY type, name").fetchall()


def get_cohort(conn, cohort_id):
    return conn.execute("SELECT * FROM cohorts WHERE cohort_id=?", (cohort_id,)).fetchone()


def upsert_cohort(conn, cohort_id, name, cohort_type):
    """Record a cohort seen in the LMS, keeping any playlist already assigned."""
    with _lock, conn:
        conn.execute(
            "INSERT INTO cohorts (cohort_id, name, type) VALUES (?, ?, ?) "
            "ON CONFLICT(cohort_id) DO UPDATE SET name=excluded.name, type=excluded.type",
            (int(cohort_id), name, cohort_type),
        )


def set_cohort_playlist(conn, cohort_id, playlist_id):
    with _lock, conn:
        conn.execute("UPDATE cohorts SET playlist_id=? WHERE cohort_id=?",
                     (playlist_id.strip() or None, int(cohort_id)))


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


def assign_recording(conn, erecording_id, cohort_id, session_id=None):
    """Admin override for a recording the router could not place."""
    with _lock, conn:
        conn.execute(
            "UPDATE recordings SET cohort_id=?, session_id=?, match_reason='manual', "
            "attempts=0, last_error=NULL, updated_at=?, "
            "status=CASE WHEN video_id IS NULL THEN 'pending' ELSE 'uploaded' END "
            "WHERE erecording_id=? AND status IN ('needs_review', 'failed', 'pending')",
            (int(cohort_id), int(session_id) if session_id else None, _now(), erecording_id),
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
        "SELECT r.*, c.name AS cohort_name, c.type AS cohort_type FROM recordings r "
        "LEFT JOIN cohorts c ON c.cohort_id = r.cohort_id "
        "ORDER BY r.updated_at DESC LIMIT ?",
        (limit,),
    ).fetchall()
