"""
AI Agent — OpenRouter tool-calling loop.

Sends user messages to OpenRouter with tool definitions, executes any
tool calls, feeds results back, and returns the final text response.
"""

import os
import json
from openai import OpenAI
from .tools import TOOL_SCHEMAS, execute_tool
from .calculator import VARIABLES, VARIABLE_LABELS, BMR

# ── Load .env (needed for uvicorn --reload which spawns a child process) ──
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    _env_path = os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), ".env")
    if os.path.exists(_env_path):
        with open(_env_path) as _f:
            for _line in _f:
                _line = _line.strip()
                if _line and not _line.startswith("#") and "=" in _line:
                    _k, _, _v = _line.partition("=")
                    os.environ.setdefault(_k.strip(), _v.strip())

# ── Config ────────────────────────────────────────────────────────

MAX_TOOL_ROUNDS = 15  # safety limit to prevent infinite loops


def _get_api_key():
    """Read the API key fresh from env (not cached at import time)."""
    return os.environ.get("OPENROUTER_API_KEY", "")


def _get_default_model():
    return os.environ.get("AI_MODEL", "openai/gpt-4o-mini")

SYSTEM_PROMPT = f"""\
You are the AI assistant for an Energy Habit Tracker app. You have the SAME \
capabilities as the user — you can set individual variables, view drafts, \
commit entries, and get statistics.

## Data model: DRAFT vs COMMITTED
- Each day has one DRAFT entry that the user builds up throughout the day.
- Variables can be null (not entered yet) or a number (entered, including 0).
- When the user hits "Save", the draft is COMMITTED as a permanent log.
- You edit the DRAFT. You can also commit it if the user asks.

## What TS is
TS is a daily energy/habit score computed from 12 component variables:
- Energy tab: FH (fasting hours), FAC, FIC, TAC, EC
- Other tab: B, NM, Trio (hours), FMF, SR, P, SM

## Formulas (BMR is fixed at {BMR})
- FS = (FH * (BMR / 24)) + FAC + FIC
- CD = (BMR + TAC) - EC  (CD = 0 if TAC or EC not entered yet)
- TES = (FS + CD) / 100
- TS = TES + B + NM + Trio + FMF + SR + P + SM
- TS only calculates if FH is entered. FAC/FIC default to 0 if not entered.

## Your tools
1. set_variables — Set one or more variables in today's DRAFT (partial update). \
PRIMARY tool. Use immediately when user gives any variable + value.
2. get_draft — Get today's draft (all vars + TS). Use for "what is my X today?"
3. commit_draft — Commit today's draft as permanent. Use when user says "save" \
or "commit" or "log today".
4. get_all_entries — List committed entries only.
5. calculate_ts — Preview without saving.
6. delete_entry — Delete a committed entry by ID.
7. get_stats — Stats for committed entries (avg, min, max, trend, per-var).
8. get_today_date — Get today's date.
9. create_note — Create a note with title and optional content.
10. get_all_notes — List all notes (id + title + preview).
11. get_note — Read a single note by ID.
12. update_note — Update a note's title and/or content by ID.
13. delete_note — Delete a note by ID.
14. search_notes — Search notes by title or content.

## Key behaviors
- When the user says "set FH to 16" → call set_variables immediately with ONLY \
that variable. Do NOT ask for other variables. A single variable is enough.
- When the user gives multiple variables → set them all in one call.
- NEVER refuse because "other variables are missing." Partial saves are always valid.
- When the user says "save" or "commit" or "I'm done for today" → call commit_draft.
- When the user asks "what is my X today?" → call get_draft.
- When the user asks for stats → call get_stats.
- When the user says "create a note" or "write this down" → call create_note \
with the title and content.
- When the user asks "show my notes" → call get_all_notes.
- When the user asks "find my note about X" → call search_notes.
- When editing/deleting notes → use get_all_notes first to find the ID.
- After set_variables, confirm what was saved and show the computed TS (if calculable).
- Be concise. Show numbers, not paragraphs.
"""


def get_client() -> OpenAI:
    """Get an OpenRouter client. Raises if API key is not set."""
    api_key = _get_api_key()
    if not api_key:
        raise ValueError(
            "OPENROUTER_API_KEY is not set. Add it to your .env file."
        )
    return OpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=api_key,
    )


def chat(user_message: str, history: list[dict] | None = None,
         model: str | None = None) -> dict:
    """
    Process a user message through the AI agent with tool-calling.

    Args:
        user_message: The user's input text.
        history: Previous conversation messages (list of {role, content}).
        model: Override the default model.

    Returns:
        dict with:
          - reply: The assistant's final text response
          - tool_calls: List of tool calls made (for transparency)
          - error: Error message if something went wrong
    """
    api_key = _get_api_key()
    if not api_key:
        return {
            "reply": (
                "⚠️ AI agent is not configured. Set OPENROUTER_API_KEY "
                "in your .env file to enable chat."
            ),
            "tool_calls": [],
            "error": "no_api_key",
        }

    client = get_client()
    use_model = model or _get_default_model()

    # Build message list
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    if history:
        messages.extend(history)
    messages.append({"role": "user", "content": user_message})

    all_tool_calls = []

    for _round in range(MAX_TOOL_ROUNDS):
        # After just 1 round of tool-calling, force the model to stop and respond
        if _round >= 1:
            tool_choice = "none"
        else:
            tool_choice = "auto"

        response = client.chat.completions.create(
            model=use_model,
            messages=messages,
            tools=TOOL_SCHEMAS,
            tool_choice=tool_choice,
            temperature=0.7,
        )

        msg = response.choices[0].message

        # If no tool calls, we're done — return the text response
        if not msg.tool_calls:
            return {
                "reply": msg.content or "",
                "tool_calls": all_tool_calls,
                "model": use_model,
            }

        # Add the assistant's message (with tool calls) to the conversation
        messages.append(msg.model_dump())

        # Execute each tool call
        for tc in msg.tool_calls:
            tool_name = tc.function.name
            try:
                tool_args = json.loads(tc.function.arguments)
            except json.JSONDecodeError:
                tool_args = {}

            result = execute_tool(tool_name, tool_args)

            all_tool_calls.append({
                "tool": tool_name,
                "arguments": tool_args,
                "result": json.loads(result),
            })

            # Feed the tool result back with a stop hint
            messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": result + "\n\nYou have the result. Respond to the user now. Do NOT call more tools.",
            })

    # If we hit the round limit, return what we have
    return {
        "reply": "I reached the maximum number of tool calls. Please try "
                 "simplifying your request.",
        "tool_calls": all_tool_calls,
        "model": use_model,
        "error": "max_rounds",
    }
