# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- `copilot-cli-healer.py`: cross-platform self-healer for Copilot CLI pkg dir corruption. Detects and cleans stale `.replaced-*` rename-backup dirs, `pkg/tmp/` partial downloads, and empty dummy version dirs. CLI: `--status`, `--check`, `--heal`, `--heal --dry-run`, `--update`, `--install-schedule`, `--uninstall-schedule`. Supports Windows Task Scheduler, macOS launchd, and Linux systemd. Concurrent-heal guard via `O_CREAT|O_EXCL` lock; Windows rmtree retry loop (3×500ms); stdlib-only.
- `hooks/copilot-cli-healer-check.py`: sessionStart hook that warns to stderr in <500ms when stale Copilot CLI pkg state is detected. Never auto-heals; notifies only.
- `launchd/com.copilot.cli-healer.plist`: macOS LaunchAgent template for daily healer runs (10:00).
- `install.py --install-healer` / `--uninstall-healer`: delegate to healer's schedule management.
- `auto-update-tools.py --doctor`: now reports Copilot CLI pkg health.
- `auto-update-tools.py --heal-copilot-cli`: new flag that invokes healer.
- `docs/copilot-cli-healer.md`: deep-dive doc — root cause, detection rules, heal procedure, schedule config, hook behaviour.
- README.md: new "Copilot CLI auto-heal" section under Troubleshooting with exact error string for Google-ability.
- `launchd/install-launchd.sh`: now installs `com.copilot.cli-healer` alongside existing agents.


- CONTRIBUTING.md with development guidelines
- CHANGELOG.md (this file)
- FAQ section in README
- Badges (license, python, platform, tests, dependencies)
- Table of Contents in README
- Collision-renamed tentacle bundles now include `slug` in `manifest.json` and a `Slug:` header in `session-metadata.md`, so sub-agents can always resolve the correct invocation name after a directory collision rename.
- `_normalize_posix_home()` in `check_subagent_marker.py` now handles Cygwin-style `/cygdrive/<drive>/...` paths in addition to Git Bash `/c/...` and WSL `/mnt/c/...` forms.

### Changed
- Restructured README from 577 lines to ~280 lines (moved details to docs/)
- Added "Why?" section for first-time readers
- Added Quick Start section (3 commands from zero to working)
- Cross-repo isolation git-root comparison now uses `Path.resolve()` canonical paths (`_same_canonical_root()`), so repos reached through dotdot paths or symlink-equivalent representations are correctly identified as the same repo — prevents stranded or duplicate dispatched-subagent markers.

### Fixed
- `tentacle.py` Unicode console failures on Windows: added standard UTF-8 stdout/stderr reconfigure block (matching `briefing.py`/`install.py` pattern), eliminating `UnicodeEncodeError` for emoji and non-ASCII output.
- `test_karpathy_skill_rollout.py` Windows path assertion failures: two assertions now use `.as_posix()` for cross-platform path comparison.
- POSIX home normalization in `check_subagent_marker.py` now uses explicit backslash (`chr(92)`) instead of `os.sep` for separator replacement, so the function produces correct Windows paths even when the Python runtime reports a POSIX `os.sep`. This fixes a latent bug that could have surfaced if the code were ever exercised in a POSIX-hosted Windows-emulation layer.
- Section 17g tests in `test_hooks.py` now build expected Windows paths with `chr(92)` instead of literal backslash string comparisons, making assertions valid on both Windows and non-Windows hosts.

## [1.3.1] - 2026-04-24

### Added
- **Quality gates** — new scripts and workflow for CI-level syntax enforcement:
  - `scripts/check_syntax.py`: `py_compile`-based syntax check for all Python files; exits non-zero on first failure. Intended for pre-commit and CI.
  - `run_all_tests.py`: single-command test runner; discovers and executes all `test_*.py` files, reports pass/fail counts, exits non-zero if any test fails.
  - `.github/workflows/ci.yml`: GitHub Actions workflow running syntax check + full test suite on every push and pull request.
  - `hooks/rules/syntax_gate.py`: preToolUse hook rule that py_compiles the post-edit/create content of `.py` files and blocks `edit`/`create` tools on `SyntaxError`. Does NOT run on bash / git commit.
- **Auto-update coverage expansion**: `auto-update-tools.py` now detects changes to `scripts/` and `hooks/rules/` directories alongside the existing detection rules; adds `syntax_gate.py` to the set of hook rules refreshed on `--skip-pull`.
- **I1** `test_retrieval.py`: added 4 assertions covering FTS5 snippet extraction edge-cases that were previously untested (empty-result snippet, multi-column snippet, snippet with special FTS5 characters, and snippet on a contentless table). All 4 pass.
- `test_hooks.py`: 16 new tests (Section 17) covering cross-repo isolation, TTL expiry, legacy-format migration, and HMAC end-to-end validation for the `tentacle-edits` marker.

### Changed
- **I2** Added inline comment on `cursor.lastrowid` in `browse/routes/eval.py` documenting the per-request-cursor / pre-commit atomicity invariant.
- `hooks/rules/common.py`: `get_module()` now accepts optional `repo_prefix` param for cross-repo distinction (backwards-compatible).
- `install.py`: `deploy_hooks()` now enumerates hook subdirectories, auto-discovering `hooks/rules/*.py` hook files.
- `hooks/hooks.json`: source copy updated alongside the syntax_gate rule registration in `hooks/rules/__init__.py`.
- `auto-update-tools.py`: VENDORED global skill dirs are now update-only (match BUILTIN_PROJECT_SKILLS rule) — no auto-create of absent dirs or asset files.

### Fixed
- **C1** `watch-sessions.py`: Misindented Windows UTF-8 stdout/stderr reconfigure block was inside `_is_pid_running()` body instead of module top-level, causing `SyntaxError` at import time. Moved block to module top-level with try/except guard matching pattern used by embed.py / learn.py.
- **hooks/rules/tentacle.py**: false-positive tentacle-enforce blocks on unrelated git repos. Edit list is now partitioned by `git_root` (resolved via `git rev-parse --show-toplevel`); per-root counters each carry their own TTL so stale entries from a previous session in a different repo never inflate the current session's module count.
- `test_indexing.py`: I6 boundary test changed input from 120s to 119s to eliminate timing race where `time.time()` drift pushed age above the boundary.
- `test_hooks.py`: 17f2 filter tightened to match only assignment lines (was matching kwarg `=` in usage lines, producing false failures).

## [1.3.0] - 2026-04-24

### Added — W0 (browse/ package foundation)
- `browse/` package: modular web UI replacing monolithic `browse.py`; `browse.py` is now a thin shim.
- `browse/core/`: `server.py` (ThreadingHTTPServer), `auth.py` (token auth), `csp.py` (nonce CSP), `fts.py` (FTS5 helpers), `registry.py` (route decorator), `templates.py` (base page), `static.py` (vendored assets), `projection.py` (PCA), `palette.py` (command palette commands), `streaming.py` (SSE helper).
- F1 `/` — home page with recent sessions.
- F2 `/sessions` — FTS-powered sessions list.
- F3 `/session/<id>` — session detail view.
- F8 dark-mode toggle baked into base template (`prefers-color-scheme` + localStorage).

### Added — W1 (knowledge + graph features)
- F4 command palette (`Ctrl+K`, ninja-keys) — global keyboard navigation on every page.
- F5 `/graph` — interactive Cytoscape.js knowledge-entity graph.
- F6 `/diff` — side-by-side checkpoint diff viewer.
- F7 `/search` + `/api/search` — FTS5 full-text search with facets (F7 rich UX).

### Added — W2 (analytics + streaming)
- F9 `/dashboard` + `/api/dashboard/stats` — aggregate stats and session health.
- F10 `/embeddings` + `/api/embeddings/points` — 2-D PCA scatterplot of knowledge vectors.
- F11 `/live` + `/api/live` (SSE) — real-time feed of new knowledge events.

### Added — W3 (session deep-dive + eval)
- F12 `/session/<id>/agents` — sub-agent dispatch log per session.
- F13 `/session/<id>/mindmap` — D3.js radial mind-map of session knowledge.
- F15 `/eval` + `POST /api/feedback` — thumbs-up/down eval/feedback for knowledge entries.
- Share: `share.js` — copy-link and screenshot-to-clipboard on every page.

### Tests
- Added 372 new browse tests across 12 feature suites (graph, agents, timeline, search_v2, dashboard, diff, mindmap, palette, share, live, eval, embeddings). Total browse tests: 412 pass.

## [1.2.0] - 2026-05-01

### Added
- `providers/` package: `SessionProvider` ABC + `Event` IR dataclass; `CopilotProvider` and `ClaudeProvider` implementations. `EventKind` literals: `user_msg`, `assistant_msg`, `tool_call`, `tool_result`, `diff`, `system`, `note`.
- Schema v7 — two-phase indexing: `event_offsets` table stores byte-offset seeks per session; `build-session-index.py` Phase 1 (metadata) / Phase 2 (full events) split.
- Schema v8 — `sessions_fts` contentless FTS5 table with BM25 ranking and column-scoped search (column indices: `session_id=0` UNINDEXED, `title=1`, `user_messages=2`, `assistant_messages=3`, `tool_names=4`).
- `query-session.py` (`qs`) new flags: `--in <column>` (column-scoped FTS), `--from <session-id>` (filter by session), `--snippet` (context snippet), `--session-raw <session-id>` (dump raw session events).
- `index-status.py` — index health inspection: row counts, FTS integrity check, event-offset coverage.
- `browse.py` — read-only local web UI; bound to `127.0.0.1` with token authentication and `Content-Security-Policy` headers.
- `checkpoint-diff.py --pager` with subprocess `shell=False` + basename allowlist for safe external pager invocation.

### Changed
- `build-session-index.py` refactored to use `SessionProvider` ABC with Phase 1 (session metadata) / Phase 2 (event content) split.
- `watch-sessions.py` adaptive polling tiers: 5 s (active), 30 s (idle), 300 s (dormant).
- `claude-adapter.py` backward-compatibility preserved via `ClaudeProvider` in the `providers/` package.

### Security
- `checkpoint-diff.py --pager`: `shell=False` + basename allowlist prevents RCE via hostile `$PAGER` environment variable.
- `browse.py`: bound to `127.0.0.1` only; token auth on every request; `Content-Security-Policy` blocks inline scripts.

### Tests
- Added `test_providers.py` (9), `test_indexing.py` (7), `test_retrieval.py` (11), `test_browse.py` (26), `test_diff_viewer.py` (19). All 83 new tests pass.

## [1.1.0] - 2026-04-18

### Added
- Unified hook runner architecture (1 process vs 11 per event)
- HMAC-signed markers for tamper-resistant counters
- Audit logging for all hook decisions (`~/.copilot/markers/audit.jsonl`)
- Dry-run mode (`HOOK_DRY_RUN=1`)
- Hook tamper protection with OS immutable flags
- SHA256 integrity manifest for hook files
- Bash bypass detection via `git status`

### Fixed
- Emoji detection on macOS (grep -P → Python regex)
- commit-gate iOS screenshot 30-min time constraint

### Changed
- Merged duplicate hook rules (tentacle enforce+suggest, track+test)
- Fail-open architecture — rule errors don't block the agent

## [1.0.0] - 2026-04-01

### Added
- Initial release
- SQLite FTS5 session indexing
- 7-type knowledge extraction (mistake, pattern, decision, tool, feature, refactor, discovery)
- Knowledge graph with auto-detected relations
- Palace concepts (wing/room) for hierarchical organization
- Semantic search with embedding API support (OpenAI, Fireworks, OpenRouter)
- TF-IDF fallback for offline semantic search
- Auto-update mechanism with smart diff pipeline
- LaunchAgent/systemd/Task Scheduler auto-start
- Claude Code adapter (JSONL → common format)
- Cross-environment sync (Windows ↔ WSL)
- Input validation and SQL injection prevention
- 74 tests (9 security + 65 functional)
