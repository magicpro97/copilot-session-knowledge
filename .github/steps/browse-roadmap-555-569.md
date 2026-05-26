# STEPS: Implement Browse roadmap issues #555-#569

**Task:** Implement all open Browse roadmap/security issues #555 through #569 using tentacle orchestration; all implementation, review, and high-risk design decisions must be done by Opus-class agents.
**Scope:** `browse/**`, `browse-ui/src/**`, `tests/**`, `browse-ui/src/**/*.test.*`, docs directly affected by API/operator behavior.
**Estimated phases:** CLARIFY -> RESEARCH/DESIGN -> BUILD -> TEST -> REVIEW -> LOOP-EVAL -> COMMIT/CLOSE

## Step 1: CLARIFY — Confirm issue set and routing constraints

**Goal:** Establish the exact implementation backlog, project rules, agent profiles, and verification gates before any code changes.

**Actions:**
1. `gh issue list --repo magicpro97/copilot-session-knowledge --state open --limit 40 --json number,title,url --jq '.[] | select(.number>=555 and .number<=569)'` — confirm the issue set.
2. `sk briefing "implement Browse roadmap issues #555-#569 with tentacle orchestration opus agents"` — load relevant prior lessons.
3. Read `AGENTS.md`, `browse-ui/AGENTS.md`, `.github/agents/*.agent.md`, and tentacle verification references.

**Done when:** The orchestrator has the issue list, routing constraints, available specialist profiles, and verification commands in hand.
**Confidence:** `1.0` required.

## Step 2: RESEARCH/DESIGN — Resolve cross-cutting backend contracts before implementation

**Goal:** Prevent incompatible backend/UI schemas across quota, preflight, queue, telemetry, cancellation, workbench, prior-context, and resilience features.

**Actions:**
1. Dispatch an Opus design tentacle to inspect #555-#559, #563, #564, #568, #569 and produce one additive API/schema plan.
2. Dispatch an independent Opus security review of the same backend plan, including static-slot readonly and public/unauthenticated endpoints.
3. Record accepted, edited, and rejected design decisions in tentacle handoffs before any overlapping backend implementation starts.

**Done when:** API envelopes, status names, auth/read-only rules, and dependency order are documented with synthesized decision confidence `1.0`.
**Confidence:** `1.0` required; if lower, split the uncertain contract into narrower Opus research tentacles.

## Step 3: BUILD — Independent security hardening foundation (#560-#562)

**Goal:** Ship the security fixes that other roadmap issues depend on.

**Actions:**
1. Opus backend tentacle implements #560 `/healthz` liveness-only payload.
2. Opus backend tentacle implements #561 sync status path redaction.
3. Opus backend/security tentacle implements #562 static-slot readonly guard for mutating operator APIs.

**Done when:** Backend tests prove reduced public disclosure, no absolute `db_path`, and 403/readonly behavior for static-slot mutations.
**Confidence:** `1.0` required.

## Step 4: BUILD — Independent UI quick wins (#566-#567)

**Goal:** Ship low-overlap Browse UI behavior improvements while backend contract design runs.

**Actions:**
1. Opus UI tentacle implements #566 Live Tab buffer-cap indicator, clear action, and tests.
2. Opus UI tentacle implements #567 humanized `rate_limited` subagent failure guidance and tests.

**Done when:** Vitest coverage proves dropped-count accounting, clear reset, and humanized rate-limit guidance without backend changes.
**Confidence:** `1.0` required.

## Step 5: BUILD — Adopted CLI context and metadata (#555, #568)

**Goal:** Extend CLI session discovery/adoption with safe metadata and prior-context summaries.

**Actions:**
1. Opus backend tentacle adds safe optional metadata/prior_context parsing from `workspace.yaml` and bounded `events.jsonl` reads.
2. Opus UI tentacle renders metadata and prior-context pills in picker/confirmation/metadata surfaces.
3. Opus security review verifies no raw prompts, paths, tool args, tokens, or model output are exposed.

**Done when:** API/UI tests prove metadata/prior_context appears when available and degrades safely when missing/unreadable.
**Confidence:** `1.0` required.

## Step 6: BUILD — Operator control plane foundation (#563, #569)

**Goal:** Add safe post-admission control and resilience primitives before queue/workbench features depend on them.

**Actions:**
1. Opus backend tentacle implements per-run cancel API/state transitions.
2. Opus backend tentacle implements health/watchdog/orphan/drain/retry/backpressure primitives from #569.
3. Opus UI tentacle wires Chat cancel control and health/drain/backpressure indicators.
4. Opus security review verifies auth, static-slot readonly, redaction, bounded retry, and no sensitive event leakage.

**Done when:** Backend/frontend tests prove cancel, health states, drain event, retry bounds, and backpressure indicators.
**Confidence:** `1.0` required.

## Step 7: BUILD — Quota, prompt preflight, telemetry, and queue (#556-#559)

**Goal:** Add resource-aware admission and operator safety surfaces.

**Actions:**
1. Opus backend tentacle implements token/quota ledger and usage summaries (#556).
2. Opus backend/UI tentacle implements prompt preflight endpoint and UI preview (#557).
3. Opus backend/UI tentacle implements opt-in host telemetry (#558).
4. Opus backend/UI tentacle implements resource-aware queue/backpressure (#559), integrating cancel/read-only/security primitives.
5. Opus security review verifies redaction, opt-in telemetry, admission bypass prevention, and disclosure boundaries.

**Done when:** Tests prove quota/preflight/telemetry/queue states and UI affordances, including admission denial, queued/admitted transitions, and static-slot bypass prevention.
**Confidence:** `1.0` required.

## Step 8: BUILD — Cross-session workbench and tentacle status (#564-#565)

**Goal:** Surface multi-session and tentacle orchestration state in Chat without leaking sensitive data.

**Actions:**
1. Opus backend/UI tentacle implements cross-session active-runs endpoint and Workbench UI (#564).
2. Opus UI tentacle implements Chat shell tentacle orchestration status (#565) using existing `/api/tentacles/status`.
3. Opus review verifies no prompt/event/path leakage and no duplicate polling loops.

**Done when:** UI/API tests prove active-run overview, session badges, tentacle status chip, diagnostics opt-in behavior, and accessible states.
**Confidence:** `1.0` required.

## Step 9: TEST — Run all required project gates

**Goal:** Hold concrete evidence for all modified surfaces.

**Actions:**
1. Python: `python3 test_security.py && python3 test_fixes.py && python3 run_all_tests.py`.
2. Browse UI: `cd browse-ui && pnpm typecheck && pnpm lint && pnpm format:check && pnpm test && pnpm build`.
3. Hooks/docs/skills if changed: `python3 tests/test_quality_gates.py`.
4. Rust if touched: `cargo fmt --all -- --check && cargo clippy -- -D warnings && cargo test`.

**Done when:** Gate output is recorded with pass/fail counts; any baseline failures are separated from regressions.
**Confidence:** `1.0` required.

## Step 10: REVIEW — Independent Opus code/security/whole-app review

**Goal:** Catch defects that implementation agents missed before commit/close.

**Actions:**
1. Dispatch Opus `code-review` across the final diff.
2. Dispatch Opus `browser-security-reviewer` for auth/CORS/PNA/token/telemetry/disclosure changes.
3. Dispatch Opus `verification-gate` for whole-app synchronization evidence.
4. Loop fixes through responsible Opus tentacles until all substantive findings are resolved.

**Done when:** Review verdicts are CLEAN or all findings are addressed and re-reviewed.
**Confidence:** `1.0` required.

## Step 11: LOOP-EVAL — Verify all issues are actually satisfied

**Goal:** Evaluate the overarching user goal before commit or issue closeout.

**Actions:**
1. `gh issue list --repo magicpro97/copilot-session-knowledge --state open --limit 40 --json number,title --jq '.[] | select(.number>=555 and .number<=569)'` — identify remaining issue state.
2. For each issue #555-#569, map acceptance criteria to test/review evidence.
3. `sk tentacle verify <name> "<success-criteria command>" --label "goal-eval"` — record final goal evidence.
4. If any issue lacks evidence, return to BUILD with a new scoped tentacle.

**Done when:** Every issue #555-#569 has implementation evidence, verification evidence, and review evidence.
**Confidence:** `1.0` required.

## Step 12: COMMIT/CLOSE — Orchestrator-only shipping

**Goal:** Preserve verified work and close issues with evidence.

**Actions:**
1. Orchestrator only: AST-parse modified `.py` files, run final gates, `git diff --stat`.
2. Commit with required co-authored trailer.
3. Close each issue #555-#569 only with per-acceptance verification evidence.
4. `sk learn` and `sk tentacle complete/delete` completed tentacles.

**Done when:** Commit exists on the target branch, issue closeouts cite evidence, tentacles are completed, and no roadmap issue in #555-#569 remains open without a stated blocker.
**Confidence:** `1.0` required.

## Phase Gates

| Phase | Artifact | Status |
|-------|----------|--------|
| CLARIFY | Issue list + briefing + agent inventory | ☐ |
| RESEARCH/DESIGN | Opus backend/security design confidence `1.0` | ☐ |
| BUILD security | #560-#562 implemented by Opus tentacle(s) | ☐ |
| BUILD UI quick wins | #566-#567 implemented by Opus tentacle(s) | ☐ |
| BUILD metadata/context | #555/#568 implemented by Opus tentacle(s) | ☐ |
| BUILD control/resilience | #563/#569 implemented by Opus tentacle(s) | ☐ |
| BUILD quota/preflight/telemetry/queue | #556-#559 implemented by Opus tentacle(s) | ☐ |
| BUILD workbench/tentacles | #564-#565 implemented by Opus tentacle(s) | ☐ |
| TEST | Python + browse-ui gates recorded | ☐ |
| REVIEW | Opus code/security/verification review clean | ☐ |
| LOOP-EVAL | Each issue #555-#569 mapped to evidence | ☐ |
| COMMIT/CLOSE | Verified commit and issue closeouts | ☐ |
