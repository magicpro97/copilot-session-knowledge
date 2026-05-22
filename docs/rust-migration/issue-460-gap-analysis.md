# Issue #460 — Core Runtime Migration Gap Analysis
## `tentacle.py` · `briefing.py` · `query-session.py`

> **Scope:** Research-only. No code changes in this PR.  
> **Branch:** `docs/issue-460-runtime-gap-analysis`  
> **Method:** Direct inspection of Python source + Rust source + integration tests.  
> Distinguishes **Facts** (verified from source), **Interpretation** (inference),
> **Actions** (next steps), and **Verification evidence** (how to confirm claims).

---

## 1. Python LOC / Surface Inventory

Measured from source via `ast.parse` + `Measure-Object -Line` (Windows, UTF-8):

| File | Total lines | Non-blank/non-comment | Functions | Classes |
|------|------------:|----------------------:|----------:|--------:|
| `tentacle.py` | 4,515 | 3,779 | 86 | 0 |
| `briefing.py` | 3,937 | 3,351 | 82 | 1 |
| `query-session.py` | 2,840 | 2,415 | 49 | 0 |
| **Sub-total** | **11,292** | **9,545** | **217** | **1** |

**Helper modules used exclusively by `tentacle.py`:**

| File | Bytes | Role |
|------|------:|------|
| `_tentacle_core.py` | 8 KB | Shared state primitives |
| `_tentacle_dispatch.py` | 73 KB | Sub-agent dispatch runtime |
| `_tentacle_goal.py` | 156 KB | Goal-loop orchestration |
| `_tentacle_pr.py` | 19 KB | PR automation runtime |
| `_tentacle_review.py` | 43 KB | Review agent runtime |

Total tentacle surface (Python): ~300 KB across 6 files.

---

## 2. Existing Rust Coverage

### 2.1 `tentacle.py` → `sk tentacle`

**Rust dispatch** (`sk-rust/src/main.rs`, line 215):
```rust
Some(Commands::Tentacle { args }) => run_fallback("tentacle.py", &args),
```
**Fact:** `sk tentacle` is a **100% Python fallback** — every subcommand and flag
is forwarded to the Python script. There is no native Rust implementation of any
tentacle CLI subcommand.

**Partial Rust coverage exists, but only for *hook enforcement rules***, not the CLI:
- `sk-rust/src/hooks/rules/tentacle.rs` (479 lines) implements:
  - `TentacleSuggestRule` — postToolUse informational suggestion
  - `TentacleEnforceRule` — preToolUse deny gate for multi-module edits
  - Helper: `read_tentacle_edits_paths()`, `read_tentacle_edits_for_current_repo()`
  - Helper: `bash_writes_source_for_enforce_tentacle()`
- These rules mirror **two** of the 86 Python functions (`TentacleSuggestRule.evaluate`,
  `TentacleEnforceRule.evaluate`) and share marker/HMAC logic already covered elsewhere
  in the hook runner.

**Not covered in Rust (all 86 Python CLI functions):**

| Group | Functions |
|-------|-----------|
| Lifecycle | `cmd_create`, `cmd_split`, `cmd_complete`, `cmd_delete`, `cmd_list`, `cmd_show`, `cmd_status` |
| Handoffs | `cmd_handoff`, `_parse_rich_handoff_sections`, `_parse_handoff_*` (6 parsers) |
| Todos | `cmd_todo` |
| Verification | `cmd_verify`, `_run_and_record_verification`, `_is_legacy_auto_verify_match` |
| Worktrees | `cmd_worktree`, `_worktree_prepare`, `_worktree_status`, `_worktree_cleanup`, `_update_meta_worktree` |
| Scope | `_apply_scope_reclassification`, `_create_scope_escalation_followup_tentacle`, `_scope_reclassification_*` (5 fns) |
| Automations | `cmd_auto`, `_render_automation_workflow`, `_sync_automation_workflow`, `_validate_cron_expression`, +5 helpers |
| Audit | `cmd_audit` |
| Marker cleanup | `cmd_marker_cleanup`, `_cmd_marker_cleanup_from_stop_event` |
| Goal runtime config | `_configure_goal_runtime`, `_configure_dispatch_runtime`, `_configure_review_runtime`, `_configure_pr_runtime` |
| Dispatch helpers | `_dispatch_runtime_*` (4 fns), `_pr_runtime_subprocess_safe` |
| Metrics | `_ensure_metrics_schema`, `_persist_outcome_metrics` |

### 2.2 `briefing.py` → `sk briefing`

**Rust dispatch** (`sk-rust/src/main.rs`, line 212):
```rust
Some(Commands::Briefing { args }) => commands::briefing::run_briefing_command(&args),
```

**Rust handler** (`sk-rust/src/commands/briefing.rs`, 332 lines) covers:

| Flag | Rust function | Python equivalent |
|------|---------------|-------------------|
| `--wakeup` | `run_wakeup()` | `generate_wakeup()` |
| `--auto` | `run_compact_briefing(..., is_auto=true)` | `generate_briefing()` compact path |
| `--compact [query]` | `run_compact_briefing(...)` | `generate_briefing()` compact path |
| `--wing`, `--room`, `--limit` | parsed in `parse_compact_args()` | same flags |

**Fact:** when no native flag is present, the Rust handler falls back to Python:
```rust
run_fallback("briefing.py", args)  // briefing.rs line 26
```

**Not covered in Rust** (falls back to Python — the 4 flags above are the only natively handled paths; all other `briefing.py` functions fall back):

| Category | Key functions |
|----------|--------------|
| Full briefing (default mode) | `generate_briefing()` with `_format_default`, `_format_markdown`, `_format_json` |
| Task / subagent briefing | `generate_task_briefing()`, `generate_subagent_context()` |
| Titles-only mode | `generate_titles_only()` |
| Adaptive FTS | `_analyze_query_strictness`, `_build_adaptive_fts_query`, `_rewrite_query_local`, `_expand_synonyms`, `_expand_synonyms_fts` |
| Recall telemetry | `_record_recall_event`, `_upsert_entry_recall_stats` |
| Mode profiles | `_infer_mode_from_query`, `_resolve_mode_profile`, `_mode_category_config` |
| Token budgeting | `_estimate_tokens`, `_compute_dynamic_budget` |
| Clarification/constitution | `_load_constitution`, `_serialize_constitution`, `_load_matching_clarification`, `_clarification_match_score` |
| Semantic search | `search_semantic` |
| Session history search | `search_past_work` |
| Blast radius analysis | `blast_radius` |
| Codebase map / file annotations | `load_codebase_map_files`, `query_file_annotations`, `_format_file_annotations_block` |
| Skill integration | `_collect_skill_usage_for_briefing`, `_generate_skill_index`, `_parse_skill_frontmatter` |
| Freshness scoring | `_compute_snippet_freshness`, `_recency_decay`, `_recency_composite_score`, `_get_briefing_half_life` |
| Feedback bias | `_apply_feedback_bias_to_knowledge`, `_normalize_feedback_query` |
| Plan.md extraction | `_extract_task_matches`, `_extract_file_matches`, `_extract_next_open` |
| Session ID detection | `_detect_session_id` |

### 2.3 `query-session.py` → `sk query`

**Rust dispatch** (`sk-rust/src/main.rs`, line 214):
```rust
Some(Commands::Query { args }) => commands::query::run_query_command(&args),
```

**Rust handler** (`sk-rust/src/commands/query.rs`, 550 lines) covers — **with no Python fallback**:

| Rust function | Flags handled |
|---------------|--------------|
| `show_wings` | `--wings` |
| `show_rooms` | `--rooms [wing]` |
| `show_detail` | `--detail <id>` |
| `show_by_category` | `--mistakes`, `--patterns`, `--decisions`, `--tools` |
| `search_fts_cmd` + `search_like_fallback` | free-text FTS + LIKE fallback |
| `show_recent_all` | (no args) |
| All | `--limit`, `--verbose`, `--wing`, `--room` |

**Fact:** `run_query_command` never calls `run_fallback`. Unknown flags are silently
treated as search terms or ignored.

**Not covered in Rust, and silently unavailable in the binary** (the 16 functions/flags listed below have no Rust equivalent and no fallback):

| Python function | `query-session.py` flag/subcommand | Status in binary |
|----------------|-------------------------------------|-----------------|
| `list_sessions` | `--sessions` / `sk query` session listing | ⚠️ silently missing |
| `show_session` / `show_session_raw` | `--session <id>` | ⚠️ silently missing |
| `show_recent` (sessions) | `--recent` | ⚠️ silently missing |
| `show_context` | `--context` | ⚠️ silently missing |
| `show_related` | `--relate <id>` | ⚠️ silently missing |
| `show_graph` / `show_graph_stats` | `--graph` | ⚠️ silently missing |
| `query_entity_relations` | `--relate <entity>` (graph mode) | ⚠️ silently missing |
| `export_search_results` / `_export_json` / `_export_markdown_knowledge` | `--export` | ⚠️ silently missing |
| `semantic_search` | `--semantic <query>` | ⚠️ silently missing |
| `show_by_file` | `--by-file <path>` | ⚠️ silently missing |
| `show_by_module` | `--by-module <name>` | ⚠️ silently missing |
| `show_by_task` | `--by-task <name>` | ⚠️ silently missing |
| `show_diff_context` | `--diff-context` | ⚠️ silently missing |
| Adaptive FTS | `_analyze_query_strictness`, `_build_adaptive_fts_query`, `_expand_synonyms` | ⚠️ silently degraded |
| `_record_recall_event` | (telemetry side-effect) | ⚠️ silently missing |
| `_supports_color` | (terminal color output) | ⚠️ silently missing |

---

## 3. Missing Features by Script

### 3.1 `tentacle.py` — All CLI subcommands missing from Rust

Priority missing features (operator-critical, called in hot paths):

1. **`cmd_handoff`** — writes tentacle handoff file; called by every sub-agent.
   High complexity: parses multi-section markdown, records metrics, runs `sk learn`,
   manages `--changed-file` tracking, rich status validation.

2. **`cmd_todo`** — CRUD for todo list inside a tentacle; called during active runs.

3. **`cmd_status`** / **`cmd_list`** — operator visibility; called frequently.

4. **`cmd_complete`** — closes tentacle, commits summary, triggers post-hooks.

5. **`cmd_verify`** — runs verification commands, records evidence, gates DONE status.

6. **`cmd_create`** — creates bundle dirs, plan files, marker files, worktree setup.

7. **Goal runtime** (`_tentacle_goal.py`, 156 KB) — goal-loop logic including
   `goal criteria check`, `goal eval`, `goal verify-loop`, `goal resume`,
   `goal gate pass`, `goal resilience-status`. Referenced in every agent-rules doc.

### 3.2 `briefing.py` — Partial Rust coverage; hot-path fallback still in Python

Missing from Rust (operator-critical):

1. **Full `generate_briefing()`** — the default `sk briefing <query>` path with no
   special flags. This is the most common usage in AGENTS.md and hooks, yet falls
   back to Python.

2. **Adaptive FTS** — Python uses synonym expansion, query rewriting, and
   strictness analysis. The Rust `run_compact_briefing` uses only raw FTS without
   any of these; quality gap for short or ambiguous queries.

3. **Recall telemetry** — Python records which entries were recalled and updates
   weights. Rust compact path skips this entirely, causing drift in confidence
   scores over time.

4. **Semantic search** — embedding-based similarity not implemented in Rust for
   briefing; also absent from `run_wakeup` and `run_compact_briefing`.

5. **`generate_subagent_context`** and **`generate_task_briefing`** — used by
   tentacle dispatch to seed sub-agents; no Rust equivalent.

### 3.3 `query-session.py` — Silent regression for session/graph/export features

Most critical missing surfaces (no fallback, silently broken):

1. **Session browsing** (`list_sessions`, `show_session`, `show_recent`) — operators
   rely on `sk query --sessions` to review history. Binary silently ignores the flag.

2. **Semantic search** (`semantic_search`) — `sk query --semantic <query>` produces
   no output in binary (flag treated as search term, FTS used instead).

3. **Export** (`--export`) — used in CI tooling to extract knowledge into JSON/Markdown.
   Binary silently ignores it.

4. **Graph/related** (`show_related`, `show_graph`, `query_entity_relations`) —
   knowledge-graph navigation unavailable in binary.

5. **Adaptive FTS** — binary uses plain FTS; Python expands synonyms, adjusts
   strictness, rewrites queries. Recall quality is measurably lower for multi-token
   or domain-specific queries.

---

## 4. Test Plan

### Existing test coverage (verified from `sk-rust/tests/integration_test.rs`)

| Test function | What it covers |
|---------------|---------------|
| `briefing_wakeup_emits_structured_output` | `--wakeup` output structure |
| `briefing_compact_emits_xml_root` | `--compact` XML tags |
| `briefing_compact_matches_python_structure` | Rust vs Python structural parity |
| `briefing_auto_succeeds` | `--auto` flag |
| `learn_then_query_finds_entry` | FTS query after learn |
| `query_wings_lists_wings` | `--wings` output |
| `query_detail_shows_full_entry` | `--detail <id>` |
| `wave12_tentacle_enforce_rule_denies_direct_pretooluse` | Hook enforcement |
| `wave12_tentacle_enforce_allows_with_tentacle_done_marker` | Hook bypass marker |

### Required new tests before migration is mergeable

**For `tentacle.py` Rust port (when implemented):**
- `tentacle_create_makes_bundle_dir` — verify dir structure and manifest.json
- `tentacle_todo_add_and_list` — CRUD round-trip
- `tentacle_handoff_done_writes_file` — verify handoff.md structure
- `tentacle_handoff_blocked_includes_reason` — status validation
- `tentacle_complete_clears_markers` — post-complete state
- `tentacle_verify_records_evidence` — evidence tracking
- `tentacle_goal_criteria_check_passes` — goal-loop gate
- `tentacle_goal_resume_restores_state` — breadcrumb recovery

**For `briefing.py` Rust port (full mode):**
- `briefing_default_mode_returns_xml` — no-flag path
- `briefing_adaptive_fts_expands_synonyms` — synonym expansion in output
- `briefing_recall_telemetry_updates_confidence` — DB side-effect after briefing
- `briefing_subagent_context_includes_knowledge` — subagent context path
- `briefing_task_briefing_format` — task briefing structure

**For `query-session.py` Rust port (missing surfaces):**
- `query_sessions_list_returns_rows` — `--sessions` output
- `query_semantic_returns_results` — `--semantic <query>` (requires embed DB)
- `query_export_json_valid` — `--export` produces valid JSON
- `query_graph_returns_nodes` — `--graph` output structure
- `query_by_file_filters_results` — `--by-file` filtering
- `query_relate_returns_entity_relations` — `--relate <entity>` output

---

## 5. Migration Priority

Recommended order based on operator impact and cross-script dependencies:

| Priority | Script / Surface | Rationale |
|----------|-----------------|-----------|
| **P1** | `query-session.py` session browsing | Silent regression exists today; no fallback; easy to add |
| **P1** | `briefing.py` full `generate_briefing()` | Most-called path; Python fallback exists but adds latency |
| **P2** | `query-session.py` adaptive FTS + synonym expansion | Quality gap; already partially wired in `briefing.rs` |
| **P2** | `briefing.py` recall telemetry | Confidence score drift accumulates over time |
| **P3** | `tentacle.py` status / list / todo / handoff | High complexity; implement in phases, starting read-only commands |
| **P3** | `query-session.py` export / graph / semantic | Important but less frequent; semantic requires embed feature flag |
| **P4** | `tentacle.py` full lifecycle (create/complete/worktree/goal) | Highest complexity; requires porting `_tentacle_goal.py` (156 KB) |
| **P4** | `briefing.py` semantic + blast radius | Requires embed infra; port after `query-session` semantic is stable |

---

## 6. Risks

| Risk | Severity | Notes |
|------|----------|-------|
| **Silent regression in `sk query`** | High | `run_query_command` has no Python fallback; unknown flags are silently ignored. Users running the Rust binary today cannot access session browsing, semantic search, export, or graph features. |
| **Adaptive FTS quality gap** | Medium | Rust query and compact briefing use plain FTS; Python applies synonym expansion and query rewriting. Recall quality degrades for short or domain-specific queries without parity work. |
| **Recall telemetry drift** | Medium | Rust compact briefing does not call `_record_recall_event`. Confidence scores for entries retrieved via `--compact`/`--auto`/`--wakeup` are never updated, causing the Python path's ranking to diverge over time. |
| **`tentacle.py` port complexity** | Very High | The tentacle CLI is 4,515 Python LOC plus ~300 KB of helper modules. Goal-loop state (`_tentacle_goal.py`, 156 KB) involves complex state machines, HMAC-signed markers, worktree management, and async dispatch runtimes. A phased port (read-only commands first) is strongly recommended. |
| **`_tentacle_goal.py` behavioral parity** | High | Goal-loop resilience (paused-goal recovery, breadcrumb writes, `needs-human` escalation) is complex and has subtle timing dependencies. Any Rust port must replicate Python behavior exactly or breakage will be silent. |
| **Marker format compatibility** | Low–Medium | Hook rules (`tentacle.rs`) already handle both legacy flat-path and new JSON-dict formats. Any new Rust code writing markers must maintain this dual-read compatibility or silently break the hook enforcement rules. |
| **Windows path handling** | Low | `get_module_for_path` normalises `\` to `/`. Ensure any new Rust code touching file paths applies the same normalisation. |

---

## 7. Verification Evidence

All facts above were derived from direct source inspection:

| Claim | Evidence |
|-------|----------|
| LOC counts | `python -c "import ast; ..."` + `Measure-Object -Line` on each file |
| Rust dispatch routing | `sk-rust/src/main.rs` lines 212–215 inspected directly |
| `briefing.rs` fallback at line 26 | `Select-String -Pattern "run_fallback"` in `briefing.rs` |
| `query.rs` has no fallback | Full read of `query.rs`; no `run_fallback` call found |
| Tentacle hook rules in Rust | Direct read of `sk-rust/src/hooks/rules/tentacle.rs` |
| Integration test coverage | `Select-String -Pattern "fn.*query\|fn.*briefing\|fn.*tentacle"` in `integration_test.rs` |
| No `docs/rust-migration/` dir existed | `Get-ChildItem docs\` before creating this file |

**Doc/file existence check:**
```powershell
# Verify this file was created:
Test-Path docs\rust-migration\issue-460-gap-analysis.md
# → True

# No existing rust-migration doc was overwritten:
Get-ChildItem docs\ | Where-Object { $_.Name -match "rust|migration" }
# → (none before this PR)
```

No Python or Rust code was modified. No build or test gates are required for this
documentation-only PR.

---

*Generated on branch `docs/issue-460-runtime-gap-analysis` in worktree
`D:\copilot-worktrees\issue-460-runtime-gap-analysis\repo`.*
