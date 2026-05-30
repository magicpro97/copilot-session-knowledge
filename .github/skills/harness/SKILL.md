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
# Scaffold harness for current project (auto-detects language/commands)
sk harness init

# Verify harness config is valid
sk harness doctor

# Show registered commands
sk harness show

# Enable dispatch middleware for a command
SK_HARNESS=1 sk briefing "my task"
```

## What This Skill Does

This skill helps you:
1. **Initialize** a `harness.yaml` and `.harness/` structure for any project
2. **Configure** success criteria that gate AI agent merges
3. **Enable** dispatch middleware (`SK_HARNESS=1`) for telemetry and hooks
4. **Wire** harness checks into GitHub Actions CI
5. **Debug** non-reproducible agent results with structured evidence

The core idea: harness configuration produces a 36% performance gap with the same AI model
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
sk harness init                        # scaffold for current directory
sk harness init --target /path         # scaffold for a specific path
sk harness init --name my-project      # set project name in harness.yaml
sk harness init --skeleton-only        # create .harness/ dirs only, no harness.yaml
sk harness init --ci                   # also create .github/workflows/harness-ci.yml
sk harness init --yes                  # non-interactive, accept all defaults
```

Detects project type from: `pyproject.toml`, `setup.py`, `requirements.txt` (Python),
`package.json` (Node), `Cargo.toml` (Rust), `go.mod` (Go), `pom.xml`/`build.gradle` (Java).

### `sk harness show` — Display Registered Commands

```bash
sk harness show                # show all registered commands
sk harness show --tag test     # filter by tag
sk harness show --json         # JSON output
```

### `sk harness check` — Validate Harness State

```bash
sk harness check               # check harness.yaml + manifest consistency
sk harness check --json        # JSON output for scripting
```

### `sk harness doctor` — Full Harness Diagnosis

```bash
sk harness doctor              # check commands resolve, criteria reachable, env setup valid
sk harness doctor --json       # JSON output
```

### `sk harness config` — Manage Harness Configuration

```bash
sk harness config list         # list all config keys
sk harness config get <key>    # get a specific value
sk harness config set <key> <value>  # set a value
```

### Enable Dispatch Middleware

```bash
SK_HARNESS=1 sk <command>             # enable middleware for one command
export SK_HARNESS=1 && sk briefing    # enable for session
SK_DRY_RUN=1 SK_HARNESS=1 sk check   # dry-run: test hooks without executing command
SK_DEBUG_TIMING=1 SK_HARNESS=1 sk check  # emit per-hook timing to stderr
```

## `harness.yaml` Schema

```yaml
harness:
  name: my-project          # project identifier
  version: "1.0"            # schema version

environment:
  type: python              # python | node | rust | go | java | generic
  setup: []                 # commands to run before agent tasks (e.g. pip install -e .)

commands:
  test: python3 run_all_tests.py    # primary test command
  lint: ruff check .                # lint command
  format_check: ruff format --check .
  build: ~                          # null if not applicable

success_criteria:
  - id: tests-pass
    command: python3 run_all_tests.py
    required: true           # required=true gates CI merges
    description: All tests must pass
  - id: lint-clean
    command: ruff check .
    required: false          # required=false is advisory only
    description: Lint should be clean

ci:
  enabled: false             # set to true when CI workflow is ready
  provider: github-actions
  on: [push, pull_request]
```

## Workflows

### New Project Setup

```bash
# 1. Initialize harness
sk harness init --yes

# 2. Review and adjust harness.yaml (add your real test commands)
$EDITOR harness.yaml

# 3. Validate the config
sk harness doctor

# 4. Enable harness for an agent task
SK_HARNESS=1 sk briefing "implement feature X"

# 5. Review telemetry
cat ~/.copilot/markers/harness-telemetry.jsonl | tail -5
```

### Existing Project Retrofit

```bash
# 1. Run init — it detects existing commands automatically
sk harness init

# 2. Promote stable criteria to required: true
# Edit harness.yaml: change required: false → required: true for stable tests

# 3. Add CI gating (optional)
sk harness init --ci
```

### CI Integration (GitHub Actions)

Running `sk harness init --ci` generates `.github/workflows/harness-ci.yml`:

```yaml
# Generated by sk harness init --ci
name: Harness CI
on: [push, pull_request]
jobs:
  harness:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Run required success criteria
        run: |
          python3 run_all_tests.py   # from harness.yaml success_criteria[required=true]
```

Edit the generated file to add secrets, matrix builds, or additional steps.

### Agent Evaluation Workflow

```bash
# 1. Define success criteria in harness.yaml before running agent
# 2. Inject context with sk briefing
sk briefing "implement caching for query-session.py" --compact

# 3. Run agent with harness middleware
SK_HARNESS=1 sk tentacle swarm cache-feature --agent-type general-purpose \
  --model claude-sonnet-4.6

# 4. Check criteria
sk harness check

# 5. Record outcome
sk learn --pattern "Caching retrofit" "harness init detected Python correctly" --tags "harness,caching"
```

## Integration with sk Toolchain

| Tool | How it relates to harness |
|------|--------------------------|
| `sk briefing` | Injects past knowledge into agent tasks; use inside harness-wrapped tasks |
| `sk learn` | Records outcomes after agent runs; complements harness telemetry |
| `sk tentacle` | Orchestrates multi-agent harness runs; owns locking and handoff |
| `sk hooks` | Lifecycle events (sessionStart/End, preToolUse/postToolUse); harness adds its own hooks |

## Limitations

- **Non-determinism**: same harness + same model ≠ same result; use pass@k for measurement
- **API cost**: every harness-wrapped invocation may call APIs; use `SK_DRY_RUN=1` to test
- **Overhead**: ~50–100ms per dispatch; negligible for interactive use
- **Scope**: `SK_HARNESS=1` only wraps the Python shim; native Rust `sk` binary bypasses it
- **No containers**: tasks run in current environment (no Docker isolation like SWE-bench)
- **CI**: only GitHub Actions is auto-generated; other providers need manual adaptation
- **Concurrency**: use `sk tentacle` for multi-agent runs; concurrent bare harness runs can race

> See [docs/HARNESS-PHILOSOPHY.md](../../../docs/HARNESS-PHILOSOPHY.md) for full rationale,
> architecture details, and best practices.
> See [docs/HARNESS.md](../../../docs/HARNESS.md) for API reference.
