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
CORE_PY = TOOLS_DIR / "_tentacle_core.py"
GOAL_PY = TOOLS_DIR / "_tentacle_goal.py"

if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

T = importlib.import_module("tentacle")
C = importlib.import_module("_tentacle_core")
G = importlib.import_module("_tentacle_goal")

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
        "_get_path_lock",
        "_retry_windows_fs",
        "_is_pid_running",
        "find_git_root",
        "_same_canonical_root",
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

CORE_EXPORTS = (
    "TOOLS_DIR",
    "LEARN_PY",
    "BRIEFING_PY",
    "CHECKPOINT_RESTORE_PY",
    "MARKERS_DIR",
    "_DISPATCHED_MARKER_PATH",
    "_MARKER_SECRET_PATH",
    "SKILL_METRICS_DB",
    "_WORKTREE_STATE_ROOT",
    "AGENT_PROFILE_REFERENCE_DIR",
    "_get_path_lock",
    "file_locked",
    "_retry_windows_fs",
    "_is_pid_running",
    "find_git_root",
    "_same_canonical_root",
    "get_tentacles_dir",
    "parse_todos",
    "render_todos",
)

GOAL_EXPORTS = (
    "GOAL_STATE_FILENAME",
    "_GOAL_LOCK_TIMEOUT_S",
    "_GOAL_LOCK_POLL_S",
    "GOAL_STATUS_ACTIVE",
    "GOAL_STATUS_PAUSED",
    "GOAL_STATUS_COMPLETED",
    "GOAL_STATUS_ABANDONED",
    "GOAL_STATUS_NEEDS_HUMAN",
    "GOAL_STATUS_AWAITING_GATE",
    "GOAL_STATUS_BUDGET_LIMITED",
    "GOAL_EVAL_DECISIONS",
    "_GOAL_TEXT_SOFT_LIMIT",
    "_GOAL_TEXT_HARD_LIMIT",
    "_GOAL_TEXT_EXTERNALIZE_HINT",
    "_goal_path",
    "_goal_lock_path",
    "_goal_lock",
    "_goal_load",
    "_goal_write",
    "_goal_transact",
    "_goal_update",
    "_goal_budget_status",
    "_goal_gates_all_passed",
    "_goal_criteria_run_one",
    "_cmd_goal_init",
    "_cmd_goal_validate",
    "_cmd_goal_status",
    "_cmd_goal_dispatch",
    "_cmd_goal_eval",
    "_cmd_goal_resume",
    "_cmd_goal_verify_loop",
    "_cmd_goal_loop",
    "_cmd_goal_resilience_status",
    "cmd_goal",
)

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
        [sys.executable, "-c", "import tentacle; import _tentacle_core"],
        cwd=str(TOOLS_DIR),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=15,
    )
    test(
        "tentacle and _tentacle_core import in a fresh Python process",
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


def test_core_symbols_reexported_from_tentacle() -> None:
    test("_tentacle_core.py exists", CORE_PY.is_file(), str(CORE_PY))
    for symbol in CORE_EXPORTS:
        core_value = getattr(C, symbol, None)
        tentacle_value = getattr(T, symbol, None)
        test(f"_tentacle_core exports {symbol}", core_value is not None)
        test(
            f"tentacle re-exports _tentacle_core.{symbol}",
            tentacle_value is core_value,
            f"tentacle={tentacle_value!r}, core={core_value!r}",
        )


def test_goal_symbols_reexported_from_tentacle() -> None:
    test("_tentacle_goal.py exists", GOAL_PY.is_file(), str(GOAL_PY))
    for symbol in GOAL_EXPORTS:
        goal_value = getattr(G, symbol, None)
        tentacle_value = getattr(T, symbol, None)
        test(f"_tentacle_goal exports {symbol}", goal_value is not None)
        test(
            f"tentacle re-exports _tentacle_goal.{symbol}",
            tentacle_value is goal_value,
            f"tentacle={tentacle_value!r}, goal={goal_value!r}",
        )


def test_core_extraction_stays_small_and_scoped() -> None:
    line_count = len(CORE_PY.read_text(encoding="utf-8", errors="replace").splitlines())
    test("_tentacle_core.py remains under 600 lines", line_count < 600, f"lines={line_count}")
    goal_line_count = len(GOAL_PY.read_text(encoding="utf-8", errors="replace").splitlines())
    test("_tentacle_goal.py remains under 4200 lines", goal_line_count < 4200, f"lines={goal_line_count}")
    extraction_modules = sorted(
        path.name for path in TOOLS_DIR.glob("tentacle_*.py") if path.name not in {"tentacle.py", Path(__file__).name}
    )
    test(
        "no unapproved tentacle_* extraction modules exist",
        not extraction_modules,
        ", ".join(extraction_modules),
    )


def main() -> int:
    print("\n-- Tentacle seam / re-export contract checks --------------------------")
    test_import_contract()
    test_seam_markers_present()
    test_coupling_map_documents_callers()
    test_reexport_symbols_remain_on_tentacle_module()
    test_core_symbols_reexported_from_tentacle()
    test_goal_symbols_reexported_from_tentacle()
    test_core_extraction_stays_small_and_scoped()
    print(f"\nResults: {PASS} passed, {FAIL} failed")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
