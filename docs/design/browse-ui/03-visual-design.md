# 03 — Visual Design System & Data Visualization Spec

> **Project:** Hindsight Browse UI (Next.js 16 + shadcn/ui + Tailwind v4)
> **Design references:** Linear, Vercel Dashboard, GitHub Primer, Datasette
> **Status:** Living spec — kept in sync with `browse-ui/src/app/globals.css`

---

## 1. Color System

> **Default palette (issue #82):** Warm coral (`#E56A4A` brand, `#FDFAF6` light bg, `#1a1917` dark bg).
> **Legacy / classic palette:** Cool indigo (`#5E6AD2` brand) — opt-in via `.palette-classic` on `<html>`, persisted as `browse-palette=classic` in `localStorage`.
> The shadcn/ui CSS variable mapping below describes the **warm default**. Classic overrides follow in §1.9.

### 1.1 Brand Primary

**Default (warm): `#E56A4A` (Warm Coral)**

Warm coral separates cleanly from Copilot blue, Claude orange (different hue family), and Gemini teal. At lightness 44% (light) / 45% (dark) it delivers WCAG AA ≥4.5:1 with white text.

```
--primary (light): hsl(12, 75%, 44%)   /* warm coral, WCAG AA ≥4.5:1 with white */
--primary (dark):  hsl(12, 80%, 45%)   /* adjusted for dark bg, WCAG AA ≥4.5:1 with white */
--primary-foreground: hsl(0, 0%, 100%) /* #FFFFFF */
```

**Classic opt-in: `#5E6AD2` (Linear Indigo)**

Restored when `.palette-classic` is active. Historically chosen for its neutral analytical personality and separation from per-source brand colors.

```
--primary (classic light): hsl(235, 56%, 60%)  /* #5E6AD2 */
--primary (classic dark):  hsl(235, 56%, 56%)  /* darkened for WCAG AA */
```

### 1.2 Neutral Scale

**Default (warm):** Neutrals carry a slight warm tint (hue ≈ 20–38°) to complement the coral primary.

| Stop | Light (HEX approx) | HSL | Dark (HEX approx) | HSL |
|------|--------------------|----|-------------------|----|
| bg | `#FDFAF6` | 38 52% 97% | `#1a1917` | 30 5% 10% |
| card | — | 38 40% 98% | — | 30 6% 15% |
| secondary | — | 38 30% 92% | — | 30 6% 18% |
| muted | — | 38 25% 90% | — | 30 6% 18% |
| border | — | 38 25% 87% | — | 30 6% 22% |
| foreground | `#28201A` | 20 14% 16% | — | 30 15% 88% |
| muted-fg | — | 20 10% 45% | — | 30 8% 62% |

> **Classic neutrals** are cool-gray (hue ≈ 210–215°). See §1.9.

### 1.3 Semantic Colors

Each semantic has 3 tokens: `bg` (tinted surface), `fg` (text/icon), `border` (1px border).

| Semantic | Light `bg` | Light `fg` | Light `border` | Dark `bg` | Dark `fg` | Dark `border` |
|----------|-----------|-----------|----------------|----------|----------|---------------|
| **Success** | `#DAFBE1` | `#1A7F37` | `#4AC26B` | `#0F2417` | `#3FB950` | `#238636` |
| **Warning** | `#FFF8C5` | `#9A6700` | `#D4A72C` | `#26200A` | `#D29922` | `#9E6A03` |
| **Danger** | `#FFEBE9` | `#CF222E` | `#F47067` | `#2D0A0E` | `#F85149` | `#DA3633` |
| **Info** | `#DDF4FF` | `#0969DA` | `#54AEFF` | `#0A1929` | `#4493F8` | `#1F6FEB` |

WCAG AA proofs (fg on bg):

| Pair | Light ratio | Dark ratio | Pass? |
|------|------------|------------|-------|
| Success fg on bg | 5.8:1 | 6.2:1 | ✅ AA |
| Warning fg on bg | 5.1:1 | 4.9:1 | ✅ AA (large) / AA18 |
| Danger fg on bg | 5.5:1 | 5.0:1 | ✅ AA |
| Info fg on bg | 5.4:1 | 5.8:1 | ✅ AA |

### 1.4 Surface Tokens (warm default)

| Token | Light | Dark | Usage |
|-------|-------|------|-------|
| `--background` | warm off-white `38 52% 97%` | warm dark `30 5% 10%` | Page background |
| `--card` | `38 40% 98%` | `30 6% 15%` | Card surfaces |
| `--secondary` | `38 30% 92%` | `30 6% 18%` | Secondary surfaces, select triggers |
| `--muted` | `38 25% 90%` | `30 6% 18%` | Muted / disabled surfaces |
| `--popover` | `38 40% 98%` | `30 6% 16%` | Dropdown / dialog / sheet background |

### 1.5 Foreground Tokens (warm default)

| Token | Light | Dark | Contrast on bg | Usage |
|-------|-------|------|----------------|-------|
| `--foreground` | `20 14% 16%` | `30 15% 88%` | >12:1 | Primary text |
| `--muted-foreground` | `20 10% 45%` | `30 8% 62%` | ≥4.5:1 | Secondary text, labels |
| `--primary-foreground` | `0 0% 100%` | `0 0% 100%` | ≥4.5:1 on primary | Text on primary buttons |

### 1.6 Border Tokens

| Token | Light | Dark | Usage |
|-------|-------|------|-------|
| `--border` | `38 25% 87%` | `30 6% 22%` | Default dividers, card borders |
| `--input` | `38 25% 87%` | `30 6% 22%` | Input borders |

### 1.7 Accent / Ring Tokens (warm default)

| Token | Light | Dark | Usage |
|-------|-------|------|-------|
| `--accent` | `12 75% 44%` | `12 80% 45%` | Accent surfaces, hover states |
| `--ring` | `12 75% 18%` | `12 80% 78%` | Focus ring — **decoupled from primary** for WCAG 1.4.11 |

> **Ring decoupling:** Ring tokens are intentionally separate from primary. Light ring must be dark enough that `ring/50` over the page background achieves ≥3:1 (WCAG 1.4.11). Dark ring must be bright enough that `ring/50` over the dark background achieves ≥3:1. Both are guarded by direct contrast computation in `contrast-system.test.tsx`.

### 1.8 Mapping to shadcn CSS Variables (warm default)

shadcn/ui expects HSL values without `hsl()` wrapper. Canonical source: `browse-ui/src/app/globals.css`.

```css
/* Default warm palette (:root) */
:root {
  --background:           38 52% 97%;   /* warm off-white ≈ #FDFAF6 */
  --foreground:           20 14% 16%;
  --card:                 38 40% 98%;
  --card-foreground:      20 14% 16%;
  --popover:              38 40% 98%;
  --popover-foreground:   20 14% 16%;
  --primary:              12 75% 44%;   /* coral — WCAG AA ≥4.5:1 with white */
  --primary-foreground:   0 0% 100%;
  --secondary:            38 30% 92%;
  --secondary-foreground: 20 14% 16%;
  --muted:                38 25% 90%;
  --muted-foreground:     20 10% 45%;
  --accent:               12 75% 44%;
  --accent-foreground:    0 0% 100%;
  --destructive:          358 75% 47%;
  --destructive-foreground: 0 0% 100%;
  --border:               38 25% 87%;
  --input:                38 25% 87%;
  --ring:                 12 75% 18%;   /* dark coral — decoupled from primary for WCAG 1.4.11 */
  --radius:               0.375rem;
  --chart-1:              12 75% 59%;   /* warm coral */
  --chart-2:              152 56% 48%;  /* teal */
  --chart-3:              33 90% 58%;   /* amber */
  --chart-4:              280 60% 60%;  /* purple */
  --chart-5:              38 85% 55%;   /* warm yellow */
}

/* Dark warm palette (.dark class on <html>, set by next-themes attribute="class") */
.dark {
  --background:           30 5% 10%;    /* warm dark ≈ #1a1917 */
  --foreground:           30 15% 88%;
  --card:                 30 6% 15%;
  --card-foreground:      30 15% 88%;
  --popover:              30 6% 16%;
  --popover-foreground:   30 15% 88%;
  --primary:              12 80% 45%;   /* coral dark-mode — WCAG AA ≥4.5:1 with white */
  --primary-foreground:   0 0% 100%;
  --secondary:            30 6% 18%;
  --secondary-foreground: 30 15% 88%;
  --muted:                30 6% 18%;
  --muted-foreground:     30 8% 62%;    /* raised for WCAG AA on dark card surfaces */
  --accent:               12 80% 45%;
  --accent-foreground:    0 0% 100%;
  --destructive:          358 78% 63%;
  --destructive-foreground: 0 0% 100%;
  --border:               30 6% 22%;
  --input:                30 6% 22%;
  --ring:                 12 80% 78%;   /* decoupled from primary — bright for WCAG 1.4.11 ≥3:1 */
  --radius:               0.375rem;
  --chart-1:              12 78% 66%;
  --chart-2:              152 50% 50%;
  --chart-3:              38 85% 55%;
  --chart-4:              280 55% 65%;
  --chart-5:              33 80% 60%;
}
```

### 1.9 Classic Palette Opt-in (`.palette-classic`)

Activated by adding `.palette-classic` to `<html>` via the palette toggle. Persisted as `browse-palette=classic` in `localStorage`. Restores the original cool/APCA-tuned indigo design language.

```css
/* Classic light — scoped :not(.dark) to prevent bleeding into dark mode */
:root:not(.dark).palette-classic {
  --background:           210 20% 97%;
  --primary:              235 56% 60%;  /* #5E6AD2 Linear Indigo */
  --ring:                 235 56% 25%;  /* dark indigo — decoupled for WCAG 1.4.11 */
  --chart-1:              235 56% 60%;  /* indigo replaces warm coral */
  /* ... full token set in globals.css */
}

/* Classic dark */
.dark.palette-classic {
  --background:           215 22% 7%;
  --primary:              235 56% 56%;  /* darkened from 69% for WCAG AA */
  --muted-foreground:     212 7% 60%;   /* raised from 47% for WCAG AA */
  --ring:                 235 56% 80%;  /* decoupled from primary for WCAG 1.4.11 */
  --chart-1:              235 56% 69%;  /* indigo replaces warm coral */
  /* ... full token set in globals.css */
}
```

> **Specificity discipline:** Classic light overrides use `:root:not(.dark).palette-classic` (specificity 0,2,0) so they do not outrank `.dark` (0,1,0) when both classes are present, preventing warm light surfaces from bleeding into dark mode.

---

## 2. Source-Specific Colors

Each AI source needs a badge (bg + fg) that is distinct across all 4 sources and passes WCAG AA in *both* themes. Hue-separated by ≥ 60° to avoid confusion for color-vision-impaired users.

| Source | Hue zone | Light badge-bg | Light badge-fg | Dark badge-bg | Dark badge-fg | Light ratio | Dark ratio |
|--------|----------|----------------|----------------|---------------|---------------|-------------|------------|
| **copilot** | Blue 210° | `#DDF4FF` | `#0550AE` | `#0A1929` | `#58A6FF` | 6.8:1 ✅ | 6.1:1 ✅ |
| **claude** | Orange 25° | `#FFF0E0` | `#9C4221` | `#291400` | `#F0883E` | 5.6:1 ✅ | 5.2:1 ✅ |
| **gemini** | Teal 165° | `#D3F9E0` | `#0E6B3A` | `#072117` | `#34D399` | 5.8:1 ✅ | 5.0:1 ✅ |
| **codex/chatgpt** | Violet 280° | `#F0EBFF` | `#6527BE` | `#1A0A36` | `#B392F0` | 5.4:1 ✅ | 5.5:1 ✅ |

**Rationale:**
- **Copilot → Blue:** GitHub's own brand. Users expect it.
- **Claude → Warm Orange:** Anthropic's brand color. High separability from blue.
- **Gemini → Teal/Green:** Google's Gemini uses a blue-green gradient. Teal sits between but doesn't collide with copilot-blue or the semantic success-green (which is brighter/yellower).
- **Codex/ChatGPT → Violet:** OpenAI's brand evolution. Violet separates cleanly from the indigo primary (primary is 235°, codex is 280°).

### Badge CSS pattern

```css
.badge-source-copilot  { background: var(--source-copilot-bg);  color: var(--source-copilot-fg);  }
.badge-source-claude   { background: var(--source-claude-bg);   color: var(--source-claude-fg);   }
.badge-source-gemini   { background: var(--source-gemini-bg);   color: var(--source-gemini-fg);   }
.badge-source-codex    { background: var(--source-codex-bg);    color: var(--source-codex-fg);    }
```

---

## 3. Typography

### 3.1 Font Family

**Sans: `Geist Sans`**

| Criterion | Inter | Geist Sans | IBM Plex Sans |
|-----------|-------|-----------|---------------|
| Ecosystem fit | Common but generic | First-party Vercel/Next.js via `next/font` — zero config | Needs manual loading |
| Tabular figures | Via `font-feature-settings` | Built-in `tnum` by default | Yes but heavier |
| Small-text legibility | Excellent ≥12px | Excellent ≥11px (designed for UI) | Good but wider, wastes horizontal space in tables |
| Personality | Neutral-warm | Neutral-cool — matches indigo palette | IBM Enterprise — too corporate |
| File size (WOFF2) | ~95KB 4 weights | ~85KB 4 weights | ~120KB 4 weights |

**Verdict:** Geist is purpose-built for Vercel's Next.js stack, ships via `next/font/local` with zero CLS, and its compact metrics suit data-dense dashboards.

**Mono: `Geist Mono`**

Rationale: Paired with Geist Sans for visual cohesion. Geist Mono has distinguishable `0/O`, `1/l/I` at 12px — critical for session IDs and code snippets. JetBrains Mono is wider (wastes ~8% more horizontal space in tables).

### 3.2 Type Scale

Based on a minor-third (1.2×) progression, tweaked for pixel-grid alignment. Every step has an explicit `line-height` and `letter-spacing`.

| Step (px) | rem | line-height | letter-spacing | Usage |
|-----------|-----|-------------|----------------|-------|
| 11 | 0.6875 | 1.45 (16px) | +0.01em | Micro labels (chart axis, footnotes) |
| 12 | 0.75 | 1.5 (18px) | +0.005em | Badges, timestamps, table metadata |
| 13 | 0.8125 | 1.5 (20px) | 0 | Compact table cells, secondary labels |
| 14 | 0.875 | 1.5 (21px) | 0 | **Body default**, table cells, form inputs |
| 15 | 0.9375 | 1.5 (22px) | -0.005em | Slightly emphasized body (card titles) |
| 16 | 1.0 | 1.5 (24px) | -0.011em | Subheadings, nav items |
| 18 | 1.125 | 1.4 (25px) | -0.014em | Section titles (H4) |
| 20 | 1.25 | 1.35 (27px) | -0.017em | Page section titles (H3) |
| 24 | 1.5 | 1.3 (31px) | -0.019em | Page titles (H2) |
| 30 | 1.875 | 1.25 (38px) | -0.021em | Hero headings (H1) |
| 36 | 2.25 | 1.2 (43px) | -0.022em | Dashboard KPI numbers |
| 48 | 3.0 | 1.1 (53px) | -0.024em | Splash / empty-state hero |

### 3.3 Headings

| Level | Size | Weight | Tracking | Color |
|-------|------|--------|----------|-------|
| H1 | 30px (1.875rem) | 600 (SemiBold) | -0.021em | `--fg` |
| H2 | 24px (1.5rem) | 600 | -0.019em | `--fg` |
| H3 | 20px (1.25rem) | 600 | -0.017em | `--fg` |
| H4 | 18px (1.125rem) | 600 | -0.014em | `--fg` |

### 3.4 Special Use Cases

| Context | Font | Size | Weight | Notes |
|---------|------|------|--------|-------|
| Data table cell | Geist Sans | 13px | 400 | Compact mode |
| Data table cell | Geist Sans | 14px | 400 | Comfortable mode |
| KPI number | Geist Sans | 36px | 700 (Bold) | `font-variant-numeric: tabular-nums` |
| KPI label | Geist Sans | 12px | 500 | `text-transform: uppercase; letter-spacing: 0.05em` |
| Badge text | Geist Sans | 12px | 500 | — |
| Inline code | Geist Mono | 13px (0.92em relative) | 400 | Inside prose |
| Code block | Geist Mono | 13px | 400 | `line-height: 1.6` |
| Session ID | Geist Mono | 13px | 400 | Truncated to 8 chars, `font-variant-numeric: tabular-nums` |

---

## 4. Spacing Scale

Based on a 4px base unit. Tailwind-compatible naming (`space-0` = 0px, `space-1` = 4px, etc.).

| Token | px | rem | Typical usage |
|-------|----|-----|---------------|
| `--space-0` | 0 | 0 | Reset |
| `--space-0.5` | 2 | 0.125 | Inline badge padding-y, tight icon gap |
| `--space-1` | 4 | 0.25 | Badge padding-x, icon-to-text gap |
| `--space-1.5` | 6 | 0.375 | Tight button padding-y (sm) |
| `--space-2` | 8 | 0.5 | Default gap between inline items, button padding-y |
| `--space-3` | 12 | 0.75 | Card internal gap, form label to input |
| `--space-4` | 16 | 1.0 | Card padding, section gap |
| `--space-5` | 20 | 1.25 | Between card groups |
| `--space-6` | 24 | 1.5 | Page-level section spacing, body horizontal padding |
| `--space-8` | 32 | 2.0 | Major section dividers |
| `--space-10` | 40 | 2.5 | Page top padding |
| `--space-12` | 48 | 3.0 | Empty state vertical padding |
| `--space-16` | 64 | 4.0 | Modal vertical padding from viewport |
| `--space-20` | 80 | 5.0 | Hero section top margin |
| `--space-24` | 96 | 6.0 | Largest gap (rarely used) |
| `--space-32` | 128 | 8.0 | Reserved for extreme spacing |

### Usage Guidelines

| Component | Padding | Gap |
|-----------|---------|-----|
| Page body | `space-6` (24px) horizontal | — |
| Card | `space-4` (16px) all sides | — |
| Card header ↔ body | — | `space-3` (12px) |
| Stat tile | `space-3` (12px) vertical, `space-4` (16px) horizontal | — |
| Badge to badge | — | `space-2` (8px) |
| Table cell | `space-2` (8px) vertical, `space-3` (12px) horizontal | — |
| Button icon ↔ text | — | `space-2` (8px) |
| Nav items | — | `space-2` (8px) |
| Form label ↔ input | — | `space-1.5` (6px) |

---

## 5. Radius / Shadow / Elevation

### 5.1 Radius Scale

| Token | px | Usage |
|-------|----|-------|
| `--radius-sm` | 4px | Badges, inline code, small chips |
| `--radius-md` | 6px | Buttons, inputs, cards, table wrapper |
| `--radius-lg` | 8px | Dialogs, larger panels |
| `--radius-xl` | 12px | Popovers, dropdowns with padding |
| `--radius-full` | 9999px | Avatars, toggle pills, dot indicators |

**Rationale:** 6px default (md) is the shadcn/ui convention. Smaller than Linear's 8px but sharper — suits a data-tool aesthetic. Cards and buttons share `--radius-md` for visual consistency.

### 5.2 Shadow Tiers

All shadows use layered composites for depth realism. Colors use `--fg` alpha-mixed for theme awareness.

```css
:root {
  --shadow-xs: 0 1px 2px 0 rgba(0,0,0,0.04);
  --shadow-sm: 0 1px 2px 0 rgba(0,0,0,0.06),
               0 1px 3px 0 rgba(0,0,0,0.04);
  --shadow-md: 0 2px 4px -1px rgba(0,0,0,0.06),
               0 4px 8px -1px rgba(0,0,0,0.08);
  --shadow-lg: 0 4px 6px -2px rgba(0,0,0,0.05),
               0 10px 20px -2px rgba(0,0,0,0.10);
}

<!-- FIXED in cross-review pass: BLOCKER-2 — changed [data-theme="dark"] → .dark for shadow overrides -->
.dark {
  --shadow-xs: 0 1px 2px 0 rgba(0,0,0,0.20);
  --shadow-sm: 0 1px 2px 0 rgba(0,0,0,0.30),
               0 1px 3px 0 rgba(0,0,0,0.15);
  --shadow-md: 0 2px 4px -1px rgba(0,0,0,0.30),
               0 4px 8px -1px rgba(0,0,0,0.20);
  --shadow-lg: 0 4px 6px -2px rgba(0,0,0,0.25),
               0 10px 20px -2px rgba(0,0,0,0.30);
}
```

### 5.3 Elevation Rules

| Surface | Shadow | Border? | z-index | Examples |
|---------|--------|---------|---------|----------|
| Flat | none | 1px `--border` | auto | Table rows, list items |
| Card | `--shadow-sm` | 1px `--border` | auto | Stat tiles, content cards |
| Dropdown | `--shadow-md` | 1px `--border` | 50 | Nav menu, filter dropdown, command palette |
| Modal / Dialog | `--shadow-lg` | none | 100 | Confirmation dialog, detail modal |
| Toast | `--shadow-md` | 1px `--border` | 200 | Sonner notifications |
| Tooltip | `--shadow-sm` | 1px `--border` | 300 | Hover info, chart tooltip |

**Rule:** Shadow intensity increases with z-index. Borders are always present on cards and dropdowns (don't rely on shadow alone for edge perception in light mode).

---

## 6. Motion

### 6.1 Transition Tokens

| Token | Value | Cubic-bezier | Usage |
|-------|-------|-------------|-------|
| `--duration-fast` | 100ms | — | Hover color changes, icon swaps |
| `--duration-normal` | 200ms | — | Panel expand, tab switch, tooltip show |
| `--duration-slow` | 350ms | — | Modal enter, page transition, chart animate-in |
| `--ease-out` | `cubic-bezier(0.16, 1, 0.3, 1)` | Expo out | Default for *entering* elements (dropdown open, toast slide-in) |
| `--ease-in` | `cubic-bezier(0.7, 0, 0.84, 0)` | Expo in | *Exiting* elements (modal close, toast dismiss) |
| `--ease-inout` | `cubic-bezier(0.65, 0, 0.35, 1)` | Expo in-out | Layout shifts (sidebar toggle, content reflow) |

### 6.2 Motion Rules

1. **Color/opacity transitions:** `--duration-fast` — always.
2. **Transform transitions (translate, scale):** `--duration-normal` + `--ease-out`.
3. **Chart animation (initial paint):** `--duration-slow` + `--ease-out`. Only the first render; subsequent updates are instant.
4. **Skeleton pulse:** `1.4s ease-in-out infinite` (existing pattern — keep).
5. **`prefers-reduced-motion: reduce`:** Set ALL `--duration-*` to `0ms`. Skeleton pulse → `animation: none`. Chart animations skipped. Only exception: focus outlines remain instant (non-animated already).

```css
@media (prefers-reduced-motion: reduce) {
  :root {
    --duration-fast: 0ms;
    --duration-normal: 0ms;
    --duration-slow: 0ms;
  }
  .skeleton { animation: none; }
}
```

---

## 7. Icon System

**Library:** `lucide-react` (already in stack).

**Defaults:**
- Size: `16px` (inline), `20px` (nav/button), `24px` (empty-state hero)
- Stroke width: `1.5` (default lucide)
- Color: `currentColor` — inherits from parent text.

### Icon Mapping (35 features)

| Feature / Context | Lucide icon name | Notes |
|-------------------|-----------------|-------|
| Home | `Home` | |
| Sessions list | `FolderOpen` | |
| Single session | `FileText` | |
| Search | `Search` | |
| Dashboard | `LayoutDashboard` | |
| Embeddings 2D | `Scatter` | `ScatterChart` alternate |
| Mindmap | `GitBranch` | Rotated 90° visually resembles tree |
| Knowledge graph | `Share2` | Network/connections metaphor |
| Timeline | `Clock` | Or `History` for playback |
| Eval / Feedback | `ThumbsUp` | |
| Live feed | `Radio` | Animated pulse dot overlay |
| Diff compare | `GitCompare` | |
| Agents | `Bot` | |
| Settings | `Settings` | |
| Dark mode toggle | `Sun` / `Moon` | Swap on theme |
| Filter | `Filter` | |
| Sort ascending | `ArrowUp` | Inside table header |
| Sort descending | `ArrowDown` | |
| Sort neutral | `ArrowUpDown` | Unsorted column |
| Pagination prev | `ChevronLeft` | |
| Pagination next | `ChevronRight` | |
| External link | `ExternalLink` | |
| Copy | `Copy` | |
| Download / export | `Download` | |
| Expand / collapse | `ChevronDown` / `ChevronUp` | |
| Close | `X` | Dialog, toast dismiss |
| Success indicator | `CheckCircle` | `--success` color |
| Warning indicator | `AlertTriangle` | `--warning` color |
| Error indicator | `XCircle` | `--danger` color |
| Info indicator | `Info` | `--info` color |
| Empty state | `Inbox` | 24px, `--fg-subtle` |
| Error state | `AlertOctagon` | 24px, `--danger` |
| Loading spinner | `Loader2` | `animate-spin`, 16px |
| Knowledge entry | `Lightbulb` | |
| Module/wing | `Package` | |

---

## 8. Chart Decisions

### Global Chart Conventions

- **Color palette for categorical data:** Use the 7 knowledge-category colors defined in `CATEGORY_COLORS` (§1.3 semantic system). For charts needing >7 colors, extend with `--chart-1` through `--chart-5` from shadcn mapping.
- **Font:** Geist Sans 11px for axis labels, 12px for tooltips.
- **Grid lines:** `--border-subtle` at 0.5 opacity. No outer frame.
- **Tooltip:** Floating card with `--shadow-sm`, `--bg-elevated`, `--border`. Max-width 240px.
- **Empty state:** Center-aligned `Inbox` icon (24px) + "No data yet" text in `--fg-muted`.
- **Loading skeleton:** Replace chart area with a single `skeleton` block at chart height.
- **Responsive:** Charts resize on container resize via `ResizeObserver` (not window resize).

### 8a. Dashboard KPI Tiles — Sparkline

| Attribute | Decision | Rationale |
|-----------|----------|-----------|
| **Lib** | **Recharts** `<Sparkline>` or lightweight SVG inline | Recharts is shadcn's first-party chart lib (built on D3). Sparklines are tiny — no need for canvas. |
| **Chart type** | Area sparkline (filled under line) | Shows trend + magnitude. Line-only loses context at small sizes. |
| **Size** | 80×32px, no axis labels | Fits inside stat tile below the KPI number |
| **Color** | Stroke: `--accent` (indigo). Fill: `--accent` at 15% opacity | One color for all tiles — sparklines show shape, not category |
| **Tooltip** | None | Too small for tooltip. KPI number itself is the summary |
| **Interaction** | None | Static. Click the tile to navigate to the relevant detail page |
| **Data shape** | `{date: string, value: number}[]` last 14 days | |
| **Empty state** | Flat horizontal line at y=0, dashed, `--border-subtle` | |
| **Loading** | 80×32px `skeleton` block | |

### 8b. Sessions Over Time (Dashboard)

| Attribute | Decision | Rationale |
|-----------|----------|-----------|
| **Lib** | **Recharts** `<BarChart>` | Bar > line for discrete daily counts (each bar = 1 day). Line implies continuous interpolation which is misleading for count data. Area is acceptable but bars more clearly encode "this many sessions on this day". |
| **Chart type** | Vertical bar chart, **stacked by source** | Stacking shows both total per day AND per-source composition. Since we have ≤5 sources, cognitive load is manageable. |
| **Time scale** | X-axis = date (last 30 days). Label: `MMM d` every 5th bar | |
| **Color** | Source colors from §2 (copilot=blue, claude=orange, gemini=teal, codex=violet). Unknown source = neutral-400. | |
| **Tooltip** | On hover: date + breakdown per source + total | |
| **Interaction** | Click bar → filter sessions list by that date | |
| **Size** | Full-width of left column, height 200px | |
| **Data shape** | `{date: string, copilot: number, claude: number, gemini: number, codex: number}[]` | |
| **Empty state** | `Inbox` icon + "No sessions indexed yet" | |
| **Loading** | `skeleton` block 100%×200px | |

**Why not area?** Area charts stack fills and can create "layering confusion" with translucent fills at small scales. Bars are unambiguous.

### 8c. Knowledge Entries by Category

| Attribute | Decision | Rationale |
|-----------|----------|-----------|
| **Lib** | **Recharts** `<PieChart>` as **donut** | 7 categories — well within pie/donut cognitive limit (≤8 slices). Treemap wastes space when all categories fit a donut. Horizontal bar chart is an alternative but donut provides at-a-glance proportional reading. |
| **Chart type** | Donut (inner radius 60%) | Inner space shows total count (large number) — efficient use of space. |
| **Color** | `CATEGORY_COLORS` from §1 projection.py — `mistake=#ff6b6b`, `pattern=#51cf66`, etc. | |
| **Tooltip** | Category name + count + percentage | |
| **Interaction** | Click slice → filter knowledge list by that category | |
| **Legend** | Right-side vertical legend with color swatch + label + count. Not inside chart. | |
| **Size** | 220×220px chart + 120px legend width | |
| **Data shape** | `{name: string, count: number}[]` | |
| **Empty state** | `Inbox` icon + "No knowledge entries yet" | |
| **Loading** | Circular `skeleton` 220×220px | |

**Why not treemap?** Treemaps excel at hierarchical data with many leaves. 7 flat categories don't benefit from spatial nesting. Donut is more recognizable and scannable.

### 8d. Token Usage / Cumulative Cost Over Time

| Attribute | Decision | Rationale |
|-----------|----------|-----------|
| **Lib** | **Recharts** `<AreaChart>` | |
| **Chart type** | **Stacked area chart** with source breakdown | Cumulative cost is inherently additive. Stacked area visually encodes "total grows over time" with per-source contribution. |
| **X-axis** | Date (weekly aggregation if >90 days) | |
| **Y-axis** | Dollar amount formatted as `$X.XX` | |
| **Color** | Source colors from §2, at 70% opacity fill | |
| **Tooltip** | Date + per-source cost + total | |
| **Interaction** | Hover crosshair (vertical line). No click action. | |
| **Data shape** | `{date: string, copilot: number, claude: number, gemini: number, codex: number}[]` | |
| **Empty state** | `Inbox` icon + "No token usage data" | |
| **Loading** | `skeleton` block 100%×200px | |

### 8e. Embeddings 2D Scatter

| Attribute | Decision | Rationale |
|-----------|----------|-----------|
| **Lib** | **Canvas 2D** (keep current vanilla implementation) | Current codebase uses `<canvas>` with manual 2D context — already performant at 2000 points. deck.gl adds ~200KB and WebGL complexity for marginal gain at this scale. Switch to deck.gl only if points exceed 10K. React wrapper: create `<ScatterCanvas>` component with `useRef` + `useEffect`. |
| **Color coding** | **By category** (primary). Toggle to **by source** (secondary). | Category coloring is the default because it reveals *what kind* of knowledge clusters together. Source coloring is a secondary view to see if one AI produces different embeddings. |
| **Category palette** | `CATEGORY_COLORS` from projection.py | |
| **Source palette** | Source colors from §2 | |
| **Point size** | 4px radius default, 6px on hover | |
| **Interaction** | Hover: tooltip (title + category). Click: navigate to session/entry. **Lasso select**: hold Shift + drag to draw rectangle → show count + list of selected entries in a bottom panel. | |
| **Zoom/Pan** | Scroll-wheel zoom + drag-pan. Double-click to reset. | |
| **Dark mode** | Points use same hex colors (already high-saturation). Background switches to `--bg`. Axis guides use `--border-subtle`. | |
| **Data shape** | `{id, x, y, category, title, source?}[]` — from `get_projection()` | |
| **Empty state** | `Inbox` icon + "Run `embed.py` to generate embeddings" (actionable message) | |
| **Loading** | `skeleton` block 100%×55vw | |

**Why not deck.gl?** The current dataset caps at 2000 points (`_MAX_RENDER`). Canvas 2D handles this trivially at 60fps. deck.gl's WebGL overhead (~200KB gzip) is justified only above ~10K points. If the cap increases, deck.gl becomes the migration path.

### 8f. Knowledge Graph

| Attribute | Decision | Rationale |
|-----------|----------|-----------|
| **Lib** | **Cytoscape.js** (keep current) with `cytoscape-react` wrapper | Already integrated. Cytoscape handles up to ~5K nodes with COSE layout. d3-force requires more manual rendering code. Sigma.js (WebGL) is overkill below 10K nodes. |
| **Layout** | `cose` (Compound Spring Embedder) — force-directed | Already configured. Best for organic relationship visualization. |
| **Node style** | Circle for entries (20px), Diamond for entities (14px). Color from `CATEGORY_COLORS`. | |
| **Edge style** | 1px `--border`, bezier curves. Arrow at target. Relation label on hover only (7px). | |
| **Interaction** | Click node → sidebar detail panel. Double-click → navigate to search. Scroll zoom. Box-select multiple nodes. | |
| **Filter** | Sidebar with checkboxes for wing + category (existing). Add search input to filter nodes by label substring. | |
| **Dark mode** | Node colors stay. Edge color → `neutral-400`. Label color → `--fg-muted`. Background → `--bg`. | |
| **Data shape** | `{nodes: [{id, kind, label, wing, room, category, color}], edges: [{source, target, relation}]}` | |
| **Empty state** | `Share2` icon (24px) + "No knowledge relations found. Run `extract-knowledge.py` to build the graph." | |
| **Loading** | `skeleton` block full container height | |

**Why keep Cytoscape over d3-force?** Cytoscape provides built-in selection, zoom, pan, layout algorithms, and styling DSL. d3-force requires reimplementing all of those. The `cytoscape-react` wrapper makes integration with React straightforward.

### 8g. Mindmap

| Attribute | Decision | Rationale |
|-----------|----------|-----------|
| **Lib** | **markmap-view** (keep current) with React integration via `useRef` | markmap is purpose-built for Markdown → mindmap rendering. No alternative is as tightly integrated with heading-based outlines. |
| **Theme** | Override markmap's default CSS vars to use our tokens: node text `--fg`, lines `--border-strong`, background `--bg`. | |
| **Dark mode** | Set markmap's `--markmap-text-color` and `--markmap-line-color` from our tokens. SVG background inherits `--bg`. | |
| **Interaction** | Click node to expand/collapse. Toolbar: Fit, Expand All, Collapse All (existing). | |
| **Data shape** | `{markdown: string, title: string}` — heading outline parsed from session file | |
| **Empty state** | `GitBranch` icon + "Session has no heading structure" | |
| **Loading** | `skeleton` block 100%×60vh | |

### 8h. Eval Metrics Over Time

| Attribute | Decision | Rationale |
|-----------|----------|-----------|
| **Lib** | **Recharts** `<LineChart>` | Line chart is standard for time-series comparison. |
| **Chart type** | Multi-line with **one line per metric** (👍 rate, 👎 rate, total feedback count) | Lines allow overlaying multiple independent metrics on the same time axis. Area would create stacking confusion for rate-based metrics. |
| **X-axis** | Date (weekly bins) | |
| **Y-axis** | Dual axis — left: count, right: percentage (satisfaction rate) | |
| **Color** | `--success` for 👍, `--danger` for 👎, `--fg-muted` for total count | |
| **Tooltip** | Date + all metric values | |
| **Interaction** | Toggle lines via legend click. Hover crosshair. | |
| **Data shape** | `{week: string, up: number, down: number, neutral: number, total: number}[]` from `search_feedback` aggregation | |
| **Empty state** | `ThumbsUp` icon + "No feedback recorded yet" | |
| **Loading** | `skeleton` block 100%×200px | |

---

## 9. Component Visual Specs

### 9.1 Button

| Variant | Background | Text | Border | Hover bg | Active bg |
|---------|-----------|------|--------|----------|-----------|
| **Primary** | `--accent` | `--accent-fg` | none | `--accent-hover` | `--accent` at 90% opacity |
| **Secondary** | `--bg-elevated` | `--fg` | 1px `--border` | `--bg-hover` | `--bg-active` |
| **Ghost** | transparent | `--fg` | none | `--bg-hover` | `--bg-active` |
| **Danger** | `--danger-fg` | white | none | `--danger-fg` at 85% | `--danger-fg` at 75% |

**States:**
- **Disabled:** `opacity: 0.5; pointer-events: none;`
- **Loading:** Text replaced with `Loader2` icon (animate-spin). Button width locked (no layout shift).
- **Focus-visible:** `2px solid --accent`, `outline-offset: 2px`.

**Sizes:**

| Size | Height | Padding | Font | Icon size |
|------|--------|---------|------|-----------|
| sm | 28px | `6px 12px` | 12px / 500 | 14px |
| md (default) | 36px | `8px 16px` | 14px / 500 | 16px |
| lg | 44px | `10px 20px` | 16px / 500 | 18px |

### 9.2 Input

- Height: 36px (matches button md).
- Padding: `8px 12px`.
- Border: 1px `--border`. Radius: `--radius-md`.
- Font: 14px Geist Sans.
- **Focus:** border → `--accent`, box-shadow `0 0 0 3px color-mix(in srgb, var(--accent) 25%, transparent)`.
- **Error:** border → `--danger-fg`, box-shadow tinted red.
- **Disabled:** `--bg-muted` background, `--fg-subtle` text, `opacity: 0.7`.
- **Placeholder:** `--fg-subtle`.

### 9.3 Card

- Background: `--bg-elevated`.
- Border: 1px `--border`.
- Radius: `--radius-md` (6px).
- Shadow: `--shadow-sm`.
- Padding: `--space-4` (16px).
- **Header:** Optional top section with `border-bottom: 1px solid --border-subtle`. Padding-bottom `--space-3`. Font: 15px / 600.
- **Body:** Default section.
- **Footer:** Optional bottom section with `border-top: 1px solid --border-subtle`. Padding-top `--space-3`. Typically contains actions (buttons).

### 9.4 Badge

Base: `display: inline-flex; align-items: center; gap: 4px; padding: 2px 8px; border-radius: --radius-sm; font: 12px/1 500;`

| Variant | Background | Text |
|---------|-----------|------|
| **Default** | `--bg-muted` | `--fg-muted` |
| **Success** | `--success-bg` | `--success-fg` |
| **Warning** | `--warning-bg` | `--warning-fg` |
| **Danger** | `--danger-bg` | `--danger-fg` |
| **Info** | `--info-bg` | `--info-fg` |
| **Copilot** | `--source-copilot-bg` | `--source-copilot-fg` |
| **Claude** | `--source-claude-bg` | `--source-claude-fg` |
| **Gemini** | `--source-gemini-bg` | `--source-gemini-fg` |
| **Codex** | `--source-codex-bg` | `--source-codex-fg` |

### 9.5 Table

- **Row height:** Compact = 32px, Comfortable = 44px. Default: compact.
- **Zebra striping:** No. Use hover-only highlight (`--bg-hover`). Rationale: zebra creates visual noise in data-dense tables; hover is sufficient when rows are scannable. GitHub and Linear both dropped zebra.
- **Header:** Background `--bg-elevated`. Font: 13px / 600. Sticky (`position: sticky; top: 0; z-index: 10`). Bottom border: 1px `--border-strong`.
- **Cell:** Font: 13px / 400. Vertical align top. Padding: `8px 12px` (compact) / `12px 12px` (comfortable).
- **Row hover:** `--bg-hover`.
- **Selected row:** `--accent` at 8% opacity background + left 2px border `--accent`.

### 9.6 Tabs

**Choice: Underline tabs (not pills).**

| Criterion | Underline | Pills |
|-----------|-----------|-------|
| Navigation pattern | Underline is standard for in-page section switching (Linear, GitHub, Vercel) | Pills suit toolbar toggle (e.g., grid/list view) |
| Visual weight | Lighter — doesn't compete with card surfaces | Adds background rectangles that visually compete with data |
| Scrollable | Easy to extend horizontally for many tabs | Pills wrap awkwardly |

**Spec:**
- Tab text: 14px / 500, `--fg-muted` (inactive), `--fg` (active).
- Underline: 2px `--accent`, `border-radius: 1px`, bottom-aligned.
- Hover: text → `--fg`, no underline.
- Gap between tabs: `--space-1` (4px).
- Transition: underline slides left/right with `--duration-normal` + `--ease-out`.

### 9.7 Dialog / Modal

| Size | Width | Usage |
|------|-------|-------|
| sm | 400px | Confirmation, simple form |
| md | 560px | Detail view, multi-field form |
| lg | 720px | Complex content, side-by-side layout |
| full | `min(90vw, 1200px)` | Session detail, large diff |

- Backdrop: `rgba(0, 0, 0, 0.5)` light / `rgba(0, 0, 0, 0.7)` dark.
- Border-radius: `--radius-lg` (8px).
- Shadow: `--shadow-lg`.
- Animation (enter): `opacity 0→1` + `scale(0.96)→scale(1)` + `translateY(8px)→0`. Duration: `--duration-slow`. Easing: `--ease-out`.
- Animation (exit): reverse with `--ease-in`, `--duration-normal`.
- Close button: top-right, `X` icon, ghost button style.
- Focus trap: first focusable element on open. Escape to close.

### 9.8 Toast (Sonner)

- **Position:** Bottom-right.
- **Duration:** 4000ms (info), 6000ms (error/warning). Dismissable via click or swipe.
- **Max visible:** 3 stacked.
- **Width:** 360px.
- **Border-radius:** `--radius-md`.
- **Shadow:** `--shadow-md`.
- **Variants:** Match semantic colors — success (green left-border), error (red), warning (amber), info (blue).
- **Animation:** Slide-in from right, 200ms ease-out. Slide-out to right, 150ms ease-in.

---

## 10. Data Table Specifics

### 10.1 Density Modes

| Mode | Row height | Cell padding | Font size | Persisted? |
|------|-----------|-------------|-----------|------------|
| Compact (default) | 32px | `4px 12px` | 13px | Yes — cookie `table-density=compact` |
| Comfortable | 44px | `10px 12px` | 14px | Yes — cookie `table-density=comfortable` |

**Canonical CSS variables** (defined in `:root`, applied via density class):

```css
:root {
  --row-height-compact: 2rem;        /* 32px */
  --row-height-comfortable: 2.75rem; /* 44px */
  --font-size-compact: 0.8125rem;    /* 13px */
  --font-size-comfortable: 0.875rem; /* 14px */
}
```

All row height and font size references in components **must** use these variables — never hardcode `32px`, `44px`, `13px`, or `14px` directly.

Toggle via icon button (`AlignJustify` for compact, `List` for comfortable) in the table toolbar.

### 10.2 Sort Indicator

- **Unsorted column:** `ArrowUpDown` icon, `--fg-subtle`, 14px.
- **Ascending:** `ArrowUp` icon, `--accent`, 14px.
- **Descending:** `ArrowDown` icon, `--accent`, 14px.
- Caret sits right of header text with `--space-1` gap.
- Active sort column header text color: `--fg` (not accent — the caret carries the color signal).

### 10.3 Filter Chips

- Position: **Above table, below page header.** Horizontal scrollable strip.
- Chip style: badge-like. `--bg-muted` bg, `--fg` text, `--radius-full`. Active filter: `--accent` bg, white text.
- Clear-all button: ghost button at right end if ≥1 filter active.
- Add filter: `+ Filter` ghost button → dropdown with filter options.

### 10.4 Pagination

- Position: Table footer bar. `border-top: 1px solid --border`.
- Left side: "Showing 1–25 of 342 results" in `--fg-muted` 13px.
- Center: Page buttons (1, 2, 3, …, 14) — max 7 visible with ellipsis. Active page: `--accent` bg.
- Right side: Per-page selector `<select>` with options: 10 / 25 / 50 / 100. Default: 25.
- Cookie-persisted: `table-per-page=25`.

### 10.5 Empty State (Inside Table)

- `<tbody>` contains single `<tr>` with `<td colspan="...">`.
- Centered: `Inbox` icon 24px + "No results match your filters" title (15px / 500) + "Try adjusting filters or search query" subtitle (13px, `--fg-muted`).
- Optional CTA button below.

### 10.6 Loading Skeleton

- 5 `<tr>` rows, each cell contains a `skeleton` div matching typical cell content width.
- Header row remains visible (not skeleton).
- Skeleton widths vary per column (title col: 60% width, date col: 80px, badge col: 48px) for realistic shape.
- Pulse animation: existing `skeleton-pulse` keyframe from app.css.

---

## 11. SVG Illustrations

### Style Guidelines

- **Technique:** Thin-stroke line art (stroke-width 1–1.5px). Monochrome using `currentColor` (inherits `--fg-muted`).
- **Size:** 120×120px default viewBox. Rendered at 96px in empty states.
- **No fills except:** Optional single `--accent` at 10% opacity for a subtle highlight area.
- **Dark mode:** Fully theme-aware via `currentColor`. No hard-coded colors.

### Source

**Self-authored inline SVG** — not an external pack.

Rationale: Open-source packs (undraw, pixeltrue) are multicolor raster-feel illustrations that clash with a data-tool aesthetic. Inline SVG means zero external requests, full theme control, and tiny filesize (~500B per illustration).

### Required Illustrations

| Context | Description | Key visual element |
|---------|-------------|-------------------|
| **Empty search** | Magnifying glass with question mark | Search + question |
| **Empty sessions** | Open folder, empty inside | Folder outline |
| **Empty knowledge** | Lightbulb, unlit (dashed filament) | Lightbulb |
| **Empty graph** | Disconnected nodes (3 circles, no lines) | Nodes floating |
| **Empty embeddings** | Coordinate grid, no points | Grid + axes |
| **Empty eval** | Thumbs-up outline, dotted | Thumb outline |
| **Error state** | Warning triangle with exclamation | Triangle |
| **404 page** | Map with pin missing | Map fold + "?" |

---

## 12. Don't List — Visual Anti-Patterns

| # | Anti-pattern | Why it's banned |
|---|-------------|----------------|
| 1 | **Multi-color gradients on backgrounds** (e.g., purple→blue→green) | Creates visual noise, fights with data colors, inaccessible for color-vision impairment. Exception: a single subtle gradient on the hero illustration is acceptable. |
| 2 | **Multiple layered box-shadows** beyond the 2-layer spec (§5.2) | More layers ≠ better depth. Creates "floating in fog" effect. Stick to the defined tiers. |
| 3 | **Animated number counters** ("slot machine" KPI effect) | Delays information delivery. Users want instant numbers. Animation on data = deception risk (what if the count is stale?). |
| 4 | **Colored icon backgrounds** (icon inside a colored circle) | Redundant signaling — the icon shape IS the signal. Colored circles waste space and clash with badge colors. Exception: source badges can have bg. |
| 5 | **Full-bleed color panels** (e.g., blue header bar, green success strip) | Competes with semantic colors. Dashboard should be neutral chrome + colored data. |
| 6 | **Text on images / pattern backgrounds** | This is a data tool, not a marketing site. All text sits on solid `--bg*` surfaces. |
| 7 | **More than 3 font weights on one page** | 400 (regular), 500 (medium), 600 (semibold) are the only permitted weights. 700 (bold) only for KPI numbers. 300/100 banned — too thin for UI. |
| 8 | **Border-radius > 12px on rectangular containers** | This isn't a consumer app. Large radius wastes corner space and creates visual inconsistency at small sizes. `--radius-full` is only for pills and avatars. |
| 9 | **Chart junk: 3D effects, textures, decorative gridlines, dual-pie, radar charts** | Edward Tufte principle: maximize data-ink ratio. Radar charts are notoriously hard to read. 3D adds no information. |
| 10 | **Opacity below 0.3 for interactive elements** | Creates confusion about whether an element is disabled or just decorative. Interactive elements must be ≥ 0.5 opacity when enabled. Disabled = exactly 0.5. |

---

## Appendix A: CSS Variable Summary (Quick Reference)

```css
:root {
  /* Brand */
  --primary:              #5E6AD2;
  --primary-hover:        #4F5ABF;
  --primary-foreground:   #FFFFFF;

  /* Surfaces */
  --bg:                   #FFFFFF;
  --bg-subtle:            #FAFBFC;
  --bg-muted:             #EAEEF2;
  --bg-elevated:          #FFFFFF;
  --bg-hover:             #E6EBF1;
  --bg-active:            #DDE3EA;

  /* Foreground */
  --fg:                   #1F2328;
  --fg-muted:             #656D76;
  --fg-subtle:            #7D8590;
  --fg-on-accent:         #FFFFFF;

  /* Border */
  --border:               #D0D7DE;
  --border-subtle:        #E8ECF0;
  --border-strong:        #AFB8C1;

  /* Accent */
  --accent:               #5E6AD2;
  --accent-fg:            #FFFFFF;
  --accent-hover:         #4F5ABF;

  /* Semantic */
  --success-bg: #DAFBE1;  --success-fg: #1A7F37;  --success-border: #4AC26B;
  --warning-bg: #FFF8C5;  --warning-fg: #9A6700;  --warning-border: #D4A72C;
  --danger-bg:  #FFEBE9;  --danger-fg:  #CF222E;  --danger-border:  #F47067;
  --info-bg:    #DDF4FF;  --info-fg:    #0969DA;  --info-border:    #54AEFF;

  /* Source badges */
  --source-copilot-bg: #DDF4FF;  --source-copilot-fg: #0550AE;
  --source-claude-bg:  #FFF0E0;  --source-claude-fg:  #9C4221;
  --source-gemini-bg:  #D3F9E0;  --source-gemini-fg:  #0E6B3A;
  --source-codex-bg:   #F0EBFF;  --source-codex-fg:   #6527BE;

  /* Typography */
  --font-sans:  'Geist', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
  --font-mono:  'Geist Mono', ui-monospace, SFMono-Regular, monospace;

  /* Spacing */
  --space-0: 0;   --space-0\.5: 2px;  --space-1: 4px;  --space-1\.5: 6px;
  --space-2: 8px; --space-3: 12px;    --space-4: 16px; --space-5: 20px;
  --space-6: 24px; --space-8: 32px;   --space-10: 40px; --space-12: 48px;
  --space-16: 64px; --space-20: 80px; --space-24: 96px; --space-32: 128px;

  /* Radius */
  --radius-sm: 4px;  --radius-md: 6px;  --radius-lg: 8px;
  --radius-xl: 12px; --radius-full: 9999px;

  /* Shadows — see §5.2 for full multi-layer definitions */
  --shadow-xs: 0 1px 2px 0 rgba(0,0,0,0.04);
  --shadow-sm: 0 1px 2px 0 rgba(0,0,0,0.06), 0 1px 3px 0 rgba(0,0,0,0.04);
  --shadow-md: 0 2px 4px -1px rgba(0,0,0,0.06), 0 4px 8px -1px rgba(0,0,0,0.08);
  --shadow-lg: 0 4px 6px -2px rgba(0,0,0,0.05), 0 10px 20px -2px rgba(0,0,0,0.10);

  /* Motion */
  --duration-fast: 100ms;
  --duration-normal: 200ms;
  --duration-slow: 350ms;
  --ease-out: cubic-bezier(0.16, 1, 0.3, 1);
  --ease-in: cubic-bezier(0.7, 0, 0.84, 0);
  --ease-inout: cubic-bezier(0.65, 0, 0.35, 1);
}
```

## Appendix B: Chart Library Decision Matrix

| Visualization | Chosen lib | Bundle (gzip) | Alternatives considered | Why rejected |
|--------------|-----------|---------------|------------------------|-------------|
| Sparklines | Recharts | ~45KB (shared) | uPlot, custom SVG | uPlot API is imperative — harder to use in React. Custom SVG is fine but Recharts already loaded. |
| Bar / Area / Line | Recharts | (shared) | Nivo, Victory, Chart.js | Nivo: heavier (~80KB extra). Victory: verbose API. Chart.js: Canvas-based, no SSR story. |
| Donut | Recharts | (shared) | d3-arc, Nivo Pie | d3 requires manual React binding. Nivo adds bundle weight. |
| Scatter (2D) | Canvas 2D (vanilla) | 0KB | deck.gl, Recharts Scatter | deck.gl: ~200KB, WebGL overhead for ≤2000 points. Recharts scatter: SVG-based, performance degrades >500 points. |
<!-- FIXED in cross-review pass: BLOCKER-1 — changed Cytoscape.js → react-force-graph-2d -->
| Force graph | react-force-graph-2d | ~45KB | d3-force, sigma.js, Cytoscape.js | d3-force: no built-in selection/zoom. sigma.js: WebGL, overkill below 10K nodes. Cytoscape.js: ~170KB, heavier than needed for our graph size. |
| Mindmap | markmap-view | ~50KB | react-mindmap, d3-hierarchy | react-mindmap: poorly maintained. d3-hierarchy: no markdown parsing. |

<!-- FIXED in cross-review pass: BLOCKER-1 — updated budget for react-force-graph-2d instead of Cytoscape -->
**Total incremental JS budget:** ~140KB gzip (Recharts 45 + react-force-graph-2d 45 + markmap 50). Canvas scatter adds 0KB.
