# Operator Playbook

> Day-to-day health monitoring, maintenance, and troubleshooting for copilot-session-knowledge operators.

## Health Checks

### Knowledge base health

```bash
sk index status                        # Row counts, FTS integrity, event-offset coverage
sk index health                        # Full health dashboard
sk index health --recall               # Recall-only telemetry
sk index health --recall --json        # Machine-readable recall stats
# fallback: python3 ~/.copilot/tools/index-status.py
# fallback: python3 ~/.copilot/tools/knowledge-health.py [--recall] [--json]
```

### Sync health

```bash
sk sync status                                  # Local sync state summary
sk sync status --health-check --json            # Exit 0/2 health check
sk sync status --audit --json                   # Detailed audit
sk sync status --watch-status --json            # File-watcher status
# fallback: python3 ~/.copilot/tools/sync-status.py [--health-check|--audit|--watch-status] [--json]
```

### Runtime health

```bash
sk update --doctor                     # Auto-update pipeline health
sk update --watch-status               # Watcher daemon status
sk update --health-check               # Exit-code health check
sk update --audit-runtime              # Runtime audit
# fallback: python3 ~/.copilot/tools/auto-update-tools.py [--doctor|--watch-status|...]
```

---

## Auto-Update

```bash
sk update                  # Auto-update (24h cooldown)
sk update --force          # Force update now
sk update --restart-watch  # Restart watcher daemon
# fallback: python3 ~/.copilot/tools/auto-update-tools.py [--force|--restart-watch]
```

The smart pipeline analyzes `git diff` to run only what changed. The post-merge hook auto-triggers on `git pull`.

> **After major updates:** re-run `sk install --install-git-hooks` in every protected repo to refresh per-repo git hooks.

📖 Full auto-update reference: [docs/AUTO-UPDATE.md](AUTO-UPDATE.md)

---

## Hook Maintenance

```bash
# Deploy / re-deploy hooks
sk install --deploy-hooks

# Lock hooks against AI modification (OS immutable flags)
sk install --lock-hooks

# Unlock for updates
sk install --unlock-hooks

# Install per-repo git-level subagent guard
sk install --install-git-hooks
# fallback: python3 ~/.copilot/tools/install.py [--deploy-hooks|--lock-hooks|--unlock-hooks|--install-git-hooks]
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
sk index migrate     # Apply all pending migrations
# fallback: python3 ~/.copilot/tools/migrate.py
```

Migrations are versioned in `migrate.py`'s `MIGRATIONS` list. Running `migrate.py` is idempotent — it only applies migrations not already applied.

Current schema: **v15** (v8 introduced `sessions_fts` contentless FTS5 + BM25; v15 `confidence_backfill_wave3` raised pattern confidence floors and applied recurrence rewards to existing entries).

---

## DB Maintenance (VACUUM and WAL Checkpoint)

Two optional cron templates keep `knowledge.db` compact and WAL files small:

| Template | Default schedule | What it does |
|---|---|---|
| `wal-checkpoint` | Daily 04:00 | `PRAGMA wal_checkpoint(TRUNCATE)` — flushes WAL frames back to the main DB file and truncates the WAL |
| `vacuum` | Weekly Sunday 04:30 | `VACUUM` + `PRAGMA quick_check` — reclaims freelist pages and verifies integrity |

**Set up** (run once; adjust `--at` / `--day` as needed):

```bash
sk cron add wal-checkpoint
# fallback: python3 ~/.copilot/tools/cron-tasks.py add wal-checkpoint

sk cron add vacuum
# fallback: python3 ~/.copilot/tools/cron-tasks.py add vacuum
```

**Custom schedule examples:**

```bash
sk cron add wal-checkpoint --at 02:00
sk cron add vacuum --day saturday --at 03:00
```

**Verify** after the next run:

```bash
sk cron list
# Inspect the most recent entry in SESSION_STATE/cron-executions.jsonl
# Result fields: status, freed_bytes (vacuum), before/after sizes, elapsed_ms
```

**Behavior notes:**
- If `knowledge.db` is missing, both tasks return `status=missing` and write a standard artifact noting the DB was absent (no VACUUM or checkpoint is performed, safe to retry).
- If the DB is locked by another process, tasks return `status=busy` and do **not** advance `last_run_at`, so they retry on the next scheduled run.
- VACUUM acquires an exclusive lock; avoid scheduling it at peak indexing times.

---

## Watcher Management

```bash
# Start watcher manually
python3 ~/.copilot/tools/watch-sessions.py

# macOS: via LaunchAgent (auto-start on login)
launchctl load ~/Library/LaunchAgents/com.copilot.watch-sessions.plist

# Restart via auto-update operator surface
sk update --restart-watch
```

The watcher uses adaptive polling: 5 s / 30 s / 300 s tiers based on session activity.

---

## Troubleshooting

### Copilot CLI auto-heal

If `copilot update` fails with `ENOENT` or `EPERM` on a rename inside `pkg/universal/`:

```bash
sk heal --status   # Diagnose
sk heal --heal     # Fix
sk heal --update   # Heal + retry copilot update
# fallback: python3 ~/.copilot/tools/copilot-cli-healer.py [--status|--heal|--update]
```

Root cause: upstream Node updater calls `fs.rename(src, dst)` without checking that `src` exists, leaving stale `.replaced-*` dirs behind.

Prevent recurrence by scheduling a daily heal:

```bash
sk heal --install-schedule
# or:
sk install --install-healer
```

📖 Details: [docs/copilot-cli-healer.md](copilot-cli-healer.md)

### Stuck dispatched-subagent marker

If `git commit` is blocked by a stale `dispatched-subagent-active` marker:

```bash
# Preferred: complete the tentacle cleanly
sk tentacle complete <name>

# Emergency: remove marker directly
rm ~/.copilot/markers/dispatched-subagent-active
```

Markers expire automatically after 4 hours (TTL dead-man switch). After clearing, re-dispatch any tentacles still in flight.

### FTS integrity errors

```bash
sk index status            # Check FTS integrity
sk index build             # Rebuild index
```

### Sync not working

```bash
sk sync config --status --json   # Check config
sk sync status --health-check    # Health check
sk sync run --once               # Manual one-shot sync
# fallback: python3 ~/.copilot/tools/sync-config.py --status --json
# fallback: python3 ~/.copilot/tools/sync-status.py --health-check
# fallback: python3 ~/.copilot/tools/sync-daemon.py --once
```

Common issues:
- `connection_string` not set → daemon is local-only/idle (not an error)
- Gateway URL must be HTTP/HTTPS — not a raw Postgres/libSQL DSN
- `sync-gateway.py` is reference/mock only — use a real gateway in production

### Knowledge base not growing

```bash
# Trigger a manual re-index
sk index build
sk index extract
```

If running in watch mode, check watcher status:

```bash
sk update --watch-status
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
sk checkpoint save --title "Auth done" --overview "JWT added"

# List checkpoints
sk checkpoint restore --list

# Restore / inspect
sk checkpoint restore --show latest

# Diff checkpoints
sk checkpoint diff --from 1 --to latest
# fallback: python3 ~/.copilot/tools/checkpoint-save.py / checkpoint-restore.py / checkpoint-diff.py
```

Hooks **never** auto-save checkpoints. Save them manually at meaningful milestones.

---

## Tentacle Operator View

```bash
# Dashboard: all tentacles and states
sk tentacle status

# Next step for a specific tentacle
sk tentacle next-step <name>
sk tentacle next-step <name> --all     # All pending todos

# Verify and close
sk tentacle verify <name> "python3 test_fixes.py" --label "tests"
sk tentacle handoff <name> "Summary" --learn
sk tentacle complete <name>
# Or: combine verify + complete in one step (fail-open)
sk tentacle complete <name> --auto-verify "python3 test_fixes.py"
# fallback: python3 ~/.copilot/tools/tentacle.py [status|next-step|verify|handoff|complete] ...
```

> Full tentacle workflow: **[docs/USAGE.md](USAGE.md#tentacle-orchestration)**

> **Resilience and recovery** (compaction, interruption, quota/rate-limit): **[docs/RESILIENCE-RUNBOOK.md](RESILIENCE-RUNBOOK.md)**

---

## Browse UI — Operator Diagnostics Settings Page

The `/settings/` page in the Browse UI is the primary **browser-based operator surface**.
All diagnostic panels are read-only — no write operations are exposed — except the **Hosts & connections** card which manages host profiles.

| Card | API endpoint | Shows |
|------|-------------|-------|
| Sync diagnostics | `/api/sync/status` | Mode, pending txns, failed ops, gateway config, rollout guidance |
| Trend Scout diagnostics | `/api/scout/status` | Config, grace window, audit checks, discovery lanes |
| Trend Scout research pack | `/api/scout/research-pack` | Latest pack summary, repo count, novelty/risk/follow-up snippets |
| Tentacle runtime diagnostics | `/api/tentacles/status` | Active tentacles, dispatch marker, registry, audit checks |
| Skill outcome metrics | `/api/skills/metrics` | Pass rate, outcomes, skill usage summary |
| System health | `/healthz` | DB schema version, session count, knowledge entries, last indexed |
| **Hosts & connections** | *(localStorage only)* | List, add, remove, default, restore-local for operator host profiles |

Each card with live data also renders an **Operator checks (read-only)** panel that lists
safe CLI commands the operator can **copy** to their terminal. The browser never executes
commands — the panel is display-only with copy-to-clipboard buttons.

Navigate to the Settings page:
```
http://localhost:<port>/settings/?token=<token>
```

---

## Browse UI — Operator Console (`/chat`)

The `/chat` route is the browser-managed Copilot CLI execution console. It is distinct from the read-only settings and diagnostics surfaces and is the only browse page that actively launches Copilot CLI.

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

If the browser reloads while a run is still executing, the backend keeps the run alive as long as the browse server process is still running. On page load, `/chat` refetches the active session's run list, rediscovers any non-terminal run, and reconnects the live stream instead of waiting for the run to finish first.

The console launches a fresh Copilot CLI process per prompt so hooks, permissions, and current CLI behavior remain intact. This means model/agent response time is dominated by Copilot CLI startup, context resume, hooks, and model latency; the UI should still show the submitted prompt immediately, auto-scroll streamed output, and report elapsed wall-clock time after completion.

### Guardrails

- All workspaces and file-review paths are normalized against `~/`; paths outside `Path.home()` are rejected.
- Prompt text is capped at 4096 characters.
- `/api/operator/*` uses the same per-launch browse token as the rest of the UI.
- Runs launched from `/chat` still inherit the installed Copilot CLI's hooks, custom instructions, and permission system. Browser use does not bypass briefing/tentacle/learn or other active policy gates.

### Chat transcript — visible indicators

| Indicator | Where it appears | What it means |
|-----------|-----------------|---------------|
| **Final answer text** | AssistantBubble body | Promoted from user-facing `assistant.message`, `session.task_complete` summary, or `task_complete` tool result; procedural completion summaries such as "Acknowledging the greeting and closing the turn." are suppressed even when the CLI emits them as assistant text. |
| **Elapsed duration** | AssistantBubble footer (e.g. `41s`, `2m 5s`) | Wall-clock time from `started_at` to `finished_at`; shown only after the run finishes. |
| **Context badge** | MetadataBar (session header) | `context ready` (green) when `resume_ready: true`; `new context` otherwise. Reflects whether the active host offered a resumable context window. This badge is independent of CLI session adoption — a fresh operator session also shows `context ready` once the CLI warms up its context window. |
| **Adopted badge** | MetadataBar / session list | Shown when `source = "cli_adopt"`. Indicates the operator session is backed by an existing CLI session UUID stored in `resume_target`. |
| **Confirmation pending** | Composer / header | Composer is disabled and a confirmation prompt is shown until `confirmed_at` is set on an adopted session. |

### Chat Resume / CLI Session Adoption

The **From CLI history** flow lets an operator resume an existing Copilot CLI session from the
browser without knowing the CLI UUID or session path.

> Architecture details, two-ID model, and guardrail inventory:
> **[docs/ARCHITECTURE.md — CLI Session Adoption / Two-ID Model](ARCHITECTURE.md#cli-session-adoption--two-id-model)**

#### Operator flow

1. Open `/chat`.
2. Click **From CLI history** (in the New Chat dialog or the session header).
   `CliSessionPicker` loads the list from `GET /api/operator/cli-sessions`.
3. Select a CLI session.  The picker calls `POST /api/operator/sessions/adopt`.
   - Backend creates an operator session with `source="cli_adopt"` and stores the CLI UUID in
     `resume_target`.  The operator session gets a new ID separate from the CLI UUID.
   - HTTP 200 with a session body means the CLI session was previously adopted but is not yet
     confirmed (idempotent re-adopt); the response includes the existing operator session.
   - HTTP 409 means the CLI session was already adopted and confirmed (error-only body; no
     session object is returned).  Locate the existing operator session from the session list
     and navigate to it instead of creating a duplicate.
4. The `ConfirmAdoptionPanel` appears.  Review or set the workspace directory and any
   `add_dirs`.  Click **Confirm**.
   - Backend sets `confirmed_at` and `resume_ready=True`.
   - The composer is enabled.
5. Submit a prompt normally.  The backend invokes `copilot -p <prompt> --resume=<cli_uuid>`
   (plus `--add-dir <path>` for each configured add_dirs entry) with no `--workspace` or
   `--name` argument.
6. The transcript streams live; run history is persisted under `operator-console/<operator_id>/`.

#### Recovery procedures

**Stale / missing CLI session (session was deleted after adoption):**
- `CliSessionPicker` shows a warning badge on entries whose CLI UUID no longer appears in
  `GET /api/operator/cli-sessions`.
- `GET /api/operator/cli-sessions/{uuid}` returns 404 for missing sessions.
- Option A: delete the operator session (`POST /api/operator/sessions/{id}/delete`) and
  re-adopt from a fresh CLI session.
- Option B: there is no supported re-confirm flow when the CLI session is missing.
  `POST /api/operator/sessions/{id}/confirm` calls `get_cli_session_by_id` and returns
  `CLI_SESSION_NOT_FOUND` / 404 when the CLI UUID is absent; it will not fall back to a
  new context.  Delete the unconfirmed operator session and adopt a different CLI session.

**Duplicate adoption:**
`POST /api/operator/sessions/adopt` returns one of two responses:

- **HTTP 200** — duplicate exists but is still unconfirmed; response body includes the existing
  operator session (idempotent re-adopt).  Continue from the `ConfirmAdoptionPanel`.
- **HTTP 409** (`ALREADY_ADOPTED`) — duplicate is already confirmed; response is error-only with
  no session object.  To locate the existing session, open the operator session list and navigate
  to it in `/chat`.

**Context badge `context ready` vs CLI adoption confirmation:**
`context ready` reflects `resume_ready: true` on the active host's context probe.  It is
unrelated to the CLI adoption confirmation step.  An adopted session may show `context ready`
once the first successful run completes.

**Deleting an operator session:**
`POST /api/operator/sessions/{id}/delete` removes operator-side state only.  The CLI session
tree under `~/.copilot/session-state/` is never modified.

### Compatibility

- `watch-sessions.py` continues to process normal Copilot session artifacts; the operator console reads its own persisted history from `operator-console/`.
- `auto-update-tools.py` does not manage active operator runs or restart the browse server.
- After Python changes to `browse/api/operator.py` or `browse/core/operator_console.py`, restart the browse server manually.

Direct link:

```text
http://localhost:<port>/chat/?token=<token>
```

---

## Browse UI — Global Host Selection

Browse-wide host state is managed by `HostProvider` (root layout context) and persisted by `host-profiles.ts` (localStorage). All pages read the active host from `useHostState()` — there is no per-page host state.

### Quick reference

| Action | Where |
|--------|-------|
| Switch active host | Header → global host dropdown (AWS-region-style compact selector) |
| Add / remove / set-default / restore-local | Settings → **Hosts & connections** (`/settings#hosts`) |
| Verify active host in code | `useHostState().host` — resolves via `getEffectiveHost()` |

### Same-tab refresh

All profile mutations and selection changes dispatch `browse:host-change` on `window`. `HostProvider` listens and re-evaluates immediately. No page reload is needed after switching hosts or saving/deleting a profile.

### Session create dialog pre-population

When `SessionCreateDialog` opens (`/chat → New Chat`), it reads the global active host via `useHostState()` and pre-fills the host picker. The user may still override the host for that session; the override is local to that dialog open.

### Diagnostics enabled gate

`diagnosticsEnabled` (from `useHostState()`) is `true` when any of the following holds:
- A remote host with a non-empty `base_url` is active, **or**
- `NEXT_PUBLIC_API_BASE` is set at build time, **or**
- `LOCAL_HOST` is selected and a same-origin `/healthz` probe succeeds.

When the same-origin probe fails (for example on a Firebase-hosted static origin with no local
backend), `diagnosticsEnabled` stays `false`.

When `diagnosticsEnabled` is `false` (e.g. the static UI is opened on its Firebase domain without a remote host configured), all diagnostic API calls are suppressed and each card shows a prompt to configure a host in Settings → Hosts & connections.

---

> **Facts vs guidance separator:** Verified facts in this section are derived from source-code inspection and web research. Operational guidance is labelled **[guidance]**.

The browse server binds to `127.0.0.1` by design. Remote or mobile access requires a tunnel.

### DNS coexistence: tunnel subdomain + Firebase on apex domain

**Verified fact:** Cloudflare Tunnel can front a subdomain (e.g. `browse.example.com`) while the apex domain (`example.com`) remains served by Firebase Hosting. The two services use separate DNS records and do not conflict.

Two approaches:

| Approach | How | Trade-offs |
|----------|-----|------------|
| **Cloudflare DNS for the whole zone** (recommended) | Move your domain nameservers to Cloudflare. Keep A/AAAA records for the apex pointing to Firebase Hosting IPs. Add a Cloudflare Tunnel CNAME for the browse subdomain via `cloudflared tunnel route dns`. | Full Cloudflare Zero Trust + WAF + Access features available. Standard Cloudflare Tunnel workflow. |
| **External DNS only** | Keep DNS at current provider. After creating the tunnel, add a CNAME record: `browse-subdomain → <tunnel-id>.cfargotunnel.com`. | No Cloudflare WAF/caching on the subdomain. Cloudflare Access policies still apply (enforced at the tunnel edge). Firebase on the apex domain is unaffected. |

**[guidance]** For a personal operator setup that primarily needs access control, the external-DNS CNAME approach is the simpler path. Move to full Cloudflare DNS only if WAF or caching on the subdomain is needed.

Firebase Hosting custom-domain verification uses A records or CNAME records at your DNS registrar. Neither approach disturbs these; the Firebase apex records remain unchanged.

### Starting the tunnel

```bash
# Install cloudflared (macOS)
brew install cloudflared

# Authenticate and create a named tunnel
cloudflared tunnel login
cloudflared tunnel create copilot-browse

# Configure ingress in ~/.cloudflared/config.yml:
#   tunnel: <tunnel-id>
#   credentials-file: ~/.cloudflared/<tunnel-id>.json
#   ingress:
#     - hostname: browse.example.com
#       service: http://127.0.0.1:<browse-port>
#     - service: http_status:404

# Route DNS (Cloudflare-managed zone only; skip for external DNS)
cloudflared tunnel route dns copilot-browse browse.example.com

# Run the tunnel
cloudflared tunnel run copilot-browse
```

The browse server must already be running on the configured port before or alongside `cloudflared`.

### Security posture for remote exposure

#### Browse token

- The per-launch browse token is passed as `?token=<token>` in the URL on first load, then stored as a `browse_token` cookie (`HttpOnly; SameSite=Strict; Path=/; Max-Age=86400`).
- **[guidance]** Never share the first-load URL (containing the token in the query string) in publicly visible locations — browser history and server logs will record it. Use Cloudflare Access (see below) as a second auth layer so the token URL is only reachable by authenticated users.
- The token is per-launch: restarting `browse.py` with a different `--token` value invalidates previous sessions.

#### Cloudflare Access (recommended)

**[guidance]** Add a Cloudflare Access policy on the operator tunnel hostname to require identity verification (email OTP, GitHub SSO, or Google OAuth) before the tunnel endpoint is reachable. This means an attacker who discovers the subdomain cannot even attempt to brute-force the browse token — Access gates the connection first.

Configure Access in **Cloudflare Zero Trust → Access → Applications → Add an application → Self-hosted**, with your tunnel hostname.

#### Known blocker: Origin check for POST requests (code-level issue)

**Verified from source code (`browse/core/auth.py` · `check_origin`):** The CSRF origin check compares the `Origin` header to `http://{Host}`. Behind Cloudflare Tunnel, the browser sends `Origin: https://browse.example.com` but the check builds `http://browse.example.com` — these do not match. All POST mutations (prompt submission, session create/delete) return **403 Forbidden**.

This is a code-level fix required in `browse/core/auth.py`: the check must accept `https://` origins when `X-Forwarded-Proto: https` is present, or accept both schemes for the configured hostname. **This fix is not in the scope of this playbook entry.** Until it is applied, the operator console (`/chat`) is read-browseable behind the tunnel but prompt submission will fail. Open a fix tentacle or issue targeting `browse/core/auth.py`.

#### Cookie `Secure` flag

**Verified from source code and tests:** forwarded HTTPS headers are trusted only when `BROWSE_TRUSTED_PROXY=1` (or `true` / `yes`) is set on the operator host. Without that opt-in, the server ignores `X-Forwarded-Proto` / `X-Forwarded-Ssl`, so auth cookies are still issued but **without** the `Secure` flag.

**[action]** If you run browse behind Cloudflare Tunnel, ngrok, a load balancer, or any HTTPS reverse proxy, export:

```bash
export BROWSE_TRUSTED_PROXY=1
```

before starting `browse.py` (or add it to your service manager / LaunchAgent / systemd unit). This preserves the `Secure` flag on cookies for proxied HTTPS deployments while keeping untrusted forwarded headers disabled by default.

#### Same-origin assumption in the UI (Cloudflare Tunnel deployment)

The Next.js static export makes all API calls to relative paths (`/api/*`) on the same origin. This assumption holds behind Cloudflare Tunnel: both the static UI and the Python API are served from the same origin. No cross-origin configuration is needed for this deployment mode.

**Firebase Hosting deployment changes this assumption.** When the static UI is served from a Firebase custom domain while the API lives at the operator's tunnel URL, all API calls become cross-origin. The operator host implements an explicit CORS allowlist, Bearer auth, and a capabilities endpoint. See [Firebase-hosted control plane](#firebase-hosted-control-plane) below.

### Mobile access

**Verified fact (same-origin / Cloudflare Tunnel deployment):** The browse server binds to `127.0.0.1`; direct LAN access from a mobile device is not possible. Via Cloudflare Tunnel, mobile browsers reach the app over HTTPS at the configured subdomain.

| Feature | Mobile status |
|---------|--------------|
| All app page routes (`/chat`, `/sessions`, `/search`, `/insights`, `/graph`, `/settings`) | ✅ Work in iOS Safari and Android Chrome — Next.js static export, no SSR |
| Token auth (first-load `?token=…`) | ✅ Works — cookie is stored in browser session storage per architecture notes |
| SSE streaming (`/chat` live transcript) | ✅ Works — iOS Safari 13+ and Android Chrome support `EventSource` |
| POST mutations (prompt submit) | ⚠️ Requires `check_origin` fix in `browse/core/auth.py` to accept `https://` origins (see [Known blocker](#known-blocker-origin-check-for-post-requests-code-level-issue) above) |
| Keyboard shortcuts (`g c`, `g s`, etc.) | ⚠️ Not accessible without a physical keyboard |

**[guidance]** To verify mobile access: open `https://browse.example.com/?token=<token>` on iOS Safari or Android Chrome (substitute your operator tunnel hostname). The sessions list and search pages should load. The operator console page loads, but prompt submission requires the Origin fix to be applied first.

---

## Firebase-hosted control plane

> **Facts vs guidance separator:** Verified facts are derived from config inspection and code review. Architecture notes marked **[guidance]** describe intended or recommended work.

**Verified fact:** `firebase.json` and `.firebaserc` are committed to the repo. `firebase.json` defines a hosting target named `agents` serving the generated `browse-ui/dist/` directory. `.firebaserc` contains a placeholder project ID (`your-project-id`) — operators configure the real project ID and custom domain in a **private hosting repo** (see [external hosting-repo pattern](#external-hosting-repo-pattern) below).

### Topology

```
                ┌──────────────────────────────────┐
  browser ─────▶│  Firebase Hosting (static)        │
                │  <your-firebase-domain>            │
                │  generated browse-ui dist artifact  │
                └────────────────┬─────────────────┘
                                 │  cross-origin /api/operator/* calls
                                 │  (operator URL configured per host profile)
                                 ▼
                ┌──────────────────────────────────┐
                │  Cloudflare Tunnel                │
                │  <your-tunnel-host>               │
                │  ──▶ browse.py on operator host   │
                │      REST + SSE (/api/operator/*) │
                └──────────────────────────────────┘
```

In this topology, the Firebase-hosted static UI is the **control plane** — a durable, always-available URL the operator opens from any device. The **operator host** (the machine running `browse.py`) is reached via its public tunnel URL, configured as a host profile in the UI.

### External hosting-repo pattern

Actual production deployments should live in a **private hosting repo** rather than in this open-source repo. Recommended steps:

1. Create a private repo (e.g. `my-org/copilot-ui-hosting`).
2. Create a `.firebaserc` with your real Firebase project ID and target-to-site mapping.
3. Produce a Firebase-compatible build of `browse-ui/` (see [build modes](#build-modes) below) and copy `dist-release/` into the hosting repo.
4. Run `firebase deploy --only hosting:agents` from the private repo.

This keeps personal project IDs and custom domains out of the public repo.

### What is implemented

| Component | Status |
|-----------|--------|
| `firebase.json` hosting config (template, repo) | ✅ Committed |
| `.firebaserc` generic template (repo) | ✅ Committed — fill in your project ID in your private hosting repo |
| Firebase CLI deploy flow | ✅ Documented |
| Firebase custom domain verification | 🔲 Manual console step in your private hosting environment |
| DNS records for the Firebase domain | 🔲 Manual step at DNS registrar or Cloudflare |
| Firebase-targeted build (`pnpm build:release`) | ✅ Implemented |
| Cross-origin API: CORS allowlist + Bearer auth + capabilities endpoint | ✅ Implemented on the operator host |

### Build modes

**Verified from source:** `browse-ui/next.config.ts` reads `basePath` from `NEXT_BASE_PATH` and
defaults to `""`, so the default `pnpm build` artifact is root-relative for both the local Python
browse server and Firebase Hosting. The Python browse server now serves the app at root and keeps
`/v2/*` compatibility redirects for old bookmarks.

For Firebase Hosting, the release artifact must emit `/_next/…` asset URLs because Firebase serves static files from the domain root. Use the dedicated release build:

1. `cd browse-ui && pnpm release:check`
2. Copy `browse-ui/dist-release/` into your private hosting repo
3. Confirm `pnpm release:check` passed
4. `firebase deploy --only hosting:agents` — run from your private hosting repo

### Firebase release-gate check

**Run this before every Firebase deploy** to build the root-hosted artifact and catch basePath leakage before it reaches production:

```bash
# From browse-ui/:
pnpm release:check
```

**What it checks (facts):**

- Builds the release artifact into `browse-ui/dist-release/` without touching the local generated `browse-ui/dist/`.
- Reads `browse-ui/dist-release/chat/index.html` directly from the filesystem (no server required).
- Asserts zero `/v2/_next/` occurrences — these are the broken asset shape that 404 on Firebase.
- Asserts at least one `/_next/` occurrence — confirms the export is non-trivial.

**Failure interpretation:**

| Symptom | Likely cause |
|---------|-------------|
| `dist-release/chat/index.html` not found | Release artifact was not built — run `pnpm release:check` first |
| `Found N /v2/_next/ reference(s)` | The release build did not run, or `NEXT_BASE_PATH` leaked back to `/v2` |
| `No /_next/ references found` | The page did not build correctly; inspect `dist-release/` for build errors |

The proof test is skipped in normal CI. `pnpm release:check` enables it explicitly and runs it in isolation, so the rest of the Playwright suite does not get forced onto the root-hosted artifact.

### Post-deploy verification (live)

`pnpm release:check` is a pre-deploy build gate. After `firebase deploy` completes, run the
companion live check from `browse-ui/` to confirm the hosted origin is actually serving the new
SHA and that HTML routes revalidate (so users do not keep a pre-fix app-shell HTML for an hour):

```bash
# From browse-ui/, against agents.linhngo.dev by default:
pnpm verify:deploy

# Pin to the SHA you just deployed (recommended in CI):
EXPECTED_SHA=$(git rev-parse --short HEAD) pnpm verify:deploy

# Or target a different origin:
pnpm verify:deploy --origin https://agents.example.com --expected-sha abc1234
```

**What it asserts (live HTTP, exits non-zero on failure):**

- `GET /version.json` is reachable; prints `buildHash`, `builtAt`, `basePath`.
- If `EXPECTED_SHA` / `--expected-sha` is set, the hosted `buildHash` must start with it.
- `/version.json` and every app-shell route HTML (`/`, `/chat/`, `/sessions/_verify`,
  `/search/`, `/insights/`, `/graph/`, `/settings/`, `/diagnostics/`)
  `Cache-Control` must revalidate
  (`no-cache`, `no-store`, or `max-age=0`). `must-revalidate` alone is **not**
  sufficient — it only forces revalidation once the response is already stale,
  so a `max-age=3600, must-revalidate` response still lets the browser keep a
  pre-fix app-shell for an hour.
- A sampled `/_next/static/**` chunk must still be `immutable` with a long `max-age`
  (so the cache-header changes did not regress hashed-chunk caching, including
  hashed fonts/images under `/_next/static/media/**`).

**Why this is a separate step:** `release:check` must stay offline so CI does not depend on the
public DNS/CDN. `verify:deploy` is an explicit, network-gated operator command. The first failure
mode this catches is exactly the one observed on 2026-05-23: `version.json` reported
`buildHash=9468685` but the homepage HTML was served with `Cache-Control: max-age=3600`, so users
kept the pre-fix app-shell until the CDN/browser cache expired.

**Verified repro (2026-05-03):** A root-hosted Firebase deployment of `browse-ui` returned HTML with `/v2/_next/static/…` URLs. Requests to `/v2/_next/…` returned 404; requests to `/_next/…` returned 200. Root cause: the build included `basePath: "/v2"` in `next.config.ts`.

### Hosted Launcher — One-Shot Install/Uninstall (Issue #57)

`browse.py --install-launcher` installs a convenience launcher for the hosted-shell Windows
(and POSIX) flow so that a single command starts `browse.py` pre-configured for
`https://agents.linhngo.dev`.

#### What gets installed

| File | Platform | Purpose |
|------|----------|---------|
| `~/.copilot/bin/browse-hosted` | POSIX (macOS / Linux) | Executable shell script; runs `browse.py --hosted-bootstrap --port 8765` |
| `~/.copilot/bin/browse-hosted.cmd` | Windows | CMD script; same command |
| `~/.copilot/bin/browse-hosted.url` | Windows only | Internet shortcut that opens `https://agents.linhngo.dev` in the default browser |
| `~/Desktop/Browse Backend.lnk` | Windows only | Desktop shortcut; runs `python.exe browse.py --hosted-bootstrap --port 8765` |
| `~/Desktop/Browse UI.lnk` | Windows only | Desktop shortcut; opens `https://agents.linhngo.dev` in Edge default profile |

Desktop `.lnk` shortcuts are created via PowerShell `WScript.Shell` COM (stdlib `subprocess`).
No browser security-bypass flags (`--disable-web-security`, `--allow-insecure-localhost`, etc.)
are used in any shortcut target.

`~/.copilot/bin/` must be on `PATH` (the `sk` launcher install already adds it; run
`python3 install.py --install-sk` if it is not present yet).

#### Install

```bash
# POSIX
python3 browse.py --install-launcher

# Windows
python browse.py --install-launcher
```

After install, start the backend with:

```bash
browse-hosted                          # open-auth, random port will be 8765
browse-hosted --token <your-token>    # token-protected
```

Then open `https://agents.linhngo.dev` (or double-click `Browse UI.lnk` on the Desktop, or
`browse-hosted.url` in `~/.copilot/bin/`) and add `http://127.0.0.1:8765` as a host profile.
Alternatively, double-click `Browse Backend.lnk` on the Desktop to start the backend in a
console window.

#### Local browser fallback

If the hosted page cannot reach the local backend, use the local browser fallback commands:

```bash
python3 browse.py --list-browsers
python3 browse.py --hosted-bootstrap --open-browser chrome
```

`--list-browsers` reports allowlisted Chrome, Edge, Firefox, and Safari candidates. Safari is
reported as unsupported for hosted-to-local recovery; Chrome/Edge are preferred, and Firefox is
usable only through the direct local app path (`http://127.0.0.1:8765/`). `--open-browser` only
opens loopback URLs and never appends token query strings.

**Security note:** The launcher does **not** use any browser security-bypass flags
(`--disable-web-security`, `--allow-insecure-localhost`, etc.). Use `--hosted-bootstrap` for
proper CORS / PNA configuration.

#### Edge caveat

`Browse UI.lnk` targets `msedge.exe` at one of the standard install paths:
- `C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe`
- `C:\Program Files\Microsoft\Edge\Application\msedge.exe`

If Edge is not found at either path, the UI shortcut is skipped and a warning is printed;
install Edge first or create the shortcut manually.  The shortcut opens Edge in the **default
profile** — no custom `--user-data-dir` and no security-bypass flags.

#### Uninstall

```bash
python3 browse.py --uninstall-launcher
```

Removes `browse-hosted` (or `.cmd`), `browse-hosted.url`, and the desktop `.lnk` shortcuts.
Does **not** affect the `sk` launcher or any other install.py-managed files.

#### Manual proof path (Windows CI not available)

Because this environment is macOS, Windows-specific behaviour (`.cmd` script execution,
`.url` shortcut file association, PowerShell COM `.lnk` creation, Edge launch) cannot be
exercised in CI here. If you are validating on Windows, perform these manual checks:

1. Run `python browse.py --install-launcher` in a Command Prompt.
2. Verify `%USERPROFILE%\.copilot\bin\browse-hosted.cmd` exists and contains `--hosted-bootstrap`.
3. Verify `%USERPROFILE%\.copilot\bin\browse-hosted.url` exists and opens `https://agents.linhngo.dev`.
4. Verify `%USERPROFILE%\Desktop\Browse Backend.lnk` exists (right-click → Properties shows Target: `python.exe` with `browse.py --hosted-bootstrap --port 8765` as Arguments; no security-bypass flags).
5. Verify `%USERPROFILE%\Desktop\Browse UI.lnk` exists (Properties shows Target: `msedge.exe`, Arguments: `https://agents.linhngo.dev`; no `--user-data-dir`, no security-bypass flags).
6. Double-click `Browse Backend.lnk` and confirm the backend starts in a console on port 8765.
7. Double-click `Browse UI.lnk` and confirm Edge opens `https://agents.linhngo.dev` in the default profile.
8. Run `browse-hosted` (if `~/.copilot/bin` is on PATH) and confirm the server starts on port 8765.
9. Run `python browse.py --uninstall-launcher` and confirm `browse-hosted.cmd`, `browse-hosted.url`, `Browse Backend.lnk`, and `Browse UI.lnk` are all removed.

**Interpretation (not a guaranteed fact from this macOS environment):** Steps 4–7 above
confirm the Edge-specific shortcut target.  The CI gates verify only the Python-level logic
(path helpers, PowerShell script generation, no-bypass-flag assertions, and POSIX install
round-trip).  Windows runtime proof requires manual execution of the above checklist.

### Loopback Bootstrap for Hosted UI (Issue #49)

The hosted static UI (`https://agents.linhngo.dev`, `https://agents-linhngo-dev.web.app`) can
auto-detect a locally running `browse.py` instance through loopback fetches. Chromium/Edge use
the browser's [Private Network Access (PNA)](https://wicg.github.io/private-network-access/) /
Local Network Access flow; Safari/Firefox behavior depends on standard CORS and browser policy.

#### Enabling loopback bootstrap

Start `browse.py` on the local machine with the `--hosted-bootstrap` flag:

```bash
# Open auth — probe will auto-activate the backend:
python3 browse.py --hosted-bootstrap

# Token auth — probe succeeds but the frontend shows manual-token state:
python3 browse.py --hosted-bootstrap --token <your-secret-token>
```

`--hosted-bootstrap` does:
1. Binds to `127.0.0.1` (loopback only — not exposed on the LAN).
2. Appends canonical hosted origins to `BROWSE_CORS_ORIGINS`:
   `https://agents.linhngo.dev` and `https://agents-linhngo-dev.web.app`.
3. Emits `Access-Control-Allow-Private-Network: true` on PNA preflights from allowlisted origins.
4. Prints startup guidance including the discovery URL and auth instructions.

#### Discovery endpoint

`GET http://127.0.0.1:8765/.well-known/browse-host` returns:

```json
{
  "schema": "browse-host/1",
  "status": "ok",
  "auth": "open" | "token",
  "manual_token_required": false | true,
  "capabilities": ["discovery", "healthz", "api"],
  "cors_origins_configured": true
}
```

Verify locally:
```bash
curl -s http://127.0.0.1:8765/.well-known/browse-host | python3 -m json.tool
```

#### Manual-token fallback

When `manual_token_required: true` the hosted UI shows a manual-token prompt instead of
auto-activating. The user supplies the token; the frontend never invents a blank credential.

#### Browser support caveat

**Interpretation (not a guaranteed fact):**

| Browser family | Loopback from hosted HTTPS | Action |
|---|---|---|
| Chromium / Edge 104+ (pre-Chrome 142) | Requires PNA preflight headers and may show a local-network permission prompt. | Use `--hosted-bootstrap`; accept the browser prompt if shown. Do **not** use `--disable-web-security` for normal operation. |
| **Chromium / Edge Chrome 142+** | LNA (Local Network Access) is enforced. The first probe triggers a permission dialog. | Accept the permission prompt once; use `LocalNetworkAccessAllowedByOrigins` enterprise policy to suppress for managed fleets. See [CONNECTIVITY-TROUBLESHOOTING.md §Chrome LNA](CONNECTIVITY-TROUBLESHOOTING.md#chrome-138-local-network-access-lna-permission-model). |
| Firefox | Does not implement Chromium's PNA/LNA header flow. | Prefer Chrome/Edge for hosted detection, or open the direct local app at `http://127.0.0.1:8765/`. |
| Safari | Unsupported for hosted-to-local recovery. | Use Chrome/Edge, an HTTPS tunnel, or the direct local app. |
| Strict enterprise browsers | May block local-network access regardless of headers. | Use HTTPS tunnel if available; otherwise see outbound control-bus guidance. |

**Non-loopback HTTP hosts** (`http://192.168.x.x`, `http://custom.host`) are **not reachable**
from HTTPS hosted pages in any browser.

For predictable cross-browser support, expose the backend over HTTPS via a tunnel and add it as
a host profile manually in Settings → Hosts & connections.

#### Tunnel-hostile networks (FPT / campus / corporate proxy)

If `cloudflared` / `ngrok` never connect, confirm the network policy before debugging the app:

```bash
# DNS block / NXDOMAIN
nslookup abc123.ngrok-free.app

# SNI / TLS reset
curl -vk https://abc123.ngrok-free.app

# Alternate tunnel port blocked (Cloudflare Tunnel example)
nc -zv 198.41.192.7 7844
```

If those checks fail:

1. keep `--hosted-bootstrap` for Chromium / Edge loopback when available
2. otherwise use Same-Origin Relay / Gateway
3. otherwise fall back to the documented outbound control-bus architecture

See:

- [docs/CONNECTIVITY-TROUBLESHOOTING.md](CONNECTIVITY-TROUBLESHOOTING.md)
- [docs/HOSTED-SHELL-ARCHITECTURE.md §5](HOSTED-SHELL-ARCHITECTURE.md#5-outbound-control-bus-mode-tunnel-hostile-networks)

_Full spec: [docs/HOSTED-SHELL-ARCHITECTURE.md §4](HOSTED-SHELL-ARCHITECTURE.md#4-hosted-loopback-bootstrap--pnahttp-shipped-issue-49)_

---

## Broker Mode (Outbound-Only Control Bus, Issue #71)

> Use this when tunnels (`cloudflared`, `ngrok`) are blocked on the operator's network (FPT,
> corporate DPI, university filtering) and the operator wants to control browse.py from any
> device. No inbound port is opened.

### Prerequisites

1. **Create a Telegram bot**: message `@BotFather`, run `/newbot`, copy the token.
2. **Find your Telegram user ID**: message `@userinfobot` or `@RawDataBot`.
3. Set environment variables:

```bash
export BROWSE_BROKER_TELEGRAM_TOKEN="123456:ABC-DEF..."
export BROWSE_BROKER_AUTHORIZED_USER_ID="<your integer user_id>"
```

### Start the broker

```bash
python browse.py --broker-mode telegram [--db /path/to/knowledge.db]
```

Expected startup output:

```
[broker/telegram] Starting outbound-only long-poll loop (no inbound port opened). Ctrl-C to stop.
[broker/telegram] Authenticated as @YourBot (id=123456)
```

### Available commands

Send any of these to your bot:

| Command | Description |
|---|---|
| `/status` | Daemon uptime, session count, DB schema version |
| `/search <query>` | FTS5 search, returns top 5 results |
| `/briefing <topic>` | Runs `briefing.py --compact` for the topic |
| `/recent` | Lists 10 most recent sessions |
| `/help` | Command list |

### Security

- Every Telegram update from a user_id ≠ `BROWSE_BROKER_AUTHORIZED_USER_ID` is **silently dropped** — no reply is sent.
- No inbound port is opened. The broker loop polls `api.telegram.org:443` via outbound HTTPS only.
- Outbound calls use `ProxyHandler({})` to bypass any local HTTP proxy that might intercept traffic.

### Verification checklist

```bash
# 1. Confirm Telegram API is reachable from the operator's machine
curl -I https://api.telegram.org

# 2. Confirm no inbound port was opened (run in a second terminal while broker is running)
netstat -an | grep LISTEN | grep -v 127.0.0.1
# Expected: broker-mode does NOT add a new LISTEN entry

# 3. Start broker and confirm "no inbound port opened" message
python browse.py --broker-mode telegram
# Expected first line: "[broker/telegram] Starting outbound-only long-poll loop (no inbound port opened)."

# 4. Send /status to the bot — confirm it replies with uptime and session count.
# 5. Send /search auth — confirm results appear (if index is built).
# 6. Send /briefing authentication — confirm briefing.py runs and returns output.
```

### Blockers / unverified acceptance criteria

The following acceptance criterion from issue #71 **cannot be verified from this machine**:

> **"Verified working on FPT network (manual test by maintainer)"**

The implementation is functionally complete and unit-tested. End-to-end verification on an
actual FPT network requires a human maintainer with FPT access to:

1. Set the two env vars.
2. Run `python browse.py --broker-mode telegram`.
3. Send the 5 commands from a Telegram client and confirm replies.
4. Capture `netstat` output confirming no new LISTEN entry.

Until this is done, issue #71 should remain **open** with the label `needs-manual-verification`.

_Spec: [docs/HOSTED-SHELL-ARCHITECTURE.md §5](HOSTED-SHELL-ARCHITECTURE.md#5-outbound-control-bus-mode-tunnel-hostile-networks)_

---

## Discord Broker Mode (Issue #72)

> **Status:** Code shipped. Needs live credentials to run end-to-end.
> **Architecture:** HTTP REST-polling of Discord channel history. Stdlib-only.
> No WebSocket, no inbound port. Poll latency ~2 s.

### Prerequisites

1. Create a Discord application and bot at <https://discord.com/developers/applications>.
2. Under *Bot* settings, enable *Message Content Intent* and copy the bot token.
3. Invite the bot to a server with **Read Messages** and **Send Messages** permissions.
4. Copy the target channel ID (right-click channel → *Copy Channel ID* with Developer Mode on).
5. Copy your Discord user ID (right-click your profile → *Copy User ID* with Developer Mode on).
6. Set environment variables:

```bash
export BROWSE_BROKER_DISCORD_TOKEN="<your-bot-token>"
export BROWSE_BROKER_DISCORD_CHANNEL_ID="<channel-snowflake-id>"
export BROWSE_BROKER_DISCORD_AUTHORIZED_USER_ID="<your-user-snowflake-id>"
```

### Start the broker

```bash
python browse.py --broker-mode discord [--db /path/to/knowledge.db]
```

Expected startup output:

```
[broker/discord] Starting HTTP-polling loop (no inbound port opened; ~2 s poll interval). Ctrl-C to stop.
[broker/discord] Authenticated as YourBot#1234 (id=123456789)
```

### Available commands

Post any of these to the configured Discord channel:

| Command | Description |
|---|---|
| `/status` | Daemon uptime, session count, DB schema version |
| `/search <query>` | FTS5 search, returns top 5 results |
| `/briefing <topic>` | Runs `briefing.py --compact` for the topic |
| `/recent` | Lists 10 most recent sessions |
| `/help` | Command list |

### Architecture constraint

This broker uses HTTP polling of `GET /channels/{channel_id}/messages?after={snowflake}`.
Poll latency is ~2 s. If sub-second latency is required, the Discord Gateway (WebSocket)
is the correct approach, but it requires a non-stdlib dependency (`discord.py ≥2.0`).
That is a maintainer architecture decision, not something the broker code can resolve.

### Blockers / unverified acceptance criteria (#72)

> 1. **Credentials** — the three env vars above must be set.
> 2. **Maintainer RTT benchmark** — median + p95 latency over ≥100 messages on a
>    tunnel-hostile (FPT) network has not been measured.
> 3. **Latency decision** — ~2 s HTTP-polling may be acceptable for control workflows;
>    if not, the non-stdlib WebSocket SDK must be adopted.

Until all three are resolved, issue #72 should remain **open**.

---

## Ably Broker Mode (Issue #72)

> **Status:** Code shipped. Needs live credentials to run end-to-end.
> **Architecture:** HTTP REST-polling of Ably channel history + REST publish. Stdlib-only.
> No WebSocket, no inbound port. Poll latency ~2 s.

### Prerequisites

1. Create an Ably account at <https://ably.com> and create an app.
2. Copy the API key (format: `app_id.key_id:key_secret`) from the app settings.
3. Optionally, configure inbound/outbound channel names (defaults: `browse-commands` / `browse-responses`).
4. Set environment variables:

```bash
export BROWSE_BROKER_ABLY_API_KEY="<app_id.key_id:key_secret>"
# Optional overrides:
export BROWSE_BROKER_ABLY_CHANNEL_IN="browse-commands"    # default
export BROWSE_BROKER_ABLY_CHANNEL_OUT="browse-responses"  # default
export BROWSE_BROKER_ABLY_AUTHORIZED_CLIENT_ID="operator" # default
```

### Start the broker

```bash
python browse.py --broker-mode ably [--db /path/to/knowledge.db]
```

Expected startup output:

```
[broker/ably] Starting REST-polling loop on 'browse-commands' (no inbound port; ~2.0s interval). Ctrl-C to stop.
[broker/ably] API key accepted; polling 'browse-commands'.
```

### Sending commands

Publish a message to the `browse-commands` Ably channel with your clientId set to
`operator` (or whatever BROWSE_BROKER_ABLY_AUTHORIZED_CLIENT_ID is configured to).
The broker will poll for new messages and publish responses to `browse-responses`.

| Command | Description |
|---|---|
| `/status` | Daemon uptime, session count, DB schema version |
| `/search <query>` | FTS5 search, returns top 5 results |
| `/briefing <topic>` | Runs `briefing.py --compact` for the topic |
| `/recent` | Lists 10 most recent sessions |
| `/help` | Command list |

### Architecture constraint

This broker uses HTTP polling of `GET /channels/{name}/messages?start={ts}&direction=forwards`.
Poll latency is ~2 s. The Ably Realtime WebSocket client would reduce latency to <100 ms but
requires the `ably` PyPI package (non-stdlib). That is a maintainer architecture decision.

### Blockers / unverified acceptance criteria (#72)

> 1. **Credentials** — BROWSE_BROKER_ABLY_API_KEY must be set.
> 2. **Maintainer RTT benchmark** — median + p95 latency over ≥100 messages on a FPT network
>    has not been measured.
> 3. **Latency decision** — ~2 s HTTP-polling may be acceptable; if not, the `ably` PyPI
>    package must be adopted.
> 4. **clientId trust boundary** — The broker drops messages where `clientId` does not match
>    `BROWSE_BROKER_ABLY_AUTHORIZED_CLIENT_ID`. However, `clientId` in REST-published messages
>    is self-reported by the publisher and is **not** server-verified by Ably unless the API
>    key's [capability](https://ably.com/docs/auth/capabilities) is restricted to the authorized
>    clientId.  Without that restriction, any holder of your API key can set any `clientId` and
>    bypass the whitelist.  To enforce the boundary, create a narrowly-scoped Ably API key or
>    token that allows publish only for the specific clientId used by your operator client.
> 5. **Pagination gap** — polling fetches at most 100 messages per cycle.  Burst arrivals
>    exceeding 100 messages in a 2 s poll window will emit a stderr warning and the overflow
>    messages will be silently dropped.  This is safe for interactive usage but should be
>    noted for high-volume environments.

Until all five are resolved, issue #72 should remain **open**.

---

## Slack Broker Mode (Issue #72)

> **Status:** Code shipped. Needs live credentials to run end-to-end.
> **Architecture:** HTTP polling of `conversations.history` + `chat.postMessage`. Stdlib-only.
> No WebSocket, no inbound port. Poll latency ~2 s.

### Prerequisites

1. Create a Slack app at <https://api.slack.com/apps> and install it to a workspace.
2. Add OAuth scopes: `channels:history`, `chat:write` (for public channels) or
   `groups:history` (for private channels).
3. Copy the Bot User OAuth Token (`xoxb-...`).
4. Copy the channel ID (from the channel URL or via API).
5. Copy your Slack user ID (`UXXXXXXX` format — found in your profile settings).
6. Set environment variables:

```bash
export BROWSE_BROKER_SLACK_BOT_TOKEN="xoxb-..."
export BROWSE_BROKER_SLACK_CHANNEL_ID="C01ABCDEF"
export BROWSE_BROKER_SLACK_AUTHORIZED_USER_ID="U01234567"
```

### Start the broker

```bash
python browse.py --broker-mode slack [--db /path/to/knowledge.db]
```

Expected startup output:

```
[broker/slack] Starting HTTP-polling loop (no inbound port opened; ~2 s poll interval). Ctrl-C to stop.
[broker/slack] Authenticated as @yourbot (id=B01ABCDEF)
```

### Available commands

Post any of these to the configured Slack channel:

| Command | Description |
|---|---|
| `/status` | Daemon uptime, session count, DB schema version |
| `/search <query>` | FTS5 search, returns top 5 results |
| `/briefing <topic>` | Runs `briefing.py --compact` for the topic |
| `/recent` | Lists 10 most recent sessions |
| `/help` | Command list |

### Architecture constraint

This broker uses HTTP polling of `GET /conversations.history?oldest={ts}` at ~2 s intervals.
Slack Socket Mode (WebSocket) would reduce latency to <500 ms but requires the `slack_bolt`
PyPI package (non-stdlib). The Slack Events API (webhook) requires an inbound port (violates
the "no inbound port" architecture constraint). HTTP polling is the only stdlib-compatible
option without an inbound port.

### Blockers / unverified acceptance criteria (#72)

> 1. **Credentials** — the three env vars above must be set.
> 2. **Maintainer RTT benchmark** — median + p95 latency over ≥100 messages on a FPT network
>    has not been measured.
> 3. **Latency decision** — ~2 s HTTP-polling may be acceptable; if not, the `slack_bolt`
>    package must be adopted (non-stdlib dependency decision for maintainer).

Until all three are resolved, issue #72 should remain **open**.

---

### CORS and auth on the operator host

**Verified fact:** The operator host implements explicit cross-origin support in `browse/core/auth.py` and `browse/api/operator.py`:

- `Access-Control-Allow-Origin` allowlist: only the configured Firebase domain origin is permitted
- `Access-Control-Allow-Credentials: true` for cookie-based flows
- Preflight (`OPTIONS`) responses on all `/api/operator/*` routes
- Bearer token auth (`Authorization: Bearer <token>`) as the cross-origin authentication mechanism
- `GET /api/operator/capabilities` endpoint so the static UI can discover what the connected operator host supports

The operator console is fully functional across origins when a host profile is configured in the UI pointing to the operator's tunnel URL and Bearer token.

### Host profiles

The static UI uses **host profiles** — named, user-configurable entries storing the operator tunnel URL, Bearer token, optional label, and CLI kind — to target API calls. Profiles are stored in `localStorage` by `browse-ui/src/lib/host-profiles.ts` and exposed browse-wide via `HostProvider`.

**To configure a host profile from the UI:**

1. Open the browse UI and navigate to **Settings → Hosts & connections** (`/settings#hosts`).
2. Click **Add host** and enter the public tunnel URL (e.g. `https://abc123.ngrok.io`), an optional label, and the Bearer auth token.
3. Optionally mark the profile as **default** (⭐) so it is selected automatically on fresh load.
4. The header's global host dropdown immediately reflects the new profile. Any page that calls `useHostState()` — including the operator console's session create dialog — updates without a reload.

**Restore local (same-origin) behavior:** From Settings → Hosts & connections, click **Restore local**. This clears all `is_default` flags and the explicit selection, falling back to the `LOCAL_HOST` sentinel.

**Active host resolution order** (see `getEffectiveHost()` in `host-profiles.ts`):
1. Explicit selection stored in `localStorage` (`browse_selected_host_id`), if the profile still exists.
2. First saved remote profile with `is_default === true`.
3. `LOCAL_HOST` — same-origin, no bearer token required.

> The old `localStorage.setItem("hostProfile", …)` console snippet is no longer the intended path — the Settings host management UI ships in this codebase and handles all CRUD operations.

### Future CLI families (Claude Code, etc.)

The Firebase-hosted control plane is intentionally CLI-agnostic. The `browse/core/operator_console.py` backend currently launches only Copilot CLI. Supporting Claude Code or other CLI families requires:

- A pluggable provider interface in `operator_console.py`
- A CLI-selector in the UI operator console
- Per-CLI session schema normalisation

This is architecture intent, documented here for future contributors. No CLI family other than Copilot CLI is implemented.

### Manual steps still required in external consoles

| Step | Where |
|------|-------|
| Create Firebase project and verify custom domain | Firebase console → Hosting → Custom domains |
| Add DNS records provided by Firebase | DNS registrar or Cloudflare DNS dashboard |
| (Optional) Create separate `agents` site if project has multiple sites | Firebase console → Hosting → Add another site |
| Cloudflare Access policy on the operator tunnel hostname | Cloudflare Zero Trust → Access → Applications |



## Trend Scout Research Pack

The `--research-pack` flag writes a structured JSON artifact with per-repo analysis fields
that go beyond the GitHub issue body: novelty signals, risk signals, recommended follow-ups,
and a tentacle-handoff hint.

```bash
# Combine with --search-only --dry-run for a safe local preview (no network writes)
sk scout run --search-only --dry-run --research-pack

# Combine with --explain for full explainability coverage
sk scout run --search-only --dry-run --research-pack --explain

# Full pipeline run with research pack written after issue creation
sk scout run --research-pack

# Custom output path
sk scout run --research-pack --research-pack-output my-pack.json
# fallback: python3 ~/.copilot/tools/trend-scout.py [flags]
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
sk retro

# Repo-only (safe in CI; no local DB needed)
sk retro --mode repo

# JSON payload (stable contract consumed by the browse API)
sk retro --json

# Single score line
sk retro --score

# One section only: knowledge | skills | hooks | git | behavior (local mode)
sk retro --subreport knowledge
# fallback: python3 ~/.copilot/tools/retro.py [flags]
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
sk benchmark record

# Inspect recent snapshots
sk benchmark list --limit 5

# Compare two commits or snapshot IDs
sk benchmark compare --commits <older> <newer>
# fallback: python3 ~/.copilot/tools/benchmark.py [record|list|compare] [flags]
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
sk retro --subreport behavior
```

**Skills subscore — verification evidence discipline:**

When skill outcomes exist but no `tentacle_verifications` rows are recorded, the skills
subscore uses **30.0 (sub-neutral)** to reflect the unverified state.  The
`skills_unverified` distortion flag is set and `toward_100` lists
`no_verification_evidence` as the barrier.

To raise the skills subscore above sub-neutral: complete tentacles with an explicit
verification step so that `tentacle_verifications` rows are populated:

```bash
sk tentacle verify <name> "python3 test_fixes.py" --label "tests"
# Or in one step with complete --auto-verify (Wave 3; fail-open):
sk tentacle complete <name> --auto-verify "python3 test_fixes.py"
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
| `knowledge.embed_pct` | Below target | Run `sk index embed` to populate embeddings |
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

---

## Orchestrator-only next steps — hosted loopback compatibility goal

> **Interpretation / Action / Verification evidence layer** (see Rule 7 in AGENT-RULES.md).
>
> The steps below are the **final goal-eval checklist** for the hosted loopback compatibility goal
> (`345fa7cb-fb2c-4125-9d26-a4492234beea`). Each item must be completed and evidence recorded
> before the goal can be closed.

### Verification evidence already produced (targeted)

| Check | Status |
|-------|--------|
| `pnpm vitest run src/app/settings/page.test.tsx` | ✅ Passed (targeted — reported by browse-host-ui tentacle) |
| `pnpm vitest run src/app/chat/chat-shell.test.tsx` | ✅ Passed (targeted) |
| `pnpm vitest run src/app/insights/layout.test.tsx` | ✅ Passed (targeted) |
| `pnpm typecheck` | ✅ Passed |
| `pnpm exec playwright test e2e/chat.spec.ts --grep "header host switcher"` | ✅ Passed (targeted Playwright) |
| `python3 tests/test_hooks.py` | ✅ Passed (Python tooling regression) |
| `python3 tests/test_auto_update_coverage.py` | ✅ Passed |
| `python3 tests/test_sync_status.py` | ✅ Passed |
| `python3 test_fixes.py` | ✅ Passed |
| `pnpm vitest run src/lib/hosts/local-bootstrap.test.ts` | ✅ Passed (loopback probe, issue #49) |
| `pnpm vitest run src/providers/host-provider.test.tsx` | ✅ Passed (loopback probe + negative cache, issue #49) |
| `pnpm vitest run src/components/hosts/host-management.test.tsx` | ✅ Passed (PNA note, issue #49) |
| `pnpm vitest run src/app/insights/knowledge-tab.test.tsx` | ✅ Passed (capabilityState, issue #47) |

### Goal-eval checklist — orchestrator actions required

**Phase 1 — Python + UI unit tests:**
```bash
python3 run_all_tests.py                                          # Full Python test suite
cd browse-ui && pnpm test                                        # Full vitest suite
```

**Phase 2 — TypeScript and lint gates:**
```bash
cd browse-ui && pnpm typecheck
cd browse-ui && pnpm lint
cd browse-ui && pnpm format:check
```

**Phase 3 — Build and release artifact:**
```bash
cd browse-ui && pnpm build                                        # Local dist/
cd browse-ui && pnpm release:check                               # Firebase release artifact
```

**Phase 4 — Playwright / browser smoke:**
```bash
# Behavioral E2E suite (mocked backends — no live backend needed):
cd browse-ui && pnpm test:e2e --project behavioral

# Hosted smoke — Chromium/Edge (requires browse.py + live hosted URL):
# 1. python3 browse.py --hosted-bootstrap --token <token>
# 2. Open https://agents.linhngo.dev in Chromium/Edge
# 3. Assert: "Local backend (auto-detected)" appears in Settings → Hosts & connections
# 4. Assert: DevTools → Network shows /.well-known/browse-host → 200, PNA headers present
# 5. Assert: diagnostics data loads on Insights page
# 6. Assert: header host dropdown shows auto-detected host label

# Hosted smoke — Safari/Firefox:
# 1. Open https://agents.linhngo.dev in Safari or Firefox
# 2. Observe whether direct loopback auto-detection succeeds or is blocked
# 3. If blocked, add a host manually via Settings → Hosts & connections with an HTTPS tunnel URL
# 4. Assert: manual host activates and diagnostics load
```

**Phase 5 — Discovery endpoint live verification:**
```bash
# Requires browse.py running:
curl -s http://127.0.0.1:8765/.well-known/browse-host | python3 -m json.tool
# Assert: schema == "browse-host/1", status == "ok"
```

**Phase 6 — Deploy and hosted network sweep:**
```bash
# From private hosting repo:
firebase deploy --only hosting:agents

# Post-deploy verification from browser DevTools:
# - https://agents.linhngo.dev loads (HTTP 200)
# - https://agents-linhngo-dev.web.app loads (HTTP 200)
# - No /v2/_next/ 404s in Network tab
# - /.well-known/browse-host probe visible in DevTools when browse.py is running locally
```

**Phase 7 — Issue comments and closures:**
```bash
# Issue #46 (capability gates — legacy fallback):
gh issue comment 46 --repo magicpro97/copilot-session-knowledge \
  --body "Fixed: useHostFeature now applies LEGACY_CORE_FEATURES fallback for backends without protocol:v2 marker. Modern backends with protocol:\"v2\" have supported_features trusted exactly. Documented in docs/HOSTED-SHELL-ARCHITECTURE.md §4.6."
gh issue close 46 --repo magicpro97/copilot-session-knowledge

# Issue #47 (Insights child tabs):
gh issue comment 47 --repo magicpro97/copilot-session-knowledge \
  --body "Fixed: capabilityState is now threaded from insights layout.tsx to all child tabs. Vitest coverage added for all four states (no-host, checking, unsupported, ready) in knowledge-tab, live-tab, retro-tab, search-quality-tab. Documented in docs/HOSTED-SHELL-ARCHITECTURE.md §4.7."
gh issue close 47 --repo magicpro97/copilot-session-knowledge

# Issue #49 (loopback bootstrap):
gh issue comment 49 --repo magicpro97/copilot-session-knowledge \
  --body "Implemented: /.well-known/browse-host discovery endpoint, --hosted-bootstrap flag, PNA preflight headers, frontend probe (127.0.0.1 first, then localhost), 5-min negative cache, manual-token state. Documented in docs/HOSTED-SHELL-ARCHITECTURE.md §4 and docs/OPERATOR-PLAYBOOK.md."
gh issue close 49 --repo magicpro97/copilot-session-knowledge

# Issue #50 (Trend Scout / LocalKinAI/kincode) — leave open, add label:
gh issue comment 50 --repo magicpro97/copilot-session-knowledge \
  --body "Triaged: this is a Trend Scout research issue for LocalKinAI/kincode, not a runtime regression. Separating from the hosted loopback compatibility goal (#46, #47, #49). Leaving open for future follow-up research (MCP surface, frontmatter indexing, Claude Code session patterns). Relevant ideas: spawn a research tentacle on kincode to evaluate MCP tool-server surface for briefing.py integration."
```

Until all phases are verified with evidence, the goal status should be read as
"targeted checks passed; full orchestrator gates pending".

---

## Orchestrator-only next steps — host-management wave (previous wave, archived)

> Previous wave (#35–#45) was deployed at build `2184c36`. The steps below are the original
> orchestrator checklist, kept for reference. Do **not** re-open this scope.

**Verification evidence (targeted, from previous wave):**

| Check | Status |
|-------|--------|
| `pnpm vitest run src/app/settings/page.test.tsx` | ✅ Passed (targeted — reported by browse-host-ui tentacle) |
| `pnpm vitest run src/app/chat/chat-shell.test.tsx` | ✅ Passed (targeted) |
| `pnpm vitest run src/app/insights/layout.test.tsx` | ✅ Passed (targeted) |
| `pnpm typecheck` | ✅ Passed |
| `pnpm exec playwright test e2e/chat.spec.ts --grep "header host switcher"` | ✅ Passed (targeted Playwright) |
| `python3 tests/test_hooks.py` | ✅ Passed (Python tooling regression) |
| `python3 tests/test_auto_update_coverage.py` | ✅ Passed |
| `python3 tests/test_sync_status.py` | ✅ Passed |
| `python3 test_fixes.py` | ✅ Passed |

**Previous orchestrator actions (completed at `2184c36`):**

- [x] `cd browse-ui && pnpm lint`
- [x] `cd browse-ui && pnpm format:check`
- [x] `cd browse-ui && pnpm test`
- [x] `cd browse-ui && pnpm build`
- [x] `pnpm release:check` (from `browse-ui/`)
- [x] `python3 run_all_tests.py`
- [x] `git commit` + `git push`
- [x] Firebase deploy (from private hosting repo)
- [x] Hosted smoke (host dropdown, host management, session create dialog)
