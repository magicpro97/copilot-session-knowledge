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

Do NOT mark a task complete until tests pass. Pre-existing failures (7 in test_fixes.py) are acceptable — new failures are not.

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

There is no build step, linter, or CI pipeline. Tests use a custom `test()` harness (not pytest).

## Architecture

A set of standalone Python CLI scripts that index AI coding sessions (Copilot CLI, Claude Code) into a SQLite FTS5 database and provide search/briefing capabilities across sessions.

**Data pipeline:**
1. `build-session-index.py` — Scans session `.md`/`.jsonl` files → SQLite FTS5 documents
2. `extract-knowledge.py` — Classifies documents into 7 categories (mistake, pattern, decision, tool, feature, refactor, discovery) using regex heuristics, deduplicates by content hash
3. `query-session.py` / `briefing.py` — Search and retrieve from the knowledge base
4. `watch-sessions.py` — Polls for file changes, triggers incremental re-indexing
5. `learn.py` — Manual knowledge entry (CLI interface for agents to record learnings)
6. `tentacle.py` — Multi-agent orchestration: `create` → `todo add` → `bundle` (optional isolated context) → `swarm [--bundle]` → `complete` (verification/closure). Operator view: `tentacle.py status`. Delegated-agent recall prefers structured `[KNOWLEDGE EVIDENCE]` from `briefing.py --task <id> --json` with `--pack` fallback; `--for-subagent` remains manual compatibility

**Supporting tools:**
- `embed.py` — Optional semantic search via external embedding APIs (OpenAI, Fireworks, etc.) with TF-IDF fallback
- `claude-adapter.py` — Parses Claude Code JSONL sessions into the common DB format
- `sync-knowledge.py` — Merges `knowledge.db` files across environments (Windows ↔ WSL)
- `sync-config.py` — Stores one optional `connection_string` in `~/.copilot/tools/sync-config.json` (`--setup`, `--setup-env`, `--status --json`)
- `sync-daemon.py` — Local-first push/pull runtime (`--once`, `--daemon`, `--interval`, `--push-only`, `--pull-only`) with backlog-aware adaptive sync limits, multi-page pull per cycle, and post-pull local `knowledge_fts` / `ke_fts` refresh; no connection string = local-only idle
- `sync-status.py` — Local sync diagnostics (`--json`, `--watch-status`, `--health-check`, `--audit`)
- `auto-update-tools.py` / `sk-update` — Runtime operator surface (`--restart-watch`, `--watch-status`, `--health-check`, `--audit-runtime`)
- `sync-gateway.py` — **Reference/mock only** gateway contract (`/sync/push`, `/sync/pull`, `/healthz`), not production authority
- `migrate.py` — Versioned schema migrations via a `schema_version` table
- `install.py` / `setup-project.py` — Deploy SKILL.md and inject into project/global AI instructions

**Enforcement hooks** (`hooks/` — cross-platform Python, deployed to `~/.copilot/hooks/`):
- Unified `hook_runner.py` handles `sessionStart`, `sessionEnd`, `preToolUse`, `postToolUse`, `agentStop`, `subagentStop`, `errorOccurred`
- Active guards: briefing + learn + tentacle enforcement, edit tracking, integrity checks, syntax/XSS/dist/lockfile gates, error KB lookup
- `agentStop` / `subagentStop` perform best-effort dispatched-subagent marker cleanup from stop payload hints (tentacle id/name)
- Hooks are protected with OS immutable flags (`install.py --lock-hooks`)

**Central database:** `~/.copilot/session-state/knowledge.db` — SQLite with FTS5, WAL journal mode, and optional vector embeddings.

## Conventions

- **Pure stdlib Python 3.10+** — zero pip dependencies required. `scikit-learn` and embedding API keys are optional.
- **Every script is standalone** — no shared library or package imports between scripts. Each script duplicates its own DB path constants, encoding fix, etc.
- **Windows encoding fix** — every script starts with the same `os.name == "nt"` block to reconfigure stdout/stderr to UTF-8. Preserve this pattern in new scripts.
- **Parameterized SQL only** — all user input uses `?` placeholders. Never interpolate strings into SQL.
- **FTS5 query sanitization** — strip FTS5 operators (`OR`, `AND`, `NOT`, `NEAR`, `*`, `"`) before passing to MATCH. See `_sanitize_fts_query()` in `query-session.py`.
- **Memory JSON placement** — keep field envelopes stable:
  `query-session.py --task --export json` → `entries[]`,
  `briefing.py --task --json` → `tagged_entries[]` / `related_entries[]`,
  `briefing.py --pack` → `entries.<category>[]`.
- **Phase 4 read-surface metadata** — `snippet_freshness` is only `fresh|drifted|missing|unknown`;
  `related_entry_ids` must remain JSON ints, confidence-ranked and capped to top 3.
- **Phase 5 recall telemetry** — `recall_events` is lean telemetry (counts/IDs/output size only).
  `knowledge-health.py --recall` / `--recall --json` are recall-only outputs.
  `query-session.py --detail` is stateless `detail_open` logging (`hit_count=1` only when found; miss logs `hit_count=0`).
  Default `query-session` search telemetry must cover the full emitted surface, including later `sessions_fts` + knowledge-entry blocks.
- **JSON serialization only** — never use pickle. Legacy pickle detection exists but new code must use JSON/`struct.pack`.
- **Atomic lock files** — use `O_CREAT | O_EXCL` for process locks (no TOCTOU races).
- **Input limits** — title ≤ 200 chars, content ≤ 10K chars, FTS queries ≤ 500 chars, paths ≤ 256 chars.
- **Cross-platform paths** — use `Path.home()` and `pathlib` throughout. Handle WSL path differences explicitly.
- **Sync diagnostics surfaces** — keep browse sync status read-only (`/healthz` advertises `/api/sync/status`; `/api/sync/status` reports local queue/config/failure state).
- **Sync semantics** — local DB is authoritative for reads; remote sync is optional transport/storage, not a query-serving authority.
- **Sync connection contract** — `sync-config.py --setup` accepts only HTTP(S) gateway URLs; do not claim direct Postgres/libSQL DSN support in CLI core.
- **Provider rollout default** — recommend Neon (backing Postgres) + Railway (thin gateway host) as default rollout guidance, not a hard vendor lock.
- **Trend Scout automation surface** — keep `trend-scout.py` scheduled/manual (`trend-scout.yml` or explicit CLI runs), not bound to interactive `preToolUse`/`postToolUse` hooks (avoid hook spam).
- **DB migrations** — add new migrations to the `MIGRATIONS` list in `migrate.py` with incrementing version numbers and a descriptive name.
