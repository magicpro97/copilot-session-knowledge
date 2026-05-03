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
3. **Mark todos as you complete them**: `python3 ~/.copilot/tools/tentacle.py todo <tentacle-name> done <index>`
4. **No git operations** — do NOT run `git commit` or `git push`; the orchestrator owns all git operations.
5. **Write a structured handoff before stopping**: `python3 ~/.copilot/tools/tentacle.py handoff <tentacle-name> "<summary>" --status <STATUS> [--changed-file <path>] --learn`
6. Use one of `DONE`, `BLOCKED`, `TOO_BIG`, `AMBIGUOUS`, or `REGRESSED` for `<STATUS>`. Add one `--changed-file` per modified file; omit it when no files changed. Handoff must list changed rules, source-of-truth file for each rule, and any remaining ambiguity.

See [docs/AGENT-RULES.md](../docs/AGENT-RULES.md) for the complete Rule 8 text.

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

> Full hook inventory: **[docs/AGENT-RULES.md](../docs/AGENT-RULES.md)** · **[docs/HOOKS.md](../docs/HOOKS.md)**

## Testing

```bash
python3 test_security.py    # 11 security tests (SQL injection, pickle, locks, paths)
python3 test_fixes.py       # 137 tests (noise filter, sub-agent, launchd, DB health)
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
- **Hooks** — Copilot CLI only; `hook_runner.py` is the single entry point; fail-open; `pre-commit` also runs scoped Ruff + Prettier cleanliness checks (fail-open when tooling absent)
- **Tentacle marker-cleanup** — use `tentacle.py marker-cleanup [--apply]` to inspect/remove stale dispatched-subagent marker entries without completing a tentacle

For the full script inventory, data pipeline, host scope table, provider package, and all coding conventions: **[docs/ARCHITECTURE.md](../docs/ARCHITECTURE.md)**
