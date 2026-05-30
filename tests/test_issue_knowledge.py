#!/usr/bin/env python3
"""
tests/test_issue_knowledge.py — Tests for I690–I697 (knowledge, cost, freshness, doctor, broker).

Covers:
  I690: sk audit-log alias and sk knowledge freshness
  I691: GitHub PAT injection detection in learn.py
  I692: cost trend — DB migration + ASCII chart
  I693: freshness-check cron template + staleness banner in wakeup
  I694: sk doctor global health surface
  I695: _should_use_writer_broker() auto-enable detection
  I697: --refresh-cost backfills NULL cost columns

Run: python3 tests/test_issue_knowledge.py
"""

import builtins
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

# Allow imports from repo root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")

_WINDOWS_TTY = os.name == "nt" and sys.stdout.isatty()
_ORIG_PRINT = builtins.print


def _safe_print(*values, sep=" ", end="\n", file=None, flush=False):
    target = sys.stdout if file is None else file
    if target is sys.stdout and _WINDOWS_TTY:
        text = sep.join(str(value) for value in values) + end
        sys.stdout.write(text.encode("ascii", "backslashreplace").decode("ascii"))
        sys.stdout.flush()
        return
    _ORIG_PRINT(*values, sep=sep, end=end, file=target, flush=flush)


print = _safe_print

PASS = 0
FAIL = 0
REPO = Path(__file__).parent.parent


def test(name: str, condition: bool, detail: str = ""):
    global PASS, FAIL
    if condition:
        PASS += 1
        if not _WINDOWS_TTY:
            print(f"  ✅ {name}")
    else:
        FAIL += 1
        print(f"  ❌ {name}" + (f" — {detail}" if detail else ""))


# ---------------------------------------------------------------------------
# I691: GitHub PAT injection detection in learn.py
print("\n🔒 GitHub token injection detection (I691)")

try:
    import importlib.util as _ilu691, types as _types691
    _learn_src691 = (REPO / "learn.py").read_text()
    _learn_mod691 = _types691.ModuleType("learn_mod_691")
    _learn_mod691.__file__ = str(REPO / "learn.py")
    exec(compile(_learn_src691, str(REPO / "learn.py"), "exec"), _learn_mod691.__dict__)

    _scan691 = getattr(_learn_mod691, "scan_content_for_injection", None)
    test("I691-1: scan_content_for_injection exists in learn.py", _scan691 is not None)

    if _scan691 is not None:
        _ghp = "ghp_ABcdefGHIjklmNOpqrsTUVwxy1234567890ab"
        _findings = _scan691("test title", f"Use {_ghp} for auth")
        test(
            "I691-2: test_injection_github_token_blocked — ghp_ token rejected",
            any("GitHub" in str(f) for f in _findings),
            f"findings={_findings}",
        )
        _clean = _scan691("test title", "Use environment variables for auth")
        test("I691-3: clean content not rejected", not _clean, f"findings={_clean}")
except Exception as _e691:
    test("I691: GitHub token injection detection", False, str(_e691))

# ---------------------------------------------------------------------------
# I690: sk audit-log alias and sk knowledge freshness
print("\n🔍 audit-log alias + knowledge freshness (I690)")

try:
    _al_result = subprocess.run(
        [sys.executable, str(REPO / "sk.py"), "audit-log", "--help"],
        capture_output=True, text=True, cwd=str(REPO),
    )
    test(
        "I690-1: test_audit_log_alias — sk audit-log --help exits 0",
        _al_result.returncode == 0,
        f"rc={_al_result.returncode} stderr={_al_result.stderr[:100]}",
    )
    _kf_result = subprocess.run(
        [sys.executable, str(REPO / "sk.py"), "knowledge", "freshness", "--days", "365", "--json"],
        capture_output=True, text=True, cwd=str(REPO),
    )
    test(
        "I690-2: test_knowledge_freshness_output — sk knowledge freshness --json exits 0",
        _kf_result.returncode == 0,
        f"rc={_kf_result.returncode} stderr={_kf_result.stderr[:100]}",
    )
    if _kf_result.returncode == 0 and _kf_result.stdout.strip():
        _kf_data = json.loads(_kf_result.stdout)
        test(
            "I690-3: knowledge freshness JSON has days_threshold + entries keys",
            "days_threshold" in _kf_data and "entries" in _kf_data,
            f"keys={list(_kf_data.keys())}",
        )
except Exception as _e690:
    test("I690: audit-log alias + knowledge freshness", False, str(_e690))

# ---------------------------------------------------------------------------
# I692: cost trend — DB migration + --trend ASCII chart
print("\n💰 cost trend migration + ASCII chart (I692)")

try:
    import sqlite3 as _sqlite3_692
    import tempfile as _tempfile692

    # ── Test 1: migration v33 is additive on an empty DB ─────────────────────
    with _tempfile692.TemporaryDirectory() as _tmp692:
        _db692 = Path(_tmp692) / "test.db"
        _conn692 = _sqlite3_692.connect(str(_db692))
        _conn692.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY, path TEXT, indexed_at TEXT)")
        _conn692.commit()
        # Apply v33 columns manually (same as migrate.py does)
        for _col in ["cost_usd_est REAL", "total_input_tokens INTEGER", "total_output_tokens INTEGER"]:
            try:
                _conn692.execute(f"ALTER TABLE sessions ADD COLUMN {_col}")
            except _sqlite3_692.OperationalError:
                pass  # already exists — idempotent
        _conn692.commit()
        _pragma692 = {r[1] for r in _conn692.execute("PRAGMA table_info(sessions)")}
        test(
            "I692-1: migration v33 additive — cost_usd_est column exists",
            "cost_usd_est" in _pragma692,
            f"cols={sorted(_pragma692)}",
        )
        test(
            "I692-2: migration v33 additive — total_input_tokens column exists",
            "total_input_tokens" in _pragma692,
            f"cols={sorted(_pragma692)}",
        )
        test(
            "I692-3: migration v33 additive — total_output_tokens column exists",
            "total_output_tokens" in _pragma692,
            f"cols={sorted(_pragma692)}",
        )

        # ── Test 2: migration is idempotent (re-apply on existing DB) ─────────
        _before692 = len([r for r in _conn692.execute("PRAGMA table_info(sessions)")])
        for _col in ["cost_usd_est REAL", "total_input_tokens INTEGER", "total_output_tokens INTEGER"]:
            try:
                _conn692.execute(f"ALTER TABLE sessions ADD COLUMN {_col}")
            except _sqlite3_692.OperationalError:
                pass
        _conn692.commit()
        _after692 = len([r for r in _conn692.execute("PRAGMA table_info(sessions)")])
        test(
            "I692-4: migration v33 idempotent — no extra columns on re-apply",
            _before692 == _after692,
            f"before={_before692} after={_after692}",
        )

        # ── Test 3: migration safe on DB with existing data ───────────────────
        _conn692.execute(
            "INSERT INTO sessions (id, path, indexed_at) VALUES (?, ?, ?)",
            ("test-session-1", "/path/to/session", "2026-01-15T10:00:00"),
        )
        _conn692.commit()
        _row692 = _conn692.execute(
            "SELECT cost_usd_est, total_input_tokens, total_output_tokens FROM sessions WHERE id=?",
            ("test-session-1",),
        ).fetchone()
        test(
            "I692-5: migration safe on existing data — cost_usd_est defaults to NULL",
            _row692 is not None and _row692[0] is None,
            f"row={_row692}",
        )

        # ── Test 4: can write + read cost data ────────────────────────────────
        _conn692.execute(
            "UPDATE sessions SET cost_usd_est=?, total_input_tokens=?, total_output_tokens=? WHERE id=?",
            (0.42, 50000, 1200, "test-session-1"),
        )
        _conn692.commit()
        _row692b = _conn692.execute(
            "SELECT cost_usd_est, total_input_tokens, total_output_tokens FROM sessions WHERE id=?",
            ("test-session-1",),
        ).fetchone()
        test(
            "I692-6: cost_usd_est write+read roundtrip",
            _row692b is not None and abs((_row692b[0] or 0) - 0.42) < 0.001,
            f"row={_row692b}",
        )
        _conn692.close()

except Exception as _e692_migrate:
    test("I692: migration test", False, str(_e692_migrate))

# ── Test 5: _print_cost_trend renders correct ASCII bar chart ─────────────
try:
    import importlib.util as _ilu692, types as _types692
    _sl_src692 = (REPO / "statusline.py").read_text()
    _sl_mod692 = _types692.ModuleType("statusline_692")
    _sl_mod692.__file__ = str(REPO / "statusline.py")
    exec(compile(_sl_src692, str(REPO / "statusline.py"), "exec"), _sl_mod692.__dict__)

    _trend_fn692 = getattr(_sl_mod692, "_print_cost_trend", None)
    test("I692-7: _print_cost_trend function exists in statusline.py", _trend_fn692 is not None)

    if _trend_fn692 is not None:
        import io as _io692, sqlite3 as _sqlite3_692b, tempfile as _tf692

        with _tf692.TemporaryDirectory() as _td692:
            _db_path692 = Path(_td692) / "knowledge.db"
            _dbc692 = _sqlite3_692b.connect(str(_db_path692))
            _dbc692.execute(
                """CREATE TABLE sessions (
                    id TEXT PRIMARY KEY,
                    path TEXT,
                    indexed_at TEXT,
                    cost_usd_est REAL,
                    total_input_tokens INTEGER,
                    total_output_tokens INTEGER
                )"""
            )
            # Seed 3 days of data in the last 7 days using relative dates
            import datetime as _dt692
            _today692 = _dt692.date.today()
            _dbc692.executemany(
                "INSERT INTO sessions VALUES (?,?,?,?,?,?)",
                [
                    ("s1", "/s1", (_today692 - _dt692.timedelta(days=3)).isoformat() + "T10:00:00", 0.10, 10000, 200),
                    ("s2", "/s2", (_today692 - _dt692.timedelta(days=2)).isoformat() + "T10:00:00", 0.42, 50000, 1200),
                    ("s3", "/s3", (_today692 - _dt692.timedelta(days=1)).isoformat() + "T10:00:00", 0.07, 8000, 150),
                ],
            )
            _dbc692.commit()
            _dbc692.close()

            # Patch SK_DB_PATH so _print_cost_trend reads our test DB
            import os as _os692
            _old_sk_db = _os692.environ.get("SK_DB_PATH")
            _os692.environ["SK_DB_PATH"] = str(_db_path692)
            _buf692 = _io692.StringIO()
            _sys_stdout_orig692 = sys.stdout
            sys.stdout = _buf692
            try:
                _trend_fn692()
            finally:
                sys.stdout = _sys_stdout_orig692
                if _old_sk_db is None:
                    _os692.environ.pop("SK_DB_PATH", None)
                else:
                    _os692.environ["SK_DB_PATH"] = _old_sk_db
            _out692 = _buf692.getvalue()

        # Verify output contains bar characters and cost values
        _has_bar = "█" in _out692 or "░" in _out692
        _has_dollar = "$" in _out692
        _has_total = "Total" in _out692
        _has_avg = "Avg" in _out692
        test(
            "I692-8: trend chart renders bar characters",
            _has_bar,
            f"output={_out692[:200]}",
        )
        test(
            "I692-9: trend chart renders dollar cost values",
            _has_dollar,
            f"output={_out692[:200]}",
        )
        test(
            "I692-10: trend chart renders Total + Avg footer",
            _has_total and _has_avg,
            f"output={_out692[:300]}",
        )
except Exception as _e692_trend:
    test("I692: trend chart render test", False, str(_e692_trend))

# ── Test 6: _print_cost_trend falls back when no data ────────────────────
try:
    import importlib.util as _ilu692b, types as _types692b
    _sl_src692b = (REPO / "statusline.py").read_text()
    _sl_mod692b = _types692b.ModuleType("statusline_692b")
    _sl_mod692b.__file__ = str(REPO / "statusline.py")
    exec(compile(_sl_src692b, str(REPO / "statusline.py"), "exec"), _sl_mod692b.__dict__)
    _trend_fn692b = getattr(_sl_mod692b, "_print_cost_trend", None)
    if _trend_fn692b is not None:
        import io as _io692b, os as _os692b
        _old_sk_db2 = _os692b.environ.get("SK_DB_PATH")
        _os692b.environ["SK_DB_PATH"] = "/nonexistent/path/to/knowledge.db"
        _buf692b = _io692b.StringIO()
        _sys_stdout_orig692b = sys.stdout
        sys.stdout = _buf692b
        try:
            _trend_fn692b()
        finally:
            sys.stdout = _sys_stdout_orig692b
            if _old_sk_db2 is None:
                _os692b.environ.pop("SK_DB_PATH", None)
            else:
                _os692b.environ["SK_DB_PATH"] = _old_sk_db2
        _out692b = _buf692b.getvalue()
        test(
            "I692-11: trend chart shows hint when no DB",
            "sk index build" in _out692b or "No cost data" in _out692b,
            f"output={_out692b[:150]}",
        )
except Exception as _e692_nodata:
    test("I692-11: trend no-data fallback", False, str(_e692_nodata))

# ---------------------------------------------------------------------------
# I693: freshness-check cron template + staleness banner in wakeup
print("\n🔍 freshness-check cron + wakeup staleness banner (I693)")

# --- I693-1: freshness-check is a registered template in cron-tasks.py ---
try:
    import importlib.util as _ilu693, types as _types693
    _ct_src = (REPO / "cron-tasks.py").read_text()
    _ct_mod = _types693.ModuleType("cron_tasks_mod_693")
    _ct_mod.__file__ = str(REPO / "cron-tasks.py")
    exec(compile(_ct_src, str(REPO / "cron-tasks.py"), "exec"), _ct_mod.__dict__)

    _templates = getattr(_ct_mod, "TEMPLATE_DEFINITIONS", {})
    test(
        "I693-1: freshness-check template registered in TEMPLATE_DEFINITIONS",
        "freshness-check" in _templates,
        f"templates={list(_templates.keys())}",
    )

    # --- I693-2: cron artifact written to correct path (ARTIFACTS_DIR / freshness-YYYYMMDD.json) ---
    _artifacts_dir = getattr(_ct_mod, "ARTIFACTS_DIR", None)
    test("I693-2a: ARTIFACTS_DIR exists in cron-tasks", _artifacts_dir is not None)

    _run_freshness = getattr(_ct_mod, "_run_freshness_check", None)
    test("I693-2b: _run_freshness_check function exists", _run_freshness is not None)

    if _run_freshness is not None:
        # Run against a non-existent path to get the 'missing' status
        _missing_result = _run_freshness(Path("/nonexistent/knowledge.db"))
        test(
            "I693-2c: _run_freshness_check returns status='missing' for non-existent DB",
            _missing_result.get("status") == "missing",
            f"status={_missing_result.get('status')}",
        )

    # Test with a real in-memory-style temp DB
    import tempfile as _tf693, os as _os693
    _tmp_db = Path(REPO / "_test_i693_knowledge.db")
    try:
        _con693 = sqlite3.connect(str(_tmp_db))
        _con693.execute(
            "CREATE TABLE IF NOT EXISTS knowledge_entries "
            "(id INTEGER PRIMARY KEY, category TEXT, title TEXT, last_seen TEXT, confidence REAL, deleted_at TEXT)"
        )
        _now_iso = "2024-01-01T00:00:00"  # definitely > 90 days ago
        _con693.execute("INSERT INTO knowledge_entries (category, title, last_seen, confidence) VALUES (?,?,?,?)",
                        ("mistake", "old mistake", _now_iso, 0.8))
        _con693.commit()
        _con693.close()

        _fr_result = _run_freshness(_tmp_db, days=90)
        test(
            "I693-2d: _run_freshness_check finds stale entry in test DB",
            _fr_result.get("status") == "ok" and _fr_result.get("count", 0) >= 1,
            f"status={_fr_result.get('status')} count={_fr_result.get('count')}",
        )

        # Test that _execute_task writes JSON artifact to ARTIFACTS_DIR
        _execute_task = getattr(_ct_mod, "_execute_task", None)
        if _execute_task is not None and _artifacts_dir is not None:
            _orig_arts_dir = _ct_mod.ARTIFACTS_DIR
            _orig_ss = _ct_mod.SESSION_STATE
            _tmp_arts = Path(REPO / "_test_i693_artifacts")
            _tmp_arts.mkdir(exist_ok=True)
            _ct_mod.ARTIFACTS_DIR = _tmp_arts
            _ct_mod.SESSION_STATE = REPO / "_test_i693_state"
            (REPO / "_test_i693_state").mkdir(exist_ok=True)
            # patch DB path used internally
            _orig_run_freshness = _ct_mod._run_freshness_check
            _ct_mod._run_freshness_check = lambda db_path, **kw: _run_freshness(_tmp_db, **kw)
            try:
                _fake_task = {
                    "id": "test001",
                    "name": "test freshness",
                    "template": "freshness-check",
                    "schedule": {"kind": "daily", "time": "09:00"},
                    "retention_days": 90,
                }
                from datetime import datetime as _dt693
                _now_dt = _dt693.now().astimezone()
                _log = _execute_task(_fake_task, _now_dt)
                _expected_json = _tmp_arts / f"freshness-{_now_dt.strftime('%Y%m%d')}.json"
                test(
                    "I693-2e: _execute_task writes freshness JSON artifact to ARTIFACTS_DIR",
                    _expected_json.exists(),
                    f"expected={_expected_json} log={_log.get('artifact_path')}",
                )
                if _expected_json.exists():
                    _json_data = json.loads(_expected_json.read_text())
                    test(
                        "I693-2f: freshness JSON artifact contains 'entries' key",
                        "entries" in _json_data,
                        f"keys={list(_json_data.keys())}",
                    )
            finally:
                _ct_mod.ARTIFACTS_DIR = _orig_arts_dir
                _ct_mod.SESSION_STATE = _orig_ss
                _ct_mod._run_freshness_check = _orig_run_freshness
                import shutil as _sh693
                _sh693.rmtree(str(_tmp_arts), ignore_errors=True)
                _sh693.rmtree(str(REPO / "_test_i693_state"), ignore_errors=True)
    finally:
        try:
            _tmp_db.unlink(missing_ok=True)
        except Exception:
            pass

except Exception as _e693a:
    test("I693-cron: freshness-check cron template", False, str(_e693a))

# --- I693-3: staleness banner appears only at >= 40% threshold ---
try:
    import importlib.util as _ilu693b, types as _types693b
    _br_src = (REPO / "briefing.py").read_text()
    _br_mod = _types693b.ModuleType("briefing_mod_693b")
    _br_mod.__file__ = str(REPO / "briefing.py")
    exec(compile(_br_src, str(REPO / "briefing.py"), "exec"), _br_mod.__dict__)

    _gen_wakeup = getattr(_br_mod, "generate_wakeup", None)
    test("I693-3a: generate_wakeup function exists in briefing.py", _gen_wakeup is not None)

    if _gen_wakeup is not None:
        # Build a temp DB with controlled stale/total ratio
        _tmp_br_db = Path(REPO / "_test_i693b_knowledge.db")
        try:
            _con_br = sqlite3.connect(str(_tmp_br_db))
            _con_br.execute(
                "CREATE TABLE IF NOT EXISTS knowledge_entries "
                "(id INTEGER PRIMARY KEY, category TEXT, title TEXT, last_seen TEXT, "
                "confidence REAL, occurrence_count INTEGER, intensity REAL, deleted_at TEXT)"
            )
            _con_br.execute(
                "CREATE TABLE IF NOT EXISTS wakeup_config (key TEXT PRIMARY KEY, value TEXT)"
            )
            # Insert 10 entries: 5 stale (old date), 5 fresh (today)
            _old = "2020-01-01T00:00:00"
            import datetime as _datetime693b
            _fresh = _datetime693b.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S")
            for _i in range(5):
                _con_br.execute(
                    "INSERT INTO knowledge_entries (category, title, last_seen, confidence, occurrence_count) VALUES (?,?,?,?,?)",
                    ("mistake", f"old mistake {_i}", _old, 0.8, 1),
                )
            for _i in range(5):
                _con_br.execute(
                    "INSERT INTO knowledge_entries (category, title, last_seen, confidence, occurrence_count) VALUES (?,?,?,?,?)",
                    ("pattern", f"fresh pattern {_i}", _fresh, 0.8, 1),
                )
            _con_br.commit()
            _con_br.close()

            # Patch DB_PATH in the loaded briefing module
            _orig_db_path_br = _br_mod.DB_PATH
            _br_mod.DB_PATH = _tmp_br_db
            try:
                _wu_output = _gen_wakeup()
                test(
                    "I693-3b: wakeup banner appears when stale_pct=50% (>=40%)",
                    "⚠" in _wu_output and "stale entries" in _wu_output,
                    f"output_last200={_wu_output[-200:]}",
                )

                # Now test below 40% threshold: remove 3 stale entries (2 stale / 7 fresh = 28%)
                _con_br2 = sqlite3.connect(str(_tmp_br_db))
                _con_br2.execute("DELETE FROM knowledge_entries WHERE title LIKE 'old mistake%' AND id > (SELECT MIN(id) FROM knowledge_entries WHERE title LIKE 'old mistake%') + 1")
                _con_br2.commit()
                _con_br2.close()
                _wu_output2 = _gen_wakeup()
                test(
                    "I693-3c: wakeup banner absent when stale_pct < 40%",
                    "stale entries" not in _wu_output2,
                    f"output_last200={_wu_output2[-200:]}",
                )
            finally:
                _br_mod.DB_PATH = _orig_db_path_br
        finally:
            try:
                _tmp_br_db.unlink(missing_ok=True)
            except Exception:
                pass

except Exception as _e693b:
    test("I693-wakeup: staleness banner in generate_wakeup", False, str(_e693b))

# ---------------------------------------------------------------------------
# I694: sk doctor global health surface (watcher, DB size, index, sync, hooks)
print("\n🏥 I694: doctor() global health surface")

try:
    import importlib.util as _ilu694, types as _types694, io as _io694

    _inst_src = (REPO / "install.py").read_text()
    _inst_mod = _types694.ModuleType("install_mod_694")
    _inst_mod.__file__ = str(REPO / "install.py")
    exec(compile(_inst_src, str(REPO / "install.py"), "exec"), _inst_mod.__dict__)

    # --- I694-1: _doctor_watcher_status returns expected keys ---
    _ws = _inst_mod._doctor_watcher_status()
    test(
        "I694-1: _doctor_watcher_status returns dict with running/pid keys",
        isinstance(_ws, dict) and "running" in _ws and "pid" in _ws,
        f"got keys={list(_ws.keys())}",
    )

    # --- I694-2: _doctor_db_size returns expected keys ---
    _ds = _inst_mod._doctor_db_size()
    test(
        "I694-2a: _doctor_db_size returns dict with size_mb/exists/db_path keys",
        isinstance(_ds, dict) and "size_mb" in _ds and "exists" in _ds and "db_path" in _ds,
        f"got keys={list(_ds.keys())}",
    )
    test(
        "I694-2b: _doctor_db_size size_mb is a float >= 0",
        isinstance(_ds["size_mb"], (int, float)) and _ds["size_mb"] >= 0,
        f"size_mb={_ds['size_mb']}",
    )

    # --- I694-3: _doctor_db_size with missing DB returns size_mb=0, exists=False ---
    _orig_db_path = _inst_mod.DB_PATH
    _inst_mod.DB_PATH = Path("/nonexistent/knowledge.db")
    try:
        _ds_missing = _inst_mod._doctor_db_size()
        test(
            "I694-3: _doctor_db_size returns exists=False and size_mb=0 for missing DB",
            _ds_missing["exists"] is False and _ds_missing["size_mb"] == 0.0,
            f"exists={_ds_missing['exists']} size_mb={_ds_missing['size_mb']}",
        )
    finally:
        _inst_mod.DB_PATH = _orig_db_path

    # --- I694-4: _doctor_index_health returns expected keys (fails-open) ---
    _ih = _inst_mod._doctor_index_health()
    test(
        "I694-4a: _doctor_index_health returns dict with score/total/available/error keys",
        isinstance(_ih, dict) and all(k in _ih for k in ("score", "total", "available", "error")),
        f"got keys={list(_ih.keys())}",
    )

    # Test fails-open when script missing
    _orig_script_dir = _inst_mod._SCRIPT_DIR
    _inst_mod._SCRIPT_DIR = Path("/nonexistent/dir")
    try:
        _ih_missing = _inst_mod._doctor_index_health()
        test(
            "I694-4b: _doctor_index_health available=False when script missing",
            _ih_missing["available"] is False,
            f"available={_ih_missing['available']}",
        )
    finally:
        _inst_mod._SCRIPT_DIR = _orig_script_dir

    # --- I694-5: _doctor_sync_status returns expected keys (fails-open) ---
    _ss694 = _inst_mod._doctor_sync_status()
    test(
        "I694-5a: _doctor_sync_status returns dict with configured/gateway_available/available/error keys",
        isinstance(_ss694, dict) and all(k in _ss694 for k in ("configured", "gateway_available", "available", "error")),
        f"got keys={list(_ss694.keys())}",
    )

    # Test fails-open when script missing
    _inst_mod._SCRIPT_DIR = Path("/nonexistent/dir")
    try:
        _ss_missing = _inst_mod._doctor_sync_status()
        test(
            "I694-5b: _doctor_sync_status available=False when script missing",
            _ss_missing["available"] is False,
            f"available={_ss_missing['available']}",
        )
    finally:
        _inst_mod._SCRIPT_DIR = _orig_script_dir

    # --- I694-6: _doctor_hooks_count returns expected keys ---
    _hc = _inst_mod._doctor_hooks_count()
    test(
        "I694-6a: _doctor_hooks_count returns dict with count/hooks_json_exists/error keys",
        isinstance(_hc, dict) and all(k in _hc for k in ("count", "hooks_json_exists", "error")),
        f"got keys={list(_hc.keys())}",
    )
    test(
        "I694-6b: _doctor_hooks_count count is int >= 0",
        isinstance(_hc["count"], int) and _hc["count"] >= 0,
        f"count={_hc['count']}",
    )

    # Test fails-open when hooks.json missing
    _orig_copilot_dir = _inst_mod.COPILOT_DIR
    _inst_mod.COPILOT_DIR = Path("/nonexistent/dir")
    try:
        _hc_missing = _inst_mod._doctor_hooks_count()
        test(
            "I694-6c: _doctor_hooks_count hooks_json_exists=False when dir missing",
            _hc_missing["hooks_json_exists"] is False,
            f"hooks_json_exists={_hc_missing['hooks_json_exists']}",
        )
    finally:
        _inst_mod.COPILOT_DIR = _orig_copilot_dir

    # --- I694-7: doctor --json emits valid JSON with issues[] array ---
    _result694 = subprocess.run(
        [sys.executable, str(REPO / "install.py"), "--doctor", "--json"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    test(
        "I694-7a: doctor --json exits without error",
        _result694.returncode in (0, 1),
        f"rc={_result694.returncode} stderr={_result694.stderr[:100]}",
    )
    try:
        _json694 = json.loads(_result694.stdout)
        test(
            "I694-7b: doctor --json output is valid JSON with issues[] key",
            "issues" in _json694 and isinstance(_json694["issues"], list),
            f"keys={list(_json694.keys())}",
        )
        test(
            "I694-7c: doctor --json output has watcher/db/index_health/sync/hooks keys",
            all(k in _json694 for k in ("watcher", "db", "index_health", "sync", "hooks")),
            f"keys={list(_json694.keys())}",
        )
        test(
            "I694-7d: doctor --json watcher has running key",
            "running" in _json694.get("watcher", {}),
            f"watcher={_json694.get('watcher')}",
        )
        test(
            "I694-7e: doctor --json db has size_mb key",
            "size_mb" in _json694.get("db", {}),
            f"db={_json694.get('db')}",
        )
        test(
            "I694-7f: doctor --json hooks has count key",
            "count" in _json694.get("hooks", {}),
            f"hooks={_json694.get('hooks')}",
        )
        test(
            "I694-7g: doctor --json issue_count matches len(issues)",
            _json694.get("issue_count") == len(_json694.get("issues", [])),
            f"issue_count={_json694.get('issue_count')} issues={len(_json694.get('issues', []))}",
        )
    except json.JSONDecodeError as _je694:
        test("I694-7b: doctor --json valid JSON", False, f"JSONDecodeError: {_je694} stdout={_result694.stdout[:200]}")

    # --- I694-8: doctor() exits non-zero when issues present (simulated) ---
    # Simulate by patching _watcher_running to False and hooks missing
    with tempfile.TemporaryDirectory(prefix="i694-test-") as _i694_tmp:
        _fake_home = Path(_i694_tmp)
        _fake_copilot = _fake_home / ".copilot"
        _fake_copilot.mkdir()
        # patch COPILOT_DIR and LOCK_FILE to simulate no hooks, no watcher
        _orig_copilot_i694 = _inst_mod.COPILOT_DIR
        _orig_lock_i694 = _inst_mod.LOCK_FILE
        _inst_mod.COPILOT_DIR = _fake_copilot
        _inst_mod.LOCK_FILE = _fake_copilot / ".watcher.lock"
        _orig_stdout694 = sys.stdout
        sys.stdout = _io694.StringIO()
        try:
            _rc694_issues = _inst_mod.doctor(manifest_only=False, as_json=False)
        except Exception:
            _rc694_issues = -1
        finally:
            sys.stdout = _orig_stdout694
            _inst_mod.COPILOT_DIR = _orig_copilot_i694
            _inst_mod.LOCK_FILE = _orig_lock_i694
        test(
            "I694-8: doctor() returns non-zero when hooks.json missing",
            _rc694_issues > 0,
            f"rc={_rc694_issues}",
        )

except Exception as _e694:
    test("I694: doctor global health surface", False, str(_e694))

# ---------------------------------------------------------------------------
# I695: _should_use_writer_broker() auto-enable detection
# ---------------------------------------------------------------------------
print("\n🔌 I695: _should_use_writer_broker() auto-enable detection")

try:
    import importlib.util as _ilu695
    import time as _time695

    _spec695 = _ilu695.spec_from_file_location("learn_i695", REPO / "learn.py")
    _learn695 = _ilu695.module_from_spec(_spec695)  # type: ignore[arg-type]
    _spec695.loader.exec_module(_learn695)  # type: ignore[union-attr]

    _fn695 = _learn695._should_use_writer_broker
    _marker_ttl695 = _learn695._MARKER_ENTRY_TTL

    # --- I695-1: auto-enable returns True when marker exists with fresh entry ---
    with tempfile.TemporaryDirectory(prefix="i695-test-") as _td695:
        _marker_dir695 = Path(_td695) / ".copilot" / "markers"
        _marker_dir695.mkdir(parents=True)
        _marker_file695 = _marker_dir695 / "dispatched-subagent-active"
        _fresh_ts695 = _time695.time() - 60  # 1 minute ago — within 4h TTL
        _marker_file695.write_text(
            json.dumps({
                "name": "dispatched-subagent-active",
                "ts": str(int(_fresh_ts695)),
                "active_tentacles": [
                    {"name": "i695-test", "ts": _fresh_ts695, "git_root": "/repo"},
                ],
            }),
            encoding="utf-8",
        )
        _orig_path695 = _learn695._DISPATCHED_MARKER_PATH
        _learn695._DISPATCHED_MARKER_PATH = _marker_file695
        _orig_env695 = os.environ.pop("SK_WRITER_BROKER", None)
        try:
            _result695_1 = _fn695()
        finally:
            _learn695._DISPATCHED_MARKER_PATH = _orig_path695
            if _orig_env695 is not None:
                os.environ["SK_WRITER_BROKER"] = _orig_env695
        test("I695-1: auto-enable True with fresh marker entry", _result695_1, f"got={_result695_1}")

    # --- I695-2: auto-enable returns False when marker entry is expired (>4h) ---
    with tempfile.TemporaryDirectory(prefix="i695-test-") as _td695b:
        _marker_dir695b = Path(_td695b) / ".copilot" / "markers"
        _marker_dir695b.mkdir(parents=True)
        _marker_file695b = _marker_dir695b / "dispatched-subagent-active"
        _old_ts695 = _time695.time() - (_marker_ttl695 + 3600)  # 5h ago — expired
        _marker_file695b.write_text(
            json.dumps({
                "name": "dispatched-subagent-active",
                "ts": str(int(_old_ts695)),
                "active_tentacles": [
                    {"name": "i695-stale", "ts": _old_ts695, "git_root": "/repo"},
                ],
            }),
            encoding="utf-8",
        )
        _orig_path695b = _learn695._DISPATCHED_MARKER_PATH
        _learn695._DISPATCHED_MARKER_PATH = _marker_file695b
        _orig_env695b = os.environ.pop("SK_WRITER_BROKER", None)
        try:
            _result695_2 = _fn695()
        finally:
            _learn695._DISPATCHED_MARKER_PATH = _orig_path695b
            if _orig_env695b is not None:
                os.environ["SK_WRITER_BROKER"] = _orig_env695b
        test("I695-2: auto-enable False with expired marker entry", not _result695_2, f"got={_result695_2}")

    # --- I695-3: auto-enable returns False when SK_WRITER_BROKER=0 explicitly set ---
    _orig_env695c = os.environ.get("SK_WRITER_BROKER")
    os.environ["SK_WRITER_BROKER"] = "0"
    try:
        _result695_3 = _fn695()
    finally:
        if _orig_env695c is None:
            del os.environ["SK_WRITER_BROKER"]
        else:
            os.environ["SK_WRITER_BROKER"] = _orig_env695c
    test("I695-3: auto-enable False when SK_WRITER_BROKER=0", not _result695_3, f"got={_result695_3}")

    # --- I695-4: auto-enable returns False when marker file is missing ---
    with tempfile.TemporaryDirectory(prefix="i695-test-") as _td695d:
        _missing695 = Path(_td695d) / "nonexistent-marker"
        _orig_path695d = _learn695._DISPATCHED_MARKER_PATH
        _learn695._DISPATCHED_MARKER_PATH = _missing695
        _orig_env695d = os.environ.pop("SK_WRITER_BROKER", None)
        try:
            _result695_4 = _fn695()
        finally:
            _learn695._DISPATCHED_MARKER_PATH = _orig_path695d
            if _orig_env695d is not None:
                os.environ["SK_WRITER_BROKER"] = _orig_env695d
        test("I695-4: auto-enable False when marker file missing", not _result695_4, f"got={_result695_4}")

    # --- I695-5: auto-enable returns False when marker file is malformed JSON (fail-open) ---
    with tempfile.TemporaryDirectory(prefix="i695-test-") as _td695e:
        _marker_dir695e = Path(_td695e) / ".copilot" / "markers"
        _marker_dir695e.mkdir(parents=True)
        _marker_file695e = _marker_dir695e / "dispatched-subagent-active"
        _marker_file695e.write_text("{ this is not valid json !!!", encoding="utf-8")
        _orig_path695e = _learn695._DISPATCHED_MARKER_PATH
        _learn695._DISPATCHED_MARKER_PATH = _marker_file695e
        _orig_env695e = os.environ.pop("SK_WRITER_BROKER", None)
        try:
            _result695_5 = _fn695()
        finally:
            _learn695._DISPATCHED_MARKER_PATH = _orig_path695e
            if _orig_env695e is not None:
                os.environ["SK_WRITER_BROKER"] = _orig_env695e
        test("I695-5: auto-enable False on malformed JSON (fail-open)", not _result695_5, f"got={_result695_5}")

except Exception as _e695:
    test("I695: _should_use_writer_broker setup", False, str(_e695))

# ---------------------------------------------------------------------------
# I697: --refresh-cost backfills NULL cost columns in sessions table
# ---------------------------------------------------------------------------
print("\n💰 I697: --refresh-cost backfills NULL cost columns")

import importlib.util as _ilu697
import io as _io697
import json as _json697

try:
    _spec697 = _ilu697.spec_from_file_location("build_session_index", REPO / "build-session-index.py")
    _bsi697 = _ilu697.module_from_spec(_spec697)
    _spec697.loader.exec_module(_bsi697)

    # Helper: create a minimal in-memory sessions table
    def _make_cost_db(sessions: list[dict]) -> sqlite3.Connection:
        """Create an in-memory DB with the sessions table populated from *sessions*."""
        db = sqlite3.connect(":memory:")
        db.execute("""
            CREATE TABLE sessions (
                id TEXT PRIMARY KEY,
                path TEXT,
                cost_usd_est REAL,
                total_input_tokens INTEGER,
                total_output_tokens INTEGER
            )
        """)
        for s in sessions:
            db.execute(
                "INSERT INTO sessions (id, path, cost_usd_est, total_input_tokens, total_output_tokens) VALUES (?,?,?,?,?)",
                (s["id"], s.get("path"), s.get("cost_usd_est"), s.get("total_input_tokens"), s.get("total_output_tokens")),
            )
        db.commit()
        return db

    # --- I697-1: sessions with NULL cost_usd_est are updated ---
    with tempfile.TemporaryDirectory(prefix="i697-test-") as _i697_tmp:
        _sess_dir = Path(_i697_tmp) / "sess1"
        _sess_dir.mkdir()
        # Write a minimal events.jsonl with a session.shutdown event
        _events = {
            "type": "session.shutdown",
            "data": {
                "modelMetrics": {
                    "claude-3-5-sonnet-20241022": {
                        "usage": {"inputTokens": 1000, "outputTokens": 200, "cacheReadTokens": 0}
                    }
                }
            },
        }
        (_sess_dir / "events.jsonl").write_text(_json697.dumps(_events) + "\n")

        _db697_1 = _make_cost_db([{"id": "s1", "path": str(_sess_dir), "cost_usd_est": None}])
        _bsi697._refresh_cost(_db697_1, limit=None)
        _row697_1 = _db697_1.execute("SELECT cost_usd_est, total_input_tokens, total_output_tokens FROM sessions WHERE id='s1'").fetchone()
        test(
            "I697-1: sessions with NULL cost_usd_est are updated after --refresh-cost",
            _row697_1 is not None and _row697_1[0] is not None and _row697_1[0] > 0,
            f"row={_row697_1}",
        )

    # --- I697-2: sessions with existing cost_usd_est are NOT overwritten ---
    with tempfile.TemporaryDirectory(prefix="i697-test-") as _i697_tmp2:
        _sess_dir2 = Path(_i697_tmp2) / "sess2"
        _sess_dir2.mkdir()
        _events2 = {
            "type": "session.shutdown",
            "data": {
                "modelMetrics": {
                    "claude-3-5-sonnet-20241022": {
                        "usage": {"inputTokens": 999, "outputTokens": 111, "cacheReadTokens": 0}
                    }
                }
            },
        }
        (_sess_dir2 / "events.jsonl").write_text(_json697.dumps(_events2) + "\n")

        _db697_2 = _make_cost_db([{"id": "s2", "path": str(_sess_dir2), "cost_usd_est": 0.042, "total_input_tokens": 500, "total_output_tokens": 50}])
        _bsi697._refresh_cost(_db697_2, limit=None)
        _row697_2 = _db697_2.execute("SELECT cost_usd_est, total_input_tokens FROM sessions WHERE id='s2'").fetchone()
        test(
            "I697-2: sessions with existing cost_usd_est are NOT overwritten",
            _row697_2 is not None and abs(_row697_2[0] - 0.042) < 1e-9 and _row697_2[1] == 500,
            f"row={_row697_2}",
        )

    # --- I697-3: --limit 2 stops after refreshing 2 sessions ---
    with tempfile.TemporaryDirectory(prefix="i697-test-") as _i697_tmp3:
        _sessions_697_3 = []
        for _i3 in range(4):
            _sd = Path(_i697_tmp3) / f"sess{_i3}"
            _sd.mkdir()
            _ev = {
                "type": "session.shutdown",
                "data": {
                    "modelMetrics": {
                        "claude-3-5-sonnet-20241022": {
                            "usage": {"inputTokens": 100, "outputTokens": 10, "cacheReadTokens": 0}
                        }
                    }
                },
            }
            (_sd / "events.jsonl").write_text(_json697.dumps(_ev) + "\n")
            _sessions_697_3.append({"id": f"s3_{_i3}", "path": str(_sd), "cost_usd_est": None})

        _db697_3 = _make_cost_db(_sessions_697_3)
        _bsi697._refresh_cost(_db697_3, limit=2)
        _nulls_left = _db697_3.execute("SELECT COUNT(*) FROM sessions WHERE cost_usd_est IS NULL").fetchone()[0]
        _updated = _db697_3.execute("SELECT COUNT(*) FROM sessions WHERE cost_usd_est IS NOT NULL").fetchone()[0]
        test(
            "I697-3: --limit 2 stops after refreshing 2 sessions (2 updated, 2 still NULL)",
            _updated == 2 and _nulls_left == 2,
            f"updated={_updated} nulls_left={_nulls_left}",
        )

    # --- I697-4: sessions with no events.jsonl are counted as skipped ---
    with tempfile.TemporaryDirectory(prefix="i697-test-") as _i697_tmp4:
        _sess_dir4 = Path(_i697_tmp4) / "sess_noev"
        _sess_dir4.mkdir()
        # No events.jsonl written — dir exists but file absent

        _db697_4 = _make_cost_db([{"id": "s4", "path": str(_sess_dir4), "cost_usd_est": None}])
        _captured697 = _io697.StringIO()
        _orig_stdout697 = sys.stdout
        sys.stdout = _captured697
        try:
            _bsi697._refresh_cost(_db697_4, limit=None)
        finally:
            sys.stdout = _orig_stdout697
        _out697_4 = _captured697.getvalue()
        _row697_4 = _db697_4.execute("SELECT cost_usd_est FROM sessions WHERE id='s4'").fetchone()
        test(
            "I697-4a: sessions with no events.jsonl leave cost_usd_est NULL",
            _row697_4 is not None and _row697_4[0] is None,
            f"cost_usd_est={_row697_4[0] if _row697_4 else 'row missing'}",
        )
        test(
            "I697-4b: skipped count appears in progress output",
            "skipped" in _out697_4,
            f"output={_out697_4!r}",
        )

except Exception as _e697:
    test("I697: --refresh-cost backfills NULL cost columns", False, str(_e697))

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
print(f"\n{'=' * 60}")
print(f"Results: {PASS}/{PASS + FAIL} passed")
if FAIL == 0:
    print("🎉 All tests passed!")
else:
    print(f"⚠️  {FAIL} test(s) need attention")
sys.exit(0 if FAIL == 0 else 1)
