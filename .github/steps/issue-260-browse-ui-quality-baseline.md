# STEPS: Issue 260 browse-ui complexity and coverage baseline

**Task:** Add warn-level browse-ui ESLint complexity rules and Vitest V8 coverage baseline.
**Scope:** `browse-ui/eslint.config.mjs`, `browse-ui/vitest.config.ts`, `browse-ui/package.json`, `browse-ui/pnpm-lock.yaml`.

## Step 1: CLARIFY — Confirm scoped surfaces

**Goal:** Confirm issue #260 can be completed in an isolated worktree without touching unrelated root changes.

**Actions:**
1. `git status --short --branch`
2. Read `browse-ui/eslint.config.mjs`, `browse-ui/vitest.config.ts`, `browse-ui/package.json`, and `browse-ui/pnpm-lock.yaml`.

**Done when:** The lane is clean, #260 is In Progress, and the edit surface is limited to browse-ui config/package files plus this step file.

## Step 2: BUILD — Add ESLint advisory rules

**Goal:** Add measurement-only lint baselines that warn without failing CI.

**Actions:**
1. Set `@typescript-eslint/no-explicit-any` to `warn`.
2. Add warn-level `complexity`, `max-lines-per-function`, and `max-params`.

**Done when:** `browse-ui/eslint.config.mjs` contains all required rule names with warn severity.

## Step 3: BUILD — Add Vitest V8 coverage baseline

**Goal:** Configure Vitest coverage without changing the default `pnpm test` behavior.

**Actions:**
1. Add `test.coverage.provider = "v8"` in `browse-ui/vitest.config.ts`.
2. Add `@vitest/coverage-v8` as a dev dependency.
3. Update `browse-ui/pnpm-lock.yaml` through pnpm.

**Done when:** The config contains `coverage`, package.json declares the provider package, and the lockfile is consistent.

## Step 4: TEST — Run issue acceptance checks

**Goal:** Prove the baseline is installable and does not break existing browse-ui gates.

**Actions:**
1. `cd browse-ui && pnpm install --frozen-lockfile`
2. `cd browse-ui && pnpm lint`
3. `cd browse-ui && pnpm typecheck`
4. `cd browse-ui && pnpm test`
5. `cd browse-ui && pnpm build`
6. Confirm config strings: `complexity`, `max-lines-per-function`, `no-explicit-any`, and `coverage`.

**Done when:** All commands exit 0 and the required strings are present.

## Step 5: REVIEW — Check scope and advisory behavior

**Goal:** Confirm only measurement/advisory behavior changed.

**Actions:**
1. Review `git diff --stat`.
2. Confirm no ESLint rule uses `error`.
3. Confirm `pnpm test` still runs without coverage unless explicitly requested.

**Done when:** The diff is limited to scoped files and the baseline remains warning-only.

## Step 6: COMMIT — Package and ship

**Goal:** Produce a PR that closes #260 and carries verification evidence.

**Actions:**
1. Commit the scoped changes.
2. Push `issue-260-browse-ui-quality-baseline`.
3. Open a PR with `Closes #260`.
4. Copy issue labels to the PR.

**Done when:** The PR exists with the closing keyword and expected labels.

## Phase Gates

| Phase | Artifact | Status |
|-------|---------|--------|
| CLARIFY | Scoped isolated lane | ☐ |
| BUILD | ESLint + Vitest + package/lock updates | ☐ |
| TEST | browse-ui install/lint/typecheck/test/build pass | ☐ |
| REVIEW | Warning-only and scoped diff confirmed | ☐ |
| COMMIT | PR closes #260 and mirrors labels | ☐ |
