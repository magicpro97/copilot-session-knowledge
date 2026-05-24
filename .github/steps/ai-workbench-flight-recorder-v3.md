# STEPS: AI Workbench Flight Recorder v3

**Task:** Enrich Debug Log Flow and Timeline so the session-state view has a clear mission: an AI Workbench Flight Recorder that explains what happened, why it mattered, where work failed or succeeded, and where to resume.
**User intent:** Do not self-code from intuition. Research and coordinate agents first; use lessons from Debug Flow v2 so implementation is spec-led, reviewed, tested, and deployed.
**Initial scope candidates:** `events.jsonl` importer/API mapping, Browse UI timeline/flow data models, session detail tabs, checkpoints/workspace artifacts, E2E hosted proof. Final implementation scope is blocked until research/spec confidence is `1.0`.

---

## Step 1: CLARIFY - Define the product mission and acceptance signal

**Goal:** Turn "make it useful for life" into an implementation-ready spec.

**Actions:**
1. Use current session-state evidence from session `33169957-0dc1-4998-86c0-d2beba02e8b4`: `events.jsonl`, `checkpoints/`, `workspace.yaml`, `files/`, `rewind-snapshots/`.
2. Define the mission in product terms: explain, debug, audit, resume, learn.
3. Define concrete acceptance criteria for a user opening `#debug-log` or `#timeline`: what question should the UI answer within 10 seconds?

**Done when:** A research/spec handoff states a CLEAN spec, affected surfaces, risks, rejected alternatives, and confidence `1.0`.
**Confidence:** `<1.0` until Opus research/spec agents complete.

---

## Step 2: RESEARCH - Audit session-state enrichment sources

**Goal:** Prove which session-state sources are safe, useful, and cheap enough to add.

**Actions:**
1. Profile `events.jsonl` event types and safe fields already exposed by `browse/routes/debug_log.py`.
2. Audit checkpoint metadata, workspace metadata, rewind snapshots, and persistent files for safe summary/enrichment use.
3. Classify candidate enrichments by value:
   - narrative chapters from checkpoints,
   - tool/hook/subagent/skill lanes and outcomes,
   - resume anchors,
   - failure/retry hotspots,
   - evidence/deploy/CI markers,
   - learning patterns.

**Done when:** Handoff lists each candidate with source file, safety constraints, UI value, implementation cost, and whether it is accepted/rejected/deferred.
**Confidence:** `1.0` required before design.

---

## Step 3: DESIGN - Flight Recorder information architecture

**Goal:** Design a minimal v3 that adds meaning without making the UI noisier.

**Actions:**
1. Decide whether v3 is additive on existing Flow/Timeline or needs a new "Flight Recorder" layer.
2. Specify model contracts: phase chapters, event summaries, agent/tool/skill rollups, failure hotspots, resume anchors.
3. Specify UI placement and interaction:
   - Timeline chapter rail,
   - Flow insight overlays,
   - selected event "why useful" panel,
   - resume/action anchors.
4. Specify safe-field rules: no raw paths, prompts, tool args/results, or unsanitized attrs.

**Done when:** Design handoff includes exact model/UI contracts, data source ownership, test selectors, security constraints, and no unresolved ambiguity.
**Confidence:** `1.0` required before build.

---

## Step 4: VERIFY - Reader test and decomposition review

**Goal:** Prevent agents from building the wrong product.

**Actions:**
1. Run a fresh-agent reader test against the spec: ask what v3 should answer, which files it may touch, what is out of scope, and how success is proven.
2. Review this step file using `tentacle-orchestration/references/decomposition-review.md`.
3. Record accepted, edited, and rejected steps in the implementation tentacle context.

**Done when:** Reader test returns no "NOT SPECIFIED" for blocking requirements and decomposition confidence is `1.0`.
**Confidence:** `1.0` required before implementation tentacles.

---

## Step 5: BUILD - Backend/model foundation

**Goal:** Add only the data contracts needed for the accepted v3 scope.

**Actions:**
1. If research proves existing debug-log API is enough, avoid backend changes.
2. If safe aggregation is needed, add bounded projections only; do not expose raw `events.jsonl` payloads.
3. Add pure model helpers and tests before UI wiring.

**Done when:** Focused model/backend tests prove the accepted contract and redaction boundaries.
**Confidence:** Blocked until Steps 1-4 pass.

---

## Step 6: BUILD - Browse UI Flight Recorder layer

**Goal:** Make Flow/Timeline answer useful questions, not merely show log geometry.

**Actions:**
1. Add the accepted UI elements from the design: chapters, hotspots, rollups, resume anchors, or insight panel.
2. Preserve Debug Flow v2 and Timeline playback test IDs and behavior.
3. Ensure every new interactive element links back to an event, checkpoint, phase, or evidence marker.

**Done when:** Component tests prove the user can answer the accepted questions from the UI.
**Confidence:** Blocked until Step 5 passes.

---

## Step 7: TEST - Local and hosted proof

**Goal:** Prove the feature works on the real target session, not only a tiny fixture.

**Actions:**
1. Run relevant Browse UI unit/component tests.
2. Run Playwright smoke for local fixtures.
3. Run hosted proof against `https://agents.linhngo.dev/sessions/33169957-0dc1-4998-86c0-d2beba02e8b4#debug-log` and `#timeline`.
4. Record visible v3 selectors, counts, selected anchors, API status, and console/page/API error counts.

**Done when:** Local gates and hosted proof are green with concrete output.

---

## Step 8: REVIEW - Code, security, UX, and QA

**Goal:** Catch the same classes of mistakes that v2 review caught before merge.

**Actions:**
1. Code review: boundary math, event-to-anchor mapping, sorting, null timestamps, page/window behavior.
2. Security review: no raw attrs, paths, prompts, tool args/results, or unsafe HTML.
3. UX QA: verifies v3 answers the mission questions and is not just decorative.
4. Verification gate: typecheck, lint, format, tests, build, E2E.

**Done when:** Review/security/QA are CLEAN/PASS or findings are fixed and re-reviewed.

---

## Step 9: LOOP-EVAL - Decide whether v3 is useful enough

**Goal:** Evaluate the overarching goal before commit/deploy.

**Actions:**
1. Run a goal-eval proof script or Playwright assertion that answers:
   - What phase am I in?
   - What failed or took longest?
   - Which agent/tool/skill mattered?
   - Where can I resume?
   - What evidence proves success?
2. If any question is not answerable, create a new focused tentacle for the gap.

**Done when:** Goal-eval evidence shows all accepted mission questions are answerable on the target session.

---

## Step 10: COMMIT - Ship and deploy

**Goal:** Preserve, push, deploy, and verify the exact shipped commit.

**Actions:**
1. Run final gates.
2. Commit with evidence and trailer.
3. Push `main`.
4. Deploy Firebase hosting target `agents`.
5. Verify `/version.json`, hosted proof, CI success, and clean worktree.

**Done when:** `origin/main`, hosted `version.json`, CI, and hosted proof all point to the final commit.

---

## Phase Gates

| Phase | Artifact | Status |
|-------|----------|--------|
| CLARIFY | CLEAN product spec + mission questions | [ ] |
| RESEARCH | Session-state source audit confidence `1.0` | [ ] |
| DESIGN | Data/UI/security contract | [ ] |
| VERIFY | Reader test + decomposition review PASS | [ ] |
| BUILD | Backend/model/UI implementation | [ ] |
| TEST | Local + hosted proof | [ ] |
| REVIEW | Code/security/UX/verification CLEAN | [ ] |
| LOOP-EVAL | Mission questions answerable | [ ] |
| COMMIT | Pushed/deployed commit with CI success | [ ] |

---

## Step-plan review

- Source: generated by orchestrator for tentacle workflow, then reviewed against `decomposition-review.md`.
- Accepted steps: 1-4 for research/spec and decomposition; 7-10 for evidence and ship gates.
- Edited steps before dispatch: Steps 5-6 are intentionally blocked until research defines the safe minimal v3 scope.
- Rejected for first wave: direct UI coding, backend API expansion, raw event payload display, replacing v2 Flow/Timeline, and any implementation based only on the orchestrator's intuition.
- Dependency order: research/spec -> reader/decomposition review -> implementation -> tests -> reviews -> goal-eval -> commit/deploy.
- Evidence contract: each tentacle handoff must list facts, interpretation, actions, verification evidence, rejected alternatives, and confidence. Implementation tentacles must run focused tests; orchestrator must rerun final gates.
