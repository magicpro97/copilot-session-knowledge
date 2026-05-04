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

## Browse UI — Global Host Selection

Browse-wide host state is managed by `HostProvider` (root layout context) and persisted by `host-profiles.ts` (localStorage). All pages read the active host from `useHostState()` — there is no per-page host state.

### Quick reference

| Action | Where |
|--------|-------|
| Switch active host | Header → global host dropdown (AWS-region-style compact selector) |
| Add / remove / set-default / restore-local | Settings → **Hosts & connections** (`/v2/settings#hosts`) |
| Verify active host in code | `useHostState().host` — resolves via `getEffectiveHost()` |

### Same-tab refresh

All profile mutations and selection changes dispatch `browse:host-change` on `window`. `HostProvider` listens and re-evaluates immediately. No page reload is needed after switching hosts or saving/deleting a profile.

### Session create dialog pre-population

When `SessionCreateDialog` opens (`/v2/chat → New Chat`), it reads the global active host via `useHostState()` and pre-fills the host picker. The user may still override the host for that session; the override is local to that dialog open.

### Diagnostics enabled gate

`diagnosticsEnabled` (from `useHostState()`) is `true` when any of the following holds:
- A remote host with a non-empty `base_url` is active, **or**
- The current pathname starts with `/v2` (same-origin Python browse server), **or**
- `NEXT_PUBLIC_API_BASE` is set at build time.

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

This is a code-level fix required in `browse/core/auth.py`: the check must accept `https://` origins when `X-Forwarded-Proto: https` is present, or accept both schemes for the configured hostname. **This fix is not in the scope of this playbook entry.** Until it is applied, the operator console (`/v2/chat`) is read-browseable behind the tunnel but prompt submission will fail. Open a fix tentacle or issue targeting `browse/core/auth.py`.

#### Cookie `Secure` flag

**Verified from source code:** The `browse_token` cookie is issued without the `Secure` attribute. Behind HTTPS (Cloudflare Tunnel), browsers accept and return the cookie correctly — the `Secure` flag would only be required for `SameSite=None` cookies, not for `SameSite=Strict`. All remote browse traffic goes through HTTPS, so this does not block functionality. It is a hardening gap: a future change to add `Secure` when serving behind HTTPS is recommended.

#### Same-origin assumption in the UI (Cloudflare Tunnel deployment)

The Next.js static export makes all API calls to relative paths (`/api/*`) on the same origin. This assumption holds behind Cloudflare Tunnel: both the static UI and the Python API are served from the same origin. No cross-origin configuration is needed for this deployment mode.

**Firebase Hosting deployment changes this assumption.** When the static UI is served from a Firebase custom domain while the API lives at the operator's tunnel URL, all API calls become cross-origin. The operator host implements an explicit CORS allowlist, Bearer auth, and a capabilities endpoint. See [Firebase-hosted control plane](#firebase-hosted-control-plane) below.

### Mobile access

**Verified fact (same-origin / Cloudflare Tunnel deployment):** The browse server binds to `127.0.0.1`; direct LAN access from a mobile device is not possible. Via Cloudflare Tunnel, mobile browsers reach the app over HTTPS at the configured subdomain.

| Feature | Mobile status |
|---------|--------------|
| All `/v2/*` page routes | ✅ Work in iOS Safari and Android Chrome — Next.js static export, no SSR |
| Token auth (first-load `?token=…`) | ✅ Works — cookie is stored in browser session storage per architecture notes |
| SSE streaming (`/v2/chat` live transcript) | ✅ Works — iOS Safari 13+ and Android Chrome support `EventSource` |
| POST mutations (prompt submit) | ⚠️ Requires `check_origin` fix in `browse/core/auth.py` to accept `https://` origins (see [Known blocker](#known-blocker-origin-check-for-post-requests-code-level-issue) above) |
| Keyboard shortcuts (`g c`, `g s`, etc.) | ⚠️ Not accessible without a physical keyboard |

**[guidance]** To verify mobile access: open `https://browse.example.com/?token=<token>` on iOS Safari or Android Chrome (substitute your operator tunnel hostname). The sessions list and search pages should load. The operator console page loads, but prompt submission requires the Origin fix to be applied first.

---

## Firebase-hosted control plane

> **Facts vs guidance separator:** Verified facts are derived from config inspection and code review. Architecture notes marked **[guidance]** describe intended or recommended work.

**Verified fact:** `firebase.json` and `.firebaserc` are committed to the repo. `firebase.json` defines a hosting target named `agents` serving `browse-ui/dist/`. `.firebaserc` contains a placeholder project ID (`your-project-id`) — operators configure the real project ID and custom domain in a **private hosting repo** (see [external hosting-repo pattern](#external-hosting-repo-pattern) below).

### Topology

```
                ┌──────────────────────────────────┐
  browser ─────▶│  Firebase Hosting (static)        │
                │  <your-firebase-domain>            │
                │  browse-ui/dist (HTML/JS/CSS)      │
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

**Verified from source:** `browse-ui/next.config.ts` now reads `basePath` from `NEXT_BASE_PATH` and defaults to `"/v2"`. The Python browse server strips the `/v2/` prefix and maps requests to `browse-ui/dist/`. This remains the correct same-origin deployment mode.

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

- Builds the release artifact into `browse-ui/dist-release/` without touching the committed `browse-ui/dist/`.
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

**Verified repro (2026-05-03):** `https://agents-linhngo-dev.web.app/chat/` returned HTML with `/v2/_next/static/…` URLs. Requests to `/v2/_next/…` returned 404; requests to `/_next/…` returned 200. Root cause: the build included `basePath: "/v2"` in `next.config.ts`.

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

1. Open the browse UI and navigate to **Settings → Hosts & connections** (`/v2/settings#hosts`).
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

---

## Orchestrator-only next steps — host-management wave

> **Interpretation / Action / Verification evidence layer** (see Rule 7 in AGENT-RULES.md).
>
> The docs lane (`browse-docs-verification` tentacle) documents what is shipped.
> The steps below are **not yet done** and must be completed by the orchestrator before the wave can be considered released.

**Verification evidence already produced (targeted):**

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

**Orchestrator actions required before release:**

- [ ] `cd browse-ui && pnpm lint` — full lint pass on the browse-ui surface
- [ ] `cd browse-ui && pnpm format:check` — Prettier format check
- [ ] `cd browse-ui && pnpm test` — full vitest suite (all spec files)
- [ ] `cd browse-ui && pnpm build` — production build; rebuild `dist/` and stage the artifact
- [ ] `pnpm release:check` (from `browse-ui/`) — Firebase-targeted release artifact verification
- [ ] `python3 run_all_tests.py` — full Python test suite
- [ ] `git commit` with complete `dist/` update and a descriptive message
- [ ] `git push`
- [ ] Firebase deploy from private hosting repo (for Firebase-hosted deployments)
- [ ] Hosted smoke: open `https://<your-firebase-domain>/chat/` and verify header host dropdown, Settings → Hosts & connections card, and session create dialog host pre-population

Until these steps are completed, the verification status should be read as "targeted checks passed; full gates pending".

