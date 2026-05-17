# STEPS: Issue #264 new-file advisory hook rule

**Task:** Add a fail-open advisory hook rule that warns when an agent creates a new root Python script.
**Scope:** `hooks/rules/new_file_advisory.py`, `hooks/rules/__init__.py`, `docs/HOOKS.md`, `tests/test_quality_gates.py`, `.github/steps/issue-264-new-file-advisory.md`.

## Step 1: CLARIFY - Confirm advisory behavior

**Goal:** Confirm the rule stays narrow and advisory-only.

**Actions:**
1. Read issue #264 and confirm the required outputs: new rule module, registry entry, docs row, and unit tests.
2. Inspect the #263 `file-size-advisory` pattern and shared hook helpers.
3. Confirm Rule 11 text is not yet present on `main`; keep this lane scoped to the runtime nudge and do not implement #254 here.

**Done when:** The event/tool scope, path scope, and fail-open result shape are known.

## Step 2: BUILD - Add the advisory rule

**Goal:** Implement a hook rule that detects root-level Python `create` payloads.

**Actions:**
1. Add `hooks/rules/new_file_advisory.py`.
2. Return `info(...)` only for `toolName=create` targeting `*.py` directly under the repository root.
3. Return `None` for nested files such as `browse/core/new_module.py`, edit events, non-Python paths, and malformed payloads.

**Done when:** Root Python creates emit advisory info and the rule never returns `permissionDecision: deny`.

## Step 3: BUILD - Register and document

**Goal:** Make the rule active in the unified runner and visible in hook docs.

**Actions:**
1. Register `NewFileAdvisoryRule()` in `hooks/rules/__init__.py`.
2. Add a `new-file-advisory` row to the `docs/HOOKS.md` rules table.

**Done when:** `_registered_hook_rule_names()` includes `new-file-advisory`, and docs contain the matching table row.

## Step 4: TEST - Cover acceptance criteria

**Goal:** Prove the advisory rule behaves correctly and remains fail-open.

**Actions:**
1. Add tests in `tests/test_quality_gates.py` for import, registry, root create advisory, nested create no-op, edit no-op, Rule 11 message citation, and docs coverage.
2. Run `python tests/test_quality_gates.py`.

**Done when:** The quality-gate test runner passes with the new new-file advisory assertions.

## Step 5: REVIEW - Verify repository gates

**Goal:** Confirm the change does not regress existing Python hook behavior.

**Actions:**
1. Run `python -m py_compile hooks/rules/new_file_advisory.py hooks/rules/__init__.py tests/test_quality_gates.py`.
2. Run `python tests/test_quality_gates.py`.
3. Run `python test_fixes.py`.
4. Run `python run_all_tests.py`.
5. Run `git diff --check`.
6. Run a code-review agent on the diff.

**Done when:** Local gate output is recorded and review returns CLEAN or all findings are addressed.

## Step 6: COMMIT - Package and ship

**Goal:** Merge #264 through the isolated lane and clean local state.

**Actions:**
1. Commit only expected files with the required co-author trailer.
2. Open a PR with `Closes #264` and mirror labels.
3. Wait for CI, merge, move #264 project item to Done, and delete branch/worktree/tentacle.

**Done when:** PR is merged, issue #264 is closed, project item is Done, and the isolated lane is removed.

## Phase Gates

| Phase | Artifact | Status |
|-------|----------|--------|
| CLARIFY | Advisory-only behavior confirmed | [ ] |
| BUILD | `NewFileAdvisoryRule` added | [ ] |
| BUILD | Rule registered and documented | [ ] |
| TEST | `tests/test_quality_gates.py` passes | [ ] |
| REVIEW | Local gates and code review complete | [ ] |
| COMMIT | PR merged and lane cleaned | [ ] |
