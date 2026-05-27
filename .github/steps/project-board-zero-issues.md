# STEPS: Project board zero open issues

**Task:** Drive `magicpro97/copilot-session-knowledge` open issues to zero, ordered by the `SK Research Enhancements` project board and issue priority labels.
**Scope:** GitHub issues/project board, `.github/steps/project-board-zero-issues.md`, `.octogent/tentacles/**`, then implementation scopes determined per issue wave.
**Estimated phases:** CLARIFY -> RESEARCH -> DESIGN -> VERIFY -> BUILD -> TEST -> REVIEW -> LOOP-EVAL -> COMMIT/CLOSE

## Step 1: CLARIFY — Establish the authoritative backlog and priority order

**Goal:** Produce a reproducible list of all open issues and the execution order.

**Actions:**
1. `gh project item-list 1 --owner magicpro97 --format json --limit 400` — capture the SK Research Enhancements board items, `Status`, `Priority`, `Source`, labels, and board order.
2. `gh issue list --repo magicpro97/copilot-session-knowledge --state open --limit 200 --json number,title,labels,projectItems,url` — capture all repo open issues, including issues not on the project board.
3. Sort execution as: project-board `Todo` items first, explicit board `Priority` if present, otherwise `priority:high` labels before `priority:medium`, P3/low/proposals after, and non-project issues last.

**Done when:** The orchestrator has an issue list whose count matches `gh issue list`, plus a documented order and the evidence command output.
**Confidence:** `1.0` required. If board metadata is incomplete, dispatch Opus research to synthesize ordering from labels, board order, issue bodies, and dependencies.

## Step 2: RESEARCH — Resolve decomposition and dependency confidence

**Goal:** Reach confidence `1.0` before any implementation, closure, or commit decision.

**Actions:**
1. Dispatch independent Opus research agents for:
   - project-board priority/dependency audit,
   - high-priority durability/retry/retrieval/MCP issues,
   - medium-priority learning/taxonomy/automation issues,
   - Browse/connectivity/Rust/proposal legacy issues,
   - trend-scout and non-project issue triage.
2. Require each research handoff to state facts, interpretations, rejected alternatives, dependencies, and confidence.
3. If any agent reports confidence `<1.0`, split the uncertain area into smaller research questions and dispatch again.

**Done when:** The synthesized routing/decomposition decision is `confidence=1.0`; otherwise implementation is blocked.
**Confidence:** `1.0` required.

## Step 3: DESIGN — Convert researched issues into non-overlapping waves

**Goal:** Create tentacles with atomic scopes and no file overlap.

**Actions:**
1. Group issues by dependency and file surface, not merely by issue number.
2. Create one foundation/research tentacle for broad epics that cannot be closed by code alone.
3. Create implementation tentacles only for issues with concrete acceptance criteria and a known verification surface.
4. Record in each tentacle `CONTEXT.md`: source issue(s), accepted/edited/rejected step numbers, dependencies, evidence contract, and Opus confidence source.

**Done when:** Every open issue is assigned to a research, implementation, review, or closeout tentacle with non-overlapping scope and an evidence owner.
**Confidence:** `1.0` required.

## Step 4: VERIFY — Opus decomposition review before dispatch

**Goal:** Prevent wrong parallelism and premature implementation.

**Actions:**
1. Dispatch an independent Opus review agent over the step file, issue ordering, and proposed tentacles.
2. Require verdict `APPROVED confidence=1.0`.
3. If review is not clean, edit the plan and repeat research/review.

**Done when:** Opus decomposition review returns `APPROVED confidence=1.0`.
**Confidence:** `1.0` required.

## Step 5: BUILD — Execute issue waves through Opus tentacles only

**Goal:** Implement or resolve each assigned issue through scoped Opus agents.

**Actions:**
1. Dispatch implementation/fix tentacles with `claude-opus-4.7` or the strongest available Opus model.
2. Each implementation tentacle must run a strict TDD loop: RED evidence first, minimal BUILD, GREEN evidence.
3. Sub-agents must not commit, push, close issues, or expand scope silently.

**Done when:** Each wave handoff is `DONE` with changed files, commands run, and evidence for its issue acceptance criteria.
**Confidence:** `1.0` required before accepting a wave.

## Step 6: TEST — Main-session verification gates

**Goal:** Hold concrete proof before reviews or closeout.

**Actions:**
1. Run surface-specific gates after each wave:
   - Python: `python3 test_security.py` and `python3 test_fixes.py`, plus focused tests.
   - Hooks/docs/skills: `python3 tests/test_quality_gates.py`.
   - Browse UI: `cd browse-ui && pnpm typecheck && pnpm lint && pnpm format:check && pnpm test && pnpm build`.
   - Rust: `cargo fmt --all -- --check && cargo clippy -- -D warnings && cargo test`.
   - Remote terminal: `npm test && npm run lint && npm run lint:clean`.
2. Record exact pass/fail counts and command output.

**Done when:** All relevant gates pass in the main session.
**Confidence:** `1.0` required.

## Step 7: REVIEW — Independent Opus code/security review

**Goal:** Catch correctness, contract, security, and scope errors before commit.

**Actions:**
1. Dispatch Opus code review for every wave.
2. Dispatch Opus security review for auth, file IO, DB, network, hooks, redaction, MCP, and remote-connectivity changes.
3. If any review reports findings or confidence `<1.0`, dispatch Opus fix tentacles, rerun gates, and re-review.

**Done when:** All wave reviews return `CLEAN confidence=1.0`.
**Confidence:** `1.0` required.

## Step 8: LOOP-EVAL — Recompute open issue count and board status

**Goal:** Continue until the repo has zero open issues.

**Actions:**
1. `gh issue list --repo magicpro97/copilot-session-knowledge --state open --limit 200 --json number,title,labels,projectItems,url` — recompute open count.
2. `gh project item-list 1 --owner magicpro97 --format json --limit 400` — recompute project board Todo/In Progress items.
3. If any issue remains open, return to Step 1 for the remaining set.

**Done when:** Open issue count is `0` and project-board Todo/In Progress issue count is `0`.
**Confidence:** `1.0` required.

## Step 9: COMMIT/CLOSE — Orchestrator-only closeout

**Goal:** Preserve verified work and close issues with evidence.

**Actions:**
1. Commit each verified wave from the orchestrator session with the required Co-authored-by trailer.
2. Push `main` after rebasing on `origin/main` and rerunning verification.
3. Close each issue only with per-acceptance evidence, commit hash, gate output, and Opus review verdict.

**Done when:** `git status -sb` is clean, pushed `main` contains all commits, `gh issue list --state open` returns `0`, and session learning is recorded.
**Confidence:** `1.0` required.

## Phase Gates

| Phase | Artifact | Status |
|-------|----------|--------|
| CLARIFY | Open issue + project-board command output captured | ☐ |
| RESEARCH | Opus research confidence `1.0` for order/decomposition | ☐ |
| DESIGN | Non-overlapping tentacle map for every issue | ☐ |
| VERIFY | Opus decomposition review `APPROVED confidence=1.0` | ☐ |
| BUILD | Opus tentacle handoffs `DONE` with evidence | ☐ |
| TEST | Main-session verification gates pass | ☐ |
| REVIEW | Opus code/security reviews `CLEAN confidence=1.0` | ☐ |
| LOOP-EVAL | Repo open issues count is `0` | ☐ |
| COMMIT/CLOSE | Clean pushed main and evidence-backed issue closeout | ☐ |
