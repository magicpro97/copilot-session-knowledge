# STEPS: Issue #265 pre-commit complexity advisory

**Task:** Add a fail-open pre-commit complexity advisory for staged Python files.
**Scope:** `hooks/pre-commit`, `scripts/check_complexity.py`, `docs/HOOKS.md`, `CONTRIBUTING.md`, `tests/test_quality_gates.py`, `.github/steps/issue-265-precommit-complexity.md`.

## Step 1: CLARIFY - Confirm advisory behavior

**Goal:** Confirm complexity reporting is non-blocking and scoped to staged Python files.

**Actions:**
1. Read issue #265 and confirm the required outputs: pre-commit integration, docs updates, and quality-gate tests.
2. Inspect `hooks/pre-commit` fail-open check patterns.
3. Inspect `scripts/check_complexity.py` JSON contract from #257.

**Done when:** The hook can run the reporter without blocking on findings or reporter failures.

## Step 2: BUILD - Add pre-commit advisory

**Goal:** Run the complexity reporter on staged `.py` files and print warnings only when needed.

**Actions:**
1. Add `COMPLEXITY_CHECKER = TOOLS_DIR / "scripts" / "check_complexity.py"`.
2. Add `check_complexity(staged)` that runs `check_complexity.py --json` on staged Python files.
3. Parse JSON and print non-blocking advisory lines for `warning`/`high` file or function metrics.
4. Return `0` for missing reporter, reporter exceptions, malformed JSON, and findings.
5. Wire the check into `main()` after Ruff and before Prettier.

**Done when:** Complex staged Python files print an advisory and the hook still exits 0.

## Step 3: TEST - Cover acceptance criteria

**Goal:** Prove the hook warns, stays quiet, and fails open.

**Actions:**
1. Add tests in `tests/test_quality_gates.py` that create an isolated git repo and isolated `HOME`.
2. Stage a complex Python file and assert the hook exits 0 while printing a complexity advisory.
3. Stage a small Python file and assert no complexity advisory is printed.
4. Run with `scripts/check_complexity.py` absent and assert the hook exits 0.
5. Parse `hooks/pre-commit` with `ast.parse`.

**Done when:** `python tests/test_quality_gates.py` passes with the new pre-commit complexity assertions.

## Step 4: DOCS - Document local behavior

**Goal:** Make the non-blocking advisory discoverable.

**Actions:**
1. Update `docs/HOOKS.md` pre-commit row and Local vs CI section.
2. Update `CONTRIBUTING.md` local enforcement bullets and complexity reporter paragraph.

**Done when:** Docs say the staged complexity check is fail-open and non-blocking.

## Step 5: REVIEW - Verify repository gates

**Goal:** Confirm no regression in hook quality gates.

**Actions:**
1. Run `python -m py_compile hooks/pre-commit tests/test_quality_gates.py`.
2. Run `python tests/test_quality_gates.py`.
3. Run `python test_fixes.py`.
4. Run `python run_all_tests.py`.
5. Run `ruff check hooks/pre-commit tests/test_quality_gates.py`.
6. Run `ruff format --check hooks/pre-commit tests/test_quality_gates.py`.
7. Run `git diff --check`.
8. Run a code-review agent on the diff.

**Done when:** Local gate output is recorded and review returns CLEAN or all findings are addressed.

## Step 6: COMMIT - Package and ship

**Goal:** Merge #265 through the isolated lane and clean local state.

**Actions:**
1. Commit only expected files with the required co-author trailer.
2. Open a PR with `Closes #265` and mirror labels.
3. Wait for CI, merge, move #265 project item to Done, and delete branch/worktree/tentacle.

**Done when:** PR is merged, issue #265 is closed, project item is Done, and the isolated lane is removed.

## Phase Gates

| Phase | Artifact | Status |
|-------|----------|--------|
| CLARIFY | Fail-open staged advisory confirmed | [ ] |
| BUILD | Pre-commit complexity advisory added | [ ] |
| TEST | `tests/test_quality_gates.py` passes | [ ] |
| DOCS | Hook and contributor docs updated | [ ] |
| REVIEW | Local gates and code review complete | [ ] |
| COMMIT | PR merged and lane cleaned | [ ] |
