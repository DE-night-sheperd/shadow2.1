"""
Persistent memory for Shadow Core.

Two kinds of memory:
  - facts: durable key/value style notes the agent decides are worth keeping
           ("user prefers tabs over spaces", "project X lives in ~/dev/x", ...)
  - session_summaries: a short summary generated at the end of each session,
           so the next session can recall "what we were doing" without
           replaying the full transcript.

This intentionally stores plain text only -- no screenshots, no credentials.
"""
import sqlite3
import time
import os

DIR = os.path.dirname(os.path.realpath(__file__))
DATA_DIR = os.path.join(DIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)
DB_PATH = os.path.join(DATA_DIR, "memory.db")


def _connect():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS facts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            key TEXT NOT NULL,
            value TEXT NOT NULL,
            created_at REAL NOT NULL,
            UNIQUE(key)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS session_summaries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            summary TEXT NOT NULL,
            created_at REAL NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS action_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT NOT NULL,      -- 'watcher_fs', 'watcher_screen', 'goal', 'manual'
            description TEXT NOT NULL,
            created_at REAL NOT NULL
        )
    """)
    return conn


def remember_fact(key, value):
    conn = _connect()
    conn.execute(
        "INSERT INTO facts(key, value, created_at) VALUES (?, ?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value, created_at=excluded.created_at",
        (key.strip(), value.strip(), time.time()),
    )
    conn.commit()
    conn.close()


def recall_facts(limit=25):
    conn = _connect()
    rows = conn.execute(
        "SELECT key, value FROM facts ORDER BY created_at DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    return rows


def save_session_summary(session_id, summary):
    conn = _connect()
    conn.execute(
        "INSERT INTO session_summaries(session_id, summary, created_at) VALUES (?, ?, ?)",
        (session_id, summary.strip(), time.time()),
    )
    conn.commit()
    conn.close()


def recall_recent_summaries(limit=5):
    conn = _connect()
    rows = conn.execute(
        "SELECT session_id, summary FROM session_summaries ORDER BY created_at DESC LIMIT ?",
        (limit,),
    ).fetchall()
    conn.close()
    return rows


def log_action_event(source, description):
    conn = _connect()
    conn.execute(
        "INSERT INTO action_events(source, description, created_at) VALUES (?, ?, ?)",
        (source, description.strip(), time.time()),
    )
    conn.commit()
    conn.close()


def build_memory_context(max_chars=1500):
    """Render recent facts + summaries into a short block to prepend to the system prompt."""
    facts = recall_facts()
    summaries = recall_recent_summaries()
    lines = []
    if facts:
        lines.append("Known facts about the user/environment:")
        for k, v in facts:
            lines.append(f"- {k}: {v}")
    if summaries:
        lines.append("Recent session summaries:")
        for sid, s in summaries:
            lines.append(f"- [{sid}] {s}")
    text = "\n".join(lines)
    return text[:max_chars]
