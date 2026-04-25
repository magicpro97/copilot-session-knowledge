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
| `/v2/sessions` | Session list |
| `/v2/sessions/[id]` | Session detail + timeline |
| `/v2/search` | Full-text + semantic search |
| `/v2/insights` | Knowledge insights |
| `/v2/graph` | Knowledge graph (force-directed) |
| `/v2/settings` | Preferences |

## Build output

`pnpm build` runs `next build` (static export) then `scripts/post-build.mjs` which writes `dist/version.json`.

The `dist/` directory is **committed to git** and served directly by `browse/routes/serve_v2.py`.

Do **not** edit files in `dist/` directly — they are build artifacts. Run `pnpm build` instead.

## Architecture notes

- API calls go to `/api/*` on the same origin (Python browse server)
- Auth token is injected via URL param `?token=…` on first load, then stored in `sessionStorage`
- `output: "export"` in next.config.ts means no SSR — all pages are static HTML + client JS
- Dynamic routes require `generateStaticParams()` in a server component wrapper

## Phases

- **Phase 6** (this PR): Scaffold — stub pages, providers, API client, build pipeline
- **Phase 7**: Sessions list + detail pages with real data
- **Phase 8**: Search, insights, graph pages
- **Phase 9**: Settings, keyboard shortcuts, polish

