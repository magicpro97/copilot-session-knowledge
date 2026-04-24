#!/usr/bin/env python3
"""
test_diff_viewer.py — Tests for checkpoint-diff.py --pager and --color flags (Batch D)

Tests the _resolve_pager() function and _PAGER_ALLOWLIST in isolation,
without spawning an actual pager process.
"""

import importlib.util
import os
import sys
from pathlib import Path

if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")

# Load checkpoint-diff.py via importlib (filename has a hyphen)
_spec = importlib.util.spec_from_file_location(
    "checkpoint_diff",
    Path(__file__).parent / "checkpoint-diff.py",
)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

_resolve_pager = _mod._resolve_pager
_PAGER_ALLOWLIST = _mod._PAGER_ALLOWLIST

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


def _with_env(key: str, value: str | None):
    """Context manager that sets/unsets an env var and restores original."""
    class _Ctx:
        def __enter__(self):
            self._orig = os.environ.get(key)
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        def __exit__(self, *_):
            if self._orig is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = self._orig
    return _Ctx()


def run_all_tests() -> int:
    print("=== test_diff_viewer.py ===")

    # ── D1: CHECKPOINT_DIFF_PAGER=less -R is accepted ─────────────────────────
    print("\n-- D1: less -R from env")
    with _with_env("CHECKPOINT_DIFF_PAGER", "less -R"):
        args = _resolve_pager()
        test("D1: 'less -R' env → accepted", args[0] == "less")
        test("D1: args contains -R", "-R" in args)

    # ── D2: Malicious pager is rejected ───────────────────────────────────────
    print("\n-- D2: malicious pager rejected")
    with _with_env("CHECKPOINT_DIFF_PAGER", "rm -rf /"):
        raised = False
        try:
            _resolve_pager()
        except SystemExit:
            raised = True
        test("D2: 'rm' → SystemExit", raised)

    with _with_env("CHECKPOINT_DIFF_PAGER", "bash -c 'id'"):
        raised = False
        try:
            _resolve_pager()
        except SystemExit:
            raised = True
        test("D2b: 'bash' → SystemExit", raised)

    with _with_env("CHECKPOINT_DIFF_PAGER", "python -c \"import os;os.system('id')\""):
        raised = False
        try:
            _resolve_pager()
        except SystemExit:
            raised = True
        test("D2c: 'python' → SystemExit", raised)

    # ── D3: Default (no env var) is less -R ────────────────────────────────────
    print("\n-- D3: default pager")
    with _with_env("CHECKPOINT_DIFF_PAGER", None):
        args = _resolve_pager()
        test("D3: default pager is 'less'", args[0] == "less")
        test("D3: default pager has -R", "-R" in args)

    # ── D4: 'more' is allowed ─────────────────────────────────────────────────
    print("\n-- D4: more pager")
    with _with_env("CHECKPOINT_DIFF_PAGER", "more"):
        args = _resolve_pager()
        test("D4: 'more' → accepted", args[0] == "more")

    # ── D5: 'most' is allowed ─────────────────────────────────────────────────
    print("\n-- D5: most pager")
    with _with_env("CHECKPOINT_DIFF_PAGER", "most"):
        args = _resolve_pager()
        test("D5: 'most' → accepted", args[0] == "most")

    # ── D6: Empty pager string is rejected ────────────────────────────────────
    print("\n-- D6: empty pager")
    with _with_env("CHECKPOINT_DIFF_PAGER", "   "):
        raised = False
        try:
            _resolve_pager()
        except SystemExit:
            raised = True
        test("D6: whitespace-only → SystemExit", raised)

    # ── D7: Allowlist contents ─────────────────────────────────────────────────
    print("\n-- D7: allowlist contents")
    test("D7: allowlist contains 'less'", "less" in _PAGER_ALLOWLIST)
    test("D7: allowlist contains 'more'", "more" in _PAGER_ALLOWLIST)
    test("D7: allowlist contains 'most'", "most" in _PAGER_ALLOWLIST)
    test("D7: allowlist excludes 'bash'", "bash" not in _PAGER_ALLOWLIST)
    test("D7: allowlist excludes 'rm'", "rm" not in _PAGER_ALLOWLIST)
    test("D7: allowlist excludes 'python'", "python" not in _PAGER_ALLOWLIST)
    test("D7: allowlist excludes 'sh'", "sh" not in _PAGER_ALLOWLIST)

    # ── D8: Full path to allowed pager is accepted ────────────────────────────
    print("\n-- D8: full path to allowed pager")
    with _with_env("CHECKPOINT_DIFF_PAGER", "/usr/bin/less -R"):
        args = _resolve_pager()
        test("D8: /usr/bin/less → accepted (basename=less)", args[0] == "/usr/bin/less")

    # ── D9: no shell=True dependency ──────────────────────────────────────────
    print("\n-- D9: no shell=True in module source")
    src = Path(__file__).parent / "checkpoint-diff.py"
    text = src.read_text(encoding="utf-8")
    # Check that subprocess.run is never called with shell=True
    import re
    shell_true = re.search(r'subprocess\.run\([^)]*shell\s*=\s*True', text)
    test("D9: no subprocess.run(..., shell=True)", shell_true is None)

    print(f"\n{'='*50}")
    print(f"Results: {_PASS} passed, {_FAIL} failed")
    return _FAIL


if __name__ == "__main__":
    sys.exit(run_all_tests())
