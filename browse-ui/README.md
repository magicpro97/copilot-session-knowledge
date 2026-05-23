# browse-ui

Next.js 16 frontend for the Hindsight local web UI. Serves as both the local browse server's authenticated app and the Firebase-hosted static control plane.

## Stack

| Layer | Library |
|-------|---------|
| Framework | Next.js 16 (static export) |
| UI | shadcn/ui + Tailwind v4 |
| Charts | Recharts |
| State | TanStack Query v5 |
| Tables | TanStack Table v8 |
| Forms | React Hook Form + Zod |
| Icons | Lucide React |
| Themes | next-themes |

## Development

```bash
# Install deps
pnpm install

# Dev server (proxies API to localhost:8765)
pnpm dev

# Build and run the Python-backed local web app
node scripts/run-local.mjs -- --port 8792 --token localtest --no-tunnel

# Type check
pnpm typecheck

# Unit tests
pnpm test

# Build (output → dist/)
pnpm build
```

## Routes

| Path | Description |
|------|-------------|
| `/chat` | Operator console — run Copilot CLI prompts, review touched files, and inspect inline diffs |
| `/sessions` | Session list |
| `/sessions/[id]` | Session detail (real UUID paths) + Overview / Timeline / Mindmap / Checkpoints / **Debug Log** tabs |
| `/search` | Full-text + semantic search |
| `/insights` | Knowledge insights |
| `/graph` | Graph workspace: Evidence + Similarity + Communities |
| `/settings` | Preferences + **Hosts & connections** (host management) |

> **Debug Log tab** (`/sessions/[id]`, 5th tab): displays debug events for the most recent
> operator run — event list table with timestamp/kind/source/duration/status columns, filter
> toolbar (text, kind, level, status), detail drawer with full field view, and three view
> modes (toggled when span data is present): **List** (flat table), **Tree** (collapsible
> parent/child hierarchy), and **Flow** (VS Code Agent Debug-style visual event hierarchy
> built from `span_id`/`parent_span_id`, with node-click detail panel, pan/zoom canvas, and
> a 100-event pagination limit).

> **Deployment prefix:** Canonical routes are root-relative on both the local Python browse server
> and the Firebase-hosted deployment. Compatibility redirects from `/v2/*` → `/*` remain for old
> bookmarks and deep links.

## E2E tests

Playwright specs live in `e2e/`:

| Spec | Coverage |
|------|----------|
| `smoke.spec.ts` | Core route rendering, session detail, diff viewer, insights panels |
| `shortcuts.spec.ts` | Global keyboard shortcuts and navigation chords |
| `chat.spec.ts` | `/chat` operator console shell, history, file preview, and inline diff review |
| `visual.spec.ts` | Screenshot comparisons for stable visual surfaces |
| `hosted-regression.spec.ts` | Hosted-origin regression proof with source-based console classification and scrubbed artifacts (gate: `HOSTED_PROOF=1`) |

Typical local runs:

```bash
pnpm test:e2e --project behavioral
pnpm test:e2e --project visual
```

`playwright.config.ts` builds the static export, creates the fixture DB, and boots the Python browse server automatically for the suite. The `behavioral` project is the stable day-to-day smoke surface; `visual` remains manual-dispatch CI only.


### Hosted regression proof (#519)

Requires `HOSTED_PROOF=1`. Does **not** start the local webServer. Targets `https://agents.linhngo.dev` (or `HOSTED_URL` override). No route stubs are registered; real network behavior is captured as JSON artifacts.

```bash
cd browse-ui

# Dry-run / list tests (no real session needed)
HOSTED_PROOF=1 HOSTED_URL=https://agents.linhngo.dev CLI_SESSION_ID=dummy \
  pnpm exec playwright test --project hosted-regression --list

# Full run with real session
pnpm exec playwright install chromium
HOSTED_PROOF=1 \
  HOSTED_URL=https://agents.linhngo.dev \
  CLI_SESSION_ID=<real-session-id> \
  pnpm exec playwright test --project hosted-regression --reporter=line,html --trace on

# Strict mode: assert empty artifacts after fixes land
HOSTED_PROOF=1 HOSTED_PROOF_STRICT=1 \
  HOSTED_URL=https://agents.linhngo.dev \
  CLI_SESSION_ID=<real-session-id> \
  pnpm exec playwright test --project hosted-regression
```

Artifacts written per test: `loopback-hits.json`, `runs-404.json`, `app-console-errors.json`, `extension-noise.json`, `browser-internal.json`, and `console-raw.json`.

> **Chrome Local Network Access (LNA) permission — hosted proof only:** Modern Chromium (M123+) requires a `local-network-access` Web permission before a hosted HTTPS page may probe a loopback address, even when the backend supplies correct `Access-Control-Allow-Private-Network: true` headers. The hosted regression proof grants this permission programmatically via `context.grantPermissions(["local-network-access"], { origin })`, scoped strictly to the `HOSTED_URL` origin. No unsafe browser flags (`--disable-web-security`, `--disable-features`) are used.

## Build output

`pnpm build` runs `next build` (static export) then `scripts/post-build.mjs` which writes
`dist/version.json`.
`pnpm build:release` writes the isolated Firebase artifact to `dist-release/version.json`.

The `dist/` directory is served directly by `browse/routes/serve_v2.py` for the local root app.
`dist/` is generated on demand and ignored by git; use `node scripts/run-local.mjs -- --port
8792 --token localtest --no-tunnel` to rebuild it and launch `browse.py` in one command.
`dist-release/` exists so release verification can build a separate Firebase artifact without
touching the local `dist/`.

Do **not** edit files in `dist/` directly — they are build artifacts. Run `pnpm build` instead.

## Architecture notes

- **Same-origin deployment** (default): API calls go to `/api/*` on the same origin (Python browse server behind Cloudflare Tunnel). No CORS configuration needed. This is the currently implemented and tested path.
- **Firebase Hosting deployment** (static UI on a Firebase custom domain, API at the operator's tunnel URL): All API calls become cross-origin. The operator host exposes a CORS allowlist, Bearer auth, and a capabilities endpoint. See [Firebase Hosting topology](#firebase-hosting-topology) below.
- HTTPS proxy note: if browse is behind Cloudflare Tunnel, ngrok, or another HTTPS reverse proxy, set `BROWSE_TRUSTED_PROXY=1` (or `true` / `yes`) on the operator host so forwarded HTTPS is trusted and auth cookies keep the `Secure` flag.
- Cross-origin streaming avoids `?token=` leakage: `/api/operator/*` and `/api/live` use fetch-based SSE with `Authorization: Bearer ...` for remote hosts, while same-origin/local surfaces keep `EventSource`.
- Auth token is injected via URL param `?token=…` on first load, then stored in `sessionStorage`
- `output: "export"` in next.config.ts means no SSR — all pages are static HTML + client JS
- `basePath` in `next.config.ts` defaults to `""`, so `pnpm build` produces a root-relative
  artifact for both the local browse server and Firebase Hosting. `pnpm build:release` still
  emits `dist-release/` so release verification can stay isolated from the local `dist/`
  artifact. See [Firebase Hosting topology](#firebase-hosting-topology) for the release build
  step.
- Dynamic routes require `generateStaticParams()` in a server component wrapper

## Host selection & management

Browse-wide host state is shared by all pages via `HostProvider` (mounted at the root layout) and `host-profiles.ts` (localStorage persistence layer). This replaces any previous per-page or per-component host state.

### Source of truth

| Source | Role |
|--------|------|
| `src/providers/host-provider.tsx` | Root context provider; exposes `host` and `diagnosticsEnabled` to all pages |
| `src/lib/host-profiles.ts` | Read/write localStorage helpers; the `LOCAL_HOST` sentinel; same-tab change notification via `BROWSE_HOST_CHANGE_EVENT` |

### Active host resolution order

1. Explicit selection stored in `localStorage` (`browse_selected_host_id`), if the referenced profile still exists.
2. First saved remote profile with `is_default === true`.
3. `LOCAL_HOST` sentinel (same-origin, no bearer token required).

### Same-tab refresh

Both profile mutations (save, delete) and host selection changes dispatch `BROWSE_HOST_CHANGE_EVENT` on `window`. `HostProvider` listens for this event and re-evaluates immediately — no page reload or navigation needed. Cross-tab changes propagate via the standard `storage` event.

### Header global host dropdown

The header renders a compact AWS-region-style dropdown showing the active host label. Clicking it
lists all saved profiles plus `Local (same-origin)`. Selecting a profile calls
`setSelectedHostId()` and triggers `BROWSE_HOST_CHANGE_EVENT`. A **Manage hosts…** link navigates
to `/settings#hosts`.

### Settings — Hosts & connections (`HostManagement`)

The Settings page at `/settings` contains a dedicated **Hosts & connections** card that renders `HostManagement`. From this surface the operator can:

- **List** all saved profiles plus the built-in `Local (same-origin)` entry (not deletable).
- **Add** a remote host — requires a public tunnel URL (e.g. ngrok, Cloudflare Tunnel); label, auth token, and CLI kind are optional.
  - **Browser-context validation**: clicking **Save host** probes the remote host's `/healthz` endpoint from the actual browser context (CORS, network, and auth are all exercised). The host is only saved and selected if the probe succeeds. Actionable error messages are shown for auth failures (401), CORS/allowlist issues (403), unreachable tunnels, and HTTP errors.
  - **Save anyway**: after a validation failure, an escape hatch lets the operator save the profile without a probe (useful when the host is intentionally restricted).
- **Switch** the active selection to any listed host.
- **Set default** — marks a profile `is_default: true` so it is selected on fresh load (before any explicit selection).
- **Remove** a remote profile; if the removed profile was active, the selection falls back through the resolution order above.
- **Restore local** — clears all `is_default` flags and removes any explicit selection, returning to the `LOCAL_HOST` sentinel.

#### Control-plane origin (Firebase-hosted deployment)

When the browser is not on `localhost` / `127.0.0.1`, the **Hosts & connections** card shows a **Control-plane origin** strip with the current `window.location.origin` and a copy button. This is the URL operators share to access the hosted control plane from another device — it is read dynamically from the runtime and is never hardcoded in the source.

### Session creation pre-population

`SessionCreateDialog` (`/chat`) reads the global active host from `useHostState()` and pre-populates the host picker when the dialog opens. The user can still override the host per session; the override is local to that dialog invocation.

### Verified (targeted checks)

- `pnpm vitest run src/components/hosts/host-management.test.tsx` — HostManagement validation, hosted-origin strip, save flow
- `pnpm vitest run src/app/settings/page.test.tsx` — Settings page + HostManagement rendering
- `pnpm vitest run src/app/chat/chat-shell.test.tsx` — ChatShell SessionCreateDialog pre-population
- `pnpm exec playwright test e2e/chat.spec.ts --grep "header host switcher"` — header dropdown E2E
- `python3 tests/test_browse_chat_resume.py` — CR1-CR14 mock-Copilot adopt/confirm/prompt/stream proof (Python-side)
- `pnpm typecheck` — TypeScript across the full browse-ui surface

> Full gates (lint, build, full E2E suite, deploy, hosted smoke) are orchestrator-owned and have not been run by this docs lane.

## Mobile support

The UI is a static Next.js export and renders in any modern mobile browser (iOS Safari, Android Chrome). Access requires a tunnel (e.g. Cloudflare Tunnel) since the browse server binds to `127.0.0.1`.

**Same-origin deployment (Cloudflare Tunnel at `<your-tunnel-host>`):**

| Surface | Mobile status |
|---------|--------------|
| Sessions, search, insights, graph, settings | ✅ Fully functional via browser |
| Operator console page load (`/chat`) | ✅ Page loads |
| SSE live transcript streaming | ✅ `EventSource` is supported on iOS Safari 13+ and Android Chrome |
| Prompt submission (POST) | ⚠️ Requires `check_origin` fix in `browse/core/auth.py` to accept `https://` origins — see [docs/OPERATOR-PLAYBOOK.md](../docs/OPERATOR-PLAYBOOK.md#remote-access-via-cloudflare-tunnel) |
| Keyboard shortcuts | ⚠️ Not usable without a physical keyboard |

**Firebase Hosting deployment (`<your-firebase-domain>`):**

| Surface | Status |
|---------|--------|
| All read-only pages | ✅ Fully static; works as soon as the UI is deployed |
| Operator console | ✅ Cross-origin API support is implemented — CORS allowlist, Bearer auth, and capabilities endpoint are in place on the operator host |
| Live knowledge feed | ✅ Remote `/api/live` streams use fetch-based SSE + Bearer auth, so tokens stay out of browser-visible URLs |

## Hosted Shell Specs

Design specifications for the hosted browse-UI operating as a remote shell:

| Topic | Document |
|---|---|
| Localhost bootstrap (mkcert/HTTPS, future design), relay/gateway, version negotiation | [docs/HOSTED-SHELL-ARCHITECTURE.md](../docs/HOSTED-SHELL-ARCHITECTURE.md) |
| **PNA/HTTP loopback bootstrap (`--hosted-bootstrap`), discovery endpoint, browser compat** | [docs/HOSTED-SHELL-ARCHITECTURE.md §4](../docs/HOSTED-SHELL-ARCHITECTURE.md#4-hosted-loopback-bootstrap--pnahttp-shipped-issue-49) |
| Browser token storage, stream reconnect, first-run UX | [docs/HOSTED-SHELL-RESEARCH.md](../docs/HOSTED-SHELL-RESEARCH.md) |
| Operator runbook: tunnels, Firebase deploy, CORS, hosted smoke | [docs/OPERATOR-PLAYBOOK.md](../docs/OPERATOR-PLAYBOOK.md) |

### Loopback backend auto-detection (issue #49)

When accessed from a hosted HTTPS origin (`https://agents.linhngo.dev` or the Firebase URL)
with no explicit remote host configured, the UI probes:
1. `http://127.0.0.1:8765/.well-known/browse-host`
2. `http://localhost:8765/.well-known/browse-host`

A detected backend is activated ephemerally. Probe results are cached (5-minute negative cache)
to avoid storms. Explicit remote-host selections are never overridden.

**To enable:** start the backend with `python3 browse.py --hosted-bootstrap [--token <token>]`.

**Browser caveat:** Chromium/Edge can require PNA/LNA preflight headers and a local-network
permission prompt. Safari/Firefox behavior depends on CORS/browser policy. If direct loopback is
blocked, use an HTTPS tunnel (Cloudflare Tunnel / ngrok); do not assume universal browser support.

## CLI Session Adoption UX

The `/chat` operator console supports resuming an existing Copilot CLI session from the browser
via the **From CLI history** flow.  See
[docs/OPERATOR-PLAYBOOK.md — Chat Resume / CLI Session Adoption](../docs/OPERATOR-PLAYBOOK.md#chat-resume--cli-session-adoption)
for the full operator runbook.

### Components

| Component | File | Role |
|-----------|------|------|
| `CliSessionPicker` | `src/components/chat/cli-session-picker.tsx` | Lists real CLI sessions from `/api/operator/cli-sessions`; drives the adopt API call |
| `CliAdoptedBadge` | `src/components/chat/cli-session-picker.tsx` | Badge rendered on sessions adopted from CLI history (`source = "cli_adopt"`) |
| `ConfirmAdoptionPanel` | `src/components/chat/cli-session-picker.tsx` / `chat-shell.tsx` | Workspace/add_dirs confirmation step; composer is disabled until `confirmed_at` is set |

### Two-ID model

- **Operator session ID** — the route key used in all `/api/operator/sessions/<id>/*` calls and
  `navigation.push`.  Never the CLI UUID.
- **CLI UUID** — stored backend-side in `resume_target`; passed to `copilot` as
  `--resume=<cli_uuid>`.  Never rendered as a route segment in the UI.

`chat-shell.tsx` navigates using the operator session ID only.

### Composer gate

The Composer component remains disabled (`disabled={!session.confirmed_at}`) until
`confirmed_at` is set by `POST /api/operator/sessions/{id}/confirm`.

## Phases

- **Phase 6**: Shipped scaffold — stub routes, providers, API client, and build pipeline
- **Phase 7**: Shipped sessions list + session detail + search pages with real data
- **Phase 8**: Shipped insights + graph pages (dashboard/live and Evidence/Similarity/Communities)
- **Phase 9**: Shipped settings, global keyboard shortcuts, and session detail compare/export polish
- **Phase 10**: Shipped operator console (`/chat`) — browser-managed Copilot CLI execution with streamed output, persisted run history, and file review
- **Phase 11**: Shipped browse-wide host selection (`HostProvider` + `host-profiles.ts`), global header host dropdown, Settings host management surface (`HostManagement`), and same-tab host-change refresh
- **Phase 12**: Unified root-served browse app — the Python browse server and Firebase-hosted
  build now share the same root-relative routes (`/*`); `/v2/*` compatibility redirects remain for
  old deep links.
- **Phase 13**: CLI Session Adoption UX — `CliSessionPicker`, `CliAdoptedBadge`,
  `ConfirmAdoptionPanel`; two-ID model (operator ID vs CLI UUID in `resume_target`); mock-Copilot
  E2E proof (CR1-CR14, `tests/test_browse_chat_resume.py`).

## Firebase Hosting topology

`firebase.json` and `.firebaserc` (repo root) provide a **template** for deploying the static browse-ui to [Firebase Hosting](https://firebase.google.com/docs/hosting). The `.firebaserc` uses a placeholder project ID (`your-project-id`) — production deployments should live in a private hosting repo where the real project ID and custom domain are configured. See the [external hosting-repo pattern](#external-hosting-repo-pattern) below.

### Topology diagram

```
                     ┌─────────────────────────────────┐
                     │  Firebase Hosting (static)       │
  browser ──HTTPS──▶ │  <your-firebase-domain>          │
                     │  browse-ui/dist-release          │
                     │  (HTML/JS/CSS)                  │
                     └──────────────┬──────────────────┘
                                    │  cross-origin API calls
                                    │  (operator URL set at runtime via host profile)
                                    ▼
                     ┌─────────────────────────────────┐
                     │  Cloudflare Tunnel               │
                     │  <your-tunnel-host>              │
                     │  ──▶ browse.py (127.0.0.1:PORT)  │
                     │      /api/operator/* (REST+SSE)  │
                     └─────────────────────────────────┘
```

### What is implemented

| Layer | Status |
|-------|--------|
| `firebase.json` + `.firebaserc` hosting config (template) | ✅ In repo |
| Firebase Hosting custom domain | 🔲 Manual console step in your private hosting repo (see below) |
| Static pages (sessions, search, insights, graph, settings) | ✅ Serve correctly once the `build:release` step is used |
| Cross-origin API: CORS allowlist + Bearer auth + capabilities endpoint | ✅ Implemented on the operator host |

### External hosting-repo pattern

Actual production deployments should **not** be made from this open-source repo. The recommended pattern is:

1. Create a private hosting repo (e.g. `my-org/copilot-ui-hosting`).
2. Copy or symlink `firebase.json` and create a `.firebaserc` with your real Firebase project ID and custom domain.
3. Run `pnpm release:check` in `browse-ui/`, copy `dist-release/` to the hosting repo, and deploy from there.
4. Keep this open-source repo's `.firebaserc` as a generic template only.

An external hosting repo can automate this flow end-to-end: check out `copilot-session-knowledge`, run the same release gate (`pnpm release:check`), sync `dist-release/` into its hosting target directory, and deploy from there. A push-triggered `repository_dispatch` hook from this repo is optional; a scheduled poller that compares the hosted `version.json.buildHash` against the latest browse-ui release commit is a viable fallback when cross-repo dispatch secrets are not available.

This separation ensures no personal project IDs or custom domains are committed to the public repo.

### Build modes: local root app vs isolated release artifact

`next.config.ts` defaults `basePath` to `""`, so `pnpm build` produces the root-relative `dist/`
artifact served by the Python browse server at `/*`.

Firebase Hosting also serves files from the site root, so the release artifact uses the same
root-relative `/_next/…` asset paths. Use the dedicated release artifact when you want a clean
build-and-verify step without touching `dist/`:

1. Run `pnpm release:check` to build and verify `dist-release/`
2. Copy that `dist-release/` into your private hosting repo
3. Confirm `pnpm release:check` passed
4. Run `firebase deploy --only hosting:agents` from your private hosting repo

`pnpm build` remains the default local build for the Python browse server.

### Release-gate check

Before every Firebase deploy, run the following to build and verify the root-hosted export:

```bash
# From browse-ui/:
pnpm release:check
```

This command:

- Builds the Firebase artifact into `dist-release/` without touching `dist/`
- Runs the proof test in isolation (it auto-selects the `[FIREBASE_PROOF]` case only)
- Reads `dist-release/chat/index.html` directly from the filesystem and asserts:

- No `/v2/_next/` references exist (these 404 on Firebase)
- At least one `/_next/` reference exists (sanity: the export is non-trivial)

The proof test is skipped in normal CI runs; `pnpm release:check` enables it explicitly for the
release gate without rebuilding the local `dist/` artifact.

### Post-deploy verification (`pnpm verify:deploy`)

After `firebase deploy` completes from the private hosting repo, run a **live** check against the
hosted origin to confirm the deploy is actually serving the new SHA and that the app-shell HTML
revalidates instead of being cached for an hour:

```bash
# From browse-ui/:
pnpm verify:deploy
# Optionally pin to a specific commit SHA you just deployed:
EXPECTED_SHA=$(git rev-parse --short HEAD) pnpm verify:deploy
# Or against a different origin:
pnpm verify:deploy --origin https://agents.example.com --expected-sha abc1234
```

It performs network calls against the hosted origin and asserts:

| Check | Why it matters |
|-------|----------------|
| `GET /version.json` is reachable and (optionally) `buildHash` starts with `--expected-sha` | Proves the hosted control plane is running the commit you just deployed |
| `/version.json` `Cache-Control` revalidates (`no-cache` / `no-store` / `max-age=0`) | Otherwise operators see a stale `buildHash` even after a fresh deploy. `must-revalidate` alone is **not** sufficient because it only forces revalidation after the response is stale. |
| `/`, `/chat/`, `/sessions/_verify`, `/search/`, `/insights/`, `/graph/`, `/settings/`, `/diagnostics/` HTML `Cache-Control` revalidate | Stops the "stale app-shell after deploy" failure mode where users keep pre-fix HTML referencing old hashed chunks for the full max-age. Probing one path per app-shell route prefix (including a rewrite/cleanUrls path under `/sessions/<id>`) catches cases where the HTML rule only covers `/` or `**/*.html`. |
| A sampled `/_next/static/**` chunk is `immutable` with a long `max-age` | Confirms the immutable-asset caching guarantee was preserved by the cache rules, including for hashed font/image assets under `/_next/static/media/**`. |

Exit code is non-zero (with an actionable message) on any failure. This command is intentionally
**not** wired into `release:check` so the build gate stays offline; run `verify:deploy` as a
post-deploy operator step.

### Deploying

Requires [`firebase-tools`](https://firebase.google.com/docs/cli) installed globally (`npm install -g firebase-tools`).

```bash
# Authenticate (one-time)
firebase login

# Full root-hosted release sequence:
cd /path/to/copilot-session-knowledge

# 1. Produce and verify the root-hosted release artifact:
pnpm --dir browse-ui release:check

# 2. Sync the release artifact into your private hosting repo:
rsync -a --delete browse-ui/dist-release/ /path/to/private-hosting-repo/agents-public/

# 3. Deploy from the private hosting repo:
cd /path/to/private-hosting-repo
firebase deploy --only hosting:agents
```

For production deployments, run these commands from your **private hosting repo** (see [external hosting-repo pattern](#external-hosting-repo-pattern) above).

### CORS and auth on the operator host

When the static UI is served from Firebase and the API is at the operator's tunnel URL (e.g. `<your-tunnel-host>`), all `/api/operator/*` calls are cross-origin. The operator host implements:

- Explicit CORS allowlist: `Access-Control-Allow-Origin` set to the Firebase domain origin
- `Access-Control-Allow-Credentials: true` for cookie-based flows
- Preflight (`OPTIONS`) responses for POST and SSE routes
- Bearer token auth (`Authorization: Bearer <token>`) as the cross-origin auth mechanism
- A `GET /api/operator/capabilities` endpoint so the UI can discover what the connected host supports

These are implemented in `browse/core/auth.py` and `browse/api/operator.py`. The operator console is fully functional across origins when the host profile is configured.

### Future-ready: other CLI families (Claude Code, etc.)

The Firebase-hosted static UI is designed as a CLI-agnostic control plane. The operator console currently launches Copilot CLI exclusively (via `browse/core/operator_console.py`). Supporting Claude Code or other CLI families requires:

1. A CLI-selection UI in the operator console
2. Additional `operator_console.py` backends or a pluggable provider interface
3. Host-profile configuration so the UI knows which tunnel URL to target for each operator machine

This is architecture intent, not shipped functionality. The docs and config in this repo establish the hosting foundation; the CLI backend extensions are future work.

### Manual Firebase/Cloudflare console steps required

The following steps cannot be automated from this repo and must be performed in your private hosting environment:

1. **Firebase console** — create a project and verify your custom domain under Hosting → Custom domains
2. **DNS registrar or Cloudflare DNS** — add the `A`/`CNAME` records that Firebase provides during custom-domain verification
3. **Firebase console** (optional) — if the project has multiple hosting sites, create a site named `agents` and update your private `.firebaserc` targets accordingly
4. **Cloudflare Access** (recommended) — add an Access policy on the operator tunnel URL to gate access before Bearer auth is exercised
