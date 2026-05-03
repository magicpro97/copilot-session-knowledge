# tests/

This directory consolidates tests that have been moved here from the repo root under **Layout A** of the test reorganisation, plus tests that were always scoped to specific subsystems (browse UI, sync, profile, etc.).

## Repo-root path convention

All tests under `tests/` are designed to be run **from the repo root**, not from inside `tests/` itself.  Each test file inserts the parent directory (repo root) into `sys.path` at startup:

```python
sys.path.insert(0, str(Path(__file__).parent.parent))
```

This means you always invoke them as:

```bash
# From ~/.copilot/tools (repo root)
python3 tests/test_visual_snapshot.py
python3 tests/test_browse.py
python3 tests/test_hooks.py
```

Running a test from inside `tests/` (e.g., `cd tests && python3 test_visual_snapshot.py`) will break the `sys.path` insertion and cause import failures.

## Canonical root canary tests (stay at root)

The following test files live at the **repo root** and are the canonical quality-gate entrypoints.  They are **not** moved here and should be run directly from the root:

| File | What it covers |
|------|---------------|
| `test_security.py` | SQL injection, pickle safety, atomic locks, path traversal |
| `test_fixes.py` | Noise filter, sub-agent, launchd, DB health, runtime checks |
| `run_all_tests.py` | Discovers and runs **all** `test_*.py` files (root + `tests/`) |

`run_all_tests.py` recursively discovers and runs every `test_*.py` in the repo, so adding a new file here is sufficient — no additional registration is required.

## What lives here

Tests are grouped by subsystem:

| Group | Files (prefix) | Covers |
|-------|---------------|--------|
| Browse legacy routes | `test_browse*.py` | Python browse server routes, API, graph, dashboard, timeline, etc. |
| Browse UI primitives | `test_ui_components.py`, `test_ui_foundation.py` | `browse.components.primitives` rendering helpers, token/layout layer |
| Visual snapshots | `test_visual_snapshot.py` | SHA-256 snapshot tests for stable browse routes |
| Session export | `test_session_export.py` | `GET /session/{id}.md` export route |
| Sync | `test_sync_*.py` | Sync capture, gateway, runtime, and status |
| Hooks | `test_hooks.py`, `test_hook_compat.py` | Hook runner rules, compat layer |
| Profile | `test_profile_*.py` | Profile builder, export, import |
| Trend Scout | `test_trend_scout*.py` | Trend Scout operations and unit checks |
| Project context | `test_project_context.py`, `test_project_hooks.py` | Project-context generation and hooks |
| Quality gates | `test_quality_gates.py` | Syntax gate, Ruff lint enforcement |
| Workflow | `test_workflow_*.py` | Workflow health checks, profile integration |
| Benchmarks | `test_benchmark.py` | Benchmark snapshot capture and comparison |
| Skill metrics | `test_skill_metrics.py` | Skill invocation metric tracking |
| Auto-update | `test_auto_update_coverage.py` | Auto-update script coverage |
| CLI healer | `test_copilot_cli_healer.py` | Copilot CLI healer self-checks |
| Retro | `test_retro.py` | Retro pipeline unit checks |
| Codebase map | `test_codebase_map.py` | Codebase map generation |

Supporting directories:

| Path | Purpose |
|------|---------|
| `fixtures/` | Static input fixtures shared across tests |
| `snapshots/` | Auto-generated SHA-256 baseline files; re-generate with `UPDATE_SNAPSHOTS=1 python3 tests/test_visual_snapshot.py` |

## Adding new tests

1. Place the new file as `tests/test_<name>.py`.
2. Add `sys.path.insert(0, str(Path(__file__).parent.parent))` near the top (after stdlib imports).
3. Run it from the repo root: `python3 tests/test_<name>.py`.
4. `run_all_tests.py` will pick it up automatically on the next full run.

> **Rule of thumb:** if the test covers a _security or runtime-regression_ concern, prefer adding it to `test_security.py` or `test_fixes.py` at the repo root so it is visible in the canonical quality-gate surface.
