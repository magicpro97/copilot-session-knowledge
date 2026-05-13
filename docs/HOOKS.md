# Enforcement Hooks

> Cross-platform Python hooks that enforce knowledge-base usage across sessions.

## Architecture

Uses a **unified hook runner** — one `hook_runner.py` dispatcher per event instead of separate scripts. Reduces process overhead from ~11 Python processes per tool call to 1.

```
hooks/
  hook_runner.py          # Single entry point — dispatches to rules
  marker_auth.py          # HMAC-signed marker authentication
  rules/
    __init__.py           # Rule registry
    common.py             # Shared utilities (get_module, deny, info, etc.)
    briefing.py           # Auto-briefing + enforce-briefing
    learn_gate.py         # Enforce learn.py before commit/task_complete
    learn_reminder.py     # Remind to record learnings
    read_tracker.py       # Warn on repeated view reads via shared session state
    tentacle.py           # Tentacle enforce + suggest (merged)
    edit_tracker.py       # Track bash edits + test reminder (merged)
    token_tracker.py      # Estimate session token usage + budget warnings
    error_kb.py           # Auto-search KB on errors
    integrity.py          # Verify hook file integrity
    session_lifecycle.py  # Session end + subagent/agent stop marker cleanup
```

## Rules

| Rule | Event | Description |
|------|-------|-------------|
| `auto-briefing` | sessionStart | Auto-runs briefing.py + refreshes codebase-map.py, creates HMAC-signed marker |
| `integrity` | sessionStart | Verifies hook files via SHA256 manifest |
| `session-end` | sessionEnd | Cleans up marker files, writes session.log entry, opt-in checkpoint reminder (`COPILOT_CHECKPOINT_REMIND=1`) |
| `recurrence-detector` | sessionEnd | Detects briefed mistakes that recurred in the same session; increments `recurrence_after_briefing` counter |
| `subagent-stop-cleanup` | agentStop + subagentStop | Best-effort dispatched-subagent marker cleanup from stop-event payload hints |
| `enforce-briefing` | preToolUse | Blocks edit/create/bash-writes until briefing done |
| `enforce-learn` | preToolUse | Blocks git commit AND task_complete without learn.py |
| `tentacle-enforce` | preToolUse | Blocks (deny) edits once ≥3 files across ≥2 modules are reached without tentacle setup. **Session-state paths** (`~/.copilot/session-state/`) are always exempt — `/research` outputs and other session artifacts are never blocked. **Bash redirects** are only flagged when the destination is a real source file; redirects to `.txt`, `.log`, `/dev/null`, or session-state paths are allowed. The deny message contains convention-level guidance: if you are the **orchestrator**, follow the runtime-bundle workflow — `tentacle.py create <name> --scope "<paths>" --desc "<desc>" --briefing` → `tentacle.py todo <name> add "<task>"` → `tentacle.py swarm <name> --agent-type general-purpose --model claude-sonnet-4.6 --briefing` (bundle is default); if you are a **dispatched sub-agent**, read the bundle manifest first, stay within your declared scope, write any scope gaps to `handoff.md`, and by convention avoid `git commit`/`git push`. |
| `subagent-git-guard` | preToolUse | **Defense-in-depth**: blocks `git commit`/`git push` bash commands when the `dispatched-subagent-active` marker is fresh. This is a secondary surface — **not** the primary enforcement path (see §Dispatched-Subagent Git Guard below). Whether `preToolUse` fires inside a delegated subagent context is not guaranteed by the platform. |
| `syntax-gate` | preToolUse | Blocks `edit`/`create` payloads that introduce Python syntax errors — applies the proposed change in memory and runs `py_compile`; fail-open on non-`.py` paths and missing files. Catches errors before they land on disk. |
| `read-before-edit` | preToolUse + postToolUse | Tracks viewed files (postToolUse on `view`), warns on `edit`/`create` of files not yet read in session (fail-open). |
| `read-tracker` | preToolUse | Warns on repeated `view` reads of the same file in a session, using shared per-session state populated by `token-tracker`; configurable ignores via `READ_TRACKER_IGNORE_SUFFIXES`; never blocks. |
| `block-edit-dist` | preToolUse | Blocks `edit`/`create` targeting `browse-ui/dist/`. These are build artifacts — run `cd browse-ui && pnpm build` instead. |
| `pnpm-lockfile-guard` | preToolUse | Blocks staging `browse-ui/package.json` changes without a matching `pnpm-lock.yaml` update. Prevents lockfile drift. |
| `block-unsafe-html` | preToolUse | Blocks `dangerouslySetInnerHTML` usage in `.ts`/`.tsx` files without `DOMPurify.sanitize()` or the `<Highlight>` component. |
| `verification-gate` | preToolUse + postToolUse | Tracks dirty Python / `browse-ui` TS/JS surfaces, records successful verification commands, and blocks closeout-style actions (`task_complete`, `gh issue close/comment`, tentacle `handoff --status DONE`, tentacle `complete`) until the required fresh evidence exists. |
| `track-edits` | postToolUse | Detects file changes via `git status` (language-agnostic) |
| `learn-reminder` | postToolUse | Reminds to record learnings after task_complete; also surfaces [docs/SYNC-MATRIX.md](SYNC-MATRIX.md) for docs/memory follow-ups |
| `test-reminder` | postToolUse | Reminds to run tests after 3+ Python file edits |
| `auto-bug-detector` | postToolUse | Detects bug-fix patterns from `edit`/`create` diffs and records them via `learn.py --mistake` with a 5-minute bucketed title so repeated detections increment `occurrence_count` rather than being silently dropped; writes `learn-done` once after one or more successful learn calls in the same evaluation. **edit** covers all five categories (error-handling, null-safety, guard-clause, async-fix, type-fix). **create** covers only `null-safety` (0.62) and `async-fix` (0.62) — the three remaining categories are excluded on create because any file with try/except, guard patterns, or type annotations would trigger them spuriously. Session-state paths (`~/.copilot/session-state/`) are always skipped on both paths. Informational-only. Fail-open. (issue #86) |
| `tentacle-suggest` | postToolUse | Suggests tentacle when edits reach ≥3 files across ≥2 modules (same threshold as tentacle-enforce); also references [docs/SYNC-MATRIX.md](SYNC-MATRIX.md) |
| `nextjs-typecheck-reminder` | postToolUse | Reminds to run `pnpm typecheck` after editing `.ts`/`.tsx` files in `browse-ui/` |
| `token-tracker` | postToolUse | Estimates per-session token usage from `view`/`edit`/`create`, stores totals plus `files_read` metadata in shared session state, and emits one-time budget warnings (default 80% / 95%; `TOKEN_BUDGET` override). |
| `error-kb` | errorOccurred | Auto-searches knowledge base on errors |
| `pre-commit` | git pre-commit | (1) Blocks commit when `dispatched-subagent-active` marker is fresh (primary subagent guard); (2) validates `.agent.md` / `SKILL.md` via `lint-skills.py`; (3) runs `scripts/check_syntax.py` on **all** staged `.py` files — fail-open when `check_syntax.py` is absent; (4) runs scoped Ruff format + lint check on staged Python files in the Ruff surface (see §Local vs CI below); (5) runs Prettier format check on supported staged files under `browse-ui/src/`. Checks (3)–(5) are **fail-open** — they silently skip when the respective tool is not installed. Requires `install.py --install-git-hooks`. |
| `pre-push` | git pre-push | Blocks push when `dispatched-subagent-active` marker is fresh. Requires `install.py --install-git-hooks`. |

### Local vs CI enforcement boundary

**Syntax gate** (`scripts/check_syntax.py`): the local `pre-commit` hook runs `check_syntax.py` on **all** staged `.py` files — this is a bounded check (staged files only, not full repo) and is fail-open when the script is absent. CI does not run a separate syntax-only pass (syntax errors would also fail the Ruff step), but the local hook catches them faster. Root scripts outside the Ruff surface (e.g., `watch-sessions.py`, `auto-update-tools.py`) **are** covered by this syntax gate even though they are not in the Ruff surface.

**Ruff lint surface** (identical between local `pre-commit` and CI `quality-gates` job):

```
embed.py  scout-config.py  scout-status.py
sync-config.py  sync-daemon.py  sync-status.py
migrate.py  generate-summary.py
briefing.py  learn.py  query-session.py  extract-knowledge.py
build-session-index.py  tentacle.py
checkpoint-diff.py  checkpoint-restore.py  checkpoint-save.py
browse/  hooks/
```

Both the local hook and CI run `ruff format --check` and `ruff check` on staged/changed files in this surface. Locally, **both checks are fail-open** — they skip silently when `ruff` is not installed. CI always has Ruff and will fail hard on violations. Other root scripts (e.g., `watch-sessions.py`, `install.py`, `auto-update-tools.py`) are **not** in scope.

The `browse/*` and `hooks/*` patterns in the local `_py_in_surface()` function match **all subdirectory depths** — consistent with CI's directory-level `ruff check browse/ hooks/` invocation. This ensures depth-4 files like `browse/static/vendor/_download.py` are covered locally as well as in CI.

**Full test suite** (`python3 run_all_tests.py`) is **not** enforced by the local `pre-commit` hook — it is too slow for every-commit use. CI runs it on every push/PR. Operators are expected to run it manually before submitting PRs. The local hook only enforces the fast checks listed in the table above.

### Browse operator console surfaces

The `/chat` operator console does **not** introduce a new hook class. Existing guardrails already cover it:

- Python-side operator files (`browse/core/operator_console.py`, `browse/api/operator.py`) stay inside the normal `browse/` syntax + Ruff surface.
- Frontend operator files under `browse-ui/src/app/chat/` and `browse-ui/src/components/chat/` stay under `block-edit-dist`, `block-unsafe-html`, `nextjs-typecheck-reminder`, and the staged Prettier check in `pre-commit`.
- `browse-ui/e2e/chat.spec.ts` is not hook-enforced directly; quality for that surface comes from `pnpm test:e2e` locally and the manual-dispatch CI `e2e` job.

### Browse host management surfaces

The browse-wide host state layer (`host-provider.tsx`, `host-profiles.ts`, `host-management.tsx`, and the header dropdown) does **not** introduce new hook classes. The existing guardrails apply without change:

- All files under `browse-ui/src/` are covered by `block-edit-dist` (dist artifact guard), `block-unsafe-html`, `nextjs-typecheck-reminder`, and the staged Prettier + pnpm-lockfile-guard checks in `pre-commit`.
- `host-profiles.ts` is pure localStorage — no server writes, no Python-side hook implications.
- `BROWSE_HOST_CHANGE_EVENT` is a browser `window.dispatchEvent(new Event(…))` — not a server-side or CLI hook event; it has no interaction with the Copilot hook platform.

### Platform events not currently handled

The Copilot platform provides 8 hook event types (per [GitHub docs](https://docs.github.com/en/copilot/concepts/agents/cloud-agent/about-hooks)). This repo's `hooks.json` and `hook_runner.py` handle 7 of them. The only currently unhandled platform event is:

| Event | Available since | Status | Notes |
|-------|----------------|--------|-------|
| `userPromptSubmitted` | 2024 | **Not handled** — no rules registered | Fires when user submits a prompt; input includes `prompt` field. Could be used for prompt logging/auditing. Deliberately excluded for now; add a rule in `hooks/rules/` to use it. |

`agentStop` and `subagentStop` are handled by `SubagentStopRule` in `hooks/rules/session_lifecycle.py` and are registered in `hooks/hooks.json` for best-effort dispatched-subagent marker cleanup.

### `toolArgs` type: platform sends dict, docs show string

The [official GitHub docs](https://docs.github.com/en/copilot/reference/hooks-configuration) show `toolArgs` as a JSON-encoded string:

```json
{"toolArgs": "{\"command\":\"rm -rf dist\"}"}
```

The **actual platform sends `toolArgs` as a parsed JSON object (dict)**, not a string. This repo's hooks handle it correctly via defensive `isinstance(tool_args, dict)` checks in `hooks/rules/briefing.py` and related rules. If you write hooks from the official docs' Bash examples, `jq -r '.toolArgs'` will give you the dict — no secondary `jq` parse needed. If the platform ever aligns with the docs and starts sending a string, the defensive checks will silently fall back to fail-open behavior.

## Key Features

- **Single process per event** — 1 Python process instead of 3-4
- **Fail-open** — rule errors/crashes don't block the agent
- **HMAC-signed counters** — all counters use HMAC (fixes plain counter bug)
- **Audit logging** — all decisions logged to `~/.copilot/markers/audit.jsonl`
- **Dry-run mode** — set `HOOK_DRY_RUN=1` to test without blocking
- **Merged duplicates** — tentacle enforce+suggest, track+test share code

## Native sk Routing

The managed `hooks.json` entries prefer the native `sk hooks run <event>` command surface when `sk` is available in PATH, falling back to direct hook-runner execution when not:

```bash
# hooks.json bash field (all events):
if command -v sk >/dev/null 2>&1; then sk hooks run <event>; else python3 "$HOME/.copilot/tools/hooks/hook_runner.py" <event>; fi
```

```powershell
# hooks.json powershell field (all events):
if (Get-Command sk -ErrorAction SilentlyContinue) { sk hooks run <event> } else { python "$env:USERPROFILE\.copilot\tools\hooks\hook_runner.py" <event> }
```

### Routing chain

```
hooks.json → sk hooks run <event>    (when sk is in PATH — native Rust or Python shim)
          OR → python3 hook_runner.py  (fallback when sk not installed)
          OR → python hook_runner.py   (PowerShell fallback on Windows)

sk hooks run <event>
  → [Rust binary] run_hooks_command(args)   (in sk-rust/src/commands/hooks.rs)
      OR [Python shim] sk.py::_run_hooks()
  → hook_runner.py <event>                  (Python fallback path for non-binary installs)
```

The `sk hooks` command is available in both the **Rust binary** (`sk-rust/src/commands/hooks.rs`) and the Python `sk.py` shim. For Rust-binary installs, all managed events route natively through the Rust runner. The Python `sk.py` shim always routes through `hook_runner.py` — shim behavior is unchanged regardless of native Rust availability.

### Install sk launcher

To enable the native routing path, install the `sk` launcher:

```bash
python install.py --install-sk    # Creates ~/.copilot/bin/sk (Unix) or sk.cmd (Windows)
# or let auto-update-tools.py do it automatically on next update
```

After install, `sk` must be in PATH (the installer adds `~/.copilot/bin` to your shell profile). The `--deploy-hooks` step informs you whether the native path is active.

### Runtime restart via native sk

`auto-update-tools.py --restart-watch` also prefers `sk watch` over direct `watch-sessions.py`:

```
_restart_manual():
  1. Check _sk_binary_path() → sk-native (Rust) or sk (shim) in ~/.copilot/bin/
  2. If found: spawn sk watch [--service]       ← native watcher on Rust binary, compat route on shim
  3. If not:   spawn python/pythonw watch-sessions.py  ← direct Python fallback
```

## Native Parity Gap Analysis

This section documents precisely which Python hook rules have been ported to the native Rust runner (`sk hooks <event>` direct path), which remain Python-only, and the exact hard blockers that prevent native parity.

### Ported to native Rust (`sk-rust/src/hooks/rules.rs`)

| Rule | Event(s) | Status | Notes |
|------|----------|--------|-------|
| `session-start` | sessionStart | ✅ Native (informational) | Emits "[sk] Session started" acknowledgement only; `AutoBriefingRule` and `IntegrityRule` follow in registration order |
| `auto-briefing` | sessionStart | ✅ **Native (wave9)** | `AutoBriefingRule` spawns `briefing.py` via `python_exe()`, signs HMAC `briefing-done` + `codebase-map-ran` markers on completion; 10s bounded timeout (same cap as Python path); fail-open if `briefing.py` absent; HMAC write uses `marker_auth::sign_marker` |
| `integrity` | sessionStart | ✅ **Native (wave9)** | `IntegrityRule` reads SHA256 hook-file manifest at `~/.copilot/hooks/integrity-manifest.json`; refreshes manifest when files change; emits integrity-verified or refresh notice; informational only; fail-open |
| `subagent-git-guard` | preToolUse | ✅ Native (deny-capable, wave6 hardened) | Blocks `git commit/push` when dispatched-subagent marker is fresh; verifies HMAC marker authenticity when a secret exists, but stays backward-compatible without a secret |
| `block-edit-dist` | preToolUse | ✅ Native (deny-capable, wave6; managed Rust path active in wave13) | Blocks `edit`/`create` targeting `browse-ui/dist/` on the direct Rust path and, after the wave13 routing flip, on managed `sk hooks run preToolUse` for Rust-binary installs. The Python `sk.py` shim still routes through `hook_runner.py`. |
| `block-unsafe-html` | preToolUse | ✅ Native (deny-capable, wave6; managed Rust path active in wave13) | Blocks unsanitized `dangerouslySetInnerHTML` in `.ts/.tsx/.js/.jsx` on the direct Rust path and, after the wave13 routing flip, on managed `sk hooks run preToolUse` for Rust-binary installs. The Python `sk.py` shim still routes through `hook_runner.py`. |
| `track-edits` | postToolUse | ✅ Native (wave6 write-side parity for `bash`; wave8 direct edit/create paths) | `bash` path now runs git-status diff, preserves HMAC `code-edit-count` / `py-edit-count`, updates `tentacle-edits`; wave8 also appends direct `edit`/`create` events to `tentacle-edits` for multi-step direct-edit flow accumulation; `TestReminderRule` remains authoritative for counter writes on edit/create to avoid dual-writer drift |
| `learn-reminder` | postToolUse | ✅ Native direct path (wave6) | Emits the task-complete reminder and writes `learn-done` on `learn.py` bash runs |
| `test-reminder` | postToolUse | ✅ Native managed + direct path (full counter-write; wave7 direct, wave10 managed) | Increments HMAC-signed `py-edit-count`, deletes/touches `tests-ran`; after the wave10 routing flip the native runner is the sole writer for managed `postToolUse` |
| `nextjs-typecheck-reminder` | postToolUse | ✅ Native managed + direct path (full counter-write; wave7 direct, wave10 managed) | Increments `ts-edit-count` (plain counter) for `browse-ui` `.ts/.tsx`; after the wave10 routing flip the native runner is the sole writer for managed `postToolUse` |
| `read-before-edit` | preToolUse + postToolUse | ✅ Native managed + direct path (wave7 direct; wave10 postToolUse managed; wave13 preToolUse managed) | postToolUse tracks viewed files in HMAC-signed `viewed-files` list marker; preToolUse warns when target file was not yet read on both direct and managed Rust paths. The Python `sk.py` shim still routes through `hook_runner.py`; fail-open. |
| `pnpm-lockfile-guard` | preToolUse | ✅ Native (deny-capable, wave7) | Blocks `git commit` bash commands when `browse-ui/package.json` is staged but `browse-ui/pnpm-lock.yaml` is not; runs `git diff --cached --name-only` subprocess; fail-open |
| `verification-gate` (postToolUse half) | postToolUse | ✅ Native direct path (evidence-recording, wave7) | Records evidence from successful verification commands (Python tests, pnpm checks) into HMAC-signed ledger; marks dirty surfaces when bash writes source files; path extraction handles `>`, `>>` redirects, `sed -i`, `tee`, and heredoc `open(...)` forms; preToolUse dirty-marking + informational deny resolved on direct path (wave8) — see `VerificationGatePreRule` |
| `verification-gate` (preToolUse dirty-mark + informational deny) | preToolUse | ✅ Native managed + direct path (wave8 direct; wave13 managed Rust) | `VerificationGatePreRule` marks Python/`browse-ui` surfaces dirty on `edit`/`create`; emits informational deny on `task_complete`/bash closeout actions when evidence is missing. After wave13, the same native rule runs on managed `sk hooks run preToolUse` for Rust-binary installs; the Python `sk.py` shim still routes through `hook_runner.py`. |
| `tentacle-suggest` | postToolUse | ✅ Native direct path (read-only, wave8) | `TentacleSuggestRule` reads `tentacle-edits` HMAC list marker and suggests tentacle-orchestration when edits span ≥3 files across ≥2 modules; `TrackEditsRule` remains the sole writer; handles both legacy flat-path and new JSON-dict marker formats; fail-open |
| `recurrence-detector` | sessionEnd | ✅ **Native (wave9)** | `RecurrenceDetectorRule` increments `recurrence_after_briefing` counter in `knowledge.db` for briefed mistakes that recurred in the session; uses `COPILOT_AGENT_SESSION_ID`; informational side-effect only; fail-open when DB absent or session ID missing |
| `enforce-briefing` | preToolUse | ✅ **Native managed + direct path (wave11 direct; wave13 managed Rust, deny-capable)** | `EnforceBriefingRule` blocks `edit`/`create`/bash writes to source files until a valid `briefing-done` marker is present, using the same `marker_auth` verification semantics as Python (HMAC-enforced when a secret exists, backward-compatible otherwise). Registered in `all_rules()` before `SubagentGitGuardRule` to match Python first-deny-wins order. Preserves tamper kill-switch behavior. After wave13, both direct `sk hooks preToolUse` and managed `sk hooks run preToolUse` for Rust-binary installs include this rule. The Python `sk.py` shim still routes through `hook_runner.py`. |
| `enforce-learn` | preToolUse | ✅ **Native managed + direct path (wave11 direct; wave13 managed Rust, deny-capable)** | `EnforceLearnRule` tracks code-file edits (increments `code-edit-count` via the same `marker_auth` counter semantics as Python); blocks `git commit`/`git push`/`task_complete` when edits ≥ 3 without a `learn-done` marker. Registered after `EnforceBriefingRule` and before `SubagentGitGuardRule`. Preserves tamper kill-switch behavior. After wave13, both direct `sk hooks preToolUse` and managed `sk hooks run preToolUse` for Rust-binary installs include this rule. The Python `sk.py` shim still routes through `hook_runner.py`. |
| `tentacle-enforce` | preToolUse | ✅ **Native managed + direct path (wave12 direct; wave13 managed Rust, deny-capable)** | `TentacleEnforceRule` mirrors Python `TentacleEnforceRule` in `hooks/rules/tentacle.py`. Reads the HMAC-signed `tentacle-edits` list marker, parsing both the legacy flat-path format (including the current Rust `TrackEditsRule` writer format) and the same-repo JSON bucket format. Fires when edits span ≥3 files across ≥2 modules without an active `tentacle-done` or `tentacle-bypass` marker; preserves tamper kill-switch. Session-state paths (`~/.copilot/session-state/`) remain exempt. Registered after `EnforceLearnRule` and before `SubagentGitGuardRule` (matching Python dispatch order). 24h TTL semantics preserved. After wave13, both direct `sk hooks preToolUse` and managed `sk hooks run preToolUse` for Rust-binary installs include this rule; the Python `sk.py` shim still routes to `hook_runner.py`. |
| `syntax-gate` | preToolUse | ✅ **Native managed + direct path (wave13, fail-open via Python subprocess)** | `SyntaxGateRule` applies the proposed file content in memory and runs `py_compile` via `python_exe()` subprocess; registered between `SubagentGitGuardRule` and `BlockEditDistRule` in `all_rules()`. Fail-open on non-`.py` paths, missing files, and subprocess failures. **Rust-binary installs only**: active on both direct `sk hooks preToolUse` and managed `sk hooks run preToolUse` after wave13; `preToolUse` is now in `NATIVE_EVENTS`. The Python `sk.py` shim still routes `sk hooks run preToolUse` through `hook_runner.py`. `hooks/rules/syntax_gate.py` and `hook_runner.py` remain necessary for the Python shim and non-binary installs — do NOT delete. Windows proof accepted: `cargo test --quiet` + `python tests\test_hook_compat.py` passed. WSL/Linux/macOS not separately re-proved in wave13. |
| `session-end` | sessionEnd | ✅ **Native + cleanup (wave4)** |Cleans per-session markers via `COPILOT_AGENT_SESSION_ID`; writes `session.log`; emits ack; fail-open when env var absent |
| `agent-stop` | agentStop, subagentStop | ✅ **Native + cleanup** (wave3) | Calls `tentacle.py marker-cleanup --from-stop-event` subprocess for marker cleanup; emits event acknowledgement; fail-open |
| `error-kb` | errorOccurred | ✅ **Native (direct DB, wave5)** | Queries `knowledge.db` natively via FTS5 (no subprocess on normal paths); falls back to `query-session.py` only when DB is unavailable; fail-open |

### Events now routed natively by `sk hooks run` (wave3–wave13)

As of wave3, `sk hooks run <event>` routes `agentStop` and `subagentStop` directly to the
native Rust runner (`run_hook`) instead of `hook_runner.py`.  The stable CLI boundary
`tentacle.py marker-cleanup --from-stop-event` (reads stop-event JSON from stdin, clears
matching marker entries) allows `AgentStopRule` to perform cleanup without importing Python
internals or reading HMAC secrets directly.

As of wave4, `sessionEnd` and `errorOccurred` are also routed natively:
- **`sessionEnd`**: `SessionEndRule` cleans per-session markers using `COPILOT_AGENT_SESSION_ID`
  and writes a `session.log` entry.  All operations are pure filesystem — no HMAC required.
  Fail-open: if `COPILOT_AGENT_SESSION_ID` is absent, cleanup is skipped.
- **`errorOccurred`**: `ErrorOccurredRule` (wave5 upgrade) queries `knowledge.db` directly via
  native Rust FTS5 (`crate::db::fts::search_kb_snippet`).  No subprocess on normal
  DB-present paths.  Falls back to spawning `query-session.py` only when the DB is
  genuinely unavailable (e.g. first-run before migration).  Fail-open at every step.

As of wave9, **`sessionStart`** is also routed natively:
- **`sessionStart`**: Three rules fire in registration order:
  1. `SessionStartRule` — emits "[sk] Session started" acknowledgement.
  2. `AutoBriefingRule` — spawns `briefing.py` via the correct Python interpreter; signs the
     `briefing-done` and `codebase-map-ran` HMAC markers on successful completion.  Uses a
     **10-second bounded timeout** (matching the Python path's `BRIEFING_TIMEOUT_SEC = 10`
     constant); a timeout is treated as pass-through (the rule does not block the session).
     Fail-open: if `briefing.py` is absent, the rule emits no output and returns `None`.
  3. `IntegrityRule` — reads `~/.copilot/hooks/integrity-manifest.json`; refreshes the
     manifest when hook files have changed since the last check; emits a verified or
     refresh notice.  Informational only — never blocks.  Fail-open on missing manifest
     or filesystem errors.

  Additionally, `RecurrenceDetectorRule` on `sessionEnd` (wave9 addition): increments the
  `recurrence_after_briefing` counter in `knowledge.db` for briefed mistakes that recurred
  in the current session.  Uses `COPILOT_AGENT_SESSION_ID`; informational side-effect only;
  fail-open when the DB is absent or the session ID is missing.

**`sessionStart` HMAC note:** `AutoBriefingRule` writes HMAC-signed markers using the same
`marker_auth::sign_marker` path used by `TrackEditsRule` (wave6).  The HMAC secret is read
from `~/.copilot/hooks/.marker-secret` — the same file as the Python `marker_auth.py` path.
This means native-written `briefing-done` markers are readable by Python hooks, and
Python-written markers are readable by the native runner.  The shared-secret precondition
(file must already exist; generated by `install.py --deploy-hooks`) still applies.

**Wave6 watch improvements (not hook events, but documented here for completeness):**
`sk-rust/src/index/session.rs` now applies sessions-table column migrations natively
(`file_mtime`, `indexed_at_r`, `fts_indexed_at`, `event_count_estimate`) and enqueues
sync-ops rows (state 1 — native, fail-open). Wave6 also adds a native local-only
`sessions_fts` writer for the non-JSONL Copilot path. Remaining Python-backed watch
surfaces are `extract-knowledge.py` classification and first-run DB creation fallback.

**Wave10 watch boundary update:** `sk watch` no longer spawns
`build-session-index.py --incremental` for existing-DB non-JSONL Copilot changes — the
native Rust indexer covers those paths fully (sessions-table column migrations + sync-op
enqueueing + `sessions_fts`). Python is now only invoked by `sk watch` for: (1) **first-run
DB bootstrap** when `knowledge.db` does not yet exist, and (2) **`extract-knowledge.py`**
NLP classification on non-JSONL Copilot checkpoint changes. These two Python surfaces are
intentionally preserved; `build-session-index.py` and `extract-knowledge.py` are not
removed. See `sk-rust/src/commands/watch.rs` for the authoritative boundary.

**Wave16 watch boundary update (native relation slice):** `sk watch` now extracts
deterministic knowledge relations natively in Rust: `SAME_SESSION`, `SAME_TOPIC`,
`TAG_OVERLAP`, `RESOLVED_BY`. After a successful native extract pass, `sk watch` invokes
`extract-knowledge.py --residual-only`, narrowing Python residual ownership to:
`SEMANTIC_PROXIMITY`, backfill helpers, confidence decay, and non-hot-path NLP.
`extract-knowledge.py` is NOT removed. First-run DB bootstrap (`spawn_indexer()`)
is unchanged. Windows proof: `cargo test --quiet` (519 unit + 71 integration passed),
`python tests\test_indexing.py` (25/25 passed). WSL/Linux/macOS not separately re-proved.

**`sessionStart` managed routing is now native (wave9)**: `sk hooks run sessionStart`
routes to the native Rust runner (`run_hook`) instead of `hook_runner.py`.
`AutoBriefingRule` (briefing.py subprocess + HMAC marker writes) and `IntegrityRule`
(SHA256 manifest check) run on the native path.  `RecurrenceDetectorRule` was also added
on `sessionEnd` in wave9.  No routing flip occurred for `preToolUse` or `postToolUse` in wave9.

**`postToolUse` managed routing is now native (wave10)**: `sk hooks run postToolUse`
routes to the native Rust runner (`run_hook`) instead of `hook_runner.py` as of wave10.
All seven postToolUse rules are informational-only and fully ported natively:
`TrackEditsRule`, `LearnReminderRule`, `TestReminderRule`, `NextjsTypecheckReminderRule`,
`VerificationGatePostRule`, `ReadBeforeEditRule`, `TentacleSuggestRule`.
`sync_markers::record_sync_signal` writes `sync-nudge.json` after rule dispatch.
No HMAC enforcement rules exist for `postToolUse` — all deny-capable rules (`enforce-briefing`,
`enforce-learn`, `tentacle-enforce`, `syntax-gate`) are `preToolUse`-only.
**Proof**: `sk hooks run postToolUse` with isolated USERPROFILE writes `sync-nudge.json`
and all marker files; 148 `test_hook_compat.py` tests pass.

**`preToolUse` managed routing remains Python-backed (wave8–wave12 unchanged)**: direct
`sk hooks preToolUse` gained `VerificationGatePreRule` (dirty-marking + informational deny) in
wave8, `EnforceBriefingRule` + `EnforceLearnRule` (deny-capable, native HMAC verification)
in wave11, and `TentacleEnforceRule` (deny-capable, same-repo JSON + legacy flat-path reader) in
wave12, but `sk hooks run preToolUse` still delegates to `hook_runner.py` because
`syntax-gate` still requires Python, and managed preToolUse was not
included in `NATIVE_EVENTS` during any wave through wave12. No routing flip occurred for
`preToolUse` through wave12.

**`preToolUse` managed routing is now native for Rust-binary installs (wave13)**: `SyntaxGateRule` —
the final remaining blocker for the managed routing flip — is now implemented natively in
`sk-rust/src/hooks/rules.rs`, registered between `SubagentGitGuardRule` and `BlockEditDistRule`.
It invokes `py_compile` via a Python subprocess (`python_exe()`) and remains fail-open.
`preToolUse` is now in `NATIVE_EVENTS` in `sk-rust/src/commands/hooks.rs`, so
`sk hooks run preToolUse` routes to the native Rust runner on Rust-binary installs.
**Python shim boundary unchanged**: the Python `sk.py` shim still routes `sk hooks run preToolUse`
through `hook_runner.py`. `hooks/rules/syntax_gate.py`, `hook_runner.py`, and all Python hook
fallback files remain necessary for the Python shim and non-binary installs; they are NOT removed.
Windows proof accepted: `cargo test --quiet` (unit tests in `rules.rs`) and
`python tests\test_hook_compat.py` passed after the wave13 audit. WSL/Linux/macOS parity
for wave13 is **not separately re-proved** — no separate platform proof is available from repo files.

### Python-only rules (via `sk hooks run` → `hook_runner.py`)

For the Python `sk.py` shim and non-binary installs, `sk hooks run` delegates to
`hook_runner.py` for all events. For Rust-binary installs, only the Python shim path
still routes `preToolUse` through `hook_runner.py` — the native Rust runner handles
all events including `preToolUse` (wave13). The table below lists each rule, its
blockers (all resolved for Rust-binary installs as of wave13), and remaining notes.

#### preToolUse — deny-capable (high risk if incorrectly ported)

| Rule | Hard Blocker | Why dangerous to port partially |
|------|-------------|--------------------------------|
| ~~`enforce-briefing`~~ | ✅ **Resolved on direct path (wave11); also active on managed path (wave13, Rust-binary installs)** | `EnforceBriefingRule` now native (`sk hooks preToolUse` and `sk hooks run preToolUse` on Rust-binary installs); preserves tamper kill-switch; Python `sk.py` shim still routes through `hook_runner.py` |
| ~~`enforce-learn`~~ | ✅ **Resolved on direct path (wave11); also active on managed path (wave13, Rust-binary installs)** | `EnforceLearnRule` now native; Python `sk.py` shim still routes through `hook_runner.py` |
| ~~`tentacle-enforce`~~ | ✅ **Resolved on direct path (wave12); also active on managed path (wave13, Rust-binary installs)** | `TentacleEnforceRule` now native; reads same-repo JSON bucket plus legacy flat-path formats; session-state paths exempt; 24h TTL semantics preserved; Python `sk.py` shim still routes through `hook_runner.py` |
| ~~`syntax-gate`~~ | ✅ **Resolved (wave13)** — `SyntaxGateRule` now native via Python subprocess (`python_exe()` + `py_compile`); registered between `SubagentGitGuardRule` and `BlockEditDistRule`; fail-open; active on both direct path and managed path (Rust-binary installs). Python `sk.py` shim still routes to `hook_runner.py`; `hooks/rules/syntax_gate.py` not removed. |
| `block-edit-dist` | None — simple path check | Low risk, but low value to port alone |
| ~~`pnpm-lockfile-guard`~~ | ✅ **Resolved on direct path (wave7); also active on managed path (wave13, Rust-binary installs)** | Now native (`sk hooks preToolUse`); Python `sk.py` shim still routes through `hook_runner.py` |
| `block-unsafe-html` | None — regex on proposed file content | Medium; requires reading the full proposed content from `toolArgs` |
| `verification-gate` | `marker_auth.py` HMAC ledger — `verification-ledger` multi-surface HMAC marker | postToolUse evidence-recording resolved on direct path (wave7); preToolUse dirty-marking + informational deny resolved on direct path (wave8); HMAC-gated managed enforcement remains Python-only for the `sk.py` shim |
| ~~`read-before-edit`~~ | ✅ **Resolved on direct path (wave7); also active on managed path (wave13, Rust-binary installs)** | Now native (`sk hooks preToolUse/postToolUse`); Python `sk.py` shim still routes through `hook_runner.py` |

#### postToolUse / lifecycle / errorOccurred

| Rule | Hard Blocker |
|------|-------------|
| ~~`test-reminder`~~ | ✅ **Resolved (wave10)** — Full counter-write port landed on the direct path in wave7; managed `postToolUse` now also routes natively in wave10 | native runner is now the sole writer for managed `postToolUse` |
| ~~`tentacle-suggest`~~ | ✅ **Resolved (wave10)** — read-only `TentacleSuggestRule` landed on the direct path in wave8; managed `postToolUse` now also routes natively in wave10 | native runner now owns the managed path too |
| ~~`nextjs-typecheck-reminder`~~ | ✅ **Resolved (wave10)** — Full counter-write port landed on the direct path in wave7; managed `postToolUse` now also routes natively in wave10 | native runner is now the sole writer for managed `postToolUse` |
| ~~`read-before-edit` (postToolUse half)~~ | ✅ **Resolved (wave10)** — Native `ReadBeforeEditRule` landed on the direct path in wave7; managed `postToolUse` now also routes natively in wave10 | native runner now owns the managed path too |
| ~~`verification-gate` (postToolUse half)~~ | ✅ **Resolved on managed path (wave10)** — Evidence-recording native since wave7; managed `postToolUse` now routes natively in wave10; deny-capable preToolUse closeout-state machine and full HMAC ledger remain Python-only on `preToolUse` |
| ~~`managed postToolUse routing`~~ | ✅ **Resolved (wave10)** — `sk hooks run postToolUse` now routes natively; all seven postToolUse rules fully ported; `sync_markers.rs` writes `sync-nudge.json`; dual-writer concern resolved (native runner is now sole writer for postToolUse markers) |
| ~~`auto-briefing` (sessionStart)~~ | ✅ **Resolved (wave9)** — `AutoBriefingRule` spawns `briefing.py` via `python_exe()` and signs HMAC markers; 10s bounded timeout matching the Python path |
| ~~`integrity` (sessionStart)~~ | ✅ **Resolved (wave9)** — `IntegrityRule` reads/refreshes the SHA256 manifest; informational only |
| ~~`recurrence-detector` (sessionEnd)~~ | ✅ **Resolved (wave9)** — `RecurrenceDetectorRule` increments `recurrence_after_briefing` counter in `knowledge.db`; informational side-effect; fail-open |
| ~~`session-end` (sessionEnd, Python)~~ | ✅ **Resolved (wave4)** — now handled natively via `COPILOT_AGENT_SESSION_ID` filesystem cleanup; Python `recurrence-detector` also ported to native in wave9 |
| ~~`subagent-stop-cleanup`~~ (agentStop/subagentStop) | ✅ **Resolved (wave3)** — now handled natively via `tentacle.py marker-cleanup --from-stop-event` stable CLI boundary |
| ~~`error-kb` (errorOccurred)~~ | ✅ **Resolved (wave5)** — now uses native Rust DB path (FTS5 direct query); `query-session.py` subprocess kept only as DB-unavailable fallback |

### Prerequisite history — managed `sk hooks run preToolUse` routing flip

`preToolUse` is now routed natively for **Rust-binary installs** as of wave13.
This section records the resolved blockers and the remaining Python-shim boundary.

The following blockers were present before wave13:

1. **`syntax-gate` (`py_compile` dependency)** — the effective final blocker before wave13. `VerificationGatePreRule` was already native/deny-capable since wave8; `enforce-briefing`, `enforce-learn`, and `tentacle-enforce` were resolved on the direct path in wave11–wave12. The only remaining blocker for the managed routing flip was `SyntaxGateRule`. Wave13 resolves this via a Python subprocess (`python_exe()` + `py_compile`), keeping the Python boundary at the subprocess level rather than requiring an embedded interpreter.

2. ~~**`py_compile` substitute for `syntax-gate`**~~ — ✅ **Resolved (wave13)** — `SyntaxGateRule` now uses `python_exe()` subprocess; fail-open; registered between `SubagentGitGuardRule` and `BlockEditDistRule`.

3. **Full test coverage** — Windows proof accepted: `cargo test --quiet` (unit tests in `rules.rs`) and `python tests\test_hook_compat.py` passed after the wave13 audit. WSL/Linux/macOS parity for wave13 is **not separately re-proved**.

> **Resolved (wave3):** blocker #3 from the original list — `tentacle._clear_dispatched_subagent_marker` ABI — is resolved by the `tentacle.py marker-cleanup --from-stop-event` stable CLI boundary. `agentStop`/`subagentStop` now use the native Rust path in `sk hooks run`.
>
> **Resolved (wave4):** `sessionEnd` and `errorOccurred` now use the native Rust path in `sk hooks run`. The `postToolUse` event intentionally remained Python-backed through wave9 due to HMAC-gated enforcement rules.
>
> **Resolved (wave5):** `errorOccurred` (`error-kb` rule) now queries `knowledge.db` directly via native Rust FTS5 — no Python subprocess on normal DB-present paths. `query-session.py` is retained as a DB-unavailable fallback only.
>
> **Wave6 HMAC progress:** `sk-rust/src/hooks/marker_auth.rs` is now partially wired into native rules — wave6 uses it for git-guard marker verification and TrackEdits counter/list-marker writes. `sessionStart`, managed `preToolUse`, and managed `postToolUse` still remained Python-backed until those rules were ported safely.
>
> **Wave7 direct-path progress:** `TestReminderRule` and `NextjsTypecheckReminderRule` are now full counter-write ports on the direct `sk hooks postToolUse` path (HMAC-signed `py-edit-count`, plain `ts-edit-count`, `tests-ran` marker). `ReadBeforeEditRule` (preToolUse + postToolUse), `PnpmLockfileGuardRule` (preToolUse, deny-capable), and `VerificationGatePostRule` (postToolUse evidence-recording with improved path extraction for `>`, `sed -i`, `tee`, and heredoc `open(...)`) are new native additions. The managed `sk hooks run postToolUse` path remained Python-backed through wave7.
>
> **Wave8 direct-path progress:** `VerificationGatePreRule` (preToolUse dirty-marking + informational closeout deny) and `TentacleSuggestRule` (postToolUse read-only, suggests tentacle when edits span ≥3 files across ≥2 modules) are new native additions. `TrackEditsRule` now also appends direct `edit`/`create` events to `tentacle-edits`, fixing multi-step direct-edit flow accumulation for `TentacleSuggestRule`. The managed `sk hooks run` paths remained Python-backed through wave8.
>
> **Wave9 routing flip (sessionStart only):** `sessionStart` now uses the native Rust path in `sk hooks run`. `AutoBriefingRule` spawns `briefing.py` with a 10s timeout and writes HMAC-signed `briefing-done` + `codebase-map-ran` markers via `marker_auth::sign_marker`. `IntegrityRule` verifies/refreshes the SHA256 hook-file manifest. `RecurrenceDetectorRule` on `sessionEnd` increments `recurrence_after_briefing` in `knowledge.db`. No routing flip occurred for `preToolUse` or `postToolUse` in wave9.
>
> **Wave10 routing flip (postToolUse):** `postToolUse` now uses the native Rust path in `sk hooks run`. All seven postToolUse rules (informational-only) are fully ported; `sync_markers::record_sync_signal` writes `sync-nudge.json` after dispatch. No HMAC enforcement rules exist for `postToolUse` — dual-writer concern is resolved (native runner is now the sole writer for postToolUse markers). `preToolUse` remained Python-backed; remaining blockers at that point were `tentacle-enforce`, `syntax-gate`, and (stale, as later clarified) the HMAC-gated `verification-gate` closeout block.
>
> **Wave11 direct-path progress (no routing flip):** `EnforceBriefingRule` and `EnforceLearnRule` are now implemented natively in `sk-rust/src/hooks/rules.rs` and registered in `all_rules()` before `SubagentGitGuardRule` (matching Python first-deny-wins order). Both preserve the tamper kill-switch behavior from the Python originals and reuse `marker_auth` verification semantics for markers/counters (HMAC-enforced when a secret exists, backward-compatible otherwise). Direct `sk hooks preToolUse` now denies via stdout JSON for these two rules. Tests in `tests/test_hook_compat.py` cover the wave11 checks (verified on Windows by `cargo test --quiet` + `python tests\test_hook_compat.py`). `preToolUse` is NOT in `NATIVE_EVENTS` — managed `sk hooks run preToolUse` still routes through `hook_runner.py`. `tentacle-enforce` and `syntax-gate` remained Python-only through wave11; WSL proof was not separately verified by the wave11 code tentacle.
>
> **Wave12 direct-path progress (no routing flip):** `TentacleEnforceRule` is now implemented natively in `sk-rust/src/hooks/rules.rs` and registered in `all_rules()` after `EnforceLearnRule` and before `SubagentGitGuardRule` (matching Python dispatch order). The native reader supports the same-repo JSON bucket format as well as legacy flat-path entries (including the current Rust direct-path writer shape); 24h TTL semantics are preserved. Session-state paths (`~/.copilot/session-state/`) remain exempt. Tamper kill-switch behavior is preserved. Direct `sk hooks preToolUse` now also denies via stdout JSON for this rule. Proof accepted on Windows: `cargo test --quiet` (unit tests in `rules.rs`) and `python tests\test_hook_compat.py` (all wave12 checks pass). WSL or macOS parity for wave12 is **not separately verified**. `preToolUse` is NOT in `NATIVE_EVENTS` — managed `sk hooks run preToolUse` still routes through `hook_runner.py`. After wave12, `syntax-gate` was the sole remaining blocker for a managed routing flip; the stale claim about the HMAC-gated `verification-gate` closeout as a managed-path blocker is corrected here — `VerificationGatePreRule` was already native/deny-capable since wave8.
>
> **Wave13 routing flip (preToolUse, Rust-binary installs):** `SyntaxGateRule` is now implemented natively in `sk-rust/src/hooks/rules.rs`, registered between `SubagentGitGuardRule` and `BlockEditDistRule`. It invokes `py_compile` via `python_exe()` subprocess; fail-open. `preToolUse` is now in `NATIVE_EVENTS` in `sk-rust/src/commands/hooks.rs` — `sk hooks run preToolUse` routes to the native Rust runner on Rust-binary installs. **Python shim boundary unchanged**: the Python `sk.py` shim still routes `sk hooks run preToolUse` through `hook_runner.py`. `hooks/rules/syntax_gate.py`, `hook_runner.py`, and all Python hook fallback files are NOT removed — they remain the authoritative path for the Python shim and non-binary installs. Proof accepted on Windows: `cargo test --quiet` + `python tests\test_hook_compat.py` passed. WSL/Linux/macOS parity for wave13 is **not separately re-proved**.


### Testing the native direct path

The native `sk hooks <event>` path (not `sk hooks run`) can be tested with:

```bash
# POSIX
echo '{"toolName":"bash","toolArgs":{"command":"git commit -m test"}}' | sk hooks preToolUse
echo '{}' | sk hooks sessionStart
echo '{}' | sk hooks agentStop

# Windows PowerShell
'{"toolName":"bash","toolArgs":{"command":"git commit -m test"}}' | sk hooks preToolUse
'{}' | sk hooks sessionStart
'{}' | sk hooks agentStop
```

Set `HOOK_DRY_RUN=1` to verify denial logic without blocking, and `HOOK_LOG_LEVEL=DEBUG` for verbose audit output. Audit entries are written to `~/.copilot/markers/audit.jsonl`.

## preToolUse Routing-Flip Specification

This section records the verified state of the managed routing flip for `preToolUse`.
The flip **has occurred** for Rust-binary installs as of wave13. The Python `sk.py` shim
path remains unchanged.

> **Note:** `postToolUse` was flipped to native in wave10. All seven postToolUse rules
> are informational-only and fully ported; `sync_markers.rs` writes `sync-nudge.json`
> after dispatch. `preToolUse` was flipped in wave13 (Rust-binary installs only).

### Current managed routing state (post-wave13, Rust-binary installs)

```
# Rust-binary installs (sk binary present):
sk hooks run sessionStart   → native Rust  (wave9: AutoBriefingRule + IntegrityRule + SessionStartRule)
sk hooks run sessionEnd     → native Rust  (wave4+wave9: SessionEndRule + RecurrenceDetectorRule)
sk hooks run agentStop      → native Rust  (wave3: AgentStopRule)
sk hooks run subagentStop   → native Rust  (wave3: AgentStopRule)
sk hooks run errorOccurred  → native Rust  (wave5: ErrorOccurredRule)
sk hooks run postToolUse    → native Rust  (wave10: all postToolUse rules natively ported)
sk hooks run preToolUse     → native Rust  (wave13: SyntaxGateRule via py_compile subprocess — Rust-binary installs only)

# Python sk.py shim (no compiled binary, or sk.py used directly):
sk hooks run <any event>    → hook_runner.py  (shim always delegates to Python; behavior unchanged)
```

### Three distinct `preToolUse` paths (wave13 and later)

| Path | How invoked | Routing | Notes |
|------|------------|---------|-------|
| Direct Rust path | `sk hooks preToolUse` (no `run`) | Native Rust runner; all registered rules in `all_rules()` | Incremental rollout path; all deny rules active |
| Managed Rust path | `sk hooks run preToolUse` (Rust binary) | Native Rust runner via `NATIVE_EVENTS` | All rules active including `SyntaxGateRule`; wave13 flip |
| Python shim path | `sk hooks run preToolUse` (Python `sk.py`) | `hook_runner.py` | Unchanged; Python shim always routes all events to `hook_runner.py` |

### Why the Python `sk.py` shim still routes `preToolUse` through `hook_runner.py`

The direct Rust path (`sk hooks preToolUse`) and the managed Rust path (`sk hooks run preToolUse`
on Rust-binary installs) now both use the native Rust runner.  The Python `sk.py` shim still
routes `sk hooks run preToolUse` to `hook_runner.py` — this is the only remaining Python-backed
surface for `preToolUse`. The Python shim is intentionally preserved for non-binary installs and
fallback compatibility.

| Rule | Status on managed Rust path (wave13) | Status on Python shim path |
|------|--------------------------------------|---------------------------|
| `enforce-briefing` | ✅ Native (`EnforceBriefingRule`; deny-capable) | Python (`hook_runner.py`) |
| `enforce-learn` | ✅ Native (`EnforceLearnRule`; deny-capable) | Python (`hook_runner.py`) |
| `tentacle-enforce` | ✅ Native (`TentacleEnforceRule`; deny-capable) | Python (`hook_runner.py`) |
| `syntax-gate` | ✅ Native (`SyntaxGateRule`; via `python_exe()` subprocess; fail-open) | Python (`hook_runner.py`) |
| `verification-gate` (HMAC-enforced closeout block) | `VerificationGatePreRule` (dirty-mark + info-deny) is native; full HMAC-enforced closeout block is Python-only on the shim | Python (`hook_runner.py`) |

### Counter parity for `preToolUse` — wave13 status

Counter write paths for `preToolUse`-triggered thresholds are now owned by the native Rust
runner for Rust-binary installs (wave13). The `postToolUse` counter race was resolved in
wave10: the native runner is the sole writer for postToolUse markers, and `hook_runner.py`
no longer fires for `postToolUse`. For the Python `sk.py` shim, `hook_runner.py` remains
the authoritative counter writer for `preToolUse`.

### Non-goals for Python shim path

The Python shim must NOT be removed or disabled:
- `hook_runner.py` remains the Python fallback for non-binary installs and the Python `sk.py` shim
- `hooks/rules/syntax_gate.py` remains necessary; it is the Python-side `syntax-gate` implementation
- Claiming the Python fallback can be deleted is incorrect — the shim boundary is intentionally preserved
- Deny-capable rule parity on the Rust path does not eliminate the need for the Python path; the shim always routes to `hook_runner.py`

Rule 9 now has a **partial hook enforcement surface** via `verification-gate`. The hook does not parse every prose sentence an agent writes, but it does prevent common closeout actions from going through after tracked code edits unless matching verification commands have succeeded and been recorded in the signed ledger.

**What this means in practice:**

| Claim type | Hook enforcement | Policy enforcement |
|------------|-----------------|-------------------|
| "Format / lint clean" | `verification-gate` blocks closeout after dirty `browse-ui` TS/JS edits until `pnpm format:check` and `pnpm lint` succeed | For non-`browse-ui` surfaces, the agent must still run the command and attach output |
| "Tests pass" | `verification-gate` blocks closeout after dirty Python edits until `test_security.py`, `test_fixes.py`, `pytest`, or equivalent recorded test commands succeed | Agent must still record pass/fail counts in the handoff/comment |
| "CI is green" | None | Agent must supply CI run URL or job output |
| "Build succeeds" | `verification-gate` blocks closeout after dirty `browse-ui` TS/JS edits until `pnpm build` succeeds; `syntax-gate` still covers Python syntax only | Non-`browse-ui` build claims still need explicit command output |
| "Tool works" (runtime) | None | Agent must include runtime execution evidence |

**Current hook scope:** `verification-gate` tracks two dirty surfaces today:

- `py` — any `.py` edit/write
- `ui` — `.ts` / `.tsx` / `.js` / `.jsx` edits/writes under `browse-ui/`

It records successful verification commands into the signed `~/.copilot/markers/verification-ledger` marker and clears stale evidence when more edits land on the same surface. This is a closeout gate, not a prose parser: it blocks `task_complete`, `gh issue close/comment`, `tentacle.py handoff --status DONE`, and `tentacle.py complete` when tracked evidence is missing.

**Orchestrator responsibility:** Even with `verification-gate`, the orchestrator must still treat any `DONE` status that lacks concrete verification evidence for runtime/tool/CI claims as `AMBIGUOUS` and triage accordingly before running the standard Build → Lint → Test → Review gates.

**Still policy-level:** CI-green assertions, screenshot-diff proofs, benchmark thresholds, and runtime/tool-specific correctness claims are not inferred by the hook. Agents must still provide the actual run URL, hash, or runtime log.

> Full Rule 9 text: **[docs/AGENT-RULES.md](AGENT-RULES.md#rule-9--claims-require-evidence)**

## Test Isolation

`hook_runner.py` writes audit entries to `Path.home() / ".copilot" / "markers" / "audit.jsonl"`.
Since `Path.home()` reads `$HOME` at runtime, subprocess-based tests **must** override `HOME` to
prevent polluting the operator audit log (which feeds `retro.py` and `knowledge-health.py`).

**Pattern for any subprocess test that invokes `hook_runner.py`:**

```python
import tempfile, shutil

_isolated_home = Path(tempfile.mkdtemp(prefix="test-hooks-home-"))
_isolated_env = {**os.environ, "HOME": str(_isolated_home)}

r = subprocess.run(
    [sys.executable, str(RUNNER), "preToolUse"],
    input=..., capture_output=True, text=True,
    env=_isolated_env,   # ← required; keeps audit writes off the real log
    timeout=10,
)
# ... assertions ...

shutil.rmtree(_isolated_home, ignore_errors=True)
```

**Regression test** — `test_hooks.py` Section 1 (test 1i) reads the audit file under the isolated
HOME and asserts that dry-run / parse-error entries land there, proving the HOME override
redirected audit writes away from operator state.

Do **not** rely on `HOOK_DRY_RUN=1` alone for isolation: dry-run suppresses `deny` output but
still writes `deny-dry` and `parse-error` entries to the audit log.

## `hooks.json` Schema Notes

### `comment` field

The repo's `hooks.json` includes a `comment` field on each hook entry (e.g. `"comment": "[GLOBAL] Unified hook runner — auto-briefing + integrity check"`). The [official schema](https://docs.github.com/en/copilot/concepts/agents/cloud-agent/about-hooks) defines only `type`, `bash`, `powershell`, `cwd`, `env`, and `timeoutSec`. The `comment` field is **not part of the official schema** and is likely silently ignored by the platform. It is kept here as human-readable inline documentation. If the platform adds strict JSON validation, these comments will need to be removed.

## Bash Bypass Protection

Hooks detect file writes via bash commands (heredocs, redirects, `sed -i`, `tee`, `cp`, `mv`, `curl -o`, etc.) AND verify actual changes via `git status`.

## Learn Gate — Code Edit Counting

The `enforce-learn` rule (and its legacy standalone `enforce-learn.py`) counts a fixed set of source-code extensions toward the learn-gate threshold. Two surfaces are **always excluded**:

- **`.md` files** — markdown documentation and session-research notes are never counted. Writes to these files during active research, documentation, or planning passes must not inflate the code-edit counter or trigger false learn-gate blocks.
- **Session-state writes** — any file path under `~/.copilot/session-state/` is excluded regardless of extension. Briefings, knowledge fragments, and other session artifacts are not project source code.

**Extensions that count toward the threshold:**

```
.py  .kt  .ts  .tsx  .js  .jsx  .swift  .java  .go  .rs
.json  .yaml  .yml  .xml  .html  .css  .toml  .sh  .bat  .ps1
```

> **Shell scripts are included.** `.sh`, `.bat`, and `.ps1` count as code edits. Both the unified
> `hooks/rules/learn_gate.py` and the legacy `hooks/enforce-learn.py` use the same extension set,
> so the threshold behavior is consistent regardless of which runner fires.
>
> The canonical definition lives in `hooks/rules/common.py` (`CODE_EXTENSIONS`) and is mirrored
> verbatim in `enforce-learn.py` so both paths remain in sync after updates.

## Tamper Protection

Hook files are locked with OS immutable flags:
- **macOS**: `chflags uchg` — user immutable
- **Linux**: `chattr +i` — requires root to modify
- **Windows**: `attrib +R` — read-only (weaker)

```bash
sk install --deploy-hooks       # Deploy Copilot CLI hooks
sk install --lock-hooks         # Lock (AI can't modify)
sk install --unlock-hooks       # Unlock for updates
sk install --install-git-hooks  # Install pre-commit/pre-push into current repo
# fallback: python3 ~/.copilot/tools/install.py [flags]
```

`~/.copilot/hooks/hooks.json` is treated as a **managed global file**. `sk update` may redeploy it
from this repo when hook config changes; differing local content is backed up before overwrite, but
long-lived customizations should live in source-controlled hook code instead of manual edits there.

> **Note:** `--install-git-hooks` must be run separately per repository to install the git-level
> subagent guard. It is not performed automatically by `--deploy-hooks`. Re-run after major tool
> updates to refresh hook scripts in `.git/hooks/`.

## Dispatched-Subagent Git Guard

Phase 3 adds git-level enforcement that blocks `git commit` and `git push` while a dispatched
subagent session is active. The design is **marker-based** rather than hook-only because
`preToolUse` hook inheritance inside delegated/background agent contexts is not guaranteed by
the platform — a hook that fires reliably in the orchestrator session may silently not fire
inside a `task()`-spawned subagent.

### How it works

**Step 1 — Marker write (orchestrator, via `tentacle.py swarm`)**

When `tentacle.py swarm` dispatches a subagent, it writes an HMAC-signed marker file:

```
~/.copilot/markers/dispatched-subagent-active
```

The marker is a JSON file with the following contract:

| Field | Description |
|-------|-------------|
| `name` | Always `"dispatched-subagent-active"` |
| `ts` | UNIX timestamp of the most-recent write (used for HMAC + global TTL anchor) |
| `sig` | HMAC-SHA256 over `"name:ts"` (omitted when no secret is configured) |
| `active_tentacles` | List of per-entry objects: `{"name": "<tentacle>", "ts": "<unix>", "git_root": "<abs-path>", "tentacle_id": "<uuid>"}` where `tentacle_id` is optional for legacy entries. Each entry carries its own dispatch timestamp, the git root of the dispatching repo, and, for newer entries, a stable per-instance UUID generated at `create` time. Legacy entries may omit `tentacle_id` entirely, and some readers may also encounter `null`, so consumers should use `.get("tentacle_id")`. Readers also accept the old string-list format for backward compatibility. **Deduplication key: `tentacle_id` (primary, phase 5) → `(name, git_root)` fallback (phase 4, legacy entries without `tentacle_id`).** Two instances with the same logical name in the same repo each produce a separate entry because their `tentacle_id` values differ. |
| `git_root` | Top-level field: absolute git root of the most-recent writer (used by the legacy path **only** for pure string-list `active_tentacles` — not for mixed-format or dict-list entries). Per-entry `git_root` is the authoritative source for all dict-list and mixed-format markers. |
| `scope` | File-scope list from the most-recently-dispatching tentacle |
| `dispatch_mode` | Dispatch mode of the most-recently-dispatching tentacle |
| `ttl_seconds` | Expected lifetime; consumers treat markers older than this as stale |
| `written_at` | ISO 8601 human-readable timestamp |

**Per-entry TTL:** Each `active_tentacles` entry's `ts` is used for its own TTL check. A stale
entry (its `ts` is older than `ttl_seconds`) is treated as inactive even if the global marker
file is still fresh.

**Concurrent tentacles:** Multiple tentacles dispatched in parallel each add their dict entry to
`active_tentacles`. `tentacle.py complete <name>` removes only that tentacle's entry (matched by
`tentacle_id` when present, falling back to `(name, git_root)` for legacy entries); the marker
file is deleted only when `active_tentacles` becomes empty.

**Tentacle identity (phase 5):** `tentacle.py create` now generates a UUID `tentacle_id` and
stores it in the tentacle's `meta.json`. `swarm` and `bundle` read this UUID and embed it in the
marker entry. This enables two orchestrators in the same repo using the same logical name to each
hold a separate, non-colliding marker entry. `complete` reads `tentacle_id` from `meta.json` and
removes only the entry with the matching identity — completing one session does not clear a
same-named sibling in the same repo.

**Same-repo directory collision avoidance (phase 5):** If `tentacle.py create <name>` finds that
the directory `<name>` already exists, it automatically creates `<name>-<uuid[:8]>` instead of
exiting with an error. The unique slug is printed to stderr and stored as `dir_name` in
`meta.json`. **All subsequent commands (`todo`, `swarm`, `complete`, `handoff`, etc.) must use
the printed slug** — `_validate_tentacle_name` resolves by exact directory name, so the logical
name passed to `create` will find the original (other session's) directory, not the slug.

**Migration cleanup:** When re-dispatching from a known git repo, `tentacle.py swarm` eagerly
removes legacy entries whose `name` matches, `tentacle_id` is absent or null, and whose `git_root` is
either `None` (old string-list artefacts with no repo identity) or equal to the current repo
when the new dispatch carries a `tentacle_id` (crash-then-upgrade: stale phase-4 dict entry for
the same repo that would otherwise keep blocking commits until TTL expiry).

**Step 2 — Git pre-commit / pre-push (primary enforcement)**

`hooks/check_subagent_marker.py` is called by both `hooks/pre-commit` and `hooks/pre-push`.
When the marker is present, auth-valid, within the 4-hour TTL, and its `git_root` matches the
repository where the git operation is running, it exits with code 1 and prints a diagnostic
message — blocking the git operation.

**Repo-scope check:** Each `active_tentacles` entry carries a `git_root` field. The hook
resolves the current repo's root with `git rev-parse --show-toplevel` and compares it against
the entry's `git_root`. If they differ, that entry does not block the operation. This prevents
a tentacle active in repo A from falsely blocking unrelated commits in repo B — the cross-repo
false-positive that existed before phase 4.

**Backward compatibility:** If a marker entry has no `git_root` (written by old code or
dispatched from a non-git directory), the hook conservatively blocks — same behavior as before.

**Mixed-format markers:** The format dispatch checks `all(isinstance(e, str) for e in active)`
— only a *pure* string-list triggers the legacy top-level `git_root` path. A mixed-format
marker (some string entries, some dict entries — possible when upgrading mid-flight) is routed
through the per-entry check; string entries inside such a list carry no repo identity and
conservatively block every repo, while dict entries are evaluated per-entry as usual.

> **Upgrade migration note:** Cross-repo isolation is **not retroactive** for in-flight
> old-format markers. If you upgrade while a tentacle is still active and the marker was
> written by old code (string-list `active_tentacles`, no per-entry `git_root`), that marker
> carries no repo identity and will continue to block **all** repos conservatively until the
> tentacle completes, the marker is cleared manually, or the 4-hour TTL expires.
>
> **Recommended action:** Before upgrading on a machine with active tentacles, run
> `tentacle.py complete <name>` for each active tentacle, then re-dispatch after upgrading.
> Or clear the marker immediately:
> ```bash
> rm ~/.copilot/markers/dispatched-subagent-active
> ```
> After clearing, re-dispatch any tentacles that still need to run — they will now write
> new-format entries with `git_root` and benefit from cross-repo isolation.

This is the **primary enforcement surface**: git hooks fire at the filesystem level for any
`git commit` or `git push` call, regardless of which agent spawned the process.

**Step 3 — `preToolUse` guard (defense-in-depth, secondary)**

`hooks/rules/subagent_guard.py` (`SubagentGitGuardRule`) checks the same marker on every
`preToolUse` event that contains a `git commit` or `git push` bash command. This provides a
second interception point when `preToolUse` does fire inside the subagent. However, it is
**not the primary path** — whether `preToolUse` events from the parent `hooks.json` propagate
into a delegated subagent context is undefined by the platform. Git hooks remain the reliable
enforcement surface. If the platform ever guarantees `preToolUse` propagation into
`task()`-spawned agents, `subagent_guard.py` could become the primary path and replace git hooks.

**Step 4 — Marker cleanup**

`tentacle.py complete <name>` removes the tentacle's entry from `active_tentacles`. The marker
file is deleted when the list becomes empty. The 4-hour TTL acts as a dead-man switch for
sessions that crash without calling `complete`.

For inspecting stale entries without completing a tentacle, use the `marker-cleanup` subcommand:

```bash
tentacle.py marker-cleanup           # dry-run: shows stale entries that would be removed
tentacle.py marker-cleanup --apply   # actually remove stale entries (per-entry TTL check)
```

Only entries whose per-entry timestamp exceeds the declared TTL are eligible for removal.
Live entries and entries with no timestamp are never touched.

### Enforcement scope

> **Local-only.** This enforcement covers local git operations on the machine where the tools
> are installed. It does **not** cover:
>
> - Cloud-hosted or remote-delegated agent runs (hooks.json is not copied to cloud environments)
> - Any environment where git hooks are not installed (`install.py --install-git-hooks`)
> - Manual filesystem operations that bypass git (direct file writes without committing)

### Known limitations

| Limitation | Detail |
|---|---|
| `preToolUse` non-inheritance | `preToolUse` hooks from the parent `hooks.json` may not fire inside `task()`-spawned subagents — platform-level behavior, not fixable here. Git hooks remain the reliable surface. |
| Same-repo multi-orchestrator | Supported (phase 5): each tentacle gets a stable `tentacle_id` at create time. Two instances with the same logical name in the same repo each hold a separate marker entry and `complete` removes only the matching identity. **Caveat: working-tree / git-index side effects are not isolated** — concurrent tentacles in the same repo that touch the same files will still produce conflicts in the shared working tree and index. |
| Cloud/remote agents | Hooks are local-only. Cloud-delegated or remote agent runs have no coverage. |
| `auto-update` does not reinstall git hooks | `auto-update-tools.py` updates tools-repo files but does **not** re-run `--install-git-hooks` in registered repos. It prints a warning when hook files change. Users must re-run `install.py --install-git-hooks` manually to apply new hook logic in each protected repo. |

### Installing the git hooks

The git-level guard requires installation per repository:

```bash
# Install into the current repo's .git/hooks/
sk install --install-git-hooks
# fallback: python3 ~/.copilot/tools/install.py --install-git-hooks

# On Windows (PowerShell)
python "$env:USERPROFILE\.copilot\tools\install.py" --install-git-hooks
```

`install.py --install-git-hooks` also sets `core.hooksPath = .git/hooks` in the repository
config to ensure the hooks fire even when a project-level override is present.

> **Non-interactive mode:** When run without a terminal (e.g., from a script or CI), if a hook
> already exists in `.git/hooks/` and differs from the source, installation is skipped with a
> warning. Back up the existing hook and re-run interactively to overwrite it.

After tool updates (`git pull` or `sk update --force`), re-run
`sk install --install-git-hooks` to refresh the hook scripts in `.git/hooks/`. `sk update`
does **not** perform this reinstallation automatically — it cannot safely enumerate every repo
where hooks are installed. When hook files change, it emits these three warnings to stderr:

```
[sk-update] ⚠️  Git hook scripts updated — installed per-repo hooks are NOT automatically refreshed.
[sk-update] ⚠️  ACTION REQUIRED to pick up the cross-repo isolation fix (and future hook changes):
[sk-update] ⚠️    Re-run in EVERY protected repo: sk install --install-git-hooks
```

### Fail-open behavior

Enforcement surfaces are **selectively fail-open**. The behavior differs by error type:

| Condition | Behavior |
|-----------|----------|
| Missing marker file | allow (no false positives) |
| Stale marker (age ≥ 4 hours) | allow |
| HMAC auth failure (tampered or written without secret) | allow |
| Missing or unparseable timestamp | allow |
| Empty `active_tentacles` list (zombie marker) | allow |
| Exception during entry processing or repo-scope check | **conservative (block)** — errors in parsing `active_tentacles` entries or the `git_root` comparison fall through to blocking to avoid accidentally unblocking an active session |

> **Note:** The `is_marker_fresh()` docstring and implementation are now aligned: auth/parse
> failures are fail-open (return `False` → allow), while repo-scope check exceptions are
> fail-conservative (`pass` → keep blocking). See `hooks/check_subagent_marker.py`.

To clear a stuck marker manually:

```bash
sk tentacle complete <name>
# or delete the marker file directly:
rm ~/.copilot/markers/dispatched-subagent-active
```

## Host Scope

Hook deployment is **Copilot CLI only** (`~/.copilot/hooks/`). Claude Code does not support
the Copilot CLI hook runner format (`hook_runner.py` / `hooks.json`). The global enforcement
hooks documented here run exclusively inside Copilot CLI sessions.

For project-level hooks (`.github/hooks/`) that enforce coding standards, commit gates, and
TDD pipelines, see [docs/SKILLS.md — Hook Templates](SKILLS.md) and the `hook-creator` skill.
Those hooks are registered via `hooks.json` / `review-policy.json` in the project repo and
are also **Copilot CLI only**.

## Load Awareness

The unified hook runner (`hook_runner.py`) is **not** a significant context-load contributor: it
runs as a single Python process per event type, outside the LLM context window, and its output
(markers, audit log entries) does not increase prompt tokens.

Context load problems come from instruction surfaces and skill duplication, not hooks:

| Root cause | Effect | Remedy |
|---|---|---|
| Skill deployed at both `~/.copilot/skills/` and `.github/skills/` | Skill appears twice in catalog; Copilot deduplicates by name but extra copy adds noise | Remove project copy once globally deployed — see [docs/SKILLS.md](SKILLS.md#meta-skill-rollout--global-vs-project-scope) |
| Instruction file with `applyTo: '**/*'` | File is injected into every context, including trivial ones | Narrow `applyTo` to the file patterns that actually need the instruction |
| Same instruction deployed at both user-level and project-level | Duplicate injection on every context | Remove the project copy; keep only the user-level one |

Hook rules themselves follow a **minimal-output-first** discipline: `deny()` and `info()` outputs
are kept short; verbose details are written to `~/.copilot/markers/audit.jsonl` only, not surfaced
as inline context. If a hook rule needs to escalate (e.g., the briefing gate hasn't fired), it
blocks with a single concise message — it does not dump session history into the prompt.
