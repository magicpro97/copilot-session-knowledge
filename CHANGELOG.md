# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- **Debug-log read API and registry-driven debug auth gate (WBS-104, #429):**
  - `browse/api/operator.py`: new `GET /api/operator/sessions/{session_id}/runs/{run_id}/debug`
    endpoint (registered with `debug=True`).  Returns a paginated, redacted `DebugLogResponse`
    envelope with `schema_version`, `session_id`, `run_id`, `total`, `from`, `limit`,
    `has_more`, and `events` (array of `BrowseDebugEntry`).  Reads events exclusively from
    persisted operator run JSON (`get_session` + `get_run_status`); no WBS-103 SQLite storage
    consulted.  Supports `from`, `limit`, `kind`, `level`, and `since` query parameters with
    strict validation (bad params → 400 JSON).  Operator events are mapped to BrowseDebugEntry
    via the WBS-101 taxonomy; `source="operator_console"`, `level=null`, synthetic span_id via
    `sha1("operator_console:{idx}:1")[:16]`.  Events whose serialized JSON exceeds 8192 bytes
    receive a truncation marker (`[TRUNCATED sha256=<hex16> bytes=<n>]`) with
    `attrs.truncated=true` and `attrs.bytes_in=n`.  All entries pass through
    `browse.core.redaction.redact_entry`; redaction runs on at most `limit` entries (≤100)
    per request.  Session/run 404 errors include no UUID or path leakage.
  - `browse/core/registry.py`: generalized `match_route` from hard-coded `{id}` → `session_id`
    to arbitrary `{name}` named placeholders.  `{id}` still maps to kwarg `session_id` for
    backward compatibility.  New `_build_route_pattern()` helper splits on `_PLACEHOLDER_RE`
    and constructs named-group regex patterns.  Multi-placeholder paths such as
    `/sessions/{session_id}/runs/{run_id}/debug` are fully supported.
  - `browse/core/server.py`: debug auth gate generalized from prefix `/api/debug-log/` to
    registry-driven `match_route(path, "GET")` returning `debug_flag=True`.  Any route
    registered with `debug=True` — regardless of URL prefix — automatically receives
    Bearer/cookie-only auth, `?token=` rejection (401), static-slot blocking (403), and the
    non-loopback insecure-config 403 rule.  `/api/debug-log/healthz` behaviour is unchanged.
  - `tests/test_browse_debug_log_api.py`: 36 tests (105 assertions) covering unknown/non-UUID session/run 404
    no leakage; ownership mismatch 404; missing/invalid auth 401; `?token=` rejection 401;
    valid Bearer and cookie 200; static slot 403; pagination (default/explicit/overflow/max
    limit/negative from); kind/level/since filters and bad params; redaction; truncation
    indicator; CORS disallowed no ACAO; HEAD status/body; JSON content-type on all responses;
    performance 100 entries from 1000-event run <200 ms; empty run; schema_version; non-UUID
    session_id.
  - `docs/DEBUG-LOG-CONTRACT.md`: updated with WBS-104 section documenting the read API
    endpoint, auth/status semantics, query parameters, response shape, data source, event
    mapping, truncation semantics, `since` filter semantics, and registry-driven debug auth
    gate generalisation.

- **Debug-log importers — VS Code Agent and OTel JSONL (WBS-106, #431):**
  - `browse/importers/__init__.py`: new `browse.importers` package.
  - `browse/importers/_common.py`: shared helpers — `check_path_safe()` (rejects
    `..`, enforces `safe_base` + symlink escape detection), `file_hash_sha256()`,
    `synthetic_span_id()`, `content_hash_16()`, `DedupSet`, `is_valid_span_id()`,
    `max_line_bytes()` (default 1 MiB, env `BROWSE_DEBUG_LOG_MAX_LINE_BYTES`),
    and bounded JSONL line iteration that rejects over-cap lines without buffering
    the whole line.
    Error types: `PathTraversalError`, `SymlinkEscapeError`, `UnsupportedFormatError`.
  - `browse/importers/vscode_agent_debug_log.py`: read-only importer for VS Code
    `IDebugLogEntry` JSONL.  Normalises to `BrowseDebugEntry`; source = `vscode`.
    Features: directory-mode reads `main.jsonl`, companion-file skip (models.json,
    system_prompt_*.json, tools_*.json, title-*.jsonl, etc.), format detection,
    epoch-ms→ISO-UTC-Z, dur=0→null, attrs pre-filter (inputTokens→tokens_in,
    outputTokens→tokens_out; drop args/result/content/…), `redact_entry` pass,
    dedup by `{sid}:{type}:{spanId}:{ts}:{content_hash_16}`, dry-run mode.
    CLI: `python -m browse.importers.vscode_agent_debug_log --path … --dry-run --json-summary`.
  - `browse/importers/otel_file.py`: read-only importer for OTel `ReadableSpan`
    JSONL (ConsoleSpanExporter output and compatible variants).  Supports: `id`,
    `spanId`, or `spanContext.spanId` for span ID; `traceId`; `parentSpanId` /
    `parentSpanContext.spanId`; `timestamp` (µs), `startTime` (HrTime tuple or
    ISO string), `timeUnixNano`/`startTimeUnixNano` (ns); `duration` (µs→ms);
    `status.code` 0/1/2→null/ok/error; attrs pre-filter to allowlist with OTel
    semantic convention renames.  Detects and rejects VS Code files.
    CLI: `python -m browse.importers.otel_file --path … --dry-run --json-summary`.
  - `tests/test_browse_debug_log_importers.py`: 214 assertions covering: happy path,
    BrowseDebugEntry shape, kind mapping, timestamp, duration, attr renames,
    dangerous attr drop, tool_name, synthetic span ID, privacy (no username in
    summary), directory mode, companion skip, malformed lines, dedup, unsupported
    format, path traversal, symlink escape, dry-run, OTel status/level, HrTime,
    ISO/no-TZ startTime, timeUnixNano, bounded line-size cap, missing-span dedup,
    redaction scrubbing, required-field enforcement (spanId/status/attrs missing or
    wrong type), status null/unrecognized mapping, FIFO synthetic pairing
    (tool_call+result, FIFO order, orphan, cross-pair isolation, malformed parent
    normalisation, rIdx ignored), and VS Code scan-forward detection hardening
    (all-oversized, oversized-then-valid, oversized-then-non-VS-Code).
  - `tests/fixtures/debug-log/vscode-agent/debug-logs/0000fixture-session-aaaa/`:
    12-entry happy-path fixture with companion files (models.json,
    system_prompt_abc.json, tools_fixture.json) for skip tests.
  - `tests/fixtures/debug-log/vscode-agent/debug-logs/0000fixture-session-bbbb/`:
    malformed-lines fixture (missing ts, missing sid, not-JSON, v=2).
  - `tests/fixtures/debug-log/vscode-agent/debug-logs/0000fixture-session-cccc/`:
    dedup fixture (5 lines, 2 duplicates → 3 unique).
  - `tests/fixtures/debug-log/otel/console-spans.jsonl`: ConsoleSpanExporter
    happy-path with status codes 0/1/2, forbidden attrs, path/bearer secret for
    redaction test.
  - `tests/fixtures/debug-log/otel/hrtime-spans.jsonl`: HrTime tuple, ISO string,
    no-TZ ISO, and timeUnixNano timestamp variants.
  - `tests/fixtures/debug-log/otel/malformed-spans.jsonl`: malformed OTel fixture.
  - `docs/DEBUG-LOG-CONTRACT.md`: WBS-106 importer contract section added.
  - Oversized-line test cases are generated dynamically in temp; no >1 MiB fixture
    files are committed.

- **VS Code importer review hardening (PR #445, WBS-106):**
  - `browse/importers/vscode_agent_debug_log.py`: enforce required presence + string
    type for `spanId`, `status`, and `attrs` in `_parse_line`; status `null` or
    unrecognised string maps to `None`/omitted; non-string/non-null status, non-dict
    attrs, or missing any of the three → malformed+skip.  FIFO synthetic span-id
    pairing for `tool_call`/`tool_result` rows lacking a valid native `spanId`:
    key = `(sid, name, normalized_parent_span_id)` with `rIdx` omitted; deduped
    `tool_call` rows do not enqueue orphan synthetic spans.  `_detect_format` now
    mirrors OTel scan-forward hardening: skips oversized and unparseable leading
    lines, requires VS Code fingerprint on first parseable JSON object, and raises
    `UnsupportedFormatError` at EOF if no fingerprint is found.

- **Bounded redacted operator debug-event sidecar (WBS-105, #430):**
  - `browse/core/operator_console.py`:
    - Constants `_MAX_DEBUG_EVENTS = 5000` and `_DEBUG_SOURCE = "operator_console"`.
    - `_classify_debug_kind(event_type)` — maps Copilot CLI event types to BrowseDebugEntry kinds
      (session_start, turn_start, llm_request, tool_call, hook, subagent, agent_response, error,
      generic, raw); unknown typed JSON → generic; None/empty → raw.
    - `_synthetic_span_id(idx, seq)` — deterministic `sha1("operator_console:{idx}:{seq}")[:16]`;
      re-hashes on all-zeros (astronomically unlikely).
    - `_build_debug_entry(parsed_event, debug_idx, run_seq)` — builds a BrowseDebugEntry-shaped
      dict: source=operator_console, message ≤ 2048 chars, synthetic span_id, allowlisted attrs.
    - `_append_debug_event(run_state, entry, session_id)` — appends to `run["debug_events"]`;
      enforces cap: at `_MAX_DEBUG_EVENTS - 1` appends a single sentinel
      `{message: "[DEBUG TRUNCATED]", attrs: {truncated: True, event_count: 5000}}`; subsequent
      events dropped. Returns the redacted entry to callers, which invoke `_store_debug_event`
      outside `_RUNS_LOCK`; storage errors are logged and do not propagate.
    - `start_run` initializes `debug_events: []`, `_debug_idx: 0`, `_debug_seq: 1`.
    - `_run_copilot_thread` appends a classified debug entry after each stream event (does not
      interleave into `run["events"]`; never emitted over SSE).  Appends terminal debug events
      for success, failure, timeout, cancellation, FileNotFoundError, and generic exceptions.
    - `_persist_run` writes `debug_events` to disk while excluding `_debug_idx`, `_debug_seq`,
      and `proc`.
    - SSE stream shape, `make_stream_generator`, `_RESUME_TOKENS`, `_CHECKPOINT_INTERVAL`, and
      `_MAX_OUTPUT_LINES` semantics are **unchanged**.
    - `debug_log_storage` lifecycle remains in `browse/__init__.py`; never initialized from
      `operator_console`.
  - `tests/test_browse_operator_debug_events.py`: 27-test-case suite (204 assertions) covering classification
    fixtures, synthetic span-id format/determinism/no-zeros, entry construction for raw and
    structured events, sidecar cap and sentinel, sealed-sidecar drop, redaction pass, stream
    byte-equality (debug_events absent from SSE), _MAX_OUTPUT_LINES sidecar independence,
    feature-disabled path, persistence (debug_events written, _debug_idx/_debug_seq excluded),
    failed-exit and FileNotFoundError terminal events, raw truncation at 2048 chars, and public
    status/runs response filtering.
  - `docs/DEBUG-LOG-CONTRACT.md`: new **WBS-105 Operator Debug Event Capture** section
    documenting constants, classification table, synthetic span-id formula, sidecar lifecycle,
    truncation sentinel shape, and all terminal debug event shapes.

- **Debug-log separate SQLite storage and healthz probe (WBS-103, #428):**
  - `browse/core/debug_log_storage.py`: new isolated debug-log store — separate SQLite DB at
    `~/.copilot/operator-console/debug-log/debug-log.db` (outside session-state and `knowledge.db`).
    WAL/NORMAL pragmas; redaction before insert (JSON-only); deterministic age-then-size pruning;
    optional background retention thread; explicit shutdown, no `atexit`/signal handlers.
  - `browse/routes/debug_log.py`: `GET /api/debug-log/healthz` — safe probe route (enabled only);
    returns `ok`, `enabled`, and `retention` config; no paths, event counts, or session content.
  - `browse/__init__.py`: new CLI flags and env vars:
    `--debug-log` / `BROWSE_DEBUG_LOG_ENABLED=1` (disabled by default);
    `--debug-log-dir` / `BROWSE_DEBUG_LOG_DIR`;
    `--debug-log-max-age-seconds` / `BROWSE_DEBUG_LOG_MAX_AGE_S` (default 86400);
    `--debug-log-max-bytes` / `BROWSE_DEBUG_LOG_MAX_BYTES` (default 52428800);
    `--debug-log-retention-interval` / `BROWSE_DEBUG_LOG_RETENTION_INTERVAL_S` (default 300);
    `--debug-log-ephemeral` / `BROWSE_DEBUG_LOG_EPHEMERAL`.
  - `browse/core/server.py`: debug-log gate — disabled route → 404; `?token=` rejected with 401;
    static/demo mode → 403; non-loopback with no server token → 403; auth via `check_debug_token`
    (Bearer/cookie only, no open-auth); inherits `/api` CORS/PNA/Vary policy.
  - `tests/test_browse_debug_log_storage.py`, `tests/test_browse_debug_log_transport.py`,
    `tests/test_browse_debug_log_exclusion.py`: isolation verified — default path outside
    session-state; `.db` ignored by watcher; sync auto-detect excludes `operator-console/`;
    session export body excludes debug-log content.
  - `docs/DEBUG-LOG-CONTRACT.md`: updated to document WBS-103 storage/transport contract.

- **Explicit improvement signal tracking (#126):**
  - `migrate.py` v24: new `improvement_signals` table in the session knowledge DB — stores explicit user-reported `missed_match`, `wrong_skill`, and `outdated_skill` signals linked to session IDs, with `consumed` tracking and indexes on `consumed`, `signal_type`, `created_at`, and `mentioned_skill`.
  - `improvement-signals.py`: new standalone stdlib-only script exposing `record`, `list`, `consume`, and `stats` subcommands. Supports `--format json` throughout. Fail-open on missing DB. Creates the table itself if migration has not yet run.
  - `sk.py`: `improvement-signals` added to `_DIRECT` command map → `improvement-signals.py`.
  - `skill-suggest.py`: new `_load_improvement_signals()` and `_signals_to_candidates()` functions. `suggest()` now merges unconsumed signal-derived candidates with knowledge-derived candidates (boost existing or append new). Consumed rows never surface. Fail-open: behavior is identical to pre-signal when the table is absent. New `improvement_signal_count` field in the result dict.
  - `tests/test_improvement_signals.py`: new test file covering table creation, all signal types, consumed filtering, list/consume/stats, and CLI dispatch.
  - `tests/test_skill_suggest.py`: `TestSignalIntegration` class added — covers fail-open (no table, missing DB), signal-derived candidate appearance, consumed exclusion, score boosting of existing candidates, patch_guidance for wrong/outdated signals, and `improvement_signal_count` key presence.
  - `tests/test_sk_cli.py`: `improvement-signals` direct command routing tests added.
  - `docs/USAGE.md`: `sk improvement-signals` section documenting signal types, record/list/consume/stats usage, and skill-suggest integration.


  - `hooks/rules/skill_usage.py`: New `SkillUsageRule` (postToolUse, `skill` tool only) — records `triggered`, `loaded`, or `skipped` events in `skill_usage_events` table of `skill-metrics.db`. Exit code 0 always yields `loaded`; non-zero yields `skipped`; absent exit code falls back to short-output skip-marker heuristic. Fail-open; never blocks tool use.
  - `hooks/rules/__init__.py`: `SkillUsageRule` registered in the postToolUse rule list.
  - `skill-metrics.py`: Event-level skill usage is included in the default output and `--json` status surface (`total_skill_events` plus per-skill triggered/loaded/skipped counts); no separate `--events` flag is needed.
  - `briefing.py`: New skill usage section surfaces top/bottom skills by load rate from `skill_usage_events` when the table exists (incremental; existing briefing sections unchanged).
  - `browse/routes/skills.py`: `event_skill_usage` data (per-skill triggered/loaded/skipped counts) is returned inline inside the existing `/api/skills/metrics` response; there is no separate `/api/skills/usage-events` route.
  - `docs/HOOKS.md`: `skill-usage` rule added to the registered-rules table.

- **Level 0 progressive skill loading at sessionStart (#118):**
  - `briefing.py`: `--session-start` flag causes a compact skill index to be printed to stdout *before* any knowledge-DB access. Index is generated from `skills/*/SKILL.md` YAML frontmatter (stdlib-only parsing); descriptions longer than 60 chars are truncated to the first 57 characters followed by `"..."` (total 60 chars). Fail-open: missing skills dir, unreadable files, or parse errors produce empty output silently. No leakage into normal `sk briefing` / `sk query` paths.
  - `briefing.py`: `_parse_skill_frontmatter()` — pure-stdlib YAML frontmatter parser; handles folded/literal block scalars (`>`, `|`, `>-`, `|-`, `>+`, `|+`).
  - `briefing.py`: `_generate_skill_index()` — discovers `skills/*/SKILL.md`, builds formatted index block, wraps entire body in try/except for fail-open contract.
  - `hooks/rules/briefing.py`: `AutoBriefingRule` now passes `--session-start` to the `briefing.py` subprocess on `sessionStart`.
  - `hooks/auto-briefing.py`: legacy sessionStart path now passes `--session-start` to the `briefing.py` subprocess.
  - `sk-rust/src/hooks/rules.rs`: Rust `AutoBriefingRule::evaluate()` now chains `--session-start` with `--budget 2000` in the subprocess args.
  - `tests/test_briefing.py`: Section 16 — regression tests for `_parse_skill_frontmatter`, `_generate_skill_index`, constants, no-leakage, and subprocess behavior (tests 16a–16i).
  - `tests/test_hooks.py`: Sections 27–29 — Section 27 covers SkillUsageRule (#119); Section 28 covers `--session-start` wiring in all three caller paths and skill index emission (#118); Section 29 covers SK_TOOLS_DIR existence guard (#118 follow-up).
  - `sk-rust/tests/integration_test.rs`: `auto_briefing_passes_session_start_flag` integration test — uses a stub `briefing.py` to verify `--session-start` reaches the subprocess.
  - `docs/HOOKS.md`: Updated `auto-briefing` rule description to document `--session-start` flag and Level 0 skill index.
  - `docs/SKILLS.md`: New *Progressive Skill Loading* section describing L0/L1/L2 loading levels, index format, fail-open contract, and implementation surface.

- **Quota-blocked handoff metadata and retry queue (#187):**
  - `tentacle.py`: `_classify_quota_signal(text)` — classifies raw dispatch output into a machine-readable `quota_reason` token (`rate_limit`, `quota_exceeded`, `daily_quota`, `monthly_quota`, `token_quota`, `context_limit`). Pattern list is intentionally minimal pending `#183`.
  - `tentacle.py handoff` gains `--quota-reason <reason>` and `--retry-hint <hint>` optional flags for `BLOCKED` handoffs. These serialize as `QUOTA_REASON:` / `RETRY_HINT:` lines in `handoff.md`.
  - `tentacle.py`: `_parse_handoff_quota_metadata(content)` extracts `(quota_reason, retry_hint)` from the most recent handoff section. Old `BLOCKED` handoffs without these lines are fully backward compatible.
  - `tentacle.py complete`: when a tentacle's handoff carries `quota_reason`, `meta.json` gains `quota_reason` and `retry_hint` fields, and an entry is appended to `goal.json["quota_retry_queue"]` for orchestrator tracking.
  - `tentacle.py goal next-iter`: quota-blocked tentacles (BLOCKED + `quota_reason`) are rendered with a 🚦 icon and quota hint, distinct from generic ⚠️ blocked tentacles. Quota retry queue summary is printed when non-empty. Recommendation advice is quota-aware.
  - `browse/routes/tentacles.py`: `_parse_handoff_quota_metadata` added; each tentacle entry now includes optional `quota_reason` and `retry_hint` fields from `meta.json` (or live handoff for not-yet-completed tentacles).
  - Docs updated: `docs/ARCHITECTURE.md` (handoff contract), `docs/USAGE.md` (quota-blocked operator flow), `docs/SYNC-MATRIX.md` (handoff field parity note).

- **Agent Error Prevention System (5-phase implementation):**
  - **Phase 1 — Schema & Learn Enhancement:**
    - `migrate.py` v16: 7 new columns on `knowledge_entries` — `error_type`, `root_cause`, `severity`, `is_resolved`, `fix_steps`, `prevention_hook`, `recurrence_after_briefing`.
    - `learn.py`: New CLI flags `--error-type`, `--root-cause`, `--severity`, `--fix-step` for manual classification.
    - `extract-knowledge.py`: Auto error-type classification (8 types), root-cause extraction, severity detection for `mistake` entries.
    - FTS5 index now includes `error_type` and `root_cause` for full-text search.
  - **Phase 2 — Core Hooks:**
    - `hooks/rules/read_before_edit.py`: New `ReadBeforeEditRule` — tracks viewed files, warns on edit of unread files (fail-open).
    - Briefing auto-budget increased from 500→2000 tokens.
    - Enhanced briefing compact format with severity, error_type, root_cause metadata.
    - `migrate.py` v17: `briefing_deliveries` table for tracking which entries were briefed per session.
    - `briefing.py`: Delivery tracking — records briefed entries to `briefing_deliveries`.
  - **Phase 3 — Verification & Feedback Loop:**
    - `tentacle.py`: `--strict-verify` flag on `complete` — exits non-zero on verify failure, does not force-mark pending todos.
    - `hooks/rules/recurrence_detector.py`: New `RecurrenceDetectorRule` — detects briefed mistakes that recurred in the same session, increments `recurrence_after_briefing` counter at session end.
  - **Phase 4 — Enhanced Classification & Error KB:**
    - `hooks/rules/error_kb.py`: Captures tool name, file path context; increased search from 100→500 chars; richer KB match output.
    - `extract-knowledge.py`: Noise filter preserves code blocks containing stack traces/errors.
    - `query-session.py`: `--error-type` filter for knowledge search; display error lifecycle metadata in results.
  - **Phase 5 — Browse App & Research Tools:**
    - `browse/api/errors.py`: New API endpoints `/api/errors` (error type distribution, severity, trends) and `/api/recurrence` (briefing effectiveness, recurring mistakes).
    - `error-analysis.py`: New standalone CLI tool for on-demand error pattern analysis with `--type`, `--recurring`, `--root-causes`, `--export json` options.

- **Single-version browse app migration — root-served routes:**
  - The browse app now uses a unified root-served app (`/*`) for both the local Python browse
    server and the hosted static deployment.
  - Canonical routes are now `/*`: `/chat`, `/sessions`, `/sessions/[id]`, `/search`, `/insights`, `/graph`, `/settings`.
  - The Python browse server gains root routing for the Next.js app; compatibility redirects from `/v2/*` → `/*` are provided for old bookmarks and deep links.
  - `browse-ui/next.config.ts` default `basePath` transitions from `"/v2"` to `""` so that `pnpm build` produces a root-relative artifact for both the local server and Firebase Hosting deployments.
  - The legacy Python HTML UI routes (`/sessions`, `/graph`, `/search`, etc. served by
    `browse/routes/*.py`) are retired; those routes now resolve to the Next.js app, while
    `/v2/*` remains only as a compatibility redirect layer.
  - Docs (`docs/ARCHITECTURE.md`, `docs/OPERATOR-PLAYBOOK.md`, `docs/AUTO-UPDATE.md`, `docs/HOOKS.md`, `browse-ui/README.md`) updated to reference canonical root routes.

- **Knowledge pipeline hardening and verification lifecycle:**
  - `tentacle.py complete` now supports `--auto-verify <cmd>` (optional): runs the command, persists the result as a `tentacle_verifications` row, and logs pass/fail before closing the tentacle. Fail-open — completion proceeds even when the verification command exits non-zero. `--auto-verify-timeout <seconds>` controls the timeout (default: 120 s).
  - `extract-knowledge.py` — category-aware confidence floors: `pattern` entries use floor `0.5`; other categories use floor `0.4`. Recurring entries (same topic key seen again) receive a `+0.03` recurrence reward on each upsert, capped so confidence never exceeds `1.0`. `learn.py` raises the default initial confidence for `pattern` entries from `0.6` to `0.7`.
  - `sync-knowledge.py` — merge now uses MAX confidence semantics: when a matching entry exists in both source and target, the higher confidence value is kept rather than overwriting.
  - `migrate.py` — v15 `confidence_backfill_wave3`: raises pattern confidence floor to `0.5` and applies a recurrence reward (`+0.03 × min(occurrence_count − 1, 5)`) to entries with `occurrence_count ≥ 2` in existing databases (capped to avoid runaway). Current schema version: **v15**.
  - **Remaining operational gaps after Wave 3 (not fixed by code; require operator action):** local retro `behavior` sub-dimensions (`completion_rate`, `efficiency_ratio`); knowledge `embed_pct`; health `confidence_quality`, `relation_density`, `embedding_coverage`. Run `embed.py` to grow embedding coverage; add more verified tentacles to improve confidence quality and relation density.
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

- **Debug Log tab — session detail debug event viewer (WBS-107):**
  - New fifth tab on `/sessions/[id]`: **Debug Log** — shows all debug events recorded for the
    most recent operator run associated with the session.
  - Event list table: timestamp, kind badge, source, tool name, duration, status, message
    preview columns; paginates server-side in blocks of 100.
  - Filter toolbar: text substring search, kind, level, and status dropdowns — AND composition,
    no regex, safe for arbitrary user input.
  - Detail drawer: slides in from the right on row click; renders all `BrowseDebugEntry` fields
    as plain text (no `dangerouslySetInnerHTML`); includes one-click copy of the redacted JSON.

- **Span tree visualization (WBS-108):**
  - Added span tree view inside the Debug Log detail drawer: when an entry carries a `span_id`
    and related spans are present in the current page, a collapsible parent/child tree is
    rendered showing the call hierarchy, durations, and status badges.

- **Soak diagnostics — `soak-diagnostics.py` (WBS-109):**
  - New standalone stdlib-only script for validating debug-log pipeline health under soak
    conditions.  Exposes `ThresholdEvaluator` for per-metric pass/fail gates.
  - Two modes: `--smoke` (quick sanity check, < 30 s) and `--full` (extended soak, configurable
    duration).  Outputs a structured JSON report with per-check verdicts, timing, and failure
    reasons.  Exit code 0 = all checks passed; non-zero = at least one threshold breached.

- **CSP hardening — nonce-only script execution (#441):**
  - Eliminated `unsafe-inline` from the `script-src` Content Security Policy directive across
    all browse-server responses.  All inline scripts now use a per-request nonce generated by
    `browse/core/server.py` and injected into the CSP header and every `<script>` tag.
  - Static asset scripts already used nonces; this change closes the remaining gap for
    dynamically-generated page scripts (dark-mode toggle, command-palette boot, analytics stubs).
  - `tests/test_browse_csp.py`: new test suite verifying the absence of `unsafe-inline` in
    `script-src` and the presence of a `nonce-` source in every served HTML response.

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

### Added
- `browse/` package: modular web UI replacing monolithic `browse.py`; `browse.py` is now a thin shim.
- `browse/core/`: `server.py` (ThreadingHTTPServer), `auth.py` (token auth), `csp.py` (nonce CSP), `fts.py` (FTS5 helpers), `registry.py` (route decorator), `templates.py` (base page), `static.py` (vendored assets), `projection.py` (PCA), `palette.py` (command palette commands), `streaming.py` (SSE helper).
- F1 `/` — home page with recent sessions.
- F2 `/sessions` — FTS-powered sessions list.
- F3 `/session/<id>` — session detail view.
- F4 command palette (`Ctrl+K`, ninja-keys) — global keyboard navigation on every page.
- F5 `/graph` — interactive Cytoscape.js knowledge-entity graph.
- F6 `/diff` — side-by-side checkpoint diff viewer.
- F7 `/search` + `/api/search` — FTS5 full-text search with facets (F7 rich UX).
- F8 dark-mode toggle baked into base template (`prefers-color-scheme` + localStorage).
- F9 `/dashboard` + `/api/dashboard/stats` — aggregate stats and session health.
- F10 `/embeddings` + `/api/embeddings/points` — 2-D PCA scatterplot of knowledge vectors.
- F11 `/live` + `/api/live` (SSE) — real-time feed of new knowledge events.
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
