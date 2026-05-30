---
name: harness
description: >
  Universal harness engineering for AI agent projects. Initialize, configure, and manage
  project harnesses that make AI agent work reproducible, measurable, and CI-ready.
  Use when setting up a new AI agent project, retrofitting harness to existing project,
  creating harness.yaml config, running `sk harness init`, debugging non-reproducible
  agent results, defining success criteria for AI tasks, or wiring harness to GitHub Actions CI.
  Trigger phrases: "harness init", "setup harness", "harness engineering", "agent evaluation",
  "success criteria", "sk harness", "harness.yaml", "make agent work reproducible".
---

# Harness Skill

Make AI agent work reproducible, measurable, and CI-ready using `sk harness`.

## Quick Start

```bash
sk harness init                  # scaffold — auto-detects project language
sk harness doctor                # verify config is valid
sk harness show                  # list registered commands
SK_HARNESS=1 sk briefing "task"  # enable dispatch middleware
```

## What This Skill Does

1. **Initialize** `harness.yaml` and `.harness/` for any project
2. **Configure** success criteria that gate AI agent merges
3. **Enable** dispatch middleware (`SK_HARNESS=1`) for telemetry and hooks
4. **Wire** harness checks into GitHub Actions CI
5. **Debug** non-reproducible agent results with structured evidence

Core insight: harness configuration produces a 36% performance gap with the same AI model
(CORE benchmark, arXiv 2412.04524). A well-structured harness is worth more than a model upgrade.

## When to Use

| Situation | What to do |
|-----------|-----------|
| New project, no tests yet | `sk harness init --skeleton-only` |
| New project with tests | `sk harness init` (auto-detects commands) |
| Existing project | `sk harness init` (detects existing test/lint commands) |
| >1 AI agent on same codebase | Full harness + `sk tentacle` for orchestration |
| CI failing from agent changes | Retrofit with `required: true` success criteria |
| One-off script, no recurrence | Skip harness; use `sk learn` to record the pattern |

## Commands Reference

### `sk harness init` — Scaffold Harness Config

```bash
sk harness init                              # scaffold for current directory
sk harness init --target /path              # scaffold for a specific path
sk harness init --name my-project           # set project name in harness.yaml
sk harness init --skeleton-only             # create .harness/ dirs only, no harness.yaml
sk harness init --ci                        # also create .github/workflows/harness-ci.yml
sk harness init --no-ci                     # skip CI workflow generation
sk harness init --force                     # overwrite existing harness.yaml
sk harness init --yes                       # non-interactive, accept all defaults
sk harness init --json                      # machine-readable output
```

Detects project type from: `pyproject.toml`, `setup.py`, `requirements.txt` (Python),
`package.json` (Node/bun/pnpm/yarn), `Cargo.toml` (Rust), `go.mod` (Go), `pom.xml`/`build.gradle` (Java).

### Other Commands

```bash
sk harness show [--tag TAG] [--json]         # list registered commands
sk harness check [--json]                    # verify scripts exist on disk
sk harness doctor [--json]                   # full self-check (scripts, DB, hooks)
sk harness config list|get|set               # manage env vars (SK_HARNESS, SK_DRY_RUN,
                                             #   SK_DEBUG_TIMING, SK_TOOLS_DIR)
```

### Enable Dispatch Middleware

```bash
SK_HARNESS=1 sk <command>              # enable for one command
export SK_HARNESS=1                    # enable for session
SK_DRY_RUN=1 SK_HARNESS=1 sk check    # dry-run: test hooks without executing
SK_DEBUG_TIMING=1 SK_HARNESS=1 sk check  # emit per-hook timing to stderr
```

## `harness.yaml` Schema

```yaml
harness:
  name: my-project
  version: "1.0"

environment:
  type: python        # python | node | rust | go | java | generic
  setup: []           # commands to run before agent tasks

commands:
  test: python3 run_all_tests.py
  lint: ruff check .
  format_check: ruff format --check .
  build: ~

success_criteria:
  - id: tests-pass
    command: python3 run_all_tests.py
    required: true     # gates CI merges
    description: All tests must pass
  - id: lint-clean
    command: ruff check .
    required: false    # advisory only
    description: Lint should be clean

reporting:
  format: jsonl
  output_dir: .harness/reports/
  telemetry: true

ci:
  enabled: false
  provider: github-actions
  on: [push, pull_request]
```

## Typical Workflow

```bash
# 1. Initialize harness
sk harness init --yes

# 2. Review and adjust harness.yaml (add your real test commands)
$EDITOR harness.yaml

# 3. Validate
sk harness doctor

# 4. Run agent with harness middleware
SK_HARNESS=1 sk tentacle swarm my-feature --agent-type general-purpose --model claude-sonnet-4.6

# 5. Check criteria passed
sk harness check

# 6. Record outcome
sk learn --pattern "Harness retrofit" "harness init detected Python correctly" --tags "harness"
```

For CI: `sk harness init --ci` generates `.github/workflows/harness-ci.yml`. Edit it to add
secrets, matrix builds, or additional steps as needed.

## Limitations

- **Non-determinism**: same harness + same model ≠ same result; use pass@k for measurement
- **API cost**: every harness-wrapped invocation may call APIs; use `SK_DRY_RUN=1` to test
- **Overhead**: ~50–100ms per dispatch; negligible for interactive use
- **Scope**: `SK_HARNESS=1` only wraps the Python shim; native Rust binary bypasses it
- **No containers**: tasks run in current environment (no Docker isolation like SWE-bench)
- **CI**: only GitHub Actions is auto-generated; other providers need manual adaptation
- **Concurrency**: use `sk tentacle` for multi-agent runs; concurrent bare harness runs can race

> See [docs/HARNESS-PHILOSOPHY.md](../../../docs/HARNESS-PHILOSOPHY.md) for full rationale.
> See [docs/HARNESS.md](../../../docs/HARNESS.md) for API reference.
