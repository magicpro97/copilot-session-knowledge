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
| `/v2/settings` | Preferences |

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

The `dist/` directory is **committed to git** and served directly by `browse/routes/serve_v2.py`.

Do **not** edit files in `dist/` directly — they are build artifacts. Run `pnpm build` instead.

## Architecture notes

- **Same-origin deployment** (default): API calls go to `/api/*` on the same origin (Python browse server behind Cloudflare Tunnel). No CORS configuration needed. This is the currently implemented and tested path.
- **Firebase Hosting deployment** (static UI on a Firebase custom domain, API at the operator's tunnel URL): All API calls become cross-origin. The operator host exposes a CORS allowlist, Bearer auth, and a capabilities endpoint. See [Firebase Hosting topology](#firebase-hosting-topology) below.
- Auth token is injected via URL param `?token=…` on first load, then stored in `sessionStorage`
- `output: "export"` in next.config.ts means no SSR — all pages are static HTML + client JS
- `basePath: "/v2"` in `next.config.ts` is required for the Python-server deployment; a Firebase-targeted build must remove this basePath so that asset paths (`/_next/…`) resolve correctly at the Firebase origin. See [Firebase Hosting topology](#firebase-hosting-topology) for the required build step.
- Dynamic routes require `generateStaticParams()` in a server component wrapper

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

## Firebase Hosting topology

`firebase.json` and `.firebaserc` (repo root) provide a **template** for deploying the static browse-ui to [Firebase Hosting](https://firebase.google.com/docs/hosting). The `.firebaserc` uses a placeholder project ID (`your-project-id`) — production deployments should live in a private hosting repo where the real project ID and custom domain are configured. See the [external hosting-repo pattern](#external-hosting-repo-pattern) below.

### Topology diagram

```
                     ┌─────────────────────────────────┐
                     │  Firebase Hosting (static)       │
  browser ──HTTPS──▶ │  <your-firebase-domain>          │
                     │  browse-ui/dist  (HTML/JS/CSS)   │
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
| Static pages (sessions, search, insights, graph, settings) | ✅ Serve correctly once the basePath build step is done |
| Cross-origin API: CORS allowlist + Bearer auth + capabilities endpoint | ✅ Implemented on the operator host |

### External hosting-repo pattern

Actual production deployments should **not** be made from this open-source repo. The recommended pattern is:

1. Create a private hosting repo (e.g. `my-org/copilot-ui-hosting`).
2. Copy or symlink `firebase.json` and create a `.firebaserc` with your real Firebase project ID and custom domain.
3. Run `pnpm build` in `browse-ui/` (with `basePath` removed — see below), copy `dist/` to the hosting repo, and deploy from there.
4. Keep this open-source repo's `.firebaserc` as a generic template only.

This separation ensures no personal project IDs or custom domains are committed to the public repo.

### Known build constraint: `basePath: "/v2"`

The current `next.config.ts` sets `basePath: "/v2"`, which causes all asset URLs in the generated HTML to reference `/v2/_next/…`. The Python browse server strips the `/v2/` prefix before serving — this works correctly for the same-origin deployment.

**Firebase Hosting serves files from the root of `browse-ui/dist/`**, so `/v2/_next/…` references will 404 unless the basePath is removed before building. Before deploying to Firebase:

1. Temporarily remove or comment out `basePath: "/v2"` in `browse-ui/next.config.ts`
2. Run `pnpm build` to produce a Firebase-compatible `dist/`
3. Run `pnpm deploy` (or `firebase deploy --only hosting:agents`)
4. Restore `basePath: "/v2"` for the Python-server build

A future improvement is to make `basePath` conditional on an environment variable in `next.config.ts` to automate this (outside the current scope).

### Deploying

Requires [`firebase-tools`](https://firebase.google.com/docs/cli) installed globally (`npm install -g firebase-tools`).

```bash
# Authenticate (one-time)
firebase login

# Deploy static dist to agents hosting target
cd /path/to/copilot-session-knowledge
pnpm --dir browse-ui build   # must be a basePath-free build (see above)
firebase deploy --only hosting:agents
# or:
cd browse-ui && pnpm deploy
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
