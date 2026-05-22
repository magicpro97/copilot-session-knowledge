# Hooks Migration Parity — Native Rule Coverage Inventory

> **Issue:** [#461](https://github.com/copilot-session-knowledge/copilot-session-knowledge/issues/461) — `[Rust] Hooks Migration Parity — Complete Native Rule Coverage`
>
> **Branch:** `docs/issue-461-hooks-parity-inventory`
>
> **Scope:** Research-only. No code changes. This document inventories Python hook rules versus
> native Rust hook rules and produces a priority-ranked parity plan.
>
> **Facts / Interpretation / Actions / Verification evidence** are kept in separate sections
> per Rule 7 (docs output quality).

---

## 1. Executive Summary

**Facts (verified from source files):**

- Python registry (`hooks/rules/__init__.py`) defines **35 unique rules** across 7 event types.
- Rust registry (`sk-rust/src/hooks/rules/mod.rs → all_rules()`) registers **27 rules**, of which
  26 correspond to Python rules and 1 (`SessionStartRule`) is a Rust-only informational stub.
- **10 Python rules have no Rust counterpart** and remain reachable only via `hook_runner.py`
  (Python shim path and non-binary installs).
- All 7 managed event types (`sessionStart`, `sessionEnd`, `preToolUse`, `postToolUse`,
  `agentStop`, `subagentStop`, `errorOccurred`) are now routed natively for **Rust-binary installs**
  (final managed flip: wave13 for `preToolUse`).
- The `userPromptSubmitted` event is defined by the platform but is **not** registered in
  `hooks/hooks.json` and has no Rust rule, matching the Python state (one Python rule exists but
  `userPromptSubmitted` is absent from `hooks.json`).
- Rust test coverage: **433 tests total** — 421 in the 7 dedicated test files under
  `sk-rust/src/hooks/rules/tests/`, plus 12 inline tests in `runner.rs` covering the
  dispatcher, dedup, fail-open, and preToolUse first-deny-wins logic.

**Interpretation:** The remaining 10 gaps are low-to-medium risk. Two (`confidence-gate` and
`constitution-gate`) are deny-capable preToolUse rules and carry the highest parity risk for
Rust-binary installs. The remaining 8 are informational-only or opt-in and have no blocking
impact on the Rust path.

---

## 2. Complete Rule Inventory

Each row covers: Python rule name, Python file, Rust struct (or gap), event(s), tool filter,
deny-capable flag, porting status, and current test coverage.

### 2.1 Managed Events — Full Inventory Table

| # | Rule name | Python file | Rust struct | Event(s) | Deny? | Rust status | Rust test file |
|---|-----------|------------|-------------|----------|-------|-------------|----------------|
| 1 | `session-start` *(Rust-only)* | *(none)* | `SessionStartRule` | sessionStart | No | ✅ Rust-only stub | `session.rs` |
| 2 | `auto-briefing` | `briefing.py` | `AutoBriefingRule` | sessionStart | No | ✅ Ported (wave9) | `session.rs` |
| 3 | `integrity` | `integrity.py` | `IntegrityRule` | sessionStart | No | ✅ Ported (wave9) | `session.rs` |
| 4 | `enforce-briefing` | `briefing.py` | `EnforceBriefingRule` | preToolUse | **Yes** | ✅ Ported (wave11 direct; wave13 managed) | `guard.rs` |
| 5 | `enforce-learn` | `learn_gate.py` | `EnforceLearnRule` | preToolUse | **Yes** | ✅ Ported (wave11 direct; wave13 managed) | `learn.rs` |
| 6 | `tentacle-enforce` | `tentacle.py` | `TentacleEnforceRule` | preToolUse | **Yes** | ✅ Ported (wave12 direct; wave13 managed) | `tentacle.rs` |
| 7 | `subagent-git-guard` | `subagent_guard.py` | `SubagentGitGuardRule` | preToolUse | **Yes** | ✅ Ported (wave6) | `guard.rs` |
| 8 | `confidence-gate` | `confidence_gate.py` | *(none)* | preToolUse | **Yes** | ❌ **Gap** | *(none)* |
| 9 | `constitution-gate` | `constitution_gate.py` | *(none)* | preToolUse | **Yes** | ❌ **Gap** | *(none)* |
| 10 | `syntax-gate` | `syntax_gate.py` | `SyntaxGateRule` | preToolUse | **Yes** | ✅ Ported (wave13; via `py_compile` subprocess) | `guard.rs` |
| 11 | `block-edit-dist` | `block_edit_dist.py` | `BlockEditDistRule` | preToolUse | **Yes** | ✅ Ported (wave6) | `guard.rs` |
| 12 | `pnpm-lockfile-guard` | `pnpm_lockfile_guard.py` | `PnpmLockfileGuardRule` | preToolUse | **Yes** | ✅ Ported (wave7) | `guard.rs` |
| 13 | `block-unsafe-html` | `block_unsafe_html.py` | `BlockUnsafeHtmlRule` | preToolUse | **Yes** | ✅ Ported (wave6) | `guard.rs` |
| 14 | `verification-gate` (pre) | `verification_gate.py` | `VerificationGatePreRule` | preToolUse | Info-deny | ✅ Ported (wave8 direct; wave13 managed) | `verification.rs` |
| 15 | `file-size-advisory` | `file_size_advisory.py` | `FileSizeAdvisoryRule` | preToolUse | No | ✅ Ported (wave13) | `guard.rs` |
| 16 | `new-file-advisory` | `new_file_advisory.py` | `NewFileAdvisoryRule` | preToolUse | No | ✅ Ported (wave13) | `guard.rs` |
| 17 | `read-before-edit` | `read_before_edit.py` | `ReadBeforeEditRule` | preToolUse + postToolUse | No | ✅ Ported (wave7 direct; wave10/13 managed) | `edit_track.rs` |
| 18 | `read-tracker` | `read_tracker.py` | *(none)* | preToolUse (`view` only) | No | ❌ **Gap** | *(none)* |
| 19 | `track-edits` | `edit_tracker.py` | `TrackEditsRule` | postToolUse | No | ✅ Ported (wave6/8) | `edit_track.rs` |
| 20 | `learn-reminder` | `learn_reminder.py` | `LearnReminderRule` | postToolUse | No | ✅ Ported (wave6) | `learn.rs` |
| 21 | `test-reminder` | `edit_tracker.py` | `TestReminderRule` | postToolUse | No | ✅ Ported (wave7 direct; wave10 managed) | `edit_track.rs` |
| 22 | `auto-bug-detector` | `auto_bug_detector.py` | `AutoBugDetectorRule` | postToolUse | No | ✅ Ported (wave10) | `learn.rs` |
| 23 | `tentacle-suggest` | `tentacle.py` | `TentacleSuggestRule` | postToolUse | No | ✅ Ported (wave8 direct; wave10 managed) | `tentacle.rs` |
| 24 | `nextjs-typecheck-reminder` | `nextjs_typecheck.py` | `NextjsTypecheckReminderRule` | postToolUse | No | ✅ Ported (wave7 direct; wave10 managed) | `edit_track.rs` |
| 25 | `skill-nudge` | `skill_nudge.py` | *(none)* | postToolUse | No | ❌ **Gap** | *(none)* |
| 26 | `skill-usage` | `skill_usage.py` | `SkillUsageRule` | postToolUse | No | ✅ Ported (wave10) | `session.rs` |
| 27 | `token-tracker` | `token_tracker.py` | *(none)* | postToolUse | No | ❌ **Gap** | *(none)* |
| 28 | `episode-batcher` | `episode_batcher.py` | *(none)* | postToolUse | No | ❌ **Gap** (opt-in) | *(none)* |
| 29 | `verification-gate` (post) | `verification_gate.py` | `VerificationGatePostRule` | postToolUse | No | ✅ Ported (wave7 direct; wave10 managed) | `verification.rs` |
| 30 | `error-fix-nudge` | `error_kb.py` | *(none)* | postToolUse | No | ❌ **Gap** | *(none)* |
| 31 | `error-kb` | `error_kb.py` | `ErrorOccurredRule` | errorOccurred | No | ✅ Ported (wave5; native FTS5) | `session.rs` |
| 32 | `session-end` | `session_lifecycle.py` | `SessionEndRule` | sessionEnd | No | ✅ Ported (wave4) | `session.rs` |
| 33 | `recurrence-detector` | `recurrence_detector.py` | `RecurrenceDetectorRule` | sessionEnd | No | ✅ Ported (wave9) | `session.rs` |
| 34 | `session-compiler` | `session_compiler.py` | *(none)* | sessionEnd | No | ❌ **Gap** (opt-in) | *(none)* |
| 35 | `skill-improvement-advisor` | `skill_improvement_advisor.py` | *(none)* | sessionEnd | No | ❌ **Gap** | *(none)* |
| 36 | `subagent-stop-cleanup` | `session_lifecycle.py` | `AgentStopRule` | agentStop + subagentStop | No | ✅ Ported (wave3) | `session.rs` |
| 37 | `user-prompt-audit` | `user_prompt_audit.py` | *(none)* | userPromptSubmitted | No | ❌ **Gap** (event absent from `hooks.json`) | *(none)* |

**Legend:**
- ✅ = Ported to native Rust and reachable on managed event path for Rust-binary installs
- ❌ = No Rust counterpart; runs via `hook_runner.py` on Python shim path only (Rust-binary installs skip it)
- "Info-deny" = emits `permissionDecision: deny` but with advisory-level messaging, not a hard block

---

## 3. Parity Gaps — Priority-Ranked

Ten Python rules have no Rust counterpart. The following table ranks them by risk, with
reasoning for each priority level.

| Priority | Rule | Event | Deny? | Missing impact on Rust-binary installs | Effort estimate |
|----------|------|-------|-------|----------------------------------------|-----------------|
| **P1 — HIGH** | `confidence-gate` | preToolUse | **Yes** | When `research_gate.required=true` is set in a conductor plan, Rust-binary installs will NOT block edit/create/bash/task_complete. The gate silently has no effect. | Medium — reads 3 possible JSON marker files; no subprocess; no HMAC |
| **P1 — HIGH** | `constitution-gate` | preToolUse | **Yes** | Project-local `.copilot/constitution.md` rule tags (e.g., `no-destructive-git`, `no-force-push`) are not enforced for Rust-binary installs. | Low-Medium — reads one file, two built-in rule tags; no subprocess |
| **P2 — MEDIUM** | `error-fix-nudge` | postToolUse | No | After an `errorOccurred`, Rust-binary installs will not emit the nudge to record the fix via `sk learn`. Advisory only. | Low — checks for an error marker, emits message |
| **P2 — MEDIUM** | `read-tracker` | preToolUse (`view`) | No | Repeat-read warnings (issue #85) are silently absent for Rust-binary installs. Advisory only; uses shared session state populated by `token-tracker`. | Medium — depends on session state written by `token-tracker` (also a gap) |
| **P3 — LOW** | `skill-nudge` | postToolUse | No | One-time skill-creation nudge (issue #116) not emitted for Rust-binary installs. Advisory only. | Low — counter check against session state |
| **P3 — LOW** | `token-tracker` | postToolUse | No | Per-session token-usage estimation and `files_read` metadata (issue #84) not recorded for Rust-binary installs. Session state will be absent, which also blocks `read-tracker`. | Medium — reads tool output, writes to session state JSON file |
| **P3 — LOW** | `skill-improvement-advisor` | sessionEnd | No | Skill improvement suggestions are not written at session end for Rust-binary installs. Advisory only. | High — queries `knowledge.db` for recent entries, writes to markers |
| **P4 — DEFER** | `episode-batcher` | postToolUse | No | Opt-in (`SK_EPISODE_BATCH_ENABLED=1`). No user impact without opt-in. | High — batch episode writes to knowledge DB |
| **P4 — DEFER** | `session-compiler` | sessionEnd | No | Opt-in (`SK_SESSION_COMPILE_ENABLED=1`). No user impact without opt-in. | High — consolidates knowledge entries |
| **P4 — DEFER** | `user-prompt-audit` | userPromptSubmitted | No | `userPromptSubmitted` is absent from `hooks.json` — the platform fires it but the repo intentionally does not handle it yet. Matching Python state. Defer until hooks.json registers the event. | Low — once event is registered |

---

## 4. Managed Event Routing State (post-wave13)

**Facts** — verified from `sk-rust/src/commands/hooks.rs` and `hooks/hooks.json`:

```
# Rust-binary installs:
sk hooks run sessionStart   → native Rust  (wave9)
sk hooks run sessionEnd     → native Rust  (wave4 + wave9)
sk hooks run agentStop      → native Rust  (wave3)
sk hooks run subagentStop   → native Rust  (wave3)
sk hooks run errorOccurred  → native Rust  (wave5)
sk hooks run postToolUse    → native Rust  (wave10)
sk hooks run preToolUse     → native Rust  (wave13)

# Python sk.py shim (non-binary installs):
sk hooks run <any event>    → hook_runner.py  (all events, behavior unchanged)
```

**Implication for gaps:** Rules #8, #9, #18, #25, #27, #28, #30, #34, #35 are silently absent
on all native Rust paths. They remain active only via `hook_runner.py` (Python shim and
non-binary installs).

---

## 5. Rust Test Coverage Summary

**Facts** — verified from `sk-rust/src/hooks/rules/tests/`:

| Test file | Covers rules | Test count |
|-----------|-------------|-----------|
| `all_rules.rs` | Registration order, event/tool routing | 17 |
| `edit_track.rs` | `TrackEditsRule`, `TestReminderRule`, `NextjsTypecheckReminderRule`, `ReadBeforeEditRule` | 55 |
| `guard.rs` | `SubagentGitGuardRule`, `PnpmLockfileGuardRule`, `SyntaxGateRule`, `BlockEditDistRule`, `BlockUnsafeHtmlRule`, `EnforceBriefingRule`, `FileSizeAdvisoryRule`, `NewFileAdvisoryRule` | 71 |
| `learn.rs` | `LearnReminderRule`, `AutoBugDetectorRule`, `EnforceLearnRule` | 120 |
| `session.rs` | `SessionStartRule`, `AutoBriefingRule`, `IntegrityRule`, `SessionEndRule`, `RecurrenceDetectorRule`, `AgentStopRule`, `ErrorOccurredRule`, `SkillUsageRule` | 79 |
| `tentacle.rs` | `TentacleSuggestRule`, `TentacleEnforceRule` | 37 |
| `verification.rs` | `VerificationGatePostRule`, `VerificationGatePreRule` | 42 |
| `runner.rs` (inline) | Dispatcher, dedup, fail-open, preToolUse first-deny-wins | 12 |
| **Total** | | **433** |

**Tests needed for gap rules** (not yet written; listed as actions):

| Rule | Required test scenarios |
|------|------------------------|
| `confidence-gate` | (a) no conductor file → pass; (b) `research_gate.required=true` → deny; (c) `research_gate.required=false` → pass; (d) malformed JSON → fail-open |
| `constitution-gate` | (a) no constitution file → pass; (b) `no-destructive-git` tag, `git reset --hard` → deny; (c) `no-force-push` tag, `git push --force` → deny; (d) `--force-with-lease` → pass; (e) missing/malformed file → fail-open |
| `error-fix-nudge` | (a) no error marker → pass; (b) error marker present, bash/edit → emit nudge; (c) `sk learn` detected → clear marker |
| `read-tracker` | (a) first view → no warning; (b) second view same file → warning; (c) ignore suffix list respected; (d) no session state → fail-open |
| `skill-nudge` | (a) below threshold → pass; (b) exactly at threshold → emit once; (c) above threshold → emit only once |
| `token-tracker` | (a) view → accumulate token estimate; (b) budget warning at 80%/95%; (c) `TOKEN_BUDGET` override respected; (d) session state update |
| `skill-improvement-advisor` | (a) no DB → fail-open; (b) recent entries → suggestions file written; (c) max 5 suggestions |
| `episode-batcher` | (a) opt-in env absent → skip; (b) opt-in enabled → batch after threshold |
| `session-compiler` | (a) opt-in env absent → skip; (b) opt-in enabled → consolidation triggered |

---

## 6. Actions

> These are concrete next steps derived from the parity inventory.
> Ordered by priority. Each action includes an executable command or file reference.

### P1 — Port deny-capable preToolUse gaps (wave14 candidates)

1. **Port `confidence-gate` to Rust** (`sk-rust/src/hooks/rules/guard.rs` or a new `confidence.rs`):
   - Read `~/.copilot/markers/confidence-gate.json`, `.github/conductor/last-plan.json`, and
     `.copilot/confidence-gate.json`; check `research_gate.required == true`; deny for tools
     `edit`, `create`, `bash` (write-mode), `task_complete`, and git closeout commands.
   - Register in `all_rules()` between `SubagentGitGuardRule` and `SyntaxGateRule` to match
     Python dispatch order.
   - Add tests to `guard.rs` tests or a new `confidence.rs` test file.
   - **Verification command:** `cargo test -p sk-rust -- confidence --nocapture`

2. **Port `constitution-gate` to Rust** (`sk-rust/src/hooks/rules/guard.rs` or new file):
   - Read `.copilot/constitution.md`; parse YAML front-matter rule tags; apply built-in tag
     handlers `no-destructive-git` and `no-force-push` to bash commands.
   - Register after `confidence-gate` in `all_rules()`.
   - Add tests covering both rule tags and fail-open on missing/malformed file.
   - **Verification command:** `cargo test -p sk-rust -- constitution --nocapture`

### P2 — Port medium-priority informational gaps (wave15 candidates)

3. **Port `error-fix-nudge` to Rust** (`sk-rust/src/hooks/rules/learn.rs`):
   - Check for an `errorOccurred` marker; if present, emit nudge on bash/edit/create; clear
     when `sk learn` is detected.
   - Add tests to `learn.rs` tests.

4. **Port `read-tracker` to Rust** (`sk-rust/src/hooks/rules/edit_track.rs`):
   - Requires `token-tracker` session state (`files_read` metadata) as prerequisite; port or
     stub `token-tracker` first.
   - Add tests to `edit_track.rs` tests.

### P3 — Informational-only gaps (wave16+ candidates)

5. **Port `token-tracker`** — writes session-state JSON with per-session token estimates.
   Prerequisite for `read-tracker`. Add budget-warning tests.

6. **Port `skill-nudge`** — one-time counter check; emit informational message once per session.

7. **Port `skill-improvement-advisor`** — queries `knowledge.db`; writes suggestions marker file.
   Fail-open gating is critical.

### P4 — Deferred (no active user impact without opt-in / event registration)

8. **`episode-batcher`** — defer until opt-in usage increases.
9. **`session-compiler`** — defer until opt-in usage increases.
10. **`user-prompt-audit`** — defer until `userPromptSubmitted` is added to `hooks.json`.

### Documentation

11. **Update `docs/HOOKS.md` § "Python-only rules"** to link to this inventory and note the
    remaining 10 gaps explicitly with their `hooks.json` routing state.
    File: `docs/HOOKS.md` lines 345–383.

---

## 7. Verification Evidence

This document is research-only. The following evidence was gathered during inventory:

| Claim | Evidence |
|-------|---------|
| Python registry contains 35 rules | `hooks/rules/__init__.py` ALL_RULES list, lines 53–99; `get_rules_for_event()` imports verified |
| Rust registry contains 27 rules | `sk-rust/src/hooks/rules/mod.rs` `all_rules()`, lines 130–159; counted 27 `Box::new(...)` entries |
| 10 rules have no Rust counterpart | Cross-reference of Python ALL_RULES vs Rust `all_rules()` struct names; no struct with matching semantic purpose found in any `*.rs` files under `sk-rust/src/hooks/rules/` |
| wave13 is the latest managed flip | `docs/HOOKS.md` lines 386–418; routing state table at lines 450–462 |
| 433 total Rust tests | `Select-String "#\[test\]"` across 7 test files + runner inline tests |
| `userPromptSubmitted` absent from `hooks.json` | `hooks/hooks.json` contains only 7 event keys: `sessionStart`, `sessionEnd`, `preToolUse`, `postToolUse`, `agentStop`, `subagentStop`, `errorOccurred` |
| `confidence-gate` tools filter | `hooks/rules/confidence_gate.py` line: `tools = ["edit", "create", "bash", "task_complete"]` |
| `constitution-gate` tools filter | `hooks/rules/constitution_gate.py` line: `tools = ["bash"]` |
| All 10 gap rules are deny-capable=No except confidence/constitution | `hooks/rules/*.py` — `evaluate()` returns `None` or info for all non-deny rules; confidence_gate and constitution_gate return `common.deny(...)` |

**Commands used to verify (repeatable):**

```powershell
# Count Python rules
(Select-String -Path "hooks\rules\__init__.py" -Pattern "Rule\(\)").Count

# Count Rust rules
(Select-String -Path "sk-rust\src\hooks\rules\mod.rs" -Pattern "Box::new\(").Count

# List Rust rule struct names
Select-String -Path "sk-rust\src\hooks\rules\*.rs" -Pattern "^pub struct.*Rule" | ForEach-Object { $_.Line.Trim() }

# Count Rust tests
Get-ChildItem "sk-rust\src\hooks\rules\tests" -Filter "*.rs" | ForEach-Object {
    "$($_.Name): $((Select-String -Path $_.FullName -Pattern '#\[test\]').Count)"
}
```

---

## 8. Sources Consulted

| File | Purpose |
|------|---------|
| `hooks/rules/__init__.py` | Python rule registry (ALL_RULES, get_rules_for_event) |
| `hooks/rules/*.py` | Individual Python rule implementations (events, tools, deny logic) |
| `hooks/hooks.json` | Managed hook event registrations and routing commands |
| `sk-rust/src/hooks/rules/mod.rs` | Rust `all_rules()` registry and `HookRule` trait |
| `sk-rust/src/hooks/rules/*.rs` | Individual Rust rule implementations |
| `sk-rust/src/hooks/runner.rs` | Native dispatch loop, dedup, fail-open contract |
| `sk-rust/src/hooks/rules/tests/*.rs` | Rust unit test coverage |
| `docs/HOOKS.md` | Wave history, parity gap analysis, routing state table |

---

*Generated for issue #461. Document status: **complete — research-only, no code changes**.*
*Last updated: 2025-01-01 (branch `docs/issue-461-hooks-parity-inventory`).*
