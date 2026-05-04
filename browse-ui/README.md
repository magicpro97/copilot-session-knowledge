# browse-ui

Next.js 16 frontend for the Hindsight local web UI, served at `/v2/` by the Python browse server.

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
| `/v2/chat` | Operator console — run Copilot CLI prompts, review touched files, and inspect inline diffs |
| `/v2/sessions` | Session list |
| `/v2/sessions/[id]` | Session detail (real UUID paths) + timeline/mindmap/checkpoints |
| `/v2/search` | Full-text + semantic search |
| `/v2/insights` | Knowledge insights |
| `/v2/graph` | Graph workspace: Evidence + Similarity + Communities |
| `/v2/settings` | Preferences + **Hosts & connections** (host management) |

## E2E tests

Playwright specs live in `e2e/`:

| Spec | Coverage |
|------|----------|
| `smoke.spec.ts` | Core route rendering, session detail, diff viewer, insights panels |
| `shortcuts.spec.ts` | Global keyboard shortcuts and navigation chords |
| `chat.spec.ts` | `/v2/chat` operator console shell, history, file preview, and inline diff review |
| `visual.spec.ts` | Screenshot comparisons for stable visual surfaces |

Typical local runs:

```bash
pnpm test:e2e --project behavioral
pnpm test:e2e --project visual
```

`playwright.config.ts` builds the static export, creates the fixture DB, and boots the Python browse server automatically for the suite. The `behavioral` project is the stable day-to-day smoke surface; `visual` remains manual-dispatch CI only.

## Build output

`pnpm build` runs `next build` (static export) then `scripts/post-build.mjs` which writes `dist/version.json`.
`pnpm build:release` writes the Firebase-safe root-hosted artifact to `dist-release/version.json`.

The `dist/` directory is **committed to git** and served directly by `browse/routes/serve_v2.py`.

Do **not** edit files in `dist/` directly — they are build artifacts. Run `pnpm build` instead.

## Architecture notes

- **Same-origin deployment** (default): API calls go to `/api/*` on the same origin (Python browse server behind Cloudflare Tunnel). No CORS configuration needed. This is the currently implemented and tested path.
- **Firebase Hosting deployment** (static UI on a Firebase custom domain, API at the operator's tunnel URL): All API calls become cross-origin. The operator host exposes a CORS allowlist, Bearer auth, and a capabilities endpoint. See [Firebase Hosting topology](#firebase-hosting-topology) below.
- Auth token is injected via URL param `?token=…` on first load, then stored in `sessionStorage`
- `output: "export"` in next.config.ts means no SSR — all pages are static HTML + client JS
- `basePath` in `next.config.ts` defaults to `/v2` for the Python-server deployment; use `pnpm build:release` for a Firebase-targeted export so asset paths resolve from the site root (`/_next/…`). See [Firebase Hosting topology](#firebase-hosting-topology) for the release build step.
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

The header renders a compact AWS-region-style dropdown showing the active host label. Clicking it lists all saved profiles plus `Local (same-origin)`. Selecting a profile calls `setSelectedHostId()` and triggers `BROWSE_HOST_CHANGE_EVENT`. A **Manage hosts…** link navigates to `/v2/settings#hosts` (safe under both the local `/v2` basePath and the Firebase root-hosted build).

### Settings — Hosts & connections (`HostManagement`)

The Settings page at `/v2/settings` contains a dedicated **Hosts & connections** card that renders `HostManagement`. From this surface the operator can:

- **List** all saved profiles plus the built-in `Local (same-origin)` entry (not deletable).
- **Add** a remote host — requires a public tunnel URL (e.g. ngrok, Cloudflare Tunnel); label, auth token, and CLI kind are optional.
- **Switch** the active selection to any listed host.
- **Set default** — marks a profile `is_default: true` so it is selected on fresh load (before any explicit selection).
- **Remove** a remote profile; if the removed profile was active, the selection falls back through the resolution order above.
- **Restore local** — clears all `is_default` flags and removes any explicit selection, returning to the `LOCAL_HOST` sentinel.

### Session creation pre-population

`SessionCreateDialog` (`/v2/chat`) reads the global active host from `useHostState()` and pre-populates the host picker when the dialog opens. The user can still override the host per session; the override is local to that dialog invocation.

### Verified (targeted checks)

- `pnpm vitest run src/app/settings/page.test.tsx` — Settings page + HostManagement rendering
- `pnpm vitest run src/app/chat/chat-shell.test.tsx` — ChatShell SessionCreateDialog pre-population
- `pnpm exec playwright test e2e/chat.spec.ts --grep "header host switcher"` — header dropdown E2E
- `pnpm typecheck` — TypeScript across the full browse-ui surface

> Full gates (lint, build, full E2E suite, deploy, hosted smoke) are orchestrator-owned and have not been run by this docs lane.

## Mobile support

The UI is a static Next.js export and renders in any modern mobile browser (iOS Safari, Android Chrome). Access requires a tunnel (e.g. Cloudflare Tunnel) since the browse server binds to `127.0.0.1`.

**Same-origin deployment (Cloudflare Tunnel at `<your-tunnel-host>`):**

| Surface | Mobile status |
|---------|--------------|
| Sessions, search, insights, graph, settings | ✅ Fully functional via browser |
| Operator console page load (`/v2/chat`) | ✅ Page loads |
| SSE live transcript streaming | ✅ `EventSource` is supported on iOS Safari 13+ and Android Chrome |
| Prompt submission (POST) | ⚠️ Requires `check_origin` fix in `browse/core/auth.py` to accept `https://` origins — see [docs/OPERATOR-PLAYBOOK.md](../docs/OPERATOR-PLAYBOOK.md#remote-access-via-cloudflare-tunnel) |
| Keyboard shortcuts | ⚠️ Not usable without a physical keyboard |

**Firebase Hosting deployment (`<your-firebase-domain>`):**

| Surface | Status |
|---------|--------|
| All read-only pages | ✅ Fully static; works as soon as the UI is deployed |
| Operator console | ✅ Cross-origin API support is implemented — CORS allowlist, Bearer auth, and capabilities endpoint are in place on the operator host |

## Phases

- **Phase 6**: Shipped scaffold — stub routes, providers, API client, and build pipeline
- **Phase 7**: Shipped sessions list + session detail + search pages with real data
- **Phase 8**: Shipped insights + graph pages (dashboard/live and Evidence/Similarity/Communities)
- **Phase 9**: Shipped settings, global keyboard shortcuts, and session detail compare/export polish
- **Phase 10**: Shipped operator console (`/v2/chat`) — browser-managed Copilot CLI execution with streamed output, persisted run history, and file review
- **Phase 11**: Shipped browse-wide host selection (`HostProvider` + `host-profiles.ts`), global header host dropdown, Settings host management surface (`HostManagement`), and same-tab host-change refresh

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

This separation ensures no personal project IDs or custom domains are committed to the public repo.

### Build modes: local `/v2` vs root-hosted release

`next.config.ts` defaults `basePath` to `/v2`, which keeps the checked-in `dist/` artifact compatible with the Python browse server at `/v2/*`.

Firebase Hosting serves files from the site root, so the root-hosted release artifact must emit `/_next/…` asset URLs instead of `/v2/_next/…`. Use the dedicated release artifact:

1. Run `pnpm release:check` to build and verify `dist-release/`
2. Copy that `dist-release/` into your private hosting repo
3. Confirm `pnpm release:check` passed
4. Run `firebase deploy --only hosting:agents` from your private hosting repo

`pnpm build` remains the default local build and should be used whenever you want the checked-in `/v2` artifact for the Python browse server.

### Release-gate check

Before every Firebase deploy, run the following to build and verify the root-hosted export:

```bash
# From browse-ui/:
pnpm release:check
```

This command:

- Builds the Firebase artifact into `dist-release/` without touching the committed `dist/`
- Runs the proof test in isolation (it auto-selects the `[FIREBASE_PROOF]` case only)
- Reads `dist-release/chat/index.html` directly from the filesystem and asserts:

- No `/v2/_next/` references exist (these 404 on Firebase)
- At least one `/_next/` reference exists (sanity: the export is non-trivial)

The proof test is skipped in normal CI runs; `pnpm release:check` enables it explicitly for the release gate without rebuilding the regular `/v2` artifact.

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
