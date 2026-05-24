# STEPS: Debug Log Flow v2 trace inspector

**Task:** Upgrade the Debug Log `flow` view beyond the current parent/child SVG tree into a richer trace inspector: timeline lanes, event density, span details, skill/tool/model metadata, safe redacted details, and proof on the target hosted session.
**Scope:** `browse-ui/src/app/sessions/[id]/debug-log-flow-chart.tsx`, `browse-ui/src/lib/debug-span-flow.ts`, their tests, and `browse-ui/e2e/smoke.spec.ts`. Backend changes are out unless research proves missing safe fields are required.

---

## Step 1: CLARIFY — Define the v2 acceptance signal

**Goal:** Convert “is this hết cỡ?” into measurable Debug Log Flow v2 criteria.

**Actions:**
1. Inspect current flow renderer/model/tests.
2. Compare current capability against prior reference research and the user’s expectation: time, layers, agents/tools/skills, detailed event lifecycle.
3. Freeze v2 scope to the smallest complete improvement that can ship safely today.

**Done when:** A research/design handoff states acceptance criteria and confidence `1.0`.
**Confidence:** `<1.0` until the research/design tentacle completes.

---

## Step 2: DESIGN — Trace inspector model

**Goal:** Specify the pure model additions needed for a Debug Log Flow inspector.

**Actions:**
1. Define safe derived fields only: lane/category counts, time range, per-lane bars, selected event metrics, skill byte/token/tool metadata, and error/status summaries.
2. Preserve existing span-tree behavior and public test IDs unless intentionally extended.
3. Define missing timestamp fallback and large-page behavior.

**Done when:** Model contract is implementable without reading raw unsafe payloads.
**Confidence:** `1.0` required before BUILD.

---

## Step 3: BUILD — Pure model additions

**Goal:** Add reusable, tested trace-inspector derivation helpers.

**Actions:**
1. Extend `debug-span-flow.ts` with pure helpers for trace lanes/range/bars/summary.
2. Add unit tests covering mixed lanes, missing timestamps, skill bytes/tokens, errors, and large pages.

**Done when:** Focused model tests pass.
**Confidence:** Blocked until Steps 1–2 pass.

---

## Step 4: BUILD — Flow UI v2

**Goal:** Render a richer Debug Log Flow view using the new model.

**Actions:**
1. Add a compact trace overview above the existing tree: time ruler, lane rows, density/error markers, and click-to-select bars.
2. Add an inspector strip/card showing selected event details, layer/category, status, duration, token/byte/tool/skill metadata where available.
3. Keep non-passive wheel zoom and existing tree interactions.

**Done when:** Component tests prove lanes/time axis/inspector/click selection and existing flow behavior still work.
**Confidence:** Blocked until Step 3 passes.

---

## Step 5: TEST — Browser proof

**Goal:** Prove Debug Log Flow v2 works in Playwright and does not regress Timeline.

**Actions:**
1. Extend `browse-ui/e2e/smoke.spec.ts` flow-chart proof to assert the v2 trace lanes, time ruler, inspector details, and click-to-select behavior.
2. Run focused Vitest + Playwright gates.

**Done when:** Smoke proof passes with zero console/page/API errors.
**Confidence:** `1.0` only with command output.

---

## Step 6: REVIEW — Code/security/UX

**Goal:** Catch correctness, redaction, and UX regressions before shipping.

**Actions:**
1. Dispatch code review for model/UI changes.
2. Dispatch security review for safe fields/redaction/no raw attrs leakage.
3. Dispatch UX QA against “trace inspector, not sparse tree only.”

**Done when:** Reviews are CLEAN or findings are fixed and re-reviewed.
**Confidence:** `1.0` only with reviewer evidence.

---

## Step 7: LOOP-EVAL — Hosted target proof

**Goal:** Confirm the real hosted URL now has Debug Log Flow v2, not only local fixture proof.

**Actions:**
1. Build/deploy.
2. Run browser proof on `https://agents.linhngo.dev/sessions/33169957-0dc1-4998-86c0-d2beba02e8b4#debug-log`.
3. Confirm v2 flow trace lanes and inspector render, clicking a lane bar selects an event, and console/page/API errors are zero.

**Done when:** Hosted proof output records the target URL, visible v2 elements, selected entry, API status 200s, and zero errors.
**Confidence:** `1.0` only with recorded proof.

---

## Step 8: COMMIT — Ship

**Goal:** Commit, push, deploy, and keep CI green.

**Actions:**
1. `git diff --stat` and `git diff --check`.
2. Run final Python and Browse UI gates.
3. Commit with evidence, push `main`, deploy Firebase hosting, verify `/version.json`, and wait for CI.

**Done when:** `origin/main`, hosted `version.json`, hosted proof, and CI all point to the final commit.

---

## Phase Gates

| Phase | Artifact | Status |
|-------|---------|--------|
| CLARIFY | Research/design handoff confidence `1.0` | ☐ |
| DESIGN | Trace inspector model/UI contract | ☐ |
| BUILD | Model + UI implementation | ☐ |
| TEST | Unit + Playwright proof | ☐ |
| REVIEW | Code/security/UX clean | ☐ |
| LOOP-EVAL | Hosted target proof | ☐ |
| COMMIT | Pushed/deployed commit with CI success | ☐ |
