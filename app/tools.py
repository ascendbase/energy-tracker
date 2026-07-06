"""
AI tool definitions for the OpenRouter tool-calling agent.
The agent edits today's DRAFT (not committed logs).
The user commits the draft via the Save button.
"""

import json
from . import database as db
from .calculator import calculate_from_dict, VARIABLES, VARIABLE_LABELS, BMR

# ── Variable properties for tool schemas ──────────────────────────

_VAR_PROPS = {
    "fh":   {"type": "number", "description": "FH (fasting hours)"},
    "fac":  {"type": "number", "description": "FAC value"},
    "fic":  {"type": "number", "description": "FIC value"},
    "tac":  {"type": "number", "description": "TAC value"},
    "ec":   {"type": "number", "description": "EC value"},
    "b":    {"type": "number", "description": "B value"},
    "nm":   {"type": "number", "description": "NM value"},
    "trio": {"type": "number", "description": "Trio hours"},
    "fmf":  {"type": "number", "description": "FMF value"},
    "sr":   {"type": "number", "description": "SR value"},
    "p":    {"type": "number", "description": "P value"},
    "sm":   {"type": "number", "description": "SM value"},
}

TOOL_SCHEMAS = [
    # ── 1. Set variables (updates draft) ──
    {
        "type": "function",
        "function": {
            "name": "set_variables",
            "description": (
                "Set one or more component variables for today's draft. "
                "This is a PARTIAL update — only the variables you provide "
                "are changed. TS auto-recalculates. "
                "The draft is NOT committed yet — the user commits it via "
                "the Save button. Use this when the user says 'set FH to 16'. "
                "If no date is given, uses today."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "date": {
                        "type": "string",
                        "description": "Date in YYYY-MM-DD format. Defaults to today.",
                    },
                    **_VAR_PROPS,
                },
            },
        },
    },
    # ── 2. Get today's draft ──
    {
        "type": "function",
        "function": {
            "name": "get_draft",
            "description": (
                "Get today's draft entry (all variables + computed TS). "
                "Variables that haven't been entered yet will be null. "
                "Use this when the user asks 'what is my FH today?' or "
                "'show me today's values'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "date": {
                        "type": "string",
                        "description": "Date in YYYY-MM-DD format. Defaults to today.",
                    },
                },
            },
        },
    },
    # ── 3. Commit draft ──
    {
        "type": "function",
        "function": {
            "name": "commit_draft",
            "description": (
                "Commit today's draft as a permanent log entry. "
                "Use this when the user says 'save', 'commit', or "
                "'log today's entry'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "date": {
                        "type": "string",
                        "description": "Date in YYYY-MM-DD format. Defaults to today.",
                    },
                },
            },
        },
    },
    # ── 4. Get all committed entries ──
    {
        "type": "function",
        "function": {
            "name": "get_all_entries",
            "description": (
                "Get all committed (permanent) entries, ordered by date "
                "descending. Use this when the user asks to 'show all "
                "logs' or 'list my entries'."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    # ── 5. Calculate TS (preview, no saving) ──
    {
        "type": "function",
        "function": {
            "name": "calculate_ts",
            "description": (
                "Calculate TS from given values WITHOUT saving. "
                "Use for quick previews when the user asks 'what would "
                "my TS be if...'"
            ),
            "parameters": {
                "type": "object",
                "properties": {**_VAR_PROPS},
            },
        },
    },
    # ── 6. Delete entry ──
    {
        "type": "function",
        "function": {
            "name": "delete_entry",
            "description": (
                "Delete a committed entry by its ID. Use get_all_entries "
                "first to find the ID."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer", "description": "The entry ID to delete"},
                },
                "required": ["id"],
            },
        },
    },
    # ── 7. Get stats ──
    {
        "type": "function",
        "function": {
            "name": "get_stats",
            "description": (
                "Get statistics for committed entries over the last N days."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "days": {"type": "integer", "description": "Days to look back (default 30)"},
                },
            },
        },
    },
    # ── 8. Get today's date ──
    {
        "type": "function",
        "function": {
            "name": "get_today_date",
            "description": "Get today's date in YYYY-MM-DD format.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    # ── 9. Create note ──
    {
        "type": "function",
        "function": {
            "name": "create_note",
            "description": (
                "Create a new note with a title and optional content. "
                "Use this when the user says 'create a note', 'write this down', "
                "or 'add a note'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Note title"},
                    "content": {"type": "string", "description": "Note content (optional, defaults to empty)"},
                },
                "required": ["title"],
            },
        },
    },
    # ── 10. Get all notes ──
    {
        "type": "function",
        "function": {
            "name": "get_all_notes",
            "description": (
                "Get all notes (id, title, content, timestamps), ordered by "
                "most recently updated. Use when the user asks to 'show notes' "
                "or 'list my notes'."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    # ── 11. Get a single note ──
    {
        "type": "function",
        "function": {
            "name": "get_note",
            "description": (
                "Get a single note by its ID. Use get_all_notes first to find "
                "the ID. Use when the user asks 'show me note X' or 'read note X'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer", "description": "The note ID"},
                },
                "required": ["id"],
            },
        },
    },
    # ── 12. Update note ──
    {
        "type": "function",
        "function": {
            "name": "update_note",
            "description": (
                "Update a note's title and/or content by ID. Only provided "
                "fields are changed. Use get_all_notes first to find the ID."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer", "description": "The note ID to update"},
                    "title": {"type": "string", "description": "New title (optional)"},
                    "content": {"type": "string", "description": "New content (optional)"},
                },
                "required": ["id"],
            },
        },
    },
    # ── 13. Delete note ──
    {
        "type": "function",
        "function": {
            "name": "delete_note",
            "description": (
                "Delete a note by its ID. Use get_all_notes first to find the ID."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer", "description": "The note ID to delete"},
                },
                "required": ["id"],
            },
        },
    },
    # ── 14. Search notes ──
    {
        "type": "function",
        "function": {
            "name": "search_notes",
            "description": (
                "Search notes by title or content. Use when the user asks "
                "'find my note about X' or 'search for X in notes'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query"},
                },
                "required": ["query"],
            },
        },
    },
]


# ── Tool handlers ─────────────────────────────────────────────────

def execute_tool(name: str, arguments: dict) -> str:
    try:
        if name == "set_variables":
            date_str = arguments.pop("date", None) or db.today_str()
            entry = db.save_draft(date_str, arguments)
            return json.dumps({
                "success": True,
                "message": _format_entry_summary(entry, "Updated draft"),
            })

        elif name == "get_draft":
            date_str = arguments.get("date") or db.today_str()
            entry = db.get_draft(date_str)
            if not entry:
                return json.dumps({
                    "success": False,
                    "error": f"No draft found for {date_str}",
                })
            return json.dumps({
                "success": True,
                "message": _format_entry_summary(entry, f"Draft for {date_str}"),
            })

        elif name == "commit_draft":
            date_str = arguments.get("date") or db.today_str()
            entry = db.commit_draft(date_str)
            if not entry:
                return json.dumps({
                    "success": False,
                    "error": f"No draft found for {date_str} to commit",
                })
            return json.dumps({
                "success": True,
                "message": f"Committed entry for {date_str} | TS={entry['ts']}",
            })

        elif name == "get_all_entries":
            entries = db.get_committed_entries()
            return json.dumps({
                "success": True,
                "entries": entries,
                "count": len(entries),
                "message": f"Found {len(entries)} committed entries",
            })

        elif name == "calculate_ts":
            result = calculate_from_dict(arguments)
            return json.dumps({
                "success": True,
                "results": result,
                "message": (
                    f"Calculated: FS={result['fs']}, CD={result['cd']}, "
                    f"TES={result['tes']}, TS={result['ts']}"
                ),
            })

        elif name == "delete_entry":
            entry_id = int(arguments["id"])
            deleted = db.delete_entry(entry_id)
            if not deleted:
                return json.dumps({"success": False, "error": f"Entry {entry_id} not found"})
            return json.dumps({"success": True, "message": f"Deleted entry #{entry_id}"})

        elif name == "get_stats":
            days = arguments.get("days", 30)
            stats = db.get_stats(days)
            return json.dumps({
                "success": True,
                "stats": stats,
                "message": _format_stats(stats, days),
            })

        elif name == "get_today_date":
            return json.dumps({"success": True, "date": db.today_str()})

        elif name == "create_note":
            title = arguments.get("title", "")
            content = arguments.get("content", "")
            note = db.create_note(title, content)
            return json.dumps({
                "success": True,
                "message": f"Created note #{note['id']}: '{title}'",
            })

        elif name == "get_all_notes":
            notes = db.get_all_notes()
            return json.dumps({
                "success": True,
                "count": len(notes),
                "message": _format_notes_list(notes),
            })

        elif name == "get_note":
            note_id = int(arguments["id"])
            note = db.get_note(note_id)
            if not note:
                return json.dumps({"success": False, "error": f"Note {note_id} not found"})
            return json.dumps({
                "success": True,
                "message": f"Note #{note_id}: '{note['title']}'\n\n{note['content']}",
            })

        elif name == "update_note":
            note_id = int(arguments["id"])
            title = arguments.get("title")
            content = arguments.get("content")
            note = db.update_note(note_id, title, content)
            if not note:
                return json.dumps({"success": False, "error": f"Note {note_id} not found"})
            return json.dumps({
                "success": True,
                "message": f"Updated note #{note_id}: '{note['title']}'",
            })

        elif name == "delete_note":
            note_id = int(arguments["id"])
            deleted = db.delete_note(note_id)
            if not deleted:
                return json.dumps({"success": False, "error": f"Note {note_id} not found"})
            return json.dumps({"success": True, "message": f"Deleted note #{note_id}"})

        elif name == "search_notes":
            query = arguments.get("query", "")
            notes = db.search_notes(query)
            return json.dumps({
                "success": True,
                "count": len(notes),
                "message": _format_notes_list(notes, f"Search results for '{query}'"),
            })

        else:
            return json.dumps({"success": False, "error": f"Unknown tool: {name}"})

    except Exception as e:
        return json.dumps({"success": False, "error": str(e)})


def _format_entry_summary(entry: dict, prefix: str) -> str:
    """Human-readable summary of an entry for the AI."""
    parts = []
    for k in VARIABLES:
        v = entry.get(k)
        if v is not None:
            parts.append(f"{k.upper()}={v}")
    vars_str = ", ".join(parts) if parts else "no variables entered yet"
    ts = entry.get("ts")
    if ts is not None:
        ts_str = f"TS={ts}"
    else:
        ts_str = "TS=not calculable yet (enter FH first)"
    return f"{prefix}: {entry['date']} | {vars_str} | {ts_str}"


def _format_stats(stats: dict, days: int) -> str:
    if stats["count"] == 0:
        return f"No committed entries in the last {days} days"
    s = (
        f"Last {days} days: {stats['count']} entries, "
        f"avg TS={stats['avg']}, min={stats['min']}, max={stats['max']}, "
        f"latest={stats['latest']} ({stats['latest_date']}), "
        f"trend={stats['trend']:+.2f}"
    )
    if stats.get("var_avgs"):
        top_vars = sorted(stats["var_avgs"].items(), key=lambda x: abs(x[1]), reverse=True)[:5]
        vars_str = ", ".join(f"{k.upper()}={v}" for k, v in top_vars)
        s += f". Avg by var: {vars_str}"
    return s


def _format_notes_list(notes: list, prefix: str = "Notes") -> str:
    if not notes:
        return f"{prefix}: none found"
    lines = [f"{prefix}: {len(notes)} note(s)"]
    for n in notes:
        title = n.get("title", "(untitled)")
        content_preview = (n.get("content", "") or "")[:80]
        if content_preview:
            lines.append(f"  #{n['id']}: '{title}' — {content_preview}...")
        else:
            lines.append(f"  #{n['id']}: '{title}' (empty)")
    return "\n".join(lines)
