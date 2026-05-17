# STEPS: Issue #262 remote-terminal quality baseline

**Task:** Add a lightweight advisory lint, security, and complexity baseline for `remote-terminal`.
**Scope:** `remote-terminal/package.json`, `remote-terminal/package-lock.json`, `remote-terminal/eslint.config.mjs`, `remote-terminal/README.md`, `.github/workflows/ci.yml`, `.github/steps/issue-262-remote-terminal-quality-baseline.md`.

## Step 1: CLARIFY - Confirm baseline target

**Goal:** Confirm the remote-terminal package has a measurable quality baseline without refactoring large files.

**Actions:**
1. Read issue #262 and confirm the accepted outputs: ESLint flat config, `npm run lint`, advisory `npm audit --audit-level=high`, CI wiring, and README documentation.
2. Inspect `remote-terminal/package.json`, `remote-terminal/server.js`, `remote-terminal/public/client.js`, `remote-terminal/test/*.test.js`, `remote-terminal/README.md`, and `.github/workflows/ci.yml`.
3. Run `cd remote-terminal && npm ci && npm test` to capture the current test baseline.

**Done when:** The package scripts, test surface, CI job, and large-file constraints are known.

## Step 2: BUILD - Add ESLint advisory baseline

**Goal:** Add warn-only JavaScript quality rules without changing runtime behavior.

**Actions:**
1. Install ESLint as a dev dependency.
2. Add `remote-terminal/eslint.config.mjs` with warn-level `complexity`, `max-lines-per-function`, `max-lines`, `max-params`, and unused-variable reporting.
3. Add `npm run lint` to run `eslint .`.

**Done when:** `cd remote-terminal && npm run lint` exits 0 while warnings remain advisory.

## Step 3: BUILD - Add dependency audit advisory

**Goal:** Add a high-severity dependency audit command that can be run locally and in CI without blocking the first baseline rollout.

**Actions:**
1. Add `npm run audit:advisory` as `npm audit --audit-level=high`.
2. Wire the CI step with `continue-on-error: true`.

**Done when:** `cd remote-terminal && npm run audit:advisory` exits 0 locally, and CI records audit output without making the job fail on future advisory findings.

## Step 4: BUILD - Wire CI and docs

**Goal:** Make the remote-terminal job publish the quality baseline and document local usage.

**Actions:**
1. Update `.github/workflows/ci.yml` remote-terminal job so lint and audit run after `npm test`.
2. Update `remote-terminal/README.md` with the local quality commands and warning-only baseline note.

**Done when:** The CI job runs tests first, then the lint baseline, then the non-blocking audit advisory; README lists the same commands.

## Step 5: REVIEW - Verify acceptance evidence

**Goal:** Prove #262 acceptance criteria and avoid packaging a broken baseline.

**Actions:**
1. Run `cd remote-terminal && npm ci`.
2. Run `cd remote-terminal && npm test`.
3. Run `cd remote-terminal && npm run lint`.
4. Run `cd remote-terminal && npm run audit:advisory`.
5. Run `git diff --check`.
6. Run a code-review agent on the resulting diff.

**Done when:** All commands exit 0, lint output includes only advisory warnings if any, and review returns CLEAN.

## Step 6: COMMIT - Package and ship

**Goal:** Merge the issue through the isolated lane and clean all local artifacts.

**Actions:**
1. Commit only the expected files with the required co-author trailer.
2. Open a PR with `Closes #262` and mirror issue labels.
3. Wait for CI, merge, move #262 project item to Done, and delete the branch/worktree/tentacle.

**Done when:** PR is merged, issue #262 is closed, the project item is Done, and the isolated lane is removed.

## Phase Gates

| Phase | Artifact | Status |
|-------|----------|--------|
| CLARIFY | Baseline package and CI surface confirmed | Done |
| BUILD | ESLint flat config and `npm run lint` added | Done |
| BUILD | Advisory audit script and CI step added | Done |
| BUILD | README documents quality commands | Done |
| REVIEW | Local gates and code review pass | Done |
| COMMIT | PR merged and lane cleaned | Pending |
