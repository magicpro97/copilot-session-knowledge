# STEPS: Issue 259 Rust Clippy advisory complexity thresholds

**Task:** Add Rust Clippy complexity thresholds and warn-only advisory CI coverage for `sk-rust/`.
**Scope:** `sk-rust/clippy.toml`, `.github/workflows/sk-ci.yml`, `docs/ARCHITECTURE.md`.

## Step 1: CLARIFY — Confirm scoped surfaces

**Goal:** Confirm issue #259 can be completed without touching the dirty root checkout or dependent documentation issues.

**Actions:**
1. `git status --short --branch` in the isolated worktree.
2. Read `.github/workflows/sk-ci.yml`, `sk-rust/Cargo.toml`, and `docs/ARCHITECTURE.md`.
3. Confirm no existing `sk-rust/clippy.toml` exists.

**Done when:** The worktree is clean on `issue-259-rust-clippy-advisory`, no existing Clippy threshold file is present, and the issue scope remains limited to the three expected surfaces.

## Step 2: BUILD — Add Clippy threshold config

**Goal:** Add warn-only threshold values that Clippy can read without changing release/build behavior.

**Actions:**
1. Create `sk-rust/clippy.toml`.
2. Add `cognitive-complexity-threshold`, `too-many-lines-threshold`, and `too-many-arguments-threshold`.

**Done when:** `sk-rust/clippy.toml` exists and contains all three required threshold keys.

## Step 3: BUILD — Add advisory CI invocation

**Goal:** Measure complexity lints in CI without breaking the existing strict Clippy job.

**Actions:**
1. Add a new step to the existing `clippy` job in `.github/workflows/sk-ci.yml`.
2. Name the step `Complexity advisory (Rust Clippy)`.
3. Run `cargo clippy --all-targets -- -W clippy::cognitive_complexity -W clippy::too_many_lines`.
4. Set `continue-on-error: true`.

**Done when:** The workflow has the advisory step, keeps the existing `cargo clippy -- -D warnings` gate intact, and the advisory step is non-blocking.

## Step 4: BUILD — Document the advisory policy

**Goal:** Make the Rust complexity advisory visible in the canonical architecture docs.

**Actions:**
1. Add a short `Rust complexity advisory` note to `docs/ARCHITECTURE.md`.
2. Mention `sk-rust/clippy.toml`, the warn-only CI step, and that warnings are measured before enforcement.

**Done when:** `docs/ARCHITECTURE.md` names the Clippy threshold file and explains the advisory CI behavior.

## Step 5: TEST — Run issue acceptance checks

**Goal:** Prove the scoped change is syntactically valid and does not break Rust checks.

**Actions:**
1. `Test-Path sk-rust\clippy.toml`
2. `Select-String -Path sk-rust\clippy.toml -Pattern 'cognitive-complexity-threshold|too-many-lines-threshold|too-many-arguments-threshold'`
3. `Select-String -Path .github\workflows\sk-ci.yml -Pattern 'Complexity advisory|continue-on-error: true|clippy::cognitive_complexity|clippy::too_many_lines'`
4. `Set-Location sk-rust; cargo clippy --all-targets`
5. `Set-Location sk-rust; cargo test --all`

**Done when:** The string checks find all required configuration and both Cargo commands exit 0.

## Step 6: REVIEW — Check for scope creep and gate safety

**Goal:** Confirm the advisory step cannot fail CI on existing complexity warnings and does not alter unrelated jobs.

**Actions:**
1. Review `git diff --stat`.
2. Confirm only the scoped files changed.
3. Confirm the new CI step has `continue-on-error: true`.

**Done when:** The diff is limited to the intended surfaces and no blocking complexity gate was introduced.

## Step 7: COMMIT — Package and ship

**Goal:** Produce a reviewed PR that closes #259 and preserves audit evidence.

**Actions:**
1. Commit the scoped changes.
2. Push `issue-259-rust-clippy-advisory`.
3. Open a PR with `Closes #259`.
4. Copy issue labels to the PR.

**Done when:** The PR exists with a closing keyword and the expected labels.

## Phase Gates

| Phase | Artifact | Status |
|-------|---------|--------|
| CLARIFY | Clean isolated worktree on fresh `origin/main` | ☐ |
| BUILD | `sk-rust/clippy.toml` and advisory CI/docs updates | ☐ |
| TEST | Required string checks + `cargo clippy --all-targets` + `cargo test --all` | ☐ |
| REVIEW | Diff limited to scoped files and advisory step is non-blocking | ☐ |
| COMMIT | PR closes #259 and mirrors labels | ☐ |
