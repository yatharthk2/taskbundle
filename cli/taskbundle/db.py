"""SQLite ledger: one row per CLI invocation, queryable by command id."""
from __future__ import annotations

# ----------------------------- Imports -----------------------------
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

# ----------------------------- Constants -----------------------------
DEFAULT_DB_PATH = ".taskbundle/db.sqlite"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS commands (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    ts      TEXT    NOT NULL,   -- UTC ISO-8601
    command TEXT    NOT NULL,   -- init | validate | run | ...
    task_id TEXT,
    status  TEXT    NOT NULL,   -- ok | error
    summary TEXT,               -- one-line human log
    details TEXT                -- JSON blob (flags, image, test results, ...)
);
"""

# ----------------------------- Connection -----------------------------
def connect(db_path: str | Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    """Open (lazily creating) the ledger; rows accessible by column name. WAL + a busy
    timeout let concurrent `task run`s append rows without tripping 'database is locked'."""
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=5)
    conn.row_factory = sqlite3.Row
    # Yatharth Note : added WAL + busy_timeout after two `task run`s at once kept tripping "database is locked" - now they can append rows concurrently.
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.executescript(_SCHEMA)
    return conn

# ----------------------------- Write -----------------------------
def log_command(
    command: str,
    status: str,
    *,
    task_id: Optional[str] = None,
    summary: str = "",
    details: Optional[dict[str, Any]] = None,
    db_path: str | Path = DEFAULT_DB_PATH,
) -> int:
    """Append one invocation record; returns its command id."""
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with connect(db_path) as conn:
        cur = conn.execute(
            "INSERT INTO commands (ts, command, task_id, status, summary, details) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (ts, command, task_id, status, summary, json.dumps(details or {})),
        )
        return int(cur.lastrowid)

# ----------------------------- Read -----------------------------
def get_command(
    cmd_id: int, db_path: str | Path = DEFAULT_DB_PATH
) -> Optional[dict[str, Any]]:
    """Fetch one record by id with `details` decoded back to a dict."""
    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM commands WHERE id = ?", (cmd_id,)
        ).fetchone()
    return _record(row) if row is not None else None


def find_commands(
    *,
    command: Optional[str] = None,
    task_id: Optional[str] = None,
    status: Optional[str] = None,
    outcome: Optional[str] = None,
    limit: Optional[int] = 10,
    db_path: str | Path = DEFAULT_DB_PATH,
) -> list[dict[str, Any]]:
    """Records newest-first (id DESC), `details` decoded, optionally filtered.
    `command`/`task_id`/`status` are real columns (SQL WHERE); `outcome` lives inside the JSON
    `details`, so it's filtered in Python. `limit=None` returns every match (used by `--stats`)."""
    clauses, params = [], []
    for col, val in (("command", command), ("task_id", task_id), ("status", status)):
        if val:
            clauses.append(f"{col} = ?")
            params.append(val)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    with connect(db_path) as conn:
        rows = conn.execute(
            f"SELECT * FROM commands{where} ORDER BY id DESC", params
        ).fetchall()
    recs = [_record(r) for r in rows]
    if outcome:
        recs = [r for r in recs if (r.get("details") or {}).get("outcome") == outcome]
    return recs if limit is None else recs[:limit]


def recent_commands(
    limit: int = 10, db_path: str | Path = DEFAULT_DB_PATH
) -> list[dict[str, Any]]:
    """Most-recent invocations first (id DESC), `details` decoded. [] if none."""
    return find_commands(limit=limit, db_path=db_path)


def _record(row: sqlite3.Row) -> dict[str, Any]:
    """Row → dict with `details` JSON decoded; tolerate a malformed/empty blob."""
    rec = dict(row)
    try:
        rec["details"] = json.loads(rec["details"] or "{}")
    except (ValueError, TypeError):
        rec["details"] = {}
    return rec
