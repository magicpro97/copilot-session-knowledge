# STEPS: Rework Browse Debug Flow to VS Code Agent Debug detail level

**Task:** Fix the hosted Browse Debug Log Flow chart passive-wheel zoom error and enrich the chart so Copilot CLI sessions show VS Code Agent Debug-style execution detail: timestamps, event layers, tool calls, subagent/skill activity, model/token metadata, readable labels/sublabels/tooltips, and browser-proven behavior.

**Scope:**
- `.github/steps/fix-debug-flow-vscode-detail.md` (this reviewed scaffold)
- `browse/routes/debug_log.py`
- `tests/test_browse_cli_session_debug_log.py`
- `docs/DEBUG-LOG-CONTRACT.md`
- `browse-ui/src/lib/debug-span-flow.ts`
- `browse-ui/src/lib/__tests__/debug-span-flow.test.ts`
- `browse-ui/src/app/sessions/[id]/debug-log-flow-chart.tsx`
- `browse-ui/src/app/sessions/[id]/debug-log-tab.tsx`
- `browse-ui/src/app/sessions/[id]/__tests__/debug-log-tab.test.tsx`
- `browse-ui/e2e/smoke.spec.ts`
- `browse-ui/README.md` if user-facing behavior changes

**New-file justification:** this file is the required task-step scaffold for `tentacle-orchestration`; implementation should reuse existing backend/UI/test files unless a tentacle records an explicit scope-escalation reason.

**Reference evidence already gathered:**
- VS Code source uses `addDisposableListener(..., 'wheel', handler, { passive: false })`, `MIN_SCALE=0.1`, `MAX_SCALE=5`, `WHEEL_ZOOM_FACTOR=0.002`, `PAGE_SIZE=100`.
- VS Code source builds graph data before rendering: `buildFlowGraph` -> `filterFlowNodes` -> `sliceFlowNodes` -> merge discovery/tool calls -> `layoutFlowGraph` -> SVG render.
- VS Code renders labels, sublabels, tooltips, colored gutters, collapsible subagent subgraphs, merged-discovery toggles, keyboard focus, detail panel, pagination, and parallel subagent branches.
- Real Copilot CLI `events.jsonl` for session `33169957-0dc1-4998-86c0-d2beba02e8b4` has top-level `type`, `data`, `id`, `timestamp`, `parentId`; first 500 events include hook/tool/assistant/user/system/session/skill event types and data keys such as `toolName`, `arguments`, `result`, `toolTelemetry`, `content`, `toolRequests`, `outputTokens`, `model`, `hookType`, `input`, `name`, `path`, and `description`.

---

## Step-plan review

### Accepted steps

1. Resolve remaining confidence gaps with Opus research before implementation.
2. Treat VS Code source as the product reference, not just inspiration.
3. Fix the passive wheel error with a native non-passive wheel listener instead of relying on React `onWheel`.
4. Enrich backend-normalized CLI event metadata with redacted, allowlisted scalar summaries so UI can render detail without exposing raw content.
5. Rework the flow layout/renderer to show VS Code-style labels, sublabels, layers/subgraphs, tooltips, timestamps, and richer node summaries.
6. Add focused backend, unit, component, and Playwright proof for the original hosted symptom and richer content.
7. Loop until live/browser QA proves the hosted session is not sparse and zoom has no passive-listener console error.

### Edited steps

- User asked for agents specializing in plan/code/review/test/QA. **Edited:** split this scaffold into tentacles after research, with implementation tentacles owning code only after Opus research/planning raises confidence to `1.0`.
- User requested "clone VS Code behavior." **Edited:** do not claim full clone parity; ship the evidence-backed subset supported by Copilot CLI data and document any unsupported VS Code-only fields.

### Rejected steps

- Do not commit or upload real local `events.jsonl` content.
- Do not add broad auth checks inside `browse/routes/debug_log.py`; route-level auth remains owned by the dispatcher/server.
- Do not solve sparsity by dumping raw nested JSON into the UI; all new fields must be redacted/allowlisted and tested.

---

## Step 1: CLARIFY — Freeze acceptance criteria

**Goal:** Convert the user's complaint into measurable acceptance criteria before code changes.

**Actions:**
1. Record the original browser symptom: `Unable to preventDefault inside passive event listener invocation` appears while zooming Flow on `https://agents.linhngo.dev/sessions/33169957-0dc1-4998-86c0-d2beba02e8b4#debug-log`.
2. Define minimum "not sparse" Flow content for the CLI session:
   - every visible node shows a readable primary label and sublabel;
   - nodes expose ISO/local time or elapsed timestamp text;
   - tool nodes show tool name plus argument/result/telemetry summary when available;
   - assistant/model nodes show model/token/request metadata when available;
   - skill nodes show skill name/path/read-size summary when available;
   - hook nodes show hook type and success/error status;
   - subagent/task nodes are grouped/layered when source hierarchy supports it;
   - detail/tooltip text exposes richer redacted metadata than the three-line legacy card.
3. Define browser proof: Playwright captures page console while zooming Flow and fails on the passive-listener error.

**Done when:** acceptance criteria above are copied into tentacle prompts and no downstream step has confidence below `1.0` without a RESEARCH dependency.
**Confidence:** `1.0`.

## Step 2: RESEARCH — Resolve VS Code and Copilot CLI data gaps

**Goal:** Produce evidence-backed implementation guidance before coding.

**Actions:**
1. Dispatch an Opus research tentacle to summarize VS Code `chatDebugFlowChartView.ts`, `chatDebugFlowGraph.ts`, and `chatDebugFlowLayout.ts`: data model, labels/sublabels/tooltips, grouping, pagination, zoom/pan, passive wheel fix, keyboard/detail behavior.
2. Dispatch an Opus research tentacle to inspect local Copilot CLI `events.jsonl` shape without exposing raw user content: event type taxonomy, safe data fields, pairing keys, parent/child hierarchy, skill/subagent/task markers, tool telemetry, token metadata, and missing fields.
3. Dispatch an Opus planning tentacle to compare current Browse implementation with the two research outputs and propose scoped backend/frontend/test/QA work units.

**Done when:** research reports list facts, interpretations, actions, and verification evidence; confidence for backend metadata and frontend design is `1.0`.
**Confidence:** `<1.0` until reports complete.

## Step 3: DESIGN — Split into tentacles and issue/work-unit mapping

**Goal:** Produce scoped work units that keep code, review, test, and QA separate.

**Actions:**
1. Create or update GitHub issues/work items for:
   - backend CLI debug metadata enrichment;
   - frontend VS Code-style Flow renderer/layout;
   - passive-wheel and Flow browser QA;
   - review/security gate.
2. Create tentacles with non-overlapping scopes:
   - Backend: `browse/routes/debug_log.py`, `tests/test_browse_cli_session_debug_log.py`, `docs/DEBUG-LOG-CONTRACT.md`;
   - Frontend: `debug-log-flow-chart.tsx`, `debug-span-flow.ts`, UI unit/component tests;
   - Test/QA: Playwright smoke and hosted/local browser evidence;
   - Review/Security: code review of diff, redaction, auth invariants, no raw data leakage.
3. Include source-of-truth references and acceptance criteria in every tentacle prompt.

**Done when:** every implementation file is owned by exactly one tentacle, dependencies are explicit, and no tentacle requires guessing from this conversation.
**Confidence:** `1.0` after Step 2.

## Step 4: BUILD — Enrich backend CLI debug entries safely

**Goal:** Expose enough redacted metadata for rich Flow nodes without changing the stable response envelope.

**Actions:**
1. Extend `_build_cli_message` so messages distinguish session, user/system/assistant, tool start/complete, hook start/end, skill invocation, notification, and task completion events.
2. Extend `_extract_cli_attrs` with allowlisted scalar/summary fields only, such as `event_type`, `event_phase`, `hook_type`, `tool_call_id`, `turn_id`, `interaction_id`, `message_id`, `request_id`, `tool_result_type`, `tool_success`, `model`, `tokens_out`, `output_tokens`, `tool_request_count`, `skill_name`, `skill_path`, `skill_content_bytes`, and telemetry duration/count fields when safely scalar.
3. Preserve redaction, truncation, path/user scrubbing, pagination limit, `span_id`/`parent_span_id`, duration pairing, and open-auth loopback behavior.
4. Add backend tests for representative synthetic events covering tool arguments/result summary, assistant token metadata, skill invocation read-size summary, hook success, and no raw nested object leakage.

**Done when:** `python3 tests/test_browse_cli_session_debug_log.py` passes and added assertions prove richer attrs/messages are present and redacted.
**Confidence:** `1.0` after Step 2.

## Step 5: BUILD — Rework Flow layout and rendering

**Goal:** Replace the sparse fixed-card tree with a VS Code-inspired event flow that remains stable on large CLI sessions.

**Actions:**
1. Update `debug-span-flow.ts` to derive render-node label, sublabel, timestamp label, tooltip lines, category/layer, error state, merged-count or grouped metadata from `BrowseDebugEntry`.
2. Add layout support for richer variable-height cards and subgraph/layer rectangles for agent/task/subagent groupings when hierarchy supports it.
3. Update `debug-log-flow-chart.tsx` to render:
   - readable primary labels and sublabels;
   - timestamp/elapsed rows;
   - colored kind gutters;
   - tooltips/detail-safe metadata;
   - subgraph/layer containers or clearly labeled grouping;
   - Show More/pagination hint when `has_more` exists in the tab state;
   - accessible keyboard/click selection.
4. Replace React `onWheel` cancellation with a `ref` + native `addEventListener("wheel", handler, { passive: false })`, zooming around pointer position and clamping to VS Code-like min/max.
5. Preserve existing List/Tree/Flow toggle behavior and DetailDrawer selection.

**Done when:** frontend unit/component tests prove rich labels/sublabels/tooltips render, flow selection still opens details, and wheel zoom does not rely on React passive handling.
**Confidence:** `1.0` after Step 2.

## Step 6: TEST — Run local gates

**Goal:** Prove backend and Browse UI changes do not regress existing behavior.

**Actions:**
1. Parse modified Python: `python3 -c "import ast; ast.parse(open('browse/routes/debug_log.py', encoding='utf-8').read())"`.
2. Run backend focused test: `python3 tests/test_browse_cli_session_debug_log.py`.
3. Run required Python suites: `python3 test_security.py && python3 test_fixes.py`.
4. Run UI gates: `cd browse-ui && pnpm typecheck && pnpm lint && pnpm lint:all && pnpm exec prettier --check e2e/smoke.spec.ts && pnpm format:check`.
5. Run UI test/build gates: `cd browse-ui && pnpm test && pnpm build`.

**Done when:** all commands exit 0 or a baseline failure is proven on the pre-change commit and documented separately.
**Confidence:** `1.0`.

## Step 7: REVIEW — Security and correctness review

**Goal:** Catch logic, redaction, auth, and UX regressions before deploy.

**Actions:**
1. Dispatch a code-review agent on the full diff with focus on redaction leaks, huge-event memory usage, auth invariant, passive listener cleanup, stale refs, and large SVG performance.
2. Dispatch a browser-security reviewer if backend response fields or hosted/local auth flow changed.
3. Address every genuine bug finding; reject style-only comments.

**Done when:** review reports contain no unresolved high/medium correctness or security findings.
**Confidence:** `1.0`.

## Step 8: QA — Browser proof on local and hosted surfaces

**Goal:** Prove the user's hosted URL shows rich Flow data and zooms without the passive-listener error.

**Actions:**
1. Start or reuse the local Browse backend serving the current session-state.
2. Run Playwright against `https://agents.linhngo.dev/sessions/33169957-0dc1-4998-86c0-d2beba02e8b4#debug-log` with local host profile/backend.
3. Capture console messages while selecting Flow and performing large wheel zoom.
4. Assert:
   - no console message contains `Unable to preventDefault inside passive event listener`;
   - Flow summary has nonzero nodes/edges;
   - visible SVG/text includes tool, hook, assistant/model, skill, timestamp, and duration/token/metadata where present;
   - selecting a node opens richer detail.

**Done when:** QA artifact contains command output and browser assertions for the exact hosted session URL.
**Confidence:** `1.0`.

## Step 9: LOOP-EVAL — Compare against goal and iterate if still sparse

**Goal:** Avoid another premature closeout.

**Actions:**
1. Compare the live Flow against the Step 1 criteria.
2. If any criterion is unmet, create follow-up tentacles scoped to the missing gap and return to BUILD/TEST/REVIEW/QA.
3. Record the lesson with `python3 learn.py --mistake "Browse debug Flow clone was shipped before studying VS Code source and raw CLI event data; future reference-based UX must research source/data model, define acceptance criteria, and prove the original browser symptom before closeout."`.

**Done when:** every criterion is met or explicitly documented as unsupported by source data with a concrete follow-up issue.
**Confidence:** `1.0`.

## Step 10: COMMIT — Package and deploy

**Goal:** Ship only after local and hosted proof.

**Actions:**
1. Run `git diff --stat` and confirm only expected files changed.
2. Commit with the required co-authored trailer.
3. Push to `main`.
4. Deploy Browse UI if hosted assets changed.
5. Verify hosted `version.json` points at the shipped commit and rerun the Step 8 smoke proof.

**Done when:** commit is on `origin/main`, hosted assets are deployed when needed, and final QA evidence references the shipped commit.
**Confidence:** `1.0`.

---

## Phase Gates

| Phase | Artifact | Status |
|---|---|---|
| CLARIFY | Acceptance criteria for passive wheel and rich Flow content | ☐ |
| RESEARCH | Opus reports for VS Code source, CLI event shape, gap audit | ☐ |
| DESIGN | Scoped tentacles/issues with dependencies | ☐ |
| BUILD backend | Rich redacted CLI debug attrs/messages with tests | ☐ |
| BUILD frontend | VS Code-inspired Flow renderer/layout and non-passive wheel listener | ☐ |
| TEST | Python + Browse UI gates pass | ☐ |
| REVIEW | Security/code review findings resolved | ☐ |
| QA | Browser proof on hosted session URL | ☐ |
| LOOP-EVAL | Goal criteria met or follow-up loop created | ☐ |
| COMMIT | Commit pushed and hosted deploy verified | ☐ |
