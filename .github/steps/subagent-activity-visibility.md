# STEPS: Sub-agent activity visibility

**Task:** Make sub-agent work visible in Flight Recorder / Debug Log so a user can understand which sub-agents ran, when they started/completed/failed, how long they ran, and what operational evidence exists.
**Scope:** `browse/routes/debug_log.py`, `tests/test_browse_cli_session_debug_log.py`, `browse-ui/src/lib/api/{types.ts,schemas.ts,hooks.ts}`, `browse-ui/src/lib/{debug-span-flow.ts,flight-recorder.ts}`, `browse-ui/src/app/sessions/[id]/**`, `browse-ui/src/components/data/**`, `browse-ui/e2e/**`
**Estimated phases:** CLARIFY -> RESEARCH -> DESIGN -> BUILD -> TEST -> REVIEW -> LOOP-EVAL -> COMMIT

---

## Step 1: CLARIFY — Define observable sub-agent visibility

**Goal:** Convert "không thấy sub agent hoạt động như thế nào" into explicit, testable behavior.

**Actions:**
1. `python3 briefing.py "make sub-agent activity visible in Flight Recorder timeline and debug log" --auto --compact` — load relevant past mistakes and patterns.
2. Inspect current event evidence from `~/.copilot/session-state/33169957-0dc1-4998-86c0-d2beba02e8b4/events.jsonl` for `subagent.started`, `subagent.completed`, and `subagent.failed`.
3. Inspect current UI/model files: `browse/routes/debug_log.py`, `browse-ui/src/lib/debug-span-flow.ts`, `browse-ui/src/lib/debug-event-playback.ts`, `browse-ui/src/lib/flight-recorder.ts`, `browse-ui/src/components/data/timeline-player.tsx`, `browse-ui/src/app/sessions/[id]/debug-log-tab.tsx`.

**Done when:** Facts show whether raw sub-agent data exists, which fields are safe, and exactly why current UI fails to explain sub-agent activity.
**Confidence:** `1.0` required before BUILD. If below `1.0`, run Step 2 research first.

---

## Step 2: RESEARCH — Validate data contract and UI fit

**Goal:** Resolve design ambiguity with independent agents before implementation.

**Actions:**
1. Dispatch a backend/data research tentacle to answer: which sub-agent fields can be safely exposed, how start/end pairing works, what route shape is needed, and how to cap large sessions.
2. Dispatch a UI/product research tentacle to answer: where sub-agent activity belongs (Timeline header, Debug Log flow, MissionStrip, drawer/table), what interactions prove usefulness, and how to avoid another superficial chart.
3. Dispatch a verification/security research tentacle to validate the data contract against redaction and large-file constraints.

**Done when:** Research handoffs agree on a bounded, redaction-safe contract and rejected alternatives are recorded. Synthesized routing confidence is `1.0`.
**Confidence:** `1.0` required; otherwise split remaining ambiguity and re-run research.

---

## Step 3: DESIGN — Specify sub-agent activity contract

**Goal:** Define a minimal contract and UI model before coding.

**Actions:**
1. Write the contract into tentacle context: event grouping by `toolCallId` / `agentId`, safe fields, caps, ordering, and failure handling.
2. Define acceptance selectors before implementation, e.g. `subagent-activity-panel`, `subagent-activity-row-*`, `mission-chip-subagents`, `debug-log-flow-node-*`.
3. Define jump behavior from an activity row to the corresponding debug event.

**Done when:** Implementation tentacles have non-overlapping scopes, exact files, and an evidence contract.
**Confidence:** `1.0` required.

---

## Step 4: BUILD — Backend/model support

**Goal:** Provide bounded safe sub-agent activity data and model helpers.

**Actions:**
1. Add or extend backend parsing so sub-agent started/completed/failed events expose safe scalar attrs only: agent display/name, model, tool-call count, token count, duration, phase/status; never raw prompts/results/descriptions.
2. Add/extend TypeScript schemas/types/hooks for any new response or attrs.
3. Add deterministic model helpers for grouping start/end/failure into activity rows.

**Done when:** Python AST parse and TypeScript typecheck pass for changed files.

---

## Step 5: BUILD — UI integration

**Goal:** Render sub-agent activity in a way that answers what ran, when, and with what outcome.

**Actions:**
1. Add a visible sub-agent activity surface in Debug Log / Flight Recorder (panel/table/rail section) with start time, status, agent name, model, tool calls, tokens, duration, and jump/open behavior.
2. Ensure Timeline and Debug Log do not depend on the first 100/5000 events to discover sub-agents in large sessions.
3. Keep existing Flow, MissionStrip, chapter rail, playback, and filters working.

**Done when:** The target session shows non-zero sub-agent activity without manual pagination guessing.

---

## Step 6: TEST — Add regression coverage

**Goal:** Prove the original complaint is fixed and existing Flight Recorder behavior remains intact.

**Actions:**
1. Add Python tests for sub-agent safe attrs and any route grouping/capping behavior.
2. Add Vitest tests for model helpers and UI rendering.
3. Add/update Playwright proof with synthetic sub-agent started/completed/failed events and the real target session path.

**Done when:** Tests fail before the change or document the current missing behavior, then pass after implementation.

---

## Step 7: REVIEW — Skeptical code/security review

**Goal:** Catch correctness, security, and scope issues before shipping.

**Actions:**
1. Run a code-review agent on changed files with focus on path traversal, redaction, large-session performance, schema compatibility, and UI false claims.
2. Fix all critical/high findings and re-review until clean.

**Done when:** Review verdict is clean or all findings are explicitly resolved with evidence.

---

## Step 8: LOOP-EVAL — Verify goal on real hosted session

**Goal:** Confirm the actual user-facing hosted page now makes sub-agent activity understandable.

**Actions:**
1. Run local gates: `python3 test_security.py && python3 test_fixes.py`; `cd browse-ui && pnpm typecheck && pnpm lint && pnpm format:check && pnpm test && pnpm build`.
2. Run targeted E2E for sub-agent activity and Flight Recorder.
3. Deploy to `agents.linhngo.dev`, verify `/version.json` hash, then run hosted Playwright proof against session `33169957-0dc1-4998-86c0-d2beba02e8b4`.

**Done when:** Hosted proof reports visible sub-agent activity rows and no console/page/API errors; CI is green for the shipped commit.

---

## Step 9: COMMIT — Ship and record lessons

**Goal:** Preserve the fixed implementation and knowledge.

**Actions:**
1. `git diff --stat` — confirm only expected files changed.
2. Commit with the required Co-authored-by trailer.
3. `python3 learn.py --mistake ...` / `--pattern ...` — record the lesson about sub-agent visibility data needing an explicit mission-level surface.

**Done when:** `origin/main` points to the final commit, hosted deploy serves that hash, worktree is clean, and lessons are recorded.

---

## Phase Gates

| Phase | Artifact | Status |
|-------|---------|--------|
| CLARIFY | Raw event evidence + current UI gap statement | ☐ |
| RESEARCH | Independent handoffs + confidence `1.0` | ☐ |
| DESIGN | Contract + selectors + jump behavior | ☐ |
| BUILD | Backend/model/UI compile | ☐ |
| TEST | Python + Vitest + Playwright proofs | ☐ |
| REVIEW | Clean code/security review | ☐ |
| LOOP-EVAL | Hosted proof + CI green | ☐ |
| COMMIT | Clean pushed commit + lessons | ☐ |
