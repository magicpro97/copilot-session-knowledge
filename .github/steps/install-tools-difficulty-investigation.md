# STEPS: Investigate install/tool-detection difficulties

**Task:** Use the two most recent prior sessions to investigate why tool installation and recognition were difficult, then prepare a reviewed remediation plan before implementation.
**Scope:** `install.py`, `install-binary.py`, `setup-project.py`, `sk.py`, `docs/INSTALL.md`, `README.md`, `templates/*.md`, `hooks/hooks.json`, `.github/skills/**`, `.github/hooks/**`
**Estimated phases:** CLARIFY -> DESIGN -> VERIFY -> BUILD (after approval only) -> TEST -> REVIEW -> LOOP-EVAL -> COMMIT

---

## Step 1: CLARIFY - Establish evidence baseline

**Goal:** Identify the exact recent sessions and observable install/tool-recognition symptoms without exposing secrets.

**Actions:**
1. Query `session_store.sessions` for the two latest prior sessions in `magicpro97/copilot-session-knowledge`.
2. Query `session_store.turns`, `checkpoints`, and `session_files` for those sessions.
3. Run a compact session briefing with the local Python launcher.
4. Record only non-secret facts from session content.

**Done when:** The evidence set names the two prior session IDs, their user asks, and any non-secret install/tool-detection symptoms.

---

## Step 2: CLARIFY - Audit current machine state

**Goal:** Determine whether the install is missing files, missing PATH wiring, or only stale in the current process.

**Actions:**
1. Run `Get-Command sk`, `Get-Command python`, `Get-Command python3`, and inspect `$env:Path`.
2. Inspect `HKCU:\Environment\Path` for `C:\Users\Linh Ngo\.copilot\bin`.
3. Run `python "$env:USERPROFILE\.copilot\tools\install.py" --doctor` and `--test`.
4. Run `"$env:USERPROFILE\.copilot\bin\sk.cmd" --help` and `"$env:USERPROFILE\.copilot\bin\python3.cmd" --version`.
5. Count `.github/skills`, `.claude/skills`, `~/.copilot/skills`, and inspect hooks deployment.

**Done when:** Each suspected failure mode has command evidence showing present, absent, or stale-process state.

---

## Step 3: DESIGN - Classify root causes and fixes

**Goal:** Convert evidence into a prioritized remediation plan instead of immediately editing code.

**Actions:**
1. Separate environment/runtime issues from repository defects.
2. Map each root cause to a minimal fix and its affected files.
3. Define rollout order: operator command first, then docs/installer improvements, then tests.
4. Mark any high-risk or ambiguous item as blocked until reviewed.

**Done when:** The plan lists root causes, recommended fixes, blast radius, and commands/tests for each fix.

---

## Step 4: VERIFY - Review decomposition and remediation before implementation

**Goal:** Ensure the plan is small, non-overlapping, reviewable, and evidence-backed before any code change.

**Actions:**
1. Apply `.github/skills/tentacle-orchestration/references/decomposition-review.md`.
2. Record accepted, edited, and rejected steps.
3. Run an independent whole-app impact review of the proposed fixes.
4. Do not implement until the review verdict is acceptable.

**Done when:** Review output explicitly states PASS or lists required edits; rejected immediate-implementation steps stay rejected.

---

## Reviewed remediation requirements

**Review verdict:** PASS WITH REQUIRED EDITS.

**Accepted steps:** 1, 2, 3, 4, 6, 7, 8, 9.

**Edited steps:**
1. Step 5 must include runtime-deployed instructions in addition to docs/templates.
2. Step 5 must include the Windows `--install-sk` success-path guidance, not only `--doctor`.
3. Step 6 must list concrete regression tests for PATH and interpreter diagnostics.

**Rejected steps:**
1. Immediate implementation before review.
2. Promoting all project skills to global scope without a deliberate scope decision.

**Required edits before implementation:**
1. Include `install.py` runtime-deployed strings: `MINIMAL_SKILL_MD` and `GLOBAL_INJECT_BLOCK`.
2. Fix the `deploy_hooks()` Windows-facing message that says hooks fall back to `python3`; PowerShell hooks actually fall back to `python`.
3. Add Windows current-session PATH refresh guidance to the install success path, not only to `--doctor`.
4. Make test coverage concrete for PATH normalization, Windows PATH injection idempotency, doctor mismatch output, and `python3` alias-risk detection.
5. Explicitly include or explicitly defer `setup-project.py` generated instruction snippets and `templates/session-knowledge.instructions.md`.

---

## Step 5: BUILD - Implement only after review approval

**Goal:** Make surgical changes that remove the verified install friction.

**Actions:**
1. If approved, update installer diagnostics so `--doctor` reports current-process PATH mismatch, registry PATH state, `sk` discoverability, and Windows `python3` alias risk.
2. If approved, update Windows-facing docs/templates/runtime-deployed instruction strings to prefer `sk`, then `python`, then quoted absolute paths; avoid stale `python3` Windows examples.
3. If approved, include `install.py` constants `MINIMAL_SKILL_MD` and `GLOBAL_INJECT_BLOCK`, `deploy_hooks()` user-facing messages, `setup-project.py` generated snippets, and `templates/session-knowledge.instructions.md` unless each surface is explicitly deferred with risk.
4. If approved, add current-session PowerShell PATH refresh guidance after Windows launcher installation or refresh, because users hit `sk` immediately after install before restarting.
5. If approved, add or update targeted tests for the diagnostics and documentation/runtime-instruction contract.

**Done when:** Only reviewed files are changed and each change maps to a root cause from Step 3.

---

## Step 6: TEST - Prove the same criteria turn green

**Goal:** Verify fixes with concrete commands, not assumptions.

**Actions:**
1. Run `python -c "import ast; ast.parse(open('<modified.py>', encoding='utf-8').read())"` for each modified Python file.
2. Run `python test_fixes.py` after any Python script change.
3. Run `python test_security.py` when touching hook, path, process, or install hardening surfaces.
4. Run targeted tests for:
   - Windows PATH entry normalization (`C:\Users\...\bin\`, case differences, slash differences).
   - Windows PATH injection idempotency (second injection does not duplicate the entry).
   - `doctor()` reporting current-process PATH mismatch and exact guidance.
   - `python3` alias-risk detection with alias present and absent.
5. Run `python "$env:USERPROFILE\.copilot\tools\install.py" --doctor` to confirm the new diagnostic reports the expected state.
6. Run `"$env:USERPROFILE\.copilot\bin\sk.cmd" --help` and, after refreshing `$env:Path`, `sk --help`.

**Done when:** Test output and diagnostic output show pass/fail counts or explicit messages for every acceptance criterion.

---

## Step 7: REVIEW - Code and security review

**Goal:** Catch correctness, security, and scope issues introduced by any approved implementation.

**Actions:**
1. Dispatch a review focused on installer behavior, PATH mutation, Windows shell behavior, and documentation drift.
2. Address all real findings.
3. Re-run affected tests after changes.

**Done when:** Reviewer verdict is CLEAN or all blocking findings have fixes and evidence.

---

## Step 8: LOOP-EVAL - Decide whether the goal is met

**Goal:** Verify the overarching goal: future users can install and agents can discover tools without the same confusion.

**Actions:**
1. Check that direct launcher, current-session PATH guidance, Windows fallback commands, skills, hooks, and project registry each have evidence.
2. If any criterion remains unproven, loop back to Step 3 with a new narrow fix.

**Done when:** All criteria are evidenced or explicitly marked "not proven yet" with the exact command to run.

---

## Step 9: COMMIT - Package only after approval and verification

**Goal:** Commit only reviewed, verified changes.

**Actions:**
1. Run `git diff --stat` and confirm only expected files changed.
2. Commit with the required co-author trailer if implementation was approved and completed.

**Done when:** Commit exists, or no commit is made because this task stopped at reviewed-plan stage.

---

## Phase Gates

| Phase | Artifact | Status |
|-------|----------|--------|
| CLARIFY | Session IDs + non-secret facts + local install diagnostics | DONE |
| DESIGN | Root-cause matrix and fix plan | IN PROGRESS |
| VERIFY | Decomposition review + independent impact review | PENDING |
| BUILD | Approved implementation only | BLOCKED UNTIL REVIEW |
| TEST | `install.py --doctor`, direct `sk.cmd`, Python tests if code changes | PENDING |
| REVIEW | Code/security review verdict | PENDING |
| LOOP-EVAL | Goal criteria evidence ledger | PENDING |
| COMMIT | Expected diff/commit or no-code closeout | PENDING |
