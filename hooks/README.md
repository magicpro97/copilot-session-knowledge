# Hooks

> Copilot CLI enforcement hooks for session knowledge, quality gates, and workflow guards.
>
> **📖 Canonical reference: [docs/HOOKS.md](../docs/HOOKS.md)**

This file is a concise directory summary. For the full architecture, rule inventory, dispatched-subagent guard design, tamper protection details, and compliance notes, see [`docs/HOOKS.md`](../docs/HOOKS.md).

---

## Directory Layout

```
hooks/
  hook_runner.py          # Unified dispatcher — single entry point for all hook events
  marker_auth.py          # HMAC-signed marker auth (shared by all rules)
  rules/                  # Rule modules — one class per guard
    __init__.py           # Rule base class + get_rules_for_event()
    common.py             # Shared constants (MARKERS_DIR, CODE_EXTENSIONS, …)
    briefing.py           # auto-briefing + enforce-briefing
    learn_gate.py         # enforce-learn
    learn_reminder.py     # learn-reminder
    tentacle.py           # tentacle-enforce + tentacle-suggest
    edit_tracker.py       # track-edits + test-reminder
    error_kb.py           # error-kb
    integrity.py          # integrity check
    session_lifecycle.py  # session-end + subagentStop cleanup
    subagent_guard.py     # subagent git guard (defense-in-depth)
    syntax_gate.py        # Blocks .py edits that fail py_compile
    block_edit_dist.py    # Blocks direct edits to browse-ui/dist/
    pnpm_lockfile_guard.py # Blocks commit without pnpm-lock.yaml when package.json staged
    block_unsafe_html.py  # Blocks dangerouslySetInnerHTML without sanitization
    nextjs_typecheck.py   # Reminds to run pnpm typecheck after TS edits
  references/
    docs-reminder.py      # Hook template: remind to update docs after edits
```

Legacy standalone scripts (`auto-briefing.py`, `enforce-briefing.py`, `enforce-learn.py`, etc.)
are kept for reference and direct invocation but are **not registered** in `hooks.json`. All
active enforcement runs through `hook_runner.py`.

---

## Quick Reference

### Registered events

| Event | Purpose |
|-------|---------|
| `sessionStart` | Auto-briefing + integrity check |
| `sessionEnd` | Marker cleanup |
| `preToolUse` | Briefing/learn/tentacle/syntax/dist/lockfile/XSS guards |
| `postToolUse` | Edit tracking + learn/test/tentacle + Next.js typecheck reminders |
| `agentStop` | Best-effort dispatched-subagent marker cleanup |
| `subagentStop` | Best-effort dispatched-subagent marker cleanup |
| `errorOccurred` | KB error search |

### Installation

```bash
python3 ~/.copilot/tools/install.py --deploy-hooks        # Deploy to ~/.copilot/hooks/
python3 ~/.copilot/tools/install.py --lock-hooks          # Lock with OS immutable flags
python3 ~/.copilot/tools/install.py --unlock-hooks        # Unlock for updates
python3 ~/.copilot/tools/install.py --install-git-hooks   # Install pre-commit/pre-push (per repo)
```

📖 **Full rule inventory, architecture, and dispatched-subagent guard:** [docs/HOOKS.md](../docs/HOOKS.md)
