# Hooks

> Copilot CLI enforcement hooks for session knowledge, quality gates, and workflow guards.

## Architecture

All hooks are dispatched through a **single entry point** — `hook_runner.py` — instead of
separate scripts per event. One Python process fires per event instead of up to 11.

```
hooks/
  hook_runner.py         # Unified dispatcher: reads stdin, routes to rules/
  marker_auth.py         # HMAC-signed marker auth (shared by all hooks)
  rules/                 # Rule modules — one class per guard
    __init__.py          # Rule base class + get_rules_for_event()
    common.py            # Shared constants (MARKERS_DIR, CODE_EXTENSIONS, …)
    briefing.py          # auto-briefing + enforce-briefing rules
    learn_gate.py        # enforce-learn rule
    learn_reminder.py    # learn-reminder rule
    tentacle.py          # tentacle-enforce + tentacle-suggest rules
    edit_tracker.py      # track-edits + test-reminder rules
    error_kb.py          # error-kb rule
    integrity.py         # integrity check rule
    session_lifecycle.py # session-end rule
    subagent_guard.py    # subagent git guard rule
    syntax_gate.py       # [added in this release] syntax/lint gate rule
  references/
    docs-reminder.py     # Hook template: remind to update docs after edits
```

Legacy standalone scripts (`auto-briefing.py`, `enforce-briefing.py`, `enforce-learn.py`,
`enforce-tentacle.py`, `tentacle-suggest.py`, `test-after-edit.py`, `track-bash-edits.py`,
`error-search-kb.py`, `verify-integrity.py`, `session-end.py`) are kept for reference and
direct invocation but are **not registered** in `hooks.json`. All active enforcement runs
through `hook_runner.py`.

---

## Registered Hooks (`hooks.json`)

All five event types are handled by `hook_runner.py`:

| Event | Command | Timeout | Notes |
|-------|---------|---------|-------|
| `sessionStart` | `hook_runner.py sessionStart` | 20 s | Auto-briefing + integrity check |
| `sessionEnd` | `hook_runner.py sessionEnd` | 5 s | Marker cleanup |
| `preToolUse` | `hook_runner.py preToolUse` | 10 s | Briefing + learn + tentacle enforcement |
| `postToolUse` | `hook_runner.py postToolUse` | 10 s | Edit tracking + reminders + suggestions |
| `errorOccurred` | `hook_runner.py errorOccurred` | 10 s | KB error search |

---

## Rule Inventory

| File | Rule class | Event | Tools covered | Purpose | Default? |
|------|-----------|-------|---------------|---------|---------|
| `rules/briefing.py` | `AutoBriefingRule` | `sessionStart` | *(all)* | Auto-runs `briefing.py`; writes HMAC-signed marker | Y |
| `rules/integrity.py` | `IntegrityRule` | `sessionStart` | *(all)* | SHA256 manifest check for hook tamper detection | Y |
| `rules/session_lifecycle.py` | `SessionEndRule` | `sessionEnd` | *(all)* | Deletes this-session markers; writes `session.log` | Y |
| `rules/briefing.py` | `EnforceBriefingRule` | `preToolUse` | `edit`, `create`, `bash` | Blocks edits until briefing marker is present | Y |
| `rules/learn_gate.py` | `EnforceLearnRule` | `preToolUse` | `edit`, `create`, `bash`, `task_complete` | Blocks `git commit` / `task_complete` after ≥3 code edits without `learn.py` | Y |
| `rules/tentacle.py` | `TentacleEnforceRule` | `preToolUse` | `edit`, `create`, `bash` | Blocks edits when ≥3 files across ≥2 modules without tentacle setup | Y |
| `rules/subagent_guard.py` | `SubagentGitGuardRule` | `preToolUse` | `bash` | Defense-in-depth: blocks `git commit`/`git push` inside dispatched subagent | Y |
| `rules/edit_tracker.py` | `TrackEditsRule` | `postToolUse` | `bash` | Runs `git status` after bash to detect all file writes (language-agnostic) | Y |
| `rules/edit_tracker.py` | `TestReminderRule` | `postToolUse` | `edit`, `create`, `bash` | Reminds to run tests after ≥3 Python file edits | Y |
| `rules/learn_reminder.py` | `LearnReminderRule` | `postToolUse` | `bash`, `task_complete` | Reminds to record learnings; creates marker when `learn.py` runs | Y |
| `rules/tentacle.py` | `TentacleSuggestRule` | `postToolUse` | `edit`, `create`, `bash` | Suggests tentacle orchestration at ≥3 files / ≥2 modules threshold | Y |
| `rules/error_kb.py` | `ErrorKBRule` | `errorOccurred` | *(all)* | Auto-searches knowledge base for past solutions when an error fires | Y |
| `rules/syntax_gate.py` | *(new)* | `preToolUse` | `edit, create` | Blocks `edit`/`create` on `.py` files when the post-edit content has a `SyntaxError` (uses `py_compile`) | Y |

---

## Standalone Helper Scripts (not in hooks.json)

| File | Purpose | Event / trigger |
|------|---------|----------------|
| `hook_runner.py` | Unified dispatcher — entry point for all hook.json entries | All events |
| `marker_auth.py` | HMAC-signed marker creation / verification | Imported by all rules |
| `lint-skills.py` | Validates `.agent.md` / `SKILL.md` frontmatter; called by `pre-commit` git hook | `git pre-commit` |
| `check_subagent_marker.py` | Blocks `git commit`/`git push` in dispatched-subagent mode | `git pre-commit`, `git pre-push` |
| `copilot-cli-healer-check.py` | Detects stale Copilot CLI state and flags it at session start (companion to `copilot-cli-healer.py`) | `sessionStart` |
| `learn-reminder.py` | Legacy standalone learn-reminder hook. Superseded by `rules/learn_reminder.py` but retained for compatibility. | `postToolUse` |
| `auto-briefing.py` | Legacy standalone auto-briefing (superseded by `rules/briefing.py`) | `sessionStart` |
| `enforce-briefing.py` | Legacy standalone enforce-briefing (superseded by `rules/briefing.py`) | `preToolUse` |
| `enforce-learn.py` | Legacy standalone learn gate (superseded by `rules/learn_gate.py`) | `preToolUse` |
| `enforce-tentacle.py` | Legacy standalone tentacle enforce (superseded by `rules/tentacle.py`) | `preToolUse` |
| `tentacle-suggest.py` | Legacy standalone tentacle suggest (superseded by `rules/tentacle.py`) | `postToolUse` |
| `test-after-edit.py` | Legacy standalone test reminder (superseded by `rules/edit_tracker.py`) | `postToolUse` |
| `track-bash-edits.py` | Legacy standalone bash edit tracker (superseded by `rules/edit_tracker.py`) | `postToolUse` |
| `error-search-kb.py` | Legacy standalone KB error search (superseded by `rules/error_kb.py`) | `errorOccurred` |
| `verify-integrity.py` | Legacy standalone integrity check (superseded by `rules/integrity.py`) | `sessionStart` |
| `session-end.py` | Legacy standalone session-end cleanup (superseded by `rules/session_lifecycle.py`) | `sessionEnd` |
| `references/docs-reminder.py` | Template hook: reminds to update docs after edits | `postToolUse` |

---

## Installation

```bash
# Deploy Copilot CLI global hooks
python3 ~/.copilot/tools/install.py --deploy-hooks

# Lock hooks against AI modification (uses OS immutable flags)
python3 ~/.copilot/tools/install.py --lock-hooks

# Unlock for updates
python3 ~/.copilot/tools/install.py --unlock-hooks

# Install git-level subagent guard (per repo)
python3 ~/.copilot/tools/install.py --install-git-hooks
```

The `hooks.json` file lives at `.github/hooks/hooks.json` and is read by Copilot CLI.
`--deploy-hooks` copies it to `~/.copilot/hooks/hooks.json`.

---

## Compliance Notes

- **stdlib-only**: All standalone hook scripts (`hooks/*.py`) use only Python standard library.  
  `rules/*.py` modules are loaded inside `hook_runner.py` — no external pip packages required.  
  Exception: `hook_runner.py` itself includes the Windows UTF-8 reconfigure block at module level.
- **Exit codes**: `hook_runner.py` exits `0` to allow, or writes a `deny` JSON object to stdout
  to block (Copilot CLI interprets non-empty stdout with `decision: deny` as a block).
- **Windows UTF-8**: All standalone `hooks/*.py` scripts include the `if os.name == "nt"` UTF-8
  reconfigure block at module top. `rules/*.py` are not standalone executables — encoding is
  configured once by `hook_runner.py`.
- **Fail-open**: Exceptions in any rule silently pass; individual rule crashes never block the agent.
