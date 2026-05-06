#!/usr/bin/env python3
"""test_hosted_firebase_config.py — Firebase hosting regressions."""

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
FIREBASE_JSON = REPO / "firebase.json"

PASS = 0
FAIL = 0


def test(name: str, passed: bool, detail: str = "") -> None:
    global PASS, FAIL
    if passed:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}" + (f" — {detail}" if detail else ""))


def main() -> int:
    config = json.loads(FIREBASE_JSON.read_text(encoding="utf-8"))
    hosting = config.get("hosting")
    if isinstance(hosting, list):
        agents = next((item for item in hosting if item.get("target") == "agents"), {})
    elif isinstance(hosting, dict):
        agents = hosting
    else:
        agents = {}

    rewrites = agents.get("rewrites", [])
    session_rewrite = next(
        (
            item
            for item in rewrites
            if item.get("regex") == "/sessions/[^/]+(/.*)?"
            and item.get("destination") == "/sessions/_placeholder/index.html"
        ),
        None,
    )

    test("firebase config has agents hosting target", bool(agents))
    test(
        "firebase rewrites session detail deep links to placeholder shell",
        session_rewrite is not None,
        f"rewrites={rewrites!r}",
    )
    test(
        "firebase session rewrite does not catch /sessions/ list route",
        session_rewrite is not None and session_rewrite.get("regex") != "/sessions(/.*)?",
    )

    print(f"\nResults: {PASS} passed, {FAIL} failed")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
