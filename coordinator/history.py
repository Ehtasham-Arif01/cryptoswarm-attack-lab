"""Attack history in SQLite (Redis is in-memory only, this survives restarts).

The coordinator records every job once it stops running (found, complete,
cancelled, ...). Stored on the `history` Docker volume.
"""

import json
import os
import sqlite3
import threading

DB_PATH = os.environ.get("HISTORY_DB", "/data/history.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id        TEXT PRIMARY KEY,
    attack    TEXT NOT NULL,
    title     TEXT NOT NULL,
    status    TEXT NOT NULL,
    params    TEXT NOT NULL,      -- json
    outcome   TEXT NOT NULL,      -- one-line summary from the attack plugin
    created   REAL NOT NULL,
    elapsed   REAL NOT NULL,
    tried     INTEGER NOT NULL,
    total     INTEGER NOT NULL,
    unit      TEXT NOT NULL,
    workers   INTEGER,
    vcpus     REAL,
    rate      REAL,               -- work units per second (average over the whole run)
    samples   TEXT                -- json {busy workers: [avg speed, n samples]}
);
CREATE INDEX IF NOT EXISTS runs_created ON runs (created DESC);
"""

_lock = threading.Lock()
_conn: sqlite3.Connection | None = None


def conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
        _conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.executescript(SCHEMA)
        try:  # databases created before the samples column existed
            _conn.execute("ALTER TABLE runs ADD COLUMN samples TEXT")
        except sqlite3.OperationalError:
            pass
    return _conn


def known_ids(ids: list[str]) -> set[str]:
    if not ids:
        return set()
    with _lock:
        rows = conn().execute(f"SELECT id FROM runs WHERE id IN ({','.join('?' * len(ids))})", ids).fetchall()
    return {r["id"] for r in rows}


def record(run: dict) -> None:
    with _lock:
        conn().execute(
            """INSERT OR IGNORE INTO runs
               (id, attack, title, status, params, outcome, created, elapsed, tried, total, unit, workers, vcpus, rate,
                samples)
               VALUES (:id, :attack, :title, :status, :params, :outcome, :created, :elapsed, :tried, :total, :unit,
                       :workers, :vcpus, :rate, :samples)""",
            {**run, "params": json.dumps(run["params"]), "samples": json.dumps(run.get("samples") or {})},
        )
        conn().commit()


def list_runs(attack: str | None = None, limit: int = 500) -> list[dict]:
    query, args = "SELECT * FROM runs", []
    if attack:
        query, args = query + " WHERE attack = ?", [attack]
    with _lock:
        rows = conn().execute(query + " ORDER BY created DESC LIMIT ?", [*args, limit]).fetchall()
    return [{**dict(r), "params": json.loads(r["params"]), "samples": json.loads(r["samples"] or "{}")} for r in rows]


def clear() -> int:
    with _lock:
        n = conn().execute("DELETE FROM runs").rowcount
        conn().commit()
    return n
