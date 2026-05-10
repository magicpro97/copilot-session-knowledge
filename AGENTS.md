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
3. **Test after every change** — run `python3 test_security.py` and/or `python3 test_fixes.py` after Python edits; do not mark complete until tests pass.
4. **Verify before committing** — AST-parse every modified `.py` file; run both test suites; `git diff --stat` before commit.
5. **Sub-agent model selection** — use `claude-sonnet-4.6` for code generation; `claude-opus-4.6` for security audits; never dispatch sub-agents with the default (haiku) model for code changes.
6. **No guessing** — verify table names, function signatures, and file paths from source; never assume.
7. **Docs output quality** — distinguish Facts / Interpretation / Actions / Verification evidence; never present inference as fact; every action must include the executable command.
8. **Tentacle execution obligations** — when dispatched inside a tentacle: (a) read bundle files first, (b) stay in declared scope, (c) mark todos done with `sk tentacle todo <name> done <index>`, (d) do NOT run `git commit`/`git push`, (e) write a structured handoff with explicit `--status` (`DONE`, `BLOCKED`, `TOO_BIG`, `AMBIGUOUS`, or `REGRESSED`) via `sk tentacle handoff <name> "<summary>" --status <STATUS> [--changed-file <path>] --learn` before stopping.
9. **Claims require evidence** — any claim about test status, lint, format, CI, or runtime correctness must be backed by concrete output. If you did not run a verification command, say "not proven yet — run `<command>`." A `DONE` handoff with no evidence is treated as `AMBIGUOUS`. Issue closeouts must include verification evidence per acceptance criterion.

**Goal-loop (orchestrators only)** — after all tentacle handoffs pass verification gates, evaluate whether the overarching goal is met. If unmet, loop back to Phase 1 (new tentacles for remaining gaps). Only commit and close when success criteria are verifiably satisfied. Sub-agents report via handoff and stop; orchestrators own continuation. Record goal-eval evidence with `sk tentacle verify <name> "<check-command>" --label "goal-eval"`.

See [docs/AGENT-RULES.md](docs/AGENT-RULES.md) for the complete rule text, goal-loop pattern, and hook-enforcement table.

## Architecture Key Facts

- **Standalone scripts** — no inter-script imports; each script is self-contained
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
- **Hooks** — Copilot CLI only; `hook_runner.py` is the single entry point for the Python `sk.py` shim and non-binary installs; native Rust runner handles all events for Rust-binary installs (wave13: `preToolUse` added to `NATIVE_EVENTS`); `pre-commit` also runs scoped Ruff + Prettier cleanliness checks (fail-open when tooling absent)
- **Tentacle marker-cleanup** — use `tentacle.py marker-cleanup [--apply]` to inspect/remove stale dispatched-subagent marker entries without completing a tentacle

### Wave13–20 hybrid state (as of rust-wave20-intentional-docs tentacle)

Do **not** overclaim native coverage. The following surfaces are partially or fully Python-backed:

| Surface | Native (Rust) | Python-backed | Notes |
|---------|--------------|---------------|-------|
| `sk hooks run agentStop\|subagentStop` | ✅ Routes natively; uses `tentacle.py marker-cleanup --from-stop-event` | HMAC marker auth, enforce-briefing, tentacle-enforce, enforce-learn (via `hook_runner.py`) | Wave3 closed the agentStop/subagentStop gap |
| `sk hooks run sessionEnd` | ✅ Routes natively; `SessionEndRule` — per-session marker cleanup via `COPILOT_AGENT_SESSION_ID` + `session.log` write; `RecurrenceDetectorRule` — increments `recurrence_after_briefing` counter in `knowledge.db` (wave9 addition) | Full session-end Python rule parity not yet ported | Wave4 + wave9 |
| `sk hooks run errorOccurred` | ✅ Routes natively; `ErrorOccurredRule` — queries `knowledge.db` via native Rust FTS5 as primary; `query-session.py` subprocess only when DB unavailable | `query-session.py` itself is Python; normal DB-present path no longer spawns subprocess | Wave5 upgrade (direct DB path) |
| `sk hooks run sessionStart` | ✅ Routes natively (wave9); `SessionStartRule` — acknowledgement; `AutoBriefingRule` — spawns `briefing.py` with 10s timeout, signs HMAC `briefing-done` + `codebase-map-ran` markers; `IntegrityRule` — verifies/refreshes SHA256 hook-file manifest | `hook_runner.py` still owns all remaining managed event parity; Python fallback for non-Rust installs intact | Wave9 routing flip for sessionStart only |
| `sk hooks run postToolUse` | ✅ **Routes natively (wave10)**; all seven postToolUse rules fully ported (`TrackEditsRule`, `LearnReminderRule`, `TestReminderRule`, `NextjsTypecheckReminderRule`, `VerificationGatePostRule`, `ReadBeforeEditRule`, `TentacleSuggestRule`); `sync_markers.rs` writes `sync-nudge.json` after dispatch | No Python-backed surfaces remain for managed `postToolUse` — native runner is sole writer for postToolUse markers | Wave10 routing flip; no HMAC enforcement rules for postToolUse; dual-writer concern resolved |
| `sk hooks run preToolUse` | ✅ **Routes natively for Rust-binary installs (wave13)**; all deny-capable preToolUse rules active including `SyntaxGateRule` (via `python_exe()` + `py_compile` subprocess; fail-open; registered between `SubagentGitGuardRule` and `BlockEditDistRule`). Direct path: `subagent-git-guard`, `block-edit-dist`, `block-unsafe-html`, `pnpm-lockfile-guard` (wave7), `read-before-edit` warn (wave7), `VerificationGatePreRule` dirty-mark + informational deny (wave8), **`EnforceBriefingRule` deny-capable (wave11)**, **`EnforceLearnRule` deny-capable (wave11)**, **`TentacleEnforceRule` deny-capable (wave12)**, **`SyntaxGateRule` (wave13)** | **Python `sk.py` shim boundary unchanged**: shim still routes `sk hooks run preToolUse` through `hook_runner.py`; `hooks/rules/syntax_gate.py` and `hook_runner.py` NOT removed — necessary for shim and non-binary installs. Windows proof accepted (wave13); WSL/Linux/macOS not separately re-proved | Wave13 routing flip for Rust-binary installs; shim unchanged |
| HMAC marker auth (`marker_auth.rs`) | Foundation + partial wiring — sign/verify functions exist and native rules now use them for git-guard verification, TrackEdits writes, and AutoBriefingRule HMAC marker signs (wave9) | Full managed enforcement parity still uses Python `marker_auth.py` | Wave6 wires read/write parity into selected native rules; wave9 adds sessionStart HMAC writes |
| `sk watch` | Lock/poll loop + Copilot session-state indexer + Claude JSONL indexing + sessions-table column migrations (`file_mtime`, `indexed_at_r`, `fts_indexed_at`, `event_count_estimate`) + sync-op enqueueing (fail-open) + local-only `sessions_fts` writer + **`knowledge_entries`/`ke_fts` hot-path writer (`native-extract` is now a default Cargo feature since wave15; sync-op enqueue parity also landed; error lifecycle metadata `error_type`/`root_cause`/`severity` filled natively for mistake entries; integration proof: `sk-rust/tests/integration_test.rs`)** + **native deterministic relations: `SAME_SESSION`, `SAME_TOPIC`, `TAG_OVERLAP`, `RESOLVED_BY` (wave16)** + **native residual helpers: `backfill_affected_files`, `infer_task_ids`, confidence decay (wave17)** + **native first-run DB bootstrap: `open_or_create_index_db` / `ensure_extract_tables` in `session.rs`/`claude.rs`/`extract.rs` — missing `knowledge.db` no longer triggers Python bootstrap (wave18)** + **native SEMANTIC_PROXIMITY: computed via TF-IDF cosine in `watch.rs`/`sk-rust/src/embeddings/tfidf.rs` (wave19) — no Python auto-spawn on the successful native watch path** | **Wave20:** `watch.rs` never spawns Python on any path — including error paths. On genuine DB open/create or extract failure, `watch` emits a structured recovery hint naming the exact manual command (`python build-session-index.py --incremental` or `python extract-knowledge.py`). **Intentional Python surfaces (not removed):** Python `sk.py` shim/no-binary paths remain fully Python-backed; `hook_runner.py` is the Python runner for shim and non-binary installs; `build-session-index.py`, `extract-knowledge.py` (including `--semantic-only`), and `migrate.py` are intentional manual operator tools named in recovery hints. | Wave6 closes `sessions_fts` gap; wave10 removes `build-session-index.py --incremental` for existing-DB non-JSONL Copilot changes; wave15 moves `native-extract` to default feature; wave16 native relation slice; wave17 native residual helpers + sklearn-gated Python spawn; wave18 native first-run DB bootstrap; wave19 native SEMANTIC_PROXIMITY — successful native path zero-Python; **wave20 removes all Python auto-spawns including error paths — recovery hints replace subprocess fallbacks** (Windows proof: `cargo test --quiet` 536u+74i; WSL/Linux/macOS not separately re-proved) |
| `sk index embed` | ✅ All flags native (`native-embed` default feature): `--build`, `--test`, `--rebuild-tfidf`, `--setup`, `--status`, `--providers`, `--search` | `embed.py` fallback if native-embed unavailable | Wave3 added native `--build` |
| `sk sync run` (compiled default binary) | ✅ Daemon loop, push, pull, FTS refresh (`knowledge_fts`/`ke_fts`) — `native-sync` is in default Cargo features since wave4 | Python `sk.py` shim and no-binary installs → `sync-daemon.py` | Wave4 moved `native-sync` to default |
| `sk sync run` (Python shim / no binary) | Daemon loop only | Each push/pull cycle → `sync-daemon.py --once` | Python `sk.py` always routes to sync-daemon.py |

> Full conventions, data pipeline, and script inventory: **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)**

## Testing

```bash
python3 test_security.py    # focused security checks
python3 test_fixes.py       # focused runtime/regression checks
python3 run_all_tests.py    # full suite
```

For `browse-ui/` changes: `cd browse-ui && pnpm typecheck && pnpm lint && pnpm format:check && pnpm test && pnpm build` (and `pnpm test:e2e` when runtime/operator surfaces change materially)

## Hard Boundaries

- NEVER interpolate user input into SQL strings
- NEVER use pickle for serialization
- NEVER run `git commit` or `git push` as a dispatched sub-agent
- NEVER modify files outside your declared tentacle scope without a scope escalation note in the handoff
- ALWAYS use `O_CREAT | O_EXCL` for process locks (no TOCTOU races)
- ALWAYS run `sk briefing` before starting work on unfamiliar code (fallback: `python3 ~/.copilot/tools/briefing.py`)

## Hook Enforcement (Principle)

All hooks **fail-open**: a hook crash or absence never blocks the agent. Hook failures are logged; work proceeds. See [docs/AGENT-RULES.md](docs/AGENT-RULES.md) for the full enforcement table.
