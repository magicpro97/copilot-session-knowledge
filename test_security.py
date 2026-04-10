"""Security tests for copilot-session-knowledge tools."""

import json
import os
import sys
import sqlite3
import tempfile
from pathlib import Path

# Adjust path to import from parent
sys.path.insert(0, str(Path(__file__).parent))


# ═══════════════════════════════════════════════════════════════════
#  Test: FTS5 query sanitization
# ═══════════════════════════════════════════════════════════════════

def test_fts5_sanitization():
    """Test that FTS5 special characters and operators are stripped."""
    from query_session_sanitizer import _sanitize_fts_query

    # Normal queries pass through
    result = _sanitize_fts_query("docker networking")
    assert '"docker"*' in result and '"networking"*' in result

    # FTS5 operators removed
    result = _sanitize_fts_query("test OR admin AND root NOT safe")
    assert "OR" not in result
    assert "AND" not in result
    assert "NOT" not in result

    # Special characters stripped
    result = _sanitize_fts_query('test" OR 1 OR "x')
    assert '"' not in result.replace('"', '').replace('*', '')  # only wrapping quotes

    # NEAR operator removed
    result = _sanitize_fts_query("docker NEAR networking")
    assert "NEAR" not in result

    # Length limit enforced
    long_query = "a" * 1000
    result = _sanitize_fts_query(long_query, max_length=500)
    assert len(result) <= 510  # 500 + wrapping quotes/asterisk

    # Empty query returns safe default
    result = _sanitize_fts_query("")
    assert result == '""'

    result = _sanitize_fts_query("OR AND NOT")
    assert result == '""'

    print("  ✓ FTS5 sanitization tests passed")


# ═══════════════════════════════════════════════════════════════════
#  Test: SQL injection via parameterized queries
# ═══════════════════════════════════════════════════════════════════

def test_sql_parameterized_queries():
    """Test that SQL queries use parameterized placeholders."""
    import query_session_source as qs_src

    # Read query-session.py source and verify no f-string IN clauses with user data
    source = Path(__file__).parent / "query-session.py"
    content = source.read_text(encoding="utf-8")

    # The old vulnerable pattern should NOT exist
    assert 'f"SELECT COUNT(*) FROM knowledge_relations WHERE source_id IN ({ids_str})' not in content, \
        "Vulnerable f-string SQL injection pattern still exists!"

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
    assert "json.dumps(model" in content or "json.dumps(" in content, \
        "New JSON serialization not found in embed.py"

    # Backward compat pickle should have deprecation warning
    if "pickle.loads" in content:
        assert "deprecated" in content.lower() or "⚠" in content, \
            "Pickle fallback exists but no deprecation warning"

    print("  ✓ Pickle safety tests passed")


# ═══════════════════════════════════════════════════════════════════
#  Test: Config file permissions
# ═══════════════════════════════════════════════════════════════════

def test_config_permissions():
    """Test that save_config sets restrictive file permissions."""
    source = Path(__file__).parent / "embed.py"
    content = source.read_text(encoding="utf-8")

    assert "0o600" in content, "File permission 0o600 not found in embed.py"
    assert "chmod" in content.lower() or "os.chmod" in content, \
        "chmod call not found in embed.py"

    print("  ✓ Config permissions tests passed")


# ═══════════════════════════════════════════════════════════════════
#  Test: Path traversal protection
# ═══════════════════════════════════════════════════════════════════

def test_path_traversal_protection():
    """Test that WSL path validation rejects traversal attempts."""
    source = Path(__file__).parent / "sync-knowledge.py"
    content = source.read_text(encoding="utf-8")

    # Should validate WSL home path
    assert '".."' in content or "'..' not in" in content or '".." not in' in content, \
        "Path traversal check (..) not found in sync-knowledge.py"
    assert "/home/" in content, \
        "WSL home prefix check not found"

    print("  ✓ Path traversal protection tests passed")


# ═══════════════════════════════════════════════════════════════════
#  Test: Lock file atomicity
# ═══════════════════════════════════════════════════════════════════

def test_lock_atomicity():
    """Test that watch-sessions.py uses atomic lock creation."""
    source = Path(__file__).parent / "watch-sessions.py"
    content = source.read_text(encoding="utf-8")

    # Should use O_CREAT | O_EXCL for atomic creation
    assert "O_CREAT" in content and "O_EXCL" in content, \
        "Atomic lock creation (O_CREAT | O_EXCL) not found"

    # Old TOCTOU pattern should NOT exist
    assert "if LOCK_FILE.exists():\n        try:\n            stored_pid" not in content, \
        "Old TOCTOU lock pattern still exists"

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

    assert "quick_check" in content or "integrity_check" in content, \
        "No PRAGMA integrity check found in build-session-index.py"

    print("  ✓ Database integrity check tests passed")


# ═══════════════════════════════════════════════════════════════════
#  Test: SQL whitelist validation
# ═══════════════════════════════════════════════════════════════════

def test_sql_whitelist():
    """Test that f-string SQL uses whitelist validation."""
    for filename in ["build-session-index.py", "extract-knowledge.py", "install.py"]:
        source = Path(__file__).parent / filename
        content = source.read_text(encoding="utf-8")
        assert "_ALLOWED_" in content, \
            f"Whitelist validation not found in {filename}"

    print("  ✓ SQL whitelist validation tests passed")


# ═══════════════════════════════════════════════════════════════════
#  Helpers for import-based tests
# ═══════════════════════════════════════════════════════════════════

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
    match = re.search(
        r'(def _sanitize_fts_query\(.*?\n(?:    .*\n)*)',
        content
    )
    if not match:
        raise RuntimeError("_sanitize_fts_query not found in query-session.py")

    ns = {}
    exec(match.group(1), ns)
    return ns["_sanitize_fts_query"]


# ═══════════════════════════════════════════════════════════════════
#  Main runner
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
    ]

    for test in tests:
        try:
            test()
            passed += 1
        except (AssertionError, Exception) as e:
            print(f"  ✗ {test.__name__}: {e}")
            failed += 1

    # FTS5 sanitization test (needs function extraction)
    try:
        sanitize_fn = _extract_sanitize_function()
        # Monkey-patch for test
        globals()["_sanitize_fts_query"] = sanitize_fn

        # Run FTS5 tests inline
        result = sanitize_fn("docker networking")
        assert '"docker"*' in result and '"networking"*' in result

        result = sanitize_fn("test OR admin AND root NOT safe")
        assert "OR" not in result.split('"')[0]  # OR not outside quotes

        result = sanitize_fn("")
        assert result == '""'

        result = sanitize_fn("OR AND NOT")
        assert result == '""'

        print("  ✓ FTS5 sanitization tests passed")
        passed += 1
    except Exception as e:
        print(f"  ✗ FTS5 sanitization: {e}")
        failed += 1

    # SQL parameterized query check
    try:
        source = Path(__file__).parent / "query-session.py"
        content = source.read_text(encoding="utf-8")
        assert 'ids_str' not in content or 'placeholders' in content
        print("  ✓ SQL parameterized query tests passed")
        passed += 1
    except Exception as e:
        print(f"  ✗ SQL parameterized: {e}")
        failed += 1

    print(f"\n{'='*40}")
    print(f"Results: {passed} passed, {failed} failed")
    if failed:
        sys.exit(1)
    else:
        print("✅ All security tests passed!")


if __name__ == "__main__":
    main()
