# Usage Guide

> Full command reference for all copilot-session-knowledge tools.

## sk — Unified CLI

`sk` is the unified front door over all standalone scripts. After the standard install, `sk` is available on your PATH automatically — no alias or pip install needed. Run `sk <command>` for day-to-day use; direct-script invocation (`python3 ~/.copilot/tools/sk.py <command>` or `python3 ~/.copilot/tools/<script>.py`) remains available as a fallback for bootstrapping, CI pipelines, and advanced use.

### Top-level commands

```bash
sk briefing "implement user CRUD"        # → briefing.py
sk query "docker error"                  # → query-session.py
sk learn --mistake "Title" "Description" # → learn.py
sk tentacle create api-export --scope "src/api/*.py" --desc "Export API"  # → tentacle.py
sk install --deploy-skill                # → install.py
sk setup --profile python                # → setup-project.py
sk update                                # → auto-update-tools.py
sk update --force
sk browse --port 8080 --token TOKEN      # → browse.py
sk benchmark record                      # → benchmark.py
sk retro                                 # → retro.py
sk heal                                  # → copilot-cli-healer.py
```

### `sk index` — knowledge index lifecycle

```bash
sk index build       # build-session-index.py  — index session files → FTS5 DB
sk index extract     # extract-knowledge.py    — classify + deduplicate entries
sk index migrate     # migrate.py              — apply DB schema migrations
sk index status      # index-status.py         — row counts, FTS integrity, offset coverage
sk index health      # knowledge-health.py     — health dashboard + recall telemetry
sk index embed       # embed.py / native Rust  — configure/run semantic embeddings
                     #   native (wave3 default): --build, --test, --rebuild-tfidf, --setup, --status, --providers, --search
                     #   Python fallback: embed.py (if native-embed feature unavailable)
```

### `sk sync` — cross-machine sync

```bash
sk sync config --setup https://gateway.example.com  # sync-config.py --setup
sk sync config --status                              # sync-config.py --status
sk sync run --once                                   # native Rust (default build); Python sk.py shim → sync-daemon.py --once
sk sync run --daemon                                 # native Rust (default build); Python sk.py shim → sync-daemon.py --daemon
sk sync status                                       # sync-status.py
sk sync status --health-check                        # sync-status.py --health-check
sk sync gateway --host 127.0.0.1 --port 8765         # sync-gateway.py (reference/mock)
sk sync merge --source /path/to/other.db             # sync-knowledge.py
```

### `sk checkpoint` — session checkpoints

```bash
sk checkpoint save --title "Auth done" --overview "JWT added"   # checkpoint-save.py
sk checkpoint restore --list                                     # checkpoint-restore.py --list
sk checkpoint restore --show latest                             # checkpoint-restore.py --show latest
sk checkpoint diff --from 1 --to latest                         # checkpoint-diff.py
```

### `sk profile` — workflow profiles

```bash
sk profile build --name myteam --hooks dangerous-blocker.py --phases BUILD TEST COMMIT
                                                    # profile-builder.py
sk profile import --file myteam.json                # profile-import.py
sk profile export --profile myteam --output out.json # profile-export.py
```

### `sk context` — project context

```bash
sk context project                  # project-context.py  — write project-context.md
sk context project --stdout         # project-context.py --stdout
sk context map                      # codebase-map.py     — refresh codebase structure snapshot
```

### `sk scout` — trend scout

```bash
sk scout run                        # trend-scout.py
sk scout run --dry-run              # trend-scout.py --dry-run
sk scout config                     # scout-config.py
sk scout status                     # scout-status.py
```

### `sk hooks` — hook runner (hybrid: richer Rust direct path + selective native routing for managed events)

> **Wave13 hybrid state:** `sk hooks` is available in both the Rust binary and the Python `sk.py` compatibility shim. The following managed events route natively through the Rust runner (**Rust-binary installs only**):
> - `agentStop`, `subagentStop` (wave3): native via `tentacle.py marker-cleanup --from-stop-event`
> - `sessionEnd` (wave4+wave9): native `SessionEndRule` — per-session marker cleanup via `COPILOT_AGENT_SESSION_ID` + `session.log` write; wave9 adds `RecurrenceDetectorRule` — increments `recurrence_after_briefing` counter in `knowledge.db`
> - `errorOccurred` (wave5): native `ErrorOccurredRule` — queries `knowledge.db` directly via native Rust FTS5 as primary path; falls back to `query-session.py` subprocess **only** when the DB is genuinely unavailable (e.g. first-run before migration)
> - `sessionStart` (wave9): native `AutoBriefingRule` — spawns `briefing.py` (10s bounded timeout), signs HMAC `briefing-done` + `codebase-map-ran` markers; `IntegrityRule` — verifies/refreshes SHA256 hook-file manifest; `SessionStartRule` — emits acknowledgement
> - `postToolUse` (**wave10**): all seven postToolUse rules fully ported natively (`TrackEditsRule`, `LearnReminderRule`, `TestReminderRule`, `NextjsTypecheckReminderRule`, `VerificationGatePostRule`, `ReadBeforeEditRule`, `TentacleSuggestRule`); `sync_markers.rs` writes `sync-nudge.json` after dispatch; native runner is now sole writer for postToolUse markers
> - `preToolUse` (**wave13, Rust-binary installs**): `SyntaxGateRule` (via `python_exe()` + `py_compile` subprocess; fail-open) added to native runner; `preToolUse` is now in `NATIVE_EVENTS` — `sk hooks run preToolUse` routes natively for Rust-binary installs. All deny-capable preToolUse rules are active on this path. **Python `sk.py` shim boundary unchanged**: the Python shim still routes `sk hooks run preToolUse` through `hook_runner.py`. `hooks/rules/syntax_gate.py` and `hook_runner.py` are NOT removed. Windows proof accepted; WSL/Linux/macOS not separately re-proved in wave13.
>
> **Wave6–13 direct-path additions:** direct `sk hooks <event>` now includes:
> - sessionStart: `AutoBriefingRule` + `IntegrityRule` (wave9, same as managed path above)
> - preToolUse: `subagent-git-guard` (deny), `block-edit-dist` (deny), `block-unsafe-html` (deny), `pnpm-lockfile-guard` (deny, wave7), `read-before-edit` warn (wave7), `VerificationGatePreRule` dirty-marking + informational deny (wave8), **`EnforceBriefingRule` deny-capable (wave11)**, **`EnforceLearnRule` deny-capable (wave11)**, **`TentacleEnforceRule` deny-capable (wave12)**, **`SyntaxGateRule` fail-open via py_compile subprocess (wave13)**
> - postToolUse: `TrackEditsRule` bash counter/list-marker writes + direct edit/create `tentacle-edits` accumulation (wave8), `LearnReminderRule`, `TestReminderRule` (full counter-write, wave7), `NextjsTypecheckReminderRule` (full counter-write, wave7), `ReadBeforeEditRule` (wave7), `VerificationGatePostRule` evidence-recording (wave7), `TentacleSuggestRule` read-only (wave8)
>
> **Three `preToolUse` paths (wave13):**
> - Direct Rust path (`sk hooks preToolUse`): native runner; all deny rules active including `SyntaxGateRule`
> - Managed Rust path (`sk hooks run preToolUse`, Rust binary): native runner via `NATIVE_EVENTS`; same rules including `SyntaxGateRule`; wave13 routing flip
> - Python shim path (`sk hooks run preToolUse`, Python `sk.py`): `hook_runner.py`; shim always delegates to Python; unchanged
>
> **Watch/bootstrap boundaries (wave20 update)**: First-run DB bootstrap is native (wave18); `SEMANTIC_PROXIMITY` is native (wave19). **Wave20:** `watch.rs` never spawns Python on any path — the last-resort `spawn_indexer()` fallback is removed. On genuine DB open/create or extract failure, `watch` emits a recovery hint naming the manual command; no Python subprocess is launched. `extract-knowledge.py --semantic-only` remains available for manual/fallback use only. `extract-knowledge.py` and `build-session-index.py` are intentional manual operator tools — NOT auto-called and NOT removed. `migrate.py` remains canonical for versioned schema upgrades. `native-extract` is a **default Cargo feature** since wave15. Wave20 proof (Windows-local): `cargo test --quiet` (536 unit + 74 integration). WSL/Linux/macOS not separately re-proved. The Python `sk.py` shim routes ALL hook events to `hook_runner.py` regardless of wave. See [docs/HOOKS.md](HOOKS.md) for the full parity gap analysis and preToolUse routing history.

```bash
sk hooks run sessionStart           # native Rust: AutoBriefingRule + IntegrityRule  (wave9 — briefing.py spawn + HMAC markers)
sk hooks run preToolUse             # native Rust (Rust binary): SyntaxGateRule + all deny rules  (wave13 — py_compile subprocess; fail-open)
                                    # hook_runner.py (Python sk.py shim): unchanged — shim always routes to Python
sk hooks run postToolUse            # native Rust: all 7 postToolUse rules  (wave10 — sync-nudge.json via sync_markers)
sk hooks run sessionEnd             # native Rust: SessionEndRule + RecurrenceDetectorRule  (wave4+wave9 — marker cleanup + session.log + recurrence counter)
sk hooks run agentStop              # native Rust: marker-cleanup  (wave3 — tentacle.py --from-stop-event)
sk hooks run subagentStop           # native Rust: marker-cleanup  (wave3 — tentacle.py --from-stop-event)
sk hooks run errorOccurred          # native Rust: ErrorOccurredRule → native FTS5 (wave5); query-session.py subprocess only if DB unavailable
```

The managed `hooks.json` prefers `sk hooks run <event>` when `sk` is in PATH. Bash falls back to `python3 hook_runner.py`; PowerShell falls back to `python hook_runner.py`. Install the launcher first: `python install.py --install-sk`.

### `sk watch` — session watcher (hybrid: Rust native loop/indexer + Python last-resort/shim fallback)

> **Wave10 watch boundary:** `sk watch` is available in both the Rust binary and the Python `sk.py` shim. The Rust binary indexes both Copilot session-state changes (wave2) and Claude `.jsonl` session changes (wave3) natively. Wave5 narrowed the watch blockers: sessions-table column migrations (`file_mtime`, `indexed_at_r`, `fts_indexed_at`, `event_count_estimate`) are applied natively via `apply_sessions_column_migrations()` before each indexing pass; sync-op enqueueing (writing to `sync_txns`/`sync_ops`) is native via `enqueue_doc_sync_op_fail_open()` (fail-open when sync schema absent). Wave6 closes the confirmed `sessions_fts` gap for the non-JSONL Copilot path with a native local-only writer. **Wave10** removes `build-session-index.py --incremental` for existing-DB non-JSONL Copilot changes — that path is now fully native. Remaining Python-backed surfaces: `extract-knowledge.py` classification and first-run DB creation fallback (these are intentionally preserved).
>
> **Wave15 (`native-extract` is now a default Cargo feature):** `sk-rust/src/index/extract.rs` handles the hot-path classification/write loop for `knowledge_entries`/`ke_fts` natively in the default Rust binary. Sync-op enqueue parity for native-extract writes landed in wave15. Error lifecycle metadata (`error_type`, `root_cause`, `severity`) is now filled natively for mistake entries. Integration proof: `sk-rust/tests/integration_test.rs`. Python `spawn_extractor()` still runs for `knowledge_relations`, relation budgets, semantic proximity, backfill, and confidence decay. First-run DB bootstrap (`spawn_indexer()`) remains Python-backed and unchanged. `extract-knowledge.py` is **NOT removed**; it remains authoritative for relation extraction and all non-hot-path NLP. Windows proof accepted: `cargo test --quiet`, `python tests\test_indexing.py`, and `python tests\test_hook_compat.py` all passed after wave15 audit. WSL/Linux/macOS were not separately re-proved.
>
> **Wave16 (native relation slice):** Deterministic knowledge relations — `SAME_SESSION`, `SAME_TOPIC`, `TAG_OVERLAP`, `RESOLVED_BY` — are now extracted natively by Rust (`sk-rust/src/index/extract.rs`). After a successful native extract pass, `sk watch` invokes `extract-knowledge.py --residual-only`, narrowing Python residual ownership to: `SEMANTIC_PROXIMITY`, backfill helpers, confidence decay, and non-hot-path NLP. `extract-knowledge.py` is **NOT removed**. First-run DB bootstrap (`spawn_indexer()`) is unchanged. Windows proof: `cargo test --quiet` (519 Rust unit + 71 integration), `python tests\test_indexing.py` (25/25). WSL/Linux/macOS not separately re-proved.
>
> **Wave17 (native residual helpers):** `backfill_affected_files`, `infer_task_ids`, and confidence decay now run natively in Rust after a successful native extract pass. Python is only invoked when scikit-learn is available, and only for `SEMANTIC_PROXIMITY` (`extract-knowledge.py --semantic-only`). When sklearn is absent, **no Python subprocess is launched** on the successful native watch path. First-run DB bootstrap (`spawn_indexer()`) remains Python-backed. The Python `sk.py` shim/no-binary install paths remain fully Python-backed regardless of sklearn availability. `extract-knowledge.py` is **NOT removed**. Proof (Windows-local): `cargo test --quiet` (531 Rust unit + 72 integration), `python tests\test_indexing.py` (28/28). WSL/Linux/macOS not separately re-proved.
>
> **Wave18 (native first-run DB bootstrap):** `session.rs`, `claude.rs`, and `extract.rs` each call `open_or_create_index_db` / `ensure_extract_tables` when `knowledge.db` is absent. The extract-owned tables (`knowledge_entries`, `ke_fts`, `knowledge_relations`, `embedding_meta`) are now created natively on first run. `None` from either native indexer now means a genuine DB open/create failure (e.g. filesystem permission error) — NOT merely "DB absent". `spawn_indexer()` (Python bootstrap via `build-session-index.py`) is now only triggered as a last-resort fallback for real creation failures, not for a missing DB. `migrate.py` remains the canonical owner of versioned schema upgrades; wave18 only creates absent tables and does **NOT** replace `migrate.py`. Python is still invoked for `SEMANTIC_PROXIMITY` when sklearn is available (`--semantic-only`). The Python `sk.py` shim/no-binary install paths remain fully Python-backed. `extract-knowledge.py` is **NOT removed**. Proof (Windows-local): `cargo test --quiet` (535 Rust unit + 73 integration), `python tests\test_indexing.py` (28/28). WSL/Linux/macOS not separately re-proved.
>
> **Wave19 (native SEMANTIC_PROXIMITY):** `SEMANTIC_PROXIMITY` is now computed natively in Rust (`watch.rs` via `sk-rust/src/embeddings/tfidf.rs` TF-IDF cosine). The successful native watch path **no longer auto-spawns Python at all**. `extract-knowledge.py --semantic-only` remains available for manual/fallback use but is **NOT** auto-called on the successful native watch path. Python is still triggered as a last-resort fallback on genuine DB creation failures. Python `sk.py` shim/no-binary install paths remain fully Python-backed. Three post-landing fixes landed after the initial wave19 code review: no-spawn tests now use side-channel flag files (not stdout sentinels); native bootstrap schema includes `error_type`/`root_cause`/`severity` for fresh DBs; backfill updated-count now tracks actual affected rows. `extract-knowledge.py` is **NOT removed**. Proof (Windows-local): `cargo test --quiet` (536 Rust unit + 73 integration), `python tests\test_indexing.py` (28/28). WSL/Linux/macOS not separately re-proved.
>
> **Wave20 (zero Python spawns; intentional boundaries declared):** `watch.rs` **never** spawns Python — including on error paths. The last-resort `spawn_indexer()` Python fallback on genuine DB open/create failures (wave18/19) is removed. On genuine DB open/create or native-extract failure, `watch` emits a structured recovery hint to stderr naming the exact manual command (`python build-session-index.py --incremental` or `python extract-knowledge.py`). **The following Python surfaces are intentional and not removed:** Python `sk.py` shim / no-binary installs remain fully Python-backed for all commands; `hook_runner.py` is the Python hook runner for shim and non-binary installs; `build-session-index.py`, `extract-knowledge.py` (including `--semantic-only`), and `migrate.py` are intentional manual operator tools. Integration proof: `wave20_db_failure_emits_recovery_no_python_spawn` in `sk-rust/tests/integration_test.rs`. Proof (Windows-local): `cargo test --quiet` (536 Rust unit + 74 integration). WSL/Linux/macOS not separately re-proved.

```bash
sk watch                            # watch-sessions.py
sk watch --once                     # watch-sessions.py --once
sk watch --service                  # watch-sessions.py --service
sk watch --install-hint             # watch-sessions.py --install-hint
```

`auto-update-tools.py --restart-watch` also prefers `sk watch` when the native binary is installed at `~/.copilot/bin/sk-native` (Unix) or `~/.copilot/bin/sk.exe` (Windows).

> **Direct-script fallback:** many `sk` subcommands still delegate to the underlying `python3 ~/.copilot/tools/<script>.py` entrypoint. After wave20: `briefing`, `learn`, `query` have stable routing; `watch` has a native loop + native indexer (Copilot and Claude JSONL) + native sessions-table column migrations + native sync-op enqueueing (wave5) + native local-only `sessions_fts` writer (wave6) + no longer spawns `build-session-index.py --incremental` for existing-DB non-JSONL Copilot changes (wave10) + **native hot-path `knowledge_entries`/`ke_fts` writer (`native-extract` is now a default Cargo feature since wave15)** + **native deterministic relation extraction (wave16)** + **native residual helpers (wave17)** + **native first-run DB bootstrap (wave18)** + **native SEMANTIC_PROXIMITY (wave19)** + **wave20: no Python subprocess on any path, including error paths** — on DB open/create or extract failure, `watch` emits a structured recovery hint naming the exact manual command; `extract-knowledge.py --semantic-only` remains available for manual/fallback use only; `migrate.py` remains canonical for versioned schema upgrades; `index embed --build` is native (`native-embed` is a default feature); `sync run` in the **default Rust build** routes natively (`native-sync` is a default Cargo feature since wave4) — `sync-daemon.py` remains the fallback for the Python `sk.py` shim and installs without a compiled binary. Hook events `sessionStart` (wave9), `sessionEnd` (wave4+wave9), `errorOccurred` (wave5), `postToolUse` (wave10), and `preToolUse` (wave13, Rust-binary installs) route natively; the Python `sk.py` shim routes `sk hooks run preToolUse` through `hook_runner.py` — shim behavior unchanged. `hooks/rules/syntax_gate.py` and `hook_runner.py` are NOT removed. `extract-knowledge.py` is NOT removed — intentional manual operator tool. Python `sk.py` shim/no-binary paths remain fully Python-backed. If `sk` is unavailable, use the direct-script form shown in each section below.

---

## Briefing

Run before every major task to surface past mistakes and relevant knowledge:

```bash
# macOS/Linux: python3 | Windows: python or py
brief "implement user CRUD"          # Compact ~500 tokens
brief "implement user CRUD" --full   # Full detail ~3K tokens
brief --auto                         # Auto-detect from git state
brief --wakeup                       # Ultra-compact (~170 tokens) for session start
brief --titles-only                  # Index only (~10 tok/entry) — progressive disclosure
brief --titles-only "DynamoDB"       # Filtered titles
brief --wing backend --room patient  # Filter by wing/room (palace-style)
brief "task" --for-subagent --budget 3000  # Manual compatibility path for ad hoc sub-agent injection
brief "task" --min-confidence 0.7    # High-quality entries only
brief "task" --for-subagent          # Manual compatibility context block for sub-agent prompts
brief "task" --compact               # XML compact block for AI context injection
brief --task "memory-surface"        # Task-scoped recall: entries tagged with this task ID
brief --task "memory-surface" --json # Includes source_document + code-location/snippet fields
brief "fix Docker" --json            # JSON output for programmatic use
```

Token-distillation flags: `--compact` produces an XML compact block; `--budget N` hard-caps output to N characters (frozen snapshot, highest-confidence entries first); `--titles-only` gives ~10 tok/entry for progressive disclosure.

For tentacle delegation, prefer `tentacle.py ... --briefing`: it injects bounded `[KNOWLEDGE EVIDENCE]` from task-scoped JSON recall first, then `--pack` fallback when task recall is empty. Bullets stay unchanged; runtime may add one optional bounded `From:` provenance line. Keep `--for-subagent` for manual compatibility and ad hoc prompts.

## Search

```bash
qs "search terms"                    # Compact results
qs "search terms" --verbose          # Full content
qs "docker" --type research          # Filter by doc type
qs "search" --budget 2000            # Cap output to 2000 chars
qs "search" --compact                # Titles-only with ~token hint
qs "spring" --source copilot         # Filter by agent source
qs --mistakes                        # View past errors
qs --patterns                        # View best practices
qs --decisions                       # View architecture decisions
qs --file src/auth.py                # Entries that touched a specific file
qs --module auth                     # Entries for a module or directory
qs --task memory-surface             # Entries tagged with a specific task ID
qs --task memory-surface --export json   # JSON object with entries[] (includes snippet_freshness + related_entry_ids)
qs --diff                            # Entries for files in the current git diff
qs "search" --export json            # Export results as JSON
qs "search" --export markdown        # Export results as Markdown
```

Default `qs "query"` telemetry records the full emitted search surface (primary block + `sessions_fts` block + knowledge-entry block), not just the first printed block.

## Drill Down

Use entry ID from search results:

```bash
qs --detail 2045                     # View full entry details (+ Snippet freshness: fresh|drifted|missing|unknown)
qs --context 2045                    # Entry + entries from same session
qs --related 2045                    # Entry + knowledge graph connections
qs --graph "spring boot"             # Mini knowledge graph by topic
```

`qs --detail <id>` writes a stateless `detail_open` telemetry row:
- existing ID → `hit_count=1`, `selected_entry_ids=[id]`
- missing ID → `hit_count=0`, `selected_entry_ids=[]`

## Recall Telemetry Stats

```bash
python3 ~/.copilot/tools/knowledge-health.py --recall         # Recall-only text dashboard
python3 ~/.copilot/tools/knowledge-health.py --recall --json  # Recall-only JSON payload
```

- `--recall` output is recall-only (it does not append the default health dashboard).
- `recall_events` is lean telemetry only (IDs/counts/output size), not verbose payload logging.
- If `recall_events` is absent (older schema), recall commands still work and stats report unavailable/empty.
- Browse UI, contextual summaries, and provider rerank are out of scope for this telemetry surface.

## Semantic Search

Requires an embedding API key (optional):

```bash
qs "deployment error" --semantic     # Search by meaning (compact output; no feedback fragment)
qs "deployment error" --semantic --verbose  # Adds feedback bias fragment only when non-zero
python3 ~/.copilot/tools/embed.py --setup   # Setup API key
```

## Sync Rollout (Local-First, Optional)

Sync is local-first: `~/.copilot/session-state/knowledge.db` stays primary for reads/search.
Remote sync is optional replication transport, not the query authority.

### Configure sync endpoint (single connection string)

`sync-config.py` stores one `connection_string` in `~/.copilot/tools/sync-config.json` (HTTP(S) gateway URL, not raw Postgres/libSQL DSN).

```bash
python3 ~/.copilot/tools/sync-config.py --setup https://gateway.example.com
python3 ~/.copilot/tools/sync-config.py --setup-env SYNC_GATEWAY_URL
python3 ~/.copilot/tools/sync-config.py --status
python3 ~/.copilot/tools/sync-config.py --status --json
python3 ~/.copilot/tools/sync-config.py --get
python3 ~/.copilot/tools/sync-config.py --clear
```

### Run sync runtime

> **Wave4 state (`native-sync` now in default Cargo features):** `sk sync run` is backed by a native Rust daemon layer (lock, signal, adaptive loop) in `sk-rust/src/commands/sync_run.rs`. The HTTP push/pull engine and FTS refresh (`knowledge_fts`/`ke_fts`) are compiled under the `native-sync` Cargo feature. Since wave4, `native-sync` is in the **default** feature set (`default = ["native-embed", "native-sync"]`), so the standard compiled `sk` binary routes `sk sync run` natively — including push, pull, and FTS refresh. The Python `sk.py` shim and any install without a compiled binary still delegate to `sync-daemon.py --once` as the Python subprocess fallback.

```bash
python3 ~/.copilot/tools/sync-daemon.py --once
python3 ~/.copilot/tools/sync-daemon.py --daemon
python3 ~/.copilot/tools/sync-daemon.py --interval 30
python3 ~/.copilot/tools/sync-daemon.py --push-only
python3 ~/.copilot/tools/sync-daemon.py --pull-only
```

If `connection_string` is unset, daemon mode remains local-only (idle/no-op for remote sync).
When backlog is large, daemon applies adaptive per-cycle limits automatically, paginates pull within one cycle, and refreshes local `knowledge_fts` / `ke_fts` rows touched by pulled canonical changes.

### Inspect sync status

```bash
python3 ~/.copilot/tools/sync-status.py
python3 ~/.copilot/tools/sync-status.py --json
python3 ~/.copilot/tools/sync-status.py --watch-status --json
python3 ~/.copilot/tools/sync-status.py --health-check --json   # exit 0/2
python3 ~/.copilot/tools/sync-status.py --audit --json          # exit 0/2
python3 ~/.copilot/tools/auto-update-tools.py --restart-watch
python3 ~/.copilot/tools/auto-update-tools.py --watch-status
python3 ~/.copilot/tools/auto-update-tools.py --health-check
python3 ~/.copilot/tools/auto-update-tools.py --audit-runtime
```

### Browse diagnostics surfaces (read-only)

- `GET /healthz` includes: `sync_status_endpoint: "/api/sync/status"`
- `GET /api/sync/status` reports local sync diagnostics (configured endpoint preview, queue/failure counts, cursor and replica state)

### Reference/mock gateway

`sync-gateway.py` is intentionally **reference/mock only** in this repo.
For provider-backed rollout, the default recommendation is Neon (backing Postgres) + Railway (thin gateway host) while keeping the same HTTP gateway contract.

```bash
python3 ~/.copilot/tools/sync-gateway.py --host 127.0.0.1 --port 8765
```

Endpoints: `/sync/push`, `/sync/pull`, `/healthz`.

## Record Knowledge (learn.py)

```bash
# 7 observation types
learn --mistake "Title"   "What went wrong and fix"         --tags "docker,compose"
learn --pattern "Title"   "What works well / best practice" --tags "lambda"
learn --decision "Title"  "Architecture decision rationale" --tags "cdk"
learn --tool "Title"      "Useful tool/config details"      --tags "vscode"
learn --feature "Title"   "New feature implementation"      --tags "api"
learn --refactor "Title"  "Code improvement description"    --tags "cleanup"
learn --discovery "Title" "Codebase finding or insight"     --tags "dynamodb"

# Tag entry with a task ID and affected files (for task-scoped recall)
learn --mistake "Title" "Description" --task "memory-surface" --file "briefing.py" --file "learn.py"

# Attach a concrete code location (path:line or path:start-end)
learn --pattern "FTS sanitizer fix" "Strip operators before MATCH" --code-location "query-session.py:120-142"

# Structured facts (discrete, verifiable statements)
learn --pattern "DynamoDB Batch Ops" "How to use batch writes" \
  --fact "batch write limit is 25 items" \
  --fact "GSI eventually consistent"

# Palace categorization
learn --mistake "Auth bug" "Description" --wing backend --room auth

# Knowledge graph relations
learn --relate "copyToGroup" "reads_from" "patient-dynamic-form.json"
learn --relate "addPatient Lambda" "writes_to" "dataTable"

# Bulk import
learn --from-file notes.md  # Format: ## category: Title

# View
learn --list               # Recent entries
learn --stats              # Knowledge base statistics

# JSON output (machine-readable; emits JSON object with id, title, category, tags, etc.)
learn --mistake "Title" "Description" --json
```

## Palace Concepts (Wing/Room)

Organize knowledge hierarchically:

| Wing | Description | Example Rooms |
|------|-------------|---------------|
| `backend` | Lambda, DynamoDB, SQS, API | patient, websocket, auth, dynamodb |
| `frontend` | Expo, React Native, screens | navigation, components, hooks |
| `testing` | Jest, Playwright, E2E | e2e, unit-test |
| `infrastructure` | CDK, VPC, CloudWatch | cdk, vpc, cloudwatch |
| `devops` | Git, CI/CD, Docker | git, pipeline, proxy |
| `shared` | TypeScript, ESLint, i18n | typescript, openapi |

Wings and rooms are **auto-detected** from tags/title. Override with `--wing`/`--room`.

## Codebase Map

`codebase-map.py` generates a structural snapshot of the current project (file tree, key modules) and writes it to the session `files/` directory.

```bash
python3 ~/.copilot/tools/codebase-map.py            # Refresh codebase map for current project
```

The map is **automatically refreshed at session start** by `hooks/auto-briefing.py` — no manual step needed during normal workflow.

## Checkpoint Save

`checkpoint-save.py` writes structured checkpoint files to `~/.copilot/session-state/<session>/checkpoints/`. Checkpoints are **never auto-written** — the agent must call this explicitly.

```bash
python3 ~/.copilot/tools/checkpoint-save.py \
  --title "Implemented auth module" \
  --overview "Added JWT login/logout" \
  --next_steps "Add refresh token support"

python3 ~/.copilot/tools/checkpoint-save.py --list   # List checkpoints for current session
python3 ~/.copilot/tools/checkpoint-save.py --dry-run --title "Test" --overview "Preview only"
```

> **Session-end reminder (opt-in):** `hooks/session-end.py` is reminder-only — it never writes checkpoints automatically. Set `COPILOT_CHECKPOINT_REMIND=1` in your environment to log a reminder when a session ends without a saved checkpoint.

## Checkpoint Restore (read-only)

`checkpoint-restore.py` reads and displays checkpoints written by `checkpoint-save.py`. All operations are **read-only** — no session state is mutated.

```bash
python3 ~/.copilot/tools/checkpoint-restore.py --list                      # List all checkpoints
python3 ~/.copilot/tools/checkpoint-restore.py --show latest               # Show most recent
python3 ~/.copilot/tools/checkpoint-restore.py --show 1                    # Show by sequence number
python3 ~/.copilot/tools/checkpoint-restore.py --export latest             # Export as text (default)
python3 ~/.copilot/tools/checkpoint-restore.py --export latest --format md    # Markdown (indexer-compatible)
python3 ~/.copilot/tools/checkpoint-restore.py --export latest --format json  # Machine-readable JSON
python3 ~/.copilot/tools/checkpoint-restore.py --session SESSION_ID        # Specify a session
```

Selectors for `--show` / `--export`: `N` (sequence number), `latest`, `first`.

## Checkpoint Diff

`checkpoint-diff.py` compares two checkpoints and shows what changed. All operations are **read-only**.

```bash
python3 ~/.copilot/tools/checkpoint-diff.py --from 1 --to latest          # Diff checkpoint 1 vs latest
python3 ~/.copilot/tools/checkpoint-diff.py --from 2 --to 3               # Diff two specific checkpoints
python3 ~/.copilot/tools/checkpoint-diff.py --consecutive                  # Diff all consecutive pairs
python3 ~/.copilot/tools/checkpoint-diff.py --summary                      # Change progression across all
python3 ~/.copilot/tools/checkpoint-diff.py --show-unchanged               # Include unchanged sections
python3 ~/.copilot/tools/checkpoint-diff.py --session SESSION_ID           # Specify a session
```

## Profile Builder

`profile-builder.py` creates custom workflow profiles (saved to `presets/`) that can then be deployed via `setup-project.py --profile <name>` or `install-project-hooks.py --profile <name>`.

```bash
python3 ~/.copilot/tools/profile-builder.py --list-hooks                          # List available hook templates
python3 ~/.copilot/tools/profile-builder.py --list-phases                         # List available workflow phases
python3 ~/.copilot/tools/profile-builder.py \
  --name myteam \
  --description "My team workflow" \
  --hooks dangerous-blocker.py secret-detector.py commit-gate.py \
  --phases CLARIFY BUILD TEST COMMIT                                               # Create a profile
python3 ~/.copilot/tools/profile-builder.py --name myteam ... --dry-run           # Preview JSON without writing
python3 ~/.copilot/tools/profile-builder.py --name myteam ... --force             # Overwrite existing profile
```

## Profile Export

`profile-export.py` exports profiles from `presets/` to portable JSON files for sharing or backup.

```bash
python3 ~/.copilot/tools/profile-export.py --profile python --output python.json          # Export single profile
python3 ~/.copilot/tools/profile-export.py --profile python --output p.bundle.json --format bundle  # With metadata wrapper
python3 ~/.copilot/tools/profile-export.py --all --output-dir ./exported/                 # Export all profiles
python3 ~/.copilot/tools/profile-export.py --all --output all.bundle.json --format bundle # All in one bundle
python3 ~/.copilot/tools/profile-export.py --profile python --dry-run                     # Preview without writing
```

## Profile Import

`profile-import.py` imports profiles exported by `profile-export.py` back into `presets/`.

```bash
python3 ~/.copilot/tools/profile-import.py --file custom-profile.json                     # Import a profile
python3 ~/.copilot/tools/profile-import.py --file all-profiles.bundle.json                # Import bundle
python3 ~/.copilot/tools/profile-import.py --file bundle.json --name python               # Import one from bundle
python3 ~/.copilot/tools/profile-import.py --file custom.json --force                     # Overwrite existing
python3 ~/.copilot/tools/profile-import.py --file custom.json --dry-run                   # Validate without writing
```


## Tentacle Orchestration

Full workflow for multi-agent parallel execution across scoped work units.

### Step-by-step workflow

```bash
# 1. Create tentacle with scope + briefing
python3 ~/.copilot/tools/tentacle.py create api-export \
  --scope "src/api/*.py" --desc "Export API endpoints" --briefing

# 2. Add atomic todo items (one per sub-agent delegation unit)
python3 ~/.copilot/tools/tentacle.py todo api-export add "Generate OpenAPI schema"
python3 ~/.copilot/tools/tentacle.py todo api-export add "Add auth middleware"

# 3. (Optional) Pre-materialize isolated context bundle before dispatch
#    Writes briefing.md, instructions.md, skills.md, session-metadata.md,
#    recall-pack.json (machine-readable JSON recall), and manifest.json
#    to .octogent/tentacles/<name>/bundle/ for sub-agents that need artifacts on disk
python3 ~/.copilot/tools/tentacle.py bundle api-export

# 4. Dispatch — bundle is the default; prompts stay lean and surface bundle_path
python3 ~/.copilot/tools/tentacle.py swarm api-export \
  --agent-type general-purpose --model claude-sonnet-4.6 --briefing      # single prompt
python3 ~/.copilot/tools/tentacle.py swarm api-export --output parallel --briefing  # one per todo
python3 ~/.copilot/tools/tentacle.py swarm api-export --output json --briefing      # JSON + bundle_path
python3 ~/.copilot/tools/tentacle.py dispatch api-export --briefing                 # single dispatch + bundle ref
python3 ~/.copilot/tools/tentacle.py swarm api-export --no-bundle                   # rare tiny-prompt opt-out

# 5. Monitor runtime (read-only)
python3 ~/.copilot/tools/tentacle.py status                # dashboard: all tentacles + states

# 6. Sub-agent: cross-review then write structured handoff when done
#    Re-read every changed file, then write handoff with --status and --changed-file receipts
python3 ~/.copilot/tools/tentacle.py handoff api-export "Completed API export. OpenAPI schema written." \
  --status DONE --changed-file src/api/schema.py --changed-file src/api/auth.py --learn

# 7. Orchestrator: verify results and close
python3 ~/.copilot/tools/tentacle.py complete api-export   # marks done, auto-learns from handoff
```

### Operator status

```bash
python3 ~/.copilot/tools/tentacle.py create api-export \
  --scope "backend/lambda/export*" --desc "Export API" --briefing \
  --skill karpathy-guidelines --skill code-reviewer
python3 ~/.copilot/tools/tentacle.py status       # Dashboard: name, status, todos done/total, last update
python3 ~/.copilot/tools/tentacle.py show api-export   # Full details for one tentacle
python3 ~/.copilot/tools/tentacle.py list              # One-line list of all tentacles
```

### Worktree + verification runtime commands

```bash
python3 ~/.copilot/tools/tentacle.py worktree api-export prepare
python3 ~/.copilot/tools/tentacle.py worktree api-export status
python3 ~/.copilot/tools/tentacle.py swarm api-export --worktree
python3 ~/.copilot/tools/tentacle.py dispatch api-export --worktree
python3 ~/.copilot/tools/tentacle.py verify api-export "python3 test_fixes.py" --label "tests"
python3 ~/.copilot/tools/tentacle.py worktree api-export cleanup
```

### Operator runtime (auto-update + watch)

```bash
python3 ~/.copilot/tools/auto-update-tools.py --restart-watch   # Restart session watcher
python3 ~/.copilot/tools/auto-update-tools.py --watch-status    # Check watcher state
python3 ~/.copilot/tools/auto-update-tools.py --health-check    # Runtime health
python3 ~/.copilot/tools/auto-update-tools.py --audit-runtime   # Audit active runtime surfaces
```

### Bundle for isolated context

`tentacle.py bundle` materializes a per-run context bundle — all artifacts needed by a
sub-agent written to `.octogent/tentacles/<name>/bundle/`. `swarm` and `dispatch` now
create this bundle by default and keep generated prompts token-lean; run `bundle` directly
only when you want to inspect or pre-warm the artifacts before dispatch.

```bash
python3 ~/.copilot/tools/tentacle.py bundle api-export               # Fetch briefing + write all artifacts
python3 ~/.copilot/tools/tentacle.py bundle api-export --no-briefing    # Skip live prose briefing
python3 ~/.copilot/tools/tentacle.py bundle api-export --no-checkpoint  # Skip checkpoint context
python3 ~/.copilot/tools/tentacle.py bundle api-export --output json    # JSON manifest + bundle_path
```

Bundle artifacts:

| File | Description |
|------|-------------|
| `briefing.md` | Prose session-knowledge briefing (or placeholder) |
| `instructions.md` | Instruction-file surface (host AI config files) |
| `skills.md` | Skill-file catalogue (SKILL.md files) |
| `session-metadata.md` | Context, todos, handoff, checkpoint |
| `recall-pack.json` | **Machine-readable JSON recall** (task_json or pack mode; envelope includes `tentacle`, `created_at`, `source_mode`, and the raw briefing.py payload) |
| `manifest.json` | Index of all artifacts with `populated`/`source_mode` flags |

`recall-pack.json` is written on every bundle run. When recall data is available, `manifest.artifacts.recall_pack.populated` is `true` and `source_mode` is `"task_json"` (from `briefing.py --task --json`) or `"pack"` (from `briefing.py --pack` fallback). When both sources are empty or unavailable, `populated` is `false` and `source_mode` is `null`. `--no-briefing` only skips the prose `briefing.md` fetch; the machine-readable recall pack is still attempted.

`swarm` and `dispatch` materialize the bundle and surface its path in the prompt by default
so sub-agents know where to find full context. Use `--no-bundle` only for tiny/manual prompts:

```bash
python3 ~/.copilot/tools/tentacle.py swarm api-export --briefing
python3 ~/.copilot/tools/tentacle.py dispatch api-export --briefing
python3 ~/.copilot/tools/tentacle.py swarm api-export --briefing --worktree
python3 ~/.copilot/tools/tentacle.py dispatch api-export --briefing --worktree
python3 ~/.copilot/tools/tentacle.py swarm api-export --no-bundle
```

### Completing and verifying results

`tentacle.py complete` is the orchestrator verification step — it marks the tentacle done,
clears the active marker (unblocking `git commit`/`git push`), and auto-learns from `handoff.md`:

```bash
python3 ~/.copilot/tools/tentacle.py complete api-export          # Mark done + auto-learn
python3 ~/.copilot/tools/tentacle.py complete api-export --no-learn  # Mark done, skip learn
```

Run `complete` only after reviewing sub-agent results and resolving any conflicts. The
orchestrator then commits and pushes — sub-agents must never commit or push.

---

## Tentacle Next Step

`tentacle.py next-step` shows the grounded next step for a named tentacle — the first pending
todo plus optional checkpoint and briefing context. **Read-only**: does not mutate tentacle state.

```bash
python3 ~/.copilot/tools/tentacle.py next-step api-export              # First pending todo + checkpoint context
python3 ~/.copilot/tools/tentacle.py next-step api-export --all        # All pending todos (not just the first)
python3 ~/.copilot/tools/tentacle.py next-step api-export --briefing   # + live knowledge briefing from briefing.py
python3 ~/.copilot/tools/tentacle.py next-step api-export --no-checkpoint  # Omit checkpoint context
python3 ~/.copilot/tools/tentacle.py next-step api-export --format json    # Machine-readable JSON output
```

JSON output includes `tentacle`, `status`, `todos_done`, `todos_total`, `pending`, `next_step`,
`checkpoint_context`, and `briefing` fields.

### Sub-agent conventions

These apply to every dispatched sub-agent.

- **Commit restriction (enforced + convention)**: Sub-agents must not run `git commit` or
  `git push`. When git hooks are installed (`install.py --install-git-hooks`), both operations
  are **blocked at the git level** by `hooks/check_subagent_marker.py` whenever the
  `dispatched-subagent-active` marker is present, fresh, and its `git_root` matches the repo
  running the git command. Even without git hooks, this remains a firm convention: only the
  orchestrator commits, after merging and verifying all tentacle results. A sub-agent commit
  mid-run risks corrupting the orchestrator's merge flow.

  > **Cross-repo isolation (phase 4+):** A marker written in repo A does not block `git commit`
  > in repo B. Each marker entry carries a `git_root` field; the hook skips entries from
  > different repos. The same tentacle name can be active in multiple repos at once — each
  > produces a separate marker entry. Dedup key: `tentacle_id` (primary, for phase-5 entries)
  > → `(name, git_root)` fallback (for phase-4 / legacy entries without `tentacle_id`).
  > Entries without `git_root` (old format) conservatively block all repos.
  >
  > **Upgrade migration:** Cross-repo isolation is not retroactive. In-flight old-format markers
  > have no `git_root` and continue to block all repos until completed, cleared, or expired.
  > To get isolation immediately after upgrading: `tentacle.py complete <name>`, then re-dispatch.

  > **Local-only enforcement**: the git hook guard fires only on local machines where hooks are
  > installed. Cloud-delegated or remote agent runs are not covered.

  > **Same-repo multi-session (phase 5 — supported at runtime, with caveats):**
  > `tentacle.py create` now generates a `tentacle_id` UUID per instance. If the requested
  > directory already exists, `create` auto-resolves the collision by creating
  > `<name>-<8-char-uuid>` and printing the slug. Two sessions in the same repo can each hold
  > separate, non-colliding marker entries; `complete` removes only the matching identity.
  >
  > **Working-tree caveat:** Runtime identity isolation does not create separate working trees
  > or index snapshots. Two tentacles in the same repo with overlapping file scopes will still
  > produce merge conflicts or overwritten files in the shared working directory. Keep tentacle
  > scopes non-overlapping when running same-repo concurrent sessions.
  >
  > **Slug name caveat:** When a collision-resolved slug (`<name>-<uuid[:8]>`) is created, all
  > subsequent commands (`todo`, `swarm`, `complete`, `handoff`) must use the slug, not the
  > original logical name. The slug is printed by `create` and stored as `dir_name` in
  > `meta.json`.

- **Stay in scope**: Avoid editing files outside your tentacle's declared scope.
- **Escalate, don't expand**: If scope is insufficient, record the gap in `handoff.md` and stop.
  Do not expand scope or commit partial work unilaterally.
- **No over-implementation**: Implement only what your todos specify.
- **Write handoff.md before stopping**: Even if your session ends early, always leave a summary so
  the orchestrator can resume or reassign.

## Tentacle Bundle

`tentacle.py bundle` materializes a per-run context bundle for a tentacle subagent — a local
`bundle/` directory containing `briefing.md`, `instructions.md`, `skills.md`,
`session-metadata.md`, `recall-pack.json` (machine-readable JSON recall), and a `manifest.json`.
`swarm` and `dispatch` create this bundle by default; this command is useful when you want to
inspect or pre-warm all context artifacts before execution.

```bash
python3 ~/.copilot/tools/tentacle.py bundle api-export              # Materialize bundle (fetches briefing + recall pack)
python3 ~/.copilot/tools/tentacle.py bundle api-export --no-briefing   # Skip live prose briefing fetch (recall pack still fetched)
python3 ~/.copilot/tools/tentacle.py bundle api-export --no-checkpoint # Skip checkpoint context
python3 ~/.copilot/tools/tentacle.py bundle api-export --output json   # JSON output (manifest + bundle_path)
```

The bundle is written under `.octogent/tentacles/<name>/bundle/`. Existing files are
overwritten on each run. JSON output returns `manifest` and `bundle_path` fields.

`recall-pack.json` contains the raw JSON payload from `briefing.py --task --json` (preferred) or
`briefing.py --pack` (fallback), wrapped in an envelope: `{tentacle, created_at, source_mode, ...data}`.
The manifest exposes `artifacts.recall_pack` with `file`, `populated`, and `source_mode` fields so
tools and sub-agents can inspect recall provenance without parsing the pack payload.

## Project Context

`project-context.py` generates a deterministic `project-context.md` artifact from repo and
profile facts — no AI generation, no network access. The output derives from git-tracked files,
the active preset profile, deployed hooks metadata, and test file discovery.

```bash
python3 ~/.copilot/tools/project-context.py                  # Write to session files/ dir
python3 ~/.copilot/tools/project-context.py --stdout         # Print to stdout only
python3 ~/.copilot/tools/project-context.py --output PATH    # Write to an explicit file path
python3 ~/.copilot/tools/project-context.py --repo PATH      # Use a different repo root
python3 ~/.copilot/tools/project-context.py --profile python # Force a specific preset profile
python3 ~/.copilot/tools/project-context.py --no-write       # Dry-run: show target path without writing
python3 ~/.copilot/tools/project-context.py --list-profiles  # Show available preset profiles
```

The output is **deterministic**: same repo state → same output. The last-commit date (not wall-clock
time) is used as the timestamp, so re-running without new commits produces an identical artifact.

## Trend Scout

`trend-scout.py` queries the **GitHub Search API** (keyword + topic searches) using a **multi-lane discovery** architecture to find relevant repos, scores and deduplicates candidates across lanes, then creates or updates structured issues in the target repo. There is no official GitHub Trending API; results are ranked by keyword match, topic overlap, star count, and recency. Each lane is an independent search channel — lanes run in parallel and their candidate sets are merged, deduplicated, and re-scored with cross-lane term-set signals before shortlisting.

### Basic usage

```bash
# Full pipeline — search, shortlist, enrich, create issues
python3 ~/.copilot/tools/trend-scout.py

# Preview without writing anything
python3 ~/.copilot/tools/trend-scout.py --dry-run

# Discovery + shortlist only; skip issue creation
python3 ~/.copilot/tools/trend-scout.py --search-only

# Emit a discovery explainability artifact (JSON) documenting lane results and scoring
python3 ~/.copilot/tools/trend-scout.py --explain

# Cap the number of issues created this run
python3 ~/.copilot/tools/trend-scout.py --limit 3

# Override the target repo
python3 ~/.copilot/tools/trend-scout.py --repo owner/repo

# Use a custom config file
python3 ~/.copilot/tools/trend-scout.py --config /path/to/config.json

# Explicit GitHub token (overrides GITHUB_TOKEN env var)
python3 ~/.copilot/tools/trend-scout.py --token TOKEN

# Bypass grace window and force a new run regardless of last-run state
python3 ~/.copilot/tools/trend-scout.py --force
```

Set `GITHUB_TOKEN` in the environment, or pass `--token TOKEN`, to avoid API rate limits.
`--limit` caps **new issue creates** only; marker-matched updates are still evaluated.
`--explain` writes a JSON artifact listing which lanes fired, candidate scores, and which
cross-lane term-set overlaps influenced final scoring. Combine with `--search-only` for a
read-only discovery audit. When `trend-scout-goldset.json` is present, the same artifact also
evaluates each curated watchlist repo as `missing`, `raw`, or `shortlisted`. In GitHub Actions
manual runs, `explain=true` uploads this file as a workflow artifact.

### Multi-lane discovery

The pipeline supports parallel discovery lanes configured in the `lanes[]` array of
`trend-scout-config.json`. Each lane can specify:

| Lane field | Effect |
|---|---|
| `name` | Lane identifier; tagged on each candidate as `_discovery_lane` |
| `keywords` | GitHub Search queries for this lane (plain phrases or qualifier-rich expressions such as exact phrases / `topic:` filters) |
| `topics` | Topic filters to search in parallel with keywords |
| `language` | Language filter (`null` = language-agnostic) |
| `min_stars` | Minimum star count for this lane (can differ from primary) |
| `max_per_query` | Result cap per individual search query |
| `lookback_days` | Repo-age window for this lane |

The primary `search.*` section still defines the default lane. `lanes[]` entries are additional
channels that run independently and merge into the same candidate pool. After merge, cross-lane
term-set scoring adjusts composite scores for repos that appear in multiple lanes.

The browse UI Settings page (`/settings`) surfaces lane metadata from `/api/scout/status`
as `discovery_lanes[]`, showing each lane name, keyword/topic count, language, and `min_stars`
in a read-only diagnostics card.

### Gold-set / watchlist replay

Trend Scout also supports a repo-local strategic watchlist in `trend-scout-goldset.json`.
This file is not another discovery lane; it is a regression benchmark used by `--explain`.
Each entry can define:

| Gold-set field | Effect |
|---|---|
| `repo` | Exact `owner/name` to track |
| `required` | Whether the repo counts toward missing-required coverage **and gets priority in the shortlist within the cap** |
| `expected_lane` | Optional expected discovery lane (for example `adjacent-ai-dev`) |
| `min_score` | Optional minimum acceptable shortlist score |
| `category` | Optional grouping label for operator review |
| `notes` | Human-readable reason the repo matters |

When `python3 ~/.copilot/tools/trend-scout.py --search-only --explain` runs, the explain JSON
adds a `goldset` block summarizing:

- total watchlist entries
- how many were found in raw discovery
- how many survived shortlist
- which required repos are still missing
- lane mismatches / score failures for tracked repos

This is the durable guardrail for repos like `1jehuang/jcode`: even if no issue is created, the
operator can still tell whether Trend Scout recall regressed.

#### Required-repo shortlist retention

Repos with `"required": true` are **prioritized** into the shortlist whenever they appear in raw
discovery and meet their per-entry `min_score` (or the global `shortlist.min_score` if not set).
They preempt non-required repos within `shortlist.max_candidates`, so high-scoring non-required
repos can never crowd them out. The terminal output prints a `📌 Retaining N required gold-set
repo(s)` line listing which repos were retained — this is the operator-visible confirmation that
retention fired.

If required repos themselves ever outnumber `shortlist.max_candidates`, Trend Scout keeps the
highest-scoring required repos, emits a warning, and leaves the cap intact. Raise
`shortlist.max_candidates` if you want to retain all required repos simultaneously.

Non-required gold-set entries (or any repo absent from `trend-scout-goldset.json`) follow the
standard top-N scoring logic unchanged.

### Operator-safe automation flow

Use this sequence to keep automation practical and low-noise:

```bash
# 1) Discovery sanity check (no issue writes)
python3 ~/.copilot/tools/trend-scout.py --search-only

# 2) Explainability audit (shows lane contributions + scoring)
python3 ~/.copilot/tools/trend-scout.py --search-only --explain

# 3) Render verification (body previews only)
python3 ~/.copilot/tools/trend-scout.py --dry-run --limit 1 --force

# 4) Controlled live write
python3 ~/.copilot/tools/trend-scout.py --limit 1 --force
```

After validation, let `.github/workflows/trend-scout.yml` handle daily scheduling.

### Optional GitHub Models analysis

Trend Scout can replace the static learning-bullet heuristics with GitHub Models inference:

```json
{
  "analysis": {
    "enabled": true,
    "model": "openai/gpt-4o-mini",
    "endpoint": "https://models.github.ai/inference/chat/completions",
    "token_env": "GITHUB_MODELS_TOKEN"
  }
}
```

- `analysis.model` must use the GitHub Models `publisher/model` format.
- `analysis.token_env` is explicit on purpose: locally, export `GITHUB_MODELS_TOKEN`; in GitHub Actions, either set `token_env` to `GITHUB_TOKEN` or map `GITHUB_MODELS_TOKEN` from `secrets.GITHUB_TOKEN`.
- If the token is missing, the model ID is invalid, or the response is malformed, Trend Scout logs the reason and falls back to the heuristic `_derive_learnings()` path.

### Veto gate

Before creating a **new** issue, the pipeline evaluates the candidate against the configured veto gate.
Set `veto.require_domain_signals=1` in `trend-scout-config.json` to skip any candidate whose heuristic
learning engine returns only the generic fallback bullet (no domain-specific signals matched). The
script default is `0` (disabled). In this repository, the bundled `trend-scout-config.json` currently
sets `1`.

Set `veto.min_distinct_learnings` to require a minimum count of **distinct novel insight families**
after already-implemented bullets are filtered out. Script default is `0` (disabled). In this
repository, the bundled config currently sets `2`, which means a candidate with only one genuinely
new idea is vetoed instead of creating a new issue. Veto decisions are printed to stdout as
`⊘ Veto (<reason>): owner/repo`.

> **Note:** the veto gate applies to **new creates only** — existing **open** issues can still update when
> marker-matched and changed. Marker-matched **closed** issues suppress writes.

### Grace window and run state

`run_control.grace_window_hours` prevents back-to-back runs from firing within the configured window.
After each successful full run (non-dry-run, non-search-only), the last-run timestamp is written to
`.trend-scout-state.json` adjacent to the script (or to the path set in `run_control.state_file`). On
the next run, if the elapsed time since `last_run_utc` is less than `grace_window_hours`, the run
exits 0 immediately and prints the remaining window. Use `--force` (or the `force` workflow input) to
bypass the grace window unconditionally.

The script default value is `0` (disabled). In this repository, the bundled config currently sets `20`.
In GitHub Actions, `.trend-scout-state.json` is preserved between runner instances via `actions/cache`
so the grace window works across daily scheduled runs.

> `.trend-scout-state.json` is a local runtime artifact — it is listed in `.gitignore` and should not
> be committed.

### Deduplication

Before create/update decisions, the script scans **all open and closed** `trend-scout`-labelled
issues for a hidden deterministic marker. The marker is a 16-character truncated SHA-256
hash of the lowercased `owner/name` embedded as an HTML comment:
`<!-- trend-scout:repo:<16-char-hex> -->`.

- **Marker missing** → create a new issue.
- **Marker present + existing issue is open + rendered body changed** → update the existing issue in place.
- **Marker present + existing issue is closed** → suppress (skip write/update).
- **Marker present + rendered body unchanged** → skip.

### Config tuning (`trend-scout-config.json`)

| Key | Effect |
|-----|--------|
| `search.seed_keywords` | Free-text queries for the primary lane |
| `search.extra_topics` | Additional topic filters for the primary lane |
| `search.min_stars` | Minimum star count for the primary lane |
| `lanes[]` | Array of additional discovery lanes (each with `name`, `keywords`, `topics`, `language`, `min_stars`, `max_per_query`, `lookback_days`) |
| `shortlist.max_candidates` | How many repos advance to enrichment |
| `shortlist.min_score` | Minimum composite score threshold (unbounded sum; default `0.15`) |
| `shortlist.scoring.*_weight` | Adjust keyword, topic, star, and recency weights |
| `enrichment.readme_max_chars` | Characters of README to fetch and pass to the heuristic engine (default `3000`); increase for feature-dense READMEs, decrease to reduce issue size |
| `dedup.search_closed_issues` | Whether to scan closed issues for markers |
| `dedup.max_issues_scan` | Max issues scanned per dedup pass (default 300); increase on busy repos to avoid missing old markers |
| `search.lookback_days` | Repo age window for the primary lane (default 730 days); lower to focus on recently active repos |
| `analysis.enabled` | Enables GitHub Models per-repo learning analysis before issue rendering |
| `analysis.model` | GitHub Models model ID in `publisher/model` format (default `openai/gpt-4o-mini`) |
| `analysis.token_env` | Environment variable that holds the models-capable token (default `GITHUB_MODELS_TOKEN`) |
| `analysis.max_learnings` | Caps LLM-generated bullets per repo before rendering |
| `analysis.temperature`, `analysis.max_tokens`, `analysis.timeout` | Controls inference determinism, output size, and request timeout |
| `veto.require_domain_signals` | Script default: `0` (disabled). Set `1` to skip candidates whose heuristic engine produces only the generic fallback bullet (no domain-specific signals). Applies to new creates only; existing open issues are still eligible for update (marker-matched closed issues suppress writes). |
| `veto.min_distinct_learnings` | Script default: `0` (disabled). Requires at least _N_ distinct novel insight families after already-implemented bullets are removed; values like `2` suppress create-stage issues when only one genuinely new insight remains. Applies to new creates only. |
| `run_control.grace_window_hours` | Script default: `0` (disabled). Hours to wait between full runs. Grace window state is persisted to `.trend-scout-state.json`. Use `--force` to bypass. |
| `run_control.state_file` | Path to the run-state JSON file. `null` or absent resolves to `.trend-scout-state.json` adjacent to the script. |

### Limitations

- Uses GitHub Search API heuristics — not an official trending list.
- Freshness depends on GitHub's search index; very new repos may not appear immediately.
- The primary lane defaults to `language: python`; other lanes can be language-agnostic.

### GitHub Actions workflow

The workflow (`.github/workflows/trend-scout.yml`) runs **daily at 07:00 UTC**. It requires
no secrets beyond the automatic `GITHUB_TOKEN` and uses minimal permissions:
`contents: read`, `issues: write`, `models: read`.

The workflow exports both `GITHUB_TOKEN` and `GITHUB_MODELS_TOKEN` from `secrets.GITHUB_TOKEN`, so the optional `analysis.enabled` path works without extra secrets when running in GitHub Actions.

**Manual dispatch inputs:**

| Input | Type | Description |
|-------|------|-------------|
| `dry_run` | boolean | Preview without creating issues |
| `search_only` | boolean | Discovery + shortlist only |
| `explain` | boolean | Emit and upload the discovery explainability artifact |
| `repo` | string | Override target repo (`OWNER/REPO`) |
| `limit` | string | Max issues to create this run |
| `force` | boolean | Bypass grace window and force a full run |

### Hook behavior (anti-spam)

Trend Scout is intentionally **not** registered in Copilot runtime hooks (`hooks/hooks.json`).
Keep scouting in explicit CLI runs or scheduled workflow automation; avoid per-tool hook triggers
that would spam session output.


## Maintenance

```bash
python3 ~/.copilot/tools/build-session-index.py --incremental   # Update changed files + auto-embed
python3 ~/.copilot/tools/build-session-index.py --no-embed      # Index only, skip embeddings
python3 ~/.copilot/tools/extract-knowledge.py --stats           # View knowledge statistics
python3 ~/.copilot/tools/extract-knowledge.py --relations       # View relation statistics
# Relation extraction runs newest-first: recent entries always get graph connections
# before older context, with a per-session cap to spread budget across sessions.
python3 ~/.copilot/tools/watch-sessions.py --install-hint      # Show auto-start setup instructions
python3 ~/.copilot/tools/embed.py --status                      # Embedding coverage stats
python3 ~/.copilot/tools/embed.py --build                       # Rebuild all embeddings
python3 ~/.copilot/tools/install.py --deploy-skill              # Deploy SKILL.md
python3 ~/.copilot/tools/install.py --deploy-hooks              # Deploy Copilot CLI hooks
python3 ~/.copilot/tools/install.py --install-git-hooks         # Install pre-commit/pre-push git hooks (per repo)
python3 ~/.copilot/tools/install.py --deploy-instructions       # Deploy global instructions
python3 ~/.copilot/tools/install.py --inject-global             # Inject into global copilot-instructions
```

## Auto-Start (Background Watcher)

### macOS — LaunchAgents (recommended)

```bash
python3 ~/.copilot/tools/launchd/install-launchd.py           # Install both agents
python3 ~/.copilot/tools/launchd/install-launchd.py --remove   # Uninstall

# Installs two LaunchAgents:
#   com.copilot.watch-sessions  — foreground watcher managed by launchd (auto-indexes + auto-embeds)
#   com.copilot.auto-update     — daily 9 AM, git pulls tool updates + migrates DB
```

### Windows — Task Scheduler

```powershell
$action = New-ScheduledTaskAction `
    -Execute "python" `
    -Argument "$env:USERPROFILE\.copilot\tools\watch-sessions.py --daemon" `
    -WorkingDirectory "$env:USERPROFILE\.copilot"

$trigger = New-ScheduledTaskTrigger -AtLogOn
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)

Register-ScheduledTask -TaskName "CopilotWatchSessions" `
    -Action $action -Trigger $trigger -Settings $settings `
    -Description "Auto-index Copilot session knowledge"
```

### Linux — systemd user service

```bash
mkdir -p ~/.config/systemd/user
cat > ~/.config/systemd/user/copilot-watch.service << 'SVC'
[Unit]
Description=Copilot Session Knowledge Watcher

[Service]
ExecStart=/usr/bin/python3 %h/.copilot/tools/watch-sessions.py
WorkingDirectory=%h/.copilot
Restart=on-failure
RestartSec=30

[Install]
WantedBy=default.target
SVC

systemctl --user enable --now copilot-watch.service
```

### macOS — launchd user agent

```bash
mkdir -p ~/Library/LaunchAgents
cat > ~/Library/LaunchAgents/dev.linhngo.sk-watcher.plist << 'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>dev.linhngo.sk-watcher</string>
  <key>ProgramArguments</key>
  <array>
    <string>sk</string>
    <string>watch</string>
  </array>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
</dict>
</plist>
PLIST

launchctl unload ~/Library/LaunchAgents/dev.linhngo.sk-watcher.plist 2>/dev/null || true
launchctl load ~/Library/LaunchAgents/dev.linhngo.sk-watcher.plist
```

### Watch-sessions runtime semantics

**Lock file** — `~/.copilot/session-state/.watcher.lock` ensures only one watcher instance runs at a time.
- On startup, the watcher atomically creates this file and writes its PID.
- On shutdown (normal or Ctrl+C), the lock is removed via an `atexit` handler.
- If the watcher crashes (power loss, SIGKILL), a stale lock may remain. The next startup detects the stale PID, removes the lock automatically, and proceeds.
- If a **live** lock is found (another watcher is running), the new instance exits with code 1 and a message like `Error: another watcher is already running (PID <n>)`.

**To recover from a stale lock manually** (only if automatic cleanup fails):
```bash
rm ~/.copilot/session-state/.watcher.lock
python3 ~/.copilot/tools/auto-update-tools.py --watch-status   # Verify no watcher running
python3 ~/.copilot/tools/auto-update-tools.py --restart-watch  # Restart via service manager
```

**Watch state** — `~/.copilot/session-state/.watch-state.json` stores file signatures (mtime + size + content hash) and a `last_index` timestamp. This file is updated atomically after every poll cycle and drives the content-hash change detection (so touch/autosave with no edits do not trigger re-indexing).

**Adaptive polling** — the watcher adjusts its poll interval automatically:

| Most recent session file | Interval |
|---|---|
| Modified within 2 minutes | 5 seconds (active tier) |
| Modified within 1 hour | 30 seconds (recent tier) |
| Older than 1 hour | 5 minutes (idle tier) |

Pass `--interval <seconds>` to override the adaptive tier with a fixed interval.

## Aliases

Add to your `~/.zshrc` or `~/.bashrc`:

```bash
alias qs='python3 ~/.copilot/tools/query-session.py'
alias brief='python3 ~/.copilot/tools/briefing.py'
alias learn='python3 ~/.copilot/tools/learn.py'
# Usage: qs "docker error" | brief "fix login" | learn --pattern "Title" "Desc"
```
