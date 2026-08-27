"""
Simulated account linking + opt-in "watch this store for specials" list.

Linking here is a stand-in for what a real system would need: the user
authenticating directly with each store/provider (their own login/OAuth),
never the assistant holding or replaying credentials. See services_sim.py's
module docstring -- same rule applies here.

The watch list is deliberately empty by default and only ever grows or
shrinks from an explicit command ("watch X", "only watch X", "stop watching
X"). Nothing gets added to it automatically, and nothing polls a store that
isn't on it -- that's what keeps this from turning into notification noise.
"""
import json
import sqlite3
import time

DB_PATH = "accounts_sim.db"


def _connect():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS linked_accounts (
            store TEXT PRIMARY KEY,
            linked_at REAL NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS watched_stores (
            store TEXT PRIMARY KEY,
            since REAL NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS catalog_snapshots (
            store TEXT PRIMARY KEY,
            snapshot_json TEXT NOT NULL
        )
    """)
    return conn


# ---------------------------------------------------------------------------
# Linking
# ---------------------------------------------------------------------------

def is_linked(store):
    conn = _connect()
    row = conn.execute("SELECT 1 FROM linked_accounts WHERE store = ?", (store,)).fetchone()
    conn.close()
    return row is not None


def link_account(store):
    """Simulated account creation/linking -- no real login happens here."""
    conn = _connect()
    conn.execute(
        "INSERT OR IGNORE INTO linked_accounts (store, linked_at) VALUES (?, ?)",
        (store, time.time()),
    )
    conn.commit()
    conn.close()
    return True


def get_linked_stores():
    conn = _connect()
    rows = conn.execute("SELECT store FROM linked_accounts ORDER BY linked_at").fetchall()
    conn.close()
    return [r[0] for r in rows]


def unlink_account(store):
    conn = _connect()
    conn.execute("DELETE FROM linked_accounts WHERE store = ?", (store,))
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Watch list (opt-in specials tracking)
# ---------------------------------------------------------------------------

def get_watched_stores():
    conn = _connect()
    rows = conn.execute("SELECT store FROM watched_stores ORDER BY since").fetchall()
    conn.close()
    return [r[0] for r in rows]


def watch_store(store, exclusive=False):
    """
    Add `store` to the watch list. If exclusive=True ("only watch X"),
    every other store is dropped from the watch list first.
    """
    conn = _connect()
    if exclusive:
        conn.execute("DELETE FROM watched_stores")
    conn.execute(
        "INSERT OR IGNORE INTO watched_stores (store, since) VALUES (?, ?)",
        (store, time.time()),
    )
    conn.commit()
    conn.close()


def unwatch_store(store):
    conn = _connect()
    conn.execute("DELETE FROM watched_stores WHERE store = ?", (store,))
    conn.commit()
    conn.close()


def unwatch_all():
    conn = _connect()
    conn.execute("DELETE FROM watched_stores")
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Specials diffing -- only ever called against stores on the watch list.
# ---------------------------------------------------------------------------

def _specials_fingerprint(catalog):
    """A comparable snapshot of just the specials (name -> special_price)."""
    return {item["name"]: item["special_price"] for item in catalog if item.get("special_price") is not None}


def check_for_new_specials(store, current_catalog):
    """
    Compare `current_catalog`'s specials against the last snapshot for this
    store, update the snapshot, and return the list of (name, price) pairs
    that are new or changed since last check.
    """
    conn = _connect()
    row = conn.execute("SELECT snapshot_json FROM catalog_snapshots WHERE store = ?", (store,)).fetchone()
    previous = json.loads(row[0]) if row else {}

    current = _specials_fingerprint(current_catalog)
    new_or_changed = [
        (name, price) for name, price in current.items()
        if previous.get(name) != price
    ]

    conn.execute(
        "INSERT INTO catalog_snapshots (store, snapshot_json) VALUES (?, ?) "
        "ON CONFLICT(store) DO UPDATE SET snapshot_json=excluded.snapshot_json",
        (store, json.dumps(current)),
    )
    conn.commit()
    conn.close()
    return new_or_changed
