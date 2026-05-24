# STEPS: Timeline + Debug Log playback design

**Task:** Research and design a combined or coordinated Timeline / Debug Log experience where playback reveals agent activity over time and the UI can draw a detailed flow state at each time position.
**Scope:** `browse/routes/timeline.py`, `browse/routes/debug_log.py`, `browse-ui/src/app/sessions/[id]/**`, `browse-ui/src/components/data/timeline-player.tsx`, `browse-ui/src/lib/debug-span-flow.ts`, `browse-ui/src/lib/api/{types,schemas}.ts`, tests under `tests/`, `browse-ui/src/**/__tests__`, and `browse-ui/e2e/`.
**Estimated phases:** CLARIFY -> RESEARCH -> DESIGN -> VERIFY -> BUILD -> TEST -> REVIEW -> QA -> LOOP-EVAL -> COMMIT

---

## Spec Health Report

### Summary

- **Clarity:** Red - user goal is clear at a product level, but exact interaction model is not implementation-ready.
- **Completeness:** Red - needs research for reference patterns, event model, large-session behavior, and tab merge/separation trade-off.
- **Consistency:** Yellow - current app already has separate `Timeline` and `Debug Log` tabs; any merge must preserve deep links and keyboard shortcuts.
- **Testability:** Yellow - acceptance can be made measurable with Playwright playback assertions, console capture, and API/schema tests.
- **Feasibility:** Yellow - feasible, but current `/api/session/{id}/events` timeline contract is raw-offset based and weaker than the debug-log contract.
- **Verdict:** BLOCKED for implementation until RESEARCH and DESIGN reach confidence `1.0`.

### Current-code facts

- `browse-ui/src/app/sessions/[id]/timeline-tab.tsx` fetches `/api/session/{id}/events?from=0&limit=200` and renders `TimelinePlayer`.
- `browse-ui/src/components/data/timeline-player.tsx` has play/pause, speed, range slider, simple colored tick bar, and raw preview card.
- `browse-ui/src/app/sessions/[id]/debug-log-tab.tsx` fetches session-scoped debug entries with pagination and supports `list`, `tree`, and `flow`.
- `browse-ui/src/app/sessions/[id]/debug-log-flow-chart.tsx` renders a span graph, not a time-axis graph.
- `browse/routes/timeline.py` returns `TimelineEvent {event_id, kind, preview, byte_offset, file_mtime, color}` from `event_offsets`.
- `browse/routes/debug_log.py` returns richer `BrowseDebugEntry` records from Copilot CLI `events.jsonl`, with safe attrs, timestamps, span IDs, parents, durations, statuses, and redaction.

### Unknowns requiring research

1. Whether the product should merge `Timeline` and `Debug Log` into one tab, keep two tabs, or make Timeline a playback mode inside Debug Log.
2. How VS Code/Copilot Chat or adjacent open-source agent-debug UIs present time playback, spans, layers, agents, skills, and tool calls.
3. What the playback state means: prefix-of-events, active interval window, selected timestamp, or animated "current event" only.
4. What clicking a timeline tick/segment should do: select event, scrub time, filter graph to prefix, open detail, show active spans, or all of the above.
5. How to avoid UI regressions on 38k+ event sessions, including paging, virtualization, and not drawing huge SVGs per frame.

---

## Step 1: CLARIFY — Freeze implementation until research answers the product questions

**Goal:** Turn the verbal goal into explicit acceptance criteria and non-goals.

**Actions:**
1. Record this step file as the source-of-truth planning scaffold.
2. Split the unknowns into research tentacles: reference UI research, current data-model audit, and UX interaction spec.
3. Do not create implementation tentacles until the design synthesis states confidence `1.0`.

**Done when:** Research tentacles exist, each has atomic questions, and BUILD steps remain blocked until confidence reaches `1.0`.
**Confidence:** `1.0` for the need to research first; `<1.0` for implementation choices.

---

## Step 2: RESEARCH — Reference UI and repository study

**Goal:** Find existing patterns to imitate/improve instead of inventing the playback UI from scratch.

**Actions:**
1. Study VS Code Copilot Chat / Agent Debug Flow source and any accessible timeline/debug/playback views in open-source repos.
2. Search for agent observability UIs: span timeline, trace waterfall, replay, tool-call timeline, and workflow graph playback.
3. Produce facts with citations: layout patterns, interaction rules, data model assumptions, and rejected alternatives.

**Done when:** Research handoff includes cited source files/URLs, screenshots or source excerpts where available, and a recommended UI pattern with confidence score.
**Confidence:** `1.0` required before DESIGN.

---

## Step 3: RESEARCH — Current app data/API capability audit

**Goal:** Determine which existing backend/frontend contracts can support playback and which must change.

**Actions:**
1. Audit `browse/routes/timeline.py`, `browse/routes/debug_log.py`, debug-log redaction contract, `TimelineEvent`, `BrowseDebugEntry`, schemas, and current tests.
2. Compare timeline raw-offset data vs debug-log rich event data for timestamp, parent/child graph, duration, status, category, pagination, and large-session behavior.
3. Propose the minimum safe contract change, or prove no contract change is required.

**Done when:** Handoff lists exact APIs/types that need change, security/redaction constraints, and test surfaces.
**Confidence:** `1.0` required before implementation decomposition.

---

## Step 4: DESIGN — Decide merge-vs-separate tab strategy

**Goal:** Produce a UX decision record for whether to merge tabs, keep tabs separate, or introduce a shared playback surface.

**Actions:**
1. Evaluate at least three options:
   - A: Merge `Timeline` and `Debug Log` into one tab.
   - B: Keep separate tabs; Timeline gains rich playback + time axis; Debug Log remains list/tree/flow.
   - C: Keep tabs but share a common `DebugEventPlayback` model and let Debug Log expose a playback mode.
2. Score each option on user comprehension, deep-link compatibility, performance, implementation risk, and testability.
3. Define selected option, non-goals, and fallback if research confidence remains below `1.0`.

**Done when:** Design handoff states one chosen option with rejected alternatives and confidence `1.0`, or returns `AMBIGUOUS`.
**Confidence:** `1.0` required before BUILD.

---

## Step 5: DESIGN — Specify timeline playback interactions

**Goal:** Define exactly what the UI shows and how every interaction behaves.

**Actions:**
1. Specify play/pause, speed, step next/previous, scrubber drag, tick click, keyboard controls, and selected-event detail behavior.
2. Specify graph playback semantics: visible prefix, active event, active span intervals, layer grouping, agent/skill/tool/hook cards, and elapsed clock.
3. Specify large-data behavior: page/window size, sampling/aggregation, "more events" hint, no layout thrash, and how playback handles missing timestamps.
4. Specify accessibility: focus order, `aria-live` policy, keyboard shortcuts, and reduced-motion behavior.

**Done when:** Interaction spec is precise enough that a fresh agent can answer "what happens when I click timestamp X?" without guessing.
**Confidence:** `1.0` required before BUILD.

---

## Step 6: VERIFY — Reader-test the spec and design

**Goal:** Prove the design is understandable before code starts.

**Actions:**
1. Dispatch a fresh review agent with only the design/spec and ask it to answer predicted implementation questions.
2. Dispatch a skeptical UX/design review agent to find ambiguous states and UI failure modes.
3. Update the design until both reviews return CLEAN or explicitly mark blockers.

**Done when:** Reader-test and UX review handoffs are CLEAN, with no blocking "NOT SPECIFIED" answers.
**Confidence:** `1.0` required before BUILD.

---

## Step 7: BUILD — Backend/API model, only after design is approved

**Goal:** Implement the smallest backend/data contract needed for the chosen playback model.

**Actions:**
1. Add or adapt API output with safe fields only; never expose raw prompts, tool args/results, paths, or unredacted content.
2. Preserve existing timeline/debug-log contracts unless the design explicitly requires a versioned addition.
3. Add focused tests in `tests/` for schema, pagination/windowing, redaction, timestamp ordering, and large-session safety.

**Done when:** Backend tests demonstrate the selected playback contract and existing debug/timeline tests still pass.
**Confidence:** Blocked until Steps 2-6 pass.

---

## Step 8: BUILD — Shared frontend playback model

**Goal:** Create pure frontend derivation helpers that convert API events into playback frames without rendering UI.

**Actions:**
1. Add pure helpers for time-domain normalization, tick aggregation, active frame derivation, and graph-prefix/active-span state.
2. Unit test missing timestamps, out-of-order events, repeated span IDs, huge pages, and safe fallback labels.
3. Keep rendering components dependent on a presentation model, not raw attrs.

**Done when:** Unit tests cover frame derivation and no React component is required to verify model logic.
**Confidence:** Blocked until Steps 2-6 pass.

---

## Step 9: BUILD — UI renderer and interaction wiring

**Goal:** Implement the chosen playback UI with stable interactions and no console/browser errors.

**Actions:**
1. Update `TimelinePlayer`, `DebugLogFlowChart`, or new shared components according to the design.
2. Wire play/pause/scrub/tick click/keyboard interactions to the shared playback model.
3. Preserve current `#timeline` and `#debug-log` deep links unless the design explicitly migrates them.

**Done when:** Component tests prove play/pause, scrubbing, tick click, detail selection, graph update, and reduced-motion behavior.
**Confidence:** Blocked until Steps 2-8 pass.

---

## Step 10: TEST — Browser and hosted proof

**Goal:** Prove the feature works on a real session and does not regress the Debug Log UX.

**Actions:**
1. Run backend tests: `python3 test_security.py && python3 test_fixes.py` plus focused timeline/debug-log tests.
2. Run Browse UI gates: `cd browse-ui && pnpm typecheck && pnpm lint && pnpm format:check && pnpm test && pnpm build`.
3. Add Playwright proof for the target session page showing playback, tick click/scrub, graph frame update, and no console errors.
4. Run hosted smoke after deploy if code ships to `agents.linhngo.dev`.

**Done when:** Evidence logs include pass/fail counts and browser proof of the original user-facing flow.
**Confidence:** `1.0` only with command output.

---

## Step 11: REVIEW — Code, security, UX, and QA review

**Goal:** Catch design mismatch, redaction leaks, performance regressions, and UI edge cases before shipping.

**Actions:**
1. Dispatch code review on the full diff.
2. Dispatch browser/security review for hosted loopback, redaction, SVG/text rendering, and local API safety.
3. Dispatch QA review to compare implemented behavior against the approved design and reference research.

**Done when:** All reviewers return CLEAN or issues are fixed and re-reviewed.
**Confidence:** `1.0` only with reviewer evidence.

---

## Step 12: LOOP-EVAL — Decide whether the goal is met or another iteration is required

**Goal:** Prevent another "technically shipped but not what the user meant" outcome.

**Actions:**
1. Evaluate implementation against the approved interaction spec and reference research.
2. Run the exact hosted URL smoke on `https://agents.linhngo.dev/sessions/33169957-0dc1-4998-86c0-d2beba02e8b4#timeline` and/or `#debug-log`.
3. If any acceptance criterion fails, create new tentacles for the gaps instead of declaring done.

**Done when:** Goal-eval evidence says MET with the browser output, or a new iteration is created for explicit gaps.
**Confidence:** `1.0` only with recorded proof.

---

## Step 13: COMMIT — Ship only after goal-eval passes

**Goal:** Commit/push/deploy only verified work.

**Actions:**
1. `git diff --stat` — confirm only expected files changed.
2. Commit with issue refs after all gates pass.
3. Push, deploy, and run deploy verifier if hosted UI changed.

**Done when:** `origin/main` and hosted build hash point to the verified commit.

---

## Phase Gates

| Phase | Artifact | Status |
|-------|---------|--------|
| CLARIFY | This step file + research tentacles created | ☐ |
| RESEARCH | Reference research and current-code audit handoffs with confidence `1.0` | ☐ |
| DESIGN | Merge-vs-separate decision + playback interaction spec | ☐ |
| VERIFY | Reader-test and UX review CLEAN | ☐ |
| BUILD | Backend/model/UI implementation gates | ☐ |
| TEST | Python + Browse UI + Playwright evidence | ☐ |
| REVIEW | Code/security/QA review CLEAN | ☐ |
| LOOP-EVAL | Hosted/session playback proof meets approved spec | ☐ |
| COMMIT | Clean commit/push/deploy | ☐ |
