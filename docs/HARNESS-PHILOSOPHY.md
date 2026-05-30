# Harness Philosophy

> Canonical philosophy and design rationale for `sk harness` — the universal harness
> engineering layer for AI agent projects.
>
> **API reference:** [docs/HARNESS.md](HARNESS.md) · **Skill:** [.github/skills/harness/SKILL.md](../.github/skills/harness/SKILL.md)

---

## 1. Why Harness Engineering?

### The 36% Performance Gap

The most important empirical finding in AI agent evaluation is not about model size or
parameter count — it is about the harness. The CORE benchmark (arXiv 2412.04524)
demonstrated that **the same AI model, evaluated across different harness configurations,
produces a 36% performance gap**. The model didn't change. The prompts didn't change.
Only the harness changed.

This means:

- A team using a well-structured harness gets the equivalent of a model upgrade for free.
- A team skipping harness engineering is systematically leaving performance on the table.
- Harness configuration is a first-class engineering concern, not an afterthought.

### AI Agents Are Non-Deterministic; Harnesses Make Outcomes Reproducible

An AI agent asked to "fix the bug" will produce a different output on each run. This
non-determinism is fundamental — it is not a defect to be fixed, it is a property to be
managed. Harness engineering accepts this property and builds a structured envelope around
it:

- **Input control**: the agent always receives the same task definition, context, and
  constraints.
- **Environment reproducibility**: dependencies, config, and test data are locked.
- **Output capture**: agent output is captured in a structured, comparable format.
- **Success criteria**: objective measures (tests pass, lint clean, file created) are
  defined before the agent runs.

With these four properties, a harness transforms non-deterministic agent runs into a
measurable distribution. You may not know which specific output the agent will produce,
but you can measure whether it satisfies your criteria.

### Harness Is Infrastructure for AI Work, Like CI Is Infrastructure for Code

Before CI existed, developers ran tests manually — sometimes, inconsistently, and often
not at all. The quality of a codebase depended on individual discipline rather than
systemic enforcement. CI changed this: tests run on every commit, automatically, and the
result gates the merge.

**Harness engineering is the same transition for AI agent work.**

| Without Harness | With Harness |
|----------------|-------------|
| Ad-hoc prompting, inconsistent results | Structured task definition, reproducible inputs |
| No way to measure improvement | Quantified success criteria, regression detection |
| Agent outputs lost or untracked | JSONL telemetry, structured reports |
| "It worked on my machine" | Locked environment, CI-ready gates |
| Vibe coding — merge if it looks right | Evidence-based merges — merge only when gates pass |

The progression is direct: **ad-hoc prompting → harness → measurable AI engineering**.

---

## 2. What Is a Harness?

A harness is a **structured runtime environment** that controls how AI agents receive
tasks, execute work, and report results. It is the glue between AI capability and
measured outcomes.

### Components

#### Task Definition
What the agent must do. Includes:
- Input specification (problem statement, file scope, constraints)
- Expected output description (file created, test passing, JSON produced)
- Success criteria (objective, checkable conditions)
- Metadata (name, tags, timeout, required/optional)

#### Environment Setup
A reproducible environment for every run:
- Locked dependencies (`requirements.txt`, `package.json`, `Cargo.lock`)
- Deterministic configuration (env vars, feature flags)
- Test data and fixtures
- Optional: containerization (Docker, devcontainer)

#### Agent Runner
Dispatches the agent and captures output:
- Wraps the agent invocation with pre/post hooks
- Captures stdout, stderr, timing, exit code
- Injects context (briefing, knowledge entries)
- Supports dry-run mode for testing the harness itself

#### Success Criteria
Objective, checkable conditions that define a passing run:
- Test suite passes (`python3 test_fixes.py`)
- Lint is clean (`ruff check .`)
- Required file exists and is syntactically valid
- No regressions introduced (existing pass tests still pass)

#### Reporter
Aggregates results across runs and surfaces evidence:
- Detects regressions (tests that were passing are now failing)
- Aggregates pass/fail statistics
- Produces evidence for closeout (required by Rule 9)
- Writes to `~/.copilot/markers/harness-telemetry.jsonl`

#### Hooks
Lifecycle events for instrumentation and enforcement:
- `preDispatch`: inject context, validate environment, record start
- `postDispatch`: capture output, run success checks, record end
- `sessionStart`: resume banner, paused-goal detection
- `sessionEnd`: write telemetry, update knowledge

### What a Harness Is Not

A harness is not:
- **A prompt template**: prompts are inputs; the harness is the container for prompt delivery
  and result capture.
- **A test framework**: tests measure code correctness; the harness measures whether the
  agent produced correct code.
- **A CI system**: CI runs tests; the harness runs agents that produce the code CI then tests.
- **A model**: the model is the cognitive engine; the harness is the mechanical infrastructure
  around it.

---

## 3. How `sk harness` Works

### Existing Infrastructure

#### `SK_HARNESS=1` — Dispatch Middleware

Setting `SK_HARNESS=1` in the environment enables the Python dispatch middleware in
`harness/dispatch.py`. Every `sk` command that passes through the shim gets wrapped with:

1. **Pre-dispatch hook pipeline**: timing start, context injection, dry-run check
2. **Command execution**: the actual `sk` subcommand runs
3. **Post-dispatch hook pipeline**: timing end, output capture, telemetry write

```bash
SK_HARNESS=1 sk briefing "my task"
# → preDispatch hook fires
# → briefing runs with context
# → postDispatch hook fires, writes JSONL telemetry
```

The middleware is **fail-open**: if `harness/dispatch.py` crashes, the command still runs.
Hook failures are logged, not fatal.

#### `harness-manifest.json` — Command Registry

The manifest registers 77+ `sk` commands with metadata:
- Command name, description, tags
- Input/output types
- Required/optional flags
- Hook points (which lifecycle events apply)

Used by `sk harness show`, `sk harness check`, and `sk harness doctor` to validate and
display the harness configuration.

#### `DispatchContext` — Per-Invocation State

```python
@dataclass(frozen=True)
class DispatchContext:
    command: str          # e.g. "briefing"
    args: list[str]       # e.g. ["my task", "--compact"]
    dry_run: bool         # SK_DRY_RUN=1
    debug_timing: bool    # SK_DEBUG_TIMING=1
    tools_dir: Path       # SK_TOOLS_DIR or default
```

Each `sk` invocation gets a fresh `DispatchContext`. State does not leak between
invocations (cf. openai/evals fresh Solver pattern).

#### `CommandMeta` — Registry Entry

```python
@dataclass(frozen=True)
class CommandMeta:
    name: str
    description: str
    tags: list[str]
    hooks: list[str]
```

Frozen dataclass: immutable once loaded, safe for concurrent access.

### New: `sk harness init`

`sk harness init` scaffolds a universal harness configuration for any project. It:

1. **Detects project type** — Python (`pyproject.toml`, `setup.py`, `requirements.txt`),
   Node (`package.json`), Rust (`Cargo.toml`), Go (`go.mod`), Java (`pom.xml`, `build.gradle`)
2. **Auto-detects commands** — infers test, lint, format, build commands from project type
3. **Creates `harness.yaml`** — universal config at the project root
4. **Creates `.harness/` structure** — local state directories
5. **Optionally creates CI** — `.github/workflows/harness-ci.yml` with `--ci` flag

```bash
# Scaffold harness for current project
sk harness init

# Scaffold for a specific path with a name
sk harness init --target /path/to/project --name my-project

# Create .harness/ structure only (no harness.yaml)
sk harness init --skeleton-only

# Include GitHub Actions CI workflow
sk harness init --ci

# Non-interactive (accept all defaults)
sk harness init --yes
```

### Environment Variables

| Variable | Default | Effect |
|----------|---------|--------|
| `SK_HARNESS` | `0` | Set to `1` to enable dispatch middleware |
| `SK_DRY_RUN` | `0` | Set to `1` to skip actual command execution |
| `SK_DEBUG_TIMING` | `0` | Set to `1` to emit per-hook timing to stderr |
| `SK_TOOLS_DIR` | `~/.copilot/tools` | Override tools directory path |

---

## 4. When to Use Harness Engineering

### Decision Matrix

| Situation | Recommended Approach |
|-----------|---------------------|
| New project, no tests yet | `sk harness init --skeleton-only` to create structure; add test commands as tests are written |
| New project, starting with tests | `sk harness init` for full config with auto-detected commands |
| Existing project with tests | `sk harness init` — detects existing test/lint commands; review and adjust `harness.yaml` |
| Existing project, no tests | `sk harness init --skeleton-only`; add tests before defining `success_criteria` |
| AI agent development (evals) | Full harness with `success_criteria`, pass@k, LLM-as-judge |
| Simple one-off script | Skip harness; use `sk learn` to record the pattern after it works |
| Multi-agent orchestration | Full harness + `sk tentacle` for orchestration coordination |
| CI failing from agent changes | Retrofit harness with `required: true` success criteria to gate merges |

### Signals That You Need a Harness

**You should start harness engineering when:**

- You have more than one AI agent working on the same codebase and cannot compare their
  results objectively.
- You cannot reproduce an AI agent's results — same prompt, different output, no way to
  understand why.
- CI keeps failing from agent-introduced changes that were "tested" locally but not
  systematically.
- You are making a decision about which AI model or configuration to use, and you have
  no quantitative basis for the comparison.
- You are onboarding a new team member who needs to understand what "a passing agent run"
  looks like.

**You can skip harness engineering when:**

- The task is a one-off with no recurrence (no need to reproduce).
- The agent output is purely informational (no code changes, no CI gates).
- You are prototyping and not yet ready to define success criteria.

---

## 5. Architecture & Components (for `sk`)

### File Map

```
~/.copilot/tools/
├── harness/
│   ├── __init__.py          # Package init, exports
│   ├── dispatch.py          # DispatchContext, run_with_hooks(), hook pipeline
│   ├── manifest.py          # lru_cache manifest loader
│   └── meta.py              # CommandMeta dataclass (frozen)
├── harness-manifest.json    # Command registry (77+ entries)
├── harness-init.py          # sk harness init implementation
│
# Per-project (created by sk harness init):
<project-root>/
├── harness.yaml             # Universal harness config
└── .harness/
    ├── tasks/               # Task definition files (.yaml)
    ├── reports/             # Agent run reports (.jsonl)
    └── state/               # Harness runtime state
```

### `harness/dispatch.py` — Dispatch Middleware

```python
def run_with_hooks(ctx: DispatchContext, fn: Callable) -> int:
    """
    Run fn wrapped with pre/post hook pipeline.
    Fail-open: hook failures are logged, fn always runs.
    Writes JSONL record to harness-telemetry.jsonl on completion.
    """
```

Key behaviors:
- Fresh `DispatchContext` per invocation (no state leakage)
- JSONL telemetry written to `~/.copilot/markers/harness-telemetry.jsonl`
- `SK_DRY_RUN=1` skips `fn` but still runs hooks (for testing the hook pipeline itself)
- `SK_DEBUG_TIMING=1` emits per-hook wall-clock timing to stderr

### `harness/manifest.py` — Manifest Loader

```python
@lru_cache(maxsize=1)
def load_manifest() -> dict:
    """
    Load harness-manifest.json once and cache.
    Thread-safe via lru_cache.
    """
```

### `harness/meta.py` — CommandMeta

```python
@dataclass(frozen=True)
class CommandMeta:
    name: str
    description: str
    tags: list[str]
    hooks: list[str]
```

Frozen dataclass: immutable, hashable, safe for concurrent reads.

### `harness.yaml` — Per-Project Config

The universal harness configuration file. See [HARNESS.md](HARNESS.md) for full schema.
Key sections:

```yaml
harness:
  name: my-project
  version: "1.0"

environment:
  type: python          # auto-detected: python | node | rust | go | java | generic
  setup: []             # setup commands to run before agent tasks

commands:
  test: python3 run_all_tests.py
  lint: ruff check .
  format_check: ruff format --check .
  build: ~

success_criteria:
  - id: tests-pass
    command: python3 run_all_tests.py
    required: true
  - id: lint-clean
    command: ruff check .
    required: false

ci:
  enabled: false
  provider: github-actions
```

---

## 6. Limitations

### Non-Determinism Is Irreducible

A harness does not eliminate non-determinism — it manages it. The same harness, same model,
same prompt will produce different outputs across runs. Strategies to manage this:

- **Temperature=0**: reduces (but does not eliminate) variance for code generation tasks.
- **pass@k**: run the agent k times and accept if any run passes; report pass rate.
- **Majority vote**: for classification or structured outputs, take the modal answer across
  k runs.
- **LLM-as-judge**: use a strong model (Opus, GPT-4) to evaluate quality dimensions that
  cannot be expressed as binary tests.

A harness makes these strategies tractable by providing reproducible inputs and structured
output capture. It does not make the agent deterministic.

### API Cost

Running agents in a harness costs API credits. Every `SK_HARNESS=1` invocation may
trigger pre/post hooks that themselves call APIs. Budget accordingly:

- Use `SK_DRY_RUN=1` to test the harness pipeline without running the agent.
- Limit pass@k runs in development; use higher k only for benchmark comparisons.
- Cache model outputs where possible (openai/evals-style JSONL caching).

### Dispatch Overhead

The Python dispatch middleware adds approximately 50–100ms per `sk` command invocation.
This is negligible for interactive use and most automation. It becomes visible at scale
(e.g., running 1000 agent tasks in a loop). For high-frequency automation, consider:

- Batching tasks and using a single harness wrapper rather than per-command hooks.
- Profiling with `SK_DEBUG_TIMING=1` to identify slow hooks.

### Multi-Agent Race Conditions

Concurrent harness runs against the same `.harness/` directory will race on:
- JSONL telemetry append (safe: append-only files are race-resistant on most filesystems)
- State files in `.harness/state/` (unsafe: use file locks or `O_CREAT | O_EXCL`)

For multi-agent orchestration, use `sk tentacle` which owns coordination, locking, and
handoff sequencing. Do not attempt concurrent harness runs without tentacle coordination.

### LLM-as-Judge Quality Ceiling

When using an LLM to grade agent output quality (as opposed to binary test pass/fail),
the judge's quality ceiling is the judge model's capability. A weak judge gives noisy
grades. Best practices:

- Use the strongest available model for judging.
- Define the rubric explicitly in the judge prompt.
- Cross-validate judge grades with human spot-checks when the rubric matters.
- Prefer binary/checkable success criteria over LLM judgment where possible.

### Scope: Python Middleware Only

`SK_HARNESS=1` enables the Python dispatch middleware in `harness/dispatch.py`. It
wraps `sk` commands that flow through the Python `sk.py` shim. It does **not** wrap:

- The native Rust `sk` binary (compiled `sk watch`, `sk hooks run`, `sk index embed`,
  `sk sync run`): these bypass the Python shim entirely.
- Direct script invocations: `python3 briefing.py` does not trigger harness hooks.
- Shell commands that are not `sk` subcommands.

If your workflow relies on native Rust paths, harness telemetry will be incomplete.
Use the Python shim explicitly (`python3 sk.py <command>`) or accept partial coverage.

### No Container Isolation

`sk harness` does not spin up Docker containers. Agent tasks run in the current shell
environment, sharing the filesystem, environment variables, and process space. This is
intentional (lower overhead, simpler setup) but means:

- Environment drift between runs is possible (e.g., a previous run modifies a file that
  affects the next run).
- Parallel runs can interfere.
- Reproducibility depends on clean environment discipline, not container isolation.

For full isolation (as in SWE-bench's three-layer Docker hierarchy), `sk harness` is not
the right tool. Use a dedicated eval framework with container support.

### Local-Only CI

`harness.yaml` CI integration targets GitHub Actions (`sk harness init --ci`). Projects
hosted on other CI platforms (GitLab CI, CircleCI, Jenkins) will need to adapt the
generated workflow manually. There is currently no `--ci-provider` flag.

---

## 7. Best Practices

### Starting Out

1. **Use `--skeleton-only` for new projects** before you have tests. Create the `.harness/`
   directory structure now; add `success_criteria` once you have something to test.
   ```bash
   sk harness init --skeleton-only
   ```

2. **Define `success_criteria` before running agents**, not after. Defining criteria after
   seeing agent output introduces confirmation bias. Write the criteria first, then run.

3. **Start with `required: false`** for criteria you are not yet confident are stable. Promote
   to `required: true` once you have verified them across multiple runs.

### During Agent Work

4. **Use `sk briefing` inside agent tasks** to inject past knowledge. The harness controls
   task dispatch; `sk briefing` injects context that improves agent quality.
   ```bash
   sk briefing "my task description" --compact
   ```

5. **Record learnings with `sk learn` after agent runs**. A harness measures outcomes;
   `sk learn` preserves the understanding of why those outcomes happened.
   ```bash
   sk learn --pattern "Harness init pattern" "sk harness init --yes detects Python correctly" --tags "harness,python"
   ```

6. **Use `SK_DRY_RUN=1` to test the harness itself** before running expensive agents.
   Verify that hooks fire, context is injected, and telemetry is written.
   ```bash
   SK_HARNESS=1 SK_DRY_RUN=1 sk briefing "test"
   ```

### Measurement and Review

7. **Review harness telemetry weekly**. Telemetry accumulates in
   `~/.copilot/markers/harness-telemetry.jsonl`. Review pass/fail rates, hook timing,
   and recurring failures.

8. **Gate merges on `required: true` success criteria**. Configure `sk harness init --ci`
   to generate a GitHub Actions workflow that runs success criteria checks on every PR.
   Only merge when required criteria pass.

9. **Use `sk harness doctor`** to validate your harness configuration before a major agent
   run. It checks that commands resolve, criteria are reachable, and environment setup
   looks correct.
   ```bash
   sk harness doctor --json
   ```

### Multi-Agent Work

10. **Use `sk tentacle` for multi-agent orchestration**. Do not run concurrent harness
    agents without tentacle coordination. The tentacle owns locking, scope, and handoff
    sequencing.

11. **One harness per project, not per agent**. Multiple agents working on the same project
    share the same `harness.yaml`. This enforces consistent success criteria across all
    agents.

---

## 8. Further Reading

### sk Documentation
- **[docs/HARNESS.md](HARNESS.md)** — API reference for `harness/dispatch.py`,
  `harness/manifest.py`, `harness/meta.py`, and `harness.yaml` schema.
- **[docs/ARCHITECTURE.md](ARCHITECTURE.md)** — Full Python/Rust boundary table and
  script inventory.
- **[docs/AGENT-RULES.md](AGENT-RULES.md)** — Mandatory agent rules, including Rule 9
  (claims require evidence) and Rule 3 (test after every change).

### Benchmark Papers
- **SWE-bench** — arXiv 2310.06770 — Three-layer Docker hierarchy, TestSpec, Fail-To-Pass
  + Pass-To-Pass dual metrics. The foundational agent evaluation benchmark.
- **AgentBench** — arXiv 2308.03688 — JSONL task definitions, multi-environment agent
  evaluation (OS, DB, web, game environments).
- **CORE benchmark** — arXiv 2412.04524 — The source of the 36% harness performance gap
  finding. Directly relevant to harness design choices.

### Reference Implementations
- **openai/evals** — Registry + Eval + Solver pattern; TaskState interface; fresh Solver
  per sample. Clean separation of task definition from solver implementation.
- **harness-boot (qwerfunch)** — `spec.yaml + harness.yaml + state.yaml`, idempotent init,
  `.harness/` directory structure. Closest in spirit to `sk harness init`.
- **moai-adk** — Levels pattern (minimal/standard/thorough) with auto-detection based on
  project complexity. Inspiration for `--skeleton-only` and progressive harness adoption.
- **Harness Protocol v1 (harness-kit)** — Plugins + mcp-servers + instructions in
  `harness.yaml`. Future direction for `sk harness` plugin extensibility.

---

*This document describes the philosophy and rationale behind `sk harness`. For commands,
flags, and schema reference, see [docs/HARNESS.md](HARNESS.md).*
