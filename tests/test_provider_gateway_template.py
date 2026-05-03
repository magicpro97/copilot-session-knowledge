#!/usr/bin/env python3
"""
test_provider_gateway_template.py — Offline validation for provider gateway scaffold.
"""

import ast
import os
import sys
from pathlib import Path

if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")

PASS = 0
FAIL = 0
REPO = Path(__file__).parent.parent
TEMPLATE_DIR = REPO / "templates" / "sync-gateway-neon-railway"


def test(name: str, condition: bool, detail: str = ""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  ✅ {name}")
    else:
        FAIL += 1
        print(f"  ❌ {name}" + (f" — {detail}" if detail else ""))


def run_all_tests() -> int:
    print("=== test_provider_gateway_template.py ===")

    expected_files = [
        "app.py",
        "requirements.txt",
        ".env.example",
        "Procfile",
        "railway.json",
        "README.md",
    ]
    for rel in expected_files:
        path = TEMPLATE_DIR / rel
        test(f"Template includes {rel}", path.exists(), str(path))

    app_path = TEMPLATE_DIR / "app.py"
    app_text = app_path.read_text(encoding="utf-8") if app_path.exists() else ""

    try:
        ast.parse(app_text)
        app_parse_ok = True
    except SyntaxError as exc:
        app_parse_ok = False
        parse_detail = str(exc)
    else:
        parse_detail = ""
    test("Template app.py parses as Python", app_parse_ok, parse_detail)

    for endpoint in ["/sync/push", "/sync/pull", "/healthz"]:
        test(f"Template preserves endpoint {endpoint}", endpoint in app_text)

    test(
        "Template reads provider DB config from env",
        "SYNC_GATEWAY_DATABASE_URL" in app_text,
    )
    test("Template uses psycopg dependency", "import psycopg" in app_text)
    test(
        "Template uses autocommit reads to avoid idle transactions",
        "autocommit = True" in app_text,
    )
    test(
        "Template reconnects closed provider connections",
        "def _reconnect" in app_text and "self.conn.closed" in app_text,
    )
    test(
        "Template retries after provider OperationalError",
        "except psycopg.OperationalError" in app_text,
    )
    test(
        "Template does not default connection to manual transaction mode",
        "self.conn.autocommit = False" not in app_text,
    )
    test(
        "Template uses explicit transaction block for writes",
        "with self.conn.transaction():" in app_text,
    )
    test(
        "Template no longer uses manual commit/rollback paths",
        "self.conn.commit()" not in app_text and "self.conn.rollback()" not in app_text,
    )

    req_path = TEMPLATE_DIR / "requirements.txt"
    req_text = req_path.read_text(encoding="utf-8") if req_path.exists() else ""
    test("Template requirements pin psycopg", "psycopg" in req_text.lower())

    railway_json_path = TEMPLATE_DIR / "railway.json"
    railway_json_text = railway_json_path.read_text(encoding="utf-8") if railway_json_path.exists() else ""
    test(
        "Template railway.json enables sleepApplication for free plan",
        '"sleepApplication": true' in railway_json_text,
    )
    test(
        "Template railway.json sets a free-plan friendly region",
        '"region": "asia-southeast1-eqsg3a"' in railway_json_text,
    )

    root_req = (REPO / "requirements-dev.txt").read_text(encoding="utf-8")
    test(
        "Core requirements-dev stays provider-free",
        "psycopg" not in root_req.lower() and "asyncpg" not in root_req.lower(),
    )

    print("\n========================================")
    print(f"Results: {PASS} passed, {FAIL} failed")
    if FAIL == 0:
        print("✅ Provider gateway template tests passed!")
        return 0
    print("❌ Provider gateway template tests failed")
    return 1


if __name__ == "__main__":
    raise SystemExit(run_all_tests())
