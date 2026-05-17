#!/usr/bin/env python3
"""tentacle.py seam and re-export contract tests.

Run:
    python tests/test_tentacle_reexport.py
"""

import importlib
import os
import subprocess
import sys
from pathlib import Path

if os.name == "nt":
    for _stream in (sys.stdout, sys.stderr):
        if hasattr(_stream, "reconfigure"):
            _stream.reconfigure(encoding="utf-8", errors="replace")

PASS = 0
FAIL = 0
TOOLS_DIR = Path(__file__).resolve().parent.parent
TENTACLE_PY = TOOLS_DIR / "tentacle.py"

if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

T = importlib.import_module("tentacle")

SEAM_LABELS = (
    "coupling map / re-export contract",
    "dispatch-bundle context packet helpers",
    "worktree-verify git worktree helpers",
    "worktree-verify verification command",
    "goal-state helpers",
    "goal-state CLI sub-command implementations",
    "handoff-complete quota / rate-limit signal classification",
    "stop-event-cleanup stable CLI boundary for Rust callers",
    "pr-automation",
    "core-cli argparse boundary",
)

CONTRACT_SYMBOLS = {
    "runtime-state": (
        "file_locked",
        "find_git_root",
        "get_tentacles_dir",
        "parse_todos",
        "render_todos",
        "_write_dispatched_subagent_marker",
        "_clear_dispatched_subagent_marker",
        "_read_dispatched_subagent_marker",
    ),
    "goal-state": (
        "_goal_path",
        "_goal_lock_path",
        "_goal_load",
        "_goal_write",
        "_goal_transact",
        "_goal_budget_status",
        "_goal_gates_all_passed",
        "_goal_criteria_run_one",
        "cmd_goal",
    ),
    "handoff-complete": (
        "_parse_handoff_status",
        "_parse_handoff_changed_files",
        "_parse_rich_handoff_sections",
        "cmd_handoff",
        "cmd_complete",
    ),
    "pr-automation": (
        "_pr_collect_handoffs",
        "_pr_collect_verifications",
        "_pr_generate_commit_message",
        "_pr_generate_body",
        "cmd_pr",
    ),
    "core-cli": (
        "cmd_create",
        "cmd_todo",
        "cmd_swarm",
        "cmd_resume",
        "cmd_delete",
        "cmd_marker_cleanup",
        "main",
    ),
}

COUPLED_SURFACES = (
    "tests/test_tentacle_runtime.py",
    "hooks/session-end.py",
    "sk.py",
    "sk-rust/src/commands/fallback.rs",
    "tests/test_tentacle_pr.py",
)


def test(name: str, condition: bool, detail: str = "") -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}" + (f" - {detail}" if detail else ""))


def _source() -> str:
    return TENTACLE_PY.read_text(encoding="utf-8", errors="replace")


def test_import_contract() -> None:
    result = subprocess.run(
        [sys.executable, "-c", "import tentacle"],
        cwd=str(TOOLS_DIR),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=15,
    )
    test(
        "tentacle imports in a fresh Python process",
        result.returncode == 0,
        f"returncode={result.returncode}, stderr={result.stderr[:300]}",
    )


def test_seam_markers_present() -> None:
    content = _source()
    seam_count = content.count("SEAM:")
    test("tentacle.py has at least 4 SEAM markers", seam_count >= 4, f"count={seam_count}")
    for label in SEAM_LABELS:
        test(f"SEAM marker present: {label}", f"SEAM: {label}" in content)


def test_coupling_map_documents_callers() -> None:
    content = _source()
    test("coupling map table is present", "| Seam | Coupled callers | Symbols / behavior to preserve |" in content)
    for surface in COUPLED_SURFACES:
        test(f"coupling map names {surface}", surface in content)


def test_reexport_symbols_remain_on_tentacle_module() -> None:
    for seam, symbols in CONTRACT_SYMBOLS.items():
        for symbol in symbols:
            value = getattr(T, symbol, None)
            test(f"{seam} exports {symbol}", value is not None)
            if value is not None:
                test(f"{seam} export is callable: {symbol}", callable(value))


def test_no_extraction_modules_added_yet() -> None:
    extraction_modules = sorted(
        path.name for path in TOOLS_DIR.glob("tentacle_*.py") if path.name not in {"tentacle.py", Path(__file__).name}
    )
    test(
        "no tentacle extraction modules added in issue #272",
        not extraction_modules,
        ", ".join(extraction_modules),
    )


def main() -> int:
    print("\n-- Tentacle seam / re-export contract checks --------------------------")
    test_import_contract()
    test_seam_markers_present()
    test_coupling_map_documents_callers()
    test_reexport_symbols_remain_on_tentacle_module()
    test_no_extraction_modules_added_yet()
    print(f"\nResults: {PASS} passed, {FAIL} failed")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
