# Agent Rules

> Canonical reference for AI agent behavior rules in copilot-session-knowledge.
>
> These rules are **non-negotiable** and apply to every agent — main session, sub-agent, explore, task, or general-purpose. They are also enforced via `.github/copilot-instructions.md` (injected into every Copilot CLI session) and partially enforced at the hook level.

## Rule 1 — Investigate Before Acting

**NEVER modify code without reading it first.** Before any edit:

1. Use `grep` / `glob` / `view` / LSP tools to read the target file(s)
2. Understand the existing logic, dependencies, and callers
3. Check related files that may be affected by your change
4. Only then make your edit

```
❌ BAD:  User says "fix the search" → immediately edit query-session.py
✅ GOOD: User says "fix the search" → grep for search functions → view the code → check callers → edit
```

## Rule 2 — Briefing Before Complex Tasks

Before starting any task that touches >1 file or involves unfamiliar code:

```bash
python3 ~/.copilot/tools/briefing.py "your task description"
```

This surfaces past mistakes, proven patterns, and relevant decisions. Skip only for trivial changes (typo fix, renaming, formatting).

The `auto-briefing` hook fires automatically at `sessionStart` and writes a marker. If it has not fired (e.g., in a sub-agent context), run `briefing.py` manually before editing.

## Rule 3 — Test After Every Change

After modifying any Python file, run the relevant tests:

```bash
python3 test_security.py    # If touching: embed.py, sync-knowledge.py, watch-sessions.py, learn.py
python3 test_fixes.py       # If touching: any script
```

Do NOT mark a task complete until the relevant tests pass. If you hit a baseline failure, separate pre-existing breakage from regressions you introduced before proceeding.

Python validation runs through `run_all_tests.py`, but individual files use a mix of the custom `test()` helper and `unittest`/`test_*` style. The repo also has GitHub Actions CI and browse-ui quality gates (`pnpm typecheck`, `pnpm lint`, `pnpm format:check`, `pnpm test`, `pnpm build`); run the surfaces relevant to the files you changed.

## Rule 4 — Verify Before Committing

Before `git commit`:
1. `python3 -c "import ast; ast.parse(open('file.py').read())"` for every modified `.py` file
2. Run both test suites (Rule 3)
3. `git diff --stat` to review what you are about to commit

The `syntax_gate.py` preToolUse hook catches syntax errors in `edit`/`create` payloads before they land, but AST-parse verification before commit is a second safety net.

## Rule 5 — Sub-Agent Model Selection

When dispatching sub-agents via the `task` tool, use the appropriate model:

| Task type | Minimum model | Example |
|-----------|--------------|---------|
| Code generation | `claude-sonnet-4.6` | Writing/modifying Python scripts |
| Code review | `claude-sonnet-4.6` | Reviewing changes for bugs |
| Security audit | `claude-opus-4.6` | Auth, data handling, injection risks |
| Exploration | `claude-haiku-4.5` (default OK) | Finding files, reading code |
| Documentation | `claude-sonnet-4` or `haiku` | Writing docs, README |

```
❌ task(agent_type="general-purpose", prompt="fix the search bug...")   # default haiku model!
✅ task(agent_type="general-purpose", model="claude-sonnet-4.6", prompt="fix the search bug...")
```

## Rule 6 — No Guessing

- Don't assume table names — check with `sqlite3 ... ".tables"` or read `migrate.py`
- Don't assume function signatures — use `grep` or LSP to verify
- Don't assume file paths — use `glob` to find them
- If unsure about behavior, write a small test or read the source

---

## Hook Enforcement

These rules are partially enforced at the tool level:

| Rule | Hook | Enforcement |
|------|------|-------------|
| Briefing before edits | `enforce-briefing` (preToolUse) | Blocks `edit`/`create`/`bash` until briefing marker is present |
| Learn after code edits | `enforce-learn` (preToolUse) | Blocks `git commit` / `task_complete` after ≥3 code edits without `learn.py` |
| Tentacle for broad changes | `tentacle-enforce` (preToolUse) | Blocks edits when ≥3 files across ≥2 modules without tentacle setup |
| No git ops in sub-agents | `subagent-git-guard` (preToolUse + git hooks) | Blocks `git commit`/`git push` while dispatched-subagent marker is active |
| Syntax errors | `syntax_gate` (preToolUse) | Blocks `.py` edit/create payloads that fail `py_compile` |

> Full hook rule inventory: **[docs/HOOKS.md](HOOKS.md)**

---

## Copilot CLI Enforcement Surface

The file `.github/copilot-instructions.md` injects these rules into every Copilot CLI session context. That file is the **runtime enforcement surface** — keep it in sync with this document. Changes to agent rules should be reflected in both places.

For Claude Code, equivalent guidance lives in `CLAUDE.md` (project root or user home) and `.claude/` instruction files.
