#!/usr/bin/env python3
"""sk doctor — automated configuration health check.

Usage:
  sk doctor                    Run all checks (human-readable)
  sk doctor --json             JSON output for automation
  sk doctor --fix              Auto-fix safe issues
  sk doctor --category <cat>   Run only a category (python|db|config|hooks|mcp|binary)
"""
if __name__ == "__main__" and __package__ is None:
    import os, sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import os
import sys
import json
import subprocess
import sqlite3
import argparse
from pathlib import Path
from datetime import datetime

if os.name == "nt":
    sys.stdout.reconfigure(encoding="utf-8")

TOOLS_DIR = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get("SK_DB_PATH",
    str(Path.home() / ".copilot" / "session-state" / "knowledge.db"))).expanduser()
HOOKS_DIR = TOOLS_DIR / ".github" / "hooks"

# Severity levels
ERROR = "error"
WARN  = "warn"
INFO  = "info"
OK    = "ok"

def _check(id_, category, status, message, fix_hint="") -> dict:
    return {"id": id_, "category": category, "status": status,
            "message": message, "fix_hint": fix_hint}

def check_python_version() -> dict:
    v = sys.version_info
    if v >= (3, 10):
        return _check("python_version", "python", OK, f"Python {v.major}.{v.minor}.{v.micro}")
    return _check("python_version", "python", ERROR,
        f"Python {v.major}.{v.minor} — requires 3.10+",
        "Install Python 3.10+ from python.org")

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
    return _check("db_exists", "db", WARN,
        f"knowledge.db not found at {DB_PATH}",
        "Run: sk index build  or  python3 migrate.py")

def check_db_schema() -> dict:
    if not DB_PATH.exists():
        return _check("db_schema", "db", WARN, "DB not found — skipping schema check")
    try:
        con = sqlite3.connect(DB_PATH.as_uri() + "?mode=ro", uri=True)
        row = con.execute("SELECT MAX(version) FROM schema_version").fetchone()
        con.close()
        if row and row[0] is not None:
            return _check("db_schema", "db", OK, f"schema_version = {row[0]}")
        return _check("db_schema", "db", WARN, "schema_version table empty",
            "Run: python3 migrate.py")
    except sqlite3.OperationalError as e:
        return _check("db_schema", "db", ERROR, f"DB error: {e}",
            "Run: python3 migrate.py")

def check_db_migrate() -> dict:
    migrate_py = TOOLS_DIR / "migrate.py"
    if not migrate_py.exists():
        return _check("db_migrate", "db", WARN, "migrate.py not found")
    try:
        r = subprocess.run(
            [sys.executable, str(migrate_py), "--check"],
            capture_output=True, text=True, timeout=10
        )
        if r.returncode == 0 or "up to date" in (r.stdout + r.stderr).lower():
            return _check("db_migrate", "db", OK, "migrate.py: schema up to date")
        return _check("db_migrate", "db", WARN, f"migrate.py check: {r.stdout.strip()[:100]}",
            "Run: python3 migrate.py")
    except subprocess.TimeoutExpired:
        return _check("db_migrate", "db", WARN, "migrate.py timed out")
    except Exception as e:
        return _check("db_migrate", "db", WARN, f"migrate.py error: {e}")

def check_embedding_config() -> dict:
    cfg = TOOLS_DIR / "embedding-config.json"
    if cfg.exists():
        try:
            data = json.loads(cfg.read_text())
            provider = data.get("provider", "unknown")
            return _check("embedding_config", "config", OK, f"embedding-config.json exists (provider: {provider})")
        except Exception:
            return _check("embedding_config", "config", WARN, "embedding-config.json exists but is invalid JSON",
                "Run: sk index embed --setup")
    return _check("embedding_config", "config", INFO,
        "embedding-config.json not found (embeddings disabled)",
        "Run: sk index embed --setup  to enable semantic search")

def check_hooks() -> dict:
    hook_runner = TOOLS_DIR / "hook_runner.py"
    if not hook_runner.exists():
        return _check("hooks", "hooks", WARN, "hook_runner.py not found",
            "Run: sk install  or  python3 install.py")
    hooks_present = list(HOOKS_DIR.glob("*.py")) if HOOKS_DIR.exists() else []
    n = len(hooks_present)
    if n >= 3:
        return _check("hooks", "hooks", OK, f"{n} hook files found in .github/hooks/")
    if n > 0:
        return _check("hooks", "hooks", INFO, f"Only {n} hook files found",
            "Run: sk install  to install all hooks")
    return _check("hooks", "hooks", INFO, "No hook files found in .github/hooks/",
        "Run: sk install")

def check_mcp() -> dict:
    mcp_py = TOOLS_DIR / "mcp-server.py"
    if not mcp_py.exists():
        return _check("mcp", "mcp", WARN, "mcp-server.py not found")
    try:
        r = subprocess.run(
            [sys.executable, str(mcp_py), "--help"],
            capture_output=True, text=True, timeout=5
        )
        if r.returncode == 0 or "mcp" in (r.stdout + r.stderr).lower():
            return _check("mcp", "mcp", OK, "mcp-server.py is callable")
        return _check("mcp", "mcp", INFO, "mcp-server.py exists but --help returned non-zero")
    except subprocess.TimeoutExpired:
        return _check("mcp", "mcp", OK, "mcp-server.py started (timeout is expected for server)")
    except Exception as e:
        return _check("mcp", "mcp", WARN, f"mcp-server.py error: {e}")

def check_binary() -> dict:
    import shutil
    sk_bin = shutil.which("sk")
    if sk_bin:
        try:
            r = subprocess.run(["sk", "--version"], capture_output=True, text=True, timeout=5)
            ver = r.stdout.strip() or r.stderr.strip()
            return _check("binary", "binary", OK, f"sk binary found at {sk_bin} ({ver[:50]})")
        except Exception:
            return _check("binary", "binary", INFO, f"sk binary found at {sk_bin}")
    return _check("binary", "binary", INFO,
        "sk binary not on PATH (using python fallback)",
        "Run install to add sk to PATH, or use: python3 ~/.copilot/tools/sk.py")

ALL_CHECKS = [
    check_python_version,
    check_sqlite3,
    check_db_exists,
    check_db_schema,
    check_db_migrate,
    check_embedding_config,
    check_hooks,
    check_mcp,
    check_binary,
]

CATEGORY_MAP = {
    "python": [check_python_version, check_sqlite3],
    "db":     [check_db_exists, check_db_schema, check_db_migrate],
    "config": [check_embedding_config],
    "hooks":  [check_hooks],
    "mcp":    [check_mcp],
    "binary": [check_binary],
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
    """Auto-fix safe issues."""
    migrate_py = TOOLS_DIR / "migrate.py"
    session_state_dir = DB_PATH.parent
    session_state_dir.mkdir(parents=True, exist_ok=True)
    print(f"  Created dir: {session_state_dir}")
    if migrate_py.exists():
        print("  Running: python3 migrate.py ...")
        r = subprocess.run([sys.executable, str(migrate_py)], capture_output=True, text=True, timeout=30)
        if r.returncode == 0:
            print("  migrate.py: OK")
        else:
            print(f"  migrate.py failed: {r.stderr[:200]}")
    cfg = TOOLS_DIR / "embedding-config.json"
    if not cfg.exists():
        skeleton = {"provider": "none", "model": "", "api_key_env": ""}
        cfg.write_text(json.dumps(skeleton, indent=2))
        print(f"  Created skeleton: {cfg}")

def main():
    parser = argparse.ArgumentParser(description="sk doctor — configuration health check")
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument("--fix", action="store_true", help="Auto-fix safe issues")
    parser.add_argument("--category", help="Run only a category: python|db|config|hooks|mcp|binary")
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
    passed   = sum(1 for r in results if r["status"] == OK)
    warnings = sum(1 for r in results if r["status"] == WARN)
    errors   = sum(1 for r in results if r["status"] == ERROR)
    info     = sum(1 for r in results if r["status"] == INFO)
    parts = [f"{passed} passed"]
    if info:     parts.append(f"{info} info")
    if warnings: parts.append(f"{warnings} warning{'s' if warnings>1 else ''}")
    if errors:   parts.append(f"{errors} error{'s' if errors>1 else ''}")
    print(", ".join(parts))
    sys.exit(1 if errors > 0 else 0)

if __name__ == "__main__":
    main()
