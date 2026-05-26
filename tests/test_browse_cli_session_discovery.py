#!/usr/bin/env python3
"""tests/test_browse_cli_session_discovery.py — Tests for issue #528 CLI session discovery.

Covers browse/core/operator_console.py: discover_cli_sessions, get_cli_session_by_id,
_parse_flat_yaml, _safe_workspace_hint, _safe_repository_hint, _read_workspace_yaml_safe.
And browse/api/operator.py: GET /api/operator/cli-sessions (debug=True),
                            GET /api/operator/cli-sessions/{cli_session_id} (debug=True).

Tests:
  CSU1:  discover_cli_sessions returns envelope with sessions/count/truncated keys
  CSU2:  valid UUID4 dir with workspace.yaml is discovered
  CSU3:  operator-console dir is excluded
  CSU4:  non-UUID dir is excluded
  CSU5:  uppercase UUID dir is excluded (UUID4 must be lowercase)
  CSU6:  symlink dir is excluded (skipped on Windows if symlink creation fails)
  CSU7:  workspace.yaml symlink is excluded (skipped on Windows if symlink fails)
  CSU8:  oversized workspace.yaml (>64 KiB) is skipped
  CSU9:  binary/non-UTF-8 workspace.yaml is skipped
  CSU10: workspace_hint strips /Users/<username> (macOS path)
  CSU11: workspace_hint strips /home/<username> (Linux path)
  CSU12: redact_secrets applied to title (GitHub tokens redacted)
  CSU13: yaml id != dir UUID → candidate excluded
  CSU14: yaml id absent with no useful fields → excluded
  CSU15: yaml id absent with useful fields → included
  CSU16: truncated flag when candidates exceed limit
  CSU17: sessions sorted most-recently-modified first
  CSU18: no mutation proof — directory hash identical before and after discovery
  CSU19: get_cli_session_by_id returns None for invalid UUID
  CSU20: get_cli_session_by_id returns None for non-existent session
  CSU21: get_cli_session_by_id returns candidate for valid session
  CSU22: _parse_flat_yaml handles quoted and unquoted values
  CSU23: _parse_flat_yaml ignores comments and blank lines
  CSU24: _safe_workspace_hint strips home dir prefix
  CSU25: _safe_repository_hint handles owner/repo, .git suffix, full path

  CSH1:  GET /api/operator/cli-sessions — no auth → 401 (no UUID/summary leakage)
  CSH2:  GET /api/operator/cli-sessions — ?token= auth → 401 (rejected for debug routes)
  CSH3:  GET /api/operator/cli-sessions — Bearer auth → 200, correct envelope
  CSH4:  GET /api/operator/cli-sessions — cookie auth → 200
  CSH5:  GET /api/operator/cli-sessions — wrong Bearer → 401
  CSH6:  GET /healthz is unauthenticated, does not leak fixture UUID/summary
  CSH7:  GET /.well-known/browse-host unauthenticated, does not leak fixture UUID/summary
  CSH8:  GET /api/operator/cli-sessions/{id} — valid UUID → 200
  CSH9:  GET /api/operator/cli-sessions/{id} — unknown UUID → 404
  CSH10: GET /api/operator/cli-sessions response has no full /Users/<name> paths
  CSH11: Empty server token loopback access is still rejected for discovery routes
"""

import hashlib
import http.client
import json
import os
import sqlite3
import sys
import tempfile
import threading
import time
import uuid
from http.server import ThreadingHTTPServer
from pathlib import Path

if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).parent.parent))

# ── Isolated test state dirs ─────────────────────────────────────────────────
# Use two temp dirs:
#   _CLI_STATE_DIR  — simulates ~/.copilot/session-state  (COPILOT_SESSION_STATE)
#   _OP_STATE_DIR   — simulates operator state            (COPILOT_OPERATOR_STATE)
# Both are set BEFORE importing operator_console to avoid cross-contamination.

_CLI_STATE_DIR_HANDLE = tempfile.TemporaryDirectory()
_CLI_STATE_DIR = Path(_CLI_STATE_DIR_HANDLE.name)
os.environ["COPILOT_SESSION_STATE"] = str(_CLI_STATE_DIR)

_OP_STATE_DIR_HANDLE = tempfile.TemporaryDirectory()
_OP_STATE_DIR = Path(_OP_STATE_DIR_HANDLE.name)
os.environ["COPILOT_OPERATOR_STATE"] = str(_OP_STATE_DIR)

import browse.api.operator  # noqa: E402 — registers routes (incl. new cli-sessions routes)
import browse.routes.health  # noqa: E402 — healthz route

from browse.core.operator_console import (  # noqa: E402
    _parse_flat_yaml,
    _safe_repository_hint,
    _safe_workspace_hint,
    discover_cli_sessions,
    get_cli_session_by_id,
)
from browse.core.server import _make_handler_class  # noqa: E402

_PASS = 0
_FAIL = 0


def test(name: str, expr: bool) -> None:
    global _PASS, _FAIL
    if expr:
        _PASS += 1
        print(f"  PASS  {name}")
    else:
        _FAIL += 1
        print(f"  FAIL  {name}")


# ── Fixture helpers ──────────────────────────────────────────────────────────


def _make_session_dir(root: Path, session_uuid: str, yaml_content: str | None = None) -> Path:
    """Create a CLI session directory with optional workspace.yaml content."""
    d = root / session_uuid
    d.mkdir(parents=True, exist_ok=True)
    if yaml_content is not None:
        (d / "workspace.yaml").write_text(yaml_content, encoding="utf-8")
    return d


_MINIMAL_YAML = """\
id: {uuid}
title: Test session title
workspace: /home/testuser/projects/myapp
branch: main
repository: owner/myapp
"""

_TOKEN = "test-cli-discovery-token"


def _make_test_db() -> sqlite3.Connection:
    db = sqlite3.connect(":memory:", check_same_thread=False)
    db.row_factory = sqlite3.Row
    return db


def _make_test_server(token: str = _TOKEN) -> tuple:
    db = _make_test_db()
    HandlerClass = _make_handler_class(db, token)
    server = ThreadingHTTPServer(("127.0.0.1", 0), HandlerClass)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    return server, server.server_address[1]


def _bearer(port: int, path: str, token: str = _TOKEN) -> http.client.HTTPResponse:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    conn.request("GET", path, headers={"Authorization": f"Bearer {token}"})
    return conn.getresponse()


def _cookie(port: int, path: str, token: str = _TOKEN) -> http.client.HTTPResponse:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    conn.request("GET", path, headers={"Cookie": f"browse_token={token}"})
    return conn.getresponse()


def _no_auth(port: int, path: str) -> http.client.HTTPResponse:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    conn.request("GET", path)
    return conn.getresponse()


def _token_qs(port: int, path: str, token: str = _TOKEN) -> http.client.HTTPResponse:
    """Request with ?token= query-string auth (must be rejected for debug routes)."""
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    sep = "&" if "?" in path else "?"
    conn.request("GET", f"{path}{sep}token={token}")
    return conn.getresponse()


def _hash_dir_state(root: Path) -> str:
    """Compute a deterministic hash of file names, sizes, and mtimes under root."""
    h = hashlib.sha256()
    try:
        for entry in sorted(root.rglob("*")):
            try:
                st = entry.stat()
                info = f"{entry.relative_to(root)}:{st.st_size}:{st.st_mtime}"
            except OSError:
                info = f"{entry.relative_to(root)}:error"
            h.update(info.encode("utf-8", errors="replace"))
    except OSError:
        pass
    return h.hexdigest()


def _fresh_uuid() -> str:
    return str(uuid.uuid4())


# ── Unit tests ───────────────────────────────────────────────────────────────


def test_csu1_discover_returns_envelope():
    """CSU1: discover_cli_sessions returns dict with sessions/count/truncated."""
    # Use a totally empty sub-dir to avoid picking up real sessions
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["COPILOT_SESSION_STATE"] = tmp
        try:
            result = discover_cli_sessions()
        finally:
            os.environ["COPILOT_SESSION_STATE"] = str(_CLI_STATE_DIR)
    test("CSU1: result is dict", isinstance(result, dict))
    test("CSU1: sessions key present", "sessions" in result)
    test("CSU1: count key present", "count" in result)
    test("CSU1: truncated key present", "truncated" in result)
    test("CSU1: sessions is list", isinstance(result.get("sessions"), list))
    test("CSU1: count equals len(sessions)", result.get("count") == len(result.get("sessions", [])))


def test_csu2_valid_session_discovered():
    """CSU2: valid UUID4 dir with workspace.yaml is returned by discovery."""
    with tempfile.TemporaryDirectory() as tmp:
        sid = _fresh_uuid()
        yaml = _MINIMAL_YAML.format(uuid=sid)
        _make_session_dir(Path(tmp), sid, yaml)
        os.environ["COPILOT_SESSION_STATE"] = tmp
        try:
            result = discover_cli_sessions()
        finally:
            os.environ["COPILOT_SESSION_STATE"] = str(_CLI_STATE_DIR)
    sessions = result.get("sessions", [])
    test("CSU2: session found", len(sessions) == 1)
    if sessions:
        s = sessions[0]
        test("CSU2: cli_session_id present", s.get("cli_session_id") == sid)
        test("CSU2: title present", isinstance(s.get("title"), str))
        test("CSU2: mtime present", bool(s.get("mtime")))
        test("CSU2: workspace_hint present", isinstance(s.get("workspace_hint"), str))
        test("CSU2: branch present", isinstance(s.get("branch"), str))
        test("CSU2: repository present", isinstance(s.get("repository"), str))
        test("CSU2: _mtime_epoch NOT in response", "_mtime_epoch" not in s)


def test_csu3_operator_console_excluded():
    """CSU3: operator-console directory is excluded from discovery."""
    with tempfile.TemporaryDirectory() as tmp:
        # Create a non-UUID 'operator-console' dir that should be skipped
        oc = Path(tmp) / "operator-console"
        oc.mkdir()
        # Also create a valid session so we know discovery works
        sid = _fresh_uuid()
        yaml = _MINIMAL_YAML.format(uuid=sid)
        _make_session_dir(Path(tmp), sid, yaml)
        os.environ["COPILOT_SESSION_STATE"] = tmp
        try:
            result = discover_cli_sessions()
        finally:
            os.environ["COPILOT_SESSION_STATE"] = str(_CLI_STATE_DIR)
    ids = [s.get("cli_session_id") for s in result.get("sessions", [])]
    test("CSU3: operator-console not in ids", "operator-console" not in ids)
    test("CSU3: valid session still found", sid in ids)


def test_csu4_non_uuid_dir_excluded():
    """CSU4: non-UUID directory names are excluded."""
    with tempfile.TemporaryDirectory() as tmp:
        # Create various non-UUID dirs
        for bad_name in ("random-name", "12345", "worktree-1", "backup"):
            bad = Path(tmp) / bad_name
            bad.mkdir()
            (bad / "workspace.yaml").write_text("title: ignored\n", encoding="utf-8")
        # Valid session
        sid = _fresh_uuid()
        _make_session_dir(Path(tmp), sid, _MINIMAL_YAML.format(uuid=sid))
        os.environ["COPILOT_SESSION_STATE"] = tmp
        try:
            result = discover_cli_sessions()
        finally:
            os.environ["COPILOT_SESSION_STATE"] = str(_CLI_STATE_DIR)
    ids = [s.get("cli_session_id") for s in result.get("sessions", [])]
    test("CSU4: only valid UUID session returned", ids == [sid])


def test_csu5_uppercase_uuid_excluded():
    """CSU5: uppercase UUID directories are excluded (must be lowercase)."""
    with tempfile.TemporaryDirectory() as tmp:
        sid = _fresh_uuid()
        upper_sid = sid.upper()
        upper_dir = Path(tmp) / upper_sid
        upper_dir.mkdir()
        (upper_dir / "workspace.yaml").write_text(f"id: {upper_sid}\ntitle: upper\n", encoding="utf-8")
        # Add a valid lowercase session too
        lower_sid = _fresh_uuid()
        _make_session_dir(Path(tmp), lower_sid, _MINIMAL_YAML.format(uuid=lower_sid))
        os.environ["COPILOT_SESSION_STATE"] = tmp
        try:
            result = discover_cli_sessions()
        finally:
            os.environ["COPILOT_SESSION_STATE"] = str(_CLI_STATE_DIR)
    ids = [s.get("cli_session_id") for s in result.get("sessions", [])]
    test("CSU5: uppercase UUID not in result", upper_sid not in ids)
    test("CSU5: lowercase UUID found", lower_sid in ids)


def test_csu6_symlink_dir_excluded():
    """CSU6: symlink directories are excluded."""
    if os.name == "nt":
        test("CSU6: symlink dir excluded (skipped on Windows)", True)
        return
    with tempfile.TemporaryDirectory() as tmp:
        real_sid = _fresh_uuid()
        real_dir = Path(tmp) / real_sid
        real_dir.mkdir()
        (real_dir / "workspace.yaml").write_text(_MINIMAL_YAML.format(uuid=real_sid), encoding="utf-8")

        # Symlink to the real dir using a different (but valid) UUID
        symlink_sid = _fresh_uuid()
        symlink_dir = Path(tmp) / symlink_sid
        try:
            symlink_dir.symlink_to(real_dir)
            symlink_created = True
        except (OSError, NotImplementedError):
            symlink_created = False

        os.environ["COPILOT_SESSION_STATE"] = tmp
        try:
            result = discover_cli_sessions()
        finally:
            os.environ["COPILOT_SESSION_STATE"] = str(_CLI_STATE_DIR)

    ids = [s.get("cli_session_id") for s in result.get("sessions", [])]
    if symlink_created:
        test("CSU6: symlink dir not in results", symlink_sid not in ids)
        test("CSU6: real dir still returned", real_sid in ids)
    else:
        test("CSU6: symlink dir excluded (symlink creation failed; skipped)", True)


def test_csu7_workspace_yaml_symlink_excluded():
    """CSU7: symlink workspace.yaml is excluded."""
    if os.name == "nt":
        test("CSU7: workspace.yaml symlink excluded (skipped on Windows)", True)
        return
    with tempfile.TemporaryDirectory() as tmp:
        # Real session
        real_sid = _fresh_uuid()
        real_dir = Path(tmp) / real_sid
        real_dir.mkdir()
        (real_dir / "workspace.yaml").write_text(_MINIMAL_YAML.format(uuid=real_sid), encoding="utf-8")

        # Session where workspace.yaml is a symlink to a real file
        sym_sid = _fresh_uuid()
        sym_dir = Path(tmp) / sym_sid
        sym_dir.mkdir()
        target_file = Path(tmp) / "_real_yaml.yaml"
        target_file.write_text(_MINIMAL_YAML.format(uuid=sym_sid), encoding="utf-8")
        sym_yaml = sym_dir / "workspace.yaml"
        try:
            sym_yaml.symlink_to(target_file)
            symlink_created = True
        except (OSError, NotImplementedError):
            symlink_created = False

        os.environ["COPILOT_SESSION_STATE"] = tmp
        try:
            result = discover_cli_sessions()
        finally:
            os.environ["COPILOT_SESSION_STATE"] = str(_CLI_STATE_DIR)

    ids = [s.get("cli_session_id") for s in result.get("sessions", [])]
    if symlink_created:
        test("CSU7: symlink workspace.yaml session excluded", sym_sid not in ids)
        test("CSU7: real workspace.yaml session included", real_sid in ids)
    else:
        test("CSU7: workspace.yaml symlink excluded (creation failed; skipped)", True)


def test_csu8_oversized_workspace_yaml_skipped():
    """CSU8: workspace.yaml > 64 KiB is skipped."""
    with tempfile.TemporaryDirectory() as tmp:
        sid = _fresh_uuid()
        d = Path(tmp) / sid
        d.mkdir()
        # Write 65 KiB of valid-looking YAML
        big_content = ("x: " + "a" * 70) * (65 * 1024 // 75 + 1)
        (d / "workspace.yaml").write_bytes(big_content.encode("utf-8")[: 65 * 1024 + 1])
        os.environ["COPILOT_SESSION_STATE"] = tmp
        try:
            result = discover_cli_sessions()
        finally:
            os.environ["COPILOT_SESSION_STATE"] = str(_CLI_STATE_DIR)
    ids = [s.get("cli_session_id") for s in result.get("sessions", [])]
    test("CSU8: oversized session not returned", sid not in ids)


def test_csu9_binary_workspace_yaml_skipped():
    """CSU9: non-UTF-8 binary workspace.yaml is skipped."""
    with tempfile.TemporaryDirectory() as tmp:
        sid = _fresh_uuid()
        d = Path(tmp) / sid
        d.mkdir()
        # Write binary content that cannot decode as UTF-8
        (d / "workspace.yaml").write_bytes(bytes(range(256)))
        os.environ["COPILOT_SESSION_STATE"] = tmp
        try:
            result = discover_cli_sessions()
        finally:
            os.environ["COPILOT_SESSION_STATE"] = str(_CLI_STATE_DIR)
    ids = [s.get("cli_session_id") for s in result.get("sessions", [])]
    test("CSU9: binary workspace.yaml session not returned", sid not in ids)


def test_csu10_workspace_hint_strips_users_prefix():
    """CSU10: workspace_hint strips /Users/<username>/ from macOS-style paths."""
    # Real home dir
    home = str(Path.home())
    hint = _safe_workspace_hint(f"{home}/projects/myapp")
    test("CSU10: home/projects/myapp → projects/myapp", "projects/myapp" in hint)
    test("CSU10: username not in hint", Path.home().name not in hint)

    # Synthetic /Users/<other>/ path
    hint2 = _safe_workspace_hint("/Users/alice/projects/myapp")
    test("CSU10: /Users/alice/ stripped", "alice" not in hint2)
    test("CSU10: /Users/alice/projects/myapp hint is projects/myapp", hint2 == "projects/myapp")


def test_csu11_workspace_hint_strips_home_prefix():
    """CSU11: workspace_hint strips /home/<username>/ (Linux-style paths)."""
    hint = _safe_workspace_hint("/home/alice/code/project")
    test("CSU11: /home/alice/ stripped", "alice" not in hint)
    test("CSU11: last 2 components kept", hint == "code/project")


def test_csu12_title_redaction():
    """CSU12: redact_secrets is applied to title — GitHub tokens are redacted."""
    with tempfile.TemporaryDirectory() as tmp:
        sid = _fresh_uuid()
        token_val = "ghp_" + "A" * 36
        yaml = f"id: {sid}\ntitle: use token {token_val} please\nbranch: main\n"
        _make_session_dir(Path(tmp), sid, yaml)
        os.environ["COPILOT_SESSION_STATE"] = tmp
        try:
            result = discover_cli_sessions()
        finally:
            os.environ["COPILOT_SESSION_STATE"] = str(_CLI_STATE_DIR)
    sessions = result.get("sessions", [])
    test("CSU12: session found", len(sessions) == 1)
    if sessions:
        title = sessions[0].get("title", "")
        test("CSU12: raw token not in title", token_val not in title)
        test("CSU12: REDACTED marker in title", "[REDACTED]" in title)


def test_csu13_yaml_id_mismatch_excluded():
    """CSU13: when yaml id != dir UUID, candidate is excluded."""
    with tempfile.TemporaryDirectory() as tmp:
        sid = _fresh_uuid()
        other_id = _fresh_uuid()
        yaml = f"id: {other_id}\ntitle: mismatch\nbranch: main\n"
        _make_session_dir(Path(tmp), sid, yaml)
        os.environ["COPILOT_SESSION_STATE"] = tmp
        try:
            result = discover_cli_sessions()
        finally:
            os.environ["COPILOT_SESSION_STATE"] = str(_CLI_STATE_DIR)
    ids = [s.get("cli_session_id") for s in result.get("sessions", [])]
    test("CSU13: id-mismatch session excluded", sid not in ids)


def test_csu14_no_useful_fields_excluded():
    """CSU14: yaml with no id and no useful fields → excluded."""
    with tempfile.TemporaryDirectory() as tmp:
        sid = _fresh_uuid()
        # Only unknown keys — none in the useful-fields allowlist
        yaml = "some_unknown_key: some_value\nanother: thing\n"
        _make_session_dir(Path(tmp), sid, yaml)
        os.environ["COPILOT_SESSION_STATE"] = tmp
        try:
            result = discover_cli_sessions()
        finally:
            os.environ["COPILOT_SESSION_STATE"] = str(_CLI_STATE_DIR)
    ids = [s.get("cli_session_id") for s in result.get("sessions", [])]
    test("CSU14: no-useful-fields session excluded", sid not in ids)


def test_csu15_no_id_with_useful_fields_included():
    """CSU15: yaml with no id but with useful fields (e.g. branch) → included."""
    with tempfile.TemporaryDirectory() as tmp:
        sid = _fresh_uuid()
        # No id field, but has branch (a useful field)
        yaml = "title: no-id session\nbranch: feature/test\n"
        _make_session_dir(Path(tmp), sid, yaml)
        os.environ["COPILOT_SESSION_STATE"] = tmp
        try:
            result = discover_cli_sessions()
        finally:
            os.environ["COPILOT_SESSION_STATE"] = str(_CLI_STATE_DIR)
    ids = [s.get("cli_session_id") for s in result.get("sessions", [])]
    test("CSU15: no-id-but-useful session included", sid in ids)


def test_csu16_truncation_flag():
    """CSU16: truncated=True when candidates exceed the limit."""
    with tempfile.TemporaryDirectory() as tmp:
        # Create 5 sessions with a limit of 3
        sids = []
        for _ in range(5):
            sid = _fresh_uuid()
            _make_session_dir(Path(tmp), sid, _MINIMAL_YAML.format(uuid=sid))
            sids.append(sid)
        os.environ["COPILOT_SESSION_STATE"] = tmp
        try:
            result_all = discover_cli_sessions(limit=5)
            result_small = discover_cli_sessions(limit=3)
        finally:
            os.environ["COPILOT_SESSION_STATE"] = str(_CLI_STATE_DIR)
    test("CSU16: no truncation at limit=5", result_all.get("truncated") is False)
    test("CSU16: truncated at limit=3", result_small.get("truncated") is True)
    test("CSU16: only 3 sessions returned at limit=3", result_small.get("count") == 3)


def test_csu17_sorted_most_recent_first():
    """CSU17: sessions are sorted most-recently-modified first."""
    with tempfile.TemporaryDirectory() as tmp:
        sid_old = _fresh_uuid()
        sid_new = _fresh_uuid()
        old_dir = _make_session_dir(Path(tmp), sid_old, _MINIMAL_YAML.format(uuid=sid_old))
        # Backdate workspace.yaml so file mtime sort sees it as older
        old_ts = time.time() - 3600
        os.utime(str(old_dir / "workspace.yaml"), (old_ts, old_ts))
        # new_dir gets current mtime
        _make_session_dir(Path(tmp), sid_new, _MINIMAL_YAML.format(uuid=sid_new))
        os.environ["COPILOT_SESSION_STATE"] = tmp
        try:
            result = discover_cli_sessions()
        finally:
            os.environ["COPILOT_SESSION_STATE"] = str(_CLI_STATE_DIR)
    sessions = result.get("sessions", [])
    test("CSU17: two sessions found", len(sessions) == 2)
    if len(sessions) == 2:
        test("CSU17: newer session first", sessions[0].get("cli_session_id") == sid_new)
        test("CSU17: older session second", sessions[1].get("cli_session_id") == sid_old)


def test_csu18_no_mutation():
    """CSU18: discover_cli_sessions does not mutate the CLI session tree."""
    with tempfile.TemporaryDirectory() as tmp:
        sid = _fresh_uuid()
        _make_session_dir(Path(tmp), sid, _MINIMAL_YAML.format(uuid=sid))
        os.environ["COPILOT_SESSION_STATE"] = tmp
        try:
            h_before = _hash_dir_state(Path(tmp))
            discover_cli_sessions()
            h_after = _hash_dir_state(Path(tmp))
        finally:
            os.environ["COPILOT_SESSION_STATE"] = str(_CLI_STATE_DIR)
    test("CSU18: directory state unchanged after discovery", h_before == h_after)


def test_csu19_get_cli_session_invalid_uuid():
    """CSU19: get_cli_session_by_id returns None for invalid UUID."""
    test("CSU19: empty string → None", get_cli_session_by_id("") is None)
    test("CSU19: not a UUID → None", get_cli_session_by_id("not-a-uuid") is None)
    test("CSU19: uppercase UUID → None", get_cli_session_by_id(_fresh_uuid().upper()) is None)
    test("CSU19: injection attempt → None", get_cli_session_by_id("'; DROP TABLE--") is None)


def test_csu20_get_cli_session_nonexistent():
    """CSU20: get_cli_session_by_id returns None for non-existent session."""
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["COPILOT_SESSION_STATE"] = tmp
        try:
            result = get_cli_session_by_id(_fresh_uuid())
        finally:
            os.environ["COPILOT_SESSION_STATE"] = str(_CLI_STATE_DIR)
    test("CSU20: non-existent session → None", result is None)


def test_csu21_get_cli_session_valid():
    """CSU21: get_cli_session_by_id returns candidate for valid session."""
    with tempfile.TemporaryDirectory() as tmp:
        sid = _fresh_uuid()
        _make_session_dir(Path(tmp), sid, _MINIMAL_YAML.format(uuid=sid))
        os.environ["COPILOT_SESSION_STATE"] = tmp
        try:
            result = get_cli_session_by_id(sid)
        finally:
            os.environ["COPILOT_SESSION_STATE"] = str(_CLI_STATE_DIR)
    test("CSU21: result is dict", isinstance(result, dict))
    if result:
        test("CSU21: cli_session_id matches", result.get("cli_session_id") == sid)
        test("CSU21: _mtime_epoch not exposed", "_mtime_epoch" not in result)
        test("CSU21: title present", isinstance(result.get("title"), str))
        test("CSU21: branch is main", result.get("branch") == "main")
        test("CSU21: repository hint safe", result.get("repository") == "owner/myapp")


def test_csu22_parse_flat_yaml_values():
    """CSU22: _parse_flat_yaml handles quoted and unquoted values."""
    text = """\
id: abc-123
title: "Quoted title"
branch: 'single-quoted'
workspace: /home/user/project
repository: owner/repo
unknown_key: ignored
"""
    result = _parse_flat_yaml(text)
    test("CSU22: id parsed", result.get("id") == "abc-123")
    test("CSU22: title double-quoted stripped", result.get("title") == "Quoted title")
    test("CSU22: branch single-quoted stripped", result.get("branch") == "single-quoted")
    test("CSU22: workspace parsed", result.get("workspace") == "/home/user/project")
    test("CSU22: repository parsed", result.get("repository") == "owner/repo")
    test("CSU22: unknown_key not in result", "unknown_key" not in result)


def test_csu23_parse_flat_yaml_ignores_noise():
    """CSU23: _parse_flat_yaml ignores comments, blanks, list items."""
    text = """\
# This is a comment
id: real-id

- list item
title: Real title
nested:
  child: value
"""
    result = _parse_flat_yaml(text)
    test("CSU23: comment line skipped", "This is a comment" not in str(result))
    test("CSU23: id parsed despite noise", result.get("id") == "real-id")
    test("CSU23: title parsed", result.get("title") == "Real title")
    test("CSU23: nested key not in result", "nested" not in result)
    test("CSU23: list item not in result", "list item" not in str(result))


def test_csu24_safe_workspace_hint():
    """CSU24: _safe_workspace_hint strips home dir prefix."""
    home = str(Path.home())
    # Home subdirectory
    hint = _safe_workspace_hint(f"{home}/src/project")
    test("CSU24: home/src/project → src/project", hint == "src/project")
    # Short path (only 1 component after home)
    hint2 = _safe_workspace_hint(f"{home}/myproject")
    test("CSU24: single component preserved", hint2 == "myproject")
    # Exactly home
    hint3 = _safe_workspace_hint(home)
    test("CSU24: home alone → ~", hint3 == "~")
    # Empty path
    hint4 = _safe_workspace_hint("")
    test("CSU24: empty → empty", hint4 == "")
    # Deep path: only last 2 components
    hint5 = _safe_workspace_hint(f"{home}/a/b/c/d/e")
    test("CSU24: deep path → last 2", hint5 == "d/e")


def test_csu25_safe_repository_hint():
    """CSU25: _safe_repository_hint handles owner/repo, .git suffix, full paths."""
    test("CSU25: owner/repo unchanged", _safe_repository_hint("owner/repo") == "owner/repo")
    test("CSU25: owner/repo.git strips .git", _safe_repository_hint("owner/repo.git") == "owner/repo")
    test("CSU25: full path returns last name", _safe_repository_hint("/Users/alice/projects/myrepo") == "myrepo")
    test("CSU25: full path with .git stripped", _safe_repository_hint("/home/bob/myrepo.git") == "myrepo")
    test("CSU25: bare name unchanged", _safe_repository_hint("myrepo") == "myrepo")
    test("CSU25: empty → empty", _safe_repository_hint("") == "")


# ── HTTP tests ────────────────────────────────────────────────────────────────


def _setup_server_with_fixture() -> tuple:
    """Create a test server and add a fixture CLI session. Returns (server, port, fixture_sid)."""
    with tempfile.TemporaryDirectory() as tmp:
        pass  # just need the path; will use _CLI_STATE_DIR
    # Add one fixture session to _CLI_STATE_DIR so the discovery route has something to return
    fixture_sid = _fresh_uuid()
    os.environ["COPILOT_SESSION_STATE"] = str(_CLI_STATE_DIR)
    _make_session_dir(_CLI_STATE_DIR, fixture_sid, _MINIMAL_YAML.format(uuid=fixture_sid))
    server, port = _make_test_server()
    return server, port, fixture_sid


def test_csh1_no_auth_returns_401():
    """CSH1: GET /api/operator/cli-sessions with no auth → 401, no UUID leakage."""
    fixture_sid = _fresh_uuid()
    _make_session_dir(_CLI_STATE_DIR, fixture_sid, _MINIMAL_YAML.format(uuid=fixture_sid))
    server, port = _make_test_server()
    try:
        resp = _no_auth(port, "/api/operator/cli-sessions")
        body = resp.read()
        test("CSH1: status 401", resp.status == 401)
        body_str = body.decode("utf-8", errors="replace")
        test("CSH1: fixture UUID not in 401 body", fixture_sid not in body_str)
        test("CSH1: title not in 401 body", "Test session title" not in body_str)
    finally:
        server.shutdown()


def test_csh2_token_qs_rejected():
    """CSH2: ?token= query-string auth → 401 for debug routes."""
    fixture_sid = _fresh_uuid()
    _make_session_dir(_CLI_STATE_DIR, fixture_sid, _MINIMAL_YAML.format(uuid=fixture_sid))
    server, port = _make_test_server()
    try:
        resp = _token_qs(port, "/api/operator/cli-sessions")
        body = resp.read()
        test("CSH2: ?token= auth rejected with 401", resp.status == 401)
        body_str = body.decode("utf-8", errors="replace")
        test("CSH2: fixture UUID not in ?token= response", fixture_sid not in body_str)
    finally:
        server.shutdown()


def test_csh3_bearer_auth_returns_200():
    """CSH3: Bearer auth → 200 with correct envelope."""
    fixture_sid = _fresh_uuid()
    _make_session_dir(_CLI_STATE_DIR, fixture_sid, _MINIMAL_YAML.format(uuid=fixture_sid))
    server, port = _make_test_server()
    try:
        resp = _bearer(port, "/api/operator/cli-sessions")
        body = resp.read()
        test("CSH3: status 200", resp.status == 200)
        data = json.loads(body)
        test("CSH3: sessions key present", "sessions" in data)
        test("CSH3: count key present", "count" in data)
        test("CSH3: truncated key present", "truncated" in data)
        ids = [s.get("cli_session_id") for s in data.get("sessions", [])]
        test("CSH3: fixture session in response", fixture_sid in ids)
    finally:
        server.shutdown()


def test_csh4_cookie_auth_returns_200():
    """CSH4: Cookie auth → 200."""
    fixture_sid = _fresh_uuid()
    _make_session_dir(_CLI_STATE_DIR, fixture_sid, _MINIMAL_YAML.format(uuid=fixture_sid))
    server, port = _make_test_server()
    try:
        resp = _cookie(port, "/api/operator/cli-sessions")
        body = resp.read()
        test("CSH4: status 200", resp.status == 200)
        data = json.loads(body)
        test("CSH4: sessions list returned", isinstance(data.get("sessions"), list))
    finally:
        server.shutdown()


def test_csh5_wrong_bearer_returns_401():
    """CSH5: Wrong Bearer token → 401."""
    server, port = _make_test_server()
    try:
        resp = _bearer(port, "/api/operator/cli-sessions", token="wrong-token")
        resp.read()
        test("CSH5: wrong Bearer → 401", resp.status == 401)
    finally:
        server.shutdown()


def test_csh6_healthz_no_uuid_leakage():
    """CSH6: /healthz is unauthenticated and does not leak fixture UUID/summary."""
    fixture_sid = _fresh_uuid()
    _make_session_dir(_CLI_STATE_DIR, fixture_sid, _MINIMAL_YAML.format(uuid=fixture_sid))
    server, port = _make_test_server()
    try:
        resp = _no_auth(port, "/healthz")
        body = resp.read()
        test("CSH6: healthz returns 200", resp.status == 200)
        body_str = body.decode("utf-8", errors="replace")
        test("CSH6: fixture UUID not in healthz body", fixture_sid not in body_str)
        test("CSH6: title not in healthz body", "Test session title" not in body_str)
    finally:
        server.shutdown()


def test_csh7_well_known_no_uuid_leakage():
    """CSH7: /.well-known/browse-host unauthenticated, does not leak fixture UUID/summary."""
    fixture_sid = _fresh_uuid()
    _make_session_dir(_CLI_STATE_DIR, fixture_sid, _MINIMAL_YAML.format(uuid=fixture_sid))
    server, port = _make_test_server()
    try:
        resp = _no_auth(port, "/.well-known/browse-host")
        body = resp.read()
        # well-known is unauthenticated (2xx or 404 depending on handler presence)
        test("CSH7: well-known not 401", resp.status != 401)
        body_str = body.decode("utf-8", errors="replace")
        test("CSH7: fixture UUID not in well-known body", fixture_sid not in body_str)
        test("CSH7: title not in well-known body", "Test session title" not in body_str)
    finally:
        server.shutdown()


def test_csh8_single_session_route_200():
    """CSH8: GET /api/operator/cli-sessions/{id} with valid ID → 200."""
    fixture_sid = _fresh_uuid()
    _make_session_dir(_CLI_STATE_DIR, fixture_sid, _MINIMAL_YAML.format(uuid=fixture_sid))
    server, port = _make_test_server()
    try:
        resp = _bearer(port, f"/api/operator/cli-sessions/{fixture_sid}")
        body = resp.read()
        test("CSH8: status 200", resp.status == 200)
        data = json.loads(body)
        test("CSH8: cli_session_id in response", data.get("cli_session_id") == fixture_sid)
        test("CSH8: title in response", "title" in data)
        test("CSH8: mtime in response", "mtime" in data)
        test("CSH8: branch in response", "branch" in data)
    finally:
        server.shutdown()


def test_csh9_single_session_route_404():
    """CSH9: GET /api/operator/cli-sessions/{unknown_id} → 404."""
    server, port = _make_test_server()
    try:
        unknown_id = _fresh_uuid()
        resp = _bearer(port, f"/api/operator/cli-sessions/{unknown_id}")
        body = resp.read()
        test("CSH9: unknown session → 404", resp.status == 404)
        data = json.loads(body)
        test("CSH9: NOT_FOUND error code", data.get("code") == "NOT_FOUND")
    finally:
        server.shutdown()


def test_csh10_no_full_paths_in_response():
    """CSH10: Response must not contain full /Users/<username> or /home/<username> paths."""
    home = str(Path.home())
    fixture_sid = _fresh_uuid()
    # workspace.yaml uses the real home path to test stripping
    yaml = f"id: {fixture_sid}\ntitle: home path test\nworkspace: {home}/my/project\nbranch: main\n"
    _make_session_dir(_CLI_STATE_DIR, fixture_sid, yaml)
    server, port = _make_test_server()
    try:
        resp = _bearer(port, "/api/operator/cli-sessions")
        body = resp.read()
        test("CSH10: status 200", resp.status == 200)
        body_str = body.decode("utf-8", errors="replace")
        # The full home path should not appear in the response
        username = Path.home().name
        test("CSH10: username not in response body", username not in body_str and home not in body_str)
        # workspace_hint should be relative
        data = json.loads(body)
        sessions = data.get("sessions", [])
        our_session = next((s for s in sessions if s.get("cli_session_id") == fixture_sid), None)
        if our_session:
            hint = our_session.get("workspace_hint", "")
            test("CSH10: workspace_hint is relative (no home prefix)", not hint.startswith(home))
    finally:
        server.shutdown()


def test_csh11_empty_server_token_still_rejected():
    """CSH11: Empty server token loopback access is rejected for discovery routes."""
    fixture_sid = _fresh_uuid()
    _make_session_dir(_CLI_STATE_DIR, fixture_sid, _MINIMAL_YAML.format(uuid=fixture_sid))
    server, port = _make_test_server(token="")
    try:
        resp = _no_auth(port, "/api/operator/cli-sessions")
        body = resp.read()
        body_str = body.decode("utf-8", errors="replace")
        test("CSH11: empty-token discovery request → 401", resp.status == 401)
        test("CSH11: fixture UUID not in empty-token body", fixture_sid not in body_str)
        test("CSH11: title not in empty-token body", "Test session title" not in body_str)

        resp_one = _no_auth(port, f"/api/operator/cli-sessions/{fixture_sid}")
        body_one = resp_one.read()
        body_one_str = body_one.decode("utf-8", errors="replace")
        test("CSH11: empty-token single-session request → 401", resp_one.status == 401)
        test("CSH11: single-session body does not leak title", "Test session title" not in body_one_str)
    finally:
        server.shutdown()


# ── Wave 1: prior_context (#568) and cli_metadata (#555) ─────────────────────


def test_pc1_prior_context_present_with_events_jsonl():
    """PC1: events.jsonl in CLI session dir surfaces prior_context envelope."""
    with tempfile.TemporaryDirectory() as tmp:
        sid = _fresh_uuid()
        d = _make_session_dir(Path(tmp), sid, _MINIMAL_YAML.format(uuid=sid))
        events = (
            '{"timestamp": "2026-05-01T10:00:00Z", "type": "user_prompt"}\n'
            '{"timestamp": "2026-05-01T10:00:01Z", "type": "assistant_msg"}\n'
            '{"timestamp": "2026-05-01T10:00:02Z", "status": "completed"}\n'
        )
        (d / "events.jsonl").write_text(events, encoding="utf-8")
        os.environ["COPILOT_SESSION_STATE"] = tmp
        try:
            got = get_cli_session_by_id(sid)
        finally:
            os.environ["COPILOT_SESSION_STATE"] = str(_CLI_STATE_DIR)
    test("PC1: candidate built", got is not None)
    if got is not None:
        pc = got.get("prior_context")
        test("PC1: prior_context dict", isinstance(pc, dict))
        if isinstance(pc, dict):
            test("PC1: event_count == 3", pc.get("event_count") == 3)
            test("PC1: first_event_at present", pc.get("first_event_at") == "2026-05-01T10:00:00Z")
            test("PC1: last_event_at present", pc.get("last_event_at") == "2026-05-01T10:00:02Z")
            test("PC1: last_status is 'completed'", pc.get("last_status") == "completed")
            test("PC1: truncated False", pc.get("truncated") is False)
            test("PC1: redacted False", pc.get("redacted") is False)


def test_pc2_prior_context_null_without_events_jsonl():
    """PC2: missing events.jsonl → prior_context is None on the candidate dict."""
    with tempfile.TemporaryDirectory() as tmp:
        sid = _fresh_uuid()
        _make_session_dir(Path(tmp), sid, _MINIMAL_YAML.format(uuid=sid))
        os.environ["COPILOT_SESSION_STATE"] = tmp
        try:
            got = get_cli_session_by_id(sid)
        finally:
            os.environ["COPILOT_SESSION_STATE"] = str(_CLI_STATE_DIR)
    test("PC2: candidate built", got is not None)
    if got is not None:
        test("PC2: prior_context key present", "prior_context" in got)
        test("PC2: prior_context is None", got.get("prior_context") is None)


def test_pc3_prior_context_skips_malformed_lines():
    """PC3: malformed JSON lines are skipped; valid lines still counted."""
    with tempfile.TemporaryDirectory() as tmp:
        sid = _fresh_uuid()
        d = _make_session_dir(Path(tmp), sid, _MINIMAL_YAML.format(uuid=sid))
        events = (
            '{"timestamp": "2026-05-01T10:00:00Z", "type": "ok"}\n'
            "not-json garbage line {{{\n"
            '{"timestamp": "2026-05-01T10:00:02Z", "type": "ok"}\n'
            "\n"
            '[1,2,3]\n'  # JSON array — not a dict, should be skipped
        )
        (d / "events.jsonl").write_text(events, encoding="utf-8")
        os.environ["COPILOT_SESSION_STATE"] = tmp
        try:
            got = get_cli_session_by_id(sid)
        finally:
            os.environ["COPILOT_SESSION_STATE"] = str(_CLI_STATE_DIR)
    test("PC3: candidate built", got is not None)
    if got is not None:
        pc = got.get("prior_context")
        test("PC3: prior_context dict", isinstance(pc, dict))
        if isinstance(pc, dict):
            test("PC3: event_count == 2", pc.get("event_count") == 2)


def test_pc4_prior_context_never_contains_raw_text():
    """PC4: prior_context never surfaces raw prompts/tool args/text fields."""
    with tempfile.TemporaryDirectory() as tmp:
        sid = _fresh_uuid()
        d = _make_session_dir(Path(tmp), sid, _MINIMAL_YAML.format(uuid=sid))
        secret_marker = "SECRET_PROMPT_TEXT_MARKER_SHOULD_NOT_LEAK"
        events = (
            '{"timestamp": "2026-05-01T10:00:00Z", "type": "user_prompt",'
            f' "prompt": "{secret_marker}", "tool_args": {{"x": "{secret_marker}"}}}}\n'
            '{"timestamp": "2026-05-01T10:00:01Z", "type": "assistant_msg",'
            f' "text": "{secret_marker}"}}\n'
        )
        (d / "events.jsonl").write_text(events, encoding="utf-8")
        os.environ["COPILOT_SESSION_STATE"] = tmp
        try:
            got = get_cli_session_by_id(sid)
        finally:
            os.environ["COPILOT_SESSION_STATE"] = str(_CLI_STATE_DIR)
    test("PC4: candidate built", got is not None)
    if got is not None:
        pc = got.get("prior_context")
        serialized = json.dumps(pc)
        test("PC4: secret marker not in prior_context", secret_marker not in serialized)


def test_pc5_prior_context_oversize_truncated():
    """PC5: events.jsonl larger than the byte cap is truncated."""
    from browse.core.operator_console import _EVENTS_JSONL_MAX_BYTES  # noqa: PLC0415
    with tempfile.TemporaryDirectory() as tmp:
        sid = _fresh_uuid()
        d = _make_session_dir(Path(tmp), sid, _MINIMAL_YAML.format(uuid=sid))
        line = '{"timestamp": "2026-05-01T10:00:00Z", "type": "x"}\n'
        # Build a file larger than the cap
        with (d / "events.jsonl").open("w", encoding="utf-8") as fh:
            written = 0
            while written < _EVENTS_JSONL_MAX_BYTES + 1024:
                fh.write(line)
                written += len(line)
        os.environ["COPILOT_SESSION_STATE"] = tmp
        try:
            got = get_cli_session_by_id(sid)
        finally:
            os.environ["COPILOT_SESSION_STATE"] = str(_CLI_STATE_DIR)
    test("PC5: candidate built", got is not None)
    if got is not None:
        pc = got.get("prior_context")
        test("PC5: prior_context dict", isinstance(pc, dict))
        if isinstance(pc, dict):
            test("PC5: truncated flag True", pc.get("truncated") is True)


def test_pc6_prior_context_symlink_rejected():
    """PC6: events.jsonl symlink → prior_context is None (no follow)."""
    if os.name == "nt":
        test("PC6: skipped on Windows", True)
        return
    with tempfile.TemporaryDirectory() as tmp:
        sid = _fresh_uuid()
        d = _make_session_dir(Path(tmp), sid, _MINIMAL_YAML.format(uuid=sid))
        target = Path(tmp) / "target.jsonl"
        target.write_text('{"timestamp":"2026-05-01T10:00:00Z","type":"ok"}\n', encoding="utf-8")
        try:
            os.symlink(str(target), str(d / "events.jsonl"))
        except OSError:
            test("PC6: skipped — symlink creation failed", True)
            return
        os.environ["COPILOT_SESSION_STATE"] = tmp
        try:
            got = get_cli_session_by_id(sid)
        finally:
            os.environ["COPILOT_SESSION_STATE"] = str(_CLI_STATE_DIR)
    test("PC6: candidate built", got is not None)
    if got is not None:
        test("PC6: prior_context is None for symlink", got.get("prior_context") is None)


def test_pc7_discovery_response_has_prior_context_key():
    """PC7: every discovery sessions entry exposes a prior_context key (additive contract)."""
    with tempfile.TemporaryDirectory() as tmp:
        sid = _fresh_uuid()
        _make_session_dir(Path(tmp), sid, _MINIMAL_YAML.format(uuid=sid))
        os.environ["COPILOT_SESSION_STATE"] = tmp
        try:
            result = discover_cli_sessions()
        finally:
            os.environ["COPILOT_SESSION_STATE"] = str(_CLI_STATE_DIR)
    sessions = result.get("sessions", [])
    test("PC7: one session", len(sessions) == 1)
    if sessions:
        test("PC7: prior_context key present", "prior_context" in sessions[0])


# ── cli_metadata #555 ────────────────────────────────────────────────────────


def test_cm1_build_cli_metadata_safe_fields():
    """CM1: _build_cli_metadata surfaces allowlisted scalar fields only."""
    from browse.core.operator_console import _build_cli_metadata  # noqa: PLC0415
    with tempfile.TemporaryDirectory() as tmp:
        sid = _fresh_uuid()
        yaml = (
            f"id: {sid}\n"
            "title: T\n"
            "workspace: /home/u/projects/app\n"
            "branch: main\n"
            "repository: owner/app\n"
            "cli_kind: copilot\n"
            "cli_version: 1.2.3\n"
            "model: gpt-5\n"
            "model_version: 2026-05-01\n"
            "host_profile_id: hp-001\n"
            "started_at: 2026-05-01T09:00:00Z\n"
            "last_activity: 2026-05-01T10:00:00Z\n"
            "tool_inventory_summary: 4 tools\n"
        )
        _make_session_dir(Path(tmp), sid, yaml)
        os.environ["COPILOT_SESSION_STATE"] = tmp
        try:
            md = _build_cli_metadata(sid)
        finally:
            os.environ["COPILOT_SESSION_STATE"] = str(_CLI_STATE_DIR)
    test("CM1: metadata dict", isinstance(md, dict))
    if isinstance(md, dict):
        test("CM1: cli_kind copilot", md.get("cli_kind") == "copilot")
        test("CM1: cli_version 1.2.3", md.get("cli_version") == "1.2.3")
        test("CM1: model gpt-5", md.get("model") == "gpt-5")
        test("CM1: model_version", md.get("model_version") == "2026-05-01")
        test("CM1: host_profile_id", md.get("host_profile_id") == "hp-001")
        test("CM1: started_at", md.get("started_at") == "2026-05-01T09:00:00Z")
        test("CM1: last_activity", md.get("last_activity") == "2026-05-01T10:00:00Z")
        test("CM1: tool_inventory_summary", md.get("tool_inventory_summary") == "4 tools")
        test("CM1: branch", md.get("branch") == "main")
        test("CM1: repository", md.get("repository") == "owner/app")
        test("CM1: cwd_label is safe hint", isinstance(md.get("cwd_label"), str) and "/home/" not in (md.get("cwd_label") or ""))
        test("CM1: hook_decision_counts None", md.get("hook_decision_counts") is None)
        test("CM1: redacted True", md.get("redacted") is True)


def test_cm2_build_cli_metadata_no_absolute_path():
    """CM2: cli_metadata.cwd_label strips username/home prefix; no absolute paths."""
    from browse.core.operator_console import _build_cli_metadata  # noqa: PLC0415
    home = str(Path.home())
    with tempfile.TemporaryDirectory() as tmp:
        sid = _fresh_uuid()
        yaml = f"id: {sid}\ntitle: T\nworkspace: {home}/projects/myapp\nbranch: main\n"
        _make_session_dir(Path(tmp), sid, yaml)
        os.environ["COPILOT_SESSION_STATE"] = tmp
        try:
            md = _build_cli_metadata(sid)
        finally:
            os.environ["COPILOT_SESSION_STATE"] = str(_CLI_STATE_DIR)
    test("CM2: metadata dict", isinstance(md, dict))
    if isinstance(md, dict):
        cwd = md.get("cwd_label") or ""
        test("CM2: home prefix stripped", home not in cwd)
        test("CM2: no /Users/ prefix in cwd_label", "/Users/" not in cwd and "/home/" not in cwd)


def test_cm3_build_cli_metadata_invalid_uuid():
    """CM3: invalid resume_target → None (graceful degrade)."""
    from browse.core.operator_console import _build_cli_metadata  # noqa: PLC0415
    test("CM3: invalid uuid → None", _build_cli_metadata("not-a-uuid") is None)
    test("CM3: empty → None", _build_cli_metadata("") is None)


def test_cm4_attach_cli_metadata_noop_for_non_cli_adopt():
    """CM4: attach_cli_metadata is a no-op for non-cli_adopt sessions."""
    from browse.core.operator_console import attach_cli_metadata  # noqa: PLC0415
    sess = {"id": "x", "source": "manual"}
    attach_cli_metadata(sess)
    test("CM4: no cli_metadata key added for non-cli_adopt", "cli_metadata" not in sess)


def test_cm5_attach_cli_metadata_adds_field_for_cli_adopt():
    """CM5: attach_cli_metadata adds cli_metadata field for cli_adopt sessions."""
    from browse.core.operator_console import attach_cli_metadata  # noqa: PLC0415
    with tempfile.TemporaryDirectory() as tmp:
        sid = _fresh_uuid()
        _make_session_dir(Path(tmp), sid, _MINIMAL_YAML.format(uuid=sid))
        os.environ["COPILOT_SESSION_STATE"] = tmp
        try:
            sess = {"id": "op-1", "source": "cli_adopt", "resume_target": sid}
            attach_cli_metadata(sess)
        finally:
            os.environ["COPILOT_SESSION_STATE"] = str(_CLI_STATE_DIR)
    test("CM5: cli_metadata key added", "cli_metadata" in sess)
    test("CM5: cli_metadata is dict", isinstance(sess.get("cli_metadata"), dict))


def test_cm6_attach_cli_metadata_handles_missing_cli_session():
    """CM6: when resume_target refers to a missing CLI dir → cli_metadata set to None."""
    from browse.core.operator_console import attach_cli_metadata  # noqa: PLC0415
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["COPILOT_SESSION_STATE"] = tmp
        try:
            sess = {"id": "op-1", "source": "cli_adopt", "resume_target": _fresh_uuid()}
            attach_cli_metadata(sess)
        finally:
            os.environ["COPILOT_SESSION_STATE"] = str(_CLI_STATE_DIR)
    test("CM6: cli_metadata key present", "cli_metadata" in sess)
    test("CM6: cli_metadata is None when CLI session is gone", sess.get("cli_metadata") is None)


def test_cm7_capabilities_advertises_new_features():
    """CM7: /api/operator/capabilities advertises cli_metadata and cli_prior_context features."""
    server, port = _make_test_server()
    try:
        resp = _bearer(port, "/api/operator/capabilities")
        body = resp.read()
        test("CM7: status 200", resp.status == 200)
        data = json.loads(body)
        feats = data.get("supported_features", [])
        test("CM7: cli_metadata advertised", "cli_metadata" in feats)
        test("CM7: cli_prior_context advertised", "cli_prior_context" in feats)
    finally:
        server.shutdown()


def test_cm8_explicit_cwd_label_macos_absolute_path_stripped():
    """CM8: explicit cwd_label with /Users/<name>/... is normalized — no /Users/, no leading /."""
    from browse.core.operator_console import _build_cli_metadata  # noqa: PLC0415
    with tempfile.TemporaryDirectory() as tmp:
        sid = _fresh_uuid()
        yaml = (
            f"id: {sid}\n"
            "title: T\n"
            "cwd_label: /Users/alice/private/secret-project\n"
        )
        _make_session_dir(Path(tmp), sid, yaml)
        os.environ["COPILOT_SESSION_STATE"] = tmp
        try:
            md = _build_cli_metadata(sid)
        finally:
            os.environ["COPILOT_SESSION_STATE"] = str(_CLI_STATE_DIR)
    test("CM8: metadata dict", isinstance(md, dict))
    if isinstance(md, dict):
        cwd = md.get("cwd_label") or ""
        test("CM8: no /Users/ prefix", "/Users/" not in cwd)
        test("CM8: no username 'alice'", "alice" not in cwd)
        test("CM8: no leading slash", not cwd.startswith("/"))
        test("CM8: useful tail kept", cwd == "private/secret-project")


def test_cm9_explicit_cwd_label_linux_absolute_path_stripped():
    """CM9: explicit cwd_label with /home/<name>/... is normalized — no /home/, no username."""
    from browse.core.operator_console import _build_cli_metadata  # noqa: PLC0415
    with tempfile.TemporaryDirectory() as tmp:
        sid = _fresh_uuid()
        yaml = (
            f"id: {sid}\n"
            "title: T\n"
            "cwd_label: /home/bob/code/internal-app\n"
        )
        _make_session_dir(Path(tmp), sid, yaml)
        os.environ["COPILOT_SESSION_STATE"] = tmp
        try:
            md = _build_cli_metadata(sid)
        finally:
            os.environ["COPILOT_SESSION_STATE"] = str(_CLI_STATE_DIR)
    test("CM9: metadata dict", isinstance(md, dict))
    if isinstance(md, dict):
        cwd = md.get("cwd_label") or ""
        test("CM9: no /home/ prefix", "/home/" not in cwd)
        test("CM9: no username 'bob'", "bob" not in cwd)
        test("CM9: no leading slash", not cwd.startswith("/"))
        test("CM9: useful tail kept", cwd == "code/internal-app")


def test_cm10_explicit_cwd_label_other_absolute_paths_stripped():
    """CM10: explicit cwd_label with /root/, /private/, Windows C:\\Users\\ — no absolute leakage."""
    from browse.core.operator_console import _build_cli_metadata  # noqa: PLC0415
    cases = [
        ("/root/secret/area", ("/root/", "/", "root")),
        ("/private/var/folders/xx/secret-project", ("/private/", "/", "private")),
        ("C:\\Users\\carol\\projects\\app", ("/Users/", "C:\\Users\\", "carol", "Users")),
    ]
    for raw, forbidden in cases:
        with tempfile.TemporaryDirectory() as tmp:
            sid = _fresh_uuid()
            yaml = f"id: {sid}\ntitle: T\ncwd_label: {raw}\n"
            _make_session_dir(Path(tmp), sid, yaml)
            os.environ["COPILOT_SESSION_STATE"] = tmp
            try:
                md = _build_cli_metadata(sid)
            finally:
                os.environ["COPILOT_SESSION_STATE"] = str(_CLI_STATE_DIR)
        cwd = (md or {}).get("cwd_label") or ""
        test(f"CM10[{raw}]: no leading slash", not cwd.startswith("/"))
        test(f"CM10[{raw}]: no backslash", "\\" not in cwd)
        for token in forbidden:
            if token in ("/",):
                continue
            test(f"CM10[{raw}]: no '{token}' leakage", token not in cwd)


def test_cm11_explicit_cwd_label_already_safe_passes_through():
    """CM11: already-safe explicit cwd_label like 'src/app' is preserved (no regression on useful labels)."""
    from browse.core.operator_console import _build_cli_metadata  # noqa: PLC0415
    with tempfile.TemporaryDirectory() as tmp:
        sid = _fresh_uuid()
        yaml = f"id: {sid}\ntitle: T\ncwd_label: src/app\n"
        _make_session_dir(Path(tmp), sid, yaml)
        os.environ["COPILOT_SESSION_STATE"] = tmp
        try:
            md = _build_cli_metadata(sid)
        finally:
            os.environ["COPILOT_SESSION_STATE"] = str(_CLI_STATE_DIR)
    cwd = (md or {}).get("cwd_label") or ""
    test("CM11: safe label preserved", cwd == "src/app")



def main() -> None:
    print("=" * 60)
    print("CLI session discovery tests (issue #528)")
    print("=" * 60)

    print("\n── Unit tests (no server) ──")
    test_csu1_discover_returns_envelope()
    test_csu2_valid_session_discovered()
    test_csu3_operator_console_excluded()
    test_csu4_non_uuid_dir_excluded()
    test_csu5_uppercase_uuid_excluded()
    test_csu6_symlink_dir_excluded()
    test_csu7_workspace_yaml_symlink_excluded()
    test_csu8_oversized_workspace_yaml_skipped()
    test_csu9_binary_workspace_yaml_skipped()
    test_csu10_workspace_hint_strips_users_prefix()
    test_csu11_workspace_hint_strips_home_prefix()
    test_csu12_title_redaction()
    test_csu13_yaml_id_mismatch_excluded()
    test_csu14_no_useful_fields_excluded()
    test_csu15_no_id_with_useful_fields_included()
    test_csu16_truncation_flag()
    test_csu17_sorted_most_recent_first()
    test_csu18_no_mutation()
    test_csu19_get_cli_session_invalid_uuid()
    test_csu20_get_cli_session_nonexistent()
    test_csu21_get_cli_session_valid()
    test_csu22_parse_flat_yaml_values()
    test_csu23_parse_flat_yaml_ignores_noise()
    test_csu24_safe_workspace_hint()
    test_csu25_safe_repository_hint()

    print("\n── HTTP tests (server) ──")
    test_csh1_no_auth_returns_401()
    test_csh2_token_qs_rejected()
    test_csh3_bearer_auth_returns_200()
    test_csh4_cookie_auth_returns_200()
    test_csh5_wrong_bearer_returns_401()
    test_csh6_healthz_no_uuid_leakage()
    test_csh7_well_known_no_uuid_leakage()
    test_csh8_single_session_route_200()
    test_csh9_single_session_route_404()
    test_csh10_no_full_paths_in_response()
    test_csh11_empty_server_token_still_rejected()

    print("\n── Wave 1: prior_context (#568) + cli_metadata (#555) ──")
    test_pc1_prior_context_present_with_events_jsonl()
    test_pc2_prior_context_null_without_events_jsonl()
    test_pc3_prior_context_skips_malformed_lines()
    test_pc4_prior_context_never_contains_raw_text()
    test_pc5_prior_context_oversize_truncated()
    test_pc6_prior_context_symlink_rejected()
    test_pc7_discovery_response_has_prior_context_key()
    test_cm1_build_cli_metadata_safe_fields()
    test_cm2_build_cli_metadata_no_absolute_path()
    test_cm3_build_cli_metadata_invalid_uuid()
    test_cm4_attach_cli_metadata_noop_for_non_cli_adopt()
    test_cm5_attach_cli_metadata_adds_field_for_cli_adopt()
    test_cm6_attach_cli_metadata_handles_missing_cli_session()
    test_cm7_capabilities_advertises_new_features()
    test_cm8_explicit_cwd_label_macos_absolute_path_stripped()
    test_cm9_explicit_cwd_label_linux_absolute_path_stripped()
    test_cm10_explicit_cwd_label_other_absolute_paths_stripped()
    test_cm11_explicit_cwd_label_already_safe_passes_through()

    print(f"\n{'=' * 60}")
    print(f"Results: {_PASS} passed, {_FAIL} failed")
    if _FAIL:
        print("FAILED")
        sys.exit(1)
    else:
        print("PASSED")
        sys.exit(0)


if __name__ == "__main__":
    main()
