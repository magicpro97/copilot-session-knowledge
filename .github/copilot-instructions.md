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
python3 test_security.py    # If touching: embed.py, sync-knowledge.py, watch-sessions.py, learn.py
python3 test_fixes.py       # If touching: any script
```

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

**Goal-loop (orchestrators only)** — after all tentacle handoffs pass verification gates, evaluate whether the overarching goal is met. If unmet, loop back to Phase 1 (new tentacles for remaining gaps). Only commit and close when success criteria are verifiably satisfied. Sub-agents report via handoff and stop; the orchestrator owns continuation. Record goal-eval evidence with `python3 ~/.copilot/tools/tentacle.py verify <name> "<check-command>" --label "goal-eval"`.

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

> Full hook inventory: **[docs/AGENT-RULES.md](../docs/AGENT-RULES.md)** · **[docs/HOOKS.md](../docs/HOOKS.md)**

## Testing

```bash
python3 test_security.py    # 11 security tests (SQL injection, pickle, locks, paths)
python3 test_fixes.py       # 137 tests (noise filter, sub-agent, launchd, DB health)
# sk has no shortcut for project-local test scripts — use python3 directly
```

Python validation runs through `run_all_tests.py`, but individual files use a mix of the custom `test()` helper and `unittest`/`test_*` style. For `browse-ui/` or CI changes, also run the relevant `pnpm` gates (`typecheck`, `lint`, `format:check`, `test`, `build`, and `test:e2e` when intentionally validating that surface). Keep GitHub Actions CI green.

## Architecture & Conventions

> Canonical reference: **[docs/ARCHITECTURE.md](../docs/ARCHITECTURE.md)**

Key facts every agent must remember:

- **Standalone scripts** — no inter-script imports; each script is self-contained
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
- **Hooks** — Copilot CLI only; `hook_runner.py` is the single entry point for the Python `sk.py` shim and non-binary installs; native Rust runner handles all events for Rust-binary installs (wave13: `preToolUse` added to `NATIVE_EVENTS`); `pre-commit` also runs scoped Ruff + Prettier cleanliness checks (fail-open when tooling absent)
- **Tentacle marker-cleanup** — use `tentacle.py marker-cleanup [--apply]` to inspect/remove stale dispatched-subagent marker entries without completing a tentacle

**Wave13–20 hybrid state** — do NOT overclaim native coverage:

| Surface | Native (Rust) | Python-backed (must not remove) | Notes |
|---------|--------------|--------------------------------|-------|
| `sk hooks run agentStop\|subagentStop` | ✅ Routes natively; uses `tentacle.py marker-cleanup --from-stop-event` | HMAC, enforce-briefing, tentacle-enforce, enforce-learn (via `hook_runner.py`) | Wave3 closed the stop-event gap |
| `sk hooks run sessionEnd` | ✅ Routes natively; `SessionEndRule` — per-session marker cleanup + `session.log` write; `RecurrenceDetectorRule` — increments `recurrence_after_briefing` in `knowledge.db` (wave9) | Full session-end Python rule parity not ported | Wave4 + wave9 |
| `sk hooks run errorOccurred` | ✅ Routes natively; `ErrorOccurredRule` → native Rust FTS5 DB query as primary; `query-session.py` subprocess only if DB unavailable | `query-session.py` is still Python; normal path no longer spawns subprocess | Wave5 upgrade (direct DB path) |
| `sk hooks run sessionStart` | ✅ Routes natively (wave9); `AutoBriefingRule` — spawns `briefing.py` with 10s timeout + signs HMAC `briefing-done`/`codebase-map-ran` markers; `IntegrityRule` — verifies/refreshes SHA256 hook-file manifest | `hook_runner.py` is still the Python fallback for non-Rust installs and the Python `sk.py` shim; Python `hook_runner.py` sessionStart path intact | Wave9 routing flip for sessionStart only |
| `sk hooks run postToolUse` | ✅ **Routes natively (wave10)**; all seven postToolUse rules fully ported (`TrackEditsRule`, `LearnReminderRule`, `TestReminderRule`, `NextjsTypecheckReminderRule`, `VerificationGatePostRule`, `ReadBeforeEditRule`, `TentacleSuggestRule`); `sync_markers.rs` writes `sync-nudge.json` | No Python-backed surfaces remain for managed `postToolUse` — native runner is sole writer for postToolUse markers | Wave10 routing flip; no HMAC enforcement rules for postToolUse; dual-writer concern resolved |
| `sk hooks run preToolUse` | ✅ **Routes natively for Rust-binary installs (wave13)**; all deny-capable preToolUse rules active including `SyntaxGateRule` (via `python_exe()` + `py_compile` subprocess; fail-open; registered between `SubagentGitGuardRule` and `BlockEditDistRule`). Direct+managed Rust paths: `subagent-git-guard`, `block-edit-dist`, `block-unsafe-html`, `pnpm-lockfile-guard` (wave7), `read-before-edit` warn (wave7), `VerificationGatePreRule` dirty-mark+informational deny (wave8), **`EnforceBriefingRule` deny-capable (wave11)**, **`EnforceLearnRule` deny-capable (wave11)**, **`TentacleEnforceRule` deny-capable (wave12)**, **`SyntaxGateRule` (wave13)** | **Python `sk.py` shim boundary unchanged**: shim still routes `sk hooks run preToolUse` through `hook_runner.py`; `hooks/rules/syntax_gate.py` and `hook_runner.py` NOT removed — necessary for shim and non-binary installs. Windows proof accepted (wave13); WSL/Linux/macOS not separately re-proved | Wave13 routing flip for Rust-binary installs; shim unchanged |
| HMAC marker auth | `sk-rust/src/hooks/marker_auth.rs` — foundation + partial wiring; wave9 adds sessionStart HMAC writes via `AutoBriefingRule` | Full managed enforcement parity still uses Python `marker_auth.py` | Wave6 wires git-guard + TrackEdits writes; wave9 adds sessionStart marker signs |
| `sk watch` | Lock/poll loop + Copilot session-state indexer + Claude JSONL indexing + sessions-table column migrations + sync-op enqueueing (fail-open) + local-only `sessions_fts` writer + **`knowledge_entries`/`ke_fts` hot-path writer (`native-extract` is now a default Cargo feature since wave15; sync-op enqueue parity also landed; error lifecycle metadata `error_type`/`root_cause`/`severity` filled natively for mistake entries; integration proof: `sk-rust/tests/integration_test.rs`)** + **native deterministic relations: `SAME_SESSION`, `SAME_TOPIC`, `TAG_OVERLAP`, `RESOLVED_BY` (wave16)** + **native residual helpers: `backfill_affected_files`, `infer_task_ids`, confidence decay (wave17)** + **native first-run DB bootstrap: `open_or_create_index_db` / `ensure_extract_tables` in `session.rs`/`claude.rs`/`extract.rs` — missing `knowledge.db` no longer triggers Python bootstrap (wave18)** + **native SEMANTIC_PROXIMITY: computed via TF-IDF cosine in `watch.rs`/`sk-rust/src/embeddings/tfidf.rs` (wave19) — no Python auto-spawn on the successful native watch path** | **Wave20:** `watch.rs` never spawns Python on any path — including error paths. On genuine DB open/create or extract failure, `watch` emits a structured recovery hint naming the exact manual command (`python build-session-index.py --incremental` or `python extract-knowledge.py`). **Intentional Python surfaces (not removed):** Python `sk.py` shim/no-binary paths remain fully Python-backed; `hook_runner.py` is the Python runner for shim and non-binary installs; `build-session-index.py`, `extract-knowledge.py` (including `--semantic-only`), and `migrate.py` are intentional manual operator tools named in recovery hints. | Wave6 closes `sessions_fts` gap; wave10 removes `build-session-index.py --incremental` for existing-DB non-JSONL Copilot changes; wave15 moves `native-extract` to default feature; wave16 native relation slice; wave17 native residual helpers + sklearn-gated Python spawn; wave18 native first-run DB bootstrap; wave19 native SEMANTIC_PROXIMITY — successful native path zero-Python; **wave20 removes all Python auto-spawns including error paths — recovery hints replace subprocess fallbacks** (Windows proof: `cargo test --quiet` 536u+74i; WSL/Linux/macOS not separately re-proved) |
| `sk index embed` | ✅ All flags native (`native-embed` default feature): `--build`, `--test`, `--rebuild-tfidf`, `--setup`, `--status`, `--providers`, `--search` | `embed.py` fallback if native-embed unavailable | Wave3 added native `--build` |
| `sk sync run` (compiled default binary) | ✅ Daemon loop, push, pull, FTS refresh (`knowledge_fts`/`ke_fts`) — `native-sync` in default Cargo features since wave4 | Python `sk.py` shim → `sync-daemon.py` | Wave4 moved `native-sync` to default |
| `sk sync run` (Python shim / no binary) | Daemon loop only | Each push/pull cycle → `sync-daemon.py --once` | Python shim always routes to sync-daemon.py |

For the full script inventory, data pipeline, host scope table, provider package, and all coding conventions: **[docs/ARCHITECTURE.md](../docs/ARCHITECTURE.md)**
