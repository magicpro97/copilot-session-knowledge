# STEPS: Issue #257 check_complexity reporter

**Task:** Add a stdlib-only AST complexity reporter with text and JSON output.
**Scope:** `scripts/check_complexity.py`, `CONTRIBUTING.md`, `tests/test_quality_gates.py`, `.github/steps/issue-257-check-complexity.md`.

## Step 1: CLARIFY - Confirm metrics and surfaces

**Goal:** Confirm the reporter is measurement-only and dependency-free.

**Actions:**
1. Read issue #257 and confirm thresholds: function complexity warning >15, high >25; function lines warning >50, high >100; file lines warning >400, high >800.
2. Inspect `scripts/check_syntax.py`, `tests/test_quality_gates.py`, and `CONTRIBUTING.md` for existing quality-gate patterns.
3. Confirm default scan surfaces: root `.py`, `browse/`, `hooks/`, and `scripts/`.

**Done when:** Metric thresholds, output modes, and test/doc surfaces are known.

## Step 2: BUILD - Add the reporter

**Goal:** Implement `scripts/check_complexity.py` as a standalone Python script.

**Actions:**
1. Add a Windows UTF-8 block and use only stdlib imports.
2. Discover default/targeted Python files while skipping `.git`, virtualenvs, cache directories, and fixtures.
3. Parse files with `ast`, collect file line counts, function line counts, and approximate cyclomatic complexity.
4. Emit text by default and JSON with `--json`.
5. Exit non-zero only for missing paths or parse/read errors.

**Done when:** Targeted text and JSON reports are produced without non-stdlib dependencies.

## Step 3: TEST - Cover acceptance criteria

**Goal:** Prove the reporter works and stays dependency-free.

**Actions:**
1. Add tests in `tests/test_quality_gates.py` for `tentacle.py` text output, `--json browse/`, self-check, invalid path handling, stdlib import scan, and py_compile.
2. Run `python tests/test_quality_gates.py`.

**Done when:** The quality-gate test runner passes with the new complexity reporter assertions.

## Step 4: DOCS - Document contributor usage

**Goal:** Make the advisory reporter discoverable for contributors.

**Actions:**
1. Add `python3 scripts/check_complexity.py` to the manual pre-PR command list.
2. Document default surfaces, targeted path usage, and `--json`.

**Done when:** `CONTRIBUTING.md` explains what the reporter measures and how to run it.

## Step 5: REVIEW - Verify repository gates

**Goal:** Confirm no regression in existing quality gates.

**Actions:**
1. Run `python -m py_compile scripts/check_complexity.py tests/test_quality_gates.py`.
2. Run `python scripts/check_complexity.py tentacle.py`.
3. Run `python scripts/check_complexity.py --json browse/`.
4. Run `python tests/test_quality_gates.py`.
5. Run `python test_fixes.py`.
6. Run `python run_all_tests.py`.
7. Run `git diff --check`.
8. Run a code-review agent on the diff.

**Done when:** Local gate output is recorded and review returns CLEAN or all findings are addressed.

## Step 6: COMMIT - Package and ship

**Goal:** Merge #257 through the isolated lane and clean local state.

**Actions:**
1. Commit only expected files with the required co-author trailer.
2. Open a PR with `Closes #257` and mirror labels.
3. Wait for CI, merge, move #257 project item to Done, and delete branch/worktree/tentacle.

**Done when:** PR is merged, issue #257 is closed, project item is Done, and the isolated lane is removed.

## Phase Gates

| Phase | Artifact | Status |
|-------|----------|--------|
| CLARIFY | Metrics and surfaces confirmed | [ ] |
| BUILD | `scripts/check_complexity.py` added | [ ] |
| TEST | `tests/test_quality_gates.py` passes | [ ] |
| DOCS | Contributor command documented | [ ] |
| REVIEW | Local gates and code review complete | [ ] |
| COMMIT | PR merged and lane cleaned | [ ] |
