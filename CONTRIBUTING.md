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

## Adding New Scripts

Before adding a new standalone script, first check whether an existing script, hook rule,
or package directory can own the behavior. Root scripts are independent CLI entry points:
do not import one root script from another just to share helpers.

Every new Python file needs an explicit lint surface decision:

1. If it is a root standalone script or belongs to an already covered package-like area
   (`browse/`, `hooks/`, or `scripts/`), it is already inside the CI and local
   pre-commit Ruff surface.
2. If it creates a new package-like Python directory outside that surface, either add
   the directory to `.github/workflows/ci.yml`, `hooks/pre-commit`,
   `tests/test_quality_gates.py`, and
   `docs/ARCHITECTURE.md#python-lint-surface-inventory`, or document why it is
   intentionally outside CI Ruff coverage.
3. If it intentionally stays outside the current lint surface, state that in the PR
   with the reason and the verification command you ran instead. When Ruff is
   installed, local `pre-commit` may still print non-blocking `[advisory]` findings
   for staged out-of-surface Python files.

At minimum, run `python3 -m py_compile <new-script.py>` and the relevant tests for the
behavior the file owns.

## Testing

### Local vs CI enforcement boundary

The local `pre-commit` git hook is **fast and scoped** — it does NOT run the full test suite. What it does enforce (when the respective tool is installed):

- Dispatched-subagent marker guard (always)
- `browse/routes/*.py` inline-`<style>` regression guard (always)
- Python syntax gate on all staged `.py` files via `scripts/check_syntax.py` (fail-open when script absent)
- Ruff format + lint on staged files in the CI surface (fail-open when Ruff absent)
- Ruff advisory scan on staged `.py` files outside the CI surface (fail-open and non-blocking)
- Complexity advisory on staged `.py` files via `scripts/check_complexity.py` (fail-open and non-blocking)
- Prettier format check on staged `browse-ui/src/` files (fail-open when Prettier absent)
- Skill/agent file lint (fail-open when lint-skills.py absent)

The full test suite is **not** run by the hook. Run it manually before submitting a PR:

```bash
python3 scripts/check_syntax.py
python3 scripts/check_complexity.py
python3 run_all_tests.py
```

`scripts/check_complexity.py` is a stdlib-only advisory reporter for Python file
size, function size, and approximate cyclomatic complexity. It scans root
Python scripts plus `browse/`, `hooks/`, and `scripts/` by default, or accepts
targeted paths such as `python3 scripts/check_complexity.py tentacle.py`. The
local `pre-commit` hook also runs it on staged `.py` files and prints
non-blocking warnings for complexity findings. Use `--json` when CI or
automation needs machine-readable metrics.

For faster targeted loops, these focused checks are still useful:

```bash
python3 test_security.py    # SQL injection, pickle, locks, paths
python3 test_fixes.py       # Noise filter, sub-agent, launchd, DB health
```

### Test layout: repo root vs `tests/`

The repo uses a two-tier layout:

- **Canonical root canary tests** (`test_security.py`, `test_fixes.py`, `run_all_tests.py`) live at the repo root and are the primary quality-gate surface. Always run these before submitting a PR.
- **Consolidated tests** live under `tests/` (browse routes, sync, hooks, profile, trend scout, UI, visual snapshots, and more). Run them from the repo root:

  ```bash
  python3 tests/test_browse.py
  python3 tests/test_visual_snapshot.py
  ```

  Each file in `tests/` inserts the repo root into `sys.path` at startup, so it **must** be invoked from the repo root — not from inside `tests/` itself.

`run_all_tests.py` discovers and runs all `test_*.py` files in both locations in one pass:

```bash
python3 run_all_tests.py
```

See [`tests/README.md`](tests/README.md) for the full path-convention details.

### Hook subprocess test isolation

Any test that invokes `hook_runner.py` in a subprocess **must** override `HOME` with a temp
directory. `hook_runner.py` writes to `Path.home()/.copilot/markers/audit.jsonl`, and
`Path.home()` reads `$HOME` at runtime, so without the override every test run appends
`parse-error`, `deny-dry`, and `errorOccurred` entries to the real operator audit log — polluting
the retro pipeline.

Required pattern (see `test_hooks.py` Section 1 for a complete example):

```python
_isolated_home = Path(tempfile.mkdtemp(prefix="test-hooks-home-"))
_isolated_env = {**os.environ, "HOME": str(_isolated_home)}
subprocess.run([sys.executable, str(RUNNER), "preToolUse"], ..., env=_isolated_env)
shutil.rmtree(_isolated_home, ignore_errors=True)
```

> **Do not** rely on `HOOK_DRY_RUN=1` alone for isolation — dry-run suppresses deny output but
> still writes `deny-dry` and `parse-error` audit entries.

CI (`quality-gates` job) runs scoped **Ruff lint** on the surface documented in
[`docs/ARCHITECTURE.md#python-lint-surface-inventory`](docs/ARCHITECTURE.md#python-lint-surface-inventory).
Current coverage:

```
*.py
browse/  hooks/  scripts/
```

If you modify any of those files and have Ruff installed locally, run `ruff format <file>` and `ruff check <file>` before committing. CI will catch scoped lint violations; the local `pre-commit` git hook enforces both `ruff format --check` and `ruff check` on the same surface when Ruff is available locally (fail-open — silently skips when Ruff is not installed). Python outside root scripts and the covered directories is not linted by CI; local `pre-commit` runs `ruff check` for staged out-of-surface `.py` files only as a non-blocking `[advisory]` scan.

CI also runs a non-blocking `Complexity advisory (Ruff C90/PLR)` step on the same
surface with `ruff check --select C90,PLR0911,PLR0912,PLR0913,PLR0915 --statistics`.
It is advisory (`continue-on-error: true`) so maintainers can track baseline counts before
promoting complexity/refactor rules to enforcement.

For `sk-rust/**` changes, `sk CI` includes a blocking startup benchmark
regression gate. `benchmark.py startup` supports `--baseline-file` and
`--regression-threshold`; CI creates the first `.benchmarks/sk-startup-baseline.json`
baseline when absent and fails later runs only when median startup time exceeds the
cached baseline by more than 20% and a 5ms absolute floor. CI also runs a non-blocking RustSec `cargo audit`
advisory. The workflow installs `cargo-audit`, runs `cargo audit --file Cargo.lock`,
and keeps `continue-on-error: true` on the audit step until the documented TODO is
removed after the baseline is clean. The install step remains blocking so missing
tooling does not masquerade as a clean advisory scan.

For `browse-ui/` changes, CI runs `pnpm format:check`. Fix formatting locally with `cd browse-ui && pnpm format` before committing. The always-on `e2e-smoke` job runs the Playwright `behavioral` project on push/PR; visual snapshots stay in the manual-only `e2e-visual` job gated by `workflow_dispatch`.

The browse-ui ESLint baseline promotes strictness by clean zone: repo-wide
`@typescript-eslint/no-explicit-any` remains advisory (`warn`) while clean,
low-churn directories such as `src/lib/**/*.{ts,tsx}` set the same rule to
`error`. Expand this pattern only after a directory has a clean lint baseline.

For `remote-terminal/`, `npm run lint` still reports legacy complexity, size,
parameter, and unused-variable warnings without blocking the whole package. Clean
files (`pty-daemon.js` and `test/client.test.js`) promote those same rules to
errors and are enforced by `npm run lint:clean` with `--max-warnings=0`. The
remote-terminal high-severity dependency audit is blocking via `npm run
audit:high` because the current audit baseline is clean.

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

### Operator console (`/v2/chat`)

The browser-managed Copilot CLI console spans three layers. Treat contract changes as cross-layer work:

- **`browse/core/operator_console.py`** — execution, event parsing, persistence, and path confinement.
- **`browse/api/operator.py`** — authenticated REST + SSE endpoints under `/api/operator/*`.
- **`browse-ui/src/app/chat/` and `browse-ui/src/components/chat/`** — shell, transcript, composer, session dialog, and file review UI.

When you change the operator API contract, update `browse-ui/src/lib/api/types.ts` and `browse-ui/src/lib/api/schemas.ts` in the same PR.

### Playwright E2E

Behavioral browser tests live in `browse-ui/e2e/` and are the main smoke surface for the shipped UI:

- `smoke.spec.ts` — core route and diff-viewer coverage
- `shortcuts.spec.ts` — navigation chords and keyboard behavior
- `chat.spec.ts` — `/v2/chat` shell, persisted history, preview, and inline diff review

Run Playwright locally:

```bash
cd browse-ui
pnpm test:e2e --project behavioral
```

`playwright.config.ts` builds the export, creates the fixture DB, and boots the Python browse server automatically. No separate local server startup is required for the E2E suite.

## Shipping checklist — operator console changes

Use this checklist before merging any PR that touches the operator backend, `/api/operator/*`, `browse-ui/src/app/chat/`, `browse-ui/src/components/chat/`, or `browse-ui/e2e/chat.spec.ts`.

### Python backend

- [ ] `python3 -c "import ast; ast.parse(open('browse/api/operator.py').read())"`
- [ ] `python3 -c "import ast; ast.parse(open('browse/core/operator_console.py').read())"`
- [ ] `python3 tests/test_browse_operator_api.py`
- [ ] `python3 test_security.py`
- [ ] `python3 test_fixes.py`

### TypeScript / UI

- [ ] `cd browse-ui && pnpm typecheck`
- [ ] `cd browse-ui && pnpm lint`
- [ ] `cd browse-ui && pnpm format:check`
- [ ] `cd browse-ui && pnpm test`
- [ ] `cd browse-ui && pnpm build`

### Browser smoke

- [ ] `cd browse-ui && pnpm test:e2e --project behavioral`

### Compatibility notes

- `watch-sessions.py` does not need special coordination for operator-only changes; normal Copilot session artifacts are still discovered on the next polling cycle.
- `auto-update-tools.py` does not restart the browse server. Restart it manually after Python-side operator changes.
- `browse-ui/dist/` is a generated, ignored build artifact. Rebuild it locally with `cd browse-ui && pnpm build` or run the local app with `cd browse-ui && node scripts/run-local.mjs -- --port <port> --token <token>`.

## Shipping checklist — host management / host-profile changes

Use this checklist before merging any PR that touches `browse-ui/src/providers/host-provider.tsx`, `browse-ui/src/lib/host-profiles.ts`, `browse-ui/src/components/hosts/`, or `browse-ui/src/components/layout/header.tsx`.

### TypeScript / UI

- [ ] `cd browse-ui && pnpm typecheck`
- [ ] `cd browse-ui && pnpm lint`
- [ ] `cd browse-ui && pnpm format:check`
- [ ] `cd browse-ui && pnpm test` — covers `src/app/settings/page.test.tsx` and `src/app/chat/chat-shell.test.tsx`
- [ ] `cd browse-ui && pnpm build`

### Browser smoke

- [ ] `cd browse-ui && pnpm exec playwright test e2e/chat.spec.ts --grep "header host switcher"`
- [ ] Manual: open Settings → Hosts & connections, add/remove a host, verify header dropdown updates immediately (same-tab refresh via `BROWSE_HOST_CHANGE_EVENT`)
- [ ] Manual: open New Chat dialog, verify host pre-populates from the global active host

### Compatibility notes

- `host-profiles.ts` reads/writes `localStorage` only; no server-side state is involved.
- Adding a new field to `HostProfile` requires a matching update to `hostProfileSchema` in `browse-ui/src/lib/api/schemas.ts` and the `HostProfile` type in `browse-ui/src/lib/api/types.ts`.
- `LOCAL_HOST` (id `"local"`) is an immutable sentinel — never write it to localStorage or allow it to be deleted via the management UI.
