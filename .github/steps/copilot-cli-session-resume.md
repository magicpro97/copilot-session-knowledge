# STEPS: Adopt and resume Copilot CLI sessions from Browse Chat

**Task:** Let the browse app adopt an existing GitHub Copilot CLI session into `/chat` and send follow-up prompts with `copilot -p ... --resume=<cli-session-id>`.
**Scope:** `browse/core/`, `browse/api/`, `tests/`, `browse-ui/src/`, `docs/`
**Estimated phases:** CLARIFY -> RESEARCH -> DESIGN/WBS -> BUILD -> TEST -> REVIEW -> LOOP-EVAL -> COMMIT/CLOSE

## Step-plan review

- **Source:** user request plus Opus research agents `copilot-resume-plan`, `copilot-resume-security`, `copilot-resume-validation2`, `copilot-resume-wbs-audit`.
- **Accepted steps:** two-ID model, strict UUID4 validation, read-only CLI session discovery, adopt API, argv resume-target update, UI picker/confirmation, integration smoke, docs/security QA.
- **Edited steps:** planner's original "reuse CLI UUID as operator session id" was rejected; security review requires a distinct operator session id plus `resume_target=<cli_uuid>`.
- **Rejected steps:** auto-adopt all CLI sessions, mutate `~/.copilot/session-state/<uuid>`, use `--session-id`, resume by session name, put prompt/resume target in query strings, grow `_ENV_ALLOWLIST`, or auto-fire a prompt on import.
- **Dependency order:** foundation validators/state -> discovery/adopt/argv -> UI -> integration smoke -> docs/security QA.
- **Decision confidence:** `1.0` for issue creation after B0 was resolved locally: real CLI sessions are UUID directories under `~/.copilot/session-state/<uuid>/workspace.yaml`; operator-console state is isolated under `~/.copilot/session-state/operator-console/`.

## Step 1: CLARIFY/RESEARCH — Lock the architecture

**Goal:** Establish an implementation-ready contract before coding.

**Actions:**
1. Use Opus research to validate Copilot CLI `--resume=<uuid>` vs `--session-id`.
2. Use Opus security review to define the safe data model and API constraints.
3. Verify the local session-state layout with:
   `python3 - <<'PY' ... Path.home()/".copilot"/"session-state" ... PY`

**Done when:** The plan uses a two-ID model and no decision remains below confidence `1.0`.

## Step 2: DESIGN/WBS — Create GitHub issues

**Goal:** Produce non-overlapping issues suitable for specialist agents.

**Actions:**
1. Create an epic issue for the feature.
2. Create child issues for:
   - backend identity/state foundation,
   - read-only CLI discovery,
   - adopt/confirm API plus argv builder,
   - frontend picker/confirmation UX,
   - integration smoke and docs/security QA.
3. Link dependencies and acceptance criteria in each issue.

**Done when:** GitHub issues exist with clear scopes, dependencies, agent routing, and evidence commands.

## Step 3: BUILD — Backend foundation

**Goal:** Add safe primitives without changing UI behavior.

**Actions:**
1. Implement strict CLI UUID validation.
2. Add additive operator-session fields: `resume_target`, `confirmed_at`, provenance/source fields as needed.
3. Update `_build_copilot_argv` to use only validated `resume_target` for `--resume`; never use display name as an identifier.

**Done when:** Backend unit tests prove valid/invalid UUID behavior, default session loading, and argv output without invoking the real Copilot CLI.

## Step 4: BUILD — Backend discovery/adopt APIs

**Goal:** Expose authenticated APIs to list and adopt CLI sessions safely.

**Actions:**
1. Add read-only discovery over `~/.copilot/session-state/<uuid>/workspace.yaml`; reject escaping symlinks and do not mutate the tree.
2. Add `POST /api/operator/sessions/adopt` and `POST /api/operator/sessions/{id}/confirm`.
3. Re-confine workspace/add_dirs and block prompt submission until confirmation succeeds.

**Done when:** API tests cover happy paths, path/UUID rejection, delete isolation, duplicate adoption semantics, auth, and concurrency.

## Step 5: BUILD — Frontend adoption UX

**Goal:** Let users adopt CLI sessions from Browse without exposing unsafe controls.

**Actions:**
1. Add typed API hooks/schemas for discovery, adopt, and confirm.
2. Add a `/chat` "From CLI history" picker.
3. Add a confirmation panel showing resolved workspace/add_dirs; disable composer until confirmed.
4. Add `/sessions/<id>` CTA when the knowledge session can be adopted.

**Done when:** UI tests prove CTA/picker visibility, no URL contains `resume_target`, and composer remains disabled until confirmation.

## Step 6: TEST — Integration and runtime smoke

**Goal:** Prove the feature works locally without spending Copilot requests.

**Actions:**
1. Add a mock Copilot executable and integration test for discovery -> adopt -> confirm -> prompt -> stream.
2. Assert the real CLI session tree is byte-identical before/after.
3. Assert subprocess env is still allowlisted and argv contains exactly one `--resume=<cli_uuid>`.

**Done when:** Integration test passes and full Python + browse-ui gates are green.

## Step 7: REVIEW/QA — Multi-layer verification

**Goal:** Catch security, contract, and whole-app synchronization defects.

**Actions:**
1. Run code-review agent on backend/frontend diffs.
2. Run browser-security-reviewer on two-ID separation, UUID validation, path confinement, no query-string secrets, no env allowlist growth.
3. Run whole-app-impact-auditor for docs/hooks/skills/CI surfaces.
4. Run verification-gate for all affected tests/build/lint.

**Done when:** Review verdicts are clean and every gate has command output evidence.

## Step 8: LOOP-EVAL — Goal check

**Goal:** Decide whether to iterate or ship.

**Actions:**
1. Run the success criteria command bundle:
   `python3 test_security.py && python3 test_fixes.py && cd browse-ui && pnpm typecheck && pnpm lint && pnpm format:check && pnpm test && pnpm build`
2. Run local browser smoke against a browse backend using the mock Copilot executable.
3. Verify all child issues' acceptance criteria are met.

**Done when:** Browse app can adopt a Copilot CLI session, confirm it, send a prompt through `/chat`, and prove the backend would invoke `copilot -p ... --resume=<cli_uuid>`.

## Step 9: COMMIT/CLOSE — Ship with evidence

**Actions:**
1. Commit only after gates pass.
2. Push to `main`, monitor CI.
3. Comment evidence on child issues and epic, then close.

**Done when:** `main` has the verified commits, CI is green, and all issues are closed with evidence.

## Phase Gates

| Phase | Artifact | Status |
|-------|----------|--------|
| CLARIFY/RESEARCH | Opus research + local B0 path evidence | ☑ |
| DESIGN/WBS | GitHub issues with dependencies and evidence commands | ☐ |
| BUILD | Backend/frontend implementation by scoped agents | ☐ |
| TEST | Python + browse-ui + integration smoke output | ☐ |
| REVIEW/QA | Code review, security review, whole-app QA clean | ☐ |
| LOOP-EVAL | Success criteria command bundle and runtime smoke pass | ☐ |
| COMMIT/CLOSE | Commit, CI, issue evidence | ☐ |
