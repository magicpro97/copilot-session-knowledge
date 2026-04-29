# Contributing to Copilot Session Knowledge

Thank you for your interest in contributing! This guide will help you get started.

> **Agent contributors:** read [`AGENTS.md`](AGENTS.md) (root instruction surface) and [`docs/AGENT-RULES.md`](docs/AGENT-RULES.md) (full rule text) before starting any task.  
> **Claude Code users:** `CLAUDE.md` at the repo root imports `AGENTS.md` automatically via `@AGENTS.md`.  
> **Architecture & conventions reference:** [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)

## Reporting Bugs

1. [Open a GitHub issue](https://github.com/magicpro97/copilot-session-knowledge/issues/new)
2. Include: steps to reproduce, expected vs actual behavior, Python version, OS

## Suggesting Features

Open an issue with the `enhancement` label. Describe the use case and expected behavior.

## Development Setup

```bash
# Clone the repo
git clone https://github.com/magicpro97/copilot-session-knowledge.git ~/.copilot/tools
cd ~/.copilot/tools

# No dependencies to install — pure stdlib Python 3.10+

# Run tests
python3 test_security.py    # 9 security tests
python3 test_fixes.py       # 65 tests
```

## Code Style

> Full conventions reference: [docs/ARCHITECTURE.md#conventions](docs/ARCHITECTURE.md#conventions)

- **Pure stdlib Python 3.10+** — zero pip dependencies required
- **Each script is standalone** — no shared library imports between scripts
- **Parameterized SQL only** — all user input uses `?` placeholders, never string interpolation
- **Windows encoding fix** — every script starts with `if os.name == "nt": sys.stdout.reconfigure(encoding="utf-8")`
- **JSON serialization only** — never use pickle

## Testing

### Local vs CI enforcement boundary

The local `pre-commit` git hook is **fast and scoped** — it does NOT run the full test suite. What it does enforce (when the respective tool is installed):

- Dispatched-subagent marker guard (always)
- `browse/routes/*.py` inline-`<style>` regression guard (always)
- Ruff format + lint on staged files in the CI surface (fail-open when Ruff absent)
- Prettier format check on staged `browse-ui/src/` files (fail-open when Prettier absent)
- Skill/agent file lint (fail-open when lint-skills.py absent)

The full test suite is **not** run by the hook. Run it manually before submitting a PR:

```bash
python3 scripts/check_syntax.py
python3 run_all_tests.py
```

For faster targeted loops, these focused checks are still useful:

```bash
python3 test_security.py    # SQL injection, pickle, locks, paths
python3 test_fixes.py       # Noise filter, sub-agent, launchd, DB health
```

CI (`quality-gates` job) runs a scoped **Ruff lint** on the following files and directories:

```
embed.py  scout-config.py  scout-status.py
sync-config.py  sync-daemon.py  sync-status.py
migrate.py  generate-summary.py
briefing.py  learn.py  query-session.py  extract-knowledge.py
build-session-index.py  tentacle.py
checkpoint-diff.py  checkpoint-restore.py  checkpoint-save.py
browse/  hooks/
```

If you modify any of those files and have Ruff installed locally, run `ruff format <file>` and `ruff check <file>` before committing. CI will catch scoped lint violations; the local `pre-commit` git hook enforces both `ruff format --check` and `ruff check` on the same surface when Ruff is available locally (fail-open — silently skips when Ruff is not installed). Other root scripts outside this surface are not currently linted by CI.

For `browse-ui/` changes, CI runs `pnpm format:check`. Fix formatting locally with `cd browse-ui && pnpm format` before committing.

If you need a narrow syntax-only check for a modified file:

```bash
python3 -c "import ast; ast.parse(open('your_file.py').read())"
```

## Pull Request Process

1. Fork the repo and create a feature branch
2. Make your changes following the code style above
3. Run both test suites — no new failures allowed
4. Submit a PR with a clear description of the change

## Security

See [SECURITY.md](SECURITY.md) for vulnerability reporting. Never commit secrets or API keys.

## UI sustainability rules

All changes under `browse/` MUST obey:

1. **Token-first.** No hardcoded colors/spacing/font sizes in Python routes OR in `app.css`.
   Use design tokens from `browse/static/css/tokens.css` (`var(--space-2)`, `var(--fg)`, `var(--radius-md)`, …).
2. **No inline `<style>` in routes.** Pre-commit hook blocks it; test `test_no_inline_style_in_routes()`
   enforces at runtime. Only allowed exception: `browse/routes/dashboard.py`'s `.db-chart-wrap` block
   (uplot-coupled). Move CSS to `browse/static/css/app.css`.
3. **Component-first rendering.** Common UI patterns (header, stat grid, table, banner, empty state,
   card, badge) MUST go through `browse.components.primitives`. If a pattern is missing, add a
   primitive with docstring + unit tests + a demo in `/style-guide` — do not inline-render.
4. **Dark mode parity.** Every new token declared in `:root` of `tokens.css` MUST have a
   `[data-theme="dark"]` override (or be theme-invariant by design, documented via comment).
5. **Accessibility baseline.** Focus rings visible (use `:focus-visible`), contrast ≥ 4.5:1 for body
   text, icon-only buttons get `aria-label`, heading hierarchy strictly nested (`base_page()`
   emits `<h1>`, sections use `<h2>`; `page_header(level=...)` clamps 2-4).
6. **Visual snapshot test** (`tests/test_visual_snapshot.py`) fails on unintended HTML drift.
   Run `UPDATE_SNAPSHOTS=1 python3 tests/test_visual_snapshot.py` to refresh baselines after
   intentional changes.

## browse-ui (Next.js v2 UI)

`browse-ui/` is the primary browse UI surface. Prefer implementing UI/product changes in v2 first; touch legacy `browse/routes/*.py` HTML only for compatibility fixes or deprecation guidance.

Typical workflow:

```bash
cd browse-ui
pnpm install
pnpm typecheck
pnpm format:check
pnpm build
```

Changes under `browse-ui/src/` follow TypeScript/React conventions. Key rules:

1. **Never edit `browse-ui/dist/` directly.** Run `cd browse-ui && pnpm build` instead.
   The `block-edit-dist` hook will block direct edits.
2. **Staged `package.json` requires lockfile.** If you change `browse-ui/package.json`, run
   `pnpm install` and stage `pnpm-lock.yaml` too. The `pnpm-lockfile-guard` hook enforces this.
3. **No `dangerouslySetInnerHTML` without sanitization.** Use `DOMPurify.sanitize()` or render
   via the `<Highlight>` component. The `block-unsafe-html` hook enforces this.
4. **Typecheck after TS edits.** Run `cd browse-ui && pnpm typecheck` after editing `.ts`/`.tsx`.
