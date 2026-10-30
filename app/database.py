"""
Database layer — transparently uses local SQLite (dev) or Turso (prod).

Schema:
  habits: user-defined habit list (persisted until deleted by the user)
  habits.value: today's value per habit (NULL = not entered);
                persisted until the user presses Reset.
  notes: simple notes store.
"""

import os
import sqlite3
import threading
from contextlib import contextmanager

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
# On Vercel the project filesystem is read-only; only /tmp is writable.
if os.environ.get("VERCEL"):
    _DEFAULT_PATH = "/tmp/energy.db"
else:
    _DEFAULT_PATH = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "data", "energy.db"
    )
_LOCAL_DB = os.environ.get("DATABASE_PATH", _DEFAULT_PATH)

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


def _row_to_dict(row, cursor=None) -> dict:
    """Convert a database row to dict. Works with both sqlite3.Row and libsql rows."""
    if row is None:
        return None
    # sqlite3.Row supports dict() conversion
    try:
        return dict(row)
    except (TypeError, ValueError):
        pass
    # libsql rows may be plain tuples — use the cursor's column description
    if cursor is not None and getattr(cursor, "description", None):
        cols = [d[0] for d in cursor.description]
        return dict(zip(cols, row))
    if hasattr(row, "keys"):
        return {k: row[k] for k in row.keys()}
    return dict(row)


def _fetch_one(conn, query, params=()):
    cur = conn.execute(query, params)
    row = cur.fetchone()
    return _row_to_dict(row, cur) if row else None


def _fetch_all(conn, query, params=()):
    cur = conn.execute(query, params)
    rows = cur.fetchall()
    return [_row_to_dict(row, cur) for row in rows]


# ── Schema ────────────────────────────────────────────────────────

_DEFAULT_HABITS = ["B", "NM", "Trio", "FMF", "SR", "P", "SM"]


def init_db():
    """Create tables if they don't exist."""
    with get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS habits (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                name       TEXT NOT NULL,
                value      REAL,
                position   INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS notes (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                title      TEXT NOT NULL,
                content    TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
        # Seed default habits on a fresh database
        count = conn.execute("SELECT COUNT(*) FROM habits").fetchone()[0]
        if count == 0:
            for i, name in enumerate(_DEFAULT_HABITS):
                conn.execute(
                    "INSERT INTO habits (name, position) VALUES (?, ?)",
                    (name, i)
                )


# ── Habits ────────────────────────────────────────────────────────

def get_habits() -> list[dict]:
    with get_conn() as conn:
        return _fetch_all(
            conn,
            "SELECT id, name, value FROM habits ORDER BY position, id"
        )


def create_habit(name: str) -> dict:
    with get_conn() as conn:
        pos = conn.execute(
            "SELECT COALESCE(MAX(position), -1) + 1 FROM habits"
        ).fetchone()[0]
        cur = conn.execute(
            "INSERT INTO habits (name, position) VALUES (?, ?)", (name, pos)
        )
        return _fetch_one(conn, "SELECT id, name, value FROM habits WHERE id = ?", (cur.lastrowid,))


def rename_habit(habit_id: int, name: str) -> dict | None:
    """Rename a habit."""
    with get_conn() as conn:
        conn.execute("UPDATE habits SET name = ? WHERE id = ?", (name, habit_id))
        return _fetch_one(conn, "SELECT id, name, value FROM habits WHERE id = ?", (habit_id,))

# ── Notes CRUD ────────────────────────────────────────────────────

def create_note(title: str, content: str = "") -> dict:
    """Create a new note."""
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO notes (title, content) VALUES (?, ?)",
            (title, content)
        )
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
        return _fetch_one(conn, "SELECT * FROM notes WHERE id = ?", (note_id,))


def delete_note(note_id: int) -> bool:
    with get_conn() as conn:
        cur = conn.execute("DELETE FROM notes WHERE id = ?", (note_id,))
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


def set_habit_value(habit_id: int, value: float | None) -> dict | None:
    with get_conn() as conn:
        conn.execute("UPDATE habits SET value = ? WHERE id = ?", (value, habit_id))
        return _fetch_one(conn, "SELECT id, name, value FROM habits WHERE id = ?", (habit_id,))


def delete_habit(habit_id: int) -> bool:
    with get_conn() as conn:
        cur = conn.execute("DELETE FROM habits WHERE id = ?", (habit_id,))
        return cur.rowcount > 0


def reset_habits() -> int:
    """Clear all habit values (habits themselves are kept)."""
    with get_conn() as conn:
        cur = conn.execute("UPDATE habits SET value = NULL")
        return cur.rowcount
