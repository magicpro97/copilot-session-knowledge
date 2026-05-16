# STEPS: Issue #213 Brewcode Trend Scout Follow-up

**Task:** Triage and implement the first actionable open-board fallback item after GitHub Projects access failed with missing `read:project` scope.
**Scope:** `mcp-server.py`, `tests/**/*mcp*`, `docs/**/*MCP*`, issue comments via `gh issue comment`.
**Estimated phases:** CLARIFY -> DESIGN -> BUILD -> TEST -> REVIEW -> LOOP-EVAL -> COMMIT/CLOSE

## Board Access Note

Project board access was attempted with `gh project list --owner magicpro97 --format json --limit 20` and failed because the token is missing `read:project`. As an autonomous fallback, use open repository issues as the todo source until project scope is refreshed.

Issue selection review:

- #71 is the only `priority:high` open issue, but it is already implemented and explicitly blocked on manual FPT-network verification by the maintainer.
- #213 is the most recently updated open issue and has an actionable Trend Scout learning related to MCP/tool-server surface and hooks/workflow patterns.
- This step file scopes the first implementation wave to the MCP/tool-server part of #213 only. Other #213 learnings (Claude JSONL parser, install UX, hook workflow patterns) require separate issue/tentacle review.

## Step-plan review

- Source step file: `.github/steps/issue-213-brewcode-trend-scout.md`
- Accepted steps: 1, 2, 3, 4, 5, 6
- Edited steps: Issue selection narrowed to #213 MCP/tool-server learning because #71 is blocked on external manual verification.
- Rejected steps: "drain every issue in one pass" is too broad; goal loop should process one verified issue/tentacle wave at a time.
- Dependency order: clarify current MCP state -> design delta -> implement scoped delta -> run tests -> review -> evaluate whether #213 MCP learning is satisfied.
- Evidence contract: command output from `gh issue view`, source inspection notes, test output, code-review result, and any issue comment/close action.

---

## Step 1: CLARIFY — Confirm #213 actionable slice

**Goal:** Determine whether #213 requires code changes, documentation updates, an issue comment, or closure.

**Actions:**
1. `gh issue view 213 --json number,title,body,labels,comments,state,url` — capture the trend-scout learning.
2. Read `mcp-server.py`, existing MCP tests, and MCP docs to verify current tool surface.
3. Search for existing `briefing` and `query_session` MCP tools and any write/latency gaps.

**Done when:** The issue's MCP/tool-server gap is classified as one of: already satisfied, docs-only, test-only, or code change required.

---

## Step 2: DESIGN — Define minimal MCP follow-up

**Goal:** Produce a small implementation design that respects CSK constraints.

**Actions:**
1. If current MCP already exposes `briefing` and `query_session`, define the evidence and issue comment needed to close or narrow #213.
2. If a gap remains, define the exact tool/schema/test delta.
3. Confirm no external dependencies are needed and JSON-RPC envelopes remain backward-compatible.

**Done when:** The tentacle context lists the exact files to edit and exact tests to run.

---

## Step 3: BUILD — Implement the scoped MCP delta

**Goal:** Apply only the minimal code/docs change required by Step 2.

**Actions:**
1. Edit only files in the tentacle scope.
2. Preserve stable JSON envelopes and parameterized SQL conventions.
3. Add or update tests for the MCP behavior if code changes.

**Done when:** Modified Python files parse with `ast.parse` and the intended MCP behavior is covered by tests or documented as already satisfied.

---

## Step 4: TEST — Run relevant verification

**Goal:** Prove the scoped change does not regress CSK.

**Actions:**
1. Run targeted MCP tests if present.
2. Run `python3 test_fixes.py`.
3. If Python security-sensitive files changed, run `python3 test_security.py`.

**Done when:** Relevant commands complete and their output is recorded in the tentacle handoff/evidence.

---

## Step 5: REVIEW — Code and issue closeout review

**Goal:** Check correctness, security, scope creep, and issue evidence.

**Actions:**
1. Dispatch a `code-review` agent if code changed.
2. Confirm issue #213 acceptance is backed by evidence, not prose.
3. Confirm #71 remains open due external FPT verification blocker.

**Done when:** Review has no blocking findings and issue close/comment text includes evidence.

---

## Step 6: LOOP-EVAL — Decide whether to continue to next board item

**Goal:** Evaluate the overarching goal: process open-board fallback issues until none are actionable.

**Actions:**
1. Re-list open issues with `gh issue list --state open --limit 40 --json number,title,labels,url,updatedAt`.
2. Mark blocked issues with the blocker reason in the orchestrator notes.
3. Select the next actionable issue or stop if only external/manual blockers remain.

**Done when:** The goal evaluation records either "continue with next issue" or "blocked: project board scope/manual verification needed".

---

## Phase Gates

| Phase | Artifact | Status |
|-------|---------|--------|
| CLARIFY | `gh issue view 213` plus source inspection notes | ☐ |
| DESIGN | Minimal MCP delta or already-satisfied closeout plan | ☐ |
| BUILD | Scoped files changed or no-code closeout prepared | ☐ |
| TEST | Targeted tests + `python3 test_fixes.py` output | ☐ |
| REVIEW | Code-review/no-scope-creep verdict | ☐ |
| LOOP-EVAL | Next issue selected or blockers documented | ☐ |
| COMMIT/CLOSE | Commit/issue comment/close with evidence, clean worktree | ☐ |
