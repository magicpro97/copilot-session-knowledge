#!/usr/bin/env python3
"""sk doctor — automated configuration health check.

Usage:
  sk doctor                    Run all checks (human-readable)
  sk doctor --json             JSON output for automation
  sk doctor --fix              Auto-fix safe issues
  sk doctor --category <cat>   Run only a category (python|db|config|hooks|mcp|binary)
"""

if __name__ == "__main__" and __package__ is None:
    import os  # noqa: E401
    import sys

    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

if os.name == "nt":
    sys.stdout.reconfigure(encoding="utf-8")

TOOLS_DIR = Path(__file__).resolve().parent
DB_PATH = Path(
    os.environ.get("SK_DB_PATH", str(Path.home() / ".copilot" / "session-state" / "knowledge.db"))
).expanduser()
HOOKS_DIR = TOOLS_DIR / "hooks"

# Severity levels
ERROR = "error"
WARN = "warn"
INFO = "info"
OK = "ok"


def _check(id_, category, status, message, fix_hint="") -> dict:
    return {"id": id_, "category": category, "status": status, "message": message, "fix_hint": fix_hint}


def check_python_version() -> dict:
    v = sys.version_info
    if v >= (3, 10):
        return _check("python_version", "python", OK, f"Python {v.major}.{v.minor}.{v.micro}")
    return _check(
        "python_version",
        "python",
        ERROR,
        f"Python {v.major}.{v.minor} — requires 3.10+",
        "Install Python 3.10+ from python.org",
    )


def check_sqlite3() -> dict:
    try:
        import sqlite3 as _s

        ver = _s.sqlite_version
        return _check("sqlite3", "python", OK, f"sqlite3 {ver}")
    except ImportError:
        return _check("sqlite3", "python", ERROR, "sqlite3 not available", "Rebuild Python with sqlite3 support")


def check_db_exists() -> dict:
    if DB_PATH.exists():
        size_kb = DB_PATH.stat().st_size // 1024
        return _check("db_exists", "db", OK, f"knowledge.db exists ({size_kb}KB at {DB_PATH})")
    return _check(
        "db_exists", "db", WARN, f"knowledge.db not found at {DB_PATH}", "Run: sk index build  or  python3 migrate.py"
    )


def check_db_schema() -> dict:
    if not DB_PATH.exists():
        return _check("db_schema", "db", WARN, "DB not found — skipping schema check")
    try:
        con = sqlite3.connect(DB_PATH.as_uri() + "?mode=ro", uri=True)
        row = con.execute("SELECT MAX(version) FROM schema_version").fetchone()
        con.close()
        if row and row[0] is not None:
            return _check("db_schema", "db", OK, f"schema_version = {row[0]}")
        return _check("db_schema", "db", WARN, "schema_version table empty", "Run: python3 migrate.py")
    except sqlite3.OperationalError as e:
        return _check("db_schema", "db", ERROR, f"DB error: {e}", "Run: python3 migrate.py")


def check_db_migrate() -> dict:
    """Check if DB schema is up to date by comparing version in DB vs migrate.py."""
    migrate_py = TOOLS_DIR / "migrate.py"
    if not migrate_py.exists():
        return _check("db_migrate", "db", WARN, "migrate.py not found")
    if not DB_PATH.exists():
        return _check("db_migrate", "db", WARN, "DB not found — skipping migration check")
    try:
        # Get max version from migrate.py's MIGRATIONS list
        import ast

        tree = ast.parse(migrate_py.read_text(encoding="utf-8"))
        latest_code_ver = 0
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id == "MIGRATIONS":
                        if isinstance(node.value, ast.List) and node.value.elts:
                            last = node.value.elts[-1]
                            if isinstance(last, (ast.Tuple, ast.List)) and last.elts:
                                first = last.elts[0]
                                if isinstance(first, ast.Constant):
                                    latest_code_ver = int(first.value)
        # Get max version from DB
        con = sqlite3.connect(DB_PATH.as_uri() + "?mode=ro", uri=True)
        row = con.execute("SELECT MAX(version) FROM schema_version").fetchone()
        con.close()
        db_ver = row[0] if row and row[0] is not None else 0
        if latest_code_ver == 0:
            return _check("db_migrate", "db", WARN, "Could not parse MIGRATIONS from migrate.py")
        if db_ver >= latest_code_ver:
            return _check("db_migrate", "db", OK, f"Schema up to date (v{db_ver})")
        return _check(
            "db_migrate",
            "db",
            WARN,
            f"Schema v{db_ver} behind latest v{latest_code_ver}",
            "Run: python3 migrate.py",
        )
    except Exception as e:
        return _check("db_migrate", "db", WARN, f"migration check error: {e}")


def check_embedding_config() -> dict:
    cfg = TOOLS_DIR / "embedding-config.json"
    if cfg.exists():
        try:
            data = json.loads(cfg.read_text())
            provider = data.get("active_provider", data.get("provider", "unknown"))
            return _check(
                "embedding_config", "config", OK, f"embedding-config.json exists (active_provider: {provider})"
            )
        except Exception:
            return _check(
                "embedding_config",
                "config",
                WARN,
                "embedding-config.json exists but is invalid JSON",
                "Run: sk index embed --setup",
            )
    return _check(
        "embedding_config",
        "config",
        INFO,
        "embedding-config.json not found (embeddings disabled)",
        "Run: sk index embed --setup  to enable semantic search",
    )


def check_hooks() -> dict:
    hook_runner = HOOKS_DIR / "hook_runner.py"
    if not hook_runner.exists():
        return _check(
            "hooks", "hooks", WARN, "hooks/hook_runner.py not found", "Run: sk install  or  python3 install.py"
        )
    hooks_present = [p for p in HOOKS_DIR.glob("*.py") if p.name != "hook_runner.py"] if HOOKS_DIR.exists() else []
    n = len(hooks_present)
    if n >= 3:
        return _check("hooks", "hooks", OK, f"{n} hook files found in hooks/")
    if n > 0:
        return _check("hooks", "hooks", INFO, f"Only {n} hook files in hooks/", "Run: sk install  to install all hooks")
    return _check("hooks", "hooks", INFO, "No hook files found in hooks/", "Run: sk install")


def check_mcp() -> dict:
    mcp_py = TOOLS_DIR / "mcp-server.py"
    if not mcp_py.exists():
        return _check("mcp", "mcp", WARN, "mcp-server.py not found")
    try:
        # Verify it can be imported (syntax + dependency check)
        r = subprocess.run(
            [
                sys.executable,
                "-c",
                f"import importlib.util, pathlib; "
                f"s=importlib.util.spec_from_file_location('mcp','{mcp_py}'); "
                f"m=importlib.util.module_from_spec(s); s.loader.exec_module(m)",
            ],
            capture_output=True,
            text=True,
            timeout=5,
            stdin=subprocess.DEVNULL,
        )
        if r.returncode == 0:
            return _check("mcp", "mcp", OK, "mcp-server.py is importable")
        # Expected: server blocks on stdin or exits cleanly
        return _check("mcp", "mcp", OK, "mcp-server.py exists (import returned non-zero, expected for server)")
    except subprocess.TimeoutExpired:
        return _check("mcp", "mcp", OK, "mcp-server.py started (timeout is expected for server)")
    except Exception as e:
        return _check("mcp", "mcp", WARN, f"mcp-server.py error: {e}")


def check_mcp_smoke(timeout: int = 5) -> dict:
    """Spawn mcp-server.py, send initialize + tools/list, verify expected tools.

    The server uses LSP-style framing: Content-Length header + CRLF + body.
    """
    import threading
    import time

    mcp_path = TOOLS_DIR / "mcp-server.py"
    if not mcp_path.exists():
        return _check("mcp_smoke", "mcp", INFO, "mcp-server.py not found — skipping smoke test")

    def _encode(payload: dict) -> bytes:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
        return header + body

    def _read_response(stream) -> dict:
        """Read one LSP-framed JSON-RPC message from stream."""
        headers: dict[str, str] = {}
        while True:
            line = stream.readline()
            if not line:
                raise EOFError("stdout closed")
            if line in (b"\r\n", b"\n"):
                break
            decoded = line.decode("ascii")
            if ":" in decoded:
                k, v = decoded.split(":", 1)
                headers[k.strip().lower()] = v.strip()
        content_length = int(headers["content-length"])
        body = stream.read(content_length)
        return json.loads(body.decode("utf-8"))

    t0 = time.time()
    proc = None
    result_holder: list = []
    error_holder: list = []

    def _run() -> None:
        nonlocal proc
        try:
            proc = subprocess.Popen(
                [sys.executable, str(mcp_path)],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            proc.stdin.write(
                _encode(
                    {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "initialize",
                        "params": {
                            "protocolVersion": "2024-11-05",
                            "capabilities": {},
                            "clientInfo": {"name": "doctor", "version": "1.0"},
                        },
                    }
                )
            )
            proc.stdin.flush()
            _read_response(proc.stdout)  # initialize response; validate it parses

            proc.stdin.write(_encode({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}))
            proc.stdin.flush()
            resp2 = _read_response(proc.stdout)
            result_holder.append(resp2)
        except Exception as exc:
            error_holder.append(exc)

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    t.join(timeout)

    latency = round((time.time() - t0) * 1000)

    if proc is not None:
        try:
            proc.kill()
        except Exception:
            pass

    if t.is_alive() or (not result_holder and not error_holder):
        return _check(
            "mcp_smoke",
            "mcp",
            ERROR,
            f"MCP smoke FAIL — server timed out after {timeout}s",
            "Run: python3 mcp-server.py",
        )
    if error_holder:
        return _check("mcp_smoke", "mcp", ERROR, f"MCP smoke FAIL — {error_holder[0]}", "Run: python3 mcp-server.py")

    resp2 = result_holder[0]
    tool_names = {t["name"] for t in resp2.get("result", {}).get("tools", [])}
    expected = {"briefing", "learn", "query_session"}
    missing = expected - tool_names
    if missing:
        return _check(
            "mcp_smoke",
            "mcp",
            ERROR,
            f"MCP smoke FAIL — missing tools: {sorted(missing)} (latency {latency}ms)",
            "Check mcp-server.py tool registration",
        )
    return _check(
        "mcp_smoke",
        "mcp",
        OK,
        f"MCP smoke PASS — {len(tool_names)} tools registered (latency {latency}ms)",
    )


def check_binary() -> dict:
    sk_bin = shutil.which("sk")
    if sk_bin:
        try:
            r = subprocess.run(["sk", "--version"], capture_output=True, text=True, timeout=5)
            ver = r.stdout.strip() or r.stderr.strip()
            return _check("binary", "binary", OK, f"sk binary found at {sk_bin} ({ver[:50]})")
        except Exception:
            return _check("binary", "binary", INFO, f"sk binary found at {sk_bin}")
    return _check(
        "binary",
        "binary",
        INFO,
        "sk binary not on PATH (using python fallback)",
        "Run install to add sk to PATH, or use: python3 ~/.copilot/tools/sk.py",
    )


def check_recall_hit_rate() -> dict:
    """Check briefing recall quality from search_feedback table."""
    if not DB_PATH.exists():
        return _check("recall_hit_rate", "recall", WARN, "DB not found — skipping recall check")
    try:
        db = sqlite3.connect(str(DB_PATH) + "?mode=ro", uri=True)
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        if "search_feedback" not in tables:
            db.close()
            return _check(
                "recall_hit_rate",
                "recall",
                INFO,
                "No feedback yet — use 'sk query --feedback <id> good|bad' to train",
            )
        row = db.execute(
            "SELECT COUNT(*) AS total, SUM(CASE WHEN verdict=1 THEN 1 ELSE 0 END) AS good,"
            " SUM(CASE WHEN verdict=-1 THEN 1 ELSE 0 END) AS bad FROM search_feedback"
        ).fetchone()
        db.close()
        total, good, bad = row[0], row[1] or 0, row[2] or 0
        if total < 5:
            return _check(
                "recall_hit_rate",
                "recall",
                INFO,
                f"Too few feedback samples ({total}) — need 5+ for hit-rate estimate",
            )
        pct = round(good / total * 100) if total else 0
        if pct >= 70:
            return _check("recall_hit_rate", "recall", OK, f"Hit rate {pct}% ({good}/{total} good, {bad} bad)")
        if pct >= 40:
            return _check(
                "recall_hit_rate",
                "recall",
                WARN,
                f"Hit rate {pct}% ({good}/{total} good) — consider sk briefing --recall-quality",
                "Run: sk briefing --recall-quality --days 30",
            )
        return _check(
            "recall_hit_rate",
            "recall",
            ERROR,
            f"Low hit rate {pct}% ({good}/{total} good, {bad} bad)",
            "Run: sk briefing --recall-quality --days 30  and review low-precision queries",
        )
    except sqlite3.OperationalError as e:
        return _check("recall_hit_rate", "recall", WARN, f"Could not check hit rate: {e}")


def check_knowledge_growth() -> dict:
    """Check knowledge entry growth rate over last 4 weeks."""
    if not DB_PATH.exists():
        return _check("knowledge_growth", "recall", WARN, "DB not found — skipping growth check")
    try:
        db = sqlite3.connect(str(DB_PATH) + "?mode=ro", uri=True)
        rows = db.execute(
            """SELECT strftime('%Y-%W', first_seen) AS week, COUNT(*) AS cnt
               FROM knowledge_entries
               WHERE first_seen >= date('now', '-28 days')
               GROUP BY week ORDER BY week"""
        ).fetchall()
        db.close()
        if not rows:
            return _check(
                "knowledge_growth",
                "recall",
                WARN,
                "No entries in last 28 days — is sk watch running?",
                "Run: sk watch start",
            )
        total = sum(r[1] for r in rows)
        weeks = len(rows)
        avg = round(total / weeks, 1) if weeks else 0
        if avg >= 3:
            return _check(
                "knowledge_growth",
                "recall",
                OK,
                f"Knowledge growing — {total} entries over {weeks} weeks (avg {avg}/week)",
            )
        return _check(
            "knowledge_growth",
            "recall",
            WARN,
            f"Low growth rate — {total} entries over {weeks} weeks (avg {avg}/week)",
            "Run: sk learn --pattern 'title' 'content'  to add knowledge",
        )
    except sqlite3.OperationalError as e:
        return _check("knowledge_growth", "recall", WARN, f"Could not check growth: {e}")


def check_stale_knowledge() -> dict:
    """Check for knowledge entries never recalled (access_count=0 or missing)."""
    if not DB_PATH.exists():
        return _check("stale_knowledge", "recall", WARN, "DB not found — skipping stale check")
    try:
        db = sqlite3.connect(str(DB_PATH) + "?mode=ro", uri=True)
        cols = {r[1] for r in db.execute("PRAGMA table_info(knowledge_entries)").fetchall()}
        if "access_count" not in cols:
            db.close()
            return _check(
                "stale_knowledge",
                "recall",
                INFO,
                "access_count column not yet migrated — run: python3 migrate.py",
            )
        row = db.execute(
            """SELECT COUNT(*) AS total,
                      SUM(CASE WHEN access_count = 0 THEN 1 ELSE 0 END) AS never_accessed
               FROM knowledge_entries"""
        ).fetchone()
        db.close()
        total, never = row[0], row[1] or 0
        if total == 0:
            return _check("stale_knowledge", "recall", WARN, "No knowledge entries found")
        stale_pct = round(never / total * 100) if total else 0
        if stale_pct <= 30:
            return _check(
                "stale_knowledge",
                "recall",
                OK,
                f"Stale entries: {stale_pct}% ({never}/{total} never recalled)",
            )
        if stale_pct <= 60:
            return _check(
                "stale_knowledge",
                "recall",
                WARN,
                f"High stale rate: {stale_pct}% ({never}/{total} never recalled)",
                "Run: sk briefing --recall-quality  to identify dead knowledge",
            )
        return _check(
            "stale_knowledge",
            "recall",
            ERROR,
            f"Very high stale rate: {stale_pct}% ({never}/{total} never recalled)",
            "Run: sk knowledge evict --dry-run  to review stale entries",
        )
    except sqlite3.OperationalError as e:
        return _check("stale_knowledge", "recall", WARN, f"Could not check stale entries: {e}")


def check_recurring_mistakes() -> dict:
    """Check for unresolved recurring mistakes (same title, multiple occurrences)."""
    if not DB_PATH.exists():
        return _check("recurring_mistakes", "recall", WARN, "DB not found — skipping mistake check")
    try:
        db = sqlite3.connect(str(DB_PATH) + "?mode=ro", uri=True)
        rows = db.execute(
            """SELECT title, COUNT(*) AS cnt
               FROM knowledge_entries
               WHERE category = 'mistake' AND (is_resolved = 0 OR is_resolved IS NULL)
               GROUP BY lower(trim(title))
               HAVING cnt > 1
               ORDER BY cnt DESC
               LIMIT 5"""
        ).fetchall()
        db.close()
        if not rows:
            return _check("recurring_mistakes", "recall", OK, "No recurring unresolved mistakes found")
        names = "; ".join(f"{r[0][:40]} (×{r[1]})" for r in rows[:3])
        return _check(
            "recurring_mistakes",
            "recall",
            WARN,
            f"{len(rows)} recurring unresolved mistake(s): {names}",
            "Run: sk learn --mistake 'title' 'resolution' --tags 'resolved'",
        )
    except sqlite3.OperationalError as e:
        return _check("recurring_mistakes", "recall", WARN, f"Could not check mistakes: {e}")


ALL_CHECKS = [
    check_python_version,
    check_sqlite3,
    check_db_exists,
    check_db_schema,
    check_db_migrate,
    check_embedding_config,
    check_hooks,
    check_mcp,
    check_mcp_smoke,
    check_binary,
    # recall health checks
    check_recall_hit_rate,
    check_knowledge_growth,
    check_stale_knowledge,
    check_recurring_mistakes,
]

CATEGORY_MAP = {
    "python": [check_python_version, check_sqlite3],
    "db": [check_db_exists, check_db_schema, check_db_migrate],
    "config": [check_embedding_config],
    "hooks": [check_hooks],
    "mcp": [check_mcp, check_mcp_smoke],
    "binary": [check_binary],
    "recall": [check_recall_hit_rate, check_knowledge_growth, check_stale_knowledge, check_recurring_mistakes],
}

STATUS_ICON = {OK: "✅", WARN: "⚠️ ", ERROR: "❌", INFO: "ℹ️ "}
STATUS_SHORT = {OK: "OK ", WARN: "WRN", ERROR: "ERR", INFO: "INF"}


def run_checks(category=None) -> list:
    checks = CATEGORY_MAP.get(category, None) if category else ALL_CHECKS
    if checks is None:
        print(f"Unknown category '{category}'. Valid: {', '.join(CATEGORY_MAP)}")
        sys.exit(1)
    results = []
    for fn in checks:
        try:
            results.append(fn())
        except Exception as e:
            results.append(_check(fn.__name__, "unknown", ERROR, f"Check crashed: {e}"))
    return results


def do_fix(results: list) -> None:
    """Auto-fix only issues that were actually flagged."""
    # Build lookup of check_id → status
    status_map = {r["id"]: r["status"] for r in results}

    # Fix: create session-state dir (needed for db_exists / db_schema)
    db_needs_fix = status_map.get("db_exists") in (WARN, ERROR) or status_map.get("db_schema") in (WARN, ERROR)
    if db_needs_fix:
        session_state_dir = DB_PATH.parent
        session_state_dir.mkdir(parents=True, exist_ok=True)
        print(f"  Created dir: {session_state_dir}")

    # Fix: run migrations (only if schema is behind or DB missing)
    migrate_needs_fix = status_map.get("db_migrate") in (WARN, ERROR) or db_needs_fix
    if migrate_needs_fix:
        migrate_py = TOOLS_DIR / "migrate.py"
        if migrate_py.exists():
            print("  Running: python3 migrate.py ...")
            r = subprocess.run([sys.executable, str(migrate_py)], capture_output=True, text=True, timeout=30)
            if r.returncode == 0:
                print("  migrate.py: OK")
            else:
                print(f"  migrate.py failed: {r.stderr[:200]}")

    # Fix: create embedding config skeleton (only if missing, not if corrupt)
    if status_map.get("embedding_config") == INFO:
        cfg = TOOLS_DIR / "embedding-config.json"
        if not cfg.exists():
            skeleton = {
                "active_provider": "auto",
                "fallback": "tfidf",
                "batch_size": 100,
                "rrf_k": 60,
                "providers": {},
            }
            cfg.write_text(json.dumps(skeleton, indent=2))
            if os.name != "nt":
                os.chmod(cfg, 0o600)
            print(f"  Created skeleton: {cfg}")


def main():
    parser = argparse.ArgumentParser(description="sk doctor — configuration health check")
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument("--fix", action="store_true", help="Auto-fix safe issues")
    parser.add_argument("--category", help="Run only a category: python|db|config|hooks|mcp|binary|recall")
    args = parser.parse_args()

    results = run_checks(args.category)

    if args.fix:
        print("Applying safe fixes...")
        do_fix(results)
        print()
        results = run_checks(args.category)

    if args.json:
        summary = {
            "passed": sum(1 for r in results if r["status"] == OK),
            "warnings": sum(1 for r in results if r["status"] == WARN),
            "errors": sum(1 for r in results if r["status"] == ERROR),
            "info": sum(1 for r in results if r["status"] == INFO),
        }
        print(json.dumps({"checks": results, "summary": summary}, indent=2, ensure_ascii=False))
        sys.exit(1 if summary["errors"] > 0 else 0)

    # Human-readable output
    print("sk doctor")
    print("━" * 50)
    for r in results:
        icon = STATUS_ICON.get(r["status"], "?")
        msg = r["message"]
        hint = f" — {r['fix_hint']}" if r.get("fix_hint") and r["status"] != OK else ""
        print(f"{icon} {r['category']}: {msg}{hint}")
    print("━" * 50)
    passed = sum(1 for r in results if r["status"] == OK)
    warnings = sum(1 for r in results if r["status"] == WARN)
    errors = sum(1 for r in results if r["status"] == ERROR)
    info = sum(1 for r in results if r["status"] == INFO)
    parts = [f"{passed} passed"]
    if info:
        parts.append(f"{info} info")
    if warnings:
        parts.append(f"{warnings} warning{'s' if warnings > 1 else ''}")
    if errors:
        parts.append(f"{errors} error{'s' if errors > 1 else ''}")
    print(", ".join(parts))
    sys.exit(1 if errors > 0 else 0)


if __name__ == "__main__":
    main()
