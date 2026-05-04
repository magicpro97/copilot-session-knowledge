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
    tentacle.py           # Tentacle enforce + suggest (merged)
    edit_tracker.py       # Track bash edits + test reminder (merged)
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
| `subagent-stop-cleanup` | agentStop + subagentStop | Best-effort dispatched-subagent marker cleanup from stop-event payload hints |
| `enforce-briefing` | preToolUse | Blocks edit/create/bash-writes until briefing done |
| `enforce-learn` | preToolUse | Blocks git commit AND task_complete without learn.py |
| `tentacle-enforce` | preToolUse | Blocks (deny) edits once ≥3 files across ≥2 modules are reached without tentacle setup. **Session-state paths** (`~/.copilot/session-state/`) are always exempt — `/research` outputs and other session artifacts are never blocked. **Bash redirects** are only flagged when the destination is a real source file; redirects to `.txt`, `.log`, `/dev/null`, or session-state paths are allowed. The deny message contains convention-level guidance: if you are the **orchestrator**, follow the runtime-bundle workflow — `tentacle.py create <name> --scope "<paths>" --desc "<desc>" --briefing` → `tentacle.py todo <name> add "<task>"` → `tentacle.py swarm <name> --agent-type general-purpose --model claude-sonnet-4.6 --briefing` (bundle is default); if you are a **dispatched sub-agent**, read the bundle manifest first, stay within your declared scope, write any scope gaps to `handoff.md`, and by convention avoid `git commit`/`git push`. |
| `subagent-git-guard` | preToolUse | **Defense-in-depth**: blocks `git commit`/`git push` bash commands when the `dispatched-subagent-active` marker is fresh. This is a secondary surface — **not** the primary enforcement path (see §Dispatched-Subagent Git Guard below). Whether `preToolUse` fires inside a delegated subagent context is not guaranteed by the platform. |
| `syntax-gate` | preToolUse | Blocks `edit`/`create` payloads that introduce Python syntax errors — applies the proposed change in memory and runs `py_compile`; fail-open on non-`.py` paths and missing files. Catches errors before they land on disk. |
| `block-edit-dist` | preToolUse | Blocks `edit`/`create` targeting `browse-ui/dist/`. These are build artifacts — run `cd browse-ui && pnpm build` instead. |
| `pnpm-lockfile-guard` | preToolUse | Blocks staging `browse-ui/package.json` changes without a matching `pnpm-lock.yaml` update. Prevents lockfile drift. |
| `block-unsafe-html` | preToolUse | Blocks `dangerouslySetInnerHTML` usage in `.ts`/`.tsx` files without `DOMPurify.sanitize()` or the `<Highlight>` component. |
| `track-edits` | postToolUse | Detects file changes via `git status` (language-agnostic) |
| `learn-reminder` | postToolUse | Reminds to record learnings after task_complete; also surfaces [docs/SYNC-MATRIX.md](SYNC-MATRIX.md) for docs/memory follow-ups |
| `test-reminder` | postToolUse | Reminds to run tests after 3+ Python file edits |
| `tentacle-suggest` | postToolUse | Suggests tentacle when edits reach ≥3 files across ≥2 modules (same threshold as tentacle-enforce); also references [docs/SYNC-MATRIX.md](SYNC-MATRIX.md) |
| `nextjs-typecheck-reminder` | postToolUse | Reminds to run `pnpm typecheck` after editing `.ts`/`.tsx` files in `browse-ui/` |
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

The `/v2/chat` operator console does **not** introduce a new hook class. Existing guardrails already cover it:

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
python3 ~/.copilot/tools/install.py --deploy-hooks       # Deploy Copilot CLI hooks
python3 ~/.copilot/tools/install.py --lock-hooks          # Lock (AI can't modify)
python3 ~/.copilot/tools/install.py --unlock-hooks        # Unlock for updates
python3 ~/.copilot/tools/install.py --install-git-hooks   # Install pre-commit/pre-push into current repo
```

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
python3 ~/.copilot/tools/install.py --install-git-hooks

# On Windows (PowerShell)
python "$env:USERPROFILE\.copilot\tools\install.py" --install-git-hooks
```

`install.py --install-git-hooks` also sets `core.hooksPath = .git/hooks` in the repository
config to ensure the hooks fire even when a project-level override is present.

> **Non-interactive mode:** When run without a terminal (e.g., from a script or CI), if a hook
> already exists in `.git/hooks/` and differs from the source, installation is skipped with a
> warning. Back up the existing hook and re-run interactively to overwrite it.

After tool updates (`git pull` or `auto-update-tools.py --force`), re-run
`--install-git-hooks` to refresh the hook scripts in `.git/hooks/`. `auto-update-tools.py`
does **not** perform this reinstallation automatically — it cannot safely enumerate every repo
where hooks are installed. When hook files change, it emits these three warnings to stderr:

```
[sk-update] ⚠️  Git hook scripts updated — installed per-repo hooks are NOT automatically refreshed.
[sk-update] ⚠️  ACTION REQUIRED to pick up the cross-repo isolation fix (and future hook changes):
[sk-update] ⚠️    Re-run in EVERY protected repo: python3 ~/.copilot/tools/install.py --install-git-hooks
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
python3 ~/.copilot/tools/tentacle.py complete <name>
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
