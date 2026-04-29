# Copilot Instructions — copilot-session-knowledge

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
python3 ~/.copilot/tools/briefing.py "your task description"
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

## Testing

```bash
python3 test_security.py    # 9 security tests (SQL injection, pickle, locks, paths)
python3 test_fixes.py       # 65 tests (noise filter, sub-agent, launchd, DB health)
```

Python tests use a custom `test()` harness (not pytest). For `browse-ui/` or CI changes, also run the relevant `pnpm` gates (`typecheck`, `lint`, `test`, `build`, and `test:e2e` when intentionally validating that surface). Keep GitHub Actions CI green.

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
- **Hooks** — Copilot CLI only; `hook_runner.py` is the single entry point; fail-open

For the full script inventory, data pipeline, host scope table, provider package, and all coding conventions: **[docs/ARCHITECTURE.md](../docs/ARCHITECTURE.md)**
