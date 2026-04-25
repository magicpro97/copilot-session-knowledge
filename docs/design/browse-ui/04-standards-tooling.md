# 04 — Standards & Tooling Audit for `browse-ui/` Integration

> **Author:** Standards & Tooling Auditor  
> **Date:** 2025-07-18  
> **Scope:** Ensure the upcoming `browse-ui/` (Next.js + TS + shadcn) module fits into the
> existing automation: auto-update pipeline, Copilot CLI hooks, skill registry, knowledge
> tracking, lint/format/test, and documentation.

---

## 1. `auto-update-tools.py` Changes

### 1.1 `COVERAGE_MANIFEST` (line 103-129)

**Current state:** 6 categories, none mention `browse-ui/`.  
**Required:** Add a new category `"Browse UI"` so `--list-coverage` and `--doctor` track
the Next.js module.

```python
# --- BEFORE (line 125-129) ---
    "Other": [
        ("docs/",                "documentation"),
        ("presets/",             "preset configurations"),
    ],
}

# --- AFTER ---
    "Browse UI": [
        ("browse-ui/src/",      "browse-ui Next.js source (TS + components)"),
        ("browse-ui/public/",   "browse-ui static assets"),
        ("browse-ui/dist/",     "browse-ui prebuilt artifacts (checked-in)"),
    ],
    "Other": [
        ("docs/",                "documentation"),
        ("presets/",             "preset configurations"),
    ],
}
```

### 1.2 `classify_changes()` (line 477-500)

Add a `browse_ui` key so the pipeline can route browse-ui changes separately from the
existing `browse` (Python module) key.

```python
# --- BEFORE (line 492) ---
        "browse":       [f for f in changed if f.startswith("browse/")],

# --- AFTER (add line after "browse") ---
        "browse":       [f for f in changed if f.startswith("browse/")],
        "browse_ui":    [f for f in changed if f.startswith("browse-ui/")],
```

### 1.3 `changed_categories` in `write_manifest()` (line 795-800)

```python
# --- BEFORE ---
        for key in ("browse", "providers", "skills", "hooks", "hooks_rules",
                    "scripts", "workflows", "launchd", "templates", "py_scripts")

# --- AFTER ---
        for key in ("browse", "browse_ui", "providers", "skills", "hooks", "hooks_rules",
                    "scripts", "workflows", "launchd", "templates", "py_scripts")
```

### 1.4 `post_pull_pipeline()` — new step (after step 5, ~line 572)

When `browse-ui/src/**` changes on `git pull`, run `pnpm install && pnpm build` to
rebuild the checked-in `dist/`.

```python
        # 5b. Browse UI source changed → rebuild dist artifacts
        if changes.get("browse_ui"):
            browse_ui_src = [f for f in changes["browse_ui"] if f.startswith("browse-ui/src/")]
            if browse_ui_src:
                browse_ui_dir = TOOLS_DIR / "browse-ui"
                pnpm = shutil.which("pnpm")
                if pnpm and browse_ui_dir.is_dir():
                    log("browse-ui source changed — rebuilding dist...")
                    try:
                        subprocess.run([pnpm, "install", "--frozen-lockfile"],
                                       cwd=str(browse_ui_dir), capture_output=True, timeout=120)
                        subprocess.run([pnpm, "build"],
                                       cwd=str(browse_ui_dir), capture_output=True, timeout=120)
                        ok("browse-ui rebuilt")
                    except Exception as e:
                        warn(f"browse-ui rebuild failed: {e}")
                elif not pnpm:
                    warn("pnpm not found — skip browse-ui rebuild (install via: npm i -g pnpm)")
```

### 1.5 `list_coverage()` print block (line 1209-1214)

Add a line:

```python
    print("  browse-ui/src/                → pnpm build (if pnpm available)")
```

### 1.6 Path considerations

- **Windows/macOS:** `classify_changes` uses `f.startswith("browse-ui/")` — works on all
  platforms because git always emits forward slashes in `diff --name-only`.
- **gitignore:** `browse-ui/node_modules/` and `browse-ui/.next/` must be ignored;
  `browse-ui/dist/` must NOT be ignored (see §10).

---

## 2. Copilot CLI Hooks — New Rules

### 2.1 Overview of existing hook architecture

- `hooks/hooks.json`: single entry per event → delegates to `hook_runner.py`.
- `hook_runner.py`: reads stdin JSON, dispatches to `hooks/rules/*.py` via
  `rules/__init__.py::get_rules_for_event()`.
- Rules extend `class Rule` with `name`, `events`, `tools`, `evaluate()`.
- `hooks/rules/common.py`: `CODE_EXTENSIONS` already contains `.ts`, `.tsx`, `.js`, `.jsx`.

### 2.2 New rule: `block_edit_dist.py`

**File:** `hooks/rules/block_edit_dist.py`  
**Event:** `preToolUse`  
**Purpose:** Prevent agents from editing files in `browse-ui/dist/` directly —
forces rebuild via `pnpm build`.

```python
"""block_edit_dist.py — Block direct edits to browse-ui/dist/."""
from pathlib import Path

from . import Rule
from .common import deny


class BlockEditDistRule(Rule):
    """Deny edit/create targeting browse-ui/dist/. Must rebuild instead."""

    name = "block-edit-dist"
    events = ["preToolUse"]
    tools = ["edit", "create"]

    PROTECTED_PREFIX = "browse-ui/dist/"

    def evaluate(self, event, data):
        tool_args = data.get("toolArgs", {})
        if not isinstance(tool_args, dict):
            return None

        file_path = tool_args.get("path", "")
        if not file_path:
            return None

        # Normalise: resolve relative to browse-ui/dist or absolute
        rel = file_path
        try:
            rel = str(Path(file_path).resolve().relative_to(
                Path.home() / ".copilot" / "tools"))
        except (ValueError, RuntimeError):
            pass

        if rel.startswith(self.PROTECTED_PREFIX) or "/browse-ui/dist/" in file_path:
            return deny(
                "🚫 Direct edits to browse-ui/dist/ are blocked.\n"
                "These are build artifacts. Run instead:\n"
                "  cd browse-ui && pnpm build"
            )
        return None
```

### 2.3 New rule: `nextjs_typecheck.py`

**File:** `hooks/rules/nextjs_typecheck.py`  
**Event:** `postToolUse`  
**Purpose:** After editing `.ts`/`.tsx` files under `browse-ui/src/`, remind to run
`pnpm typecheck`.

```python
"""nextjs_typecheck.py — postToolUse: suggest typecheck after TS edits."""
from pathlib import Path

from . import Rule
from .common import MARKERS_DIR, info

TS_EDIT_COUNTER = MARKERS_DIR / "ts-edit-count"


class NextjsTypecheckRule(Rule):
    """Remind to run typecheck after browse-ui TS edits."""

    name = "nextjs-typecheck-reminder"
    events = ["postToolUse"]
    tools = ["edit", "create"]

    def evaluate(self, event, data):
        tool_args = data.get("toolArgs", {})
        if not isinstance(tool_args, dict):
            return None

        file_path = tool_args.get("path", "")
        if not file_path:
            return None

        if not any(file_path.endswith(ext) for ext in (".ts", ".tsx")):
            return None
        if "browse-ui/" not in file_path:
            return None

        # Increment counter
        count = 0
        try:
            if TS_EDIT_COUNTER.is_file():
                count = int(TS_EDIT_COUNTER.read_text().strip())
        except (ValueError, OSError):
            pass
        count += 1
        try:
            MARKERS_DIR.mkdir(parents=True, exist_ok=True)
            TS_EDIT_COUNTER.write_text(str(count))
        except OSError:
            pass

        if count >= 3 and count % 3 == 0:
            return info(
                f"\n  ⚠️ TS REMINDER: {count} browse-ui .ts/.tsx files edited.\n"
                "  Run: cd browse-ui && pnpm typecheck\n"
            )
        return None
```

### 2.4 New rule: `pnpm_lockfile_guard.py`

**File:** `hooks/rules/pnpm_lockfile_guard.py`  
**Event:** `preToolUse`  
**Purpose:** Block `git commit` if staged files include `browse-ui/package.json` changes
but `pnpm-lock.yaml` is not also staged.

```python
"""pnpm_lockfile_guard.py — Block commit when package.json changed without lockfile."""
import re
import subprocess

from . import Rule
from .common import deny


class PnpmLockfileGuardRule(Rule):
    """Deny git commit if browse-ui/package.json staged without pnpm-lock.yaml."""

    name = "pnpm-lockfile-guard"
    events = ["preToolUse"]
    tools = ["bash"]

    def evaluate(self, event, data):
        tool_args = data.get("toolArgs", {})
        if not isinstance(tool_args, dict):
            return None

        command = tool_args.get("command", "")
        if not re.search(r"\bgit\b.*\bcommit\b", command):
            return None

        try:
            result = subprocess.run(
                ["git", "diff", "--cached", "--name-only"],
                capture_output=True, text=True, timeout=5
            )
            staged = set(result.stdout.strip().splitlines())
        except Exception:
            return None

        pkg_staged = "browse-ui/package.json" in staged
        lock_staged = "browse-ui/pnpm-lock.yaml" in staged

        if pkg_staged and not lock_staged:
            return deny(
                "🚫 browse-ui/package.json is staged but pnpm-lock.yaml is not.\n"
                "Run: cd browse-ui && pnpm install\n"
                "Then: git add browse-ui/pnpm-lock.yaml"
            )
        return None
```

### 2.5 Register new rules in `__init__.py`

**File:** `hooks/rules/__init__.py`

```python
# --- ADD imports ---
from .block_edit_dist import BlockEditDistRule
from .nextjs_typecheck import NextjsTypecheckRule
from .pnpm_lockfile_guard import PnpmLockfileGuardRule
from .block_unsafe_html import BlockUnsafeHtmlRule

# --- ADD to ALL_RULES list ---
    # preToolUse (after SyntaxGateRule)
    BlockEditDistRule(),
    PnpmLockfileGuardRule(),
    BlockUnsafeHtmlRule(),
    # postToolUse (after TentacleSuggestRule)
    NextjsTypecheckRule(),
```

<!-- FIXED in cross-review pass: MAJOR-8 — added block_unsafe_html rule -->

### 2.6 New rule: `block_unsafe_html.py`

**File:** `hooks/rules/block_unsafe_html.py`
**Event:** `preToolUse`
**Purpose:** Block edits that add `dangerouslySetInnerHTML` without a `DOMPurify.sanitize()` or `rehype-sanitize` call in the same code chunk. Prevents XSS from session data containing user-controlled content.

```python
"""block_unsafe_html.py — Block dangerouslySetInnerHTML without sanitize."""
import re

from . import Rule
from .common import deny


class BlockUnsafeHtmlRule(Rule):
    """Deny edits adding dangerouslySetInnerHTML without sanitize in same chunk."""

    name = "block-unsafe-html"
    events = ["preToolUse"]
    tools = ["edit", "create"]

    def evaluate(self, event, data):
        tool_args = data.get("toolArgs", {})
        if not isinstance(tool_args, dict):
            return None

        file_path = tool_args.get("path", "")
        if not file_path or not any(file_path.endswith(ext)
                                     for ext in (".tsx", ".jsx", ".ts", ".js")):
            return None

        # Check new content for dangerouslySetInnerHTML
        new_str = tool_args.get("new_str", "") or tool_args.get("file_text", "")
        if not new_str:
            return None

        if "dangerouslySetInnerHTML" in new_str:
            # Check if sanitize is also present
            if not re.search(r"(DOMPurify\.sanitize|sanitize\(|rehype-sanitize)", new_str):
                return deny(
                    "🚫 dangerouslySetInnerHTML detected without sanitization.\n"
                    "Session data may contain user-controlled content (XSS risk).\n"
                    "Use DOMPurify.sanitize() or render via <Highlight> component.\n"
                    "See 01-system-architecture.md §6.4 for approved patterns."
                )
        return None
```

---

## 3. Pre-commit Hook (`hooks/pre-commit`)

### 3.1 Current state

Lines 42-64: UI inline-style guard for `browse/routes/*.py`.  
Lines 67-100: Skill/agent lint via `lint-skills.py`.

### 3.2 Additions needed

Insert **after** the inline-style guard block (line 65) and **before** the skill lint
(line 67):

```sh
# ── browse-ui TypeScript lint guard ───────────────────────────────────────
STAGED_TS=$(git diff --cached --name-only --diff-filter=ACM | grep -E '^browse-ui/src/.*\.(ts|tsx)$' || true)
if [ -n "$STAGED_TS" ]; then
    # Check pnpm availability
    if command -v pnpm >/dev/null 2>&1; then
        BROWSE_UI_DIR="$HOME/.copilot/tools/browse-ui"
        if [ -d "$BROWSE_UI_DIR" ]; then
            echo "🔍 Linting staged browse-ui TypeScript files..."
            # ESLint with cache for speed
            echo "$STAGED_TS" | xargs pnpm --prefix "$BROWSE_UI_DIR" eslint --cache --no-error-on-unmatched-pattern 2>/dev/null
            ESLINT_EXIT=$?
            if [ $ESLINT_EXIT -ne 0 ]; then
                echo "❌ ESLint errors in browse-ui — fix before committing."
                exit 1
            fi

            # Quick typecheck (only if >3 files staged — full tsc is slow)
            TS_COUNT=$(echo "$STAGED_TS" | wc -l | tr -d ' ')
            if [ "$TS_COUNT" -gt 3 ]; then
                echo "🔍 Running TypeScript type-check..."
                pnpm --prefix "$BROWSE_UI_DIR" tsc --noEmit --pretty 2>/dev/null
                TSC_EXIT=$?
                if [ $TSC_EXIT -ne 0 ]; then
                    echo "❌ TypeScript errors in browse-ui — fix before committing."
                    exit 1
                fi
            fi
        fi
    fi
fi
# ──────────────────────────────────────────────────────────────────────────

# ── browse-ui dist freshness check ────────────────────────────────────────
STAGED_UI_SRC=$(git diff --cached --name-only --diff-filter=ACM | grep -E '^browse-ui/src/' || true)
STAGED_UI_DIST=$(git diff --cached --name-only --diff-filter=ACM | grep -E '^browse-ui/dist/' || true)
if [ -n "$STAGED_UI_SRC" ] && [ -z "$STAGED_UI_DIST" ]; then
    echo "⚠️  browse-ui/src/ changed but browse-ui/dist/ not staged."
    echo "   Run: cd browse-ui && pnpm build && git add browse-ui/dist/"
    exit 1
fi
# ──────────────────────────────────────────────────────────────────────────
```

---

## 4. Pre-push Hook (`hooks/pre-push`)

### 4.1 Current state

Lines 1-39: Only checks `dispatched-subagent-active` marker. No test suite execution.

### 4.2 Recommended addition

Add a full test suite gate **after** the subagent check (line 38), before `exit 0`:

```sh
# ── Full test suite before push ───────────────────────────────────────────
# Run Python tests + browse-ui tests if available.
# Fail-open: skip if commands fail or are unavailable.
TOOLS_DIR="$HOME/.copilot/tools"

# Python tests
if [ -f "$TOOLS_DIR/run_all_tests.py" ]; then
    echo "🧪 Running Python test suite..."
    "$PYTHON_BIN" "$TOOLS_DIR/run_all_tests.py" 2>&1
    PY_EXIT=$?
    if [ $PY_EXIT -ne 0 ]; then
        echo "❌ Python tests failed — push blocked."
        exit 1
    fi
fi

# browse-ui tests
BROWSE_UI_DIR="$TOOLS_DIR/browse-ui"
if [ -d "$BROWSE_UI_DIR" ] && command -v pnpm >/dev/null 2>&1; then
    echo "🧪 Running browse-ui tests..."
    pnpm --prefix "$BROWSE_UI_DIR" test --run 2>&1
    UI_EXIT=$?
    if [ $UI_EXIT -ne 0 ]; then
        echo "❌ browse-ui tests failed — push blocked."
        exit 1
    fi

    echo "🏗️  Verifying browse-ui build..."
    pnpm --prefix "$BROWSE_UI_DIR" build 2>&1
    BUILD_EXIT=$?
    if [ $BUILD_EXIT -ne 0 ]; then
        echo "❌ browse-ui build failed — push blocked."
        exit 1
    fi
fi
# ──────────────────────────────────────────────────────────────────────────
```

**Trade-off:** This makes `git push` slower (~30-60s). Consider behind an env var
`SK_PRE_PUSH_TESTS=1` (default off) to opt-in.

---

## 5. Skill (Anthropic SKILL.md Format)

### 5.1 Current skills

14 skills under `skills/`, each with `SKILL.md` using YAML frontmatter:

```yaml
---
name: <kebab-case>
description: >
  Multi-line description. Trigger phrases listed.
---
```

### 5.2 Recommendation: Add `browse-ui-development` skill

**Rationale:** Agents working on `browse-ui/` need context about shadcn patterns, pnpm
commands, file structure, test commands, and gotchas specific to this repo.

**File:** `skills/browse-ui-development/SKILL.md`

```markdown
---
name: browse-ui-development
description: >
  Next.js + TypeScript + shadcn/ui development guide for the browse-ui module.
  Use when editing browse-ui/ files, creating new components, running TS tests,
  or troubleshooting build errors. Trigger phrases: "browse-ui", "next.js component",
  "shadcn component", "browse-ui build", "browse-ui test".
---

# Browse UI Development

Development guide for the `browse-ui/` Next.js module within copilot-session-knowledge.

## When to Use

- Creating or editing files under `browse-ui/src/`
- Adding shadcn/ui components
- Running tests or fixing TypeScript errors
- Building the dist/ artifacts

## Project Structure

```
browse-ui/
├── src/
│   ├── app/              # Next.js App Router pages
│   ├── components/       # React components
│   │   └── ui/           # shadcn/ui primitives (auto-generated)
│   └── lib/              # Utilities, hooks, data fetching
├── tests/                # Vitest / Playwright tests
├── public/               # Static assets
├── dist/                 # Checked-in build artifacts
├── package.json
├── pnpm-lock.yaml
├── tsconfig.json
├── next.config.ts
└── tailwind.config.ts
```

## Key Commands

```bash
cd browse-ui
pnpm install              # Install deps
pnpm dev                  # Dev server (localhost:3000)
pnpm build                # Production build → dist/
pnpm test --run           # Run tests once
pnpm typecheck            # tsc --noEmit
pnpm lint                 # ESLint
```

## Conventions

1. **Use shadcn/ui primitives.** Don't reinvent Button, Card, Dialog, etc.
   Add new components: `pnpm dlx shadcn@latest add <component>`
2. **Never edit `browse-ui/dist/` directly.** Always `pnpm build`.
3. **Design tokens from `tailwind.config.ts`.** Match existing `browse/` Python module
   tokens where applicable.
4. **Tests live in `browse-ui/tests/`.** Use Vitest for unit, Playwright for E2E.
5. **Import from `@/` alias** — maps to `src/`.

## Common Gotchas

- `pnpm-lock.yaml` must be committed with `package.json` changes.
- `dist/` is checked into git (not ignored) — rebuild before committing source changes.
- Server components vs client components: default to server; add `"use client"` only when
  needed (hooks, event handlers, browser APIs).
```

### 5.3 Alternative: Embed in `.agent.md` global?

This repo has no `.agent.md` at root. The custom instructions live in
`~/.copilot/.agent.md` (user-level). A skill is better because:

- Skills are **opt-in** (triggered by context), not always loaded.
- Keeps the global instructions lean (already ~150 lines in custom_instruction).
- Follows existing pattern (14 skills already present).

**Verdict: Create the skill. Also add a brief mention in global instructions (see §6).**

---

## 6. Copilot CLI Instructions

### 6.1 Current state

No `.agent.md` in repo root. Custom instructions exist as embedded config
(the `<custom_instruction>` block referencing this repo's architecture).

### 6.2 Recommended: Create `.github/.agent.md` (project-level)

Copilot CLI supports per-repo `.github/.agent.md`. Create one with browse-ui rules
appended to existing Python rules:

**File:** `.github/.agent.md` (new file)

```markdown
# Project Agent Instructions — copilot-session-knowledge

## browse-ui/ Module Rules

- **Never edit `browse-ui/dist/` directly.** Run `cd browse-ui && pnpm build`.
- **Always use `pnpm`** (not npm/yarn) for browse-ui operations.
- **Tests:** `cd browse-ui && pnpm test --run` (Vitest).
- **Type-check:** `cd browse-ui && pnpm typecheck` after editing .ts/.tsx files.
- **shadcn primitives first:** Use existing shadcn/ui components before building custom ones.
  Add new ones: `pnpm dlx shadcn@latest add <name>`.
- **Import alias:** Use `@/` for src-relative imports.
- **Don't install deps globally.** All browse-ui deps go in `browse-ui/package.json`.

## Python Module Rules (existing)

- Pure stdlib Python 3.10+ — zero pip dependencies.
- Parameterized SQL only — never string-interpolate into queries.
- Each script is standalone — no shared library imports between root scripts.
- Run `python3 test_security.py && python3 test_fixes.py` after Python changes.
```

### 6.3 Update existing custom instructions

In the `<custom_instruction>` block (if managed via `install.py`), add to the
**Architecture** section:

```
- `browse-ui/` — Next.js + TS + shadcn/ui frontend. Built artifacts checked into `dist/`.
  Build: `cd browse-ui && pnpm build`. Tests: `pnpm test --run`. NOT Python — use pnpm.
```

---

## 7. Knowledge Tracking (`extract-knowledge.py`)

### 7.1 Current classifier

7 categories with regex indicators (lines 39-86). All patterns are language-agnostic
(match on natural language, not code syntax). Categories: mistake, pattern, decision,
tool, feature, refactor, discovery.

### 7.2 Gap analysis

The classifier works on **session text**, not source code. TypeScript compile errors or
ESLint patterns would appear in session logs as natural language (e.g., "TypeScript error
TS2345: Argument of type..."). The existing `MISTAKE_INDICATORS` already catch `error`,
`bug`, `fix`, `broken` — these match TS errors in session text.

### 7.3 Recommended: Add TS-specific indicators

Add to `TOOL_INDICATORS` (line 60):

```python
TOOL_INDICATORS = [
    r"(?:install|configure|setup|version|upgrade|dependency)\b",
    r"(?:gradle|maven|docker|redis|postgres|spring\s+boot)\b",
    r"(?:JDK|SDK|IDE|VSCode|extension)\b",
    r"(?:cài|cấu\s+hình|phiên\s+bản|nâng\s+cấp)",
    # --- ADD ---
    r"(?:pnpm|npm|yarn|next\.?js|vite|vitest|eslint|prettier|tailwind)\b",
    r"(?:tsconfig|typescript|tsc|tsx?)\b",
]
```

Add to `MISTAKE_INDICATORS` (line 39):

```python
    # --- ADD ---
    r"(?:TS\d{4,5}|type\s+error|cannot\s+find\s+module|is\s+not\s+assignable)\b",
```

**Impact:** Low risk — these are additive regex patterns in an existing list. Won't affect
existing classifications. Will improve recall for TS-related session knowledge.

---

## 8. Test Pipeline

### 8.1 Current state

`run_all_tests.py` discovers `test_*.py` via `rglob("test_*.py")`. Python-only.
Excludes `.octogent/`, `__pycache__`, `.git`, `fixtures/`.

### 8.2 Option A: Extend `run_all_tests.py` (recommended)

Add a browse-ui test step at the end of the Python test run:

```python
# --- Add after all Python tests complete (before final summary) ---

def run_browse_ui_tests(root: Path) -> tuple[bool, float]:
    """Run browse-ui tests if the module exists and pnpm is available."""
    browse_ui = root / "browse-ui"
    if not browse_ui.is_dir():
        return True, 0.0
    pnpm = shutil.which("pnpm")
    if not pnpm:
        print("  SKIP  browse-ui tests (pnpm not found)")
        return True, 0.0
    t0 = time.time()
    try:
        result = subprocess.run(
            [pnpm, "test", "--run"],
            cwd=str(browse_ui),
            capture_output=True, text=True,
            timeout=TIMEOUT_PER_TEST * 2,  # TS tests may be slower
        )
        elapsed = time.time() - t0
        if result.returncode == 0:
            print(f"  PASS  browse-ui tests ({elapsed:.1f}s)")
            return True, elapsed
        else:
            print(f"  FAIL  browse-ui tests ({elapsed:.1f}s)")
            print(result.stdout[-500:] if result.stdout else "")
            print(result.stderr[-500:] if result.stderr else "")
            return False, elapsed
    except subprocess.TimeoutExpired:
        return False, TIMEOUT_PER_TEST * 2
```

### 8.3 CI structure (future)

**File:** `.github/workflows/ci.yml`

```yaml
name: CI
on:
  push:
    branches: [main]
  pull_request:

jobs:
  python-tests:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: python3 run_all_tests.py

  browse-ui-tests:
    runs-on: ubuntu-latest
    defaults:
      run:
        working-directory: browse-ui
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with:
          node-version: "20"
      - uses: pnpm/action-setup@v4
        with:
          version: 9
      - run: pnpm install --frozen-lockfile
      - run: pnpm typecheck
      - run: pnpm lint
      - run: pnpm test --run
      - run: pnpm build

  dist-freshness:
    needs: browse-ui-tests
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with:
          node-version: "20"
      - uses: pnpm/action-setup@v4
        with:
          version: 9
      - run: cd browse-ui && pnpm install --frozen-lockfile && pnpm build
      - run: git diff --exit-code browse-ui/dist/
        # Fails if dist/ is stale (source changed but not rebuilt)
```

---

## 9. Lint / Format

### 9.1 Python (existing)

`pyproject.toml` configures ruff:

```toml
[tool.ruff]
line-length = 120
target-version = "py310"

[tool.ruff.lint]
select = ["E", "F", "I", "B", "UP"]
ignore = ["E501", "E741"]
```

No changes needed. Ruff does not touch `browse-ui/`.

### 9.2 TypeScript / Next.js (new)

**File:** `browse-ui/.eslintrc.json` (or `eslint.config.mjs` for flat config)

```json
{
  "extends": ["next/core-web-vitals", "next/typescript"],
  "rules": {
    "@typescript-eslint/no-unused-vars": ["error", { "argsIgnorePattern": "^_" }],
    "no-console": ["warn", { "allow": ["warn", "error"] }]
  }
}
```

**File:** `browse-ui/.prettierrc`

```json
{
  "semi": false,
  "singleQuote": true,
  "tabWidth": 2,
  "trailingComma": "all",
  "printWidth": 100
}
```

**File:** `browse-ui/package.json` scripts section:

```json
{
  "scripts": {
    "dev": "next dev",
    "build": "next build",
    "start": "next start",
    "lint": "next lint",
    "typecheck": "tsc --noEmit",
    "test": "vitest",
    "format": "prettier --write 'src/**/*.{ts,tsx,css}'"
  }
}
```

### 9.3 Pre-commit integration

Already covered in §3.2 — the pre-commit hook runs ESLint on staged `.ts/.tsx` files
under `browse-ui/src/`.

---

## 10. `.gitignore` Patch

**File:** `.gitignore` (repo root)

```diff
 # One-time publish scripts
 publish.ps1
 publish.sh

+# browse-ui (Next.js)
+browse-ui/node_modules/
+browse-ui/.next/
+browse-ui/.env.local
+browse-ui/.env*.local
+browse-ui/.turbo/
+# NOTE: browse-ui/dist/ is NOT ignored — checked-in build artifacts.
+
 # Auto-update local state
 .update-state.json
 .update-manifest.json
```

**Verification:** `browse-ui/dist/` must NOT appear in `.gitignore`.

---

## 11. Documentation Updates

### 11.1 `README.md` — Add browse-ui section

Insert after the "Architecture" section (or as a new ## heading):

```markdown
## browse-ui (Frontend)

The `browse-ui/` directory contains a Next.js + TypeScript + shadcn/ui frontend that
provides a modern web interface for browsing session knowledge.

```bash
cd browse-ui
pnpm install          # Install dependencies
pnpm dev              # Start dev server
pnpm build            # Build → dist/ (checked into git)
pnpm test --run       # Run tests
pnpm typecheck        # Type-check
```

See `skills/browse-ui-development/SKILL.md` for detailed conventions.
```

### 11.2 `CONTRIBUTING.md` — Add TS rules

Append to the existing "UI sustainability rules" section (after line 82):

```markdown
### browse-ui/ (Next.js / TypeScript) rules

1. **Use `pnpm` exclusively.** No npm or yarn. Lockfile: `pnpm-lock.yaml`.
2. **shadcn/ui first.** Use existing primitives from `browse-ui/src/components/ui/`.
   Add new ones: `pnpm dlx shadcn@latest add <name>`. Don't build custom versions.
3. **Server components by default.** Only add `"use client"` when hooks/event handlers
   are needed.
4. **Import alias `@/`.** Maps to `src/`. No relative `../../` imports.
5. **Tests in `browse-ui/tests/`.** Vitest for unit, Playwright for E2E.
6. **Rebuild dist/ before committing.** `pnpm build && git add browse-ui/dist/`.
   Pre-commit hook enforces this.
7. **TypeScript strict mode.** `tsconfig.json` uses `"strict": true`. Fix all TS errors
   before committing.
```

### 11.3 `CHANGELOG.md` — Prep entries

Add under `## [Unreleased] / ### Added`:

```markdown
- `browse-ui/` Next.js + TypeScript + shadcn/ui frontend module.
- Hook rules: `block-edit-dist`, `nextjs-typecheck-reminder`, `pnpm-lockfile-guard`.
- Pre-commit: ESLint + TypeScript check for browse-ui staged files.
- Pre-push: full test suite gate (Python + browse-ui).
- Skill: `browse-ui-development` — development guide for the Next.js module.
- `.github/.agent.md` project-level Copilot instructions.
- `auto-update-tools.py`: `browse_ui` category in coverage manifest and pipeline.
- `.gitignore`: browse-ui/node_modules/, .next/, .env.local exclusions.
```

---

## 12. Pre-flight Checklist for Pha 5+

Before starting any code implementation in Pha 5, the orchestrator MUST verify ALL items:

<!-- FIXED in cross-review pass: MAJOR-3 — added Pha 5 Python endpoint deliverables; MAJOR-2 — added pagination note -->
### Python API Deliverables (Pha 5 MUST complete before Pha 6)

- [ ] **P-1.** `/api/eval/stats` JSON endpoint exists in `browse/routes/eval.py` — returns `EvalAggregation` (aggregated feedback data).
- [ ] **P-2.** `/api/compare?a={id}&b={id}` JSON endpoint exists in `browse/routes/session_compare.py` — returns `{ a: SessionCompareData, b: SessionCompareData }`.
- [ ] **P-3.** `/sessions?format=json` returns pagination envelope `{ items, total, page, page_size, has_more }` instead of flat array. This is a **breaking change** for existing consumers — coordinate with Pha 6 scaffold.

### Infrastructure

- [ ] **I-1.** `browse-ui/` directory exists with `package.json` and `pnpm-lock.yaml`.
- [ ] **I-2.** `pnpm install --frozen-lockfile` succeeds in `browse-ui/`.
- [ ] **I-3.** `pnpm build` produces output in `browse-ui/dist/`.
- [ ] **I-4.** `pnpm typecheck` (tsc --noEmit) exits 0.
- [ ] **I-5.** `pnpm lint` (next lint) exits 0.
- [ ] **I-6.** `pnpm test --run` exits 0 (even if 0 tests — no crash).

### Auto-update Pipeline

- [ ] **A-1.** `COVERAGE_MANIFEST` in `auto-update-tools.py` contains `"Browse UI"` category.
- [ ] **A-2.** `classify_changes()` has `browse_ui` key.
- [ ] **A-3.** `post_pull_pipeline()` has browse-ui rebuild step.
- [ ] **A-4.** `python3 auto-update-tools.py --list-coverage` shows `browse-ui/src/`.
- [ ] **A-5.** `python3 auto-update-tools.py --doctor` does not report browse-ui as absent.

### Hooks

- [ ] **H-1.** `hooks/rules/block_edit_dist.py` exists and is importable.
- [ ] **H-2.** `hooks/rules/nextjs_typecheck.py` exists and is importable.
- [ ] **H-3.** `hooks/rules/pnpm_lockfile_guard.py` exists and is importable.
- [ ] **H-4.** `hooks/rules/block_unsafe_html.py` exists and is importable.
- [ ] **H-5.** `hooks/rules/__init__.py` registers all 4 new rules.
- [ ] **H-6.** `hooks/pre-commit` contains ESLint + typecheck + dist freshness checks.
- [ ] **H-7.** `hooks/pre-push` contains full test suite gate (or opt-in env var).

### Skill & Instructions

- [ ] **S-1.** `skills/browse-ui-development/SKILL.md` exists with valid YAML frontmatter.
- [ ] **S-2.** `hooks/lint-skills.py --all` passes (validates new skill).
- [ ] **S-3.** `.github/.agent.md` exists with browse-ui rules.

### Configuration

- [ ] **C-1.** `.gitignore` contains `browse-ui/node_modules/` and `browse-ui/.next/`.
- [ ] **C-2.** `.gitignore` does NOT contain `browse-ui/dist/`.
- [ ] **C-3.** `browse-ui/.eslintrc.json` (or equivalent) configured.
- [ ] **C-4.** `browse-ui/.prettierrc` configured.
- [ ] **C-5.** `browse-ui/tsconfig.json` has `"strict": true`.

### Documentation

- [ ] **D-1.** `README.md` mentions browse-ui with quick-start commands.
- [ ] **D-2.** `CONTRIBUTING.md` has TS/Next.js rules section.
- [ ] **D-3.** `CHANGELOG.md [Unreleased]` has browse-ui entries.

### Tests

- [ ] **T-1.** `python3 run_all_tests.py` still passes (no Python regressions).
- [ ] **T-2.** `run_all_tests.py` includes browse-ui test step (skips gracefully if no pnpm).
- [ ] **T-3.** `python3 test_security.py` passes.
- [ ] **T-4.** `python3 test_fixes.py` has no NEW failures (pre-existing 7 OK).

---

## Appendix A: Existing Gaps Discovered

### A.1 `syntax_gate.py` only covers `.py` files

The `SyntaxGateRule` (line 39: `if not file_path.endswith(".py"): return None`) only
validates Python syntax. No equivalent exists for TypeScript. Consider a future
`ts_syntax_gate.py` that runs `tsc --noEmit` on the edited file — but this is expensive
(full project compilation) and should be deferred.

### A.2 `test-after-edit` reminder only tracks `.py` edits

`TestReminderRule` (edit_tracker.py line 146) only fires for `.py` files. The new
`nextjs_typecheck.py` rule (§2.3) fills this gap for `.ts/.tsx`, but only for
`browse-ui/` paths. Other TS projects won't get reminders.

### A.3 `run_all_tests.py` has no `shutil` import

The proposed `run_browse_ui_tests()` uses `shutil.which()`. Need to add
`import shutil` at the top of `run_all_tests.py`.

### A.4 `hooks/pre-push` has no test gate

Currently only checks subagent marker. Even without browse-ui, a Python test gate
would be valuable. The §4 addition covers both.

### A.5 `extract-knowledge.py` — no TS-specific patterns

Addressed in §7.3. Low risk, high value.

### A.6 No `.github/.agent.md` exists

Repo relies entirely on user-level custom instructions. Per-repo instructions would
help contributors who clone without the full `~/.copilot/` setup. §6.2 addresses this.
