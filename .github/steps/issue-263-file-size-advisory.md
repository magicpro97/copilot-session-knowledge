# STEPS: Issue #263 file-size advisory hook rule

**Task:** Add a fail-open advisory hook rule that warns when Python edits create oversized files.
**Scope:** `hooks/rules/file_size_advisory.py`, `hooks/rules/__init__.py`, `docs/HOOKS.md`, `tests/test_quality_gates.py`, `.github/steps/issue-263-file-size-advisory.md`.

## Step 1: CLARIFY - Confirm hook behavior

**Goal:** Confirm the rule is advisory-only and does not block tool execution.

**Actions:**
1. Read issue #263 and confirm the required outputs: new rule module, registry entry, docs row, and unit tests.
2. Inspect `hooks/rules/__init__.py`, `hooks/rules/common.py`, `tests/test_quality_gates.py`, and `docs/HOOKS.md`.
3. Confirm dependency #283 is not needed for this narrow rule because the registry, docs contract, and quality-gate test file already exist on main.

**Done when:** The rule threshold, event/tool scope, and fail-open result shape are known.

## Step 2: BUILD - Add the advisory rule

**Goal:** Implement a hook rule that computes the proposed Python file line count for create/edit payloads.

**Actions:**
1. Add `hooks/rules/file_size_advisory.py`.
2. For `create`, read `toolArgs.file_text`.
3. For `edit`, read the current file and apply the single `old_str` -> `new_str` replacement in memory.
4. Return `info(...)` when the resulting `.py` file exceeds 600 lines; return `None` otherwise.

**Done when:** The rule returns advisory info for oversized `.py` create/edit payloads and never returns `permissionDecision: deny`.

## Step 3: BUILD - Register and document

**Goal:** Make the rule active in the unified runner and visible in hook docs.

**Actions:**
1. Register `FileSizeAdvisoryRule()` in `hooks/rules/__init__.py`.
2. Add a `file-size-advisory` row to the `docs/HOOKS.md` rules table.

**Done when:** `_registered_hook_rule_names()` includes `file-size-advisory`, and docs contain the matching table row.

## Step 4: TEST - Cover acceptance criteria

**Goal:** Prove the advisory rule behaves correctly and remains fail-open.

**Actions:**
1. Add tests in `tests/test_quality_gates.py` for import, registry, 700-line create/edit advisory, 250-line no-op, non-Python no-op, and docs coverage.
2. Run `python tests/test_quality_gates.py`.

**Done when:** The quality-gate test runner passes with the new file-size advisory assertions.

## Step 5: REVIEW - Verify repository gates

**Goal:** Confirm the change does not regress existing Python hook behavior.

**Actions:**
1. Run `python -m py_compile hooks/rules/file_size_advisory.py hooks/rules/__init__.py tests/test_quality_gates.py`.
2. Run `python tests/test_quality_gates.py`.
3. Run `python test_fixes.py`.
4. Run `python run_all_tests.py`.
5. Run `git diff --check`.
6. Run a code-review agent on the diff.

**Done when:** All commands exit 0 and review returns CLEAN.

## Step 6: COMMIT - Package and ship

**Goal:** Merge #263 through the isolated lane and clean local state.

**Actions:**
1. Commit only expected files with the required co-author trailer.
2. Open a PR with `Closes #263` and mirror labels.
3. Wait for CI, merge, move #263 project item to Done, and delete branch/worktree/tentacle.

**Done when:** PR is merged, issue #263 is closed, project item is Done, and the isolated lane is removed.

## Phase Gates

| Phase | Artifact | Status |
|-------|----------|--------|
| CLARIFY | Advisory-only behavior confirmed | [ ] |
| BUILD | `FileSizeAdvisoryRule` added | [ ] |
| BUILD | Rule registered and documented | [ ] |
| TEST | `tests/test_quality_gates.py` passes | [ ] |
| REVIEW | Local gates and code review complete | [ ] |
| COMMIT | PR merged and lane cleaned | [ ] |
