# copilot-session-knowledge — Agent Instructions

> Canonical root instruction surface for all AI agents (Claude Code, Codex, Amp, Factory).
>
> **Full rules:** [docs/AGENT-RULES.md](docs/AGENT-RULES.md) · **Copilot CLI runtime:** [.github/copilot-instructions.md](.github/copilot-instructions.md)
>
> **Drift-lock:** `docs/AGENT-RULES.md` is the canonical source for all agent rules. This file is a concise summary — when in doubt, defer to `docs/AGENT-RULES.md`.

## Short Command: `sk`

> **`sk` is the preferred command** — a managed launcher/shim provisioned by the installer.
> Check availability: `sk --help`. If not yet on PATH, fall back to `python3 ~/.copilot/tools/<script>.py`.
> Full fallback paths always work; use them during bootstrap or in non-interactive environments.

## Mandatory Rules

1. **Investigate before acting** — read target files with `grep`/`glob`/`view` before any edit; never modify without reading first.
2. **Briefing before complex tasks** — run `sk briefing "<task>"` for tasks touching >1 file. (fallback: `python3 ~/.copilot/tools/briefing.py "<task>"`)
3. **Test after every change** — run `python3 test_security.py AND python3 test_fixes.py` after Python edits (both required for closeout); do not mark complete until tests pass.
4. **Verify before committing** — AST-parse every modified `.py` file; run both test suites; `git diff --stat` before commit.
5. **Sub-agent model selection** — use `claude-sonnet-4.6` for code generation; `claude-opus-4.6` for security audits; never dispatch sub-agents with the default (haiku) model for code changes.
6. **No guessing** — verify table names, function signatures, and file paths from source; never assume.
7. **Docs output quality** — distinguish Facts / Interpretation / Actions / Verification evidence; never present inference as fact; every action must include the executable command.
8. **Tentacle execution obligations** — when dispatched inside a tentacle: (a) read bundle files first, (b) stay in declared scope, (c) mark todos done with `sk tentacle todo <name> done <index>`, (d) do NOT run `git commit`/`git push`, (e) write a structured handoff with explicit `--status` (`DONE`, `BLOCKED`, `TOO_BIG`, `AMBIGUOUS`, or `REGRESSED`) via `sk tentacle handoff <name> "<summary>" --status <STATUS> [--changed-file <path>] --learn` before stopping, (f) do NOT use the platform `create` file-creation tool to save research output — the `create` tool is not available in all agent runtimes (cloud agents, background tasks); write all persistent output via `sk tentacle handoff` to `handoff.md`, or print to chat as a fallback.
9. **Claims require evidence** — any claim about test status, lint, format, CI, or runtime correctness must be backed by concrete output. If you did not run a verification command, say "not proven yet — run `<command>`." A `DONE` handoff with no evidence is treated as `AMBIGUOUS`. Issue closeouts must include verification evidence per acceptance criterion.
10. **Minimum Footprint** — make the smallest complete change: no unjustified new files, no speculative abstractions, reuse existing patterns first, decompose functions over 50 lines or explain why not, flag files over 400 lines, and keep every changed line traceable to the task.
11. **New File Justification** — before adding a file, search for an existing home, state the new file's responsibility, wire it into the relevant lint/test/docs/CI surface, and add or update tests for its behavior.

**Goal-loop (orchestrators only)** — after all tentacle handoffs pass verification gates, evaluate whether the overarching goal is met. If unmet, loop back to Phase 1 (new tentacles for remaining gaps). Only commit and close when success criteria are verifiably satisfied. Sub-agents report via handoff and stop; orchestrators own continuation. Use `sk tentacle goal criteria check` to verify success criteria and `sk tentacle goal eval --decision continue|complete` to advance the loop. For automated retries with stall detection, use `sk tentacle goal verify-loop [--escalate]`; on `needs-human` escalation, fix the issues and run `sk tentacle goal resume` to continue. Record gate evidence with `sk tentacle goal gate pass <id> --reason "..."` and iteration verification with `sk tentacle verify <name> "<check-command>" --label "goal-eval"`.

**Paused-goal recovery** — when the session-end hook detects an active or awaiting-gate goal, it writes a breadcrumb to `.octogent/goal-resume-breadcrumb.json`. Both the Python (`hook_runner.py`) and native Rust (`sk hooks run sessionStart`) paths prepend a resume banner before the next session's briefing (the banner shows the stored pause-reason label; currently only session end writes the breadcrumb — `context compaction` and `quota limit` are recognized future-compatible labels, not yet active breadcrumb writers):

```
⏸  Paused goal: <goal title>  (session end | context compaction | quota limit)
▶  Run: sk tentacle goal resume
```

Recovery sequence: **(1)** `sk tentacle goal resume` — re-activates the goal; **(2)** `sk tentacle goal resilience-status` — compact health view; **(3)** see **[docs/RESILIENCE-RUNBOOK.md](docs/RESILIENCE-RUNBOOK.md)** for detailed flows (compaction, interruption, awaiting-gate, quota/rate-limit).

See [docs/AGENT-RULES.md](docs/AGENT-RULES.md) for the complete rule text, goal-loop pattern, and hook-enforcement table.

## Architecture Key Facts

- **Standalone scripts by default** — avoid inter-script imports unless a documented helper exception such as `_tentacle_core.py`, `_tentacle_goal.py`, `_tentacle_pr.py`, `_tentacle_dispatch.py`, or `_tentacle_review.py` preserves an existing contract
- **Pure stdlib Python 3.10+** — zero pip dependencies; `scikit-learn` / embedding keys are optional
- **Parameterized SQL only** — `?` placeholders; never interpolate user input into SQL
- **JSON serialization only** — never use pickle
- **Windows UTF-8 block** — every script starts with `if os.name == "nt": sys.stdout.reconfigure(encoding="utf-8")`
- **Atomic locks** — use `O_CREAT | O_EXCL` for process locks (no TOCTOU races)
- **FTS5 sanitization** — strip operators (`OR`, `AND`, `NOT`, `NEAR`, `*`, `"`) before MATCH
- **DB migrations** — add to `MIGRATIONS` list in `migrate.py` with incrementing version numbers
- **JSON field envelopes are stable contracts** — do not rename `entries[]`, `tagged_entries[]`, `related_entries[]`, `entries.<category>[]`
- **Trend Scout** — scheduled/manual only; never wire to `preToolUse`/`postToolUse` hooks
- **Sync** — local DB is authoritative; remote is transport only; `sync-config.py --setup` takes HTTP(S) URLs only
- **Hooks** — Copilot CLI only; `hook_runner.py` is the single entry point for the Python `sk.py` shim and non-binary installs; native Rust runner handles all managed events for Rust-binary installs; `pre-commit` also runs scoped Ruff + Prettier cleanliness checks (fail-open when tooling absent)
- **Tentacle marker-cleanup** — use `tentacle.py marker-cleanup [--apply]` to inspect/remove stale dispatched-subagent marker entries without completing a tentacle

**Python/Rust boundary (current state)** — do NOT claim the repo is fully Rust-only:

- **`sk watch`** (Rust binary) — native loop + indexer + extract; **never** auto-spawns Python. On DB or extract failure, emits a recovery hint naming the manual command.
- **`sk hooks run <event>`** (Rust binary) — all managed events route natively. Python `sk.py` shim routes all events through `hook_runner.py` — unchanged.
- **`sk index embed`** / **`sk sync run`** (compiled binary) — native Rust hot paths.
- **Intentional Python surfaces:** `sk.py` shim, `hook_runner.py`, `build-session-index.py`, `extract-knowledge.py`, `migrate.py`, `sync-daemon.py`, `_tentacle_core.py`, `_tentacle_goal.py`, `_tentacle_pr.py`, `_tentacle_dispatch.py`, `_tentacle_review.py`, and all operator CLI scripts remain intentional permanent tools.

> Full Python/Rust boundary table and script inventory: **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)**

## Testing

```bash
python3 test_security.py    # focused security checks
python3 test_fixes.py       # focused runtime/regression checks
python3 run_all_tests.py    # full suite
```

For `browse-ui/` changes: `cd browse-ui && pnpm typecheck && pnpm lint && pnpm format:check && pnpm test && pnpm build` (and `pnpm test:e2e` when runtime/operator surfaces change materially)

## 🛡️ Harness Engineering Principles

<!-- AI: Read this table before every task. Apply each principle as a checklist gate. -->

| # | Principle | Rule | <!-- AI: enforcement note --> |
|---|-----------|------|-------------------------------|
| 1 | **No-Ship-Bugs** | CODE→COMPILE→TEST→VERIFY→COMMIT. Never commit without passing tests. | <!-- AI: run test_security.py && test_fixes.py before any commit --> |
| 2 | **Follow-Workflow** | Clarify→Plan→Execute→Verify→Close. No skipping phases. | <!-- AI: check WORKFLOW.md or use PLAN→BUILD→TEST→VERIFY→COMMIT if absent --> |
| 3 | **Quality-Over-Speed** | Multi-platform = no shortcuts. Verify on all surfaces. | <!-- AI: run all surface gates (Python + browse-ui + Rust) before closeout --> |
| 4 | **Tentacle-Orchestration** | ≥3 files or ≥2 modules → tentacle required. | <!-- AI: count changed files; if ≥3, create tentacle before editing --> |
| 5 | **No-Abandon** | confidence < 1.0 = research loop, never BLOCKED. Fix or delegate. | <!-- AI: never write BLOCKED; create research-<topic> tentacle instead --> |
| 6 | **Rules-First** | Read AGENTS.md before every task. | <!-- AI: this table IS the rules — re-read on each new task --> |
| 7 | **Knowledge-Recording** | `sk learn` after every bug fix or new pattern. | <!-- AI: call sk learn --mistake or --pattern before task_complete --> |

> Canonical source: `templates/copilot-instructions.md § 🛡️ Harness Engineering — 7 Nguyên tắc`  
> Full rule details: [docs/AGENT-RULES.md](docs/AGENT-RULES.md)

## Shell Tool Preferences (Windows)

On Windows (PowerShell), apply these rules to reduce token consumption:

1. **Native tools first** — Use `grep`/`glob`/`view`/`lsp` instead of PowerShell equivalents
2. **Limit output** — Always add `| Select-Object -First N` or `| Select-Object -Last N`
3. **Use aliases** — `gci`, `?`, `%`, `select`, `sort`, `gc` (not full cmdlet names)
4. **No pager** — `git --no-pager`, `gh --no-pager` for all git/gh commands
5. **Chain commands** — Use `;` to combine related commands in one tool call
6. **Suppress noise** — `$ProgressPreference='SilentlyContinue'` before downloads
7. **Encoding** — Ensure `[Console]::OutputEncoding = [Text.Encoding]::UTF8` for Unicode output

> Full details: [docs/AGENT-RULES.md — Shell Tool Preferences](docs/AGENT-RULES.md#shell-tool-preferences-windows)

## Hard Boundaries

- NEVER interpolate user input into SQL strings
- NEVER use pickle for serialization
- NEVER run `git commit` or `git push` as a dispatched sub-agent
- NEVER modify files outside your declared tentacle scope without a scope escalation note in the handoff
- NEVER add a new file without a documented responsibility, existing-home search, and lint/test/docs/CI surface decision
- ALWAYS use `O_CREAT | O_EXCL` for process locks (no TOCTOU races)
- ALWAYS run `sk briefing` before starting work on unfamiliar code (fallback: `python3 ~/.copilot/tools/briefing.py`)

## Quality Checklist

> Concise runtime checklist. Canonical full version: **[docs/AGENT-RULES.md — Quality Checklist](docs/AGENT-RULES.md#quality-checklist)**.

**Preflight:** `sk briefing --auto --compact` → read target files → state dirty surfaces → dispatch reviewer for high-risk changes.

**Edit:** minimal footprint · no SQL interpolation · no pickle · Windows UTF-8 guard on new scripts · justify new files · decompose functions >50 lines.

**Verification by surface:**

| Surface | Required evidence |
|---------|-------------------|
| Python | `python3 test_security.py AND python3 test_fixes.py` (both; `run_all_tests.py` covers both) |
| Hooks/docs/skills | `python3 tests/test_quality_gates.py` |
| browse-ui | `pnpm typecheck && pnpm lint && pnpm format:check && pnpm test && pnpm build` |
| Rust | `cargo fmt --all -- --check && cargo clippy -- -D warnings && cargo test` |
| remote-terminal | `npm test && npm run lint && npm run lint:clean` |

**Closeout:** attach command output (not just assertions) · `sk learn` before `task_complete` · subagents handoff with `--status DONE --changed-file <file> --learn`.

## Hook Enforcement (Principle)

All hooks **fail-open**: a hook crash or absence never blocks the agent. Hook failures are logged; work proceeds. See [docs/AGENT-RULES.md](docs/AGENT-RULES.md) for the full enforcement table.
