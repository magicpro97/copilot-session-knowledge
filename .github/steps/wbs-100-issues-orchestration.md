# STEPS: WBS 100 issues orchestration

**Task:** Burn down the 100 WBS issues created from the memory-tools research report.
**Scope:** Orchestration artifacts, tentacle bundles, GitHub issues #326-#425, and the non-overlapping implementation scopes declared per tentacle.
**Estimated phases:** CLARIFY -> PLAN -> EXECUTE -> VERIFY -> LOOP-EVAL -> COMMIT/CLOSE

## Orchestrator constraints

- The orchestrator must not implement code changes directly. Only create planning/context artifacts, dispatch agents, run verification gates, review results, commit verified batches, and close issues with evidence.
- Code implementation agents must use `claude-sonnet-4.6` or stronger.
- Independent review/security review must use `claude-opus-4.6` for high-risk/security issues and at least `claude-sonnet-4.6` for normal code review.
- Do not trust agent claims. The orchestrator must run build/lint/test/review gates and record evidence before completing a tentacle or closing an issue.
- Subagents must not commit or push. Git hooks have been installed so active tentacle markers block local commit/push.

## Step-plan review

- Source step file: `.github/steps/wbs-100-issues-orchestration.md`
- Accepted steps: CLARIFY, PLAN, EXECUTE, VERIFY, LOOP-EVAL, COMMIT/CLOSE.
- Edited steps: EXECUTE is split into dependency waves and non-overlapping tentacles; VERIFY requires local evidence and Opus review for security/high-risk changes.
- Rejected steps: "dispatch all 100 at once" is rejected because scopes overlap heavily across installer/update/sync/retrieval/hook/db/CI surfaces.
- Dependency order: W1 unblock/security -> W2 verification/doctor/CI -> W3 Rust/native performance -> W4 memory intelligence -> W5 future architecture.
- Evidence contract: each tentacle handoff must name changed files, issue numbers, exact test/build/lint commands, and review status. The orchestrator records gate output with `sk tentacle verify`.

## Step 1: CLARIFY — Establish backlog and success criteria

**Goal:** Confirm the exact backlog and define completion before dispatching implementation agents.

**Actions:**
1. Fetch all WBS issues: GitHub issues #326-#425 with label `wbs`.
2. Confirm Project v2 `Priority` field is set for all 100 WBS items.
3. Confirm git hooks are installed with `sk install --install-git-hooks`.
4. Use existing goal `Issue backlog burn-down via isolated tentacles` unless superseded by a newer explicit goal.

**Done when:** 100 open WBS issues are known, priorities are set, commit/push guard is active, and success criteria are explicit.

## Step 2: PLAN — Select maximum safe parallel wave

**Goal:** Select the largest set of non-overlapping issues that can run concurrently without conflicting files or runtime state.

**Actions:**
1. Group issues by file surface: installer/update, hooks/capture, Rust/native, retrieval, DB/migrations, sync/browse, CI/release, docs/DX.
2. Pick only independent surfaces for the first wave.
3. Create one tentacle per non-overlapping surface with narrow scope and issue list.
4. Add todos for RED evidence, implementation, tests, docs, and handoff.

**Done when:** Each active tentacle has a non-overlapping scope, issue list, evidence contract, and atomic todos.

## Step 3: EXECUTE — Dispatch Sonnet implementation agents

**Goal:** Run implementation in parallel while keeping scopes isolated.

**Actions:**
1. Generate a bundle-first dispatch prompt for each tentacle with `sk tentacle swarm <name> --model claude-sonnet-4.6 --briefing`.
2. Dispatch implementation agents with `task(agent_type="general-purpose", model="claude-sonnet-4.6")`.
3. Require each agent to read bundle files first, stay in scope, run strict-TDD internally, and write a structured handoff.
4. Monitor handoffs and triage `BLOCKED`, `AMBIGUOUS`, `REGRESSED`, or `TOO_BIG` immediately.

**Done when:** All active implementation agents have written structured handoffs or are triaged for re-planning.

## Step 4: VERIFY — Run local gates and independent review

**Goal:** Prove the agent outputs are correct before merge/close.

**Actions:**
1. Run build gates for changed Python/Rust/TypeScript files.
2. Run lint/format gates that already exist for the touched surfaces.
3. Run targeted tests first, then broader regression suites required by the touched surfaces.
4. Dispatch independent review agents. Use `claude-opus-4.6` for security/auth/sync/update/installer surfaces.
5. Record all gate output with `sk tentacle verify <name> "<command>" --label "<gate>"`.

**Done when:** Build, lint, test, and review gates have concrete evidence and no unresolved critical findings.

## Step 5: LOOP-EVAL — Decide whether to continue, split, or close

**Goal:** Keep iterating until all 100 WBS issues are implemented, verified, and closed or explicitly blocked.

**Actions:**
1. Query remaining open `wbs` issues after each verified wave.
2. If issues remain, create the next non-overlapping tentacle wave.
3. If a tentacle was blocked by scope overlap, split it into smaller tentacles.
4. Record goal evaluation with `sk tentacle goal eval --decision continue|complete|pause`.

**Done when:** Either all WBS issues are closed with evidence, or remaining issues are paused with explicit blocker notes.

## Step 6: COMMIT/CLOSE — Package verified batches

**Goal:** Preserve verified work and close issues with evidence.

**Actions:**
1. Commit only after a verified phase; subagents never commit.
2. Close issues only with per-criterion evidence copied or linked in the close comment.
3. Complete tentacles with `sk tentacle complete <name>` only after gates pass.
4. Delete completed tentacles after learnings are extracted.

**Done when:** Verified changes are committed, corresponding issues are closed with evidence, and tentacle metadata is complete.

## Phase Gates

| Phase | Artifact | Status |
|-------|---------|--------|
| CLARIFY | 100 WBS issues #326-#425 identified and prioritized | ☑ |
| PLAN | Non-overlapping wave/tentacle map created | ☐ |
| EXECUTE | Sonnet implementation handoffs received | ☐ |
| VERIFY | Build/lint/test/review evidence recorded | ☐ |
| LOOP-EVAL | Remaining WBS count checked and next wave decided | ☐ |
| COMMIT/CLOSE | Verified batch committed and issues closed | ☐ |
