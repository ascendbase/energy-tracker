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
from .calculator import calculate_from_dict, VARIABLES
from .ai_agent import chat as ai_chat

app = FastAPI(title="Energy Tracker API")

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

STATIC_DIR = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "static")

# On Vercel, the project root is different
if not os.path.isdir(STATIC_DIR):
    # Try Vercel's project root
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


# ── Middleware: ensure DB is initialized for every request ─────────
@app.middleware("http")
async def ensure_db_middleware(request, call_next):
    _ensure_db()
    response = await call_next(request)
    return response


# ── API: Calculator (no saving) ───────────────────────────────────

class CalcRequest(BaseModel):
    fh: Optional[float] = None
    fac: Optional[float] = None
    fic: Optional[float] = None
    tac: Optional[float] = None
    ec: Optional[float] = None
    b: Optional[float] = None
    nm: Optional[float] = None
    trio: Optional[float] = None
    fmf: Optional[float] = None
    sr: Optional[float] = None
    p: Optional[float] = None
    sm: Optional[float] = None


@app.post("/api/calculate")
def calculate(req: CalcRequest):
    """Calculate TS from component variables. None = not entered."""
    values = {k: v for k, v in req.model_dump().items()}
    return calculate_from_dict(values)


# ── API: Draft (today's in-progress entry) ────────────────────────

class DraftRequest(BaseModel):
    date: Optional[str] = None
    fh: Optional[float] = None
    fac: Optional[float] = None
    fic: Optional[float] = None
    tac: Optional[float] = None
    ec: Optional[float] = None
    b: Optional[float] = None
    nm: Optional[float] = None
    trio: Optional[float] = None
    fmf: Optional[float] = None
    sr: Optional[float] = None
    p: Optional[float] = None
    sm: Optional[float] = None


@app.get("/api/draft")
def get_draft(date: Optional[str] = None):
    """Get today's draft entry (or by date)."""
    entry = db.get_draft(date)
    return {"entry": entry}


@app.post("/api/draft")
def save_draft(req: DraftRequest):
    """Save/update today's draft. Only provided fields are changed."""
    date_str = req.date or db.today_str()
    # Only include non-None values (so we don't overwrite existing vars with None)
    values = {k: v for k, v in req.model_dump().items() if k != "date" and v is not None}
    if not values:
        # No values to update — just return existing draft
        return {"entry": db.get_draft(date_str)}
    entry = db.save_draft(date_str, values)
    return {"success": True, "entry": entry}


@app.post("/api/draft/commit")
def commit_draft(date: Optional[str] = None):
    """Commit today's draft as a permanent log entry."""
    entry = db.commit_draft(date)
    if not entry:
        raise HTTPException(status_code=404, detail="No draft found to commit")
    return {"success": True, "entry": entry}


# ── API: Committed entries (Logs) ─────────────────────────────────

@app.get("/api/entries")
def get_entries():
    """Get all committed entries, ordered by date descending."""
    return {"entries": db.get_committed_entries()}


@app.get("/api/entries/{entry_id}")
def get_entry_by_id(entry_id: int):
    """Get a specific entry by ID."""
    entry = db.get_entry_by_id(entry_id)
    if not entry:
        raise HTTPException(status_code=404, detail=f"Entry {entry_id} not found")
    return {"entry": entry}


@app.delete("/api/entries/{entry_id}")
def delete_entry(entry_id: int):
    """Delete an entry by ID."""
    deleted = db.delete_entry(entry_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Entry {entry_id} not found")
    return {"success": True}


# ── API: Stats (committed only) ───────────────────────────────────

@app.get("/api/stats")
def get_stats(days: int = 30):
    return db.get_stats(days)


# ── API: AI Chat ──────────────────────────────────────────────────

class ChatRequest(BaseModel):
    message: str
    history: list[dict] = []
    model: Optional[str] = None


@app.post("/api/chat")
def chat_endpoint(req: ChatRequest):
    result = ai_chat(req.message, req.history, req.model)
    return result


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

@app.get("/api/health")
def health():
    return {"status": "ok", "turso": bool(os.environ.get("TURSO_URL"))}


# ── Static file serving (local dev) ───────────────────────────────

if os.path.isdir(STATIC_DIR):
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
def serve_index():
    index_path = os.path.join(STATIC_DIR, "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path)
    return JSONResponse({"error": "index.html not found in static/"}, status_code=404)
