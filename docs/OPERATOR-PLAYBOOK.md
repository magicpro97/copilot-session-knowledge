# Operator Playbook

> Day-to-day health monitoring, maintenance, and troubleshooting for copilot-session-knowledge operators.

## Health Checks

### Knowledge base health

```bash
python3 ~/.copilot/tools/index-status.py              # Row counts, FTS integrity, event-offset coverage
python3 ~/.copilot/tools/knowledge-health.py           # Full health dashboard
python3 ~/.copilot/tools/knowledge-health.py --recall  # Recall-only telemetry
python3 ~/.copilot/tools/knowledge-health.py --recall --json  # Machine-readable recall stats
```

### Sync health

```bash
python3 ~/.copilot/tools/sync-status.py               # Local sync state summary
python3 ~/.copilot/tools/sync-status.py --health-check --json  # Exit 0/2 health check
python3 ~/.copilot/tools/sync-status.py --audit --json         # Detailed audit
python3 ~/.copilot/tools/sync-status.py --watch-status --json  # File-watcher status
```

### Runtime health

```bash
python3 ~/.copilot/tools/auto-update-tools.py --doctor        # Auto-update pipeline health
python3 ~/.copilot/tools/auto-update-tools.py --watch-status  # Watcher daemon status
python3 ~/.copilot/tools/auto-update-tools.py --health-check  # Exit-code health check
python3 ~/.copilot/tools/auto-update-tools.py --audit-runtime # Runtime audit
```

---

## Auto-Update

```bash
python3 ~/.copilot/tools/auto-update-tools.py          # Auto-update (24h cooldown)
python3 ~/.copilot/tools/auto-update-tools.py --force  # Force update now
python3 ~/.copilot/tools/auto-update-tools.py --restart-watch  # Restart watcher daemon
```

The smart pipeline analyzes `git diff` to run only what changed. The post-merge hook auto-triggers on `git pull`.

> **After major updates:** re-run `python3 ~/.copilot/tools/install.py --install-git-hooks` in every protected repo to refresh per-repo git hooks.

📖 Full auto-update reference: [docs/AUTO-UPDATE.md](AUTO-UPDATE.md)

---

## Hook Maintenance

```bash
# Deploy / re-deploy hooks
python3 ~/.copilot/tools/install.py --deploy-hooks

# Lock hooks against AI modification (OS immutable flags)
python3 ~/.copilot/tools/install.py --lock-hooks

# Unlock for updates
python3 ~/.copilot/tools/install.py --unlock-hooks

# Install per-repo git-level subagent guard
python3 ~/.copilot/tools/install.py --install-git-hooks
```

### Dry-run mode

Test hook behavior without blocking:

```bash
HOOK_DRY_RUN=1 python3 ~/.copilot/hooks/hook_runner.py preToolUse
```

### Audit log

Every hook decision is logged:

```bash
tail -f ~/.copilot/markers/audit.jsonl
```

---

## DB Migrations

```bash
python3 ~/.copilot/tools/migrate.py     # Apply all pending migrations
```

Migrations are versioned in `migrate.py`'s `MIGRATIONS` list. Running `migrate.py` is idempotent — it only applies migrations not already applied.

Current schema: **v15** (v8 introduced `sessions_fts` contentless FTS5 + BM25; v15 `confidence_backfill_wave3` raised pattern confidence floors and applied recurrence rewards to existing entries).

---

## Watcher Management

```bash
# Start watcher manually
python3 ~/.copilot/tools/watch-sessions.py

# macOS: via LaunchAgent (auto-start on login)
launchctl load ~/Library/LaunchAgents/com.copilot.watch-sessions.plist

# Restart via auto-update operator surface
python3 ~/.copilot/tools/auto-update-tools.py --restart-watch
```

The watcher uses adaptive polling: 5 s / 30 s / 300 s tiers based on session activity.

---

## Troubleshooting

### Copilot CLI auto-heal

If `copilot update` fails with `ENOENT` or `EPERM` on a rename inside `pkg/universal/`:

```bash
python3 ~/.copilot/tools/copilot-cli-healer.py --status   # Diagnose
python3 ~/.copilot/tools/copilot-cli-healer.py --heal     # Fix
python3 ~/.copilot/tools/copilot-cli-healer.py --update   # Heal + retry copilot update
```

Root cause: upstream Node updater calls `fs.rename(src, dst)` without checking that `src` exists, leaving stale `.replaced-*` dirs behind.

Prevent recurrence by scheduling a daily heal:

```bash
python3 ~/.copilot/tools/copilot-cli-healer.py --install-schedule
# or:
python3 ~/.copilot/tools/install.py --install-healer
```

📖 Details: [docs/copilot-cli-healer.md](copilot-cli-healer.md)

### Stuck dispatched-subagent marker

If `git commit` is blocked by a stale `dispatched-subagent-active` marker:

```bash
# Preferred: complete the tentacle cleanly
python3 ~/.copilot/tools/tentacle.py complete <name>

# Emergency: remove marker directly
rm ~/.copilot/markers/dispatched-subagent-active
```

Markers expire automatically after 4 hours (TTL dead-man switch). After clearing, re-dispatch any tentacles still in flight.

### FTS integrity errors

```bash
python3 ~/.copilot/tools/index-status.py   # Check FTS integrity
python3 ~/.copilot/tools/build-session-index.py  # Rebuild index
```

### Sync not working

```bash
python3 ~/.copilot/tools/sync-config.py --status --json  # Check config
python3 ~/.copilot/tools/sync-status.py --health-check   # Health check
python3 ~/.copilot/tools/sync-daemon.py --once           # Manual one-shot sync
```

Common issues:
- `connection_string` not set → daemon is local-only/idle (not an error)
- Gateway URL must be HTTP/HTTPS — not a raw Postgres/libSQL DSN
- `sync-gateway.py` is reference/mock only — use a real gateway in production

### Knowledge base not growing

```bash
# Trigger a manual re-index
python3 ~/.copilot/tools/build-session-index.py
python3 ~/.copilot/tools/extract-knowledge.py
```

If running in watch mode, check watcher status:

```bash
python3 ~/.copilot/tools/auto-update-tools.py --watch-status
```

---

## Reading Research and Operator Outputs

All agent-authored outputs — tentacle handoffs, retro summaries, research-pack
summaries, knowledge-health reports — use the four-layer QA format defined in
[docs/AGENT-RULES.md](AGENT-RULES.md#rule-7--docs-output-quality).

When reviewing any such output, apply this checklist:

| ✔ | Check |
|---|-------|
| □ | Are counts/timestamps **facts** backed by a cited source or command? |
| □ | Are inferences clearly **qualified** ("suggests", "indicates") rather than stated as fact? |
| □ | Does every action item include a **concrete, executable command**? |
| □ | Is every verification claim backed by **evidence** (test log, CI link, git ref)? |

**What to do when evidence is missing:**

- For test claims: re-run the named test and paste the pass/fail count.
- For CI claims: link the workflow run URL.
- For retro scores: note `score_confidence` — a `low` confidence score is a signal, not a verdict.

**Research-pack outputs** follow the same rules.
Each repo entry distinguishes discovery facts (score, stars, language) from interpretation
(why discovered, novelty signals) and recommended follow-ups (actionable tentacle handoffs).
When `.trend-scout-research-pack.json` has been produced, the browse UI insights dashboard
and `GET /api/scout/research-pack` expose a compact read-only summary automatically.

---

## Quality Gates

| Gate | What it checks | How to run |
|------|---------------|-----------|
| `scripts/check_syntax.py` | `py_compile` every `.py` file | `python3 ~/.copilot/tools/scripts/check_syntax.py` |
| `run_all_tests.py` | Discovers and runs all `test_*.py` files | `python3 ~/.copilot/tools/run_all_tests.py` |
| `hooks/rules/syntax_gate.py` | Blocks bad `.py` edits at hook level | Automatic via `hook_runner.py preToolUse` |
| CI (`ci.yml`) | Syntax check + all tests on push/PR | GitHub Actions (ubuntu-latest, Python 3.11) |

Pre-commit checklist:

```bash
python3 ~/.copilot/tools/scripts/check_syntax.py
python3 ~/.copilot/tools/test_security.py
python3 ~/.copilot/tools/test_fixes.py
git diff --stat
```

---

## Checkpoint Lifecycle

```bash
# Save a checkpoint
python3 ~/.copilot/tools/checkpoint-save.py --title "Auth done" --overview "JWT added"

# List checkpoints
python3 ~/.copilot/tools/checkpoint-restore.py --list

# Restore / inspect
python3 ~/.copilot/tools/checkpoint-restore.py --show latest
python3 ~/.copilot/tools/checkpoint-restore.py --export latest --format json

# Diff checkpoints
python3 ~/.copilot/tools/checkpoint-diff.py --from 1 --to latest
python3 ~/.copilot/tools/checkpoint-diff.py --summary
```

Hooks **never** auto-save checkpoints. Save them manually at meaningful milestones.

---

## Tentacle Operator View

```bash
# Dashboard: all tentacles and states
python3 ~/.copilot/tools/tentacle.py status

# Next step for a specific tentacle
python3 ~/.copilot/tools/tentacle.py next-step <name>
python3 ~/.copilot/tools/tentacle.py next-step <name> --all     # All pending todos
python3 ~/.copilot/tools/tentacle.py next-step <name> --format json

# Verify and close
python3 ~/.copilot/tools/tentacle.py verify <name> "python3 test_fixes.py" --label "tests"
python3 ~/.copilot/tools/tentacle.py handoff <name> "Summary" --learn
python3 ~/.copilot/tools/tentacle.py complete <name>
# Or: combine verify + complete in one step (fail-open)
python3 ~/.copilot/tools/tentacle.py complete <name> --auto-verify "python3 test_fixes.py"
python3 ~/.copilot/tools/tentacle.py complete <name> --auto-verify "python3 test_fixes.py" --auto-verify-timeout 180
```

> Full tentacle workflow: **[docs/USAGE.md](USAGE.md#tentacle-orchestration)**

---

## Browse UI — Operator Diagnostics Settings Page

The `/v2/settings/` page in the Browse UI is the primary **browser-based operator surface**.
All diagnostic panels are read-only — no write operations are exposed.

| Card | API endpoint | Shows |
|------|-------------|-------|
| Sync diagnostics | `/api/sync/status` | Mode, pending txns, failed ops, gateway config, rollout guidance |
| Trend Scout diagnostics | `/api/scout/status` | Config, grace window, audit checks, discovery lanes |
| Trend Scout research pack | `/api/scout/research-pack` | Latest pack summary, repo count, novelty/risk/follow-up snippets |
| Tentacle runtime diagnostics | `/api/tentacles/status` | Active tentacles, dispatch marker, registry, audit checks |
| Skill outcome metrics | `/api/skills/metrics` | Pass rate, outcomes, skill usage summary |
| System health | `/healthz` | DB schema version, session count, knowledge entries, last indexed |

Each card with live data also renders an **Operator checks (read-only)** panel that lists
safe CLI commands the operator can **copy** to their terminal. The browser never executes
commands — the panel is display-only with copy-to-clipboard buttons.

Navigate to the Settings page:
```
http://localhost:<port>/v2/settings/?token=<token>
```

---

## Browse UI — Operator Console (`/v2/chat`)

The `/v2/chat` route is the browser-managed Copilot CLI execution console. It is distinct from the read-only settings and diagnostics surfaces and is the only browse page that actively launches Copilot CLI.

### Workflow

1. Open the browse UI and navigate to **Chat** (sidebar, command palette, or the `g c` navigation chord).
2. Click **New Chat** and choose a workspace under `~/`, plus the Copilot model and mode.
3. Submit a prompt with the composer.
4. Watch the streamed transcript update live.
5. Review touched files from the **Files touched** panel:
   - **Preview** loads the current file content in-browser.
   - **Diff** appears only when the run produced a truthful unified diff payload (for example from `apply_patch`).

### Session persistence

Each operator session persists its run history under:

```text
~/.copilot/session-state/operator-console/<session-id>/
```

Historical runs are replayed from disk on refresh, so the transcript and file-review context survive browser reloads and browse-server restarts.

### Guardrails

- All workspaces and file-review paths are normalized against `~/`; paths outside `Path.home()` are rejected.
- Prompt text is capped at 4096 characters.
- `/api/operator/*` uses the same per-launch browse token as the rest of the UI.
- Runs launched from `/v2/chat` still inherit the installed Copilot CLI's hooks, custom instructions, and permission system. Browser use does not bypass briefing/tentacle/learn or other active policy gates.

### Compatibility

- `watch-sessions.py` continues to process normal Copilot session artifacts; the operator console reads its own persisted history from `operator-console/`.
- `auto-update-tools.py` does not manage active operator runs or restart the browse server.
- After Python changes to `browse/api/operator.py` or `browse/core/operator_console.py`, restart the browse server manually.

Direct link:

```text
http://localhost:<port>/v2/chat/?token=<token>
```

---



## Trend Scout Research Pack

The `--research-pack` flag writes a structured JSON artifact with per-repo analysis fields
that go beyond the GitHub issue body: novelty signals, risk signals, recommended follow-ups,
and a tentacle-handoff hint.

```bash
# Combine with --search-only --dry-run for a safe local preview (no network writes)
python3 ~/.copilot/tools/trend-scout.py --search-only --dry-run --research-pack

# Combine with --explain for full explainability coverage
python3 ~/.copilot/tools/trend-scout.py --search-only --dry-run --research-pack --explain

# Full pipeline run with research pack written after issue creation
python3 ~/.copilot/tools/trend-scout.py --research-pack

# Custom output path
python3 ~/.copilot/tools/trend-scout.py --research-pack --research-pack-output my-pack.json
```

The artifact is written to `.trend-scout-research-pack.json` adjacent to the script.
When the grace window is active and the run is skipped, the pack is still written with
`run_skipped: true` and an empty `repos` list so CI consumers can distinguish intentional
skips from real zero-result runs.

### Research pack schema

```json
{
  "generated_at": "2025-07-10T03:00:00+00:00",
  "source": "trend-scout.py",
  "schema_version": 1,
  "repos": [
    {
      "full_name": "owner/repo",
      "html_url": "https://github.com/owner/repo",
      "discovery_lane": "token-efficiency-cli",
      "discovery_query": "token efficient cli agent",
      "score": 0.42,
      "stars": 123,
      "language": "Python",
      "topics": ["ai-tools"],
      "why_discovered": ["Discovered via lane 'token-efficiency-cli' using query '...'"],
      "novelty_signals": ["Strong community adoption (123 ⭐)", "License: MIT"],
      "risk_signals": ["No significant risk signals from available metadata"],
      "recommended_followups": ["Review README at ...", "Check open issues at ..."],
      "tentacle_handoff": "Spawn a research tentacle for owner/repo to evaluate: ..."
    }
  ]
}
```

When a run is skipped by the grace window:

```json
{
  "generated_at": "...",
  "source": "trend-scout.py",
  "schema_version": 1,
  "run_skipped": true,
  "skip_reason": "last run 2.0h ago, grace window 20h (18.0h remaining)",
  "repos": []
}
```

### Using the pack for follow-up research

1. **Inspect `tentacle_handoff`** — each entry has a brief text you can feed directly to
   `tentacle.py` as a task description to spawn a research spike.
2. **Filter by `novelty_signals` / `risk_signals`** — prioritise repos with low risk and
   high novelty; deprioritise stale or archived repos.
3. **Use `recommended_followups`** — the list includes direct links to the repo README and
   issues, plus suggestions for follow-up searches.

---



## Retrospective

View the composite operator score across knowledge, skills, hooks, and git signals.

```bash
# CLI — full text report
python3 ~/.copilot/tools/retro.py

# Repo-only (safe in CI; no local DB needed)
python3 ~/.copilot/tools/retro.py --mode repo

# JSON payload (stable contract consumed by the browse API)
python3 ~/.copilot/tools/retro.py --json

# Single score line
python3 ~/.copilot/tools/retro.py --score

# One section only: knowledge | skills | hooks | git | behavior (local mode)
python3 ~/.copilot/tools/retro.py --subreport knowledge
```

### Local vs CI (repo-mode) retro

| | Local (`--mode local`) | CI / repo (`--mode repo`) |
|---|---|---|
| Knowledge section | ✅ included (reads `knowledge.db`) | ❌ skipped (no DB in CI) |
| Skills section | ✅ included (reads tentacle outcomes) | ❌ skipped |
| Hooks section | ✅ included (reads hook audit log) | ❌ skipped |
| Git section | ✅ included | ✅ included |
| Typical score | 61.2 / Good (low confidence) | 78.6 / Good (medium confidence) |
| `score_confidence` | `low` — multi-source but unverified | `medium` — git only, no local noise |

**Use repo-mode retro for trend tracking.** Local-mode scores are useful for drilling into
specific sections but may reflect distortions (see below) that inflate or deflate the result.

## Benchmark ledger

Record commit-keyed snapshots so hardening work is tied to measurable deltas.

```bash
# Record the current snapshot into benchmark_snapshots
python3 ~/.copilot/tools/benchmark.py record

# Inspect recent snapshots
python3 ~/.copilot/tools/benchmark.py list --limit 5

# Compare two commits or snapshot IDs
python3 ~/.copilot/tools/benchmark.py compare --commits <older> <newer>
```

`benchmark.py` stores snapshots in `benchmark_snapshots` inside the default knowledge DB unless
you override it with `--db PATH`. `record` captures retro + knowledge-health when available and
degrades cleanly when a signal source is absent. For CI-safe artifact capture, trigger the
manual-only `.github/workflows/benchmark.yml` workflow in `repo` mode.

`compare` output includes `retro_gap` and `health_gap` (100 − score) for each snapshot plus
the improvement delta.  A negative delta (gap shrinking) is the measurable proof that a
hardening wave moved the score in the right direction.

### Score confidence

The `score_confidence` field (`low` / `medium` / `high`) indicates how much to trust the
composite score:

- **`high`** — all sections present, outcomes verified, no distortion flags.
- **`medium`** — reduced section coverage or minor caveats (common in repo-only mode).
- **`low`** — significant distortions present; treat score as a rough signal only.

### Distortion flags

When `distortion_flags` is non-empty, the score has known accuracy issues:

| Flag | Meaning | Action |
|------|---------|--------|
| `hook_deny_dry_noise` | Dry-run/test `deny-dry` entries excluded from `deny_rate` — not real enforcement denials | Ignore elevated deny_rate; re-run without HOOK_DRY_RUN in a live session |
| `skills_unverified` | Skill outcomes exist but verification evidence is missing | Run `tentacle.py verify <name>` to add verification coverage |

> **Note:** parse errors in the retro payload are reported through `accuracy_notes`, not as
> a dedicated distortion flag. They are still penalising in the score.

### Improvement actions

When `improvement_actions` is present, it contains concrete next steps surfaced by the
retro engine (e.g. "Run `tentacle.py verify` on unverified tentacles", "Add hook coverage
for new scripts"). These are read-only suggestions — the operator decides whether to act.

### Toward-100 gap diagnostics

The additive `toward_100` field in the retro JSON payload lists each section where the
score is below 100, sorted by gap (largest first).  Each entry has:

| Field | Meaning |
|-------|---------|
| `section` | Retro section name (e.g. `skills`, `behavior`, `knowledge`, `git`) |
| `score` | Current subscore |
| `gap` | `100 − score` — points remaining |
| `barriers` | Metric-derived strings explaining what pulls the score down |

`toward_100` is **diagnostic only**.  Every barrier value is derived directly from
measured metrics (stale entry counts, verification row counts, commit frequency, etc.).
It does **not** change the score formula, weights, or any existing subscore.

**Behavior section** — `--subreport behavior` (local mode only) surfaces engagement
signals such as command-execution breadth.  Available in the full local report or as a
standalone subreport:

```bash
python3 ~/.copilot/tools/retro.py --subreport behavior
```

**Skills subscore — verification evidence discipline:**

When skill outcomes exist but no `tentacle_verifications` rows are recorded, the skills
subscore uses **30.0 (sub-neutral)** to reflect the unverified state.  The
`skills_unverified` distortion flag is set and `toward_100` lists
`no_verification_evidence` as the barrier.

To raise the skills subscore above sub-neutral: complete tentacles with an explicit
verification step so that `tentacle_verifications` rows are populated:

```bash
python3 ~/.copilot/tools/tentacle.py verify <name> "python3 test_fixes.py" --label "tests"
# Or in one step with complete --auto-verify (Wave 3; fail-open):
python3 ~/.copilot/tools/tentacle.py complete <name> --auto-verify "python3 test_fixes.py"
```

**Recorded baseline (commit `2850fe12153f`):** repo retro `83.3`, local retro `61.5`,
health `66.5`.  Largest measured local gaps: retro skills `30.0`, behavior `37.5`; health
`confidence_quality` `0.2`, `learning_curve` `6.1`, `relation_density` `10.3`.
These are measured facts from a recorded snapshot, not targets.  Use
`benchmark.py compare` to track movement against this baseline.

**Wave 3 post-landing state (pre-commit):** repo retro `83.3` (git-scored; moves only after
commit), local retro `82.3` (knowledge `71.9`, skills `100.0`, behavior `37.2`), health `71.9`.
Wave 3 code changes improved `confidence_quality` and `learning_curve` via the v15 backfill
migration and recurrence reward.  **Remaining gaps still requiring operator action:**

| Gap | Current (live, pre-commit) | How to close |
|-----|--------------------------|-------------|
| `behavior.completion_rate` / `efficiency_ratio` | Low (in `37.2` composite) | Complete more tentacles with verified outcomes; increase session-to-commit cadence |
| `knowledge.embed_pct` | Below target | Run `python3 ~/.copilot/tools/embed.py` to populate embeddings |
| `health.relation_density` | Below target | Extract-knowledge run on larger session corpus grows relations |
| `health.embedding_coverage` | Below target | Same as embed_pct — run `embed.py` after re-indexing |

These are operational gaps, not code defects.  Wave 3 did not introduce fixes for them; they remain open for subsequent operator work.

### Browse UI

The **Retrospective** collapsible panel on the Insights → Dashboard tab fetches
`/api/retro/summary?mode=repo` and renders:

- composite grade + score badge
- `score_confidence` badge (absent on older payloads)
- per-section subscore cards
- summary narrative (if present)
- distortion flags with explanations (if present)
- accuracy notes (if present)
- improvement actions list (if present)
- **Scout coverage panel** — repo, label, grace-window status, and last-run time (absent on older payloads)

All new fields degrade gracefully — missing fields are silently omitted.

### Scout coverage signal

The `scout` top-level field in the retro JSON payload is a **read-only, informational-only**
snapshot of Trend Scout configuration health.  It does **not** affect `retro_score`,
`weights`, or any existing subscore.

```json
{
  "scout": {
    "available":               true,
    "configured":              true,
    "script_exists":           true,
    "config_path":             "~/.copilot/tools/trend-scout-config.json",
    "target_repo":             "owner/repo",
    "issue_label":             "trend-scout",
    "grace_window_hours":      20,
    "state_file":              "~/.copilot/tools/.trend-scout-state.json",
    "state_file_exists":       true,
    "last_run_utc":            "2025-07-10T03:00:00+00:00",
    "elapsed_hours":           8.3,
    "remaining_hours":         11.7,
    "would_skip_without_force": true
  }
}
```

| Field | Meaning |
|-------|---------|
| `available` | `true` if `trend-scout-config.json` was found and readable |
| `configured` | `true` if config file exists on disk |
| `script_exists` | `true` if `trend-scout.py` script is present |
| `grace_window_hours` | grace period from config (`0` = disabled) |
| `state_file_exists` | `true` if state file (`.trend-scout-state.json`) exists |
| `last_run_utc` | ISO-8601 timestamp of last successful run, or `null` |
| `elapsed_hours` | hours since last run, or `null` |
| `remaining_hours` | hours until grace window expires (capped at 0), or `null` |
| `would_skip_without_force` | `true` if a run now would be skipped by the grace window |

When `scout` is absent (older retro payloads), all surfaces degrade gracefully.

Standalone retro HTML page: `http://localhost:<port>/retro?token=<token>` renders
the same payload in a lightweight page suitable for quick browser-based checks.
The page fetches `/api/retro/summary?mode=repo` and renders grade, confidence,
subscores, distortions, actions, the scout coverage section, and a link to the full JSON payload.

GitHub Actions: trigger **Retrospective** (`retro.yml`) via `workflow_dispatch` to run
`retro.py --mode repo --json`, produce a markdown summary artifact with confidence,
distortion explanations, accuracy notes, and improvement actions, then write to the
job summary. Read-only — no issues, commits, or DB writes.
