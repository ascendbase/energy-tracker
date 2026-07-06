"""
Database layer — transparently uses local SQLite (dev) or Turso (prod).

Schema: entries table stores all 12 variables as NULLABLE columns.
  NULL = not entered yet
  0    = entered as zero
  is_committed: 0 = draft (in progress), 1 = committed permanent log
"""

import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, date
from .calculator import calculate_from_dict, VARIABLES

# ── Turso support (optional) ──────────────────────────────────────
_turso_url = os.environ.get("TURSO_URL")
_turso_token = os.environ.get("TURSO_TOKEN")

if _turso_url:
    try:
        import libsql  # type: ignore
        _USE_TURSO = True
    except ImportError:
        _USE_TURSO = False
        print("WARNING: TURSO_URL set but `libsql` package not installed. "
              "Falling back to local SQLite. Run: pip install libsql")
else:
    _USE_TURSO = False

# ── Local SQLite path ─────────────────────────────────────────────
_LOCAL_DB = os.environ.get("DATABASE_PATH", os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "energy.db"
))

_thread_local = threading.local()


def _get_conn():
    """Get a thread-local database connection."""
    if not hasattr(_thread_local, "conn"):
        if _USE_TURSO:
            _thread_local.conn = libsql.connect(_turso_url, auth_token=_turso_token)
        else:
            os.makedirs(os.path.dirname(_LOCAL_DB), exist_ok=True)
            _thread_local.conn = sqlite3.connect(_LOCAL_DB, check_same_thread=False)
            _thread_local.conn.row_factory = sqlite3.Row
            _thread_local.conn.execute("PRAGMA journal_mode=WAL;")
            _thread_local.conn.execute("PRAGMA foreign_keys=ON;")
    return _thread_local.conn


@contextmanager
def get_conn():
    """Context manager that yields a connection and commits on success."""
    conn = _get_conn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise


# ── Schema ────────────────────────────────────────────────────────

_ENTRY_COLUMNS = (
    "fh", "fac", "fic", "tac", "ec",
    "b", "nm", "trio", "fmf", "sr", "p", "sm"
)

_SCHEMA_COLS = {
    "id":         "INTEGER PRIMARY KEY AUTOINCREMENT",
    "date":       "TEXT NOT NULL UNIQUE",
    **{c: "REAL" for c in _ENTRY_COLUMNS},  # NULLABLE — not entered = NULL
    "ts":         "REAL",
    "is_committed": "INTEGER NOT NULL DEFAULT 0",
    "created_at": "TEXT NOT NULL DEFAULT (datetime('now'))",
    "updated_at": "TEXT NOT NULL DEFAULT (datetime('now'))",
}


def init_db():
    """Create tables if they don't exist, and migrate old schemas."""
    with get_conn() as conn:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(entries)").fetchall()]

        if not cols:
            # Fresh table
            col_defs = ", ".join(f"{k} {v}" for k, v in _SCHEMA_COLS.items())
            conn.execute(f"CREATE TABLE entries ({col_defs})")
        else:
            # Migrate: add any missing columns
            for col_name, col_def in _SCHEMA_COLS.items():
                if col_name not in cols:
                    # ALTER TABLE ADD COLUMN can't specify PRIMARY KEY etc
                    if "PRIMARY KEY" in col_def or "AUTOINCREMENT" in col_def:
                        continue
                    conn.execute(f"ALTER TABLE entries ADD COLUMN {col_name} {col_def}")

        conn.execute("CREATE INDEX IF NOT EXISTS idx_entries_date ON entries(date)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_entries_committed ON entries(is_committed)")

        # ── Notes table ──
        note_cols = [r[1] for r in conn.execute("PRAGMA table_info(notes)").fetchall()]
        if not note_cols:
            conn.execute("""
                CREATE TABLE notes (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    title      TEXT NOT NULL DEFAULT '',
                    content    TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL DEFAULT (datetime('now')),
                    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
                )
            """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_notes_updated ON notes(updated_at)")


# ── Helpers ───────────────────────────────────────────────────────

def _row_to_dict(row, cursor=None) -> dict:
    """Convert a database row to dict. Works with both sqlite3.Row and libsql rows."""
    if row is None:
        return {}
    # sqlite3.Row supports dict() conversion
    try:
        return dict(row)
    except (TypeError, ValueError):
        pass
    # libsql returns tuples — use cursor description for column names
    if cursor and hasattr(cursor, 'description') and cursor.description:
        cols = [d[0] for d in cursor.description]
        return dict(zip(cols, row))
    # Last resort: check if it has keys()
    if hasattr(row, 'keys'):
        return {k: row[k] for k in row.keys()}
    return {}


def _fetch_one(conn, query, params=()):
    """Execute query and return a single row as dict. Works with sqlite3 and libsql."""
    cur = conn.execute(query, params)
    row = cur.fetchone()
    return _row_to_dict(row, cur) if row else None


def _fetch_all(conn, query, params=()):
    """Execute query and return all rows as list of dicts. Works with sqlite3 and libsql."""
    cur = conn.execute(query, params)
    rows = cur.fetchall()
    return [_row_to_dict(row, cur) for row in rows]


def _compute_ts(values: dict) -> float | None:
    """Calculate TS from a dict of variable values (None = not entered)."""
    result = calculate_from_dict(values)
    return result["ts"]


def today_str() -> str:
    return date.today().isoformat()


# ── Draft operations ──────────────────────────────────────────────

def get_draft(date_str: str | None = None) -> dict | None:
    """Get the draft (uncommitted) entry for a date. Defaults to today."""
    if date_str is None:
        date_str = today_str()
    with get_conn() as conn:
        return _fetch_one(conn, "SELECT * FROM entries WHERE date = ?", (date_str,))


def save_draft(date_str: str, values: dict) -> dict:
    """
    Insert or update a draft entry.
    Only provided keys are set; others are left as-is (or NULL if new).
    NULL = not entered. 0 = entered as zero.
    """
    existing = get_draft(date_str)
    if existing:
        # Merge: keep existing values for keys not in `values`
        merged = {c: existing[c] for c in _ENTRY_COLUMNS}
        for k, v in values.items():
            if k in _ENTRY_COLUMNS:
                merged[k] = v  # can be None or a number
    else:
        merged = {c: values.get(c, None) for c in _ENTRY_COLUMNS}

    ts = _compute_ts(merged)

    if existing:
        sets = ", ".join(f"{c} = ?" for c in _ENTRY_COLUMNS)
        # Order: var1...var12, ts, date
        params = [merged[c] for c in _ENTRY_COLUMNS] + [ts, date_str]
        with get_conn() as conn:
            conn.execute(
                f"UPDATE entries SET {sets}, ts = ?, updated_at = datetime('now') WHERE date = ?",
                params
            )
            conn.commit()
            return _fetch_one(conn, "SELECT * FROM entries WHERE date = ?", (date_str,)) or {"date": date_str, "ts": ts}
    else:
        cols = ", ".join(_ENTRY_COLUMNS)
        placeholders = ", ".join(["?"] * len(_ENTRY_COLUMNS))
        # Order: date, var1...var12, ts
        params = [date_str] + [merged[c] for c in _ENTRY_COLUMNS] + [ts]
        with get_conn() as conn:
            cur = conn.execute(
                f"INSERT INTO entries (date, {cols}, ts) VALUES (?, {placeholders}, ?)",
                params
            )
            conn.commit()
            return _fetch_one(conn, "SELECT * FROM entries WHERE id = ?", (cur.lastrowid,)) or {"date": date_str, "ts": ts}


def commit_draft(date_str: str | None = None) -> dict | None:
    """Commit today's draft as a permanent log entry."""
    if date_str is None:
        date_str = today_str()
    with get_conn() as conn:
        conn.execute(
            "UPDATE entries SET is_committed = 1, updated_at = datetime('now') WHERE date = ?",
            (date_str,)
        )
        conn.commit()
        return _fetch_one(conn, "SELECT * FROM entries WHERE date = ?", (date_str,))


# ── Committed entry operations ────────────────────────────────────

def get_committed_entries() -> list[dict]:
    """Get all committed entries, ordered by date descending."""
    with get_conn() as conn:
        return _fetch_all(conn, "SELECT * FROM entries WHERE is_committed = 1 ORDER BY date DESC")


def get_entry_by_date(date_str: str) -> dict | None:
    """Get entry (draft or committed) by date."""
    with get_conn() as conn:
        return _fetch_one(conn, "SELECT * FROM entries WHERE date = ?", (date_str,))


def get_entry_by_id(entry_id: int) -> dict | None:
    with get_conn() as conn:
        return _fetch_one(conn, "SELECT * FROM entries WHERE id = ?", (entry_id,))


def delete_entry(entry_id: int) -> bool:
    with get_conn() as conn:
        cur = conn.execute("DELETE FROM entries WHERE id = ?", (entry_id,))
        return cur.rowcount > 0


# ── Stats (committed entries only) ────────────────────────────────

def get_stats(days: int = 30) -> dict:
    """Get statistics for committed entries over the last N days."""
    with get_conn() as conn:
        rows = _fetch_all(conn, """
            SELECT * FROM entries
            WHERE is_committed = 1 AND date >= date('now', ?)
            ORDER BY date ASC
        """, (f"-{days} days",))

        if not rows:
            return {
                "count": 0, "avg": None, "min": None,
                "max": None, "latest": None, "latest_date": None,
                "trend": None, "values": []
            }

        entries = rows
        ts_values = [e["ts"] for e in entries if e["ts"] is not None]
        if not ts_values:
            return {
                "count": 0, "avg": None, "min": None,
                "max": None, "latest": None, "latest_date": None,
                "trend": None, "values": []
            }

        avg = sum(ts_values) / len(ts_values)
        mid = len(ts_values) // 2
        if mid > 0:
            first_half_avg = sum(ts_values[:mid]) / mid
            second_half_avg = sum(ts_values[mid:]) / (len(ts_values) - mid)
            trend = second_half_avg - first_half_avg
        else:
            trend = 0.0

        var_avgs = {}
        for v in _ENTRY_COLUMNS:
            vals = [e[v] for e in entries if e[v] is not None]
            if vals:
                var_avgs[v] = round(sum(vals) / len(vals), 2)

        return {
            "count": len(ts_values),
            "avg": round(avg, 2),
            "min": round(min(ts_values), 2),
            "max": round(max(ts_values), 2),
            "latest": ts_values[-1],
            "latest_date": entries[-1]["date"],
            "trend": round(trend, 2),
            "values": [{"date": e["date"], "ts": e["ts"]} for e in entries],
            "var_avgs": var_avgs,
        }


# ── Notes CRUD ────────────────────────────────────────────────────

def create_note(title: str, content: str = "") -> dict:
    """Create a new note."""
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO notes (title, content) VALUES (?, ?)",
            (title, content)
        )
        conn.commit()
        return _fetch_one(conn, "SELECT * FROM notes WHERE id = ?", (cur.lastrowid,))


def get_all_notes() -> list[dict]:
    """Get all notes, ordered by most recently updated."""
    with get_conn() as conn:
        return _fetch_all(conn, "SELECT * FROM notes ORDER BY updated_at DESC")


def get_note(note_id: int) -> dict | None:
    with get_conn() as conn:
        return _fetch_one(conn, "SELECT * FROM notes WHERE id = ?", (note_id,))


def update_note(note_id: int, title: str | None = None,
                content: str | None = None) -> dict | None:
    """Update a note. Only provided fields are changed."""
    sets = []
    params = []
    if title is not None:
        sets.append("title = ?")
        params.append(title)
    if content is not None:
        sets.append("content = ?")
        params.append(content)
    if not sets:
        return get_note(note_id)
    sets.append("updated_at = datetime('now')")
    params.append(note_id)
    with get_conn() as conn:
        conn.execute(f"UPDATE notes SET {', '.join(sets)} WHERE id = ?", params)
        conn.commit()
        return _fetch_one(conn, "SELECT * FROM notes WHERE id = ?", (note_id,))


def delete_note(note_id: int) -> bool:
    with get_conn() as conn:
        cur = conn.execute("DELETE FROM notes WHERE id = ?", (note_id,))
        conn.commit()
        return cur.rowcount > 0


def search_notes(query: str) -> list[dict]:
    """Search notes by title or content."""
    pattern = f"%{query}%"
    with get_conn() as conn:
        return _fetch_all(
            conn,
            "SELECT * FROM notes WHERE title LIKE ? OR content LIKE ? ORDER BY updated_at DESC",
            (pattern, pattern)
        )
