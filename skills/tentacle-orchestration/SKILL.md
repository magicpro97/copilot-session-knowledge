---
name: tentacle-orchestration
description: Break complex tasks into scoped parallel work units for multi-agent execution. Use when a task spans multiple modules or layers (API + DB + UI). Each tentacle runs strict-tdd-workflow internally. Not for simple single-module tasks — use strict-tdd-workflow directly instead. Trigger words — "orchestrate", "multi-agent", "parallel agents", "tentacle", "swarm", or any task touching 3+ files across different modules.
---

# Tentacle Orchestration

Break a complex task into scoped work units ("tentacles"), enrich each with context, then dispatch agents in parallel. Results persist in files so nothing is lost between agent boundaries.

Adapted from the [OctoGent](https://github.com/hesamsheikh/octogent) tentacle pattern.

> **Relationship with strict-tdd-workflow**: Tentacle is the **orchestrator** (splits work), strict-tdd is the **executor** (runs inside each tentacle). For single-module tasks, skip tentacle and use strict-tdd directly.

## When to use

| Scope | Approach |
|-------|----------|
| 1-2 files, single concern | Direct work — no tentacle needed |
| 3+ files, single module | Optional — tentacle helps track but not required |
| 3+ files, multiple modules | **Tentacle required** — decompose into scoped units |
| Multi-phase with agent delegation | **Tentacle required** — each delegated agent gets a tentacle |
| Bug investigation, multiple hypotheses | Tentacle recommended — one tentacle per hypothesis |

**Not a good fit:** strictly sequential single-file tasks, limited token budget, trivial edits.

## Sub-agent Guardrails

These guardrails apply to dispatched sub-agents. The **commit restriction is enforced at the
git level** when hooks are installed; all other items are conventions reinforced by prompt
context.

**Why git hooks, not preToolUse alone:** When the orchestrator dispatches a sub-agent via the
`task()` tool, the platform does not guarantee that `preToolUse` hooks from the parent
`hooks.json` propagate into the sub-agent's context window. Git hooks (`pre-commit`,
`pre-push`) are filesystem-level and fire for any git process regardless of which agent spawned
it — they are the reliable enforcement surface.

**How enforcement works:**
1. `tentacle.py create` generates a UUID `tentacle_id` stored in the tentacle's `meta.json`.
   If the requested name directory already exists, `create` auto-resolves the collision by
   creating `<name>-<uuid[:8]>` — the slug is printed and must be used for all subsequent
   commands. `tentacle.py swarm` reads `tentacle_id` from `meta.json` and writes an HMAC-signed
   marker file at `~/.copilot/markers/dispatched-subagent-active` containing `active_tentacles`
   entries of the form `{"name": ..., "ts": ..., "git_root": ..., "tentacle_id": ...}`.
   **Primary deduplication key: `tentacle_id`** (when present) — two instances with the same
   logical name in the same repo each get a separate entry. Fallback for legacy entries without
   `tentacle_id`: `(name, git_root)`.
2. `hooks/pre-commit` and `hooks/pre-push` call `hooks/check_subagent_marker.py`, which blocks
   the git operation when the marker is present, auth-valid, within its 4-hour TTL, **and the
   entry's `git_root` matches the repo running the git command**. A marker from a different repo
   does not block commits there — this prevents cross-repo false positives when tentacles are
   active in other repos concurrently. Entries without `git_root` (old format) conservatively
   block all repos.
   > **Upgrade migration:** Cross-repo isolation is not retroactive. In-flight old-format marker
   > entries (no `git_root`) continue to block all repos until completed, cleared, or expired (4h
   > TTL). To get isolation immediately: `tentacle.py complete <name>` then re-dispatch.
3. `hooks/rules/subagent_guard.py` provides a secondary `preToolUse` intercept for the
   orchestrator session (defense-in-depth only — not the primary path).
4. `tentacle.py complete <name>` reads `tentacle_id` from `meta.json` and removes only the
   matching marker entry; the marker is deleted when `active_tentacles` becomes empty.

**Install the git hooks** (once per repository):

```bash
python3 ~/.copilot/tools/install.py --install-git-hooks
```

**Enforcement scope and known limitations:**

- **Local-only.** Cloud-delegated or remote agent runs are not covered.
- **`preToolUse` non-inheritance.** The `preToolUse` guard in the main session is
  defense-in-depth — it does not replace git hooks. Whether `preToolUse` propagates into
  `task()`-spawned subagents is undefined by the platform.
- **Same-repo multi-session supported (phase 5) — with working-tree caveat.** `tentacle_id`
  isolation at the marker/runtime layer means two instances with the same logical name in the
  same repo each hold a separate entry and `complete` only clears the matching one. However,
  the working tree and git index are shared — concurrent tentacles with overlapping file scopes
  will produce conflicts. Keep scopes non-overlapping.
- **Collision-resolved slug names:** When `create` auto-resolves a directory collision, the
  printed `<name>-<uuid[:8]>` slug must be used for all subsequent commands.
- **After tool updates,** `auto-update-tools.py` does NOT auto-reinstall git hooks. Re-run
  `install.py --install-git-hooks` in each protected repo after relevant updates.

| Convention | What to do |
|------------|-----------|
| **Commit restriction** | Do not run `git commit` or `git push`. When git hooks are installed, both are blocked at the filesystem level while the `dispatched-subagent-active` marker is fresh. Even without hooks, committing from a subagent mid-run risks corrupting the orchestrator's merge flow. |
| **Stay in scope** | Do not edit files outside your tentacle's declared `scope`. If you discover that more files are needed, escalate — do not expand unilaterally. |
| **Escalate, don't expand** | If your scope is insufficient to complete the task, write the gap to `handoff.md` (e.g. "blocked: need changes in `src/db/` which is outside my scope") and stop. The orchestrator decides whether to create a new tentacle or adjust scope. |
| **No over-implementation** | Implement only what your todos specify. Do not add features, refactors, or improvements that are not in your todo list — even if they seem obvious. |
| **Handoff before stopping** | Always write a structured handoff before marking your work done — even if the session ends early. Use `tentacle.py handoff <name> "<prose summary>" --status <STATUS> --changed-file <path> --learn`. Required fields: a prose summary and `--status` (one of `DONE`, `BLOCKED`, `TOO_BIG`, `AMBIGUOUS`, `REGRESSED`). Add one `--changed-file` per modified file; omit it when no files changed (common for `BLOCKED`, `TOO_BIG`, or `AMBIGUOUS`). Old form `handoff <name> "<message>" --learn` still works when no structured status is needed. The orchestrator reads `STATUS:` and `Changed:` receipts to decide next steps and triage. |

## Anti-patterns

- ❌ SQL/markdown todos only for multi-agent work → agents lose scope isolation and CONTEXT.md
- ❌ Launching sub-agents without `swarm` prompt → agent gets no scope, constraints, or key files
- ❌ Skipping `--briefing` on create → past mistakes not injected into CONTEXT.md
- ❌ Skipping `complete` before `delete` → learnings from handoff.md lost permanently
- ❌ Overlapping tentacle scopes → agents overwrite each other's work
- ❌ Skipping the runtime bundle on multi-agent work → agents lose file-backed context and `recall-pack.json`
- ❌ Using `--briefing --output json --no-bundle` → briefing cannot be represented without the bundle
- ❌ Sub-agent commits or pushes → blocked by git hooks when installed (and risky regardless: corrupts orchestrator's merge/verify flow)
- ❌ Sub-agent edits files outside declared scope → silent conflicts with other parallel agents
- ❌ Sub-agent silently expands scope instead of escalating → orchestrator loses visibility
- ❌ Skipping `install.py --install-git-hooks` → git-level commit/push guard is inactive; enforcement falls back to preToolUse only (not guaranteed in subagent contexts)

## Core concept

A **tentacle** is a scoped work unit stored as files:

```
.octogent/tentacles/<name>/
├── CONTEXT.md    ← What the agent needs to know (scope, constraints, key files)
├── todo.md       ← Checkbox items — each is a delegation unit
├── handoff.md    ← Agent writes results here when done
├── meta.json     ← Metadata (scope, status, timestamps)
└── bundle/       ← Runtime context artifacts (created by dispatch by default)
    ├── manifest.json
    ├── session-metadata.md
    ├── recall-pack.json
    ├── briefing.md
    ├── instructions.md
    └── skills.md
```

The octopus metaphor: one orchestrator (you), multiple tentacles (agents), each handling a distinct code region.

<example>
**Task:** Add dark mode support to a Next.js app

**Decomposition:**
- `theme-tokens` tentacle — scope: `src/styles/tokens.css`, `tailwind.config.ts` — create CSS variables for dark/light palettes
- `component-update` tentacle — scope: `src/components/**/*` — apply `dark:` Tailwind classes to all components
- `test-suite` tentacle — scope: `tests/**/*` — write Playwright visual regression tests for dark mode

Each tentacle is independent, non-overlapping, and completable in isolation. The orchestrator merges results after all three pass verification gates.
</example>

## Internal workflow

The workflow has 5 phases: **Clarify → Plan → Execute → Verify → Close**.

Clarification is the most important phase. A bug found in spec costs 1x to fix. Found in code: 10x. Found in production: 100x. Never skip this phase — time invested here prevents entire categories of downstream waste.

### Phase 0: Clarify Spec (Steps 0.0–0.5)

This phase takes a raw specification and makes it implementation-ready through iterative Q&A. No planning or coding happens until the spec is CLEAN.

- **Step 0.0** (optional): Co-author the spec when user has no written spec — structured context gathering + iterative drafting
- **Steps 0.1–0.4**: Analyze spec against 8 quality dimensions, generate Spec Health Report, iterative refinement until CLEAN
- **Step 0.5**: Reader Testing — verify a fresh agent (no context) can correctly understand the spec

For the full process, see `references/spec-clarification.md`.

**Gate**: Planning on an unclear spec produces incorrect decomposition, wasted agent work, and rework. Never proceed to Phase 1 until the spec is CLEAN and reader-tested.

### Phase 1: Plan (Steps 1–4)

Use the CLEAN spec and its Impact Analysis / Risk Assessment to inform decomposition.

#### Step 1: Decompose the task into modules

Read the task description and identify independent code regions. Each region becomes one tentacle.

#### Step 2: Create tentacles

```bash
python3 ~/.copilot/tools/tentacle.py create <module-name> \
  --scope "<file-patterns>" \
  --desc "<short description>" \
  --briefing
```

The `--briefing` flag injects past mistakes and patterns from session-knowledge into CONTEXT.md — use it every time.

#### Step 3: Add todos

```bash
python3 ~/.copilot/tools/tentacle.py todo <name> add "<specific, atomic task>"
```

Each todo should be one deliverable — testable, reviewable, and completable in isolation.

#### Step 4: Enrich CONTEXT.md

Read reference files with `view`, then edit CONTEXT.md to add:
- **What exists**: describe the current code in the scope area
- **Key files**: full paths to reference files the agent needs
- **Constraints**: rules specific to this code region

This is the most important step. Agent quality is directly proportional to CONTEXT.md quality.

### Phase 2: Execute (Steps 5–6)

#### Step 5: Dispatch agents (swarm)

```bash
python3 ~/.copilot/tools/tentacle.py swarm <name> --agent-type <type> --model <model> --briefing
python3 ~/.copilot/tools/tentacle.py swarm <name> --output parallel --briefing
python3 ~/.copilot/tools/tentacle.py dispatch <name> --agent-type <type> --model <model> --briefing
```

`swarm` and `dispatch` materialize a runtime bundle by default. The dispatch prompt stays
token-lean and points agents at `.octogent/tentacles/<name>/bundle/manifest.json` first.
The bundle carries the full `CONTEXT.md`, todos, latest checkpoint, instruction snippets,
skills catalogue, and `recall-pack.json`.

`--briefing` fetches live past knowledge from session-knowledge at dispatch time. With the
default bundle, briefing is stored in `briefing.md` and machine-readable recall is stored in
`recall-pack.json` by trying `briefing.py --task <id> --json` first and falling back to
`briefing.py "<query>" --pack --limit 3`. Use `--no-bundle` only for tiny/manual prompts; if
you combine `--output json --briefing`, keep the default bundle enabled so JSON output can
surface `bundle_path`.

Use the output as the prompt for `task()`. Launch independent tentacles in parallel.

#### Step 6: Monitor progress

```bash
python3 ~/.copilot/tools/tentacle.py status
python3 ~/.copilot/tools/tentacle.py show <name>
```

### Phase 3: Verify (Steps 7–12)

Every step here catches a different class of agent error. For detailed gate descriptions (build, lint, test, review, docs, QA audit), see `references/verification-gates.md`.

Summary:

| Gate | What it catches | Skip when |
|------|----------------|-----------|
| **Build** | Syntax errors, type mismatches, import failures | Never skip |
| **Lint** | Style violations, unused imports, formatting | Never skip |
| **Test** | Logic bugs, regressions, broken contracts | Never skip |
| **Review** | Security issues, design flaws, scope creep | Never skip |
| **Docs** | Stale README, outdated JSDoc, missing CHANGELOG | Internal refactors only |
| **QA audit** | Hallucinated tests, spec mismatches, blind spots | Low-risk changes only |

The first 4 gates are mandatory. Skipping any of them means you don't know if the agent output is correct.

### Phase 4: Commit + Close (Steps 13–17)

#### Step 13: Commit after each completed phase (orchestrator only)

Commit working code after completing each major phase — not just at the end.
If a later phase fails or the session crashes, earlier work is preserved and rollback is possible.

```bash
git add -A && git commit -m "feat(<scope>): <phase description>"
```

**Commit cadence:**
- After Phase 1 shared/foundation tentacles complete + build passes → commit
- After each Phase 2 parallel batch completes + build passes → commit
- After Phase 3 verification passes → commit
- Final integration wiring → commit

**Commit restriction:** Sub-agents must not run `git commit` or `git push`. When git hooks
are installed (`install.py --install-git-hooks`), both operations are blocked at the git level
while the `dispatched-subagent-active` marker is fresh. Even without hooks, this is a hard
convention: the orchestrator commits after merging and verifying all tentacle results.
This enforcement is **local-only** — cloud-delegated or remote agent runs are not covered.

#### Step 14: Runtime verification

Build passing ≠ app works. After all tentacles are merged, run the app:

```bash
# Desktop: ./gradlew :composeApp:jvmRun
# Mobile: deploy to emulator/simulator
# Web: npm run dev / python manage.py runserver
```

DI frameworks (Koin, Dagger, Spring) crash at runtime if bindings are missing — the compiler
won't catch this. A 30-second launch test catches what build+test cannot.

#### Step 15: Complete and learn

```bash
python3 ~/.copilot/tools/tentacle.py complete <name>
```

Only call `complete` after all verification gates pass. This marks all todos done and auto-extracts learnings from handoff.md into long-term knowledge.

#### Step 16: Resume a tentacle (when picking up interrupted work)

```bash
python3 ~/.copilot/tools/tentacle.py resume <name>             # Refresh briefing, mark active
python3 ~/.copilot/tools/tentacle.py resume <name> --no-briefing  # Skip briefing injection
```

`resume` refreshes the live briefing in CONTEXT.md and marks the tentacle active again. Use it when returning to a tentacle after an interruption or session boundary. Pass `--no-briefing` only when the briefing is already fresh and re-fetching would be wasteful.

#### Step 17: Cleanup

```bash
python3 ~/.copilot/tools/tentacle.py delete <name>
```

## CLI reference

See `references/cli-reference.md` for the full command reference, CONTEXT.md template, and agent selection guidance.

Quick reference:

```bash
tentacle.py create <name> --scope "<paths>" --desc "<desc>" --briefing
tentacle.py todo <name> add "<task>"
tentacle.py swarm <name> --agent-type <type> --model <model> --briefing    # bundle-first default
tentacle.py swarm <name> --output parallel --briefing                      # one worker per todo
tentacle.py swarm <name> --output json --briefing                          # JSON + bundle_path
tentacle.py dispatch <name> --agent-type <type> --briefing                 # single-agent dispatch
tentacle.py swarm <name> --no-bundle                                       # rare opt-out for tiny prompts
tentacle.py handoff <name> "<summary>" --status DONE --changed-file <path> --learn
tentacle.py resume <name>                  # resume interrupted tentacle (refreshes briefing)
tentacle.py resume <name> --no-briefing    # resume without re-fetching briefing
tentacle.py status
tentacle.py complete <name>
tentacle.py delete <name>
```

## Tips

1. **Invest in CONTEXT.md** — 2-3 minutes writing good context saves 10 minutes of agent confusion
2. **Keep todos atomic** — each item = one testable deliverable
3. **No scope overlap** — overlapping scopes cause agents to overwrite each other
4. **Complete before delete** — `complete` saves learnings; `delete` alone loses them
5. **Commit after each phase** — uncommitted code is lost if the session crashes or compacts
6. **Run the app** — build+test ≠ works. Launch the app to verify DI resolution and runtime behavior
7. **⚠️ Commit restriction** — Sub-agents must not run `git commit`/`git push`. When git hooks are installed (`install.py --install-git-hooks`), both are blocked at the filesystem level for the repo where the tentacle was dispatched, while the `dispatched-subagent-active` marker is fresh. Commits in other repos are not affected. Even without hooks, a sub-agent commit mid-run corrupts the orchestrator's merge flow. Enforcement is local-only; cloud-delegated runs are not covered.
