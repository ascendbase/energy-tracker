"""
FastAPI app — serves the API routes and static frontend.

Local dev:  python run.py
Vercel:     api/index.py imports `app` from here
"""

import os
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel
from typing import Optional
from . import database as db

app = FastAPI(title="Habit Tracker API")

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

STATIC_DIR = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "static")

# On Vercel, the project root is different
if not os.path.isdir(STATIC_DIR):
    _vercel_root = os.environ.get("VERCEL_PROJECT_ROOT", "")
    if _vercel_root:
        STATIC_DIR = os.path.join(_vercel_root, "static")


# ── Lazy DB init (works in serverless where startup event may not fire) ──
_db_initialized = False

def _ensure_db():
    global _db_initialized
    if not _db_initialized:
        db.init_db()
        _db_initialized = True


@app.on_event("startup")
def startup():
    _ensure_db()


@app.middleware("http")
async def ensure_db_middleware(request, call_next):
    _ensure_db()
    response = await call_next(request)
    return response


# ── API: Habits ───────────────────────────────────────────────────

class HabitCreateRequest(BaseModel):
    name: str


class HabitRenameRequest(BaseModel):
    name: str


class HabitValueRequest(BaseModel):
    value: Optional[float] = None


@app.get("/api/habits")
def list_habits():
    """Get all habits with their current values."""
    return {"habits": db.get_habits()}


@app.post("/api/habits")
def add_habit(req: HabitCreateRequest):
    name = req.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Habit name cannot be empty")
    habit = db.create_habit(name)
    return {"success": True, "habit": habit}


@app.patch("/api/habits/{habit_id}")
def rename_habit(habit_id: int, req: HabitRenameRequest):
    name = req.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Habit name cannot be empty")
    habit = db.rename_habit(habit_id, name)
    if not habit:
        raise HTTPException(status_code=404, detail=f"Habit {habit_id} not found")
    return {"success": True, "habit": habit}


@app.put("/api/habits/{habit_id}/value")
def update_habit_value(habit_id: int, req: HabitValueRequest):
    """Set (or clear, with null) a habit's value."""
    habit = db.set_habit_value(habit_id, req.value)
    if not habit:
        raise HTTPException(status_code=404, detail=f"Habit {habit_id} not found")
    return {"success": True, "habit": habit}


@app.delete("/api/habits/{habit_id}")
def remove_habit(habit_id: int):
    deleted = db.delete_habit(habit_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Habit {habit_id} not found")
    return {"success": True}


@app.post("/api/habits/reset")
def reset_habits():
    """Reset all habit values (habits list is kept)."""
    count = db.reset_habits()
    return {"success": True, "reset": count}


# ── API: Notes CRUD ───────────────────────────────────────────────

class NoteCreateRequest(BaseModel):
    title: str
    content: str = ""


class NoteUpdateRequest(BaseModel):
    title: Optional[str] = None
    content: Optional[str] = None


@app.get("/api/notes")
def get_notes(search: Optional[str] = None):
    """Get all notes, or search by query."""
    if search:
        return {"notes": db.search_notes(search)}
    return {"notes": db.get_all_notes()}


@app.post("/api/notes")
def create_note(req: NoteCreateRequest):
    note = db.create_note(req.title, req.content)
    return {"success": True, "note": note}


@app.get("/api/notes/{note_id}")
def get_note(note_id: int):
    note = db.get_note(note_id)
    if not note:
        raise HTTPException(status_code=404, detail=f"Note {note_id} not found")
    return {"note": note}


@app.patch("/api/notes/{note_id}")
def update_note(note_id: int, req: NoteUpdateRequest):
    note = db.update_note(note_id, req.title, req.content)
    if not note:
        raise HTTPException(status_code=404, detail=f"Note {note_id} not found")
    return {"success": True, "note": note}


@app.delete("/api/notes/{note_id}")
def delete_note(note_id: int):
    deleted = db.delete_note(note_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Note {note_id} not found")
    return {"success": True}


# ── Health check ──────────────────────────────────────────────────

@app.exception_handler(Exception)
async def unhandled_exception_handler(request, exc):
    """Surface real error text so deployment issues are diagnosable."""
    import traceback
    print("UNHANDLED ERROR:", "".join(traceback.format_exception(exc)))
    return JSONResponse({"error": f"{type(exc).__name__}: {exc}"}, status_code=500)


@app.get("/api/health")
def health():
    from . import database as dbmod
    return {
        "status": "ok",
        "turso": dbmod._USE_TURSO,
        "db_path": dbmod._LOCAL_DB if not dbmod._USE_TURSO else "turso",
    }


# ── Static file serving (local dev) ───────────────────────────────

if os.path.isdir(STATIC_DIR):
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
def serve_index():
    index_path = os.path.join(STATIC_DIR, "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path)
    return JSONResponse({"error": "index.html not found in static/"}, status_code=404)
