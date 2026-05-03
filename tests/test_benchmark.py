#!/usr/bin/env python3
"""
test_benchmark.py — Tests for benchmark.py

Covers:
1. Syntax / importability
2. _ensure_table: table + indexes created
3. cmd_record: inserts row with expected fields
4. cmd_list: lists rows without crashing; --json output is valid JSON
5. cmd_compare: compares two rows; delta math correct
6. _parse_args: parses all flags correctly
7. _delta_str: handles None and numeric cases
8. Git helpers: return str even when git absent (graceful fallback)
9. Migration round-trip: migrate.py v14 creates benchmark_snapshots
10. No writes outside benchmark_snapshots (read-only contract)
11. _collect_health uses the requested DB path and fails closed on SystemExit

Run:
    python3 test_benchmark.py
"""

import ast
import importlib.util
import io
import json
import os
import sqlite3
import sys
from contextlib import redirect_stdout
from pathlib import Path

if os.name == "nt":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

REPO = Path(__file__).parent.parent
BENCH_PY = REPO / "benchmark.py"
MIGRATE_PY = REPO / "migrate.py"

PASS = 0
FAIL = 0


def test(name: str, passed: bool, detail: str = "") -> None:
    global PASS, FAIL
    if passed:
        PASS += 1
        print(f"  ✅ {name}")
    else:
        FAIL += 1
        print(f"  ❌ {name}" + (f" — {detail}" if detail else ""))


# ── Load module ──────────────────────────────────────────────────────────────

_bench_mod = None


def _load_bench():
    global _bench_mod
    if _bench_mod is not None:
        return _bench_mod
    spec = importlib.util.spec_from_file_location("benchmark", str(BENCH_PY))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    _bench_mod = mod
    return mod


def _in_memory_db() -> "tuple[sqlite3.Connection, Path]":
    """Return an in-memory connection with a fake path sentinel for tests."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    b = _load_bench()
    b._ensure_table(conn)
    # Return a sentinel path that "exists" so helpers don't bail early
    return conn, Path("/nonexistent/knowledge.db")


# ── 1. File validity ─────────────────────────────────────────────────────────


def test_file_exists():
    test("benchmark.py exists", BENCH_PY.exists())


def test_valid_syntax():
    src = BENCH_PY.read_text(encoding="utf-8")
    try:
        ast.parse(src)
        test("valid Python syntax", True)
    except SyntaxError as e:
        test("valid Python syntax", False, str(e))


def test_importable():
    try:
        _load_bench()
        test("benchmark.py importable", True)
    except Exception as e:
        test("benchmark.py importable", False, str(e))


# ── 2. _ensure_table ─────────────────────────────────────────────────────────


def test_ensure_table_creates_table():
    b = _load_bench()
    conn = sqlite3.connect(":memory:")
    b._ensure_table(conn)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    test("_ensure_table: benchmark_snapshots exists", "benchmark_snapshots" in tables)


def test_ensure_table_creates_indexes():
    b = _load_bench()
    conn = sqlite3.connect(":memory:")
    b._ensure_table(conn)
    indexes = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index'").fetchall()}
    test("_ensure_table: idx_bsnap_commit exists", "idx_bsnap_commit" in indexes)
    test("_ensure_table: idx_bsnap_recorded exists", "idx_bsnap_recorded" in indexes)


def test_ensure_table_idempotent():
    b = _load_bench()
    conn = sqlite3.connect(":memory:")
    try:
        b._ensure_table(conn)
        b._ensure_table(conn)
        test("_ensure_table: idempotent (double call)", True)
    except Exception as e:
        test("_ensure_table: idempotent (double call)", False, str(e))


# ── 3. cmd_record ────────────────────────────────────────────────────────────


def test_cmd_record_inserts_row(tmp_path):
    b = _load_bench()
    db = tmp_path / "k.db"
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = b.cmd_record(db, commit_sha="abc123", mode="repo")
    test("cmd_record: returns 0", rc == 0, f"rc={rc}")
    conn = sqlite3.connect(str(db))
    rows = conn.execute("SELECT * FROM benchmark_snapshots").fetchall()
    conn.close()
    test("cmd_record: one row inserted", len(rows) == 1, f"len={len(rows)}")


def test_cmd_record_stores_commit_sha(tmp_path):
    b = _load_bench()
    db = tmp_path / "k.db"
    with redirect_stdout(io.StringIO()):
        b.cmd_record(db, commit_sha="deadbeef0001", mode="repo")
    conn = sqlite3.connect(str(db))
    row = conn.execute("SELECT commit_sha FROM benchmark_snapshots").fetchone()
    conn.close()
    test("cmd_record: commit_sha stored", row[0] == "deadbeef0001", f"got={row[0]}")


def test_cmd_record_stores_mode(tmp_path):
    b = _load_bench()
    db = tmp_path / "k.db"
    with redirect_stdout(io.StringIO()):
        b.cmd_record(db, commit_sha="abc", mode="repo")
    conn = sqlite3.connect(str(db))
    row = conn.execute("SELECT mode FROM benchmark_snapshots").fetchone()
    conn.close()
    test("cmd_record: mode stored", row[0] == "repo", f"got={row[0]}")


def test_cmd_record_subscores_json(tmp_path):
    b = _load_bench()
    db = tmp_path / "k.db"
    with redirect_stdout(io.StringIO()):
        b.cmd_record(db, commit_sha="abc", mode="repo")
    conn = sqlite3.connect(str(db))
    row = conn.execute("SELECT subscores_json FROM benchmark_snapshots").fetchone()
    conn.close()
    try:
        parsed = json.loads(row[0])
        test("cmd_record: subscores_json is valid JSON", isinstance(parsed, dict))
    except Exception as e:
        test("cmd_record: subscores_json is valid JSON", False, str(e))


def test_collect_health_uses_requested_db_path(tmp_path):
    b = _load_bench()
    db = tmp_path / "bench.db"
    sqlite3.connect(str(db)).close()

    class FakeHealth:
        DB_PATH = tmp_path / "missing.db"

        @staticmethod
        def compute_health():
            return {
                "score": 42.0,
                "db_path": str(FakeHealth.DB_PATH),
            }

    orig_loader = b._load_module
    b._load_module = lambda name, filename: FakeHealth
    try:
        data = b._collect_health(db)
    finally:
        b._load_module = orig_loader

    test("_collect_health: requested DB passed into module",
         data.get("db_path") == str(db), f"got={data.get('db_path')}")
    test("_collect_health: payload marked available", data.get("available") is True, f"got={data}")
    test("_collect_health: module DB_PATH restored",
         FakeHealth.DB_PATH == tmp_path / "missing.db", f"got={FakeHealth.DB_PATH}")


def test_collect_health_catches_system_exit(tmp_path):
    b = _load_bench()
    db = tmp_path / "bench.db"
    sqlite3.connect(str(db)).close()

    class FakeHealth:
        DB_PATH = db

        @staticmethod
        def compute_health():
            raise SystemExit(1)

    orig_loader = b._load_module
    b._load_module = lambda name, filename: FakeHealth
    try:
        data = b._collect_health(db)
    finally:
        b._load_module = orig_loader

    test("_collect_health: SystemExit returns unavailable",
         data.get("available") is False, f"got={data}")
    test("_collect_health: SystemExit captured in error",
         "SystemExit(1)" in data.get("error", ""), f"got={data}")


# ── 4. cmd_list ──────────────────────────────────────────────────────────────


def test_cmd_list_no_db(tmp_path):
    b = _load_bench()
    db = tmp_path / "nonexistent.db"
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = b.cmd_list(db, limit=10, as_json=False)
    test("cmd_list: no-DB exits 0", rc == 0)


def test_cmd_list_json_no_db(tmp_path):
    b = _load_bench()
    db = tmp_path / "nonexistent.db"
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = b.cmd_list(db, limit=10, as_json=True)
    test("cmd_list --json no-DB: rc=0", rc == 0, f"rc={rc}")
    try:
        data = json.loads(buf.getvalue())
        test("cmd_list --json no-DB: emits empty list", data == [], f"got={data!r}")
    except Exception as e:
        test("cmd_list --json no-DB: emits empty list", False, str(e))


def test_cmd_list_json_output(tmp_path):
    b = _load_bench()
    db = tmp_path / "k.db"
    with redirect_stdout(io.StringIO()):
        b.cmd_record(db, commit_sha="aaa", mode="repo")
        b.cmd_record(db, commit_sha="bbb", mode="repo")
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = b.cmd_list(db, limit=10, as_json=True)
    test("cmd_list --json: rc=0", rc == 0, f"rc={rc}")
    try:
        data = json.loads(buf.getvalue())
        test("cmd_list --json: valid JSON list", isinstance(data, list))
        test("cmd_list --json: two entries", len(data) == 2, f"len={len(data)}")
        test("cmd_list --json: subscores key present", "subscores" in data[0])
    except Exception as e:
        test("cmd_list --json: valid JSON list", False, str(e))


def test_cmd_list_text_output(tmp_path):
    b = _load_bench()
    db = tmp_path / "k.db"
    with redirect_stdout(io.StringIO()):
        b.cmd_record(db, commit_sha="ccc111", mode="repo")
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = b.cmd_list(db, limit=10, as_json=False)
    out = buf.getvalue()
    test("cmd_list text: rc=0", rc == 0)
    test("cmd_list text: commit sha appears", "ccc111" in out, f"output={out[:200]}")
    test("cmd_list text: Gap column header present", "Gap" in out, f"output={out[:200]}")


# ── 4b. cmd_list gap-to-target ────────────────────────────────────────────────


def test_cmd_list_json_has_retro_gap(tmp_path):
    b = _load_bench()
    db = tmp_path / "k.db"
    conn = sqlite3.connect(str(db))
    b._ensure_table(conn)
    conn.execute(
        "INSERT INTO benchmark_snapshots (commit_sha, mode, retro_score, subscores_json, extra_json) VALUES (?,?,?,?,?)",
        ("abc", "repo", 75.0, "{}", "{}"),
    )
    conn.commit()
    conn.close()
    buf = io.StringIO()
    with redirect_stdout(buf):
        b.cmd_list(db, limit=10, as_json=True)
    data = json.loads(buf.getvalue())
    test("cmd_list --json: retro_gap key present", "retro_gap" in data[0], f"keys={list(data[0])}")
    test("cmd_list --json: retro_gap value correct (25.0)", data[0]["retro_gap"] == 25.0,
         f"got={data[0].get('retro_gap')}")


def test_cmd_list_json_has_health_gap(tmp_path):
    b = _load_bench()
    db = tmp_path / "k.db"
    conn = sqlite3.connect(str(db))
    b._ensure_table(conn)
    conn.execute(
        "INSERT INTO benchmark_snapshots (commit_sha, mode, retro_score, health_score, subscores_json, extra_json) VALUES (?,?,?,?,?,?)",
        ("abc", "repo", 60.0, 80.0, "{}", "{}"),
    )
    conn.commit()
    conn.close()
    buf = io.StringIO()
    with redirect_stdout(buf):
        b.cmd_list(db, limit=10, as_json=True)
    data = json.loads(buf.getvalue())
    test("cmd_list --json: health_gap key present", "health_gap" in data[0], f"keys={list(data[0])}")
    test("cmd_list --json: health_gap value correct (20.0)", data[0]["health_gap"] == 20.0,
         f"got={data[0].get('health_gap')}")


def test_cmd_list_json_health_gap_none_when_health_null(tmp_path):
    b = _load_bench()
    db = tmp_path / "k.db"
    conn = sqlite3.connect(str(db))
    b._ensure_table(conn)
    conn.execute(
        "INSERT INTO benchmark_snapshots (commit_sha, mode, retro_score, subscores_json, extra_json) VALUES (?,?,?,?,?)",
        ("abc", "repo", 60.0, "{}", "{}"),
    )
    conn.commit()
    conn.close()
    buf = io.StringIO()
    with redirect_stdout(buf):
        b.cmd_list(db, limit=10, as_json=True)
    data = json.loads(buf.getvalue())
    test("cmd_list --json: health_gap is None when health unavailable",
         data[0]["health_gap"] is None, f"got={data[0].get('health_gap')}")


# ── 5. cmd_compare ───────────────────────────────────────────────────────────


def test_cmd_compare_two_snapshots(tmp_path):
    b = _load_bench()
    db = tmp_path / "k.db"
    conn = sqlite3.connect(str(db))
    b._ensure_table(conn)
    conn.execute(
        "INSERT INTO benchmark_snapshots (commit_sha, mode, retro_score, subscores_json, extra_json) VALUES (?,?,?,?,?)",
        ("aaa", "repo", 55.0, json.dumps({"git": 55.0}), "{}"),
    )
    conn.execute(
        "INSERT INTO benchmark_snapshots (commit_sha, mode, retro_score, subscores_json, extra_json) VALUES (?,?,?,?,?)",
        ("bbb", "repo", 70.0, json.dumps({"git": 70.0}), "{}"),
    )
    conn.commit()
    conn.close()
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = b.cmd_compare(db, commits=[], limit=10)
    out = buf.getvalue()
    test("cmd_compare: rc=0", rc == 0, f"rc={rc}")
    test("cmd_compare: shows retro delta", "▲" in out or "▼" in out or "─" in out, f"out={out[:300]}")
    test("cmd_compare: mentions 55.0", "55.0" in out, f"out={out[:300]}")
    test("cmd_compare: mentions 70.0", "70.0" in out, f"out={out[:300]}")


def test_cmd_compare_by_commit(tmp_path):
    b = _load_bench()
    db = tmp_path / "k.db"
    conn = sqlite3.connect(str(db))
    b._ensure_table(conn)
    conn.execute(
        "INSERT INTO benchmark_snapshots (commit_sha, mode, retro_score, subscores_json, extra_json) VALUES (?,?,?,?,?)",
        ("abc111", "repo", 40.0, "{}", "{}"),
    )
    conn.execute(
        "INSERT INTO benchmark_snapshots (commit_sha, mode, retro_score, subscores_json, extra_json) VALUES (?,?,?,?,?)",
        ("def222", "repo", 80.0, "{}", "{}"),
    )
    conn.commit()
    conn.close()
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = b.cmd_compare(db, commits=["abc111", "def222"], limit=10)
    test("cmd_compare by commit: rc=0", rc == 0, f"rc={rc}")


def test_cmd_compare_insufficient_rows(tmp_path):
    b = _load_bench()
    db = tmp_path / "k.db"
    conn = sqlite3.connect(str(db))
    b._ensure_table(conn)
    conn.execute(
        "INSERT INTO benchmark_snapshots (commit_sha, mode, retro_score, subscores_json, extra_json) VALUES (?,?,?,?,?)",
        ("only1", "repo", 50.0, "{}", "{}"),
    )
    conn.commit()
    conn.close()
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = b.cmd_compare(db, commits=[], limit=10)
    test("cmd_compare insufficient rows: rc=1", rc == 1, f"rc={rc}")


# ── 5b. cmd_compare gap-to-target ────────────────────────────────────────────


def test_cmd_compare_shows_gap_to_100(tmp_path):
    b = _load_bench()
    db = tmp_path / "k.db"
    conn = sqlite3.connect(str(db))
    b._ensure_table(conn)
    conn.execute(
        "INSERT INTO benchmark_snapshots (commit_sha, mode, retro_score, subscores_json, extra_json) VALUES (?,?,?,?,?)",
        ("aaa", "repo", 55.0, "{}", "{}"),
    )
    conn.execute(
        "INSERT INTO benchmark_snapshots (commit_sha, mode, retro_score, subscores_json, extra_json) VALUES (?,?,?,?,?)",
        ("bbb", "repo", 70.0, "{}", "{}"),
    )
    conn.commit()
    conn.close()
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = b.cmd_compare(db, commits=[], limit=10)
    out = buf.getvalue()
    test("cmd_compare: shows 'gap to 100' section", "gap to 100" in out, f"out={out[:400]}")
    test("cmd_compare: shows gap 45.0 for score 55.0", "45.0" in out, f"out={out[:400]}")
    test("cmd_compare: shows gap 30.0 for score 70.0", "30.0" in out, f"out={out[:400]}")


def test_cmd_compare_shows_closer_verdict(tmp_path):
    b = _load_bench()
    db = tmp_path / "k.db"
    conn = sqlite3.connect(str(db))
    b._ensure_table(conn)
    conn.execute(
        "INSERT INTO benchmark_snapshots (commit_sha, mode, retro_score, subscores_json, extra_json) VALUES (?,?,?,?,?)",
        ("aaa", "repo", 55.0, "{}", "{}"),
    )
    conn.execute(
        "INSERT INTO benchmark_snapshots (commit_sha, mode, retro_score, subscores_json, extra_json) VALUES (?,?,?,?,?)",
        ("bbb", "repo", 70.0, "{}", "{}"),
    )
    conn.commit()
    conn.close()
    buf = io.StringIO()
    with redirect_stdout(buf):
        b.cmd_compare(db, commits=[], limit=10)
    out = buf.getvalue()
    test("cmd_compare: shows 'closer' when score improved", "closer" in out, f"out={out[:400]}")


def test_cmd_compare_shows_farther_verdict(tmp_path):
    b = _load_bench()
    db = tmp_path / "k.db"
    conn = sqlite3.connect(str(db))
    b._ensure_table(conn)
    conn.execute(
        "INSERT INTO benchmark_snapshots (commit_sha, mode, retro_score, subscores_json, extra_json) VALUES (?,?,?,?,?)",
        ("aaa", "repo", 80.0, "{}", "{}"),
    )
    conn.execute(
        "INSERT INTO benchmark_snapshots (commit_sha, mode, retro_score, subscores_json, extra_json) VALUES (?,?,?,?,?)",
        ("bbb", "repo", 60.0, "{}", "{}"),
    )
    conn.commit()
    conn.close()
    buf = io.StringIO()
    with redirect_stdout(buf):
        b.cmd_compare(db, commits=[], limit=10)
    out = buf.getvalue()
    test("cmd_compare: shows 'farther' when score regressed", "farther" in out, f"out={out[:400]}")


def test_cmd_compare_shows_proof_summary(tmp_path):
    b = _load_bench()
    db = tmp_path / "k.db"
    conn = sqlite3.connect(str(db))
    b._ensure_table(conn)
    conn.execute(
        "INSERT INTO benchmark_snapshots (commit_sha, mode, retro_score, subscores_json, extra_json) VALUES (?,?,?,?,?)",
        ("aaa", "repo", 55.0, "{}", "{}"),
    )
    conn.execute(
        "INSERT INTO benchmark_snapshots (commit_sha, mode, retro_score, subscores_json, extra_json) VALUES (?,?,?,?,?)",
        ("bbb", "repo", 70.0, "{}", "{}"),
    )
    conn.commit()
    conn.close()
    buf = io.StringIO()
    with redirect_stdout(buf):
        b.cmd_compare(db, commits=[], limit=10)
    out = buf.getvalue()
    test("cmd_compare: shows 'proof summary' section", "proof summary" in out, f"out={out[:500]}")


# ── 6. _parse_args ───────────────────────────────────────────────────────────


def test_parse_args_defaults():
    b = _load_bench()
    args = b._parse_args(["benchmark.py"])
    test("_parse_args: cmd=None by default", args["cmd"] is None)
    test("_parse_args: mode=repo by default", args["mode"] == "repo")
    test("_parse_args: limit=10 by default", args["limit"] == 10)
    test("_parse_args: json=False by default", args["json"] is False)


def test_parse_args_record():
    b = _load_bench()
    args = b._parse_args(["benchmark.py", "record", "--mode", "local", "--commit", "abc123"])
    test("_parse_args: cmd=record", args["cmd"] == "record")
    test("_parse_args: mode=local", args["mode"] == "local")
    test("_parse_args: commit=abc123", args["commit"] == "abc123")


def test_parse_args_compare_commits():
    b = _load_bench()
    args = b._parse_args(["benchmark.py", "compare", "--commits", "sha1", "sha2"])
    test("_parse_args: cmd=compare", args["cmd"] == "compare")
    test("_parse_args: commits=[sha1, sha2]", args["commits"] == ["sha1", "sha2"])


def test_parse_args_list_json():
    b = _load_bench()
    args = b._parse_args(["benchmark.py", "list", "--json", "--limit", "5"])
    test("_parse_args: cmd=list", args["cmd"] == "list")
    test("_parse_args: json=True", args["json"] is True)
    test("_parse_args: limit=5", args["limit"] == 5)


# ── 7. _delta_str ────────────────────────────────────────────────────────────


def test_delta_str_positive():
    b = _load_bench()
    s = b._delta_str(40.0, 60.0)
    test("_delta_str: positive shows ▲", s.startswith("▲"), f"got={s}")
    test("_delta_str: positive shows 20.0", "20.0" in s, f"got={s}")


def test_delta_str_negative():
    b = _load_bench()
    s = b._delta_str(70.0, 50.0)
    test("_delta_str: negative shows ▼", s.startswith("▼"), f"got={s}")


def test_delta_str_zero():
    b = _load_bench()
    s = b._delta_str(50.0, 50.0)
    test("_delta_str: zero shows ─", s.startswith("─"), f"got={s}")


def test_delta_str_none():
    b = _load_bench()
    s = b._delta_str(None, 50.0)
    test("_delta_str: None → 'n/a'", s == "n/a", f"got={s}")


# ── 7b. _gap_to_target / _gap_progress_str ────────────────────────────────────


def test_gap_to_target_basic():
    b = _load_bench()
    test("_gap_to_target: 75.0 → 25.0", b._gap_to_target(75.0) == 25.0,
         f"got={b._gap_to_target(75.0)}")


def test_gap_to_target_none():
    b = _load_bench()
    test("_gap_to_target: None → None", b._gap_to_target(None) is None,
         f"got={b._gap_to_target(None)}")


def test_gap_to_target_at_100():
    b = _load_bench()
    test("_gap_to_target: 100.0 → 0.0", b._gap_to_target(100.0) == 0.0,
         f"got={b._gap_to_target(100.0)}")


def test_gap_to_target_over_100():
    b = _load_bench()
    test("_gap_to_target: 105.0 → 0.0 (clamped)", b._gap_to_target(105.0) == 0.0,
         f"got={b._gap_to_target(105.0)}")


def test_gap_progress_str_closer():
    b = _load_bench()
    s = b._gap_progress_str(45.0, 30.0)
    test("_gap_progress_str: gap 45→30 shows 'closer'", "closer" in s and "▲" in s, f"got={s}")


def test_gap_progress_str_farther():
    b = _load_bench()
    s = b._gap_progress_str(30.0, 45.0)
    test("_gap_progress_str: gap 30→45 shows 'farther'", "farther" in s and "▼" in s, f"got={s}")


def test_gap_progress_str_unchanged():
    b = _load_bench()
    s = b._gap_progress_str(30.0, 30.0)
    test("_gap_progress_str: same gap shows 'unchanged'", "unchanged" in s and "─" in s, f"got={s}")


def test_gap_progress_str_none():
    b = _load_bench()
    s = b._gap_progress_str(None, 30.0)
    test("_gap_progress_str: None → 'n/a'", s == "n/a", f"got={s}")


# ── 8. Git helpers ───────────────────────────────────────────────────────────


def test_git_head_sha_returns_str():
    b = _load_bench()
    result = b._git_head_sha(b.SCRIPT_DIR)
    test("_git_head_sha: returns str", isinstance(result, str))


def test_git_head_sha_graceful_on_bad_path(tmp_path):
    b = _load_bench()
    result = b._git_head_sha(tmp_path)
    test("_git_head_sha: graceful on non-repo path", isinstance(result, str))


def test_git_head_msg_returns_str():
    b = _load_bench()
    result = b._git_head_msg(b.SCRIPT_DIR)
    test("_git_head_msg: returns str", isinstance(result, str))


# ── 9. Migration round-trip ──────────────────────────────────────────────────


def test_migrate_v14_creates_table(tmp_path):
    """Run migrate.py as a subprocess and check v14 created benchmark_snapshots."""
    import subprocess as _sp
    db = tmp_path / "test_migrate.db"
    try:
        result = _sp.run(
            [sys.executable, str(MIGRATE_PY), str(db)],
            capture_output=True, text=True, timeout=30,
        )
        conn = sqlite3.connect(str(db))
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        conn.close()
        test("migrate v14: benchmark_snapshots table created", "benchmark_snapshots" in tables,
             f"stderr={result.stderr[:200]}")
    except Exception as e:
        test("migrate v14: benchmark_snapshots table created", False, str(e))


def test_migrate_v14_correct_version(tmp_path):
    import subprocess as _sp
    db = tmp_path / "test_migrate2.db"
    try:
        _sp.run(
            [sys.executable, str(MIGRATE_PY), str(db)],
            capture_output=True, text=True, timeout=30,
        )
        conn = sqlite3.connect(str(db))
        ver = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0]
        conn.close()
        test("migrate v14: schema_version contains 14", ver >= 14, f"MAX version={ver}")
    except Exception as e:
        test("migrate v14: schema_version contains 14", False, str(e))


# ── 10. main() entry point ───────────────────────────────────────────────────


def test_main_no_cmd():
    b = _load_bench()
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = b.main(["benchmark.py"])
    test("main: no-cmd returns 1", rc == 1)


def test_main_invalid_mode(tmp_path):
    b = _load_bench()
    db = tmp_path / "k.db"
    buf = io.StringIO()
    rc = 0
    import io as _io
    err_buf = _io.StringIO()
    with redirect_stdout(buf):
        try:
            rc = b.main(["benchmark.py", "record", "--db", str(db), "--mode", "bad"])
        except SystemExit as e:
            rc = int(str(e)) if str(e).isdigit() else 1
    test("main: invalid mode returns 1", rc == 1, f"rc={rc}")


# ── Runner ───────────────────────────────────────────────────────────────────


def run_all():
    import tempfile

    def _tmp():
        return Path(tempfile.mkdtemp())

    print("\n── 1. File validity ──────────────────────────────────────────────────────")
    test_file_exists()
    test_valid_syntax()
    test_importable()

    print("\n── 2. _ensure_table ──────────────────────────────────────────────────────")
    test_ensure_table_creates_table()
    test_ensure_table_creates_indexes()
    test_ensure_table_idempotent()

    print("\n── 3. cmd_record ─────────────────────────────────────────────────────────")
    test_cmd_record_inserts_row(_tmp())
    test_cmd_record_stores_commit_sha(_tmp())
    test_cmd_record_stores_mode(_tmp())
    test_cmd_record_subscores_json(_tmp())

    print("\n── 4. cmd_list ───────────────────────────────────────────────────────────")
    test_cmd_list_no_db(_tmp())
    test_cmd_list_json_no_db(_tmp())
    test_cmd_list_json_output(_tmp())
    test_cmd_list_text_output(_tmp())
    test_collect_health_uses_requested_db_path(_tmp())
    test_collect_health_catches_system_exit(_tmp())

    print("\n── 4b. cmd_list gap-to-target ────────────────────────────────────────────")
    test_cmd_list_json_has_retro_gap(_tmp())
    test_cmd_list_json_has_health_gap(_tmp())
    test_cmd_list_json_health_gap_none_when_health_null(_tmp())

    print("\n── 5. cmd_compare ────────────────────────────────────────────────────────")
    test_cmd_compare_two_snapshots(_tmp())
    test_cmd_compare_by_commit(_tmp())
    test_cmd_compare_insufficient_rows(_tmp())

    print("\n── 5b. cmd_compare gap-to-target ────────────────────────────────────────")
    test_cmd_compare_shows_gap_to_100(_tmp())
    test_cmd_compare_shows_closer_verdict(_tmp())
    test_cmd_compare_shows_farther_verdict(_tmp())
    test_cmd_compare_shows_proof_summary(_tmp())

    print("\n── 6. _parse_args ────────────────────────────────────────────────────────")
    test_parse_args_defaults()
    test_parse_args_record()
    test_parse_args_compare_commits()
    test_parse_args_list_json()

    print("\n── 7. _delta_str ─────────────────────────────────────────────────────────")
    test_delta_str_positive()
    test_delta_str_negative()
    test_delta_str_zero()
    test_delta_str_none()

    print("\n── 7b. _gap_to_target / _gap_progress_str ────────────────────────────────")
    test_gap_to_target_basic()
    test_gap_to_target_none()
    test_gap_to_target_at_100()
    test_gap_to_target_over_100()
    test_gap_progress_str_closer()
    test_gap_progress_str_farther()
    test_gap_progress_str_unchanged()
    test_gap_progress_str_none()

    print("\n── 8. Git helpers ────────────────────────────────────────────────────────")
    test_git_head_sha_returns_str()
    test_git_head_sha_graceful_on_bad_path(_tmp())
    test_git_head_msg_returns_str()

    print("\n── 9. Migration round-trip ───────────────────────────────────────────────")
    test_migrate_v14_creates_table(_tmp())
    test_migrate_v14_correct_version(_tmp())

    print("\n── 10. main() entry point ────────────────────────────────────────────────")
    test_main_no_cmd()
    test_main_invalid_mode(_tmp())

    print(f"\n{'─'*60}")
    if FAIL == 0:
        print(f"✅ All {PASS} tests passed.")
    else:
        print(f"❌ {FAIL} failed, {PASS} passed.")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(run_all())
