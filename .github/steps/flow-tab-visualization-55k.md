# STEPS: Upgrade Debug Log Flow tab for 55k-session visualization

**Task:** Improve the existing Debug Log **Flow** view so a large session (`33169957-0dc1-4998-86c0-d2beba02e8b4`, 55k+ events) becomes visually understandable, not just a 100-event page tree.
**Scope:** `browse-ui/src/app/sessions/[id]/debug-log-tab.tsx`, `browse-ui/src/app/sessions/[id]/debug-log-flow-chart.tsx`, `browse-ui/src/lib/debug-span-flow.ts`, `browse-ui/src/lib/api/{hooks,schemas,types}.ts`, `browse/routes/debug_log.py` or additive route files if research proves existing endpoints are insufficient, and related tests.
**Estimated phases:** CLARIFY -> RESEARCH -> DESIGN -> BUILD -> TEST -> REVIEW -> LOOP-EVAL -> COMMIT

---

## Step 1: CLARIFY — Freeze the target behavior

**Goal:** Replace the vague target "make Flow better" with observable behavior for the existing Flow tab.

**Actions:**
1. Read `browse-ui/src/app/sessions/[id]/debug-log-tab.tsx`, `debug-log-flow-chart.tsx`, `browse-ui/src/lib/debug-span-flow.ts`, and the existing Flow tests.
2. Capture RED evidence: current Flow receives only the current Debug Log page (`PAGE_SIZE = 100`) and shows "More events available" instead of a full-session visual.
3. Define the Flow tab mission: answer "when did each activity layer/subagent/tool/skill happen, how dense was it, what changed over time, and where can I drill into raw debug entries?"

**Done when:** The plan records an implementation-ready spec with no ambiguity about Flow vs Timeline/Mission Atlas ownership.
**Confidence:** `1.0` required; otherwise block BUILD and run more research.

---

## Step 2: RESEARCH — Validate data and visual model

**Goal:** Prove which existing data sources can support a 55k-event Flow visual and where new aggregation is required.

**Actions:**
1. Profile the real session via local backend: `/api/session/{id}/debug-log`, `/subagent-internals`, `/subagent-activity`, and `/mission-atlas`.
2. Compare current Flow with VS Code/Copilot-style debug flow references and large-trace visual patterns.
3. Decide whether to reuse Mission Atlas aggregates inside Flow, extend them, or add a Flow-specific aggregate route.

**Done when:** Research evidence lists accepted and rejected alternatives, with a selected design at confidence `1.0`.
**Confidence:** `1.0` required; otherwise no implementation.

---

## Step 3: DESIGN — Specify Flow 55k UI

**Goal:** Define a concrete Flow UI that remains readable at 55k events.

**Actions:**
1. Design the Flow view as layered, aggregate-first visualization: full-session lane density, subagent swimlanes, tool/skill clusters, error markers, and drill-down into page/detail.
2. Define interactions: click bucket/lane/subagent/tool/skill -> navigate to the right event page and select/open detail; zoom/pan must not break scroll.
3. Define payload bounds and redaction: no raw args/results/content/prompts/paths; only safe labels/counts/indices/timestamps.

**Done when:** UI/data contract and interaction map are specific enough for implementation and tests.
**Confidence:** `1.0` required.

---

## Step 4: BUILD — Implement the smallest complete Flow upgrade

**Goal:** Change the existing Flow tab, not a separate Timeline-only widget.

**Actions:**
1. Add or reuse a bounded full-session aggregate hook available to `DebugLogTab` when `viewMode === "flow"`.
2. Update `DebugLogFlowChart` to render full-session overview above the current page-level SVG tree: lanes, density buckets, subagent/tool/skill clusters, and clear "page window" context.
3. Wire drill-down so selected aggregate points navigate to the matching Debug Log page and open the existing detail drawer.

**Done when:** The Flow tab visually explains all 55k+ events while the detailed SVG tree still works for the selected/current page.
**Confidence:** `1.0` required.

---

## Step 5: TEST — Prove the new Flow behavior

**Goal:** Tests cover the actual regression: Flow cannot represent the full session.

**Actions:**
1. Add pure model tests for full-session aggregate rendering/drill-down decisions.
2. Add component tests for the Flow tab showing aggregate lanes/subagents/tools/skills and aggregate-click page navigation.
3. Run `cd browse-ui && pnpm typecheck && pnpm lint && pnpm format:check && pnpm test -- --run && pnpm build`.
4. If any Python backend changes are made, run `python3 test_security.py` and `python3 test_fixes.py`.

**Done when:** All relevant gates exit 0 and test output includes new Flow assertions.
**Confidence:** `1.0` required.

---

## Step 6: REVIEW — Security, correctness, and UX review

**Goal:** Catch false completion before shipping another shallow visualization.

**Actions:**
1. Run code review focused on Flow contract, large-session performance, drill-down correctness, and scope creep.
2. Run security review for any backend/data-contract changes.
3. Fix all critical/high findings and rerun impacted gates.

**Done when:** Reviews are CLEAN or all blocking findings are fixed with evidence.
**Confidence:** `1.0` required.

---

## Step 7: LOOP-EVAL — Check the original goal

**Goal:** Decide whether the upgraded Flow tab now satisfies the user's requested purpose.

**Actions:**
1. Runtime check the real hosted/local session at `https://agents.linhngo.dev/sessions/33169957-0dc1-4998-86c0-d2beba02e8b4#debug-log`.
2. Verify the Flow tab is not limited to 100 visible-page events and exposes full-session layers/subagents/tools/skills with meaningful drill-down.
3. If gaps remain, create a new research/build iteration instead of declaring success.

**Done when:** Goal criteria are met with command/test/runtime evidence, or remaining gaps are explicitly fed into a new loop.
**Confidence:** `1.0` required.

---

## Step 8: COMMIT — Ship only after goal evidence

**Goal:** Preserve the verified Flow upgrade in `main` and deploy it.

**Actions:**
1. `git diff --stat` confirms only expected files changed.
2. Commit with the required Copilot co-author trailer.
3. Push to `origin/main`, deploy `browse-ui`, and verify hosted `version.json` plus the real session page.

**Done when:** Commit is on `main`, hosted build hash matches, and Flow runtime evidence is recorded.
**Confidence:** `1.0` required.

---

## Phase Gates

| Phase | Artifact | Status |
|-------|---------|--------|
| CLARIFY | Current Flow limitation documented with source lines / runtime evidence | ☑ |
| RESEARCH | Selected data/visual model with rejected alternatives | ☑ |
| DESIGN | Flow UI + data contract + interaction map | ☑ |
| BUILD | Existing Flow tab renders full-session aggregate visual | ☑ |
| TEST | Browse UI gates and backend gates if applicable | ☑ |
| REVIEW | Code/security reviews clean or fixed | ☑ |
| LOOP-EVAL | Real 55k session Flow satisfies full-session visualization goal | ☑ |
| COMMIT | Commit, push, deploy, hosted verification | ☐ |
