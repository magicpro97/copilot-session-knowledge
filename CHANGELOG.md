# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- **Toward-100 retro and health diagnostics wave** — additive diagnostic surfaces for understanding and tracking score gaps:
  - `retro.py` gains a `toward_100` top-level array in the JSON payload: a ranked list of sections scoring below 100, each with `section`, `score`, `gap` (100 − score), and metric-derived `barriers`. `--subreport behavior` is now a valid subreport target (local mode only). Diagnostics are derived from measured metrics and do **not** change the score formula or any existing subscore.
  - `knowledge-health.py` gains a `toward_100` object in the insights output: `top_gaps` list with per-metric gap entries and `total_gap` aggregate, surfacing `confidence_quality`, `learning_curve`, and `relation_density` as the largest measured health gaps (baseline: health `66.5`, measured on `2850fe12153f`).
  - `benchmark.py compare` output now includes `retro_gap` and `health_gap` (100 − score) for each snapshot and the improvement delta, making measurable progress explicit without manual arithmetic.
  - **Docs alignment** (this wave): `docs/OPERATOR-PLAYBOOK.md` now documents toward-100 gap diagnostics, the skills subscore verification-evidence discipline (sub-neutral 30.0 floor when `tentacle_verifications` is empty), and the benchmark compare as proof workflow. `docs/ARCHITECTURE.md` updated to reflect the additive `toward_100` payload field. `README.md` updated to mention `--subreport behavior`, `toward_100`, and gap-to-target fields.
  - **Recorded baseline on `2850fe12153f`:** repo retro `83.3`, local retro `61.5`, health `66.5`. Largest local gaps: retro skills `30.0`, behavior `37.5`; health `confidence_quality` `0.2`, `learning_curve` `6.1`, `relation_density` `10.3`. These are measured facts from a benchmark snapshot, not targets.
- **Layout A test consolidation** — `tests/` now consolidates non-canonical browse/UI tests (`test_visual_snapshot.py`, `test_session_export.py`, `test_ui_components.py`, `test_ui_foundation.py`) and supporting `fixtures/` + `snapshots/` subdirectories. All tests in `tests/` follow the repo-root invocation convention (`python3 tests/test_<name>.py`); see [`tests/README.md`](tests/README.md). Canonical root canary tests (`test_security.py`, `test_fixes.py`, `run_all_tests.py`) remain at the repo root unchanged.
- `benchmark.py`, migration v14 (`benchmark_snapshots`), and manual `.github/workflows/benchmark.yml` now provide commit-keyed snapshot recording plus artifact-friendly benchmark capture for measurable hardening work.
- `watch-sessions.py` now extracts affected session IDs from both Copilot and Claude layouts and passes them into `extract-knowledge.py` so incremental `ke_fts` sync can stay scoped to changed sessions.
- Runtime/tentacle safety coverage now includes TTL-boundary checks, concurrent marker stress, stop-hint token sanitization, and `session_lifecycle` edge cases in `test_tentacle_runtime.py` and `test_hooks.py`.
- `browse-ui/` — graph redesign + runtime hardening now reflected as shipped behavior:
  - `/v2/graph` is documented and tested as three truthful tabs: **Evidence** (`/api/graph/evidence`), **Similarity** (`/api/graph/similarity` neighbors-first with optional `/api/embeddings/points` orientation map), and **Communities** (`/api/graph/communities` deterministic summaries with singleton-noise suppression + drill-in to Evidence/Similarity).
  - `browse-ui/e2e/smoke.spec.ts` includes direct real-UUID session-detail smoke coverage and `/v2/graph` live-surface guards (no placeholder shell regressions).
  - Graph surface tests now freeze evidence truncation/error handling, similarity degraded/error/map-click flows, and embeddings projection contract behavior.
- `browse-ui/` — Phase 10 final shell polish + docs truthfulness:
  - Sidebar now supports a persistent collapsible rail mode with `⌘B / Ctrl+B` for density control.
  - Header now renders route-aware breadcrumbs + context text while keeping command discoverability (`⌘K`) and theme toggle in place.
  - `/v2` not-found state now provides integrated recovery actions (Sessions/Search) and command-palette guidance.
  - `docs/design/browse-ui/README.md` now reflects shipped status (design archive), rather than pre-implementation readiness wording.
- `browse-ui/` — Phase 9 docs acceptance:
  - `/v2/settings` shipped with theme + density preferences, direct `/healthz` diagnostics, and a keyboard shortcuts reference.
  - Global shortcut layer is mounted via shared `useKeyboardShortcuts` hook, including `G`-chord navigation, `?` → `/settings#shortcuts`, and insights tab switching with `1`/`2`.
  - Session detail compare/export upgrade is shipped via `CompareSheet` + `SessionPicker` and markdown download flow (`/session/{id}.md`).
- `browse-ui/` — Phase 8 feature delivery:
  - `/v2/insights` dashboard tab with KPI tiles, area/bar/donut charts, red-flag sessions table, and eval section.
  - `/v2/insights` live feed tab with SSE streaming, pause/resume controls (intentional drop-on-pause semantics), and connection-status badge.
  - `/v2/graph` relationships tab with force-directed canvas, entity/category filters, and node detail panel.
  - `/v2/graph` clusters tab with 2-D scatter canvas, category filtering, legend/point selection, and projection-unavailable handling.
- `browse-ui/` — Phase 8 integrated acceptance:
  - `/v2/graph` now mounts `ClustersTab` from `graph/page.tsx` so the shipped clusters view is reachable.
- Repo quality gates now lint `scripts/` and `tests/test_browse_search_v2.py`, while browse/search tests enforce concrete latency/performance budgets instead of documentation-only expectations.
  - `/v2/insights` now mounts `LiveTab` from `insights/layout.tsx` and enables the live tab.
  - Docs alignment for shipped status: updated Phase 8 notes and corrected phase mapping in `browse-ui/README.md` (Search remains Phase 7).
- `browse-ui/` — Phase 7 acceptance slice:
  - Foundation layer for v2 UI (shared hooks, schemas, formatters, layout/data primitives, charts, and command wiring)
  - `/v2/sessions` sessions list with client-side filter/sort/pagination and keyboard navigation
  - `/v2/sessions/[id]` session detail with Overview/Timeline/Mindmap/Checkpoints tabs, compare sheet, and markdown export action
  - `/v2/search` faceted search with recent-query history, keyboard navigation, and session/detail jump actions
  - Command palette improvements with fuzzy matching, grouped commands, and recent-search integration
- `browse-ui/` — Next.js 16 static-export frontend scaffold (Phase 6):
  - 6 stub routes: `/v2/sessions`, `/v2/sessions/[id]`, `/v2/search`, `/v2/insights`, `/v2/graph`, `/v2/settings`
  - AppShell layout (sidebar + header) using shadcn/ui + Tailwind v4
  - TanStack Query v5 provider, next-themes dark mode provider
  - `lib/api/client.ts` — typed `apiFetch()` with token injection and 401 handling
  - `lib/api/types.ts` — full TypeScript interface set matching Python API schema
  - `hooks/use-sse.ts` — SSE hook with pause/resume; `hooks/use-density.ts`
  - Vitest unit tests (3/3); Playwright E2E stub
  - `dist/` committed to git — served at `/v2/*` by Python browse server
- `browse/routes/serve_v2.py` — Python static file server for `browse-ui/dist/` at `/v2/*`:
  - SPA fallback to `index.html` for unknown page paths
  - Path traversal protection (`..` / null-byte rejection)
  - `_next/` static assets served without auth; page routes require auth
- `browse/core/server.py` — added `/v2/` prefix handling in `do_GET`
- `hooks/rules/block_edit_dist.py` — `preToolUse`: blocks direct edits to `browse-ui/dist/`
- `hooks/rules/nextjs_typecheck.py` — `postToolUse`: reminds to run `pnpm typecheck` after TS edits
- `hooks/rules/pnpm_lockfile_guard.py` — `preToolUse`: blocks commit if `package.json` staged without `pnpm-lock.yaml`
- `hooks/rules/block_unsafe_html.py` — `preToolUse`: blocks `dangerouslySetInnerHTML` without sanitization
- `auto-update-tools.py`: `browse_ui` category in `classify_changes()`, `write_manifest()`, and `post_pull_pipeline()` step to rebuild `dist/` when source changes
- `.gitignore`: `browse-ui/node_modules/`, `browse-ui/.next/` (dist/ is NOT ignored)

- Design tokens layer (`browse/static/css/tokens.css`) — centralised colors, spacing, typography, shadows.
- Component primitives (`browse/components/primitives.py`): `page_header`, `stat_grid`, `data_table`, `empty_state`, `badge`, `banner`, `card` — pure functions, stdlib only, documented escape contracts.
- `/style-guide` route — visual reference for all primitive components.
- Visual snapshot test (`tests/test_visual_snapshot.py`) — guards against unintended HTML drift.
- Pre-commit hook blocks inline `<style>` in `browse/routes/*.py` (whitelist only for `dashboard.py`'s uplot-coupled block).
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
- **Rule 8 — Tentacle Execution Obligations** added to both instruction surfaces (`AGENTS.md` and `.github/copilot-instructions.md`): sub-agents must read the bundle first, stay in declared scope, mark todos done, skip git operations, and write a structured handoff with an explicit `--status` before stopping.
- **Hook enforcement summary** added to `AGENTS.md` and `.github/copilot-instructions.md`: concise table mapping each enforced rule to its hook and what it blocks.
- `test_quality_gates.py`: 85 tests verifying CI syntax gate behavior — `check_syntax.py` correctly rejects broken syntax and accepts valid Python; `run_all_tests.py` self-check; CONTRIBUTING.md syntax-gate documentation coverage.
- `test_auto_update_coverage.py`: 33 tests verifying auto-update coverage tracking — `COVERAGE_MANIFEST` key set, `classify_changes()` detection for browse/, providers/, hooks/rules/, scripts/, .github/workflows/, `--list-coverage` output, `write_manifest()` field contracts, and `install.py deploy_hooks` subdirectory discovery.
- `/compare?a=&b=` — side-by-side session comparison (243b85b)
- `/session/{id}.md` — markdown export of a session for copy/paste use (243b85b)
- Dashboard widgets: red-flag sessions, weekly mistakes trend, top error-prone modules (243b85b)
- Session detail: button row (Timeline / Mindmap / Export MD / Compare / Find similar) and tool-usage summary (243b85b)
- Command palette expanded to 11 commands with section grouping (Navigation / Explore / Admin / View / Help) (243b85b)
- Timeline: model-based color coding and legend, incorporating data previously served by the removed agents route (243b85b)

### Changed
- Docs rollout alignment:
  - `README.md` and `browse-ui/README.md` now describe `/v2/graph` using Evidence/Similarity/Communities semantics instead of legacy relationships/clusters wording.
  - Auto-update + hooks documentation now matches current rollout behavior (update-only directory semantics, global Copilot skill refresh behavior, and git-hook reinstall requirements after git-hook script updates).
- `auto-update-tools.py`: `COVERAGE_MANIFEST` `Hooks/` entry now distinguishes Python hook scripts (`install.py --deploy-hooks`) from git-level hooks (`install.py --install-git-hooks` per repo); `--list-coverage` output updated to match.
- `browse/static/css/app.css` — rewrote to consume design tokens, no hardcoded hex, no `var(--pico-*)` references, 24 sections (header/footer, nav, page-header, tables, stat grid, banner, card, mindmap, live feed, embeddings, …).
- Migrated `browse/routes/{home,sessions,dashboard,mindmap,live,embeddings}.py` to use component primitives; inline `<style>` blocks removed (only `dashboard.py`'s `.db-chart-wrap` whitelisted).
- `mindmap.js` now syncs `.markmap-dark` class on `#mindmap-wrap` with `[data-theme]` to restore contrast in dark mode.
- Restructured README from 577 lines to ~280 lines (moved details to docs/)
- Added "Why?" section for first-time readers
- Added Quick Start section (3 commands from zero to working)
- Cross-repo isolation git-root comparison now uses `Path.resolve()` canonical paths (`_same_canonical_root()`), so repos reached through dotdot paths or symlink-equivalent representations are correctly identified as the same repo — prevents stranded or duplicate dispatched-subagent markers.
- Navigation now uses `<details>` hamburger menu for better discoverability (243b85b)
- `browse/core/registry.py` sorts routes by path length descending so specific routes (e.g. `/session/{id}.md`) match before generic ones (e.g. `/session/{id}`) (243b85b)

### Removed
- Screenshot / "save as PNG" feature (broken under CSP, rarely used) (243b85b)
- `browse/static/vendor/html-to-image.min.js` (orphaned after screenshot removal) (243b85b)
- `/session/{id}/agents` and `/api/session/{id}/agents` routes — functionality merged into `/session/{id}/timeline` (243b85b)

### Fixed
- `/v2/sessions/[id]` real UUID session-detail runtime routing behavior is now explicitly covered in shipped smoke tests, preventing regressions back to placeholder-only resolution on real deployments.
- `tentacle.py` Unicode console failures on Windows: added standard UTF-8 stdout/stderr reconfigure block (matching `briefing.py`/`install.py` pattern), eliminating `UnicodeEncodeError` for emoji and non-ASCII output.
- `test_karpathy_skill_rollout.py` Windows path assertion failures: two assertions now use `.as_posix()` for cross-platform path comparison.
- POSIX home normalization in `check_subagent_marker.py` now uses explicit backslash (`chr(92)`) instead of `os.sep` for separator replacement, so the function produces correct Windows paths even when the Python runtime reports a POSIX `os.sep`. This fixes a latent bug that could have surfaced if the code were ever exercised in a POSIX-hosted Windows-emulation layer.
- Section 17g tests in `test_hooks.py` now build expected Windows paths with `chr(92)` instead of literal backslash string comparisons, making assertions valid on both Windows and non-Windows hosts.
- CSP-breaking inline `onclick=` on the dark-mode toggle button — replaced with `addEventListener` using a nonce'd script (243b85b)
- `copyLink()` token leak: now uses `URL.searchParams.delete('token')` instead of stripping all query params, preserving `?q=`, `?session=`, `?a=&b=` in shared links (243b85b)

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
