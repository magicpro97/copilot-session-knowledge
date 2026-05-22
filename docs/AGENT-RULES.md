# Agent Rules

> Canonical reference for AI agent behavior rules in copilot-session-knowledge.
>
> These rules are **non-negotiable** and apply to every agent — main session, sub-agent, explore, task, or general-purpose. They are also enforced via `.github/copilot-instructions.md` (injected into every Copilot CLI session) and partially enforced at the hook level.

## Rule 1 — Investigate Before Acting

**NEVER modify code without reading it first.** Before any edit:

1. Use `grep` / `glob` / `view` / LSP tools to read the target file(s)
2. Understand the existing logic, dependencies, and callers
3. Check related files that may be affected by your change
4. Only then make your edit

```
❌ BAD:  User says "fix the search" → immediately edit query-session.py
✅ GOOD: User says "fix the search" → grep for search functions → view the code → check callers → edit
```

## Rule 2 — Briefing Before Complex Tasks

Before starting any task that touches >1 file or involves unfamiliar code:

```bash
sk briefing "your task description"
# fallback: python3 ~/.copilot/tools/briefing.py "your task description"
```

This surfaces past mistakes, proven patterns, and relevant decisions. Skip only for trivial changes (typo fix, renaming, formatting).

The `auto-briefing` hook fires automatically at `sessionStart` and writes a marker. If it has not fired (e.g., in a sub-agent context), run `briefing.py` manually before editing.

## Rule 3 — Test After Every Change

After modifying any Python file, run the relevant tests:

```bash
python3 test_security.py AND python3 test_fixes.py
# test_security.py: required when touching embed.py, sync-knowledge.py, watch-sessions.py, learn.py
# test_fixes.py:    required when touching any script
```

**Both suites are required for closeout.** The verification-gate ledger tracks `py_security` and `py_fixes` as separate evidence keys; both must succeed before `task_complete`, `DONE` handoff, or issue close is permitted.

Do NOT mark a task complete until the relevant tests pass. If you hit a baseline failure, separate pre-existing breakage from regressions you introduced before proceeding.

Python validation runs through `run_all_tests.py`, but individual files use a mix of the custom `test()` helper and `unittest`/`test_*` style. The repo also has GitHub Actions CI and browse-ui quality gates (`pnpm typecheck`, `pnpm lint`, `pnpm format:check`, `pnpm test`, `pnpm build`); run the surfaces relevant to the files you changed.

## Rule 4 — Verify Before Committing

Before `git commit`:
1. `python3 -c "import ast; ast.parse(open('file.py').read())"` for every modified `.py` file
2. Run both test suites (Rule 3)
3. `git diff --stat` to review what you are about to commit

The `syntax_gate.py` preToolUse hook catches syntax errors in `edit`/`create` payloads before they land, but AST-parse verification before commit is a second safety net.

## Rule 5 — Sub-Agent Model Selection

When dispatching sub-agents via the `task` tool, use the appropriate model:

| Task type | Minimum model | Example |
|-----------|--------------|---------|
| Code generation | `claude-sonnet-4.6` | Writing/modifying Python scripts |
| Code review | `claude-sonnet-4.6` | Reviewing changes for bugs |
| Security audit | `claude-opus-4.6` | Auth, data handling, injection risks |
| Exploration | `claude-haiku-4.5` (default OK) | Finding files, reading code |
| Documentation | `claude-sonnet-4` or `haiku` | Writing docs, README |

```
❌ task(agent_type="general-purpose", prompt="fix the search bug...")   # default haiku model!
✅ task(agent_type="general-purpose", model="claude-sonnet-4.6", prompt="fix the search bug...")
```

## Rule 6 — No Guessing

- Don't assume table names — check with `sqlite3 ... ".tables"` or read `migrate.py`
- Don't assume function signatures — use `grep` or LSP to verify
- Don't assume file paths — use `glob` to find them
- If unsure about behavior, write a small test or read the source

## Rule 7 — Docs Output Quality

Agent-authored docs, tentacle handoffs, operator reports, and research outputs must
distinguish four layers. Mixing layers silently or presenting interpretation as fact
is a documentation defect.

| Layer | What it contains | Marking convention |
|-------|-----------------|-------------------|
| **Facts** | Verified, reproducible data: row counts, timestamps, test results, git refs | State directly; cite the source or command that produced it |
| **Interpretation** | Reasoning based on facts: patterns, risks, root causes, inferences | Qualify explicitly: "suggests", "indicates", "likely" |
| **Actions** | Concrete next steps: commands to run, tickets to file, follow-up tentacles | Use imperative; include the executable command |
| **Verification evidence** | Proof that work was done: test log output, CI status, measured diffs | Link or inline the evidence; do not claim verified without it |

**Rules:**

1. Do not present interpretation as fact. Every non-trivial causal claim must be qualified.
2. Every action item must be executable — include the actual command or URL.
3. Every verification claim must include evidence (test log excerpt, CI link, git ref, or pass/fail count).
4. Keep operator/research docs concise. Move lengthy context into appendices or collapsible sections.
5. Operator/research outputs (tentacle handoffs, retro summaries, knowledge-health reports, research-pack summaries) must follow all four layers. Contributor docs (`CONTRIBUTING.md`) keep their existing concise tone.

## Rule 8 — Tentacle Execution Obligations

When running inside a tentacle (dispatched by the orchestrator via `tentacle.py`):

1. **Read the bundle first** — before any edit, read `manifest.json`, `session-metadata.md`, `recall-pack.json`, and `instructions.md` from the bundle path provided in the dispatch prompt.
2. **Stay in scope** — only edit files listed in the tentacle's declared scope. Any edit outside that scope requires a scope escalation note written to the handoff before proceeding.
3. **Mark todos as you complete them** — after completing each task:
   ```bash
   sk tentacle todo <tentacle-name> done <index>
   # fallback: python3 ~/.copilot/tools/tentacle.py todo <tentacle-name> done <index>
   ```
4. **No git operations** — do NOT run `git commit` or `git push`; the orchestrator owns all git operations.
5. **Write a structured handoff before stopping**:
   ```bash
   sk tentacle handoff <tentacle-name> "<summary>" \
     --status <STATUS> [--changed-file <path>] --learn
   # fallback: python3 ~/.copilot/tools/tentacle.py handoff <tentacle-name> "<summary>" \
   #   --status <STATUS> [--changed-file <path>] --learn
   ```
   Use one of `DONE`, `BLOCKED`, `TOO_BIG`, `AMBIGUOUS`, or `REGRESSED` for `<STATUS>`. Add one `--changed-file` per modified file; omit it when no files changed. The handoff must list: which rules changed, which file is source of truth for each rule, and any remaining ambiguity.
6. **Review-ready handoff** — the handoff must include enough detail for an independent reviewer to verify all claims independently. This means attaching or referencing concrete verification evidence (test output, lint result, runtime log) for every claim made in the handoff. A `DONE` handoff with no evidence for its claims is treated as `AMBIGUOUS` by the orchestrator.

> The orchestrator runtime injects the core tentacle workflow (bundle, scope, todo, handoff, and git-operation guidance) per tentacle. The canonical full text lives here.

---

## Rule 9 — Claims Require Evidence

**Any claim about code quality, tool output, or task completion must be backed by concrete, reproducible evidence.** Asserting that something works without running it is a documentation defect — not a verification. This applies equally to issue closeouts, tentacle `DONE` handoffs, and inline comments in PRs.

| Claim | Required evidence |
|-------|------------------|
| "Tool / feature works" | Command output or test log showing successful execution at runtime |
| "Tests pass" | Test runner output with pass/fail counts and any skipped items |
| "Format / lint clean" | Output of the actual formatter/linter command, or an explicit "not proven yet — run `<command>`" |
| "CI is green" | CI run URL or copy of the passing job output |
| "Build succeeds" | Compiler or build tool output confirming exit code 0 |

**Rules:**

1. If you did not run a verification command, say so explicitly: "not proven yet — run `<command>`." Do not imply a passing status without proof.
2. Do not infer status from code inspection alone ("the code looks correct"). Runtime proof is required for correctness claims.
3. **Issue closeouts** must include verification evidence for each acceptance criterion, or explicitly list items that are "not yet proven" and the commands needed to prove them.
4. **Tentacle `DONE` handoffs** must attach or reference concrete verification evidence (test output, lint result, runtime log) for every substantive claim. A `DONE` handoff with no evidence for its claims is treated as `AMBIGUOUS` by the orchestrator and requires triage before the verification gates proceed.
5. In terms of Rule 7 (Docs Output Quality): verification evidence belongs in the **Verification evidence** layer — never in the Facts or Interpretation layers. Do not present unrun commands as established facts.

```
❌ BAD:  "All tests pass and lint is clean."  (no output, no proof)
✅ GOOD: "Tests: python3 run_all_tests.py → 137 passed, 0 failed (output attached).
          Lint: ruff check hooks/ → exit 0.
          CI: not yet run — trigger with: gh workflow run quality-gates.yml"
```

---

## Rule 10 — Minimum Footprint

Prefer the smallest change that fully satisfies the task. Every changed line should
trace directly to the requested outcome or to verification needed for that outcome.
In short: no unjustified new file, no speculative abstraction, reuse existing pattern, and changed-line traceability.

**Rules:**

1. Do not create a new file without a clear justification that an existing file is
   not the right home.
2. Do not add speculative abstractions, configuration, extension points, or
   general-purpose helpers for a single current use case.
3. Reuse an existing pattern, helper, command, or test harness before introducing
   a new one.
4. If a changed or newly added function grows beyond 50 lines, decompose it or
   explain in the PR/issue why keeping it together is safer.
5. If a changed file grows beyond 400 lines, flag it in the PR/issue with the
   reason it remains acceptable or the follow-up needed to split it.
6. Keep diffs surgical: avoid formatting churn, opportunistic cleanup, or
   adjacent refactors that are not required by the task.

```
❌ BAD:  Add a new "utils" module because it might be useful later.
✅ GOOD: Reuse the nearby helper; if a new file is unavoidable, document its single responsibility and tests.
```

---

## Rule 11 — New File Justification

New files are durable maintenance surface. Before adding one, prove that it has a
clear home, responsibility, and verification path.

**Rules:**

1. Search for an existing home first (`glob`, `rg`, LSP, or the relevant project
   registry) and reuse it when it can own the behavior cleanly.
2. State the new file's responsibility in the issue, PR, step file, or handoff.
   The responsibility must be narrow enough that future contributors know what
   belongs there and what does not.
3. Wire the file into the relevant lint, test, hook, docs, packaging, or CI
   surface. A file that is invisible to quality gates needs explicit justification.
4. Add or update tests for the behavior the new file owns, or document the exact
   verification command when tests are not applicable.
5. Avoid duplicate entry points. If the new file overlaps with an existing script,
   hook, route, skill, or module, consolidate or explain why separation is required.

```
❌ BAD:  Create scripts/new_checker.py without checking scripts/check_*.py or adding tests.
✅ GOOD: Confirm no existing checker fits, define the checker's responsibility, add tests, and document its command.
```

---

## Orchestrator Goal-Loop

When acting as an orchestrator with an active goal, the lifecycle is iterative, not linear. After all tentacle handoffs are collected and verification gates pass, the orchestrator evaluates the goal before closing:

1. **State success criteria upfront** — before dispatching any tentacle, write the goal's success criteria explicitly in `CONTEXT.md` or a shared artifact. Weak criteria ("make it work") prevent clean goal evaluation; strong criteria ("all 186 tests pass, benchmark score >= 90") enable independent verification. Add them to the goal with:
   ```bash
   sk tentacle goal criteria add --desc "All 186 tests pass" --id sc-1 --verify-cmd "python3 run_all_tests.py"
   # fallback: python3 ~/.copilot/tools/tentacle.py goal criteria add ...
   ```
2. **Evaluate after each Verify phase** — once Build -> Lint -> Test -> Review gates pass, evaluate whether the overarching goal is met. Check criteria and record evidence:
   ```bash
   sk tentacle goal criteria check          # run verify commands, update pass/fail
   sk tentacle goal gate pass G1 --reason "test output: 186/186"
   sk tentacle goal eval --decision continue   # or: complete | pause | abandon
   # fallback: python3 ~/.copilot/tools/tentacle.py goal eval ...
   ```
   For automated retries with stall detection, use `goal verify-loop` instead of (or after) `goal criteria check`. It re-runs verification commands up to `--max-retries` times and detects stalls (identical repeated failures). On `--escalate`, it marks the goal `needs-human` and prints advisory recovery steps — fix the underlying issues, then run `goal resume` to re-activate the goal before retrying. If blocked or ambiguous tentacles need to be cleared at the same time, pass `--reset-failed` (resets every BLOCKED/AMBIGUOUS tentacle to idle) or `--from-iteration N` (rewinds the iteration counter to N and resets all tentacles assigned to that iteration or later). Success-criteria pass/fail state is preserved in both cases.
   ```bash
   sk tentacle goal verify-loop [--id sc-1] [--max-retries 3] [--retry-delay 10] [--timeout 60] [--escalate]
   # fallback: python3 ~/.copilot/tools/tentacle.py goal verify-loop ...
   ```
   Also record iteration verification evidence:
   ```bash
   sk tentacle verify <name> "<check-command>" --label "goal-eval"
   # fallback: python3 ~/.copilot/tools/tentacle.py verify <name> "<check-command>" --label "goal-eval"
   ```
   **Human gate blocking** — `goal eval continue` and `goal eval complete` are hard-blocked
   when any gate is in `pending` or `rejected` state. The goal status becomes `awaiting-gate`
   and the blocking gate ID and reason are printed. To unblock: approve the gate with
   `goal gate approve <id> [--reason ...]`, then re-run `goal eval`. If the goal was left in
   `awaiting-gate` state after all gates are resolved, run `goal resume` to re-activate it.
   Use `goal gate reject <id> --reason ...` to signal that a gate failed human review; this
   also sets the goal to `awaiting-gate`. See **[docs/USAGE.md — Gates](USAGE.md#gates)** for
   the full `add / approve / reject` command reference.
3. **Loop if unmet** — if the goal is not satisfied, return to Phase 1 (Plan). Create new tentacles scoped to the remaining gap. Do not re-open completed tentacles; create new ones.
4. **Close only when verified** — proceed to commit and close only when goal success criteria are verifiably met and evidence is recorded.
5. **Sub-agents do not loop** — sub-agents report via handoff and stop. The orchestrator reads handoff statuses, evaluates the goal, and decides whether to loop or close. Never dispatch sub-agents with an implicit expectation that they will self-continue.

**Goal loop entry point:** `sk tentacle goal` (or `python3 ~/.copilot/tools/tentacle.py goal`). The Rust `sk` binary passes `tentacle goal` arguments to `tentacle.py` as a transparent pass-through — no Rust change is required when new `goal` subcommands are added. State is stored in `.octogent/goal.json`.

**Typical pattern:**
```
goal init -> goal dispatch -> wait for handoffs -> Verify gates -> goal criteria check -> goal eval
                                                       | not met
                                        Plan new tentacles for remaining gaps
                                                       | met
                                        goal eval --decision complete -> Commit + Close
```

This is the **loop-until-verified** semantic applied at the orchestrator level. At the task level, Karpathy Guideline 4 applies the same principle: define success criteria, loop until verified.

**Paused-goal recovery** — when the session-end hook detects an active or awaiting-gate goal, it writes a pause breadcrumb to `.octogent/goal-resume-breadcrumb.json`. Both the Python (`hook_runner.py`) and native Rust (`sk hooks run sessionStart`) paths prepend a resume banner before the next session's briefing output (the banner shows the stored pause-reason label; currently only session end writes the breadcrumb — `context compaction` and `quota limit` are recognized future-compatible labels, not yet active breadcrumb writers):

```
⏸  Paused goal: <goal title>  (session end | context compaction | quota limit)
▶  Run: sk tentacle goal resume
```

Recovery sequence:
1. `sk tentacle goal resume` — re-activates the goal (paused → active)
2. `sk tentacle goal resilience-status` — compact health view with per-tentacle state
3. Re-dispatch tentacle waves for remaining work, or re-run `sk tentacle goal verify-loop [--escalate]`

> 📖 **Detailed recovery flows** (compaction, interruption, awaiting-gate, quota/rate-limit):
> **[docs/RESILIENCE-RUNBOOK.md](RESILIENCE-RUNBOOK.md)**

---

## Quality Checklist

> Canonical preflight/edit/verification/closeout checklist for every agent. Mirror a concise version in `AGENTS.md` and `.github/copilot-instructions.md`. Source of truth: this section.

### Preflight (before every non-trivial task)

1. `sk briefing --auto --compact` — surface past mistakes, patterns, and decisions before touching code/config/architecture.
2. Read target files with `grep`/`glob`/`view`/LSP before any edit; do not modify without reading first.
3. State which dirty surfaces apply: Python, Rust, browse-ui, remote-terminal, docs/hooks/skills/release.
4. For tasks spanning multiple modules or high-risk changes, dispatch a specialized reviewer or opus-class agent.

### Edit rules

1. Minimal footprint — no speculative abstractions, every changed line traces to the task.
2. No SQL string interpolation — use `?` placeholders only.
3. No pickle — use JSON or `struct.pack`.
4. New Python scripts include `if os.name == "nt": sys.stdout.reconfigure(encoding="utf-8")`.
5. New files require existing-home search, responsibility statement, and wiring to tests/docs/CI (Rule 11).
6. Functions over 50 lines or files over 400 lines require decomposition or explicit justification.

### Verification by surface

| Surface changed | Required evidence |
|-----------------|-------------------|
| Python | AST-parse modified files; `python3 test_security.py AND python3 test_fixes.py` (both required; `run_all_tests.py` covers both) |
| Hooks / rules / skills / docs | `python3 tests/test_quality_gates.py`; hook-specific tests; `python3 hooks/lint-skills.py --all` if skill/agent files changed |
| Platform / install / update | `python3 tests/test_platform_compat.py`; relevant install/update tests |
| browse-ui | `pnpm typecheck`; `pnpm lint`; `pnpm format:check`; `pnpm test`; `pnpm build`; E2E when runtime/operator behavior changes |
| Rust | `cargo fmt --all -- --check`; `cargo clippy -- -D warnings`; `cargo test` |
| remote-terminal | `npm test`; `npm run lint`; `npm run lint:clean`; `npm run audit:high` |
| Release / update | Checksum/provenance verification; migration/update evidence |

### Closeout

1. Do not claim tests/lint/build/CI pass without attaching command output or a CI URL.
2. Say "not proven yet — run `<command>`" for anything not executed in this session.
3. Run `sk learn --mistake|--pattern|--decision|--discovery` before `task_complete` for meaningful work.
4. Subagents hand off via `tentacle.py handoff --status DONE --changed-file <file> --learn`; no `git commit`/`git push`.

---

## Per-Rule Enforcement Matrix

| Rule | Policy | Advisory hook | Blocking hook | pre-commit | CI |
|------|--------|--------------|---------------|------------|-----|
| 1 — Investigate before acting | ✅ | `read-before-edit` (warn) | — | — | — |
| 2 — Briefing before complex tasks | ✅ | — | `enforce-briefing` (blocks edit/create/bash) | — | — |
| 3 — Test after every change | ✅ | `test-reminder` (warns after 3 edits) | `verification-gate` (blocks closeout until `py_security` + `py_fixes` evidence recorded) | syntax + Ruff (staged files) | `quality-gates` job |
| 4 — Verify before committing | ✅ | — | `syntax-gate` (blocks py syntax errors) | `check_syntax.py` on all staged `.py` | — |
| 5 — Sub-agent model selection | ✅ | — | — | — | — |
| 6 — No guessing | ✅ | — | — | — | — |
| 7 — Docs output quality | ✅ | — | `verification-gate` (partial: blocks closeout after dirty surfaces) | — | — |
| 8 — Tentacle execution obligations | ✅ | `tentacle-suggest` (postToolUse) | `tentacle-enforce` + `subagent-git-guard` + `pre-push` | `pre-commit` subagent guard | — |
| 9 — Claims require evidence | ✅ | — | `verification-gate` (blocks task_complete / DONE / gh close) | — | — |
| 10 — Minimum footprint | ✅ | `file-size-advisory` (warns >400 lines) | — | `check_complexity.py` (advisory) | — |
| 11 — New file justification | ✅ | `new-file-advisory` (warns on new root `.py`) | — | — | — |

> All hooks **fail-open**: a hook crash or unavailability never blocks the agent. Hook failures are logged; work proceeds.
> For the full hook description, see **[docs/HOOKS.md](HOOKS.md)**.

---

## Hook Enforcement

These rules are partially enforced at the tool level. All hooks **fail-open**: if a hook itself crashes or is unavailable, the guarded operation proceeds rather than blocking the agent. Hook failures are logged but do not interrupt work.

| Rule | Hook | Enforcement |
|------|------|-------------|
| Briefing before edits | `enforce-briefing` (preToolUse) | Blocks `edit`/`create`/`bash` until briefing marker is present |
| Learn after code edits | `enforce-learn` (preToolUse) | Blocks `git commit` / `task_complete` after ≥3 code edits without `learn.py` |
| Skill follow-up after learn | `learn-reminder` (postToolUse) | After `learn.py` / `sk learn`, reminds agents to update relevant skills using skill-creator standards when the lesson changes a reusable workflow, guardrail, trigger rule, or output contract |
| Tentacle for broad changes | `tentacle-enforce` (preToolUse) | Blocks edits when ≥3 files across ≥2 modules without tentacle setup |
| No git ops in sub-agents | `subagent-git-guard` (preToolUse + git hooks) | Blocks `git commit`/`git push` while dispatched-subagent marker is active |
| Syntax errors | `syntax-gate` (preToolUse) | Blocks `.py` edit/create payloads that fail `py_compile` |
| Tentacle todo progress | runtime injection | Orchestrator expects `tentacle.py todo done` calls as tasks complete |
| Tentacle handoff | runtime injection | Orchestrator expects `tentacle.py handoff` before agent stops |
| Evidence for closeout claims (Rule 9) | `verification-gate` (preToolUse + postToolUse) | Tracks dirty Python / browse-ui surfaces, records fresh test / format / lint / typecheck / build evidence, and blocks `task_complete`, `gh issue close/comment`, and tentacle `DONE` / `complete` actions when that evidence is missing. CI/runtime proof beyond those gates remains policy-level. |
| Minimum footprint (Rule 10) | `file-size-advisory` (preToolUse) | Warns on large Python create/edit payloads so agents can decompose or justify oversized changes before they land. |
| New file justification (Rule 11) | `new-file-advisory` (preToolUse) | Warns on new root-level Python files and points agents back to the search/reuse/test-surface checklist. |

> Full hook rule inventory: **[docs/HOOKS.md](HOOKS.md)**

---

## Copilot CLI Enforcement Surface

The file `.github/copilot-instructions.md` injects these rules into every Copilot CLI session context. That file is the **runtime enforcement surface** — keep it in sync with this document. Changes to agent rules should be reflected in both places.

For Claude Code, equivalent guidance lives in `CLAUDE.md` (project root or user home) and `.claude/` instruction files.
