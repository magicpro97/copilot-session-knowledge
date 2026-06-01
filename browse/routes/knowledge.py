"""browse/routes/knowledge.py — /api/knowledge/* write endpoints (issue #897).

PUT /api/knowledge/{id} — inline entry editor: update title, content, tags, confidence.

Security:
- id validated as positive integer (no path traversal)
- All SQL uses parameterized queries (no interpolation)
- confidence clamped to [0.0, 1.0]
- title must be non-empty after strip
"""

import json
import os
import sys

if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")

from browse.core.registry import route


def _knowledge_table(db) -> str:
    """Return the correct knowledge table name (mirrors search_api.py pattern)."""
    try:
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "knowledge_entries" in tables:
            return "knowledge_entries"
    except Exception:
        pass
    return "knowledge"


def _json_error(message: str, status: int) -> tuple:
    return json.dumps({"error": message}).encode("utf-8"), "application/json", status


@route("/api/knowledge/{id}", methods=["PUT"])
def handle_knowledge_update(db, params, token, nonce, *, session_id: str) -> tuple:
    """PUT /api/knowledge/{id} — update a knowledge entry inline.

    Request body (JSON):
      {
        "title":       str   (required, non-empty after strip),
        "description": str   (optional, stored as content),
        "tags":        str   (optional, comma-separated),
        "confidence":  float (optional, 0.0–1.0)
      }

    Response (200): {"id": <int>, "updated": true}
    Response (400): {"error": "<reason>"}
    Response (404): {"error": "Entry not found"}
    """
    # ── Validate id ────────────────────────────────────────────────────────
    # Note: registry maps {id} placeholder → kwargs key 'session_id'
    id = session_id
    try:
        entry_id = int(id)
        if entry_id <= 0:
            raise ValueError
    except (ValueError, TypeError):
        return _json_error("id must be a positive integer", 400)

    # ── Parse body ─────────────────────────────────────────────────────────
    raw_body = params.get("_body", [""])[0]
    if not raw_body:
        return _json_error("request body must be JSON", 400)

    try:
        body = json.loads(raw_body)
    except json.JSONDecodeError as exc:
        return _json_error(f"invalid JSON: {exc}", 400)

    if not isinstance(body, dict):
        return _json_error("request body must be a JSON object", 400)

    # ── Validate fields ────────────────────────────────────────────────────
    title = body.get("title")
    if title is None:
        return _json_error("title is required", 400)
    if not isinstance(title, str) or not title.strip():
        return _json_error("title must be a non-empty string", 400)
    title = title.strip()

    description = body.get("description", "")
    if not isinstance(description, str):
        description = ""

    tags = body.get("tags", "")
    if not isinstance(tags, str):
        tags = ""

    raw_confidence = body.get("confidence")
    if raw_confidence is not None:
        try:
            confidence = float(raw_confidence)
        except (ValueError, TypeError):
            return _json_error("confidence must be a float", 400)
        if confidence < 0.0 or confidence > 1.0:
            return _json_error("confidence must be between 0.0 and 1.0", 400)
    else:
        confidence = None

    # ── Check entry exists ────────────────────────────────────────────────
    table = _knowledge_table(db)
    row = db.execute(f"SELECT id FROM {table} WHERE id = ?", (entry_id,)).fetchone()  # noqa: S608
    if row is None:
        return _json_error("Entry not found", 404)

    # ── Build UPDATE ───────────────────────────────────────────────────────
    if confidence is not None:
        db.execute(
            f"UPDATE {table} SET title = ?, content = ?, tags = ?, confidence = ? WHERE id = ?",  # noqa: S608
            (title, description, tags, confidence, entry_id),
        )
    else:
        db.execute(
            f"UPDATE {table} SET title = ?, content = ?, tags = ? WHERE id = ?",  # noqa: S608
            (title, description, tags, entry_id),
        )
    db.commit()

    payload = json.dumps({"id": entry_id, "updated": True})
    return payload.encode("utf-8"), "application/json", 200
