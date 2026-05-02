# copilot-session-knowledge — Agent Instructions

> Canonical root instruction surface for all AI agents (Claude Code, Codex, Amp, Factory).
>
> **Full rules:** [docs/AGENT-RULES.md](docs/AGENT-RULES.md) · **Copilot CLI runtime:** [.github/copilot-instructions.md](.github/copilot-instructions.md)
>
> **Drift-lock:** `docs/AGENT-RULES.md` is the canonical source for all agent rules. This file is a concise summary — when in doubt, defer to `docs/AGENT-RULES.md`.

## Mandatory Rules

1. **Investigate before acting** — read target files with `grep`/`glob`/`view` before any edit; never modify without reading first.
2. **Briefing before complex tasks** — run `python3 ~/.copilot/tools/briefing.py "<task>"` for tasks touching >1 file.
3. **Test after every change** — run `python3 test_security.py` and/or `python3 test_fixes.py` after Python edits; do not mark complete until tests pass.
4. **Verify before committing** — AST-parse every modified `.py` file; run both test suites; `git diff --stat` before commit.
5. **Sub-agent model selection** — use `claude-sonnet-4.6` for code generation; `claude-opus-4.6` for security audits; never dispatch sub-agents with the default (haiku) model for code changes.
6. **No guessing** — verify table names, function signatures, and file paths from source; never assume.
7. **Docs output quality** — distinguish Facts / Interpretation / Actions / Verification evidence; never present inference as fact; every action must include the executable command.
8. **Tentacle execution obligations** — when dispatched inside a tentacle: (a) read bundle files first, (b) stay in declared scope, (c) mark todos done with `tentacle.py todo <name> done <index>`, (d) do NOT run `git commit`/`git push`, (e) write a structured handoff with explicit `--status` (`DONE`, `BLOCKED`, `TOO_BIG`, `AMBIGUOUS`, or `REGRESSED`) via `tentacle.py handoff <name> "<summary>" --status <STATUS> [--changed-file <path>] --learn` before stopping.

See [docs/AGENT-RULES.md](docs/AGENT-RULES.md) for the complete rule text and hook-enforcement table.

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
- **Hooks** — Copilot CLI only; `hook_runner.py` is the single entry point; fail-open; `pre-commit` also runs scoped Ruff + Prettier cleanliness checks (fail-open when tooling absent)
- **Tentacle marker-cleanup** — use `tentacle.py marker-cleanup [--apply]` to inspect/remove stale dispatched-subagent marker entries without completing a tentacle

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
- ALWAYS run `briefing.py` before starting work on unfamiliar code

## Hook Enforcement (Principle)

All hooks **fail-open**: a hook crash or absence never blocks the agent. Hook failures are logged; work proceeds. See [docs/AGENT-RULES.md](docs/AGENT-RULES.md) for the full enforcement table.
