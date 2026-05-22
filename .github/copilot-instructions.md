# Copilot Instructions — copilot-session-knowledge

## Short Command: `sk`

> **`sk` is the preferred command** — a managed launcher/shim provisioned by the installer.
> Check: `sk --help`. Not on PATH yet? Fall back to `python3 ~/.copilot/tools/<script>.py`.

## Agent Rules (MANDATORY)

> **⚠️ These rules are NON-NEGOTIABLE.** Every agent (main, sub-agent, explore, task, general-purpose) MUST follow them. Violations = shipping broken code.

### 1. Investigate Before Acting

**NEVER modify code without reading it first.** Before any edit:

1. Use `grep`/`glob`/`view`/`lsp` tools to read the target file(s)
2. Understand the existing logic, dependencies, and callers
3. Check related files that may be affected by your change
4. Only then make your edit

```
❌ BAD:  User says "fix the search" → immediately edit query-session.py
✅ GOOD: User says "fix the search" → grep for search functions → view the code → check callers → edit
```

### 2. Briefing Before Complex Tasks

Before starting any task that touches >1 file or involves unfamiliar code:

```bash
sk briefing "your task description"
# fallback: python3 ~/.copilot/tools/briefing.py "your task description"
```

This surfaces past mistakes, proven patterns, and relevant decisions. Skip only for trivial changes (typo fix, renaming, formatting).

### 3. Test After Every Change

After modifying any Python file, run the relevant tests:

```bash
python3 test_security.py AND python3 test_fixes.py
# test_security.py: required when touching embed.py, sync-knowledge.py, watch-sessions.py, learn.py
# test_fixes.py:    required when touching any script
```

**Both suites are required for closeout.** The verification-gate ledger tracks `py_security` and `py_fixes` as separate evidence keys; both must succeed before `task_complete`, `DONE` handoff, or issue close is permitted.

Do NOT mark a task complete until the relevant tests pass. If you encounter a baseline failure, separate pre-existing breakage from regressions you introduced before proceeding.

### 4. Verify Before Committing

Before `git commit`:
1. `python3 -c "import ast; ast.parse(open('file.py').read())"` for every modified `.py` file
2. Run test suite (rule 3)
3. `git diff --stat` to review what you're about to commit

### 5. Sub-Agent Model Selection

When dispatching sub-agents via the `task` tool:

| Task type | Minimum model | Example |
|-----------|--------------|---------|
| Code generation | `claude-sonnet-4.6` | Writing/modifying Python scripts |
| Code review | `claude-sonnet-4.6` | Reviewing changes for bugs |
| Security audit | `claude-opus-4.6` | Auth, data handling, injection risks |
| Exploration | `claude-haiku-4.5` (default OK) | Finding files, reading code |
| Documentation | `claude-sonnet-4` or `haiku` | Writing docs, README |

```
❌ task(agent_type="general-purpose", prompt="fix the search bug...")  # default haiku!
✅ task(agent_type="general-purpose", model="claude-sonnet-4.6", prompt="fix the search bug...")
```

### 6. No Guessing

- Don't assume table names — check with `sqlite3 ... ".tables"` or read `migrate.py`
- Don't assume function signatures — use `grep` or `lsp` to verify
- Don't assume file paths — use `glob` to find them
- If unsure about behavior, write a small test or read the source

### 7. Docs Output Quality

Agent-authored docs, tentacle handoffs, operator reports, and research outputs must distinguish four layers. Mixing layers silently or presenting interpretation as fact is a documentation defect.

| Layer | What it contains | Marking convention |
|-------|-----------------|-------------------|
| **Facts** | Verified, reproducible data: row counts, timestamps, test results, git refs | State directly; cite the source or command that produced it |
| **Interpretation** | Reasoning based on facts: patterns, risks, root causes, inferences | Qualify explicitly: "suggests", "indicates", "likely" |
| **Actions** | Concrete next steps: commands to run, tickets to file, follow-up tentacles | Use imperative; include the executable command |
| **Verification evidence** | Proof that work was done: test log output, CI status, measured diffs | Link or inline the evidence; do not claim verified without it |

**Rules:**
1. Do not present interpretation as fact. Every non-trivial causal claim must be qualified.
2. Every action item must be executable — include the actual command or URL.
3. Every verification claim must include evidence (test log excerpt, CI link, git ref, or pass/fail count).
4. Keep operator/research docs concise. Move lengthy context into appendices or collapsible sections.
5. Operator/research outputs (tentacle handoffs, retro summaries, knowledge-health reports) must follow all four layers. Contributor docs keep their existing concise tone.

### 8. Tentacle Execution Obligations

When running inside a tentacle (dispatched by the orchestrator via `tentacle.py`):

1. **Read the bundle first** — read `manifest.json`, `session-metadata.md`, `recall-pack.json`, and `instructions.md` from the bundle path before any edit.
2. **Stay in scope** — only edit files listed in the tentacle's declared scope; write a scope escalation note to the handoff for any exception.
3. **Mark todos as you complete them**: `sk tentacle todo <tentacle-name> done <index>` (fallback: `python3 ~/.copilot/tools/tentacle.py todo <tentacle-name> done <index>`)
4. **No git operations** — do NOT run `git commit` or `git push`; the orchestrator owns all git operations.
5. **Write a structured handoff before stopping**: `sk tentacle handoff <tentacle-name> "<summary>" --status <STATUS> [--changed-file <path>] --learn` (fallback: `python3 ~/.copilot/tools/tentacle.py handoff ...`)
6. Use one of `DONE`, `BLOCKED`, `TOO_BIG`, `AMBIGUOUS`, or `REGRESSED` for `<STATUS>`. Add one `--changed-file` per modified file; omit it when no files changed. Handoff must list changed rules, source-of-truth file for each rule, and any remaining ambiguity.
7. **No platform `create` for reports** — do NOT use the runtime platform's `create` file-creation tool to save research output, investigation findings, or final reports. The `create` tool is a platform capability that is **not available in all agent runtimes** (cloud agents, Copilot cloud runs, background tasks). Write all persistent output to `handoff.md` via `tentacle.py handoff`. If `tentacle.py` is also unavailable, print the report to chat so the orchestrator can capture it. Orchestrators must not assume sub-agents can create arbitrary files.

**Goal-loop (orchestrators only)** — after all tentacle handoffs pass verification gates, evaluate whether the overarching goal is met. If unmet, loop back to Phase 1 (new tentacles for remaining gaps). Only commit and close when success criteria are verifiably satisfied. Sub-agents report via handoff and stop; the orchestrator owns continuation. Use `sk tentacle goal criteria check` to verify success criteria and `sk tentacle goal eval --decision continue|complete` to advance the loop. For automated retries with stall detection, use `sk tentacle goal verify-loop [--escalate]`; on `needs-human` escalation, fix the issues and run `sk tentacle goal resume` to continue. Record gate evidence with `sk tentacle goal gate pass <id> --reason "..."` and iteration verification with `sk tentacle verify <name> "<check-command>" --label "goal-eval"` (fallback: `python3 ~/.copilot/tools/tentacle.py verify ...`).

**Session-start paused-goal banner** — when the session-end hook detects an active or awaiting-gate goal, it writes a breadcrumb to `.octogent/goal-resume-breadcrumb.json`; both the Python (`hook_runner.py`) and native Rust (`sk hooks run sessionStart`) paths then prepend a resume banner before the next session's briefing (the banner shows the stored pause-reason label; currently only session end writes the breadcrumb — `context compaction` and `quota limit` are recognized future-compatible labels, not yet active breadcrumb writers). Recovery sequence: **(1)** `sk tentacle goal resume` — re-activates the goal; **(2)** `sk tentacle goal resilience-status` — compact health view; **(3)** see **[docs/RESILIENCE-RUNBOOK.md](../docs/RESILIENCE-RUNBOOK.md)** for detailed flows.

See [docs/AGENT-RULES.md](../docs/AGENT-RULES.md) for the complete Rule 8 text and goal-loop pattern. Goal-eval: `sk tentacle verify <name> "<check-command>" --label "goal-eval"` (fallback: `python3 ~/.copilot/tools/tentacle.py verify ...`).

### 9. Claims Require Evidence

Any claim about test status, lint, format, CI, or runtime correctness must be backed by concrete, reproducible output. Asserting something works without running it is a documentation defect.

| Claim | Required evidence |
|-------|------------------|
| "Tool / feature works" | Command output or test log showing runtime execution |
| "Tests pass" | Test runner output with pass/fail counts |
| "Format / lint clean" | Actual linter/formatter command output |
| "CI is green" | CI run URL or copy of passing job output |
| "Build succeeds" | Compiler or build tool output confirming exit code 0 |

If you did not run a verification command, say so: "not proven yet — run `<command>`." A `DONE` handoff with no evidence for its claims is treated as `AMBIGUOUS` by the orchestrator. Issue closeouts must include verification evidence per acceptance criterion or explicitly list unproven items.

### 10. Minimum Footprint

Make the smallest complete change that satisfies the task. Every changed line should trace to the request or to verification needed for the request.

**Rules:**

1. Do not create a new file without a clear justification that an existing file is not the right home.
2. Do not add speculative abstractions, configuration, extension points, or general-purpose helpers for a single current use case.
3. Reuse existing patterns, helpers, commands, and test harnesses before introducing new ones.
4. If a changed or newly added function grows beyond 50 lines, decompose it or explain why keeping it together is safer.
5. If a changed file grows beyond 400 lines, flag it in the PR/issue with the reason it remains acceptable or the follow-up needed to split it.
6. Avoid formatting churn, opportunistic cleanup, or adjacent refactors that are not required by the task.

### 11. New File Justification

New files are durable maintenance surface. Before adding one:

1. Search for an existing home first (`glob`, `rg`, LSP, or the relevant project registry) and reuse it when it can own the behavior cleanly.
2. State the new file's responsibility in the issue, PR, step file, or handoff.
3. Wire the file into the relevant lint, test, hook, docs, packaging, or CI surface, or explicitly justify why no surface applies.
4. Add or update tests for the behavior the new file owns, or document the exact verification command when tests are not applicable.
5. Avoid duplicate entry points; consolidate with existing scripts, hooks, routes, skills, or modules unless separation is justified.

> **Drift-lock:** `docs/AGENT-RULES.md` is the canonical source for all agent rules. This file (`copilot-instructions.md`) is the Copilot CLI runtime enforcement surface — keep it in sync with `docs/AGENT-RULES.md`. Changes to agent rules should be reflected in both places.

## Hook Enforcement (Summary)

Hooks **fail-open**: if a hook crashes or is unavailable, the guarded operation proceeds. Hook failures are logged but do not interrupt work.

| Rule enforced | Hook | What it does |
|--------------|------|--------------|
| Briefing before edits | `enforce-briefing` | Blocks `edit`/`create`/`bash` until briefing marker is present |
| Learn after code edits | `enforce-learn` | Blocks `git commit`/`task_complete` after ≥3 edits without `learn.py` |
| Tentacle for broad changes | `tentacle-enforce` | Blocks edits across ≥3 files / ≥2 modules without tentacle setup |
| No git ops in sub-agents | `subagent-git-guard` | Blocks `git commit`/`git push` while dispatched-subagent marker is active |
| Syntax errors | `syntax-gate` | Blocks `.py` edit/create payloads that fail `py_compile` |
| Evidence for closeout claims (Rule 9) | `verification-gate` | Tracks dirty Python / browse-ui surfaces, records fresh test / format / lint / typecheck / build evidence, and blocks `task_complete`, `gh issue close/comment`, and tentacle `DONE` / `complete` actions when that evidence is missing. CI/runtime proof beyond those gates remains policy-level. |
| Minimum footprint (Rule 10) | `file-size-advisory` | Warns on large Python create/edit payloads so agents can decompose or justify oversized changes before they land. |
| New file justification (Rule 11) | `new-file-advisory` | Warns on new root-level Python files and points agents back to the search/reuse/test-surface checklist. |

> Full hook inventory: **[docs/AGENT-RULES.md](../docs/AGENT-RULES.md)** · **[docs/HOOKS.md](../docs/HOOKS.md)**

## Quality Checklist

> Concise runtime checklist. Canonical full version: **[docs/AGENT-RULES.md — Quality Checklist](../docs/AGENT-RULES.md#quality-checklist)**.

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

## Testing

```bash
python3 test_security.py    # 11 security tests (SQL injection, pickle, locks, paths)
python3 test_fixes.py       # 137 tests (noise filter, sub-agent, launchd, DB health)
# Both required for closeout. sk has no shortcut for project-local test scripts — use python3 directly
```

Python validation runs through `run_all_tests.py`, but individual files use a mix of the custom `test()` helper and `unittest`/`test_*` style. For `browse-ui/` or CI changes, also run the relevant `pnpm` gates (`typecheck`, `lint`, `format:check`, `test`, `build`, and `test:e2e` when intentionally validating that surface). Keep GitHub Actions CI green.

## Architecture & Conventions

> Canonical reference: **[docs/ARCHITECTURE.md](../docs/ARCHITECTURE.md)**

Key facts every agent must remember:

- **Standalone scripts by default** — avoid inter-script imports unless a documented helper exception such as `_tentacle_core.py`, `_tentacle_goal.py`, `_tentacle_pr.py`, `_tentacle_dispatch.py`, or `_tentacle_review.py` preserves an existing contract
- **Pure stdlib Python 3.10+** — zero pip dependencies; `scikit-learn` / embedding keys are optional
- **Parameterized SQL only** — use `?` placeholders; never interpolate user input into SQL
- **JSON serialization only** — never use pickle; new code uses JSON / `struct.pack`
- **Windows UTF-8 block** — every script starts with `if os.name == "nt": sys.stdout.reconfigure(encoding="utf-8")`
- **Atomic locks** — use `O_CREAT | O_EXCL` for process locks (no TOCTOU races)
- **FTS5 sanitization** — strip operators (`OR`, `AND`, `NOT`, `NEAR`, `*`, `"`) before MATCH
- **DB migrations** — add to `MIGRATIONS` list in `migrate.py` with incrementing version numbers
- **JSON field envelopes are stable contracts** — `entries[]`, `tagged_entries[]`, `related_entries[]`, `entries.<category>[]` — do not rename
- **Trend Scout** — scheduled/manual only; never wire to `preToolUse`/`postToolUse` hooks
- **Sync** — local DB is authoritative; remote is transport only; `sync-config.py --setup` takes HTTP(S) URLs only
- **Hooks** — Copilot CLI only; `hook_runner.py` is the single entry point for the Python `sk.py` shim and non-binary installs; native Rust runner handles all managed events for Rust-binary installs; `pre-commit` also runs scoped Ruff + Prettier cleanliness checks (fail-open when tooling absent)
- **Tentacle marker-cleanup** — use `tentacle.py marker-cleanup [--apply]` to inspect/remove stale dispatched-subagent marker entries without completing a tentacle

**Python/Rust boundary (current state)** — do NOT claim the repo is fully Rust-only:

- **`sk watch`** (Rust binary) — native loop + native indexer + native extract; **never** auto-spawns Python. On DB open/create or extract failure, emits a structured recovery hint naming the manual command.
- **`sk hooks run <event>`** (Rust binary) — all managed events route natively (`sessionStart`, `sessionEnd`, `preToolUse`, `postToolUse`, `agentStop`, `subagentStop`, `errorOccurred`). Python `sk.py` shim always delegates to `hook_runner.py` — shim behavior unchanged.
- **`sk index embed`** — native (`native-embed` default Cargo feature); `embed.py` is the fallback when the feature is unavailable.
- **`sk sync run`** (compiled binary) — native Rust daemon loop, push, pull, FTS refresh (`native-sync` default Cargo feature). Python `sk.py` shim → `sync-daemon.py`.
- **Intentional Python surfaces (not removed):** `sk.py` shim, `hook_runner.py`, `build-session-index.py`, `extract-knowledge.py`, `migrate.py`, `sync-daemon.py`, `_tentacle_core.py`, `_tentacle_goal.py`, `_tentacle_pr.py`, `_tentacle_dispatch.py`, `_tentacle_review.py`, and all operator/admin CLI scripts remain on disk as intentional tools.

> Full Python/Rust boundary table and script inventory: **[docs/ARCHITECTURE.md](../docs/ARCHITECTURE.md)**
