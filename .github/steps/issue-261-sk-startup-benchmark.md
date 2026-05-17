# STEPS: Issue #261 sk startup benchmark advisory

**Task:** Add a non-blocking startup benchmark advisory for `sk --help`.
**Scope:** `benchmark.py`, `.github/workflows/sk-ci.yml`, `tests/test_benchmark.py`, `.github/steps/issue-261-sk-startup-benchmark.md`.

## Step 1: CLARIFY — Confirm benchmark target

**Goal:** Use a stable command that exercises `sk` startup without mutating repo or user state.

**Actions:**
1. Read issue #261 and confirm the accepted command is `sk --help` or an equivalent stable no-op.
2. Inspect `benchmark.py`, `.github/workflows/sk-ci.yml`, and `sk-rust/src/main.rs`.

**Done when:** The benchmark target is confirmed as the compiled `sk` binary invoked with `--help`.

## Step 2: BUILD — Add startup timing command

**Goal:** Extend `benchmark.py` with a local command that measures startup latency.

**Actions:**
1. Add `benchmark.py startup` with `--runs`, `--warmups`, `--timeout`, and `-- COMMAND...`.
2. Print `median_ms`, `min_ms`, and `max_ms` in text output, and expose the same fields in JSON output.
3. Return non-zero if the measured command fails, times out, or is missing.

**Done when:** `python3 benchmark.py startup --runs 3 --warmups 1 -- python -c "pass"` exits 0 and prints median/min/max milliseconds.

## Step 3: TEST — Cover parser and output contract

**Goal:** Prove the startup benchmark command is parseable and emits the required fields.

**Actions:**
1. Add `tests/test_benchmark.py` coverage for startup argument parsing.
2. Add `tests/test_benchmark.py` coverage for text and JSON startup output.
3. Run `python3 tests/test_benchmark.py`.

**Done when:** The benchmark tests pass with the new startup assertions.

## Step 4: BUILD — Add CI advisory step

**Goal:** Add a CI log baseline without making startup benchmark failures block merges.

**Actions:**
1. Add a `sk-ci.yml` advisory job that builds the release binary.
2. Run `python3 benchmark.py startup --runs 7 --warmups 2 --timeout 10 -- sk-rust/target/release/sk --help`.
3. Set `continue-on-error: true` on the benchmark step.

**Done when:** The workflow contains an advisory startup step that prints `median_ms`, `min_ms`, and `max_ms`.

## Step 5: REVIEW — Verify acceptance evidence

**Goal:** Confirm the issue acceptance criteria are independently evidenced.

**Actions:**
1. Run `python3 -m py_compile benchmark.py tests/test_benchmark.py`.
2. Run `python3 tests/test_benchmark.py`.
3. Run `python3 test_fixes.py`.
4. Run `cargo build --release --manifest-path sk-rust/Cargo.toml`.
5. Run `python3 benchmark.py startup --runs 3 --warmups 1 --timeout 10 -- ./sk-rust/target/release/sk --help`.

**Done when:** All commands exit 0 and the local startup command prints median/min/max milliseconds.

## Step 6: COMMIT — Package and ship

**Goal:** Ship the issue in an isolated lane with traceable evidence.

**Actions:**
1. Run `git diff --stat` and confirm only expected files changed.
2. Commit with the required co-author trailer.
3. Open a PR with `Closes #261` and mirror issue labels.
4. Merge after CI passes, move the project item to Done, then delete the branch/worktree/tentacle.

**Done when:** PR is merged, issue #261 is closed, project status is Done, and the local lane is cleaned.

## Phase Gates

| Phase | Artifact | Status |
|-------|----------|--------|
| CLARIFY | `sk --help` target confirmed | Done |
| BUILD | `benchmark.py startup` implemented | Done |
| TEST | Startup parser/output tests pass | Pending |
| BUILD | `sk-ci.yml` advisory step added | Done |
| REVIEW | Local startup benchmark and regression gates pass | Pending |
| COMMIT | PR merged and lane cleaned | Pending |
