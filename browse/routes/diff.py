"""browse/routes/diff.py — GET /diff (HTML) + GET /api/diff (JSON) — F6 Checkpoint Diff."""
import difflib
import json
import os
import re
import sys
from pathlib import Path

if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")

from browse.core.registry import route
from browse.core.fts import _esc
from browse.core.templates import base_page

# Validation: session id — alphanumeric + dashes, ≤64 chars
_DIFF_SESSION_RE = re.compile(r"^[a-zA-Z0-9-]{1,64}$")
# Validation: checkpoint selector — alphanumeric + dashes + underscores, ≤128 chars
# Covers: 'latest', 'first', integer strings, and actual filenames without extension slashes
_CHECKPOINT_SEL_RE = re.compile(r"^[a-zA-Z0-9_-]{1,128}$")


def _session_state_dir() -> Path:
    """Return session-state root, honouring COPILOT_SESSION_STATE env var."""
    env = os.environ.get("COPILOT_SESSION_STATE")
    return Path(env) if env else Path.home() / ".copilot" / "session-state"


def _parse_index(index_path: Path) -> list[dict]:
    """Parse checkpoints/index.md into a list of {seq, title, file} dicts."""
    if not index_path.exists():
        return []
    entries = []
    for line in index_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        m = re.match(r"\|\s*(\d+)\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|", line)
        if m:
            entries.append({
                "seq": int(m.group(1)),
                "title": m.group(2).strip(),
                "file": m.group(3).strip(),
            })
    return entries


def _resolve_selector(entries: list[dict], selector: str) -> dict | None:
    """Resolve a checkpoint selector ('latest', 'first', or seq number) to an entry."""
    if not entries:
        return None
    sel = selector.strip().lower()
    if sel == "latest":
        return max(entries, key=lambda e: e["seq"])
    if sel == "first":
        return min(entries, key=lambda e: e["seq"])
    try:
        n = int(sel)
        for e in entries:
            if e["seq"] == n:
                return e
    except ValueError:
        pass
    return None


def _load_text(path: Path) -> str:
    if not path.exists():
        return ""
    try:
        return path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


def _unified_diff(text_a: str, text_b: str, label_a: str, label_b: str) -> str:
    lines_a = text_a.splitlines(keepends=True)
    lines_b = text_b.splitlines(keepends=True)
    return "".join(difflib.unified_diff(
        lines_a, lines_b,
        fromfile=label_a,
        tofile=label_b,
        lineterm="\n",
    ))


def _diff_stats(unified: str) -> dict:
    added = sum(
        1 for line in unified.splitlines()
        if line.startswith("+") and not line.startswith("+++")
    )
    removed = sum(
        1 for line in unified.splitlines()
        if line.startswith("-") and not line.startswith("---")
    )
    return {"added": added, "removed": removed}


def _parse_params(params: dict) -> tuple:
    """Extract and validate session, from, to from query params.

    Returns (session_id, from_sel, to_sel, error_response).
    error_response is None on success, or a (body, ct, status) triple on failure.
    """
    session_id = params.get("session", [""])[0].strip()
    from_sel = params.get("from", [""])[0].strip()
    to_sel = params.get("to", [""])[0].strip()

    if not session_id:
        return "", "", "", (b"400 Bad Request: missing 'session' parameter", "text/plain", 400)
    if not from_sel:
        return "", "", "", (b"400 Bad Request: missing 'from' parameter", "text/plain", 400)
    if not to_sel:
        return "", "", "", (b"400 Bad Request: missing 'to' parameter", "text/plain", 400)

    if not _DIFF_SESSION_RE.match(session_id):
        return "", "", "", (b"400 Bad Request: invalid session ID", "text/plain", 400)
    if not _CHECKPOINT_SEL_RE.match(from_sel):
        return "", "", "", (b"400 Bad Request: invalid 'from' checkpoint selector", "text/plain", 400)
    if not _CHECKPOINT_SEL_RE.match(to_sel):
        return "", "", "", (b"400 Bad Request: invalid 'to' checkpoint selector", "text/plain", 400)

    return session_id, from_sel, to_sel, None


def _resolve_diff(session_id: str, from_sel: str, to_sel: str) -> tuple:
    """Build diff data dict or return an error triple.

    Returns (diff_data: dict, None) on success, or (None, error_triple) on failure.
    """
    state = _session_state_dir()

    # Resolve canonical paths (defense-in-depth against path traversal)
    try:
        state_res = state.resolve()
        session_dir = (state / session_id).resolve()
    except Exception:
        return None, (b"400 Bad Request: could not resolve path", "text/plain", 400)

    # Ensure session_dir is strictly under state_res
    try:
        session_dir.relative_to(state_res)
    except ValueError:
        return None, (b"403 Forbidden: path traversal detected", "text/plain", 403)

    if not session_dir.is_dir():
        return None, (b"404 Not Found: session not found", "text/plain", 404)

    cp_dir = session_dir / "checkpoints"
    entries = _parse_index(cp_dir / "index.md")
    if not entries:
        return None, (b"404 Not Found: no checkpoints for this session", "text/plain", 404)

    entry_a = _resolve_selector(entries, from_sel)
    if entry_a is None:
        return None, (b"404 Not Found: 'from' checkpoint not found", "text/plain", 404)
    entry_b = _resolve_selector(entries, to_sel)
    if entry_b is None:
        return None, (b"404 Not Found: 'to' checkpoint not found", "text/plain", 404)

    # Resolve checkpoint file paths and guard against traversal in filenames
    try:
        cp_dir_res = cp_dir.resolve()
        file_a = (cp_dir / entry_a["file"]).resolve()
        file_b = (cp_dir / entry_b["file"]).resolve()
    except Exception:
        return None, (b"400 Bad Request: could not resolve checkpoint paths", "text/plain", 400)

    try:
        file_a.relative_to(cp_dir_res)
        file_b.relative_to(cp_dir_res)
    except ValueError:
        return None, (b"400 Bad Request: invalid checkpoint file path", "text/plain", 400)

    text_a = _load_text(file_a)
    text_b = _load_text(file_b)
    label_a = f"a/{entry_a['file']}"
    label_b = f"b/{entry_b['file']}"

    unified = _unified_diff(text_a, text_b, label_a, label_b)
    stats = _diff_stats(unified)
    files = [{"from": label_a, "to": label_b}] if unified else []

    return {
        "session_id": session_id,
        "from": {
            "seq": entry_a["seq"],
            "title": entry_a["title"],
            "file": entry_a["file"],
        },
        "to": {
            "seq": entry_b["seq"],
            "title": entry_b["title"],
            "file": entry_b["file"],
        },
        "unified_diff": unified,
        "files": files,
        "stats": stats,
    }, None


@route("/api/diff", methods=["GET"])
def handle_api_diff(db, params, token, nonce) -> tuple:
    session_id, from_sel, to_sel, err = _parse_params(params)
    if err:
        return err

    data, err = _resolve_diff(session_id, from_sel, to_sel)
    if err:
        return err

    return json.dumps(data).encode("utf-8"), "application/json", 200


@route("/diff", methods=["GET"])
def handle_diff(db, params, token, nonce) -> tuple:
    session_id, from_sel, to_sel, err = _parse_params(params)
    if err:
        return err

    data, err = _resolve_diff(session_id, from_sel, to_sel)
    if err:
        return err

    sid_esc = _esc(session_id)
    from_label = _esc(f"{data['from']['seq']}: {data['from']['title']}")
    to_label = _esc(f"{data['to']['seq']}: {data['to']['title']}")
    tok_esc = _esc(token)
    stats = data["stats"]

    # Embed diff string safely in JS (escape < to prevent </script> injection)
    unified_json = json.dumps(data["unified_diff"]).replace("<", r"\u003c")

    head_extra = (
        '<link rel="stylesheet" href="/static/vendor/diff2html.min.css">\n'
        '<link rel="stylesheet" href="/static/vendor/prism.min.css">\n'
    )

    main_content = (
        f'<p class="meta">'
        f"<b>Session:</b> {sid_esc} &nbsp; "
        f"<b>From:</b> {from_label} &nbsp; "
        f"<b>To:</b> {to_label}"
        f"</p>\n"
        f'<p class="meta">'
        f'<span id="stat-added" style="color:green;">+{stats["added"]} added</span> &nbsp; '
        f'<span id="stat-removed" style="color:red;">-{stats["removed"]} removed</span>'
        f"</p>\n"
        f'<div id="diff-controls" style="margin-bottom:0.75rem;">\n'
        f'  <label style="margin-right:1rem;">'
        f'<input type="radio" name="diff-view" value="side-by-side" checked> Side-by-side'
        f"</label>\n"
        f'  <label>'
        f'<input type="radio" name="diff-view" value="line-by-line"> Line-by-line'
        f"</label>\n"
        f"</div>\n"
        f'<div id="diff-output"></div>\n'
    )

    body_scripts = (
        f'<script nonce="{nonce}" src="/static/vendor/diff2html.min.js"></script>\n'
        f'<script nonce="{nonce}" src="/static/vendor/prism.min.js"></script>\n'
        f'<script nonce="{nonce}">\n'
        f"window.__diffData = {unified_json};\n"
        f'window.__diffSession = "{sid_esc}";\n'
        f"</script>\n"
        f'<script nonce="{nonce}" src="/static/js/diff.js"></script>\n'
    )

    page_title = (
        f"Diff \u2014 {session_id[:8]} "
        f"[{data['from']['seq']} \u2192 {data['to']['seq']}]"
    )

    return (
        base_page(
            nonce,
            page_title,
            main_content=main_content,
            head_extra=head_extra,
            body_scripts=body_scripts,
            token=token,
        ),
        "text/html; charset=utf-8",
        200,
    )
