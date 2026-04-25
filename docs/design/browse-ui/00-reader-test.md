# Reader Test Results

> **Reviewer:** Fresh implementer (no prior context)  
> **Date:** 2025-07-18  
> **Docs read:** 01-system-architecture.md, 02-ux-information-architecture.md, 03-visual-design.md, 04-standards-tooling.md

---

## Q1: Cài deps bằng pnpm hay npm? Nếu pnpm chưa cài thì bước đầu tiên cmd là gì?

**Answer**: **pnpm** — docs nói rõ "Always use pnpm (not npm/yarn)". Nếu chưa cài:
```bash
npm i -g pnpm          # bước 1: cài pnpm toàn cục
cd browse-ui
pnpm install           # bước 2: cài deps dự án
```
Câu `npm i -g pnpm` chỉ xuất hiện dưới dạng warning text trong code Python của `post_pull_pipeline()`, không có section "Prerequisites" riêng.

**Source**: doc 4 §1.4 (warning text), §5.2 (key commands), §6.2 (`.github/.agent.md`)  
**Confidence**: MEDIUM  
**Reason**: Lệnh `npm i -g pnpm` chỉ ẩn trong warning string của Python code, không có hướng dẫn rõ ràng "Nếu bạn chưa cài pnpm, hãy làm X". Cần thêm một mục "Prerequisites" ở đầu doc.

---

## Q2: Output mode của Next.js? File `next.config.ts` có những config gì?

**Answer**: Output mode là **`"export"`** (static export). File `next.config.ts`:
```typescript
const config: NextConfig = {
  basePath: "/v2",
  output: "export",       // static export cho prod
  trailingSlash: true,
  async rewrites() {      // DEV-ONLY proxy → Python :8765
    return [
      { source: "/api/:path*",          destination: "http://127.0.0.1:8765/api/:path*" },
      { source: "/v2/proxy/sessions",   destination: "http://127.0.0.1:8765/sessions?format=json" },
      { source: "/v2/proxy/home",       destination: "http://127.0.0.1:8765/?format=json" },
      { source: "/v2/proxy/session/:id",destination: "http://127.0.0.1:8765/session/:id?format=json" },
      { source: "/healthz",             destination: "http://127.0.0.1:8765/healthz" },
    ];
  },
};
```
> **Lưu ý**: `output: "export"` trong Next.js 15 bỏ qua `rewrites()` khi build prod — rewrites chỉ hoạt động ở dev server (`pnpm dev`). Doc đã ghi chú "Dev-only" nên không phải bug, nhưng nếu dev không chú ý sẽ ngạc nhiên.

**Source**: doc 1 §3.1  
**Confidence**: HIGH

---

## Q3: Khi build xong, artifact nằm ở đâu? Python server serve nó như thế nào?

**Answer**:
- **Artifact**: `browse-ui/dist/` — checked into git (không bị `.gitignore`)
- **Python serve**: Tạo file mới `browse/core/static_v2.py`, route `/v2/*` trong `do_GET` của `server.py`:

```python
# Thêm vào do_GET TRƯỚC auth check:
if path.startswith("/v2/"):
    rel = path[len("/v2/"):]
    if rel.startswith("_next/"):          # static assets: public
        from browse.core.static_v2 import serve_v2
        body, ct, status = serve_v2(rel)
        self._send(body, ct, status, nonce)
        return
    # Page routes: tiếp tục → auth check → serve HTML
```
`serve_v2()` dùng SPA fallback: thử `{rel}.html` → `{rel}/index.html` → `index.html`.

**Source**: doc 1 §3.2, §1 (repo layout `dist/` annotation)  
**Confidence**: HIGH

---

## Q4: Có bao nhiêu top-level routes? List ra. Trang `/` redirect về đâu?

**Answer**:

**8 routes trong final route map** (doc 2 §2.3):
```
/sessions                ← Landing page (default)
/sessions/[id]           ← Session detail (4 tabs)
/search
/insights
/graph
/settings
/healthz                 ← API-only (không có UI page)
/api/*                   ← JSON endpoints
```

**5 items trong top-nav** (visible): Search, Sessions, Insights, Graph, Settings.

**`/` redirect**: `src/app/page.tsx` redirect đến `/sessions` (trong Next.js basePath `/v2`, tức `/v2/sessions`). Doc 2 §2.2: "MERGE → `/sessions`".

**Source**: doc 2 §2.3, §2.2; doc 1 §1 (repo layout `page.tsx` comment)  
**Confidence**: HIGH

---

## Q5: Strangler `/v2/*` hoạt động như thế nào với routes mới? Khi nào kill old `/`?

**Answer**:

**Cơ chế Strangler**:
- Next.js SPA chạy tại `/v2/*` (basePath = `/v2`)
- Python routes cũ vẫn sống tại `/` (HTML-rendered)
- Banner thêm vào tất cả trang Python: _"✨ Try the new UI"_ → `/v2/sessions`
- Migrate theo phase (P1 = core, P2 = secondary, P3 = advanced, P4 = admin)

**Kill schedule** (doc 1 §4.3):

| Milestone | Action |
|-----------|--------|
| Phase 2 complete | Banner đổi: "New UI is default. Old UI removed soon." |
| Phase 4 + 2 weeks soak | Xóa `browse/routes/*.py` HTML rendering, giữ JSON endpoints. `/` redirect → `/v2/sessions` |
| Phase 4 + 4 weeks | Xóa banner, đổi tên `/v2/*` → `/*` (drop `basePath`), cập nhật `next.config.ts` |

**Source**: doc 1 §4.1, §4.2, §4.3  
**Confidence**: HIGH

---

## Q6: Primary brand color HEX là gì? Dark mode CSS selector là gì?

**Answer**:
- **Primary color**: `#5E6AD2` (Linear Indigo) → `hsl(235, 56%, 60%)`
- **Dark mode CSS selector**: `.dark` (class trên `<html>`, set bởi `next-themes` với `attribute="class"`)

```css
/* Light: :root */
:root { --primary: 235 56% 60%; /* #5E6AD2 */ }

/* Dark: .dark */
.dark { --primary: 235 56% 69%; /* #7B86E2 — slightly lighter in dark */ }
```

**Consistency check**: ✅ nhất quán. Doc 3 §1.1 định nghĩa `#5E6AD2`. Doc 3 §1.8 xác nhận `.dark` selector (đã được fix từ `[data-theme="dark"]` → `.dark` trong cross-review).

**Source**: doc 3 §1.1, §1.8  
**Confidence**: HIGH

---

## Q7: Chart library cho sparkline trên dashboard tile là gì? Cho 2D scatter embeddings là gì? Cho graph relations là gì?

**Answer**:

| Use case | Library | Notes |
|----------|---------|-------|
| **Sparkline** (dashboard tile) | **Recharts** `<Sparkline>` hoặc lightweight SVG inline | Recharts = shadcn's first-party chart lib. 80×32px, no tooltip, no interaction. |
| **2D scatter embeddings** | **Canvas 2D** (custom `<ScatterCanvas>` với `useRef` + `useEffect`) | Giữ implementation hiện tại (vanilla canvas). Chỉ migrate sang deck.gl nếu vượt 10K points. |
| **Graph relations** | **Cytoscape.js** với `cytoscape-react` wrapper | Giữ implementation hiện tại. COSE layout. |

**Source**: doc 3 §8a (sparkline), §8e (scatter), §8f (graph)  
**Confidence**: HIGH

---

## Q8: Compact density: row height + font size?

**Answer**:
- **Row height**: **32px** (`--row-height: 2rem` trong `.density-compact`)
- **Font size**: **13px** (Geist Sans 400)

```css
/* Từ doc 1 §6.3 globals.css */
:root             { --row-height: 2.5rem; --cell-padding: 0.75rem 1rem; }   /* comfortable */
.density-compact  { --row-height: 2rem;   --cell-padding: 0.25rem 0.5rem; } /* compact */
```

**⚠️ Minor inconsistency phát hiện**:
- Doc 3 §9.5 nói comfortable = **44px**, nhưng CSS trong doc 1 §6.3 là `2.5rem` = **40px** (ở base 16px).
- Font-size (13px compact) không có trong CSS density tokens — chỉ được mô tả trong doc 3 §3.4. Dev cần tự thêm `--font-size-cell` vào density CSS.

**Source**: doc 3 §9.5, §3.4; doc 1 §6.3  
**Confidence**: MEDIUM  
**Reason**: Comfortable row height không nhất quán giữa doc 3 (44px) và CSS trong doc 1 (40px). Font-size không có CSS variable trong density tokens.

---

## Q9: Sessions list response shape có pagination không? Field tên gì?

**Answer**: **Có pagination**. Response shape:

```typescript
type SessionListResponse = {
  items:     SessionRow[];
  total:     number;
  page:      number;
  page_size: number;
  has_more:  boolean;
};
```

**⚠️ Critical note**: Python backend hiện tại trả về flat `SessionRow[]`, **chưa có envelope**. Doc 1 §2.2 ghi rõ:
> "NOTE: The Python endpoint currently returns a flat SessionRow[]. Pha 5 MUST update routes/sessions.py to return this envelope BEFORE Pha 6 scaffold consumes it. See Risk Register R8."

**Source**: doc 1 §2.2 (TypeScript interfaces); doc 2 §/sessions (data shape)  
**Confidence**: HIGH

---

## Q10: Knowledge entry có field `wing` không? `tool_usage` field trên session detail có không, lấy từ đâu?

**Answer**:

**`wing` field**:
- `SearchResult` (knowledge-only): có `wing?: string` (optional)
- `GraphNode`: có `wing?: string` (optional)
- `LiveEvent`: có `wing: string` (required)
- `WingCount`: có `wing: string` (dashboard stats)
- → **Có**, nhưng optional trên knowledge entries trong search/graph

**`tool_usage` field**:
- **KHÔNG có trong Python API**
- Được computed **client-side** từ `timeline[].content` bằng regex
- Doc 2 §/sessions/[id] ghi rõ:
  > "NOTE: tool_usage is NOT returned by the Python API. Compute client-side by parsing timeline[].content with regex (e.g., count occurrences of tool names like 'bash', 'edit', 'view' in doc_type/content fields). See session_detail.py HTML rendering for the regex pattern to reuse."

**Source**: doc 1 §2.2 (interfaces); doc 2 §/sessions/[id] (data shape comment)  
**Confidence**: HIGH (wing), MEDIUM (tool_usage)  
**Reason (tool_usage MEDIUM)**: Doc nói "see session_detail.py for regex pattern" nhưng không quote regex cụ thể. Dev phải tự đọc Python source để biết pattern chính xác.

---

## Q11: COVERAGE_MANIFEST trong auto-update-tools.py cần thêm key gì cho browse-ui?

**Answer**: Thêm key `"Browse UI"` với 3 entries:

```python
    "Browse UI": [
        ("browse-ui/src/",    "browse-ui Next.js source (TS + components)"),
        ("browse-ui/public/", "browse-ui static assets"),
        ("browse-ui/dist/",   "browse-ui prebuilt artifacts (checked-in)"),
    ],
```

Chèn **trước** key `"Other"`, sau line 129 hiện tại.

Ngoài ra cần thêm 2 thứ liên quan:
1. Key `"browse_ui"` trong `classify_changes()` (line 492)
2. Key `"browse_ui"` trong `changed_categories` của `write_manifest()` (line 795)

**Source**: doc 4 §1.1, §1.2, §1.3  
**Confidence**: HIGH

---

## Q12: Hook nào sẽ block edit `browse-ui/dist/`? File nào?

**Answer**:
- **Hook class**: `BlockEditDistRule`
- **File**: `hooks/rules/block_edit_dist.py` (file mới cần tạo)
- **Event**: `preToolUse`
- **Tools intercepted**: `["edit", "create"]`
- **Trigger**: path chứa `browse-ui/dist/` → trả về `deny()` với message hướng dẫn `pnpm build`
- **Đăng ký**: Thêm vào `hooks/rules/__init__.py` (import + thêm vào `ALL_RULES`)

**Source**: doc 4 §2.2, §2.5  
**Confidence**: HIGH

---

## Summary

| | Count |
|---|---|
| ✅ HIGH confidence | **9** |
| ⚠️ MEDIUM (cần clarify) | **3** |
| ❌ LOW (BLOCKER) | **0** |

**HIGH**: Q2, Q3, Q4, Q5, Q6, Q7, Q9, Q11, Q12  
**MEDIUM**: Q1, Q8, Q10

---

## Blockers (if any)

Không có câu nào LOW confidence — docs đủ để bắt đầu implement. Tuy nhiên, 3 gaps cần fix trước Pha 5:

### Gap 1 (Q1) — Thiếu "Prerequisites" section
**Vấn đề**: Lệnh cài pnpm (`npm i -g pnpm`) chỉ nằm trong warning string Python, không có section rõ ràng.  
**Fix**: Thêm section "## Prerequisites" vào đầu doc 4 hoặc `SKILL.md`:
```
- Node.js 22+
- pnpm: `npm i -g pnpm` (one-time global install)
```

### Gap 2 (Q8) — Comfortable row height không nhất quán
**Vấn đề**: Doc 3 §9.5 nói 44px, CSS trong doc 1 §6.3 là `2.5rem` = 40px.  
**Fix**: Đồng bộ một trong hai. Nếu chọn 44px → đổi CSS thành `--row-height: 2.75rem`. Nếu chọn 40px → update doc 3 §9.5.  
**Bonus gap**: Font-size cho compact (13px) chưa có CSS variable — nên thêm `--cell-font-size` vào density tokens.

### Gap 3 (Q10) — tool_usage regex pattern chưa được quote
**Vấn đề**: Doc nói "see session_detail.py for the regex pattern to reuse" nhưng không quote pattern cụ thể.  
**Fix**: Quote regex cụ thể vào doc 2 §/sessions/[id] data shape section, hoặc add snippet vào doc 1 §2.2 interface comment.

---

## Resolved gaps

Applied 2025-07-18. Three MEDIUM-confidence gaps fixed.

### Gap 1 — Q1: Prerequisites section (01-system-architecture.md)

**Added**: `## 0. Prerequisites` section at the top of `01-system-architecture.md`, before `## 1. Repo Layout`.

Content: Node ≥ 20 (recommended 24), pnpm `npm install -g pnpm@9` as single source of truth (no npm/yarn fallback), Git ≥ 2.30, plus one paragraph on why pnpm (lockfile determinism, disk savings, faster CI).

**Line ref**: `01-system-architecture.md` lines 8–44 (new §0).

---

### Gap 2 — Q8: Density CSS values consistency (01-system-architecture.md + 03-visual-design.md)

**Canonical values confirmed**: Compact = 32px / 13px / `2rem`; Comfortable = **44px** / 14px / **`2.75rem`** (was erroneously `2.5rem` = 40px).

**Changes**:
1. `01-system-architecture.md` §6.3 density summary: `2.5rem` → `2.75rem /* comfortable: 44px */` — line ~1028.
2. `01-system-architecture.md` full `globals.css` density block: `2.5rem` → `2.75rem`, added `--row-height-comfortable: 2.75rem`, `--row-height-compact: 2rem`, `--font-size-comfortable: 0.875rem`, `--font-size-compact: 0.8125rem` — lines ~1707–1722.
3. `03-visual-design.md` §10.1: added canonical CSS variables block with `:root { --row-height-compact: 2rem; --row-height-comfortable: 2.75rem; --font-size-compact: 0.8125rem; --font-size-comfortable: 0.875rem; }` plus enforcement note — lines ~715–726.

---

### Gap 3 — Q10: tool_usage derivation (02-ux-information-architecture.md)

**Added**: TypeScript `deriveToolUsage()` function with `ToolUsage` interface in `02-ux-information-architecture.md` §3 (Sessions/[id] Data shape section), replacing the vague "see session_detail.py for the regex pattern" comment.

The TS regex `/\b(edit|view|bash|grep|glob|write_bash|task|create)\s*\(/g` mirrors the Python `_TOOL_RE` from `browse/routes/session_detail.py` exactly. Also added Python source comment for traceability.

**Line ref**: `02-ux-information-architecture.md` lines ~299–325 (Data shape block).
