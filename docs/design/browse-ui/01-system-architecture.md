# System Architecture: browse-ui (Next.js 15 Module)

> **Status:** Design — v1.0  
> **Date:** 2025-07-18  
> **Stack:** Next.js 15 (app router, RSC) · shadcn/ui · Tailwind v4 · TanStack Table · TanStack Query · cmdk · lucide-react · nuqs · Vitest · Playwright

---

## 0. Prerequisites

Before setting up `browse-ui`, ensure the following tools are installed:

| Tool | Minimum version | Recommended | Install |
|------|----------------|-------------|---------|
| **Node.js** | 20 | **24 LTS** | https://nodejs.org or `nvm install 24` |
| **pnpm** | 9 | 9 (latest patch) | `npm install -g pnpm@9` |
| **Git** | 2.30 | latest | https://git-scm.com |

### Why pnpm (not npm or yarn)?

pnpm is the **single required package manager** for this project — do not fall back to npm or yarn.

1. **Lockfile determinism** — `pnpm-lock.yaml` pins exact resolved versions; `npm install` can silently drift between machines and CI runs.
2. **Disk savings** — content-addressable store deduplicates packages across projects; relevant when CI caches the global store.
3. **Faster CI** — install from the frozen lockfile (`--frozen-lockfile`) in CI takes 10–30 s vs npm's 60–90 s for this tree.

One-time global install (run once per machine):
```bash
npm install -g pnpm@9
```

Then from the repo root:
```bash
cd browse-ui
pnpm install          # ← always use pnpm, never npm install
```

---

## 1. Repo Layout

```
copilot-session-knowledge/
├── browse/                          # ← existing Python (UI cũ, giữ nguyên)
│   ├── core/
│   ├── routes/
│   ├── components/
│   └── static/
├── browse-ui/                       # ← NEW: Next.js 15 module
│   ├── dist/                        # Pre-built output, checked into git
│   │   ├── _next/
│   │   │   ├── static/
│   │   │   │   ├── chunks/
│   │   │   │   ├── css/
│   │   │   │   └── media/
│   │   │   └── data/
│   │   ├── v2/                      # SPA HTML pages keyed by route
│   │   │   ├── index.html
│   │   │   ├── sessions.html
│   │   │   ├── dashboard.html
│   │   │   ├── search.html
│   │   │   └── [...etc].html
│   │   └── version.json             # Build metadata
│   ├── src/
<!-- FIXED in cross-review pass: MAJOR-1 — rewritten to match doc 02's 8-route IA; MAJOR-6 — added settings -->
│   │   ├── app/                     # Next.js app router
│   │   │   ├── layout.tsx           # Root layout: providers, sidebar, theme
│   │   │   ├── page.tsx             # / → redirect to /sessions
│   │   │   ├── sessions/
│   │   │   │   ├── page.tsx         # Sessions list (landing page)
│   │   │   │   └── [id]/
│   │   │   │       ├── layout.tsx   # Session detail layout (tabs)
│   │   │   │       ├── page.tsx     # Overview tab (default)
│   │   │   │       ├── timeline/
│   │   │   │       │   └── page.tsx # Timeline tab
│   │   │   │       ├── mindmap/
│   │   │   │       │   └── page.tsx # Mindmap tab
│   │   │   │       └── checkpoints/
│   │   │   │           └── page.tsx # Checkpoints tab
│   │   │   ├── search/
│   │   │   │   └── page.tsx         # Full-text search with facets
│   │   │   ├── insights/
│   │   │   │   ├── layout.tsx       # 5-tab workspace: Overview + Knowledge + Retro + Search Quality + Live feed
│   │   │   │   └── page.tsx         # Default tab → Overview
│   │   │   ├── graph/
│   │   │   │   └── page.tsx         # 4-tab workspace: Insight (default) + Evidence + Similarity + Communities
│   │   │   ├── settings/
│   │   │   │   └── page.tsx         # Preferences: theme, density, default landing, token storage
│   │   │   └── not-found.tsx        # Custom 404 page
│   │   ├── components/              # Shared UI components
│   │   │   ├── ui/                  # shadcn/ui primitives (auto-gen)
│   │   │   │   ├── button.tsx
│   │   │   │   ├── card.tsx
│   │   │   │   ├── badge.tsx
│   │   │   │   ├── input.tsx
│   │   │   │   ├── select.tsx
│   │   │   │   ├── table.tsx
│   │   │   │   ├── dialog.tsx
│   │   │   │   ├── command.tsx       # cmdk wrapper
│   │   │   │   ├── tooltip.tsx
│   │   │   │   ├── skeleton.tsx
│   │   │   │   ├── tabs.tsx
│   │   │   │   ├── slider.tsx
│   │   │   │   └── toggle-group.tsx
│   │   │   ├── layout/
│   │   │   │   ├── app-sidebar.tsx   # Main nav sidebar
│   │   │   │   ├── header.tsx        # Top bar: breadcrumb, density toggle, theme
│   │   │   │   ├── command-palette.tsx # ⌘K palette (cmdk)
│   │   │   │   └── density-toggle.tsx
│   │   │   ├── data/
│   │   │   │   ├── data-table.tsx    # TanStack Table wrapper
│   │   │   │   ├── stat-card.tsx     # KPI tile
│   │   │   │   ├── empty-state.tsx
│   │   │   │   └── banner.tsx        # Alert banner
│   │   │   └── charts/
│   │   │       ├── bar-chart.tsx     # Recharts or lightweight
│   │   │       └── line-chart.tsx
│   │   ├── lib/                     # Non-UI logic
│   │   │   ├── api/
│   │   │   │   ├── client.ts         # fetch wrapper: base URL, token injection
│   │   │   │   ├── types.ts          # All TS interfaces (API contract)
│   │   │   │   └── hooks.ts          # TanStack Query hooks per endpoint
│   │   │   ├── auth.ts              # Token extraction + forwarding
│   │   │   ├── constants.ts
│   │   │   └── utils.ts
│   │   ├── hooks/                   # React hooks
│   │   │   ├── use-density.ts
│   │   │   └── use-sse.ts           # SSE hook for /api/live
│   │   ├── providers/
│   │   │   ├── query-provider.tsx    # TanStack QueryClientProvider
│   │   │   └── theme-provider.tsx   # next-themes
│   │   └── styles/
│   │       └── globals.css          # Tailwind v4 imports + custom tokens
│   ├── public/
│   │   └── favicon.ico
│   ├── e2e/                         # Playwright tests
│   │   ├── smoke.spec.ts
│   │   ├── dashboard.spec.ts
│   │   ├── search.spec.ts
│   │   └── visual.spec.ts
│   ├── next.config.ts
│   ├── tailwind.config.ts
│   ├── tsconfig.json
│   ├── vitest.config.ts
│   ├── playwright.config.ts
│   ├── components.json              # shadcn/ui config
│   ├── package.json
│   ├── pnpm-lock.yaml
│   └── .gitignore
├── browse.py                        # Existing shim
└── ...
```

### Why each folder exists

| Folder | Rationale |
|--------|-----------|
| `src/app/` | Next.js 15 app router — file-based routing, RSC by default |
| `src/components/ui/` | shadcn/ui auto-generated primitives — copy-paste-own pattern |
| `src/components/layout/` | Shell components shared across all pages (sidebar, header, palette) |
| `src/components/data/` | Domain data display: tables, cards, empty states |
<!-- FIXED in cross-review pass: BLOCKER-1 — changed uPlot → Recharts -->
| `src/components/charts/` | Recharts chart wrappers (bar, line, area, donut, sparkline) |
| `src/lib/api/` | Single source of truth for API contract: types, fetch client, query hooks |
| `src/hooks/` | Reusable React hooks (density, SSE) |
| `src/providers/` | Context providers wrapped in layout.tsx |
| `e2e/` | Playwright E2E tests against real Python server |
| `dist/` | Pre-built static export — checked into git so end-users skip Node |

---

## 2. API Contract: Python ↔ TypeScript

### 2.1 Complete Route Inventory

| # | Python Route | Method | `?format=json` | JSON API Route | Response Shape |
|---|-------------|--------|:---:|----------------|---------------|
| 1 | `/` | GET | ✅ | — | `HomeSession[]` |
| 2 | `/sessions` | GET | ✅ | — | `SessionRow[]` |
| 3 | `/session/{id}` | GET | ✅ | — | `{ meta: SessionMeta, timeline: TimelineEntry[] }` |
| 4 | `/session/{id}.md` | GET | — | — | `text/markdown` (no JSON needed) |
| 5 | `/session/{id}/timeline` | GET | ❌ | `/api/session/{id}/events` | `{ events: TimelineEvent[], total: number, session_id: string }` |
| 6 | `/session/{id}/mindmap` | GET | ❌ | `/api/session/{id}/mindmap` | `{ markdown: string, title: string }` |
| 7 | `/search` | GET | ✅ | `/api/search` | `{ query: string, results: SearchResult[], total: number, took_ms: number }` |
| 8 | `/dashboard` | GET | ❌ | `/api/dashboard/stats` | `DashboardStats` |
| 9 | `/graph` | GET | ❌ | `/api/graph` (legacy) + `/api/graph/evidence` (v2 primary) | `GraphResponseLegacy` + `EvidenceGraphResponse` |
| 10 | `/embeddings` | GET | ❌ | `/api/embeddings/points` | `EmbeddingProjection` (Similarity orientation map) |
| 11 | `/live` | GET | ❌ | `/api/live` | SSE stream: `LiveEvent` per event |
| 12 | `/diff` | GET | ❌ | `/api/diff` | `DiffResult` |
| 13 | `/eval` | GET | ❌ | — **NEEDS JSON** | `EvalAggregation` |
| 14 | `/compare` | GET | ❌ | — **NEEDS JSON** | `{ a: SessionCompareData, b: SessionCompareData }` |
| 15 | `/healthz` | GET | ✅ | — | `{ status: string, schema_version: number, sessions: number }` |
| 16 | `/style-guide` | GET | ❌ | — | **No migration** (dev-only, Python-rendered) |
| 17 | `/api/feedback` | POST | ✅ | — | `{ ok: boolean, id: number }` |

**Action items for Python backend:**
<!-- FIXED in cross-review pass: MAJOR-3 — added status annotations for endpoints needing creation -->
- Add `/api/eval/stats` JSON endpoint — **Status: TO BE CREATED in Pha 5** (extract `_ensure_feedback_table` + aggregate query)
- Add `/api/compare?a={id}&b={id}` JSON endpoint — **Status: TO BE CREATED in Pha 5** (reuse `_fetch_session_data`)
- Existing 13 JSON endpoints are sufficient as-is

### 2.2 TypeScript Interfaces

```typescript
// src/lib/api/types.ts

// ── Shared ────────────────────────────────────────────────────────────

export interface SessionRow {
  id: string;
  path: string | null;
  summary: string | null;
  source: string | null;
  event_count_estimate: number | null;
  fts_indexed_at: string | null;
  indexed_at_r?: string | null;
}

export interface SessionMeta extends SessionRow {
  file_mtime: string | null;
}

// ── Home (/  ?format=json) ───────────────────────────────────────────

export type HomeResponse = SessionRow[];

// ── Sessions (/sessions  ?format=json) ───────────────────────────────

<!-- FIXED in cross-review pass: MAJOR-2 — pagination envelope for sessions API -->

export type SessionListResponse = {
  items: SessionRow[];
  total: number;
  page: number;
  page_size: number;
  has_more: boolean;
};

// NOTE: The Python endpoint currently returns a flat SessionRow[].
// Pha 5 MUST update routes/sessions.py to return this envelope
// BEFORE Pha 6 scaffold consumes it. See Risk Register R8.

// ── Session Detail (/session/{id}  ?format=json) ────────────────────

export interface TimelineEntry {
  seq: number;
  title: string | null;
  doc_type: string | null;
  section_name: string | null;
  content: string | null;
}

export interface SessionDetailResponse {
  meta: SessionMeta;
  timeline: TimelineEntry[];
}

// ── Timeline (/api/session/{id}/events) ──────────────────────────────

export interface TimelineEvent {
  event_id: number;
  kind: string;
  preview: string;
  byte_offset: number | null;
  file_mtime: string | null;
  color: string;
}

export interface TimelineEventsResponse {
  events: TimelineEvent[];
  total: number;
  session_id: string;
}

// ── Mindmap (/api/session/{id}/mindmap) ──────────────────────────────

export interface MindmapResponse {
  markdown: string;
  title: string;
}

// ── Search (/api/search) ────────────────────────────────────────────

export interface SearchResult {
  type: "session" | "knowledge";
  id: string | number;
  title: string;
  snippet?: string;
  score: number;
  // knowledge-only fields
  wing?: string;
  kind?: string;
}

export interface SearchResponse {
  query: string;
  results: SearchResult[];
  total: number;
  took_ms: number;
}

// ── Dashboard (/api/dashboard/stats) ─────────────────────────────────

export interface DashboardTotals {
  sessions: number;
  knowledge_entries: number;
  relations: number;
  embeddings: number;
}

export interface CategoryCount {
  name: string;
  count: number;
}

export interface DayCount {
  date: string;
  count: number;
}

export interface WeekCount {
  week: string;
  count: number;
}

export interface ModuleCount {
  module: string;
  count: number;
}

export interface WingCount {
  wing: string;
  count: number;
}

export interface RedFlag {
  session_id: string;
  events: number;
  summary: string | null;
}

export interface DashboardStats {
  totals: DashboardTotals;
  by_category: CategoryCount[];
  sessions_per_day: DayCount[];
  top_wings: WingCount[];
  red_flags: RedFlag[];
  weekly_mistakes: WeekCount[];
  top_modules: ModuleCount[];
}

// ── Legacy Graph (/api/graph) — compatibility only ──────────────────

export interface GraphNode {
  id: string;
  kind: "entry" | "entity";
  label: string;
  wing?: string;
  room?: string;
  category?: string;
  color: string;
}

export interface GraphEdge {
  source: string;
  target: string;
  relation: string;
}

export interface GraphResponseLegacy {
  nodes: GraphNode[];
  edges: GraphEdge[];
  truncated: boolean;
}

// ── Evidence Graph (/api/graph/evidence) — v2 primary ───────────────

export type EvidenceRelationType =
  | "SAME_SESSION"
  | "RESOLVED_BY"
  | "TAG_OVERLAP"
  | "SAME_TOPIC";

export interface EvidenceEdge {
  source: string;
  target: string;
  relation_type: EvidenceRelationType;
  confidence: number; // [0, 1]
}

export interface EvidenceGraphResponse {
  nodes: GraphNode[]; // kind is expected to be "entry" for evidence graph
  edges: EvidenceEdge[];
  truncated: boolean;
  meta: {
    edge_source: "knowledge_relations";
    relation_types: EvidenceRelationType[];
  };
}

// ── Embeddings (/api/embeddings/points) ──────────────────────────────

export interface EmbeddingPoint {
  x: number;
  y: number;
  id: number;
  title: string;
  category: string;
}

export interface EmbeddingProjection {
  points: EmbeddingPoint[];
  method: string;
}

// ── Similarity (/api/graph/similarity) — neighbors are primary ───────
// NOTE (contract freeze): request shape must remain bulk-friendly.
// Final request parameters/body format is intentionally unresolved.

export interface SimilarityNeighbor {
  id: number;
  title: string;
  category: string;
  score: number; // cosine similarity in [0, 1]
}

export interface SimilarityNeighborsByEntry {
  entry_id: number;
  neighbors: SimilarityNeighbor[];
}

export interface SimilarityResponse {
  results: SimilarityNeighborsByEntry[];
  meta: {
    method: "cosine_knn";
    k: number;
  };
}

// ── Communities (/api/graph/communities) ─────────────────────────────
// Communities ship only after evidence + similarity are trustworthy.

export interface CommunitySummary {
  id: string;
  entry_count: number;
  top_categories: Array<{ name: string; count: number }>;
  representative_entries: Array<{ id: number; title: string; category: string }>;
}

export interface CommunitiesResponse {
  communities: CommunitySummary[];
}

// ── Live (/api/live  — SSE) ─────────────────────────────────────────

export interface LiveEvent {
  id: number;
  category: string;
  title: string;
  wing: string;
  room: string;
  created_at: string;
}

// ── Diff (/api/diff) ────────────────────────────────────────────────

export interface DiffCheckpoint {
  seq: number;
  title: string;
  file: string;
}

export interface DiffResult {
  session_id: string;
  from: DiffCheckpoint;
  to: DiffCheckpoint;
  unified_diff: string;
  files: Array<{ from: string; to: string }>;
  stats: { added: number; removed: number };
}

// ── Eval (/api/eval/stats  — NEEDS NEW ENDPOINT) ────────────────────

export interface EvalAggRow {
  query: string;
  up: number;
  down: number;
  neutral: number;
  total: number;
}

export interface EvalComment {
  query: string;
  result_id: string;
  verdict: -1 | 0 | 1;
  comment: string;
  created_at: string;
}

export interface EvalResponse {
  aggregation: EvalAggRow[];
  recent_comments: EvalComment[];
}

// ── Compare (/api/compare  — NEEDS NEW ENDPOINT) ────────────────────

export interface SessionCompareData {
  session: SessionMeta | null;
  timeline: TimelineEntry[];
}

export interface CompareResponse {
  a: SessionCompareData;
  b: SessionCompareData;
}

// ── Health (/healthz) ───────────────────────────────────────────────

export interface HealthResponse {
  status: string;
  schema_version: number;
  sessions: number;
}

// ── Feedback (/api/feedback  POST) ──────────────────────────────────

export interface FeedbackRequest {
  query: string;
  result_id: string;
  result_kind: string;
  verdict: -1 | 0 | 1;
  comment?: string;
}

export interface FeedbackResponse {
  ok: boolean;
  id: number;
}
```

### 2.3 Type Generation Strategy

**Decision: Hand-written TS interfaces** (not pydantic-to-typescript, not OpenAPI).

Rationale:
- Python routes return raw `dict` via `json.dumps()` — no Pydantic models exist.
- Adding Pydantic just for type export would bloat a zero-dependency Python project.
- 17 routes × simple shapes = manageable by hand; a single `types.ts` file (~200 lines).
- **Validation: Zod at client** for runtime safety on untrusted JSON.

```typescript
// src/lib/api/schemas.ts  — Zod schemas mirror types.ts

import { z } from "zod";

export const sessionRowSchema = z.object({
  id: z.string(),
  path: z.string().nullable(),
  summary: z.string().nullable(),
  source: z.string().nullable(),
  event_count_estimate: z.number().nullable(),
  fts_indexed_at: z.string().nullable(),
});

export const dashboardStatsSchema = z.object({
  totals: z.object({
    sessions: z.number(),
    knowledge_entries: z.number(),
    relations: z.number(),
    embeddings: z.number(),
  }),
  by_category: z.array(z.object({ name: z.string(), count: z.number() })),
  sessions_per_day: z.array(z.object({ date: z.string(), count: z.number() })),
  top_wings: z.array(z.object({ wing: z.string(), count: z.number() })),
  red_flags: z.array(z.object({
    session_id: z.string(),
    events: z.number(),
    summary: z.string().nullable(),
  })),
  weekly_mistakes: z.array(z.object({ week: z.string(), count: z.number() })),
  top_modules: z.array(z.object({ module: z.string(), count: z.number() })),
});

// ... one schema per response type
```

### 2.4 SSE for `/live`

**Decision: Keep SSE.** The `/api/live` endpoint streams real-time knowledge entries via SSE. The Next.js client will consume it with a custom `useSSE` hook.

```typescript
// src/hooks/use-sse.ts
import { useEffect, useRef, useState, useCallback } from "react";
import type { LiveEvent } from "@/lib/api/types";

export function useSSE(url: string, options?: { enabled?: boolean }) {
  const [events, setEvents] = useState<LiveEvent[]>([]);
  const [status, setStatus] = useState<"connecting" | "open" | "closed">("connecting");
  const esRef = useRef<EventSource | null>(null);
  const pausedRef = useRef(false);

  const toggle = useCallback(() => {
    pausedRef.current = !pausedRef.current;
  }, []);

  useEffect(() => {
    if (options?.enabled === false) return;
    const es = new EventSource(url);
    esRef.current = es;
    es.onopen = () => setStatus("open");
    es.onmessage = (e) => {
      if (pausedRef.current) return;
      const data: LiveEvent = JSON.parse(e.data);
      setEvents((prev) => [data, ...prev].slice(0, 200));
    };
    es.onerror = () => setStatus("closed");
    return () => { es.close(); setStatus("closed"); };
  }, [url, options?.enabled]);

  return { events, status, toggle };
}
```

---

## 3. Build/Serve Flow

### 3.1 Development: `pnpm dev` with API Proxy

```typescript
// browse-ui/next.config.ts
import type { NextConfig } from "next";

const config: NextConfig = {
  basePath: "/v2",
  output: "export",      // Static export for prod
  trailingSlash: true,

  // Dev-only: proxy API calls to Python server
  async rewrites() {
    return [
      // All /api/* calls → Python backend
      { source: "/api/:path*", destination: "http://127.0.0.1:8765/api/:path*" },
      // format=json calls for legacy routes
      { source: "/v2/proxy/sessions", destination: "http://127.0.0.1:8765/sessions?format=json" },
      { source: "/v2/proxy/home", destination: "http://127.0.0.1:8765/?format=json" },
      { source: "/v2/proxy/session/:id", destination: "http://127.0.0.1:8765/session/:id?format=json" },
      // Health
      { source: "/healthz", destination: "http://127.0.0.1:8765/healthz" },
    ];
  },
};

export default config;
```

**Dev workflow:**

```bash
# Terminal 1: Python API server
cd /path/to/copilot-session-knowledge
python3 browse.py --port 8765 --token mytoken

# Terminal 2: Next.js dev server
cd browse-ui
pnpm dev   # → http://localhost:3000/v2
```

The Next.js dev server (port 3000) proxies `/api/*` requests to Python (port 8765). Token is forwarded via query param.

### 3.2 Production: Static Export → Python Serves `dist/`

<!-- FIXED in cross-review pass: MINOR-3 — added rule for dynamic routes with output: export -->
> **Dynamic route rule:** Dynamic routes with `output: "export"` must EITHER (a) use client-side fetch
> (param read via `useParams()` in a client component), OR (b) export `generateStaticParams()`.
> In browse-ui, choose **(a)** for session detail (`/sessions/[id]`) because the session list is dynamic
> and cannot be enumerated at build time. Each dynamic `page.tsx` should be a `'use client'` component
> that reads `params.id` and fetches data via TanStack Query.

```bash
# Build command
cd browse-ui
pnpm build   # next build && next-export-postprocess
```

`next build` with `output: "export"` produces `browse-ui/dist/` (renamed from `out/`).

**Python serving code — add to `browse/core/server.py`:**

```python
# browse/core/static_v2.py — Serve pre-built Next.js UI at /v2/*

import os
from pathlib import Path

_V2_DIST = (Path(__file__).parent.parent.parent / "browse-ui" / "dist").resolve()

# Allowed extensions (same as static.py)
_CT = {
    ".html": "text/html; charset=utf-8",
    ".js":   "application/javascript",
    ".css":  "text/css",
    ".json": "application/json",
    ".svg":  "image/svg+xml",
    ".png":  "image/png",
    ".ico":  "image/x-icon",
    ".woff2": "font/woff2",
    ".txt":  "text/plain; charset=utf-8",
    ".map":  "application/json",
}


def serve_v2(rel_path: str) -> tuple:
    """Serve files from browse-ui/dist/ with SPA fallback.

    rel_path: path after '/v2/' (e.g. 'dashboard' or '_next/static/chunks/abc.js')
    Returns (body_bytes, content_type, status_code).
    """
    if not _V2_DIST.exists():
        return b"404 browse-ui/dist/ not found. Run: cd browse-ui && pnpm build", "text/plain", 404

    # Security: reject traversal
    if ".." in rel_path or "\x00" in rel_path:
        return b"400 Bad Request", "text/plain", 400

    # Try exact file first
    candidate = (_V2_DIST / rel_path).resolve()
    try:
        candidate.relative_to(_V2_DIST)
    except ValueError:
        return b"403 Forbidden", "text/plain", 403

    # _next/* assets: serve directly
    if candidate.is_file():
        ext = candidate.suffix.lower()
        ct = _CT.get(ext, "application/octet-stream")
        return candidate.read_bytes(), ct, 200

    # SPA fallback: try {rel_path}.html, then {rel_path}/index.html, then /v2/index.html
    for try_path in [
        _V2_DIST / f"{rel_path}.html",
        _V2_DIST / rel_path / "index.html",
        _V2_DIST / "index.html",
    ]:
        resolved = try_path.resolve()
        try:
            resolved.relative_to(_V2_DIST)
        except ValueError:
            continue
        if resolved.is_file():
            return resolved.read_bytes(), "text/html; charset=utf-8", 200

    return b"404 Not Found", "text/plain", 404
```

**Add to `do_GET` in `server.py` — before auth check:**

```python
# /v2/* — serve pre-built Next.js UI (no auth for static assets, auth for pages)
if path.startswith("/v2/"):
    rel = path[len("/v2/"):]
    # _next/ assets are public (JS/CSS/fonts)
    if rel.startswith("_next/"):
        from browse.core.static_v2 import serve_v2
        body, ct, status = serve_v2(rel)
        self._send(body, ct, status, nonce)
        return
    # Page routes require auth (fall through to auth check, then serve)
```

### 3.3 Hot Reload Story

| Layer | Tool | What happens |
|-------|------|-------------|
| Next.js components | `pnpm dev` (Turbopack) | HMR in ~100ms, no page reload |
| Python API changes | Manual `python3 browse.py` restart | No auto-reload; use `watchdog` wrapper or manual |
| Tailwind CSS | Tailwind v4 built-in watcher | Instant via PostCSS in Turbopack |
| shadcn/ui additions | `pnpm dlx shadcn@latest add <component>` | One-time; then HMR |

---

## 4. Routing Strategy (Strangler)

### 4.1 Route Mapping Table

<!-- FIXED in cross-review pass: MAJOR-1 — aligned route table with doc 02's 8-route IA; MAJOR-3 — marked eval/compare endpoints -->
| Python (old) | Next.js (new) `/v2/*` | Priority | Phase |
|-------------|----------------------|----------|-------|
| `/` | `/v2/sessions` (redirect) | P1 | 1 |
| `/sessions` | `/v2/sessions` | P1 | 1 |
| `/session/{id}` | `/v2/sessions/[id]` | P1 | 1 |
| `/session/{id}/timeline` | `/v2/sessions/[id]/timeline` (tab) | P2 | 2 |
| `/session/{id}/mindmap` | `/v2/sessions/[id]/mindmap` (tab) | P3 | 3 |
| `/search` | `/v2/search` | P1 | 1 |
| `/dashboard` | `/v2/insights` (Overview tab) | P2 | 2 |
| `/live` | `/v2/insights` (Live feed tab) | P2 | 2 |
| `/graph` | `/v2/graph` (Knowledge Graph tab) | P3 | 3 |
| `/embeddings` | `/v2/graph` (Similarity orientation map, via `/api/embeddings/points`) | P3 | 3 |
| `/eval` | `/v2/insights` (Search Quality tab) | P4 | 4 |
| `/compare` | — (action within session detail) | P3 | 3 |
| `/diff` | — (action within session detail) | P3 | 3 |
| `/settings` | `/v2/settings` | P2 | 2 |
| `/session/{id}.md` | — (keep Python) | — | — |
| `/healthz` | — (keep Python) | — | — |
| `/style-guide` | — (remove) | — | — |

### 4.2 Banner in Old UI

Add to `browse/core/templates.py` `base_page()`, above `<main>`:

```python
v2_banner = (
    '<div style="background:#1d4ed8;color:#fff;text-align:center;'
    'padding:0.4rem;font-size:0.85rem;">'
    f'✨ <a href="/v2/sessions{tok_qs}" style="color:#fff;text-decoration:underline;">'
    'Try the new UI</a> — faster, better looking, same data.'
    '</div>\n'
)
```

### 4.3 Kill Old UI Timeline

| Milestone | Action |
|-----------|--------|
| Phase 2 complete | Banner changes to "New UI is default. Old UI will be removed soon." |
| Phase 4 complete + 2 weeks soak | Remove `browse/routes/*.py` HTML rendering, keep only JSON endpoints. Old `/` redirects to `/v2/sessions`. |
| Phase 4 + 4 weeks | Remove banner, rename `/v2/*` → `/*` (drop basePath), update `next.config.ts`. |

---

## 5. Auth Flow

### 5.1 Current Auth (Python)

1. Token set at server start: `--token=<secret>`
2. Client passes `?token=<secret>` on first request
3. Server validates via `hmac.compare_digest`, sets `browse_token=<secret>` HttpOnly cookie
4. Subsequent requests use cookie (no token in URL)

### 5.2 Next.js Auth Strategy

**No Next.js middleware** (static export has no server-side middleware). Auth is handled entirely client-side + Python backend.

```typescript
// src/lib/auth.ts — Token management for the client

const TOKEN_STORAGE_KEY = "browse_token";

export function getToken(): string {
  // 1. Check URL search params (initial entry)
  if (typeof window !== "undefined") {
    const params = new URLSearchParams(window.location.search);
    const urlToken = params.get("token");
    if (urlToken) {
      // Store in sessionStorage for subsequent API calls
      sessionStorage.setItem(TOKEN_STORAGE_KEY, urlToken);
      // Clean URL (remove token from address bar)
      const clean = new URL(window.location.href);
      clean.searchParams.delete("token");
      window.history.replaceState({}, "", clean.toString());
      return urlToken;
    }
    // 2. Fallback to stored token
    return sessionStorage.getItem(TOKEN_STORAGE_KEY) ?? "";
  }
  return "";
}

export function clearToken(): void {
  sessionStorage.removeItem(TOKEN_STORAGE_KEY);
}
```

```typescript
// src/lib/api/client.ts — API client with auto-token injection

import { getToken } from "@/lib/auth";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "";

<!-- FIXED in cross-review pass: MINOR-4 — use sessionStorage-only, no query-param after bootstrap -->
export async function apiFetch<T>(
  path: string,
  init?: RequestInit,
): Promise<T> {
  const token = getToken();
  const url = new URL(path, API_BASE || window.location.origin);

  // Only append token as query-param on first load (bootstrap).
  // After that, sessionStorage token is sent; Python backend sets
  // HttpOnly cookie on first valid request. We do NOT set cookie
  // ourselves — we rely on sessionStorage only.
  if (token) url.searchParams.set("token", token);

  const res = await fetch(url.toString(), {
    ...init,
    // NOTE: no credentials: "include" — we do NOT use cookies.
    // Auth is sessionStorage-based query-param only.
  });

  if (res.status === 401) {
    clearToken();
    window.location.href = "/v2/sessions";  // Force re-auth
    throw new Error("Unauthorized");
  }

  if (!res.ok) {
    throw new Error(`API ${res.status}: ${await res.text()}`);
  }

  return res.json();
}
```

**Flow:**
1. User opens `/v2/sessions?token=abc123`
2. `auth.ts` extracts token from URL, stores in `sessionStorage`, cleans URL
3. Every `apiFetch()` call reads token from `sessionStorage` and appends `?token=...`
4. Token NEVER stored in cookies — sessionStorage only (avoids log-leak surface via Referer/proxy logs)
5. If 401 → clear stored token, redirect to home

---

## 6. State Management

### 6.1 Server State: TanStack Query

```typescript
// src/lib/api/hooks.ts

import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "./client";
import type * as T from "./types";

// ── Query keys (colocated, typed) ─────────────────────────────────

export const keys = {
  home: ["home"] as const,
  sessions: (q?: string, limit?: number, offset?: number) =>
    ["sessions", { q, limit, offset }] as const,
  session: (id: string) => ["session", id] as const,
  sessionEvents: (id: string, from: number, limit: number) =>
    ["session", id, "events", { from, limit }] as const,
  dashboard: ["dashboard"] as const,
  search: (q: string, opts?: Record<string, string>) =>
    ["search", q, opts] as const,
  graph: (filters?: Record<string, string>) =>
    ["graph", filters] as const,
  embeddings: ["embeddings"] as const,
  diff: (session: string, from: string, to: string) =>
    ["diff", session, from, to] as const,
  mindmap: (id: string) => ["mindmap", id] as const,
  eval: ["eval"] as const,
  compare: (a: string, b: string) => ["compare", a, b] as const,
  health: ["health"] as const,
};

// ── Hooks ─────────────────────────────────────────────────────────

export function useHome() {
  return useQuery({
    queryKey: keys.home,
    queryFn: () => apiFetch<T.HomeResponse>("/?format=json"),
  });
}

export function useSessions(q = "", limit = 20, offset = 0) {
  return useQuery({
    queryKey: keys.sessions(q, limit, offset),
    queryFn: () => {
      const params = new URLSearchParams({ format: "json", limit: String(limit), offset: String(offset) });
      if (q) params.set("q", q);
      return apiFetch<T.SessionsResponse>(`/sessions?${params}`);
    },
  });
}

export function useSessionDetail(id: string) {
  return useQuery({
    queryKey: keys.session(id),
    queryFn: () => apiFetch<T.SessionDetailResponse>(`/session/${id}?format=json`),
    enabled: !!id,
  });
}

export function useDashboard() {
  return useQuery({
    queryKey: keys.dashboard,
    queryFn: () => apiFetch<T.DashboardStats>("/api/dashboard/stats"),
    staleTime: 30_000,
  });
}

export function useSearch(q: string, opts?: { in?: string; src?: string; kind?: string }) {
  return useQuery({
    queryKey: keys.search(q, opts),
    queryFn: () => {
      const params = new URLSearchParams();
      params.set("q", q);
      if (opts?.in) params.set("in", opts.in);
      if (opts?.src) params.set("src", opts.src);
      if (opts?.kind) params.set("kind", opts.kind);
      return apiFetch<T.SearchResponse>(`/api/search?${params}`);
    },
    enabled: q.length >= 2,
  });
}

export function useGraph(filters?: { wing?: string; kind?: string; limit?: number }) {
  return useQuery({
    queryKey: keys.graph(filters),
    queryFn: () => {
      const params = new URLSearchParams();
      if (filters?.wing) params.set("wing", filters.wing);
      if (filters?.kind) params.set("kind", filters.kind);
      if (filters?.limit) params.set("limit", String(filters.limit));
      return apiFetch<T.GraphResponseLegacy>(`/api/graph?${params}`);
    },
  });
}

export function useEvidenceGraph(filters?: {
  wing?: string;
  kind?: string;
  relation_type?: T.EvidenceRelationType;
  limit?: number;
}) {
  return useQuery({
    queryKey: ["evidence", filters],
    queryFn: () => {
      const params = new URLSearchParams();
      if (filters?.wing) params.set("wing", filters.wing);
      if (filters?.kind) params.set("kind", filters.kind);
      if (filters?.relation_type) params.set("relation_type", filters.relation_type);
      if (filters?.limit) params.set("limit", String(filters.limit));
      return apiFetch<T.EvidenceGraphResponse>(`/api/graph/evidence?${params}`);
    },
  });
}

export function useFeedback() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: T.FeedbackRequest) =>
      apiFetch<T.FeedbackResponse>("/api/feedback", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: keys.eval }),
  });
}
```

### 6.2 URL State: `nuqs`

**Decision: `nuqs`** — type-safe URL search params with shallow routing (no full page re-render).

```typescript
// Usage in sessions page
import { useQueryState, parseAsInteger, parseAsString } from "nuqs";

export default function SessionsPage() {
  const [q, setQ] = useQueryState("q", parseAsString.withDefault(""));
  const [page, setPage] = useQueryState("page", parseAsInteger.withDefault(1));
  const [sort, setSort] = useQueryState("sort", parseAsString.withDefault("date"));

  const { data } = useSessions(q, 20, (page - 1) * 20);
  // ...
}
```

URL example: `/v2/sessions?q=auth+bug&page=2&sort=events`

### 6.3 Theme + Density

```typescript
// src/providers/theme-provider.tsx
import { ThemeProvider as NextThemesProvider } from "next-themes";

export function ThemeProvider({ children }: { children: React.ReactNode }) {
  return (
    <NextThemesProvider
      attribute="class"
      defaultTheme="system"
      enableSystem
      storageKey="browse-theme"
    >
      {children}
    </NextThemesProvider>
  );
}
```

```typescript
// src/hooks/use-density.ts
import { useLocalStorage } from "@/hooks/use-local-storage";

<!-- FIXED in cross-review pass: MINOR-1 — removed "spacious" density (only 2 modes: compact + comfortable) -->
export type Density = "comfortable" | "compact";

export function useDensity() {
  return useLocalStorage<Density>("browse-density", "comfortable");
}
```

Density is applied via a CSS class on `<html>`:

```css
/* globals.css — density summary (see full globals.css above for complete tokens) */
:root { --row-height: 2.75rem; --cell-padding: 0.75rem 1rem; }   /* comfortable: 44px */
.density-compact { --row-height: 2rem; --cell-padding: 0.25rem 0.5rem; }   /* compact: 32px */
```

<!-- FIXED in cross-review pass: MAJOR-8 — added security section for XSS prevention -->

### 6.4 Security: HTML Rendering

**Rule: No `dangerouslySetInnerHTML` without `DOMPurify.sanitize()`.**

All user-facing content rendered from session data (search snippets, markdown, timeline content) must be sanitized:

| Content type | Sanitization method | Library |
|---|---|---|
| Markdown rendering | `react-markdown` + `rehype-sanitize` plugin | `rehype-sanitize` |
| Search result snippets | `<Highlight text={snippet} ranges={matches} />` component — renders `<mark>` client-side from plain text + offset arrays. **NEVER** accepts raw HTML. | Custom component |
| Raw HTML from API (if any) | `DOMPurify.sanitize(html)` before `dangerouslySetInnerHTML` | `dompurify` |

**Implementation pattern:**

```tsx
// ✅ CORRECT — search snippet rendering
function Highlight({ text, ranges }: { text: string; ranges?: [number, number][] }) {
  if (!ranges?.length) return <>{text}</>;
  const parts: React.ReactNode[] = [];
  let cursor = 0;
  for (const [start, end] of ranges) {
    if (start > cursor) parts.push(text.slice(cursor, start));
    parts.push(<mark key={start}>{text.slice(start, end)}</mark>);
    cursor = end;
  }
  if (cursor < text.length) parts.push(text.slice(cursor));
  return <>{parts}</>;
}

// ❌ WRONG — never do this
<div dangerouslySetInnerHTML={{ __html: snippet }} />
```

---

## 7. Build Artifacts Versioning

### 7.1 `dist/` Check-in Strategy

```jsonc
// browse-ui/dist/version.json  (auto-generated at build)
{
  "version": "0.1.0",
  "buildHash": "a3b4c5d6",
  "builtAt": "2025-07-18T12:00:00Z",
  "nextVersion": "15.3.2",
  "nodeVersion": "22.12.0"
}
```

**Build script:**

```jsonc
// browse-ui/package.json (scripts section)
{
  "scripts": {
    "dev": "next dev --turbopack",
    "build": "next build && node scripts/post-build.mjs",
    "typecheck": "tsc --noEmit",
    "lint": "next lint",
    "test": "vitest run",
    "test:watch": "vitest",
    "test:e2e": "playwright test",
    "preview": "npx serve dist"
  }
}
```

```javascript
// browse-ui/scripts/post-build.mjs
import { execSync } from "child_process";
import { writeFileSync, renameSync, existsSync, rmSync } from "fs";
import { resolve } from "path";

const distDir = resolve("dist");
const outDir = resolve("out");  // Next.js static export default

// Rename out/ → dist/
if (existsSync(distDir)) rmSync(distDir, { recursive: true });
renameSync(outDir, distDir);

// Write version.json
const hash = execSync("git rev-parse --short HEAD").toString().trim();
const version = JSON.parse(
  execSync("node -e \"console.log(JSON.stringify(require('./package.json').version))\"").toString()
);

writeFileSync(resolve(distDir, "version.json"), JSON.stringify({
  version,
  buildHash: hash,
  builtAt: new Date().toISOString(),
  nextVersion: execSync("npx next --version").toString().trim(),
  nodeVersion: process.version,
}, null, 2));

console.log(`✅ dist/ ready — hash: ${hash}`);
```

### 7.2 Pre-commit Hook: Source/Dist Consistency

```bash
#!/usr/bin/env bash
# hooks/check-browse-ui-dist.sh
# Block commits where browse-ui/src/** changed but dist/ wasn't rebuilt.

SRC_CHANGED=$(git diff --cached --name-only -- browse-ui/src/ | head -1)
DIST_CHANGED=$(git diff --cached --name-only -- browse-ui/dist/ | head -1)

if [ -n "$SRC_CHANGED" ] && [ -z "$DIST_CHANGED" ]; then
  echo "❌ browse-ui/src/ changed but browse-ui/dist/ not updated."
  echo "   Run: cd browse-ui && pnpm build"
  echo "   Then stage dist/: git add browse-ui/dist/"
  exit 1
fi
```

### 7.3 Auto-rebuild Trigger

When `auto-update-tools.py` detects changes in `browse-ui/src/**`, it should trigger a rebuild:

```python
# Add to COVERAGE_MANIFEST in auto-update-tools.py
"Frontend": [
    ("browse-ui/src/", "Next.js UI source — triggers rebuild-ui"),
    ("browse-ui/dist/", "Pre-built UI assets"),
],
```

---

## 8. Testing Strategy

### 8.1 Unit Tests: Vitest

```typescript
// browse-ui/vitest.config.ts
import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import path from "path";

export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test-setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
    coverage: {
      provider: "v8",
      reporter: ["text", "json-summary"],
      thresholds: { lines: 70, functions: 70, branches: 60 },
    },
  },
  resolve: {
    alias: { "@": path.resolve(__dirname, "src") },
  },
});
```

**What to unit-test:**

| Target | Example |
|--------|---------|
| `lib/api/client.ts` | Token injection, error handling, 401 redirect |
| `lib/auth.ts` | URL token extraction, sessionStorage persistence |
| `lib/api/schemas.ts` | Zod schema validation: valid/invalid payloads |
| `components/data/data-table.tsx` | Rendering, sorting, empty state |
| `components/data/stat-card.tsx` | Number formatting, variants |
| `hooks/use-sse.ts` | EventSource mock, pause/resume |
| `hooks/use-density.ts` | State toggle, localStorage sync |

### 8.2 Integration Tests: Vitest (Mock Fetch)

```typescript
// src/lib/api/__tests__/hooks.test.ts
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useDashboard } from "../hooks";

const wrapper = ({ children }: { children: React.ReactNode }) => {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
};

test("useDashboard fetches and caches stats", async () => {
  globalThis.fetch = vi.fn().mockResolvedValueOnce({
    ok: true,
    status: 200,
    json: async () => ({
      totals: { sessions: 10, knowledge_entries: 50, relations: 5, embeddings: 100 },
      by_category: [],
      sessions_per_day: [],
      top_wings: [],
      red_flags: [],
      weekly_mistakes: [],
      top_modules: [],
    }),
  });

  const { result } = renderHook(() => useDashboard(), { wrapper });
  await waitFor(() => expect(result.current.isSuccess).toBe(true));
  expect(result.current.data?.totals.sessions).toBe(10);
});
```

### 8.3 E2E Tests: Playwright

```typescript
// browse-ui/playwright.config.ts
import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: true,
  retries: 1,
  use: {
    baseURL: "http://127.0.0.1:8765",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [
    { name: "chromium", use: devices["Desktop Chrome"] },
  ],
  webServer: {
    command: "python3 ../browse.py --port 8765 --db ../test-fixtures/knowledge.db",
    port: 8765,
    reuseExistingServer: true,
    timeout: 10_000,
  },
});
```

```typescript
// browse-ui/e2e/smoke.spec.ts
import { test, expect } from "@playwright/test";

test("sessions page loads and shows sessions table", async ({ page }) => {
  await page.goto("/v2/sessions");
  await expect(page.locator("h1")).toContainText("Recent Sessions");
  await expect(page.locator("table")).toBeVisible();
});

test("insights page shows KPI cards", async ({ page }) => {
  await page.goto("/v2/insights");
  await expect(page.getByText("Sessions")).toBeVisible();
  await expect(page.getByText("Knowledge Entries")).toBeVisible();
});

test("search returns results", async ({ page }) => {
  await page.goto("/v2/search");
  await page.getByPlaceholder("Search").fill("test query");
  // Wait for debounced search
  await expect(page.locator("#search-results")).not.toBeEmpty();
});
```

### 8.4 Visual Regression: Playwright Screenshots

```typescript
// browse-ui/e2e/visual.spec.ts
import { test, expect } from "@playwright/test";

const routes = [
  "/v2/sessions",
  "/v2/insights",
  "/v2/search",
  "/v2/graph",
  "/v2/settings",
];

for (const route of routes) {
  test(`visual snapshot: ${route}`, async ({ page }) => {
    await page.goto(route);
    await page.waitForLoadState("networkidle");
    await expect(page).toHaveScreenshot(`${route.replace(/\//g, "-")}.png`, {
      maxDiffPixelRatio: 0.01,
    });
  });
}
```

**Replaces:** `tests/test_visual_snapshot.py` — once all routes migrated to `/v2`, delete the Python visual tests.

### 8.5 Test File Mapping

| Python test (existing) | Vitest/Playwright replacement | When to switch |
|----------------------|------------------------------|---------------|
| `test_browse.py` | Keep for API-only tests | Ongoing |
| `test_browse_dashboard.py` | `e2e/dashboard.spec.ts` | Phase 2 |
| `test_browse_search_v2.py` | `e2e/search.spec.ts` | Phase 1 |
| `test_browse_timeline.py` | `e2e/session-timeline.spec.ts` | Phase 2 |
| `test_browse_graph.py` | `e2e/graph.spec.ts` | Phase 3 |
| `test_browse_embeddings.py` | `e2e/embeddings.spec.ts` | Phase 3 |
| `test_browse_mindmap.py` | `e2e/mindmap.spec.ts` | Phase 3 |

---

## 9. CI / Quality Gates

### 9.1 CI Pipeline

```bash
# Full quality gate (run in CI or pre-push)
cd browse-ui
pnpm typecheck && pnpm lint && pnpm test && pnpm build
```

| Stage | Command | Fail = block |
|-------|---------|:---:|
| Type check | `pnpm typecheck` (tsc --noEmit) | ✅ |
| Lint | `pnpm lint` (next lint + eslint) | ✅ |
| Unit tests | `pnpm test` (vitest run) | ✅ |
| Build | `pnpm build` (next build + post-build) | ✅ |
| E2E | `pnpm test:e2e` (playwright) | ⚠️ (advisory on local) |

### 9.2 Pre-commit Hook

Add to existing hooks framework:

```bash
#!/usr/bin/env bash
# .git/hooks/pre-commit (append)

# Check if browse-ui/src changed
if git diff --cached --name-only | grep -q "^browse-ui/src/"; then
  echo "🔍 browse-ui source changed — running checks..."
  cd browse-ui
  pnpm typecheck || exit 1
  pnpm lint || exit 1
  pnpm test || exit 1

  # Verify dist/ is up to date
  SRC_CHANGED=$(git diff --cached --name-only -- src/ | head -1)
  DIST_CHANGED=$(git diff --cached --name-only -- dist/ | head -1)
  if [ -n "$SRC_CHANGED" ] && [ -z "$DIST_CHANGED" ]; then
    echo "❌ src/ changed but dist/ not rebuilt. Run: pnpm build"
    exit 1
  fi
  cd ..
fi
```

### 9.3 Pre-push Hook

```bash
#!/usr/bin/env bash
# .git/hooks/pre-push (append)

if [ -d "browse-ui" ]; then
  cd browse-ui
  pnpm build || exit 1
  cd ..
fi
```

### 9.4 `auto-update-tools.py` COVERAGE_MANIFEST Update

```python
# Add to COVERAGE_MANIFEST dict:
<!-- FIXED in cross-review pass: MINOR-2 — changed "Frontend" to "Browse UI" to match doc 04 -->
"Browse UI": [
    ("browse-ui/src/",   "Next.js UI source — triggers rebuild-ui on update"),
    ("browse-ui/dist/",  "Pre-built UI assets — deployed as-is"),
    ("browse-ui/e2e/",   "Playwright E2E tests"),
],
```

---

## 10. Migration Order

<!-- FIXED in cross-review pass: MAJOR-1 — updated Phase 1-4 file paths to match 8-route IA; BLOCKER-1 — updated lib references -->
### Phase 1: Foundation + Core Pages (Week 1-2)

| Task | Files | Risk | Rollback |
|------|-------|------|----------|
| Scaffold Next.js project | `browse-ui/*` | Low | `rm -rf browse-ui` |
| Install shadcn/ui + Tailwind v4 | config files | Low | — |
| Root layout (sidebar, theme, palette) | `app/layout.tsx`, components/layout/* | Medium | — |
| `types.ts` + `client.ts` + `hooks.ts` | `lib/api/*` | Low | — |
| Sessions page (landing, TanStack Table) | `app/sessions/page.tsx` | Medium: table complexity | Old `/sessions` still works |
| Session Detail page (tabs) | `app/sessions/[id]/page.tsx` + layout | Low | Old `/session/{id}` still works |
| Search page | `app/search/page.tsx` | Medium: facet UX | Old `/search` still works |
| Settings page | `app/settings/page.tsx` | Low | — |
| Add "Try new UI" banner to old UI | `browse/core/templates.py` | Low | Remove banner |

**Exit criteria Phase 1:** 4 core pages render correctly, Vitest unit tests pass, `pnpm build` succeeds.

### Phase 2: Data-Dense Pages (Week 3-4)

| Task | Files | Risk | Rollback |
|------|-------|------|----------|
| Insights page (Overview + Knowledge + Retro + Search Quality + Live feed tabs, Recharts) | `app/insights/layout.tsx` + `app/insights/page.tsx` | Medium: chart integration | — |
| Timeline tab | `app/sessions/[id]/timeline/page.tsx` | High: paginated event replay | — |
| Update Python `/sessions?format=json` to return pagination envelope | `browse/routes/sessions.py` | Medium | — |
| Add `/api/eval/stats` Python endpoint | `browse/routes/eval.py` | Low | — |
| Add `/api/compare` Python endpoint | `browse/routes/session_compare.py` | Low | — |

**Exit criteria Phase 2:** Insights page with charts render, SSE live feed works, timeline slider works. Pagination envelope API deployed.

### Phase 3: Visualization Pages (Week 5-6)

| Task | Files | Risk | Rollback |
|------|-------|------|----------|
| Graph page — Insight tab (default, cross-graph summary) + Evidence tab (typed edges) + Similarity tab (neighbors primary + `/api/embeddings/points` map) + Communities tab | `app/graph/page.tsx` | High: graph lib integration | Keep old `/graph` |
| Mindmap tab (markmap) | `app/sessions/[id]/mindmap/page.tsx` | Medium: D3 integration | — |

**Exit criteria Phase 3:** All routes functional. Playwright E2E full-suite green.

### Phase 4: Polish + Cutover (Week 7-8)

| Task | Files | Risk | Rollback |
|------|-------|------|----------|
| Search Quality tab in Insights (eval feedback) | `app/insights/page.tsx` (tab) | Low | — |
| Visual regression baselines | `e2e/visual.spec.ts` | Low | — |
| Performance audit (bundle analyzer) | — | Low | — |
| Remove old UI HTML rendering | `browse/routes/*.py` | **High** | Git revert |
| Redirect `/` → `/v2/sessions` | `browse/core/server.py` | Low | Remove redirect |
| E2E on every Python test run | CI | Low | — |

**Exit criteria Phase 4:** Old UI HTML removed. All traffic on `/v2/*`. Python only serves JSON API + static files.

### Dependency Map

```
Phase 1 ─── [Foundation + Core Pages]
    │
    ├── Phase 2 ─── [Data-Dense Pages]
    │       │         depends on: layout, types, client from Phase 1
    │       │
    │       └── Phase 3 ─── [Visualization Pages]
    │               │         depends on: Python JSON endpoints from Phase 2
    │               │
    │               └── Phase 4 ─── [Polish + Cutover]
    │                                 depends on: all routes from Phase 3
    │
    └── (Python JSON endpoint additions can be done in parallel)
```

---

## 11. Performance Budget

| Metric | Target | Measurement |
|--------|--------|-------------|
| Initial JS (gzipped) | < 200 KB | `next build` output + `@next/bundle-analyzer` |
| First Contentful Paint | < 1.0s | Lighthouse on `http://localhost:8765/v2/sessions` |
| Largest Contentful Paint | < 1.5s | Lighthouse |
| Time to Interactive | < 2.0s | Lighthouse |
| Total Transfer (home page) | < 350 KB | Chrome DevTools Network |
| TanStack Table render (1000 rows) | < 100ms | React DevTools Profiler |

### Bundle Analyzer Setup

```typescript
// browse-ui/next.config.ts (conditional)
import type { NextConfig } from "next";
import bundleAnalyzer from "@next/bundle-analyzer";

const withBundleAnalyzer = bundleAnalyzer({
  enabled: process.env.ANALYZE === "true",
});

const config: NextConfig = {
  basePath: "/v2",
  output: "export",
  trailingSlash: true,
  // ...
};

export default withBundleAnalyzer(config);
```

```bash
ANALYZE=true pnpm build   # Opens interactive treemap
```

### Key Bundle Optimization Decisions

<!-- FIXED in cross-review pass: BLOCKER-1 — updated bundle table: cytoscape → react-force-graph-2d; added recharts, markmap -->
| Library | Size (gzipped) | Strategy |
|---------|:-:|-----------|
| React + React DOM | ~45 KB | Unavoidable |
| Next.js runtime | ~30 KB | Static export minimizes |
| TanStack Table | ~15 KB | Import only used modules |
| TanStack Query | ~12 KB | Tree-shakeable |
| shadcn/ui primitives | ~5 KB | Only import used components |
| Tailwind v4 CSS | ~10 KB | JIT removes unused |
| cmdk | ~5 KB | Small |
| nuqs | ~3 KB | Small |
| lucide-react | ~2 KB per icon | Tree-shake: import individual icons |
| zod | ~14 KB | Consider `valibot` (~6 KB) if budget tight |
| recharts | ~45 KB | **Lazy import** — shared across `/insights` charts |
| react-force-graph-2d | ~45 KB | **Lazy import** — only on `/graph` page |
| markmap-lib + markmap-view | ~50 KB | **Lazy import** — only on session mindmap tab |
| sonner | ~5 KB | Small |
| dompurify | ~7 KB | Security — required for any HTML rendering |
| **Total estimate** | ~140-180 KB | ✅ Under 200 KB (lazy-loaded heavy libs not in critical path) |

**Lazy loading strategy for heavy pages:**

```typescript
// app/graph/page.tsx
import dynamic from "next/dynamic";
import { Skeleton } from "@/components/ui/skeleton";

const GraphView = dynamic(() => import("@/components/graph-view"), {
  loading: () => <Skeleton className="h-[75vh] w-full" />,
  ssr: false,
});

export default function GraphPage() {
  return <GraphView />;
}
```

---

## 12. Risk Register

| # | Risk | Likelihood | Impact | Mitigation |
|---|------|:---:|:---:|-----------|
| **R1** | `dist/` check-in bloats git history | High | Medium | `.gitattributes`: `browse-ui/dist/** -diff` + `git lfs` for `_next/static/**` if >10MB. Alternatively: use `git update-index --assume-unchanged` for binary assets. Review size at Phase 1 exit gate — if >5MB, switch to CI-built artifact download instead. |
| **R2** | Static export (`output: "export"`) breaks features that need server runtime (middleware, rewrites at runtime) | Medium | High | Validated: auth is client-side, no server components need DB access, all data comes from Python API. If server features needed later → switch to `output: "standalone"` + Node.js sidecar (major arch change). |
| **R3** | Python API response shape changes break TS types silently | High | Medium | Zod runtime validation on every `apiFetch()` in development mode. CI E2E tests catch regressions. Add a `pnpm test:contract` script that hits every API endpoint and validates against Zod schemas. |
| **R4** | cytoscape.js / D3 / markmap integration with React causes memory leaks or rendering conflicts | Medium | High | Use `useEffect` cleanup. For cytoscape: use `cytoscape-react` wrapper or manual ref-based lifecycle. For D3/markmap: render into a `ref`'d `<div>`, destroy instance on unmount. Add explicit `cy.destroy()` / `markmap.destroy()` in cleanup. |
| **R5** | SSE EventSource reconnection storm on network flap | Medium | Medium | Exponential backoff in `useSSE` hook: initial 1s, max 30s. Cap at 5 retries then show "Connection lost — click to retry" banner. Python server already has `_MAX_SECONDS = 600` lifetime guard. |
| **R6** | End-user has stale `dist/` after `git pull` (Python updated but dist/ is old version) | High | Low | `version.json` contains `buildHash`. Python's `/v2/` handler can compare `version.json.buildHash` with current git HEAD and log a warning. Also: `auto-update-tools.py` checks if `browse-ui/src/` is newer than `browse-ui/dist/version.json.builtAt` and warns. |
| **R7** | Tailwind v4 breaking changes during development (v4 is relatively new) | Low | Medium | Pin exact version in `package.json`: `"@tailwindcss/postcss": "4.1.x"`. Use `pnpm --frozen-lockfile` in CI. Test Tailwind upgrade in isolated branch before merging. |
<!-- FIXED in cross-review pass: MAJOR-2 — added R8 for backend API change; MAJOR-3 — added R9 for missing endpoints -->
| **R8** | Backend API change required — Pha 5 must update Python `routes/sessions.py` to return pagination envelope (`{items, total, page, page_size, has_more}`) BEFORE Pha 6 scaffold consumes `/sessions?format=json` | High | High | Add to Pha 5 pre-flight checklist. Pha 6 scaffold can temporarily handle flat array fallback with a compat shim, but must be removed before production. |
| **R9** | `/api/eval/stats` and `/api/compare` endpoints do not exist yet — required by Insights and Compare features | Medium | High | Both are Pha 5 deliverables. Add to Pha 5 exit criteria. Frontend can show "Feature unavailable" stub until endpoints exist. |

---

## Appendix A: Key Config Files

### `tsconfig.json`

```json
{
  "compilerOptions": {
    "target": "ES2022",
    "lib": ["dom", "dom.iterable", "esnext"],
    "allowJs": true,
    "skipLibCheck": true,
    "strict": true,
    "noEmit": true,
    "esModuleInterop": true,
    "module": "esnext",
    "moduleResolution": "bundler",
    "resolveJsonModule": true,
    "isolatedModules": true,
    "jsx": "preserve",
    "incremental": true,
    "plugins": [{ "name": "next" }],
    "paths": { "@/*": ["./src/*"] }
  },
  "include": ["next-env.d.ts", "**/*.ts", "**/*.tsx", ".next/types/**/*.ts"],
  "exclude": ["node_modules", "dist"]
}
```

### `components.json` (shadcn/ui)

```json
<!-- FIXED in cross-review pass: BLOCKER-3 — changed baseColor from zinc to slate, added override note -->
{
  "$schema": "https://ui.shadcn.com/schema.json",
  "style": "new-york",
  "rsc": true,
  "tsx": true,
  "tailwind": {
    "config": "",
    "css": "src/styles/globals.css",
    "baseColor": "slate",
    "cssVariables": true
  },
  "aliases": {
    "components": "@/components",
    "utils": "@/lib/utils",
    "ui": "@/components/ui",
    "lib": "@/lib",
    "hooks": "@/hooks"
  },
  "iconLibrary": "lucide"
}
```

> **NOTE:** After `shadcn init`, the generated CSS defaults MUST be overridden with the HSL tokens from `03-visual-design.md §1`. The `globals.css` sample above already contains the correct values.
```

### `globals.css` (Tailwind v4)

```css
@import "tailwindcss";

@theme {
  --color-background: var(--background);
  --color-foreground: var(--foreground);
  --color-primary: var(--primary);
  --color-muted: var(--muted);
  --color-accent: var(--accent);
  --color-destructive: var(--destructive);
  --radius-sm: 0.25rem;
  --radius-md: 0.375rem;
  --radius-lg: 0.5rem;
}

<!-- FIXED in cross-review pass: BLOCKER-3 — replaced oklch Zinc defaults with HSL tokens from doc 03 §1 -->
<!-- See 03-visual-design.md §1 for canonical token values (HSL with primary #5E6AD2 indigo) -->

@layer base {
  :root {
    --background:           0 0% 99%;            /* near-white */
    --foreground:           215 14% 14%;
    --card:                 0 0% 100%;
    --card-foreground:      215 14% 14%;
    --popover:              0 0% 100%;
    --popover-foreground:   215 14% 14%;
    --primary:              235 56% 60%;          /* #5E6AD2 indigo */
    --primary-foreground:   0 0% 100%;
    --secondary:            210 16% 96%;
    --secondary-foreground: 215 14% 14%;
    --muted:                212 18% 93%;
    --muted-foreground:     212 7% 43%;
    --accent:               235 56% 60%;
    --accent-foreground:    0 0% 100%;
    --destructive:          358 75% 47%;
    --destructive-foreground: 0 0% 100%;
    --border:               210 16% 87%;
    --input:                210 16% 87%;
    --ring:                 235 56% 60%;
    --radius:               0.375rem;
    --chart-1:              235 56% 60%;
    --chart-2:              152 56% 48%;
    --chart-3:              33 90% 58%;
    --chart-4:              280 60% 60%;
    --chart-5:              12 80% 60%;
  }

  .dark {
    --background:           215 22% 7%;
    --foreground:           210 25% 93%;
    --card:                 215 20% 11%;
    --card-foreground:      210 25% 93%;
    --popover:              215 20% 11%;
    --popover-foreground:   210 25% 93%;
    --primary:              235 56% 69%;          /* #7B86E2 lighter indigo */
    --primary-foreground:   0 0% 100%;
    --secondary:            215 14% 15%;
    --secondary-foreground: 210 25% 93%;
    --muted:                215 14% 15%;
    --muted-foreground:     212 7% 47%;
    --accent:               235 56% 69%;
    --accent-foreground:    0 0% 100%;
    --destructive:          358 78% 63%;
    --destructive-foreground: 0 0% 100%;
    --border:               215 11% 20%;
    --input:                215 11% 20%;
    --ring:                 235 56% 69%;
    --radius:               0.375rem;
    --chart-1:              235 56% 69%;
    --chart-2:              152 50% 50%;
    --chart-3:              38 85% 55%;
    --chart-4:              280 55% 65%;
    --chart-5:              12 75% 65%;
  }
}

<!-- FIXED in cross-review pass: MINOR-1 — removed density-spacious (only compact + comfortable) -->
/* Density tokens */
:root {
  --row-height: 2.75rem;          /* comfortable: 44px */
  --row-height-comfortable: 2.75rem;  /* 44px — use this var for row height refs */
  --row-height-compact: 2rem;         /* 32px */
  --font-size-comfortable: 0.875rem;  /* 14px */
  --font-size-compact: 0.8125rem;     /* 13px */
  --cell-padding: 0.75rem 1rem;
  --sidebar-width: 16rem;
}

.density-compact {
  --row-height: 2rem;
  --cell-padding: 0.25rem 0.5rem;
  --sidebar-width: 14rem;
}
```

### `package.json` (key dependencies)

```json
{
  "name": "browse-ui",
  "version": "0.1.0",
  "private": true,
  "scripts": {
    "dev": "next dev --turbopack",
    "build": "next build && node scripts/post-build.mjs",
    "typecheck": "tsc --noEmit",
    "lint": "next lint",
    "test": "vitest run",
    "test:watch": "vitest",
    "test:e2e": "playwright test",
    "test:contract": "vitest run src/lib/api/__tests__/contract.test.ts",
    "preview": "npx serve dist",
    "analyze": "ANALYZE=true next build"
  },
<!-- FIXED in cross-review pass: MAJOR-7 — added all missing dependencies; BLOCKER-1 — added recharts, react-force-graph-2d, markmap -->
  "dependencies": {
    "next": "^15.3.0",
    "react": "^19.0.0",
    "react-dom": "^19.0.0",
    "@tanstack/react-query": "^5.60.0",
    "@tanstack/react-table": "^8.21.0",
    "cmdk": "^1.0.0",
    "lucide-react": "^0.460.0",
    "next-themes": "^0.4.4",
    "nuqs": "^2.2.0",
    "zod": "^3.24.0",
    "class-variance-authority": "^0.7.1",
    "clsx": "^2.1.1",
    "tailwind-merge": "^2.6.0",
    "recharts": "^2.15.0",
    "react-force-graph-2d": "^1.25.0",
    "markmap-lib": "^0.17.0",
    "markmap-view": "^0.17.0",
    "react-markdown": "^9.0.0",
    "rehype-sanitize": "^6.0.0",
    "dompurify": "^3.2.0",
    "sonner": "^1.7.0",
    "react-hook-form": "^7.54.0",
    "@hookform/resolvers": "^3.9.0",
    "date-fns": "^4.1.0"
  },
  "devDependencies": {
    "@tailwindcss/postcss": "^4.1.0",
    "tailwindcss": "^4.1.0",
    "typescript": "^5.7.0",
    "@types/react": "^19.0.0",
    "@types/react-dom": "^19.0.0",
    "@types/node": "^22.0.0",
    "vitest": "^3.0.0",
    "@vitejs/plugin-react": "^4.3.0",
    "@testing-library/react": "^16.0.0",
    "@testing-library/jest-dom": "^6.0.0",
    "jsdom": "^25.0.0",
    "@playwright/test": "^1.49.0",
    "@next/bundle-analyzer": "^15.3.0",
    "eslint": "^9.0.0",
    "eslint-config-next": "^15.3.0"
  }
}
```

---

## Appendix B: Root Layout Skeleton

```tsx
// src/app/layout.tsx
import type { Metadata } from "next";
import { QueryProvider } from "@/providers/query-provider";
import { ThemeProvider } from "@/providers/theme-provider";
import { AppSidebar } from "@/components/layout/app-sidebar";
import { Header } from "@/components/layout/header";
import { CommandPalette } from "@/components/layout/command-palette";
import "@/styles/globals.css";

export const metadata: Metadata = {
  title: "Hindsight",
  description: "AI session knowledge browser",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body>
        <ThemeProvider>
          <QueryProvider>
            <div className="flex h-screen overflow-hidden">
              <AppSidebar />
              <div className="flex flex-1 flex-col overflow-hidden">
                <Header />
                <main className="flex-1 overflow-y-auto p-4 md:p-6">
                  {children}
                </main>
              </div>
            </div>
            <CommandPalette />
          </QueryProvider>
        </ThemeProvider>
      </body>
    </html>
  );
}
```

---

*End of design document.*
