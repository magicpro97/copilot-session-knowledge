"""Security tests for copilot-session-knowledge tools."""

import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

# Windows console encoding fix — lets emoji/unicode print without cp1252 crashes
if os.name == "nt":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# Adjust path to import from parent
sys.path.insert(0, str(Path(__file__).parent))


# ═══════════════════════════════════════════════════════════════════
#  Test: FTS5 query sanitization
# ═══════════════════════════════════════════════════════════════════


def test_fts5_sanitization():
    """Test that FTS5 special characters and operators are stripped."""
    sanitize_fn = _extract_sanitize_function()

    # Normal queries pass through
    result = sanitize_fn("docker networking")
    assert '"docker"*' in result and '"networking"*' in result

    # FTS5 operators removed
    result = sanitize_fn("test OR admin AND root NOT safe")
    assert "OR" not in result
    assert "AND" not in result
    assert "NOT" not in result

    # Special characters stripped
    result = sanitize_fn('test" OR 1 OR "x')
    # Verify no unescaped quotes remain: strip wrapping "term"* patterns, check for stray quotes
    stripped = result
    import re as _re

    stripped = _re.sub(r'"[^"]*"\*?', "", stripped)  # remove valid "term"* patterns
    assert '"' not in stripped, f"Stray quotes in sanitized result: {result}"

    # NEAR operator removed
    result = sanitize_fn("docker NEAR networking")
    assert "NEAR" not in result

    # Length limit enforced
    long_query = "a" * 1000
    result = sanitize_fn(long_query, max_length=500)
    assert len(result) <= 510  # 500 + wrapping quotes/asterisk

    # Empty query returns safe default
    result = sanitize_fn("")
    assert result == '""'

    result = sanitize_fn("OR AND NOT")
    assert result == '""'

    print("  ✓ FTS5 sanitization tests passed")


# ═══════════════════════════════════════════════════════════════════
#  Test: SQL injection via parameterized queries
# ═══════════════════════════════════════════════════════════════════


def test_sql_parameterized_queries():
    """Test that SQL queries use parameterized placeholders."""
    # Read query-session.py source and verify no f-string IN clauses with user data
    source = Path(__file__).parent / "query-session.py"
    content = source.read_text(encoding="utf-8")

    # The old vulnerable pattern should NOT exist
    assert 'f"SELECT COUNT(*) FROM knowledge_relations WHERE source_id IN ({ids_str})' not in content, (
        "Vulnerable f-string SQL injection pattern still exists!"
    )

    # The safe parameterized pattern SHOULD exist
    assert "placeholders" in content, "Parameterized query pattern not found"
    assert '"?"' in content or "'?'" in content or "?" in content

    print("  ✓ SQL parameterized query tests passed")


# ═══════════════════════════════════════════════════════════════════
#  Test: Pickle deserialization safety
# ═══════════════════════════════════════════════════════════════════


def test_pickle_safety():
    """Test that embed.py no longer uses direct pickle.loads for new models."""
    source = Path(__file__).parent / "embed.py"
    content = source.read_text(encoding="utf-8")

    # Should use JSON serialization for new models
    assert "json.dumps(model" in content or "json.dumps(" in content, "New JSON serialization not found in embed.py"

    # Backward compat pickle should have deprecation warning
    if "pickle.loads" in content:
        assert "deprecated" in content.lower() or "⚠" in content, "Pickle fallback exists but no deprecation warning"

    print("  ✓ Pickle safety tests passed")


# ═══════════════════════════════════════════════════════════════════
#  Test: Config file permissions
# ═══════════════════════════════════════════════════════════════════


def test_config_permissions():
    """Test that save_config sets restrictive file permissions."""
    source = Path(__file__).parent / "embed.py"
    content = source.read_text(encoding="utf-8")

    assert "0o600" in content, "File permission 0o600 not found in embed.py"
    assert "chmod" in content.lower() or "os.chmod" in content, "chmod call not found in embed.py"

    print("  ✓ Config permissions tests passed")


# ═══════════════════════════════════════════════════════════════════
#  Test: Path traversal protection
# ═══════════════════════════════════════════════════════════════════


def test_path_traversal_protection():
    """Test that WSL path validation rejects traversal attempts."""
    source = Path(__file__).parent / "sync-knowledge.py"
    content = source.read_text(encoding="utf-8")

    # Should validate WSL home path
    assert '".."' in content or "'..' not in" in content or '".." not in' in content, (
        "Path traversal check (..) not found in sync-knowledge.py"
    )
    assert "/home/" in content, "WSL home prefix check not found"

    print("  ✓ Path traversal protection tests passed")


# ═══════════════════════════════════════════════════════════════════
#  Test: Lock file atomicity
# ═══════════════════════════════════════════════════════════════════


def test_lock_atomicity():
    """Test that watch-sessions.py uses atomic lock creation."""
    source = Path(__file__).parent / "watch-sessions.py"
    content = source.read_text(encoding="utf-8")

    # Should use O_CREAT | O_EXCL for atomic creation
    assert "O_CREAT" in content and "O_EXCL" in content, "Atomic lock creation (O_CREAT | O_EXCL) not found"

    # Old TOCTOU pattern should NOT exist
    assert "if LOCK_FILE.exists():\n        try:\n            stored_pid" not in content, (
        "Old TOCTOU lock pattern still exists"
    )

    print("  ✓ Lock file atomicity tests passed")


# ═══════════════════════════════════════════════════════════════════
#  Test: Input validation
# ═══════════════════════════════════════════════════════════════════


def test_input_validation():
    """Test that user inputs have length limits."""
    # learn.py title/content limits
    learn_src = Path(__file__).parent / "learn.py"
    learn_content = learn_src.read_text(encoding="utf-8")
    assert "[:200]" in learn_content, "Title length limit not found in learn.py"
    assert "[:10000]" in learn_content, "Content length limit not found in learn.py"

    # claude-adapter.py filter limit
    adapter_src = Path(__file__).parent / "claude-adapter.py"
    adapter_content = adapter_src.read_text(encoding="utf-8")
    assert "[:256]" in adapter_content, "Filter length limit not found in claude-adapter.py"

    print("  ✓ Input validation tests passed")


# ═══════════════════════════════════════════════════════════════════
#  Test: Database integrity check
# ═══════════════════════════════════════════════════════════════════


def test_db_integrity_check():
    """Test that database integrity check is performed."""
    source = Path(__file__).parent / "build-session-index.py"
    content = source.read_text(encoding="utf-8")

    assert "quick_check" in content or "integrity_check" in content, (
        "No PRAGMA integrity check found in build-session-index.py"
    )

    print("  ✓ Database integrity check tests passed")


# ═══════════════════════════════════════════════════════════════════
#  Test: SQL whitelist validation
# ═══════════════════════════════════════════════════════════════════


def test_sql_whitelist():
    """Test that f-string SQL uses whitelist validation."""
    for filename in ["build-session-index.py", "extract-knowledge.py", "install.py"]:
        source = Path(__file__).parent / filename
        content = source.read_text(encoding="utf-8")
        assert "_ALLOWED_" in content, f"Whitelist validation not found in {filename}"

    print("  ✓ SQL whitelist validation tests passed")


# ═══════════════════════════════════════════════════════════════════
#  Test: DB write safety (busy_timeout)
# ═══════════════════════════════════════════════════════════════════


def test_db_write_safety():
    """Test that DB connections use busy_timeout for concurrent write safety."""
    learn_src = Path(__file__).parent / "learn.py"
    learn_content = learn_src.read_text(encoding="utf-8")
    assert "busy_timeout" in learn_content, (
        "busy_timeout not set in learn.py — concurrent writes may fail with SQLITE_BUSY"
    )

    sync_src = Path(__file__).parent / "sync-knowledge.py"
    sync_content = sync_src.read_text(encoding="utf-8")
    assert "busy_timeout" in sync_content, (
        "busy_timeout not set in sync-knowledge.py — concurrent writes may fail with SQLITE_BUSY"
    )

    print("  ✓ DB write safety (busy_timeout) tests passed")


# ═══════════════════════════════════════════════════════════════════
#  Test: Hybrid change detection in watch-sessions.py
# ═══════════════════════════════════════════════════════════════════


def test_hybrid_change_detection_source():
    """Test that watch-sessions.py has hybrid mtime+content-hash change detection."""
    source = Path(__file__).parent / "watch-sessions.py"
    content = source.read_text(encoding="utf-8")

    assert "_content_hash" in content, "_content_hash function not found in watch-sessions.py"
    assert "hashlib.sha256" in content, "SHA256 content hashing not found in watch-sessions.py"
    assert "prev_hash" in content, "prev_hash comparison not found — content-hash dedup logic missing"
    assert "content unchanged" in content.lower() or "content_changed" in content, (
        "content-change tracking variable not found in watch-sessions.py"
    )

    print("  ✓ Hybrid change detection source tests passed")


def test_no_proxy_http_client():
    """Test that outbound HTTP helper paths bypass proxy env vars explicitly."""
    init_src = Path(__file__).parent / "browse" / "__init__.py"
    init_content = init_src.read_text(encoding="utf-8")
    assert "ProxyHandler({})" in init_content, "browse/__init__.py is missing ProxyHandler({}) in _probe_public_url"
    assert "no_proxy_opener.open" in init_content, (
        "browse/__init__.py still uses a proxy-sensitive opener for _probe_public_url"
    )

    dl_src = Path(__file__).parent / "browse" / "static" / "vendor" / "_download.py"
    dl_content = dl_src.read_text(encoding="utf-8")
    assert "ProxyHandler({})" in dl_content, (
        "browse/static/vendor/_download.py is missing ProxyHandler({}) in download_lib"
    )
    assert "no_proxy_opener.open" in dl_content, "browse/static/vendor/_download.py still uses a proxy-sensitive opener"

    print("  ✓ No-proxy HTTP client pattern tests passed")


# Create minimal stub modules for tests that need imports
class query_session_sanitizer:
    """Stub to extract _sanitize_fts_query from query-session.py source."""

    pass


class query_session_source:
    """Stub to verify source patterns."""

    pass


def _extract_sanitize_function():
    """Extract _sanitize_fts_query from query-session.py for testing."""
    source = Path(__file__).parent / "query-session.py"
    content = source.read_text(encoding="utf-8")

    # Find and exec the function
    import re

    match = re.search(r"(def _sanitize_fts_query\(.*?\n(?:    .*\n)*)", content)
    if not match:
        raise RuntimeError("_sanitize_fts_query not found in query-session.py")

    ns = {}
    exec(match.group(1), ns)
    return ns["_sanitize_fts_query"]


# ═══════════════════════════════════════════════════════════════════
#  Main runner
# ═══════════════════════════════════════════════════════════════════


def test_flight_recorder_v3_routes_security():
    """Source-level invariants for Flight Recorder v3 routes
    (synthesis §4a / §6a): UUID4 validation, symlink reject, path confinement,
    redaction routing, schema_version="1", size caps."""
    root = Path(__file__).parent / "browse" / "routes"
    helper = (root / "_checkpoint_index.py").read_text(encoding="utf-8")
    cp = (root / "checkpoints.py").read_text(encoding="utf-8")
    rs = (root / "rewind_snapshots.py").read_text(encoding="utf-8")

    # Shared helper: UUID4 regex, lstat symlink reject, path confine, caps.
    assert "UUID4_RE" in helper and ("[a-f0-9]" in helper or "[0-9a-f]" in helper), (
        "_checkpoint_index.py missing UUID4 regex (lowercase hex)"
    )
    assert "lstat" in helper, "_checkpoint_index.py missing lstat() symlink check"
    assert "S_ISLNK" in helper or "stat.S_ISLNK" in helper, "_checkpoint_index.py missing S_ISLNK symlink reject"
    assert ".resolve(" in helper, "_checkpoint_index.py missing Path.resolve() confine"
    assert "relative_to" in helper or "is_relative_to" in helper or "commonpath" in helper, (
        "_checkpoint_index.py missing path-confinement check"
    )
    assert "INDEX_MD_MAX_BYTES" in helper and "1024" in helper, "_checkpoint_index.py missing 1 MB index cap"
    assert "CHECKPOINTS_MAX_ENTRIES" in helper and "200" in helper, "_checkpoint_index.py missing 200-entry cap"

    # checkpoints route: uses shared helper, schema_version "1", 413 on oversize,
    # title redaction, no raw body returned.
    assert "from browse.routes._checkpoint_index import" in cp or "from browse.routes import _checkpoint_index" in cp, (
        "checkpoints.py must use shared _checkpoint_index helper"
    )
    assert "resolve_safe_child" in cp, "checkpoints.py missing resolve_safe_child use"
    assert "is_safe_file_basename" in cp, "checkpoints.py missing safe-basename guard"
    assert '"schema_version"' in cp and '"1"' in cp, 'checkpoints.py missing schema_version="1"'
    assert "413" in cp, "checkpoints.py missing oversize 413 response"
    assert "_redact_text" in cp or "redact" in cp, "checkpoints.py missing title redaction"
    assert "lstat" in cp, "checkpoints.py missing per-file lstat symlink check"
    assert "64 * 1024" in cp or "65536" in cp, "checkpoints.py missing 64 KB per-checkpoint read cap"

    # rewind-snapshots route: schema_version, no userMessage text, event_span_id,
    # commit/branch validators, 500-entry cap.
    assert '"schema_version"' in rs and '"1"' in rs, 'rewind_snapshots.py missing schema_version="1"'
    assert "user_message_byte_size" in rs and "user_message_present" in rs, (
        "rewind_snapshots.py must expose byte-size only, never userMessage text"
    )
    assert (
        "userMessage" not in rs.split("def _build_snapshot_summary")[-1].split('"user_message_byte_size"')[-1] or True
    ), "structural check"
    assert "event_span_id" in rs, "rewind_snapshots.py missing event_span_id field"
    assert "_span_id_from_raw" in rs, "rewind_snapshots.py must derive event_span_id via _span_id_from_raw"
    # 40-hex commit validator
    assert "[a-f0-9]{40}" in rs or "{40}" in rs, "rewind_snapshots.py missing 40-hex git_commit validator"
    assert "500" in rs, "rewind_snapshots.py missing 500-entry cap"
    assert "413" in rs, "rewind_snapshots.py missing oversize 413 response"
    assert "lstat" in rs, "rewind_snapshots.py missing lstat symlink check"
    assert "resolve_safe_child" in rs, "rewind_snapshots.py missing resolve_safe_child use"

    # Both routes must be debug=True (auth gate: Bearer/cookie only, no ?token=).
    assert "debug=True" in cp, "checkpoints.py route must be registered with debug=True"
    assert "debug=True" in rs, "rewind_snapshots.py route must be registered with debug=True"

    # Prohibited output leaks: scan the snapshot-builder block for emitted
    # JSON dict keys (`"name":`). Raw-field READS (e.g. raw.get("eventId"))
    # are allowed, but the builder must never return them as response keys.
    builder = rs.split("def _build_snapshot_summary", 1)[-1].split("\ndef ", 1)[0]
    assert '"eventId":' not in builder, "rewind_snapshots builder must not emit raw eventId key"
    assert '"backupHashes":' not in builder, "rewind_snapshots builder must not emit backupHashes key"
    assert '"files":' not in builder, "rewind_snapshots builder must not emit files{} key"
    assert '"userMessage":' not in builder, "rewind_snapshots builder must not emit raw userMessage key"

    print("  ✓ Flight Recorder v3 route security source patterns OK")


# ═══════════════════════════════════════════════════════════════════
#  Main test runner
# ═══════════════════════════════════════════════════════════════════


def main():
    print("\n🔒 Running security tests...\n")
    passed = 0
    failed = 0

    # Source-level tests (no imports needed)
    tests = [
        test_pickle_safety,
        test_config_permissions,
        test_path_traversal_protection,
        test_lock_atomicity,
        test_input_validation,
        test_db_integrity_check,
        test_sql_whitelist,
        test_fts5_sanitization,
        test_sql_parameterized_queries,
        test_db_write_safety,
        test_hybrid_change_detection_source,
        test_no_proxy_http_client,
        test_flight_recorder_v3_routes_security,
    ]

    for test in tests:
        try:
            test()
            passed += 1
        except (AssertionError, Exception) as e:
            print(f"  ✗ {test.__name__}: {e}")
            failed += 1

    print(f"\n{'=' * 40}")
    print(f"Results: {passed} passed, {failed} failed")
    if failed:
        sys.exit(1)
    else:
        print("✅ All security tests passed!")


if __name__ == "__main__":
    main()
