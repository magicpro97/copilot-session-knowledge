# 02 — UX & Information Architecture

> browse-ui: Next.js 15 + shadcn/ui + Tailwind + cmdk + TanStack Table + lucide-react

---

## 1. User Persona & Jobs-to-be-Done

### Persona: Linh — Solo Dev Maintaining AI-Augmented Workflow

| Attribute | Detail |
|-----------|--------|
| Role | Full-stack developer, sole maintainer of multiple KMP/web/infra projects |
| AI tools | Copilot CLI, Claude Code, Gemini — runs 5-20 sessions/day |
| Goal | Treat past AI sessions as a searchable second brain — avoid repeating mistakes, surface patterns, build on prior decisions |
| Behavior | Keyboard-first power user. Hates clicking through menus. Opens tool in a browser tab that stays pinned. Expects Linear/GitHub-grade UX speed |
| Pain point (verbatim) | _"Không hiểu các tính năng có tác dụng gì trong cuộc đời này"_ — too many pages with unclear purpose, no obvious information hierarchy |
| Environment | macOS, dark mode, 14" laptop + external monitor. Rarely mobile |

### 5 Jobs-to-be-Done

| # | JTBD | Frequency | Current route(s) | Pain |
|---|------|-----------|-------------------|------|
| **J1** | **Find sessions where I solved a similar problem** — search by keyword/tool/content, get to the relevant session section fast | Daily, 3-5×/day | `/search`, `/sessions`, `/` | Three different entry points for "find stuff". Search facets are confusing. No recent search history |
| **J2** | **Understand what happened in a specific session** — read timeline, see tool usage, export if needed | Daily, after J1 | `/session/{id}`, `/session/{id}/timeline`, `/session/{id}/mindmap`, `/compare`, `/session/{id}.md`, `/diff` | 6 sub-routes for one session. User must discover action buttons. Mindmap/timeline/diff are disconnected pages |
| **J3** | **See what knowledge I've accumulated recently** — review learnings, spot gaps, check for un-learned sessions | 2-3×/week | `/dashboard`, `/live` | Dashboard is stats-heavy but not actionable. Live feed is a novelty, not a workflow |
| **J4** | **Explore connections between knowledge entries** — find clusters, see which modules/wings have the most knowledge | Weekly | `/graph`, `/embeddings` | Two separate visualization pages with no explanation of when to use which. Graph is useful; embeddings scatter plot is hard to interpret |
| **J5** | **Assess health of my knowledge pipeline** — are sessions being indexed? any errors? schema OK? | On-demand (debug) | `/healthz`, `/eval`, `/style-guide` | Health is JSON-only. Eval is admin-niche. Style guide is dev-only. None help the user directly |

---

## 2. Information Architecture

### 2.1 Navigation Hierarchy

```
┌─────────────────────────────────────────────────────────────┐
│  Top nav (persistent):                                      │
│  [🔍 Search] [📋 Sessions] [📊 Insights] [🕸 Graph]  [⚙ Settings] │
│                                        [Cmd+K palette]     │
└─────────────────────────────────────────────────────────────┘
```

**5 top-level items:**

| # | Label | Icon | Route | JTBD served |
|---|-------|------|-------|-------------|
| 1 | **Search** | `Search` | `/search` | J1 |
| 2 | **Sessions** | `ScrollText` | `/sessions` | J1, J2 |
| 3 | **Insights** | `BarChart3` | `/insights` | J3, J5 |
| 4 | **Graph** | `Network` | `/graph` | J4 |
| 5 | **Settings** | `Settings` | `/settings` | J5 |

**Secondary navigation (contextual):**
- Session detail tabs: Overview → Timeline → Mindmap → Diff (within `/sessions/[id]`)
- Insights sub-tabs: Overview → Knowledge → Retro → Search Quality → Live feed (within `/insights`)

### 2.2 Sitemap — MERGE/REMOVE Decisions

| Current route | Verdict | Rationale | Data goes to |
|---------------|---------|-----------|--------------|
| `/` (home) | **MERGE → `/sessions`** | Home is just a thin wrapper showing 10 recent sessions + a search box. Duplicates `/sessions`. The sessions list page becomes the landing page. | Sessions list shows recent by default |
| `/sessions` | **KEEP as `/sessions`** | Core list page. Becomes the app's default landing. | — |
| `/session/{id}` | **KEEP as `/sessions/[id]`** | Session detail. Absorbs timeline, mindmap, diff, compare, export as tabs/actions. | — |
| `/session/{id}/timeline` | **MERGE → tab in `/sessions/[id]`** | Timeline is a view of the same session. Should be a tab, not a separate page. | Tab "Timeline" within session detail |
| `/session/{id}/mindmap` | **MERGE → tab in `/sessions/[id]`** | Mindmap is a view of the same session. Tab, not page. | Tab "Mindmap" within session detail |
| `/session/{id}.md` | **MERGE → action button in `/sessions/[id]`** | Export is an action (download), not a page to navigate to. | "Export .md" button in session detail header |
| `/compare` | **MERGE → action in `/sessions/[id]`** | Compare is an action initiated from a session, not a standalone destination. Opens as a sheet/modal overlaying current session. | "Compare…" action → sheet with side-by-side |
| `/diff` | **MERGE → tab in `/sessions/[id]`** | Diff is checkpoint-scoped within a session. Tab or sub-section of Timeline. | Tab "Checkpoints" or section within Timeline tab |
| `/dashboard` | **RENAME → `/insights`** | "Dashboard" is vague. "Insights" communicates value: trends, red flags, accumulated knowledge stats. | — |
| `/search` | **KEEP as `/search`** | Critical path for J1. Rich enough to be standalone. | — |
| `/live` | **MERGE → tab in `/insights`** | Live feed is useful but niche. Not worth a top-level slot. Sub-tab of Insights. | Tab "Live" within Insights |
| `/embeddings` | **MERGE → sub-view in `/graph` Similarity tab** | Embeddings 2D projection is an orientation map, not the primary semantic surface. Keep it inside Similarity while neighbors remain primary. | Similarity tab → "Map" sub-view |
| `/eval` | **MERGE → section in `/insights`** | Eval/feedback is search quality metrics — belongs in Insights as an expandable section. Almost no one navigates here directly. | Collapsible "Search Quality" section in Insights |
| `/graph` | **KEEP as `/graph`** | Knowledge graph is visually distinct, has its own interaction model (pan/zoom/filter). Deserves top-level. | — |
| `/healthz` | **KEEP as API-only** | Not a user-facing page. Remains `/healthz` for programmatic checks. Surface health status in Insights header instead. | Health badge in Insights header |
| `/style-guide` | **MOVE → `/settings/style-guide`** | Dev-only reference. Hide behind settings. Or remove entirely — shadcn Storybook replaces it. | Optional: accessible from Settings |
| `agents.py` | **DELETE** | Already deprecated. Import shim only. | Already merged into timeline.py |

### 2.3 Final Route Map (8 routes)

```
/sessions                    ← Landing page (was /)
/sessions/[id]               ← Session detail (tabs: Overview, Timeline, Mindmap, Checkpoints)
/search                      ← Full-text search with facets
/insights                    ← Overview + Knowledge + Retro + Search Quality + Live feed (5-tab workspace)
/graph                       ← Insight (default) + Evidence + Similarity + Communities (4-tab workspace)
/settings                    ← Theme, density, style guide (dev), system health
/healthz                     ← API-only, no UI page
/api/*                       ← All JSON endpoints preserved, no changes
```

---

## 3. Page-by-Page Design

---

## /sessions

### Mục tiêu
Primary landing page — answer J1 ("find sessions") and provide a fast entry into any session (J2).

### Layout
```
┌──────────────────────────────────────────────────────────────────┐
│ [🔍] [📋 Sessions•] [📊 Insights] [🕸 Graph] [⚙]    [⌘K]      │
├──────────────────────────────────────────────────────────────────┤
│ ┌─ Filter Sidebar (240px) ─┐  ┌─ Main ─────────────────────────┐│
│ │                           │  │ Sessions              [+ Jump] ││
│ │ Search _______________    │  │ ┌──────────────────────────────┐││
│ │                           │  │ │ 3329270b  Fixing ISP block… │││
│ │ Source                    │  │ │ copilot   42 events   2h ago│││
│ │ ☑ Copilot  ☑ Claude      │  │ ├──────────────────────────────┤││
│ │ ☑ Gemini                  │  │ │ 1b1c3003  iOS alarm limits… │││
│ │                           │  │ │ copilot   87 events   1d ago│││
│ │ Time range                │  │ ├──────────────────────────────┤││
│ │ [Today ▾]                 │  │ │ 1946775e  Docker auto-start │││
│ │                           │  │ │ copilot   23 events   3d ago│││
│ │ Has summary               │  │ ├──────────────────────────────┤││
│ │ ○ All  ● Yes  ○ No       │  │ │           ...                │││
│ │                           │  │ └──────────────────────────────┘││
│ │ Sort by                   │  │                                 ││
│ │ [Most recent ▾]           │  │ Showing 1-20 of 342    [< 1 >] ││
│ └───────────────────────────┘  └─────────────────────────────────┘│
└──────────────────────────────────────────────────────────────────┘
```

### Components dùng (shadcn list)
- `<DataTable>` (TanStack Table wrapper): sortable columns (ID, Summary, Source, Events, Time), row click → navigate to session detail. Virtualized for 1000+ rows.
- `<Input>` with `<SearchIcon>`: inline filter within sidebar, debounced 300ms client-side filter on summary text.
- `<Select>`: time range dropdown (Today / 7d / 30d / All).
- `<Checkbox>`: source filter (copilot/claude/gemini).
- `<RadioGroup>`: "Has summary" filter.
- `<Badge>` (custom `SourceBadge`): color-coded source indicator per row.
- `<Badge>` (custom `IDBadge`): monospace 8-char hex, click-to-copy.
- `<TimeRelative>`: "2h ago" with `title=ISO`.
- `<Pagination>`: page controls at bottom.
- `<Skeleton>`: table skeleton (8 rows × 5 cols) during load.

### States
- **Empty state**: Icon `ScrollText`, title "No sessions found", subtitle "Try adjusting your filters or run `build-session-index.py` to index sessions.", CTA button "Clear filters".
- **Loading**: Skeleton table with 8 rows, pulsing. Sidebar fully rendered (static).
- **Error**: `<Alert variant="destructive">` inline above table: "Failed to load sessions. Check if the browse server is running." with Retry button.

### Interactions
- `J` / `K`: move row focus down/up (vim-style).
- `Enter`: open focused session.
- `/`: focus sidebar search input.
- `G S`: go to Sessions (global).
- Click row → navigate to `/sessions/[id]`.
- Click `IDBadge` → copy full ID to clipboard, toast "Copied".
- `Ctrl+Shift+F`: open command palette pre-filled with "Go to session…".

### Data shape
```ts
interface Session {
  id: string;                    // UUID
  path: string;                  // filesystem path
  summary: string | null;        // AI-generated summary (may be empty)
  source: 'copilot' | 'claude' | 'gemini';
  event_count_estimate: number;
  fts_indexed_at: string | null; // ISO timestamp
  indexed_at_r: string | null;   // raw index timestamp
  file_mtime: string | null;
}

<!-- FIXED in cross-review pass: MAJOR-2 — added pagination envelope to SessionsPageData -->
interface SessionsPageData {
  items: Session[];
  total: number;
  page: number;
  page_size: number;
  has_more: boolean;
}
```

---

## /sessions/[id]

### Mục tiêu
Deep-dive into one session (J2) — understand what happened, extract learnings, compare with other sessions, export.

### Layout
```
┌──────────────────────────────────────────────────────────────────┐
│ [🔍] [📋 Sessions] [📊 Insights] [🕸 Graph] [⚙]    [⌘K]       │
├──────────────────────────────────────────────────────────────────┤
│ Sessions / 3329270b                                              │
│                                                                  │
│ ┌─ Header ──────────────────────────────────────────────────────┐│
│ │ Fixing ISP SNI-based filtering for screener.work              ││
│ │ copilot · 42 events · 2 hours ago            [Compare] [⬇.md]││
│ └───────────────────────────────────────────────────────────────┘│
│                                                                  │
│ [Overview] [Timeline] [Mindmap] [Checkpoints]                    │
│ ─────────────────────────────────────────────                    │
│                                                                  │
│ ┌─ Tab Content ─────────────────────────────────────────────────┐│
│ │                                                                ││
│ │  (varies by active tab — see below)                           ││
│ │                                                                ││
│ └───────────────────────────────────────────────────────────────┘│
└──────────────────────────────────────────────────────────────────┘
```

**Tab: Overview** (default)
```
│ ┌─ Summary ──────────────────────────┐ ┌─ Metadata ──────────┐│
│ │ The user reported inability to...  │ │ Source: copilot      ││
│ │ [full summary text]                │ │ Events: 42           ││
│ │                                    │ │ Path: ~/.copilot/... ││
│ └────────────────────────────────────┘ │ Indexed: 2025-01-15  ││
│                                        └──────────────────────┘│
│ ┌─ Tool Usage ────────────────────────────────────────────────┐│
│ │ bash ×12  edit ×8  view ×6  grep ×4  task ×3                ││
│ └─────────────────────────────────────────────────────────────┘│
│ ┌─ Sections (collapsed) ──────────────────────────────────────┐│
│ │ ▸ user: Initial request (312 chars)                          ││
│ │ ▸ assistant: Investigation (1.2K chars)                      ││
│ │ ▸ assistant: Root cause found (890 chars)                    ││
│ └─────────────────────────────────────────────────────────────┘│
```

**Tab: Timeline**
```
│ ┌─ Controls ──────────────────────────────────────────────────┐│
│ │ [▶ Play] [1x ▾]   Event 1 / 42                             ││
│ │ ████░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░  ←slider          ││
│ │ ■ Orchestrator  ■ Sonnet  ■ Opus  ■ Haiku                  ││
│ ├─────────────────────────────────────────────────────────────┤│
│ │ ┌─ Heatmap strip ──────────────────────────────────────────┐││
│ │ │ ██ ██ ░░ ██ ░░ ██ ██ ██ ░░ ░░ ██ ██ ░░ ██             │││
│ │ └──────────────────────────────────────────────────────────┘││
│ │ ┌─ Event detail ───────────────────────────────────────────┐││
│ │ │ Event #7 · tool · claude-sonnet-4                         │││
│ │ │ ┌───────────────────────────────────────────────────────┐│││
│ │ │ │ (content preview, syntax highlighted)                 ││││
│ │ │ └───────────────────────────────────────────────────────┘│││
│ │ └──────────────────────────────────────────────────────────┘││
│ └─────────────────────────────────────────────────────────────┘│
```

**Tab: Mindmap**
```
│ ┌─ Toolbar ───────────────────────────────────────────────────┐│
│ │ [⊕ Fit] [+ Expand] [− Collapse]            Loading...      ││
│ ├─────────────────────────────────────────────────────────────┤│
│ │                                                              ││
│ │              (SVG mindmap rendered by markmap)               ││
│ │                                                              ││
│ └─────────────────────────────────────────────────────────────┘│
```

**Tab: Checkpoints**
```
│ ┌─ Selector ──────────────────────────────────────────────────┐│
│ │ From: [Checkpoint 1 ▾]    To: [Checkpoint 3 ▾]    [Diff]   ││
│ ├─────────────────────────────────────────────────────────────┤│
│ │  +12 added  -3 removed                                      ││
│ │  ○ Side-by-side  ○ Line-by-line                             ││
│ │ ┌───────────────────────────────────────────────────────────┐││
│ │ │ (unified diff rendered by diff2html)                      │││
│ │ └───────────────────────────────────────────────────────────┘││
│ └─────────────────────────────────────────────────────────────┘│
```

### Components dùng (shadcn list)
- `<Tabs>` + `<TabsList>` + `<TabsTrigger>` + `<TabsContent>`: 4 tabs. Why Tabs not Accordion: tabs suit parallel views of same entity; accordion implies progressive disclosure of different entities.
- `<Breadcrumb>`: "Sessions / 3329270b".
- `<Badge>` (SourceBadge, IDBadge): header metadata.
- `<Button variant="outline">`: Compare, Export .md actions.
- `<Collapsible>`: sections in Overview tab.
- `<Slider>`: timeline scrubber.
- `<Select>`: play speed (1x/2x/4x), checkpoint selectors.
- `<RadioGroup>`: diff view mode (side-by-side / line-by-line).
- `<Card>`: metadata sidebar in Overview.
- `<Sheet>`: Compare action opens a side sheet with second session picker + side-by-side.
- `<Skeleton>`: full-page skeleton for initial load. Tab content skeleton on tab switch.

### States
- **Empty (no timeline)**: "No events indexed for this session. Run `build-session-index.py` to index."
- **Empty (no checkpoints)**: "No checkpoints found. Use `checkpoint-save.py` during a session to create snapshots."
- **Loading**: Skeleton in active tab content area. Header loads first (smaller query).
- **Error (404)**: Full-page "Session not found" with link back to sessions list.

### Interactions
- `1`/`2`/`3`/`4`: switch tabs (Overview/Timeline/Mindmap/Checkpoints).
- `Space`: play/pause timeline (when Timeline tab active).
- `←`/`→`: previous/next event in timeline.
- `F12`: fit mindmap to screen.
- `E`: export markdown.
- `C`: open compare sheet.
- `Backspace` or `Alt+←`: back to sessions list.

### Data shape
```ts
<!-- FIXED in cross-review pass: MAJOR-4 — removed tool_usage (not in DB); derived client-side from timeline -->
interface SessionDetail {
  meta: Session;
  timeline: TimelineEntry[];
  // NOTE: tool_usage is NOT returned by the Python API.
  // Derive client-side from TimelineEntry[].content using the pattern below,
  // which mirrors the Python regex in browse/routes/session_detail.py.
}

// Derived client-side from session.timeline:
// Scans content of all TimelineEntry items for tool call patterns via regex,
// then groups by tool name sorted by count descending.
//
// Python source (session_detail.py):
//   _TOOL_RE = re.compile(r'\b(edit|view|bash|grep|glob|write_bash|task|create)\s*\(')
//   tool_matches = _TOOL_RE.findall("\n".join(entry.content for entry in timeline))
//
function deriveToolUsage(timeline: TimelineEntry[]): ToolUsage[] {
  const TOOL_RE = /\b(edit|view|bash|grep|glob|write_bash|task|create)\s*\(/g;
  const counts = new Map<string, number>();
  const allContent = timeline.map(e => e.content ?? "").join("\n");
  for (const [, name] of allContent.matchAll(TOOL_RE)) {
    counts.set(name, (counts.get(name) ?? 0) + 1);
  }
  return Array.from(counts, ([name, count]) => ({ name, count }))
              .sort((a, b) => b.count - a.count);
}

interface ToolUsage {
  name: string;  // e.g. "bash", "edit", "view", "grep", "glob", "write_bash", "task", "create"
  count: number;
}

interface TimelineEntry {
  seq: number;
  title: string;
  doc_type: string;
  section_name: string;
  content: string;
}

interface TimelineEvent {
  event_id: number;
  kind: string;
  preview: string;
  byte_offset: number;
  file_mtime: string;
  color: string; // hex
}

interface CheckpointEntry {
  seq: number;
  title: string;
  file: string;
}

interface DiffResult {
  session_id: string;
  from: CheckpointEntry;
  to: CheckpointEntry;
  unified_diff: string;
  stats: { added: number; removed: number };
}

interface MindmapData {
  markdown: string; // heading outline
  title: string;
}
```

---

## /search

### Mục tiêu
Full-text search across sessions AND knowledge entries (J1) — the fastest path from question to answer.

### Layout
```
┌──────────────────────────────────────────────────────────────────┐
│ [🔍 Search•] [📋 Sessions] [📊 Insights] [🕸 Graph] [⚙]  [⌘K] │
├──────────────────────────────────────────────────────────────────┤
│                                                                  │
│  🔍 _______________________________________ [×]                  │
│     Recent: "sqlite FTS5"  "KMP alarm"  "Docker ISP"            │
│                                                                  │
│  ┌─ Filters (inline, collapsible) ──────────────────────────────┐│
│  │ Scope: [☑ Sessions] [☑ Knowledge]                            ││
│  │ In:    [☑ User] [☑ Assistant] [☐ Tools] [☑ Title]           ││
│  │ Kind:  [☐ Mistake] [☐ Pattern] [☐ Decision] [☐ Discovery]  ││
│  │        [☐ Tool] [☐ Feature] [☐ Refactor]                    ││
│  └──────────────────────────────────────────────────────────────┘│
│                                                                  │
│  12 results · 4ms                                                │
│                                                                  │
│  ┌─ Session ────────────────────────────────────────────────────┐│
│  │ 📋 3329270b  Fixing ISP SNI-based filtering...    copilot    ││
│  │ ...the root cause is VNPT ISP performing <mark>SNI</mark>...││
│  └──────────────────────────────────────────────────────────────┘│
│  ┌─ Knowledge ──────────────────────────────────────────────────┐│
│  │ 🧠 "Always check ISP filtering before DNS"    pattern       ││
│  │ ...discovered that <mark>ISP</mark> blocks SNI at layer 4...││
│  └──────────────────────────────────────────────────────────────┘│
│  ┌─ Session ────────────────────────────────────────────────────┐│
│  │ 📋 1946775e  Docker auto-start after reboot       copilot    ││
│  │ ...configured Docker Desktop and <mark>services</mark>...   ││
│  └──────────────────────────────────────────────────────────────┘│
│                                                                  │
└──────────────────────────────────────────────────────────────────┘
```

### Components dùng (shadcn list)
- `<Input type="search">` with `<SearchIcon>`: main search input. Autofocus on page load. Why not cmdk here: cmdk is for navigation commands; this is a dedicated search page with persistent results and facets.
- `<Badge variant="secondary">`: recent searches (stored in localStorage, max 8). Click to re-run.
- `<Checkbox>`: scope and column facets. Inline horizontal layout.
- `<Collapsible>`: filter row hidden behind "Filters" toggle when not active.
- `<Card>` (custom `SearchResultCard`): each result. Differentiated by leading icon (📋 session vs 🧠 knowledge).
- `<Skeleton>`: 4 result card skeletons during search.
- `<Badge>`: result type indicator, knowledge category badge.

### States
- **Idle (no query)**: Show recent searches as clickable chips. Subtitle: "Search across all sessions and knowledge entries."
- **Loading**: Skeleton cards (4 rows). Input shows spinner icon.
- **No results**: Icon `SearchX`, "No results for '{query}'", suggestions: "Try broader terms" / "Check if sessions are indexed".
- **Error**: Inline `<Alert>` below search input: "Search failed — server may be unrestarting."

### Interactions
- `/` (global): navigate to `/search` and focus input.
- `Escape`: clear input if focused, else navigate back.
- `↓`/`↑` or `J`/`K`: navigate between result cards.
- `Enter` on focused result: navigate to session detail or knowledge entry.
- Debounced search: 300ms after last keystroke, fires `/api/search`.
- `Ctrl+Shift+F`: toggle filter panel.

### Data shape
```ts
<!-- FIXED in cross-review pass: MAJOR-8 — search snippets use <Highlight> component, NOT raw HTML -->
interface SearchResult {
  type: 'session' | 'knowledge';
  id: string | number;
  title: string;
  snippet: string;        // Plain text (no HTML). Highlight matches are identified by offsets.
  matches?: Array<[number, number]>;  // [start, end] byte offsets for highlight ranges
  score: number;           // bm25 (negative, lower = better)
  // session-specific
  summary?: string;
  source?: string;
  // knowledge-specific
  wing?: string;
  kind?: string;           // category
}

// Rendering: use a <Highlight text={snippet} ranges={matches} /> component
// that wraps matched ranges in <mark> tags client-side.
// NEVER use dangerouslySetInnerHTML for search snippets.

interface SearchResponse {
  query: string;
  results: SearchResult[];
  total: number;
  took_ms: number;
}
```

---

## /insights

### Mục tiêu
Answer J3 ("what knowledge have I accumulated?") and J5 ("is my pipeline healthy?") — actionable overview, not just stats.

### Layout
```
┌──────────────────────────────────────────────────────────────────┐
│ [🔍] [📋 Sessions] [📊 Insights•] [🕸 Graph] [⚙]    [⌘K]      │
├──────────────────────────────────────────────────────────────────┤
│ Insights                                    🟢 Healthy · v12   │
│                                                                  │
│ [Overview] [Knowledge] [Retro] [Search Quality] [Live feed]     │
│ ──────────────────────────────────────────────────              │
│                                                                  │
│ ── Tab: Overview (default) ─────────────────────────────────────│
│                                                                  │
│ ┌──────┐ ┌──────┐ ┌──────┐ ┌──────┐                            │
│ │  342 │ │ 1.2K │ │  890 │ │  456 │                            │
│ │ Sess │ │ Know │ │ Rels │ │ Embd │                            │
│ └──────┘ └──────┘ └──────┘ └──────┘                            │
│                                                                  │
│ ┌─ Sessions/day (30d) ─────────┐ ┌─ By category ──────────────┐│
│ │  ▂▃▅▇▆▅▃▂▃▅▆▇▆▅▃▂▂▃▅▆▇▅▃▂  │ │  ██ pattern    45%         ││
│ │  (Recharts line chart)          │ │  ██ mistake    22%         ││
│ └──────────────────────────────┘ │  ██ decision   18%         ││
│                                   │  ██ discovery  10%         ││
│                                   │  ██ other       5%         ││
│                                   └────────────────────────────┘│
│                                                                  │
│ ┌─ 🚩 Red Flags ───────────────────────────────────────────────┐│
│ │ Sessions with many events but no learnings recorded:          ││
│ │ ┌────────────────────────────────────────────────────────────┐││
│ │ │ 3329270b   42 events   "Fixing ISP SNI..."               │││
│ │ │ 1b1c3003   87 events   "iOS alarm limits..."             │││
│ │ └────────────────────────────────────────────────────────────┘││
│ │ ✅ or: "No red flags — all high-event sessions have          ││
│ │       at least one learning. Nice work."                     ││
│ └──────────────────────────────────────────────────────────────┘│
│                                                                  │
│ ── Tab: Knowledge ──────────────────────────────────────────────│
│ Knowledge entry explorer with category/wing filters.            │
│                                                                  │
│ ── Tab: Retro ──────────────────────────────────────────────────│
│ Retrospective score, Trend Scout coverage panel, weekly charts. │
│                                                                  │
│ ── Tab: Search Quality ─────────────────────────────────────────│
│ Eval/feedback metrics — thumbs ratings, precision indicators.   │
│                                                                  │
│ ── Tab: Live feed ──────────────────────────────────────────────│
│                                                                  │
│ 🟢 Connected · Receiving                     [Pause]            │
│                                                                  │
│ ┌─────────────────────────────────────────────────────────────┐ │
│ │ 14:32  pattern  "Always sanitize FTS queries"   wing:core   │ │
│ │ 14:30  mistake  "Forgot to escape HTML in..."   wing:browse │ │
│ │ 14:28  decision "Use Recharts for all charts"       wing:ui     │ │
│ └─────────────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────────┘
```

### Components dùng (shadcn list)
- `<Tabs>`: Overview / Knowledge / Retro / Search Quality / Live feed.
- `<Card>` (custom `KPITile`): 4 stat tiles in a grid.
<!-- FIXED in cross-review pass: BLOCKER-1 — changed uPlot → Recharts -->
- `<ChartContainer>` (custom): wrapper for Recharts charts — handles resize, theme, loading.
- `<DataTable>`: red flags table, top modules table. Clickable rows.
- `<Badge variant="outline">` : health status badge in header ("🟢 Healthy · v12").
- `<Collapsible>`: Search Quality section (eval feedback data).
- `<Alert variant="default">`: "No red flags" success message.
- `<Skeleton>`: KPI tiles skeleton (4 boxes) + chart skeleton (2 rect blocks).
- Custom `<LiveFeedItem>`: timestamped card for each SSE event.
- `<Button variant="outline" size="sm">`: Pause/Resume toggle in Live tab.

### States
- **Empty (no data)**: "No sessions indexed yet. Run `build-session-index.py` to get started."
- **Loading**: KPI tiles as skeletons, charts as gray rectangles.
- **Error (API fail)**: `<Alert variant="destructive">` above KPI tiles.
- **Live disconnected**: Badge turns yellow "⚠ Disconnected", auto-reconnect in 5s.

### Interactions
- `G I`: global shortcut to Insights.
- `1`/`2`/`3`/`4`/`5`: switch Overview / Knowledge / Retro / Search Quality / Live feed.
- Click red flag row → navigate to session detail.
- Click KPI tile → contextual action (e.g., click "Sessions" → go to `/sessions`).
- Live feed auto-scrolls; `Pause` stops auto-scroll.

### Data shape
```ts
interface DashboardStats {
  totals: {
    sessions: number;
    knowledge_entries: number;
    relations: number;
    embeddings: number;
  };
  by_category: Array<{ name: string; count: number }>;
  sessions_per_day: Array<{ date: string; count: number }>;
  top_wings: Array<{ wing: string; count: number }>;
  red_flags: Array<{
    session_id: string;
    events: number;
    summary: string;
  }>;
  weekly_mistakes: Array<{ week: string; count: number }>;
  top_modules: Array<{ module: string; count: number }>;
}

interface LiveFeedEvent {
  id: number;
  category: string;
  title: string;
  wing: string;
  room: string;
  created_at: string;
}

interface HealthStatus {
  status: 'ok' | 'degraded' | 'error';
  schema_version: number;
  sessions: number;
}
```

---

## /graph

### Mục tiêu
Explore connections between knowledge entries (J4) with three distinct questions:
- **Evidence**: What proven/derived relations exist?
- **Similarity**: What is semantically close, even without explicit relations?
- **Communities**: What higher-level themes emerge once evidence + similarity are trustworthy?

### Layout
```
┌──────────────────────────────────────────────────────────────────┐
│ [🔍] [📋 Sessions] [📊 Insights] [🕸 Graph•] [⚙]    [⌘K]      │
├──────────────────────────────────────────────────────────────────┤
│ Knowledge Graph                                                  │
│                                                                  │
│ [Insight] [Evidence] [Similarity] [Communities]                  │
│ ──────────────────────────────────────────────                   │
│                                                                  │
│ ── Tab: Insight (default) ──────────────────────────────────────│
│ Cross-graph summary: node/edge counts, truncation warnings,      │
│ relation-type health, community count, embedding coverage.       │
│ Actionable findings cards (e.g. missing data, density alerts).   │
│                                                                  │
│ ── Tab: Evidence ───────────────────────────────────────────────│
│ ┌─ Filters (sidebar) ──┐ ┌─ Canvas ────────────────────────────┐│
│ │                       │ │                                      ││
│ │ Wing                  │ │      ●──────●                        ││
│ │ ☑ core                │ │     / \    / \                       ││
│ │ ☑ browse              │ │    ●   ●──●   ●                     ││
│ │ ☑ hooks               │ │   / \        / \                    ││
│ │                       │ │  ●   ●      ●   ●                   ││
│ │ Category              │ │                                      ││
│ │ ☑ mistake  ☑ pattern  │ │  (Cytoscape.js graph)               ││
│ │ ☑ decision            │ │                                      ││
│ │ ☑ discovery           │ └──────────────────────────────────────┘│
│ │                       │ [Status: N nodes, M edges; source=     ││
│ │ Relation type         │  knowledge_relations]                  ││
│ │ ☑ SAME_SESSION        │                                        │
│ │ ☑ RESOLVED_BY         │ ┌─ Detail panel ─────────────────────┐ │
│ │ ☑ TAG_OVERLAP         │ │ Selected entry                      │ │
│ │ ☐ SAME_TOPIC*         │ │ - metadata                           │ │
│ │                       │ │ - connected edges (type + confidence)│ │
│ │ [Show manual overlay] │ │ [Open in Search] [Open session]      │ │
│ │ [Reset] [Fit]         │ └──────────────────────────────────────┘ │
│ │                       │                                          │
│ │ *Only enabled when data│                                          │
│ │ exists in source DB    │                                          │
│ │                       │                                        │
│ └───────────────────────┘                                        │
│                                                                  │
│ ── Tab: Similarity ─────────────────────────────────────────────│
│ [View: Neighbors | Map]                                          │
│                                                                  │
│ Neighbors view (primary): ranked kNN list per selected entry     │
│ Map view (secondary): `/api/embeddings/points` orientation map   │
│                                                                  │
│ ── Tab: Communities ────────────────────────────────────────────│
│ Community cards + detail panel.                                  │
│ Shown as trustworthy summary layer after evidence + similarity.   │
│ Singleton/noise groups are not presented as first-class themes.   │
└──────────────────────────────────────────────────────────────────┘
```

### Components dùng (shadcn list)
- `<Tabs>`: Insight / Evidence / Similarity / Communities.
- `<Checkbox>`: wing and category filters in Evidence sidebar.
- `<Checkbox>`: relation-type filters (`SAME_SESSION`, `RESOLVED_BY`, `TAG_OVERLAP`, `SAME_TOPIC` when available).
- `<ScrollArea>`: sidebar scrollable when many wings.
- `<Card>`: detail panel (Evidence node detail, Similarity selection detail, Communities detail).
- `<Button variant="link">`: "Open in Search" / "Open session" navigation.
- `<Select>`: category filter for Similarity surfaces.
- `<Tooltip>`: hover tooltip on graph/scatter points.
- `<Skeleton>`: canvas placeholder rectangle during data load.

### States
- **Evidence empty**: "No evidence relationships found in `knowledge_relations`. Run `extract-knowledge.py` then reload."
- **Similarity empty**: "No embedding data available. Run `embed.py` then reload."
- **Communities empty**: "No communities detected yet. Communities appear after evidence + similarity are sufficiently connected."
- **Loading**: Tabs render; active panel shows centered spinner/skeleton.

### Interactions
- Mouse: pan/zoom evidence graph canvas. Click node → show detail in sidebar.
- `F`: fit graph to viewport.
- `R`: reset filters.
- `1` / `2` / `3` / `4`: switch Insight / Evidence / Similarity / Communities.
- Hover similarity map point → tooltip with title + category.
- Click similarity map point or neighbor entry → highlight, show detail.
- `V` keyboard shortcut is intentionally **not** part of the frozen contract.

### Data shape
```ts
interface GraphNode {
  id: string;            // "e-123" or "ent-abc123"
  kind: 'entry' | 'entity';
  label: string;
  wing?: string;
  room?: string;
  category?: string;
  color: string;         // hex
}

interface GraphEdge {
  source: string;
  target: string;
  relation: string;      // legacy /api/graph only
}

interface GraphData {
  nodes: GraphNode[];
  edges: GraphEdge[];
  truncated: boolean;
}

interface EvidenceEdge {
  source: string;
  target: string;
  relation_type: 'SAME_SESSION' | 'RESOLVED_BY' | 'TAG_OVERLAP' | 'SAME_TOPIC';
  confidence: number;    // 0..1
}

interface EvidenceGraphData {
  nodes: GraphNode[];    // evidence path expects entry nodes
  edges: EvidenceEdge[];
  truncated: boolean;
  meta: {
    edge_source: 'knowledge_relations';
    relation_types: string[];
  };
}

<!-- FIXED in cross-review pass: MAJOR-5 — removed wing field (not in projection.py API response) -->
interface EmbeddingPoint {
  id: number;
  x: number;
  y: number;
  category: string;
  title: string;
  // NOTE: no wing or cluster_id field — projection.py returns {id, x, y, category, title} only.
  // Cluster filtering is NOT available. Filter by category instead.
}

interface SimilarityResult {
  entry_id: number;
  neighbors: Array<{ id: number; title: string; category: string; score: number }>;
}

interface SimilarityResponse {
  results: SimilarityResult[];
  meta: { method: 'cosine_knn'; k: number };
}

interface CommunitySummary {
  id: string;
  entry_count: number;
  top_categories: Array<{ name: string; count: number }>;
  representative_entries: Array<{ id: number; title: string; category: string }>;
}
```

### Intentionally unresolved (must not be invented during implementation)
- Exact request format for similarity retrieval (query params vs body) is unresolved; implementation must remain **bulk-friendly**.
- SAME_TOPIC is kept in enum for forward compatibility but must not be presented as a normal working filter unless data exists.
- Communities algorithm choice remains open; output must avoid singleton-noise and stay deterministic.

---

## /settings

### Mục tiêu
System configuration and dev tools (J5 secondary) — theme, density, health status, style guide reference.

### Layout
```
┌──────────────────────────────────────────────────────────────────┐
│ [🔍] [📋 Sessions] [📊 Insights] [🕸 Graph] [⚙ Settings•] [⌘K]│
├──────────────────────────────────────────────────────────────────┤
│ Settings                                                         │
│                                                                  │
│ ┌─ Appearance ──────────────────────────────────────────────────┐│
│ │ Theme:   [☀ Light] [🌙 Dark] [🖥 System]                     ││
│ │ Density: [Compact] [Comfortable]                               ││
│ └───────────────────────────────────────────────────────────────┘│
│                                                                  │
│ ┌─ System Health ───────────────────────────────────────────────┐│
│ │ Status: 🟢 OK                                                 ││
│ │ Schema version: 12                                             ││
│ │ Total sessions: 342                                            ││
│ │ DB path: ~/.copilot/session-state/knowledge.db                ││
│ │ Last indexed: 2 hours ago                                     ││
│ └───────────────────────────────────────────────────────────────┘│
│                                                                  │
│ ┌─ Keyboard Shortcuts ─────────────────────────────────────────┐│
│ │ (full shortcut table, same as Section 6 below)                ││
│ └───────────────────────────────────────────────────────────────┘│
│                                                                  │
│ ▸ Style Guide (developer reference)                              │
│                                                                  │
└──────────────────────────────────────────────────────────────────┘
```

### Components dùng (shadcn list)
- `<ToggleGroup>`: theme selector (Light/Dark/System).
- `<ToggleGroup>`: density selector (Compact/Comfortable).
- `<Card>`: system health section.
- `<Badge>`: health status indicator.
- `<Table>`: keyboard shortcuts reference.
- `<Collapsible>`: style guide section (expand to show component gallery).
- `<Separator>`: between sections.

### States
- **Loading**: skeleton for health section only.
- **Error**: health section shows `<Alert variant="destructive">` with error details.

### Interactions
- Theme change: immediate, persisted to `localStorage`.
- Density change: immediate, persisted to cookie (body class toggle).
- `?`: global shortcut to show keyboard shortcuts (opens this page or a dialog).

### Data shape
```ts
interface AppSettings {
  theme: 'light' | 'dark' | 'system';
  density: 'compact' | 'comfortable';
}

// HealthStatus reused from /insights
```

---

## 4. Cross-Cutting Components

---

## AppShell

**What**: Root layout component — persistent nav bar, sidebar slot, main content area.

**Why**: Every page needs consistent navigation, command palette access, and theme/density context. Without a shell, each page re-implements nav differently (current problem: each route builds its own `base_page`).

**Where used**: All pages (root `layout.tsx`).

**Behavior contract**:
```ts
interface AppShellProps {
  children: React.ReactNode;
  sidebar?: React.ReactNode;          // optional left sidebar (Graph filters, Sessions filters)
  sidebarDefaultOpen?: boolean;       // default: true on desktop, false on mobile
  breadcrumbs?: BreadcrumbItem[];     // auto-rendered in header area
}
```
- Nav items highlight based on current `pathname` (usePathname).
- Sidebar collapses to icon-only rail on `Ctrl+B`.
- Mobile (<768px): sidebar becomes a slide-over `<Sheet>`.
- Renders `<CommandPalette>` globally.
- Applies density class (`density-compact` | `density-comfortable`) on `<body>`.

**Why not `<Sidebar>` from shadcn alone**: shadcn's Sidebar is a primitive. AppShell composes it with the top nav, breadcrumbs, and command palette into a cohesive layout. The shell enforces consistency — individual pages just fill slots.

---

## CommandPalette

**What**: Global command palette triggered by `⌘K` (or `Ctrl+K`), built on `cmdk`.

**Why**: Power user needs instant access to any page, any action, without mousing. Current app has fragmented palette commands injected per-page via `window.__paletteCommands`. Command palette centralizes this.

**Where used**: AppShell (global).

**Behavior contract**:
```ts
interface Command {
  id: string;
  title: string;
  section: 'Navigate' | 'Actions' | 'Search' | 'Settings';
  icon?: LucideIcon;
  shortcut?: string[];         // display only — actual handler is in key listener
  handler: () => void;
  keywords?: string[];         // extra fuzzy match terms
}
```
- Groups: Navigate (Go to Sessions, Insights, etc.), Actions (Export, Compare), Search (recent queries), Settings (Theme, Density).
- Fuzzy matching on title + keywords.
- Max 10 visible results; scroll for more.
- `Escape` closes. Typing immediately filters.
- Recent commands surfaced first (persisted in localStorage, max 5).

**Why not shadcn `<Command>` directly**: We ARE using shadcn Command (which wraps cmdk). This component adds: command registry, section grouping, recent tracking, and global keyboard listener. It's a composition layer, not a replacement.

---

## SessionRow

**What**: Table row component for the sessions list — displays one session as a dense, scannable row.

**Why**: Sessions list is the most-visited view. Each row must be information-dense yet scannable at a glance. Current HTML table rows are plain text — no visual hierarchy.

**Where used**: `/sessions` DataTable, search results (session type), Insights red flags table.

**Behavior contract**:
```ts
interface SessionRowProps {
  session: Session;
  isSelected?: boolean;
  onSelect?: (id: string) => void;
  density: 'compact' | 'comfortable';
}
```
- Cells: `<IDBadge>` (8-char, click-to-copy) | Summary (line-clamped 1-2 lines) | `<SourceBadge>` | Event count | `<TimeRelative>`.
- Hover: row background highlight (`muted` shade).
- Selected: left border accent color.
- Click anywhere on row (except IDBadge) → navigate to session detail.
- Keyboard focus: visible focus ring on row.

**Why not just TanStack Table default cells**: TanStack Table renders raw cells. SessionRow encapsulates the visual design (badges, clamp, relative time) as a reusable unit. It also handles the click-to-navigate pattern uniformly.

---

## DataTable

**What**: Wrapper around TanStack Table + shadcn Table primitives — sortable, filterable, paginated, optionally virtualized.

**Why**: 3 pages need tables (Sessions, Insights red flags, Eval). Without a shared wrapper, each page re-implements sort/filter/pagination differently.

**Where used**: `/sessions`, `/insights` (red flags, top modules), `/settings` (shortcuts).

**Behavior contract**:
```ts
interface DataTableProps<T> {
  data: T[];
  columns: ColumnDef<T>[];
  pagination?: { pageSize: number; pageIndex: number };
  sorting?: SortingState;
  onSortingChange?: (sorting: SortingState) => void;
  filterValue?: string;
  onFilterChange?: (value: string) => void;
  emptyState?: React.ReactNode;
  virtualize?: boolean;          // enable @tanstack/react-virtual for 500+ rows
  density: 'compact' | 'comfortable';
  onRowClick?: (row: T) => void;
  stickyHeader?: boolean;        // default true
}
```
- Sort: click column header. ▲/▼ indicator. Multi-sort with Shift+click.
- Filter: external filter input (passed as prop, not built-in).
- Pagination: `<Pagination>` component at bottom. Page size selector (20/50/100).
- Virtualization: off by default. Enabled when `virtualize=true` and data length > 200. Uses `@tanstack/react-virtual` for windowed rendering.
- Sticky header: header stays visible during scroll.

**Why not AG Grid or similar**: AG Grid is 300KB+ and enterprise-focused. TanStack Table is headless (8KB), pairs naturally with shadcn's Table primitives, and gives full control over rendering. For a single-user tool, this is ideal.

---

## FilterSidebar

**What**: Left sidebar with stacked filter controls — checkboxes, selects, radio groups — for narrowing data views.

**Why**: Sessions list and Graph both need multi-faceted filtering. A shared component ensures consistent layout, collapse behavior, and keyboard shortcuts.

**Where used**: `/sessions` (source, time, summary filters), `/graph` (wing, category filters).

**Behavior contract**:
```ts
interface FilterSidebarProps {
  sections: FilterSection[];
  onFilterChange: (filters: Record<string, string[]>) => void;
  collapsible?: boolean;          // default true on mobile
}

interface FilterSection {
  id: string;
  label: string;
  type: 'checkbox' | 'select' | 'radio';
  options: Array<{ value: string; label: string; count?: number }>;
  defaultValues?: string[];
}
```
- Each section is a `<Collapsible>` with `<CollapsibleTrigger>`.
- Shows option counts when provided (e.g., "Copilot (234)").
- "Clear all" button per section.
- Responsive: full sidebar (240px) on desktop, `<Sheet>` on mobile.

**Why not shadcn `<Sidebar>` alone**: shadcn Sidebar handles the slide-in/out layout but not the filter logic. FilterSidebar is a domain-specific composition of Collapsible + Checkbox/Select/Radio that emits structured filter state.

---

## EmptyState

**What**: Centered placeholder shown when a data view has zero results.

**Why**: 6+ pages have empty states. Without a shared component, each writes ad-hoc HTML. Consistent empty states improve discoverability (clear CTAs tell user what to do next).

**Where used**: Every page with data: Sessions, Search, Insights, Graph, Session detail tabs.

**Behavior contract**:
```ts
interface EmptyStateProps {
  icon: LucideIcon | string;     // Lucide icon or emoji fallback
  title: string;                 // e.g., "No sessions found"
  description?: string;          // e.g., "Try adjusting your filters..."
  action?: {
    label: string;               // "Clear filters" or "Run build-session-index.py"
    onClick?: () => void;
    href?: string;
  };
}
```
- Centered vertically and horizontally in parent container.
- Icon: 48px, muted color. Title: text-lg, font-medium. Description: text-sm, muted.
- Action button: `<Button variant="outline">`. Optional.

**Why not just a `<div>` with text**: Consistency. Every empty state needs the same vertical centering, icon sizing, and optional CTA pattern. A component enforces this and prevents per-page divergence.

---

## SkeletonLoaders

**What**: Pre-shaped loading placeholders that match the final layout of each content type.

**Why**: Better perceived performance than a spinner. Skeletons show the user what shape the content will take, reducing layout shift and cognitive load during load.

**Where used**: All pages.

**Behavior contract**:
```ts
// Composed from shadcn <Skeleton> primitive
function TableSkeleton({ rows = 8, cols = 5 }: { rows?: number; cols?: number }): JSX.Element;
function KPITileSkeleton({ count = 4 }: { count?: number }): JSX.Element;
function ChartSkeleton(): JSX.Element;
function SearchResultSkeleton({ count = 4 }: { count?: number }): JSX.Element;
function SessionDetailSkeleton(): JSX.Element;
```
- Each skeleton matches the exact dimensions of its resolved state.
- Uses shadcn `<Skeleton>` primitive (animated pulse).
- Density-aware: compact skeletons use smaller heights.

**Why not spinners**: Spinners offer no spatial information. Skeletons reduce perceived load time by 20-40% (per Nielsen Norman Group research). For a data-heavy tool, this matters.

---

## Toast (Sonner)

**What**: Non-blocking notification system using Sonner (shadcn's recommended toast).

**Why**: Actions like "Copied ID", "Export complete", "Filter applied" need transient feedback without interrupting workflow.

**Where used**: Global (AppShell mounts `<Toaster>`).

**Behavior contract**:
```ts
// Using sonner API directly
toast("ID copied to clipboard");
toast.success("Exported session as .md");
toast.error("Failed to load sessions", { description: "Server unreachable" });
```
- Position: bottom-right (doesn't overlap sidebar or nav).
- Duration: 3s default, 5s for errors.
- Max 3 visible toasts; older ones auto-dismiss.
- Keyboard: `Escape` dismisses all toasts.

**Why not shadcn `<Toast>` (Radix)**: shadcn docs themselves recommend Sonner over the Radix-based Toast for most use cases. Sonner has simpler API (`toast()` function call vs. imperative `useToast` hook), built-in stacking, and better animations. For a single-user app, Sonner's simplicity wins.

---

## ThemeToggle + DensityToggle

**What**: Two toggle controls for visual preferences — theme (light/dark/system) and density (compact/comfortable).

**Why**: Dark mode is essential for a dev tool. Density lets the same user switch between "scan lots of data" (compact) and "read deeply" (comfortable).

**Where used**: Settings page (primary), also accessible via CommandPalette.

**Behavior contract**:
```ts
// Theme: next-themes provider
function ThemeToggle(): JSX.Element;  // 3-way: light/dark/system
// Density: CSS class on <html>
function DensityToggle(): JSX.Element; // 2-way: compact/comfortable
```
- Theme: persisted via `next-themes` (localStorage). Applies `dark` class on `<html>`.
- Density: persisted via cookie (`density=compact|comfortable`). Applies `density-compact` or `density-comfortable` class on `<html>`.
- Command palette commands: "Toggle dark mode", "Switch to compact density".

**Why not a single "Preferences" dialog**: Preferences dialogs add clicks. These toggles are 2 controls total — a dialog is overkill. They live in Settings page but are also instant-accessible from command palette.

---

## SearchInput

**What**: Autocomplete search input with recent searches and fuzzy matching.

**Why**: Search is the #1 JTBD. The input needs to feel instant — show recent searches on focus, provide suggestions as you type.

**Where used**: `/search` (main input), FilterSidebar (quick filter), CommandPalette (search mode).

**Behavior contract**:
```ts
interface SearchInputProps {
  value: string;
  onChange: (value: string) => void;
  onSubmit: (value: string) => void;
  placeholder?: string;
  recentSearches?: string[];       // from localStorage
  autoFocus?: boolean;
  debounceMs?: number;             // default 300
}
```
- On focus with empty value: show recent searches as dropdown.
- On typing: debounced `onChange` fires. Parent decides whether to show autocomplete.
- `Enter`: fires `onSubmit`. `Escape`: blur input.
- Clear button (`×`) appears when input has value.

**Why not cmdk for search**: cmdk is optimized for command selection (pick one from list → close). Search needs persistent results with facets that stay visible. SearchInput feeds into a search results page, not a dismiss-on-select popup.

---

## Breadcrumb

**What**: Hierarchical path indicator showing current location in the app.

**Why**: With session detail having 4 tabs, and insights having sub-tabs, users need to know where they are and have a way back. Current app has no breadcrumbs — users get lost.

**Where used**: AppShell (rendered from `breadcrumbs` prop), especially in session detail.

**Behavior contract**:
```ts
interface BreadcrumbItem {
  label: string;
  href?: string;            // clickable if href provided
  icon?: LucideIcon;
}
// Example: [{ label: "Sessions", href: "/sessions" }, { label: "3329270b" }]
```
- Uses shadcn `<Breadcrumb>` + `<BreadcrumbItem>` + `<BreadcrumbSeparator>`.
- Last item is not a link (current page).
- Separator: `/` character. Why not `>`: `/` matches URL path semantics.

**Why not browser back button alone**: Back button doesn't show hierarchy. Breadcrumbs are spatial — they show "Sessions > 3329270b > Timeline" even if the user arrived via search, not from sessions list.

---

## KPITile

**What**: Single metric display tile — large number, label, optional trend indicator.

**Why**: Dashboard needs at-a-glance metrics. KPI tiles are the standard pattern for this. Current `stat_grid` is plain HTML.

**Where used**: `/insights` dashboard tab.

**Behavior contract**:
```ts
interface KPITileProps {
  value: string | number;
  label: string;
  icon?: LucideIcon;
  trend?: { direction: 'up' | 'down' | 'flat'; percentage: number };
  onClick?: () => void;       // e.g., click "342 Sessions" → go to /sessions
}
```
- Layout: icon top-left, large value center, label below, trend bottom-right.
- Hover: slight lift shadow if `onClick` provided (interactive affordance).
- Responsive: 4 tiles in 1 row on desktop, 2×2 on tablet, stacked on mobile.

**Why not shadcn `<Card>` directly**: Card is a container. KPITile has a specific layout (value hierarchy, trend indicator) that Card doesn't prescribe. Using Card as the container but adding the value/label/trend structure on top.

---

## ChartContainer

<!-- FIXED in cross-review pass: BLOCKER-1 — changed uPlot → Recharts -->
**What**: Responsive wrapper for Recharts charts — handles resize, theme-aware colors, loading state.

**Why**: Dashboard has 3 charts. Each needs consistent sizing, dark mode colors, and a loading skeleton. Without a wrapper, each chart re-implements ResizeObserver and theme detection.

**Where used**: `/insights` dashboard tab (sessions/day, by category, mistakes/week).

**Behavior contract**:
```ts
interface ChartContainerProps {
  title: string;
  description?: string;
  loading?: boolean;
  error?: string;
  children: React.ReactNode;     // Recharts component
  height?: number;               // default 200
}
```
- Wraps child in a `<Card>` with title.
- `ResizeObserver` on container → passes width to child chart.
- Loading: shows `<ChartSkeleton>` (gray rect).
- Error: shows inline `<Alert>`.
- Theme: provides CSS custom properties for chart colors.

<!-- FIXED in cross-review pass: BLOCKER-1 — replaced uPlot rationale with Recharts -->
**Why Recharts**: Recharts is React-native (declarative JSX API), tree-shakeable, and shadcn's first-party chart library. At ~45KB gzipped (shared across all charts), it provides excellent value. For our dataset sizes (≤10K points), SVG-based rendering is performant enough. The declarative API integrates naturally with React's rendering model.

---

## TabBar

**What**: Horizontal tab navigation within a page — for session detail sub-views and insights sub-pages.

**Why**: Session detail has 4 views (Overview/Timeline/Mindmap/Checkpoints). Tabs keep them all accessible without leaving the page. Current app uses separate routes — lost context, extra page loads.

**Where used**: `/sessions/[id]` (4 tabs), `/insights` (2 tabs), `/graph` (2 tabs).

**Behavior contract**:
```ts
// Uses shadcn <Tabs> directly with conventions:
// - defaultValue from URL hash or first tab
// - URL hash sync: clicking tab updates URL hash (#timeline, #mindmap)
// - Keyboard: 1-9 number keys switch tabs (when not in input)
// - Tab content lazy-loads (data fetched on tab activation, not all at once)
```

**Why not separate pages with nav links**: Tabs preserve header context (session metadata always visible). Separate pages require re-rendering the header, re-fetching session data, and losing scroll position. Tabs are the standard pattern for "multiple views of the same entity."

---

## TimeRelative

**What**: Displays relative time ("2h ago", "3d ago") with full ISO timestamp on hover.

**Why**: Absolute timestamps are unreadable at scanning speed. Relative time is instantly parseable. But users sometimes need exact timestamps — hover tooltip provides this.

**Where used**: Sessions list, session detail header, live feed, search results.

**Behavior contract**:
```ts
interface TimeRelativeProps {
  datetime: string;              // ISO 8601
  className?: string;
}
```
- Renders `<time datetime={iso} title={iso}>{relative}</time>`.
- Updates every 60s (via `setInterval` in a shared context, not per-component).
- Granularity: "just now" (<1m), "Xm ago" (<1h), "Xh ago" (<24h), "Xd ago" (<30d), "Mon DD" (>30d).

**Why not a library like `date-fns/formatDistanceToNow`**: We DO use the same logic but wrapped in a component for: (a) the `<time>` semantic element, (b) the title tooltip, (c) shared update interval. `date-fns` or a 10-line custom function handles the actual formatting.

---

## SourceBadge

**What**: Color-coded badge indicating session source — copilot (blue), claude (orange), gemini (purple).

**Why**: At scanning speed, color differentiates faster than reading text. Users working with multiple AI tools need to instantly see which tool a session came from.

**Where used**: Sessions list, session detail header, search results.

**Behavior contract**:
```ts
interface SourceBadgeProps {
  source: 'copilot' | 'claude' | 'gemini' | string;
}

const SOURCE_COLORS = {
  copilot: { bg: 'bg-blue-100 dark:bg-blue-900', text: 'text-blue-700 dark:text-blue-300' },
  claude:  { bg: 'bg-orange-100 dark:bg-orange-900', text: 'text-orange-700 dark:text-orange-300' },
  gemini:  { bg: 'bg-purple-100 dark:bg-purple-900', text: 'text-purple-700 dark:text-purple-300' },
};
```
- Renders as shadcn `<Badge variant="secondary">` with custom background/text color.
- Unknown sources: gray fallback.
- Size adapts to density mode.

**Why not a plain text label**: Color is a pre-attentive visual attribute — the eye processes it before reading text. In a table with 20+ rows, color-coded badges create scannable vertical lanes.

---

## IDBadge

**What**: Monospace 8-character hex session ID with click-to-copy.

**Why**: Session IDs are UUIDs — too long to display. 8-char prefix is unique enough for identification. Click-to-copy saves the user from selecting text.

**Where used**: Sessions list, session detail breadcrumb/header, search results, red flags.

**Behavior contract**:
```ts
interface IDBadgeProps {
  id: string;                    // full UUID
  chars?: number;                // default 8
}
```
- Renders first `chars` characters in `font-mono text-xs`.
- Background: `bg-muted` (subtle differentiation from surrounding text).
- Click: copies full ID to clipboard, shows toast "Copied {id8}".
- `title` attribute shows full UUID on hover.
- Cursor: `cursor-copy`.

**Why not showing the full UUID**: UUIDs are 36 characters. In a table, they dominate the row and push other columns out. 8-char prefix has <0.001% collision probability for <10K sessions — effectively unique.

---

## 5. Density Modes

### Compact (default)

| Property | Value |
|----------|-------|
| Table row height | 32px |
| Base font size | 13px |
| Badge padding | 2px 6px |
| Card padding | 12px |
| Spacing scale | 0.875× default |

### Comfortable

| Property | Value |
|----------|-------|
| Table row height | 44px |
| Base font size | 14px |
| Badge padding | 4px 8px |
| Card padding | 16px |
| Spacing scale | 1× default |

### Implementation

```css
/* globals.css */
html.density-compact {
  --row-height: 32px;
  --base-font: 13px;
  --card-padding: 0.75rem;
  --badge-py: 2px;
  --badge-px: 6px;
}

html.density-comfortable {
  --row-height: 44px;
  --base-font: 14px;
  --card-padding: 1rem;
  --badge-py: 4px;
  --badge-px: 8px;
}
```

### Toggle location
1. **Settings page** (`/settings`): primary location, always visible.
2. **Command palette**: "Switch to compact density" / "Switch to comfortable density".
3. **Persistence**: cookie `density=compact|comfortable` (server-readable for SSR, avoids flash).

---

## 6. Keyboard Shortcuts (Global)

| Shortcut | Action | Scope | Convention source |
|----------|--------|-------|-------------------|
| `⌘ K` / `Ctrl+K` | Open command palette | Global | GitHub, Linear, Vercel |
| `/` | Focus search input (or navigate to `/search`) | Global (when not in input) | GitHub |
| `G S` | Go to Sessions | Global | GitHub (`G` then `S`) |
| `G I` | Go to Insights | Global | GitHub |
| `G G` | Go to Graph | Global | GitHub |
| `G ,` | Go to Settings | Global | GitHub |
| `?` | Show keyboard shortcuts help | Global | GitHub, Gmail |
| `J` / `K` | Navigate down/up in lists | List views | Vim, GitHub |
| `Enter` | Open selected item | List views | Universal |
| `Escape` | Close modal/sheet/palette, clear search | Global | Universal |
| `1`-`4` | Switch tabs (when in tabbed view) | Session detail, Insights, Graph | Linear |
| `E` | Export current session as .md | Session detail | Custom |
| `C` | Open compare sheet | Session detail | Custom |
| `Space` | Play/pause timeline | Session detail → Timeline tab | Media convention |
| `←` / `→` | Previous/next timeline event | Session detail → Timeline tab | Media convention |
| `F` | Fit graph/mindmap to viewport | Graph, Mindmap | Figma |
| `Ctrl+B` | Toggle sidebar | Global | VS Code |
| `Ctrl+Shift+F` | Toggle search filters | Search page | VS Code |

### Implementation notes
- All shortcuts registered via a single `useHotkeys` hook (from `@mantine/hooks` or custom).
- Shortcuts disabled when focus is in `<input>`, `<textarea>`, or `<select>` (except `Escape`).
- `G` shortcuts use two-key sequence: press `G`, then within 500ms press the second key.
- Shortcut hints shown in command palette next to each command.

---

## 7. Empty / Loading / Error States Pattern

Every data fetch has 4 states. Unified pattern across the app:

### State Machine

```
idle → loading → success
                → error
```

### Patterns by state

| State | UI Pattern | Component | Duration |
|-------|------------|-----------|----------|
| **Idle** | Content area empty, no fetch initiated | — | Until user action |
| **Loading** | Skeleton matching final layout shape | `<TableSkeleton>`, `<KPITileSkeleton>`, etc. | Typically <500ms |
| **Success** | Render data | Page-specific | — |
| **Error** | Inline alert above content area | `<Alert variant="destructive">` | Until retry or nav |

### Loading pattern detail
- **Tables**: 8-row skeleton with pulsing cells matching column widths.
- **Charts**: Gray rectangle matching chart height.
- **KPI tiles**: 4 skeleton boxes in grid.
- **Search results**: 4 card skeletons.
- **Session detail**: header skeleton (1 line title + 2 metadata badges) + tab content skeleton.
- Show skeletons **immediately** — no spinner delay. If data arrives in <100ms, React batches and user never sees skeleton.

### Error pattern detail
```tsx
<Alert variant="destructive">
  <AlertCircle className="h-4 w-4" />
  <AlertTitle>Failed to load sessions</AlertTitle>
  <AlertDescription>
    Server returned an error. <Button variant="link" onClick={retry}>Retry</Button>
  </AlertDescription>
</Alert>
```
- Errors are inline (above the content area), NOT toasts. Toasts are for transient feedback (copy, export). Errors need to persist until resolved.
- Include a Retry button/link.
- If the entire page fails (e.g., 500 from API), show a full-page error with "Go home" link.

### Empty state pattern detail
- Uses `<EmptyState>` component (Section 4).
- Context-specific icon + message + optional CTA.
- Never show an empty table with just column headers — replace entirely with EmptyState.

---

## 8. Accessibility Checklist

### WCAG 2.1 AA Minimum

| Requirement | Implementation |
|-------------|----------------|
| **Focus ring** | All interactive elements have visible `:focus-visible` ring (2px solid, `ring-ring` Tailwind class). Never remove outline. |
| **Color contrast** | Text: minimum 4.5:1 ratio against background. Large text (18px+): 3:1. Use Tailwind's built-in accessible palette. Verify with axe DevTools. |
| **Keyboard navigation** | Every action reachable via keyboard. Tab order follows visual order. No keyboard traps. `Escape` always closes modals/sheets/palettes. |
| **Screen reader** | Semantic HTML: `<nav>`, `<main>`, `<aside>`, `<table>`, `<time>`. ARIA labels on icon-only buttons. `aria-current="page"` on active nav item. |
| **ARIA roles** | `role="tablist"` / `role="tab"` / `role="tabpanel"` on tab components (shadcn handles this). `aria-live="polite"` on search results count and live feed. |
| **Reduced motion** | Respect `prefers-reduced-motion`: disable skeleton pulse, chart animations, sidebar transitions. CSS: `@media (prefers-reduced-motion: reduce) { * { animation: none !important; } }` |
| **Image/icon alternatives** | All `<img>` have `alt`. Decorative icons: `aria-hidden="true"`. Functional icons (buttons): `aria-label` on parent button. |
| **Form labels** | Every `<input>` has an associated `<label>`. Placeholder is NOT a substitute for label. Use `sr-only` class for visually hidden labels if needed. |
| **Error announcements** | Form validation errors: `aria-describedby` linking input to error message. `role="alert"` on dynamically appearing errors. |
| **Data tables** | Use `<th scope="col">` for headers. Sortable columns: `aria-sort="ascending|descending|none"`. |
| **Skip navigation** | "Skip to main content" link as first focusable element (hidden until focused). |

---

## 9. Mobile Responsiveness

### Breakpoints

| Breakpoint | Width | Behavior |
|------------|-------|----------|
| `sm` | ≥640px | — |
| `md` | ≥768px | Sidebar becomes `<Sheet>` slide-over |
| `lg` | ≥1024px | Full sidebar visible |
| `xl` | ≥1280px | Wider main content area |

### Responsive rules

| Element | < 768px (mobile) | ≥ 768px (tablet+) | ≥ 1024px (desktop) |
|---------|-------------------|--------------------|---------------------|
| **Nav** | Bottom tab bar (5 icons, no labels) | Top nav bar with labels | Top nav bar with labels |
| **Sidebar** | Hidden. Trigger via hamburger → `<Sheet>` | Collapsible rail (icons only) | Full sidebar (240px) |
| **DataTable** | Card layout: each row becomes a stacked card | Horizontal table, fewer columns | Full table with all columns |
| **KPI tiles** | 2×2 grid | 4×1 row | 4×1 row |
| **Charts** | Full width, stacked vertically | 2-column grid | 2-column grid |
| **Graph canvas** | Full viewport height, no sidebar | Sidebar below canvas | Sidebar beside canvas |
| **Tabs** | Scrollable horizontal tab bar | Standard tab bar | Standard tab bar |
| **Command palette** | Full-screen overlay | Centered dialog (640px max-width) | Centered dialog (640px max-width) |

### Table → Card transformation (mobile)

```
Desktop row:
┌──────────────────────────────────────────────────────┐
│ 3329270b │ Fixing ISP block... │ copilot │ 42 │ 2h  │
└──────────────────────────────────────────────────────┘

Mobile card:
┌──────────────────────────────┐
│ 3329270b        copilot  2h │
│ Fixing ISP SNI-based        │
│ filtering for screener.work  │
│ 42 events                    │
└──────────────────────────────┘
```

### Implementation note
- Use Tailwind responsive prefixes (`md:`, `lg:`) for layout shifts.
- DataTable receives `isMobile` from a `useMediaQuery` hook and switches between `<Table>` and card layout.
- This is a **secondary** concern — the user is a power user on desktop. Mobile is "check something quick on phone" use case. Don't over-engineer.
