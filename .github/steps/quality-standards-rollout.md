# STEPS: Quality standards rollout

**Task:** Implement the actionable conclusions from the quality standards research in an isolated worktree, with all implementation delegated to tentacle agents.
**Scope:** P0 gate repairs, P1 canonical agent checklist + drift lock, P2 CI/platform/supply-chain hardening, verification/review/PR loop.
**Constraints:** Orchestrator coordinates only. No direct implementation edits by orchestrator. Confidence `< 1.0` and review work goes to opus-class agents. Sub-agents must not commit or push.
**Estimated phases:** CLARIFY -> RESEARCH -> BUILD -> TEST -> REVIEW -> LOOP-EVAL -> COMMIT/PR/MERGE

---

## Step 1: CLARIFY — Confirm routing and scope

**Goal:** Establish a reviewed, auditable decomposition before any implementation.

**Actions:**
1. `sk briefing --auto --compact` — load prior mistakes and project decisions.
2. Dispatch opus-class scope validator to classify in-PR work, out-of-band settings work, and confidence gaps.
3. Record accepted/edited/rejected steps in tentacle CONTEXT.md files.

**Done when:** Confidence gaps are explicit and no implementation tentacle is dispatched while confidence is `< 1.0`.
**Confidence:** `1.0` required; currently blocked by RESEARCH Step 2.

---

## Step 2: RESEARCH — Resolve confidence blockers

**Goal:** Resolve unknown e2e-smoke root cause and verification-gate evidence contract before code changes.

**Actions:**
1. Dispatch `quality-e2e-research` (read-only) to inspect latest CI e2e-smoke logs and local e2e sources.
2. Dispatch `quality-vgate-spec` (read-only, opus) to specify exact evidence keys, compatibility behavior, tests, and affected callers for verification-gate split.

**Done when:** Both research handoffs contain evidence, rejected alternatives, and confidence `1.0` implementation guidance.
**Confidence:** `1.0` required before BUILD.

---

## Step 3: BUILD — P0 gate repairs

**Goal:** Restore failing quality gates before tightening standards.

**Actions:**
1. Dispatch `quality-p0-gates` to fix real Ruff F541 locations, Rust Clippy failures, missing `skill-improvement-advisor` hook docs, and e2e-smoke fix from Step 2.
2. Agent must record exact changed files and command evidence in handoff.

**Done when:** `ruff check . --select F541`, `python tests/test_quality_gates.py`, `cd sk-rust && cargo clippy -- -D warnings`, and e2e evidence are green or explicitly blocked with evidence.
**Confidence:** `1.0` required from Step 2.

---

## Step 4: BUILD — P1 canonical checklist and drift lock

**Goal:** Make the quality checklist known and enforced across agent instruction surfaces.

**Actions:**
1. Dispatch `quality-checklist-docs` to add canonical Quality Checklist to `docs/AGENT-RULES.md` and mirror concise updates in `AGENTS.md`, `.github/copilot-instructions.md`, and PR template as needed.
2. Dispatch `quality-drift-ci` after checklist docs to enhance `audit-instructions.py`, tests, and CI wiring.

**Done when:** `python audit-instructions.py --json` succeeds on clean files and tests prove drift detection.
**Confidence:** `1.0` after Step 2 and P0 baseline.

---

## Step 5: BUILD — P2 hardening

**Goal:** Implement low-risk platform and supply-chain hardening.

**Actions:**
1. Dispatch `quality-supply-chain` for Dependabot and browse-ui audit wiring.
2. Dispatch `quality-platform-eol` for Python version matrix and `.gitattributes` EOL coverage.
3. Dispatch `quality-vgate-split` using the Step 2 spec for Python verification evidence keys.

**Done when:** Changed files have matching tests/CI evidence and no overlapping scope conflicts remain.
**Confidence:** `1.0` required.

---

## Step 6: TEST — Orchestrator verification gates

**Goal:** Hold independent evidence instead of trusting sub-agent claims.

**Actions:**
1. Run Python gates: `python test_security.py`, `python test_fixes.py`, `python tests/test_quality_gates.py`, and targeted tests from handoffs.
2. Run lint/format gates for changed Python surfaces.
3. Run Rust gates if `sk-rust/**` changed.
4. Run browse-ui gates if `browse-ui/**` changed.
5. Record evidence with `sk tentacle verify`.

**Done when:** Mandatory gates pass for the exact branch head.
**Confidence:** `1.0` required.

---

## Step 7: REVIEW — Opus review and fix loop

**Goal:** Catch correctness, security, scope, and drift risks before PR and merge.

**Actions:**
1. Dispatch opus-class `code-review` over the full diff.
2. Dispatch `whole-app-impact-auditor` or equivalent opus review for docs/hooks/skills/CI synchronization.
3. For every finding, create a new fix tentacle; do not let the orchestrator edit directly.
4. Repeat until review verdict is CLEAN.

**Done when:** Review verdict is CLEAN and all substantive findings are resolved on current head.
**Confidence:** `1.0` required.

---

## Step 8: LOOP-EVAL — Goal criteria

**Goal:** Decide whether the overarching goal is met or another tentacle wave is needed.

**Actions:**
1. Record goal-eval evidence with `sk tentacle verify <name> "<success command>" --label goal-eval`.
2. Check todos, handoff statuses, CI status, and review status.
3. If gaps remain, create new tentacles and loop to BUILD/TEST/REVIEW.

**Done when:** All accepted in-PR conclusions are implemented; out-of-band settings tasks are either configured or documented with evidence.
**Confidence:** `1.0` required.

---

## Step 9: COMMIT/PR/MERGE — Ship

**Goal:** Create PR as `magicpro97`, wait for CI/review, fix all failures/comments through agents, request re-review, then merge.

**Actions:**
1. Orchestrator commits verified changes and pushes `feat/quality-standards-rollout`.
2. Create PR with evidence and manual settings follow-up if needed.
3. Monitor CI and review threads for current head.
4. Dispatch fix tentacles for CI/review failures, push, request re-review, and loop until clean.
5. Merge the PR only when CI and review are clean.

**Done when:** PR is merged and target branch contains the merged commit.
**Confidence:** `1.0` required.

---

## Phase Gates

| Phase | Artifact | Status |
|-------|---------|--------|
| CLARIFY | Opus scope validation handoff | ☑ |
| RESEARCH | `quality-e2e-research` and `quality-vgate-spec` handoffs | ☐ |
| BUILD P0 | P0 gate fixes with command evidence | ☐ |
| BUILD P1 | Checklist + drift CI with command evidence | ☐ |
| BUILD P2 | Supply-chain/platform/vgate hardening evidence | ☐ |
| TEST | Orchestrator-run verification ledger | ☐ |
| REVIEW | Opus CLEAN review verdict | ☐ |
| LOOP-EVAL | Goal criteria met on current head | ☐ |
| COMMIT/PR/MERGE | PR merged | ☐ |
