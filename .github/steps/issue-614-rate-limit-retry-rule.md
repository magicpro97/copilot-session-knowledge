# STEPS: Issue #614 RateLimitRetryRule Phase-1 429 Queue

**Task:** Implement `RateLimitRetryRule` for `errorOccurred` hook events, durable retry queue logging, and `sk retry` read/status commands without attempting actual agent re-entry.
**Scope:** `sk-rust/src/hooks/rules/`, `sk-rust/src/commands/`, `sk-rust/src/main.rs`, Rust tests/benches as needed.
**Planning status:** The issue text cites synthesized algorithm confidence `0.85`; implementation is blocked until Opus research validates a confidence `1.0` plan.

## Step-plan review

- **Source:** `.github/steps/issue-614-rate-limit-retry-rule.md`.
- **Confidence source:** `issue-614-opus-leader` (`claude-opus-4.7`) returned confidence `1.0`.
- **Accepted steps:** 1, 2, 6, 7, 8, 9.
- **Edited steps:** 3 split into five tentacles: `issue-614-retry-core`, `issue-614-retry-queue`, `issue-614-retry-rule`, `issue-614-retry-cli`, `issue-614-retry-docs`; 4 and 5 mapped to those tentacles.
- **Rejected assumptions from issue body:** runtime `fastrand`, runtime `tempfile`, criterion bench, and external `research-429-docs.md` dependency. Replacements: SplitMix64-style jitter, append/rotate with existing stdlib patterns, CI-safe perf test, inline canonical pattern table.
- **Dependency order:** core -> queue -> rule -> CLI/docs -> orchestrator verification -> review -> commit/close.
- **Evidence contract:** each tentacle writes `handoff.md`; orchestrator runs Rust fmt/clippy/tests, Python gates, CI, and Opus review before shipping.

## Step 1: RESEARCH — Resolve confidence below 1.0

**Goal:** Produce a confidence `1.0` implementation decision before changing runtime code.

**Actions:**
1. Dispatch an Opus-class research leader to inspect `sk-rust/src/hooks/rules/mod.rs`, `sk-rust/src/hooks/rules/session.rs`, `sk-rust/src/hooks/sync_markers.rs`, `sk-rust/src/main.rs`, `sk-rust/src/commands/`, and existing tests.
2. Record accepted, edited, and rejected design choices from the leader output.
3. Decide whether #614 should implement standalone Phase-1 retry logic or wait for #616 shared retry library.

**Done when:** The orchestrator has evidence-backed confidence `1.0` for scope, dependency order, acceptance evidence, and affected systems.
**Confidence:** `1.0` required; otherwise create a new research tentacle and do not implement.

## Step 2: CLARIFY — Confirm the Phase-1 acceptance boundary

**Goal:** Lock the exact behavior #614 owns.

**Actions:**
1. Treat actual tool-call/LLM retry as out of scope; only queue structured retry decisions.
2. Confirm no database migration is needed; queue and state key are sidecar files under `~/.copilot`.
3. Confirm `RateLimitRetryRule` never denies hook execution and only handles `errorOccurred`.

**Done when:** The step review states the issue acceptance boundary and lists out-of-scope items.
**Confidence:** `1.0`.

## Step 3: DESIGN — Split into non-overlapping tentacles

**Goal:** Convert this scaffold into scoped work units.

**Actions:**
1. Create a retry-core/hook tentacle for classifier, backoff policy, redaction, HMAC record writing, rotation/path safety, and `HookRule` registration.
2. Create a retry-cli tentacle for `sk retry list/status` command parsing and display behavior.
3. Create a retry-verification tentacle for tests, benches, review evidence, and CI parity.

**Done when:** Tentacles have non-overlapping scopes, dependency order, and evidence contracts.
**Confidence:** `1.0`.

## Step 4: BUILD — Implement retry hook core

**Goal:** Add a non-blocking native hook rule that queues signed retry decision JSONL records.

**Actions:**
1. Add the smallest Rust module(s) needed under `sk-rust/src/hooks/rules/` or adjacent shared hook helpers.
2. Register `RateLimitRetryRule` in `all_rules()`.
3. Implement detection, stop conditions, env/config defaults, delay computation, credential redaction, state-key generation, HMAC signing, queue rotation, and Windows-safe atomic append.

**Done when:** `cargo test rate_limit retry -- --test-threads=1` covers the hook core and passes.
**Confidence:** `1.0`.

## Step 5: BUILD — Implement `sk retry` CLI

**Goal:** Add operator-facing queue inspection commands.

**Actions:**
1. Add a `Retry` command in `sk-rust/src/main.rs` and a handler in `sk-rust/src/commands/`.
2. Implement `sk retry list [--since 24h] [--agent copilot] [--json]`.
3. Implement `sk retry status` with last-seen pattern, active timer, and exhaustion summary.
4. Reject bad-HMAC queue lines and emit the required audit event.

**Done when:** CLI unit/integration tests prove table output, NDJSON output, filters, tamper rejection, and empty queue behavior.
**Confidence:** `1.0`.

## Step 6: TEST — Run focused and full gates

**Goal:** Prove behavior and protect existing surfaces.

**Actions:**
1. Run `cd sk-rust && cargo fmt --all -- --check`.
2. Run `cd sk-rust && cargo clippy --all-targets -- -D warnings`.
3. Run `cd sk-rust && cargo test rate_limit retry -- --test-threads=1`.
4. Run `cd sk-rust && cargo test`.
5. Run `python3 test_security.py && python3 test_fixes.py`.

**Done when:** Commands produce pass output with no introduced failures.
**Confidence:** `1.0`.

## Step 7: REVIEW — Opus code/security review

**Goal:** Catch security, logic, and scope bugs before shipping.

**Actions:**
1. Dispatch Opus-class code review on the exact diff.
2. Fix every substantive finding and rerun affected gates.
3. Repeat until review is `CLEAN` with confidence `1.0`.

**Done when:** Review output is clean, with evidence and no unresolved confidence gap.
**Confidence:** `1.0`.

## Step 8: LOOP-EVAL — Confirm issue #614 criteria are met

**Goal:** Decide whether to close #614 or iterate.

**Actions:**
1. Compare implemented behavior against every Functional, Performance, and Security checkbox in #614.
2. If any criterion is unmet, create a follow-up tentacle or explicitly defer only if it is outside #614 scope.
3. Record CI and local gate evidence.

**Done when:** All #614 in-scope acceptance criteria have evidence, or the loop identifies concrete remaining work.
**Confidence:** `1.0`.

## Step 9: COMMIT — Ship and close

**Goal:** Package verified work and close the issue.

**Actions:**
1. Commit with a `Closes #614` trailer after gates and Opus review pass.
2. Push to `main` or create a PR if branch protection/reviewer comments require it.
3. Watch CI to green and resolve reviewer comments.
4. Close #614 with evidence if not auto-closed by merge/commit.

**Done when:** Main is green, #614 is closed, and follow-up issues remain only for explicitly out-of-scope work.
**Confidence:** `1.0`.

## Phase Gates

| Phase | Artifact | Status |
|-------|----------|--------|
| RESEARCH | Opus confidence `1.0` plan | ☐ |
| CLARIFY | In-scope/out-of-scope boundary confirmed | ☐ |
| DESIGN | Tentacles with non-overlapping scopes | ☐ |
| BUILD hook | `RateLimitRetryRule` registered and tested | ☐ |
| BUILD CLI | `sk retry list/status` implemented and tested | ☐ |
| TEST | Rust + Python gates pass | ☐ |
| REVIEW | Opus review clean at confidence `1.0` | ☐ |
| LOOP-EVAL | #614 acceptance criteria proven | ☐ |
| COMMIT | Commit/PR green and #614 closed | ☐ |
