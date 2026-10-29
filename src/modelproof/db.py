"""SQLite persistence with ordered, forward-only migrations.

Uses the stdlib sqlite3 driver; WAL journaling; foreign keys enforced. The
runner is deliberately tiny and auditable — each migration is a versioned SQL
file applied exactly once inside a transaction.
"""
from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

from .errors import InfrastructureError

_MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"
_local = threading.local()


def connect(db_path: Path) -> sqlite3.Connection:
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def get_thread_conn(db_path: Path) -> sqlite3.Connection:
    """Thread-local connection, keyed by database path (a reused worker thread
    must never keep a connection to a previous database)."""
    conn = getattr(_local, "conn", None)
    current = getattr(_local, "path", None)
    if conn is None or current != str(Path(db_path).resolve()):
        if conn is not None:
            conn.close()
        conn = connect(db_path)
        _local.conn = conn
        _local.path = str(Path(db_path).resolve())
    return conn


def close_thread_conn() -> None:
    conn = getattr(_local, "conn", None)
    if conn is not None:
        conn.close()
        _local.conn = None


def migrate(conn: sqlite3.Connection) -> list[str]:
    """Apply pending migrations in order. Returns applied version names."""
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        " version TEXT PRIMARY KEY, applied_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')))"
    )
    applied = {r["version"] for r in conn.execute("SELECT version FROM schema_migrations")}
    try:
        files = sorted(p for p in _MIGRATIONS_DIR.glob("*.sql") if p.name not in applied)
    except OSError as e:  # pragma: no cover - packaging error
        raise InfrastructureError(f"cannot read migrations dir: {e}") from e
    done: list[str] = []
    for path in files:
        sql = path.read_text(encoding="utf-8")
        try:
            # NOTE: executescript() implicitly commits any open transaction, so
            # migrations must be idempotent (IF NOT EXISTS) and applied in
            # order; the version row is recorded only after the script succeeds.
            conn.executescript(sql)
            conn.execute("INSERT INTO schema_migrations(version) VALUES (?)", (path.name,))
        except sqlite3.Error as e:
            raise InfrastructureError(f"migration {path.name} failed: {e}") from e
        done.append(path.name)
    return done


def current_version(conn: sqlite3.Connection) -> str | None:
    try:
        row = conn.execute("SELECT MAX(version) AS v FROM schema_migrations").fetchone()
        return row["v"] if row else None
    except sqlite3.Error:
        return None
