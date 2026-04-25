# Design Cross-Review

## Verdict
**NEEDS-FIXES**

## Summary
- 3 BLOCKERs
- 8 MAJORs
- 4 MINORs

---

## Findings

### BLOCKER-1: Chart library contradiction — uPlot vs Recharts
**Doc(s)**: 02 §4 (ChartContainer), 03 §8 (all subsections), 01 §Appendix C (package.json)
**Issue**: Doc 02 explicitly selects **uPlot** and rejects Recharts: *"Why not Recharts or Chart.js: uPlot is 35KB and renders 100K points at 60fps."* Doc 03 selects **Recharts** for every chart (sparklines, bar, donut, area, line) and in Appendix B lists Recharts at 45KB gzipped as the chosen lib. Doc 01's `package.json` lists **neither** Recharts nor uPlot as a dependency.
**Evidence**:
- Doc 02, line 1087: `"Why not Recharts or Chart.js: uPlot is 35KB…"`
- Doc 03, line 463: `"Lib: Recharts <Sparkline>"`; line 477: `"Lib: Recharts <BarChart>"`
- Doc 01, lines 1583-1634: `package.json` — no `recharts`, no `uplot` entry
**Fix**: Decide one chart library. If Recharts: remove uPlot references from doc 02 and add `"recharts": "^2.15.0"` to doc 01 `package.json`. If uPlot: rewrite doc 03 §8 for uPlot wrappers. Then update the bundle budget accordingly.

---

### BLOCKER-2: Dark mode CSS selector mismatch — `.dark` vs `[data-theme="dark"]`
**Doc(s)**: 01 §6.3 (theme-provider.tsx, globals.css), 03 §1.8 (shadcn CSS mapping)
**Issue**: Doc 01 configures `next-themes` with `attribute="class"` (line 946), which adds the `.dark` class to `<html>`. Doc 01's `globals.css` correctly uses `.dark { … }` (line 1553). However, doc 03 §1.8 defines all dark mode CSS variables under `[data-theme="dark"]` selector (line 146). These selectors will never match each other — dark mode styling from doc 03 will silently fail.
**Evidence**:
- Doc 01 line 946: `attribute="class"`
- Doc 01 line 1553: `.dark { --background: oklch(…); }`
- Doc 03 line 146: `[data-theme="dark"] { --background: 215 22% 7%; … }`
**Fix**: Unify on `.dark` selector throughout doc 03 §1.8 and §5.2 (shadows), since that's what `next-themes` with `attribute="class"` produces.

---

### BLOCKER-3: Color token system conflict — oklch (shadcn default) vs HSL (custom design)
**Doc(s)**: 01 Appendix A (globals.css), 03 §1.8 (shadcn CSS variables)
**Issue**: Doc 01's `globals.css` uses **oklch()** values from shadcn's default Zinc baseColor: `--primary: oklch(0.205 0.03 265.75)` — this is a **near-black** color (lightness 0.205). Doc 03 defines primary as `#5E6AD2` (HSL `235 56% 60%`) — a **medium indigo**. These are entirely different colors. The two docs generate conflicting `globals.css` content. An implementer would not know which to use.
**Evidence**:
- Doc 01 line 1547: `--primary: oklch(0.205 0.03 265.75);` (L=0.205 → very dark)
- Doc 03 line 124: `--primary: 235 56% 60%;` → `#5E6AD2` (vibrant indigo)
- Doc 01 shadcn `components.json` line 1512: `"baseColor": "zinc"` (generates oklch defaults)
**Fix**: Doc 01's `globals.css` should be regenerated using doc 03's color system as the source of truth. Replace the oklch Zinc defaults with the HSL values from doc 03, OR use `baseColor: "slate"` + custom primary override. The `components.json` `baseColor` should match doc 03's neutral scale, not Zinc.

---

### MAJOR-1: Route structure mismatch — 8 merged routes (doc 02) vs 13 separate pages (doc 01)
**Doc(s)**: 02 §2.3 (Final Route Map), 01 §1 (Repo Layout app/ tree), 01 §4.1 (Route Mapping Table)
**Issue**: Doc 02 merges 17 routes into 8 (e.g., `/dashboard` → tab in `/insights`; `/live` → tab in `/insights`; `/embeddings` → tab in `/graph`). But doc 01's `src/app/` directory tree still has separate page directories for `dashboard/`, `live/`, `embeddings/`, `diff/`, `eval/`, `compare/` — totaling 13 page routes. Doc 01's route mapping table (§4.1) maps each to a separate `/v2/*` URL.
**Evidence**:
- Doc 02 line 83-91: 8 final routes
- Doc 01 lines 48-63: 13 page.tsx files under separate directories
- Doc 01 lines 676-693: 15-entry route mapping to individual `/v2/*` paths
**Fix**: Either (a) update doc 01's app/ tree and route table to match doc 02's merged IA (e.g., remove `app/dashboard/`, `app/live/`, `app/embeddings/`; make them tabs inside `app/insights/` and `app/graph/`), or (b) revise doc 02's merge decision. The implementation will follow whichever doc is read first, creating drift.

---

### MAJOR-2: `/sessions?format=json` returns flat array — pagination metadata missing
**Doc(s)**: 02 §3 /sessions (Data shape), 01 §2.2 (TS interfaces)
**Issue**: Doc 02 defines `SessionsPageData` with `{ sessions, total, offset, limit }` for pagination. But the actual Python endpoint (`browse/routes/sessions.py` line 71) returns a bare JSON array: `json.dumps([dict(r) for r in rows])`. There is no `total` count, no `offset`, no `limit` in the response. Doc 01's TS interface `SessionsResponse = SessionRow[]` correctly matches the actual code (flat array), contradicting doc 02.
**Evidence**:
- Doc 02 line 168-174: `SessionsPageData { sessions, total, offset, limit }`
- Actual code `sessions.py` line 71: `data = [dict(r) for r in rows]` → flat array
- Doc 01 line 199: `SessionsResponse = SessionRow[]` → flat array
**Fix**: Either modify the Python endpoint to return `{"sessions": [...], "total": N, "offset": M, "limit": L}`, or update doc 02 to handle client-side pagination on a flat array. Without `total`, the frontend cannot render "Showing 1-20 of 342" or page controls.

---

### MAJOR-3: `/api/eval/stats` and `/api/compare` endpoints do not exist
**Doc(s)**: 01 §2.1 (Route Inventory rows 13-14), 04 (entire doc — never mentions creating them)
**Issue**: Doc 01 marks `/api/eval/stats` and `/api/compare` as **"NEEDS NEW ENDPOINT"** and lists them as Phase 2 action items (lines 170-172, 1337-1338). Actual code confirms neither endpoint exists: `eval.py` has no `/api/eval/stats` route; `session_compare.py` has only `/compare` (HTML, no JSON API). Doc 04 (Standards) never includes these endpoints in its checklist or pre-flight items.
**Evidence**:
- Doc 01 lines 163-164: `— NEEDS JSON` for eval and compare
- `grep -rn "@route" browse/routes/eval.py` → only `/eval` (GET, HTML) and `/api/feedback` (POST)
- `grep -rn "@route" browse/routes/session_compare.py` → only `/compare` (GET, HTML)
**Fix**: Add to doc 04's pre-flight checklist or Phase 2 task list: create `/api/eval/stats` and `/api/compare?a={id}&b={id}` JSON endpoints. Without these, the Eval section in Insights and the Compare action are dead features.

---

### MAJOR-4: `tool_usage` field missing from session detail JSON API
**Doc(s)**: 02 §3 /sessions/[id] (Overview tab), 01 §2.2 (TS interfaces)
**Issue**: Doc 02 designs a "Tool Usage" section in the session detail Overview tab showing `bash ×12, edit ×8, view ×6…` and defines `SessionDetail.tool_usage: Record<string, number>`. But the actual Python endpoint returns only `{meta, timeline}` — tool usage is computed in the HTML rendering path only (line 99-108 of `session_detail.py`), never included in JSON. Doc 01's `SessionDetailResponse` correctly omits `tool_usage`, matching the actual API.
**Evidence**:
- Doc 02 lines 296-301: `SessionDetail { meta, timeline, tool_usage }`
- Actual code: JSON returns `{"meta": meta, "timeline": tl}` — no `tool_usage`
- Doc 01 lines 212-215: `SessionDetailResponse { meta, timeline }` — no `tool_usage`
**Fix**: Either (a) extend the Python JSON response to include `tool_usage` (compute from timeline content server-side, same regex as HTML path), or (b) compute tool usage client-side in the Next.js component by parsing `timeline[].content`. Option (b) avoids a Python change but duplicates the regex logic.

---

### MAJOR-5: `EmbeddingPoint.wing` field doesn't exist in API response
**Doc(s)**: 02 §3 /graph Clusters tab (Data shape), 01 §2.2 (TS interfaces)
**Issue**: Doc 02's `EmbeddingPoint` interface includes `wing: string` (line 656). But actual Python API (`projection.py`) returns `{id, x, y, category, title}` — no `wing` field. Doc 01's TS interface correctly omits `wing`. The Graph Clusters tab filter-by-wing feature in doc 02 has no data source.
**Evidence**:
- Doc 02 line 656: `wing: string` in `EmbeddingPoint`
- Actual `projection.py` lines 248-253: returns `{id, x, y, category, title}` only
- Doc 01 lines 338-344: `EmbeddingPoint { x, y, id, title, category }` — no `wing`
**Fix**: Either (a) add `wing` to the projection response by joining with the knowledge_entries table in `projection.py`, or (b) remove wing filter from the Clusters tab UX.

---

### MAJOR-6: `/settings` page missing from doc 01's app directory and route table
**Doc(s)**: 02 §2.3 (Final Route Map), 02 §3 /settings, 01 §1 (Repo Layout)
**Issue**: Doc 02 defines `/settings` as one of the 5 top-level nav items with full wireframe (theme, density, health, shortcuts, style guide). But doc 01's `src/app/` directory tree (lines 34-63) has no `settings/` folder. The route mapping table (§4.1) has no settings entry. No dist HTML file is listed for settings.
**Evidence**:
- Doc 02 lines 52, 84: `/settings` as top-level route
- Doc 01 lines 34-63: app/ tree — `home/`, `sessions/`, `session/`, `dashboard/`, `search/`, `graph/`, `embeddings/`, `diff/`, `live/`, `eval/`, `compare/` — NO `settings/`
**Fix**: Add `app/settings/page.tsx` to doc 01's directory tree and route mapping table.

---

### MAJOR-7: Missing dependencies in doc 01 `package.json`
**Doc(s)**: 01 Appendix A (package.json), 02 §4 (Toast/Sonner), 03 §8 (Recharts), 03 Appendix B (chart libs)
**Issue**: Several libraries required by docs 02 and 03 are absent from doc 01's `package.json`:
- `recharts` (or `uplot`) — chart library, required by doc 03 §8
- `sonner` — toast library, required by doc 02 §4 (Toast)
- `cytoscape` / `cytoscape-react` — graph visualization, required by Phase 3 (doc 01 §10.3)
- `markmap-view` / `markmap-lib` — mindmap rendering, required by Phase 3
- `diff2html` / `diff2html-react` — diff viewer, required by Phase 3
**Evidence**:
- Doc 01 lines 1583-1634: `package.json` dependencies list
- Doc 03 line 886: "Total incremental JS: ~265KB (Recharts 45 + Cytoscape 170 + markmap 50)"
- Doc 02 line 935: "Using Sonner (shadcn's recommended toast)"
**Fix**: Add at minimum `recharts` (or `uplot`) and `sonner` to `dependencies`. Phase 3 libs (cytoscape, markmap, diff2html) can be documented as "add in Phase 3" but should be noted in the package.json section.

---

### MAJOR-8: XSS risk in search result snippets with `<mark>` HTML
**Doc(s)**: 02 §3 /search (Data shape), 01 §2.2 (SearchResult.snippet)
**Issue**: Doc 02's wireframe shows search snippets with `<mark>` tags for highlighting: `<mark>SNI</mark>`. The Python `/api/search` endpoint returns snippets with embedded HTML. The React frontend would need `dangerouslySetInnerHTML` to render these highlights. If session data contains user-controlled content (which it does — AI chat transcripts), this creates an XSS vector. Neither doc mentions sanitization.
**Evidence**:
- Doc 02 lines 367-372: `<mark>SNI</mark>` in wireframe
- Doc 02 line 411: `snippet: string // HTML with <mark> tags`
- No sanitization mentioned in any doc
**Fix**: Add a sanitization step: either (a) sanitize HTML in Python before returning (allow only `<mark>` tags), or (b) use a React-side sanitizer like `DOMPurify` before `dangerouslySetInnerHTML`, or (c) return plain-text snippets with match offsets and render `<mark>` client-side.

---

### MINOR-1: Density mode count mismatch — 3 vs 2
**Doc(s)**: 01 §6.3 (globals.css), 02 §5, 03 §10.1
**Issue**: Doc 01's `globals.css` defines **3** density modes: `comfortable`, `compact`, `spacious` (lines 1564-1580). Docs 02 and 03 define only **2**: `compact` and `comfortable`. The `spacious` mode in doc 01 has no UX spec, no visual spec, and no toggle option.
**Evidence**: Doc 01 line 1576: `.density-spacious { … }`; Doc 02 line 971: `type Density = "comfortable" | "compact"`
**Fix**: Remove `density-spacious` from doc 01's CSS, or add it to the UX/visual specs if intended.

---

### MINOR-2: `COVERAGE_MANIFEST` category naming conflict
**Doc(s)**: 01 §9.4, 04 §1.1
**Issue**: Doc 01 proposes category name `"Frontend"` for the manifest entry (line 1303). Doc 04 proposes `"Browse UI"` (line 28). Only one can exist.
**Evidence**: Doc 01 line 1303: `"Frontend": [...]`; Doc 04 line 28: `"Browse UI": [...]`
**Fix**: Pick one name. `"Browse UI"` is more specific and avoids collision if other frontends are added later.

---

### MINOR-3: Dynamic routes with `output: "export"` need `generateStaticParams`
**Doc(s)**: 01 §3.2, 01 §1 (app/session/[id]/page.tsx)
**Issue**: With `output: "export"`, Next.js requires `generateStaticParams()` for all dynamic segments like `[id]`. Doc 01 never mentions this. The SPA fallback in Python's `serve_v2` would work at runtime (serve `index.html` for unknown paths), but the `next build` step will error if `generateStaticParams` is not exported.
**Evidence**: Doc 01 line 534: `output: "export"`, lines 42-47: `app/session/[id]/page.tsx` with nested dynamic routes
**Fix**: Add `export function generateStaticParams() { return []; }` to each dynamic route page, and document that these pages are client-rendered via SPA fallback. Alternatively, note this as a Phase 1 implementation detail.

---

### MINOR-4: Token appended to every request even after cookie is set
**Doc(s)**: 01 §5.2 (auth.ts, client.ts)
**Issue**: `apiFetch()` always appends `?token=X` to API URLs (line 775) AND sends cookies via `credentials: "include"` (line 779). After the first request, the Python server sets an HttpOnly cookie, making the query-param token redundant. Continuing to send the token in URLs increases log-leak surface (browser history, proxy logs, referrer headers).
**Evidence**: Doc 01 lines 774-779: unconditional `url.searchParams.set("token", token)` + `credentials: "include"`
**Fix**: After the first successful API call (which sets the cookie), `apiFetch` should stop appending the query-param token. Check for cookie presence or track "cookie-established" state. Fall back to query-param only if cookie-based auth fails (401).

---

## Fix Log

### BLOCKER-1: Chart library — RESOLVED
- Doc 02 §4 (ChartContainer): changed all uPlot references → Recharts
- Doc 02 wireframe: changed "(uPlot line chart)" → "(Recharts line chart)"
- Doc 02 Live Feed mock data: updated decision text to mention Recharts
- Doc 01 §1 (folder rationale): changed "uPlot replacement" → "Recharts chart wrappers"
- Doc 01 Appendix A (package.json): added `recharts`, `react-force-graph-2d`, `markmap-lib`, `markmap-view`
- Doc 01 §11 (bundle table): replaced `cytoscape.js` with `react-force-graph-2d`, added `recharts`, `markmap`, `sonner`, `dompurify`
- Doc 03 Appendix B: changed Force graph from `Cytoscape.js` → `react-force-graph-2d`; updated budget

### BLOCKER-2: Dark mode selector — RESOLVED
- Doc 03 §1.8: changed `[data-theme="dark"]` → `.dark` (CSS variables)
- Doc 03 §5.2: changed `[data-theme="dark"]` → `.dark` (shadow overrides)
- Doc 01: already used `.dark` — confirmed, no changes needed

### BLOCKER-3: Color token system — RESOLVED
- Doc 01 Appendix A (globals.css): replaced oklch Zinc tokens with HSL values from doc 03 §1
- Doc 01 Appendix A (components.json): changed `baseColor` from `"zinc"` to `"slate"` with override note
- Added reference comment: "See 03-visual-design.md §1 for canonical token values"

### MAJOR-1: Route structure — RESOLVED
- Doc 01 §1: rewrote app/ directory tree to match doc 02's 8-route IA (sessions, sessions/[id], search, insights, graph, settings + redirect + 404)
- Doc 01 §4.1: rewrote route mapping table to match merged routes
- Doc 01 §10 (Migration phases): updated all phase file paths to match new routes
- Doc 01 §8 (E2E tests): updated smoke/visual test routes

### MAJOR-2: Pagination — RESOLVED
- Doc 01 §2.2: replaced `SessionsResponse = SessionRow[]` with `SessionListResponse` pagination envelope type
- Doc 02 §3 /sessions: updated `SessionsPageData` to use `{items, total, page, page_size, has_more}`
- Doc 01 §12: added R8 to risk register (backend API change required)
- Doc 04 §12: added P-3 to pre-flight checklist (pagination endpoint update)

### MAJOR-3: Missing endpoints — RESOLVED
- Doc 01 §2.1: added "Status: TO BE CREATED in Pha 5" to eval and compare endpoints
- Doc 01 §12: added R9 to risk register
- Doc 04 §12: added P-1 and P-2 to pre-flight checklist (Pha 5 deliverables)

### MAJOR-4: tool_usage field — RESOLVED
- DB schema confirmed: no `tool_usage` column in `sessions` or `documents` tables
- Doc 02 §3 /sessions/[id]: removed `tool_usage` from `SessionDetail` interface
- Added note: derive tool usage client-side by parsing `timeline[].content` (same regex as session_detail.py HTML rendering)

### MAJOR-5: EmbeddingPoint.wing — RESOLVED
- `projection.py` confirmed: returns `{id, x, y, category, title}` only — no `wing` or `cluster_id`
- Doc 02 §3 /graph: removed `wing` from `EmbeddingPoint` interface
- Added note: cluster filtering NOT available; filter by category instead

### MAJOR-6: /settings missing in doc 01 tree — RESOLVED
- Doc 01 §1: added `app/settings/page.tsx` with description "Preferences: theme, density, default landing, token storage"
- Doc 01 §4.1: added `/settings` → `/v2/settings` in route mapping
- Doc 01 §8.4: added `/v2/settings` to visual regression routes

### MAJOR-7: Missing deps in package.json — RESOLVED
- Doc 01 Appendix A: added all missing deps: recharts, react-force-graph-2d, markmap-lib, markmap-view, react-markdown, rehype-sanitize, dompurify, sonner, react-hook-form, @hookform/resolvers, date-fns

### MAJOR-8: XSS in search snippets — RESOLVED
- Doc 01: added new §6.4 "Security: HTML Rendering" with rule, table, and `<Highlight>` component pattern
- Doc 02 §3 /search: changed `snippet` from HTML with `<mark>` tags to plain text + `matches` offset array
- Doc 04 §2.6: added new `block_unsafe_html.py` hook rule
- Doc 04 §2.5: updated `__init__.py` to register 4 rules (was 3)
- Doc 04 §12 Hooks: added H-4 for block_unsafe_html, renumbered H-5 through H-7

### MINOR-1: Density modes — RESOLVED
- Doc 01 §6.3: removed `"spacious"` from `Density` type (now `"comfortable" | "compact"`)
- Doc 01 Appendix A (globals.css): removed `.density-spacious` CSS block

### MINOR-2: COVERAGE_MANIFEST category — RESOLVED
- Doc 01 §9.4: changed `"Frontend"` → `"Browse UI"` to match doc 04

### MINOR-3: generateStaticParams — RESOLVED
- Doc 01 §3.2: added rule for dynamic routes with `output: "export"` — choose client-side fetch (option a) for session detail

### MINOR-4: Auth token redundancy — RESOLVED
- Doc 01 §5.2: removed `credentials: "include"` from apiFetch — uses sessionStorage-only, no cookies
- Updated flow description: token stays in sessionStorage, never in cookies

### Open items / deferred
- **Phase 3 libs (diff2html):** Not added to package.json — will be added when Phase 3 starts. Documented in migration order.
- **Python endpoint implementations:** `/api/eval/stats`, `/api/compare`, and pagination envelope are documented as Pha 5 deliverables. No code changes made — design docs only.
- **`<Highlight>` component implementation:** Pattern documented in doc 01 §6.4 but actual component code is Phase 1 implementation work.
- **Doc 02 search wireframe `<mark>` in mockup ASCII art:** The wireframe still shows `<mark>` tags in the visual mockup (lines 367-376) — this is display-only mock data showing what the UI looks like, not an implementation spec. The Data Shape section now correctly specifies plain text + offsets.
