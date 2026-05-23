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
| Chat resume / CLI adoption | `test_browse_chat_resume.py` | Mock-Copilot discover/adopt/confirm/prompt/stream proof (CR1-CR14); covers argv/env isolation, confirmation gate, CLI tree immutability, and two-ID model correctness. |
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

## Test shard inventory

Use `python3 tests/test_inventory.py --report` to regenerate the full inventory of every `test_*.py` file, including subsystem, line count, runtime class, and protected surface. The default `python3 tests/test_inventory.py` mode verifies that the inventory covers all discovered tests and that this section stays present.

Current top 30 by line count:

| File | Subsystem | Lines | Runtime class | Protected surface |
| --- | --- | ---: | --- | --- |
| `tests/test_tentacle_runtime.py` | tentacle | 9854 | oversized | tentacle orchestration |
| `tests/test_tentacle_goal.py` | tentacle | 7509 | oversized | tentacle orchestration |
| `tests/test_hooks.py` | hooks | 5667 | oversized | Copilot hook rules |
| `tests/test_memory_contract.py` | memory | 3704 | oversized | memory |
| `tests/test_trend_scout.py` | trend-scout | 3554 | oversized | trend scout |
| `test_fixes.py` | root-runtime | 3410 | root-canary | runtime regression gate |
| `tests/test_sync_runtime.py` | sync | 3037 | oversized | sync runtime |
| `tests/test_retro.py` | retro | 2582 | large | retro |
| `tests/test_hook_compat.py` | hooks | 2544 | large | Copilot hook rules |
| `tests/test_browse_operator_api.py` | browse | 2443 | large | browse backend/UI |
| `tests/test_hook_rules_more.py` | hooks | 2183 | large | Copilot hook rules |
| `tests/test_quality_gates.py` | quality | 1760 | large | quality |
| `tests/test_dream_score.py` | dream | 1741 | large | dream |
| `tests/test_token_read_tracker.py` | token | 1739 | large | token |
| `tests/test_indexing.py` | indexing | 1687 | large | indexing |
| `tests/test_session_surface.py` | session | 1661 | large | session |
| `tests/test_browse_api.py` | browse | 1522 | large | browse backend/UI |
| `tests/test_sk_cli.py` | sk | 1499 | large | sk |
| `tests/test_briefing.py` | briefing | 1318 | large | briefing |
| `tests/test_hook_rules_full.py` | hooks | 1283 | large | Copilot hook rules |
| `tests/test_validate_skill.py` | validate | 1182 | large | validate |
| `tests/test_retrieval_evals.py` | retrieval | 1150 | large | knowledge retrieval |
| `tests/test_install_helpers.py` | install | 1116 | large | install |
| `tests/test_karpathy_skill_rollout.py` | karpathy | 1099 | large | karpathy |
| `tests/test_knowledge_health.py` | knowledge | 1065 | large | knowledge |
| `tests/test_binary_install.py` | binary | 990 | standard | binary |
| `tests/test_benchmark.py` | benchmark | 965 | standard | benchmark |
| `tests/test_hook_runner_entrypoints.py` | hooks | 957 | standard | Copilot hook rules |
| `tests/test_skill_curator.py` | skills | 920 | standard | skills ecosystem |
| `tests/test_browse_pairing.py` | browse | 855 | standard | browse backend/UI |

### Size policy for new tests

- Root canary files (`test_security.py`, `test_fixes.py`) stay thin entrypoints for high-signal security and runtime regressions. Add focused helpers under `tests/` when a root canary starts accumulating subsystem-specific setup.
- New subsystem tests should prefer shards below 1,000 lines. Files over 1,000 lines need a short reason in this README or a nearby module comment explaining why the density is intentional.
- Files over 3,000 lines are oversized. Do not add unrelated cases to them unless the change is a narrow regression for behavior already owned by that file.
- A new shard must be directly runnable from the repo root with `python3 tests/test_<name>.py` and must also be discovered by `python3 run_all_tests.py --dry`.
- Dense tests are allowed when the setup fixture is expensive or tightly coupled, but the exception should name the protected surface and the future extraction seam.

### First safe shard extraction proposal

The first no-behavior-change extraction should target `tests/test_tentacle_runtime.py`, the largest file. Proposed seam:

1. Move pure bundle/manifest/context rendering assertions that do not mutate goal state into `tests/test_tentacle_runtime_bundle.py`.
2. Keep shared subprocess helpers and scratch-directory setup byte-for-byte equivalent while both files run independently from the repo root.
3. Prove the split with `python3 tests/test_tentacle_runtime.py`, `python3 tests/test_tentacle_runtime_bundle.py`, and `python3 run_all_tests.py`.

## Adding new tests

1. Place the new file as `tests/test_<name>.py`.
2. Add `sys.path.insert(0, str(Path(__file__).parent.parent))` near the top (after stdlib imports).
3. Run it from the repo root: `python3 tests/test_<name>.py`.
4. `run_all_tests.py` will pick it up automatically on the next full run.

> **Rule of thumb:** if the test covers a _security or runtime-regression_ concern, prefer adding it to `test_security.py` or `test_fixes.py` at the repo root so it is visible in the canonical quality-gate surface.
