# Copilot Instructions — copilot-session-knowledge

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

**Supporting tools:**
- `embed.py` — Optional semantic search via external embedding APIs (OpenAI, Fireworks, etc.) with TF-IDF fallback
- `claude-adapter.py` — Parses Claude Code JSONL sessions into the common DB format
- `sync-knowledge.py` — Merges `knowledge.db` files across environments (Windows ↔ WSL)
- `migrate.py` — Versioned schema migrations via a `schema_version` table
- `install.py` / `setup-project.py` — Deploy SKILL.md and inject into project/global AI instructions

**Central database:** `~/.copilot/session-state/knowledge.db` — SQLite with FTS5, WAL journal mode, and optional vector embeddings.

## Conventions

- **Pure stdlib Python 3.10+** — zero pip dependencies required. `scikit-learn` and embedding API keys are optional.
- **Every script is standalone** — no shared library or package imports between scripts. Each script duplicates its own DB path constants, encoding fix, etc.
- **Windows encoding fix** — every script starts with the same `os.name == "nt"` block to reconfigure stdout/stderr to UTF-8. Preserve this pattern in new scripts.
- **Parameterized SQL only** — all user input uses `?` placeholders. Never interpolate strings into SQL.
- **FTS5 query sanitization** — strip FTS5 operators (`OR`, `AND`, `NOT`, `NEAR`, `*`, `"`) before passing to MATCH. See `_sanitize_fts_query()` in `query-session.py`.
- **JSON serialization only** — never use pickle. Legacy pickle detection exists but new code must use JSON/`struct.pack`.
- **Atomic lock files** — use `O_CREAT | O_EXCL` for process locks (no TOCTOU races).
- **Input limits** — title ≤ 200 chars, content ≤ 10K chars, FTS queries ≤ 500 chars, paths ≤ 256 chars.
- **Cross-platform paths** — use `Path.home()` and `pathlib` throughout. Handle WSL path differences explicitly.
- **DB migrations** — add new migrations to the `MIGRATIONS` list in `migrate.py` with incrementing version numbers and a descriptive name.
