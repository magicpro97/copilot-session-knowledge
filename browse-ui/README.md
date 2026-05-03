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

- API calls go to `/api/*` on the same origin (Python browse server)
- Auth token is injected via URL param `?token=…` on first load, then stored in `sessionStorage`
- `output: "export"` in next.config.ts means no SSR — all pages are static HTML + client JS
- Dynamic routes require `generateStaticParams()` in a server component wrapper

## Mobile support

The UI is a static Next.js export and renders in any modern mobile browser (iOS Safari, Android Chrome). Access requires a tunnel (e.g. Cloudflare Tunnel) since the browse server binds to `127.0.0.1`.

| Surface | Mobile status |
|---------|--------------|
| Sessions, search, insights, graph, settings | ✅ Fully functional via browser |
| Operator console page load (`/v2/chat`) | ✅ Page loads |
| SSE live transcript streaming | ✅ `EventSource` is supported on iOS Safari 13+ and Android Chrome |
| Prompt submission (POST) | ❌ Blocked by the `check_origin` HTTP/HTTPS mismatch until that fix is applied — see [docs/OPERATOR-PLAYBOOK.md](../docs/OPERATOR-PLAYBOOK.md#remote-access-via-cloudflare-tunnel) |
| Keyboard shortcuts | ⚠️ Not usable without a physical keyboard |

## Phases

- **Phase 6**: Shipped scaffold — stub routes, providers, API client, and build pipeline
- **Phase 7**: Shipped sessions list + session detail + search pages with real data
- **Phase 8**: Shipped insights + graph pages (dashboard/live and Evidence/Similarity/Communities)
- **Phase 9**: Shipped settings, global keyboard shortcuts, and session detail compare/export polish
- **Phase 10**: Shipped operator console (`/v2/chat`) — browser-managed Copilot CLI execution with streamed output, persisted run history, and file review
