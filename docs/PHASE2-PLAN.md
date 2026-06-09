# Phase 2: OpenCode Bridge — Gap Analysis & Implementation Plan

## Overview

Make the `copilot-tools-bridge` plugin work 100% with opencode (not just Copilot CLI).
Phase 1 (PR #972) established the bridge foundation with 8 hooks and 20 tests.
Phase 2 closes the remaining gaps to achieve feature parity with the Copilot CLI runtime.

---

## Architecture (Current State)

```
opencode runtime
  │
  ├── session.start ──────► hook_runner sessionStart
  ├── session.stop ───────► hook_runner sessionEnd
  ├── tool.execute.before ──► hook_runner preToolUse
  ├── tool.execute.after ───► hook_runner postToolUse
  ├── tool.use ────────────► hook_runner preToolUse
  ├── chat.message ────────► hook_runner userPromptSubmitted
  ├── task ────────────────► hook_runner preToolUse (toolName: "task")
  └── event ──────────────► session.created → sessionStart
                            session.idle → sessionEnd
                            session.error → errorOccurred
```

**Current bridge source**: `opencode-plugin/copilot-tools-bridge.ts` (255 lines, 8 hooks)
**Hook runner**: `hooks/hook_runner.py` (356 lines, dispatches to 38 rules)
**Tests**: `tests/test_opencode_bridge.py` (310 lines, 20 tests)

---

## Gaps Analysis

### G1: Multi-line JSON output from hook_runner (P1)

`hook_runner.py` prints **one JSON object per line** (line 323 `print(json.dumps(result))`).
When multiple rules return results for the same event, stdout contains multiple JSON objects.
Bridge does `JSON.parse(result)` on the entire blob — fails on multi-line output.

**Affects**: `chat.message` (→ userPromptSubmitted), `tool.execute.after` (→ postToolUse)

### G2: `additionalContext` format mismatch (P1)

`hooks/rules/common.py`'s `context()` returns `{"additionalContext": "string"}`. Bridge checks `Array.isArray(parsed.additionalContext)` which is always false for a string. Briefing/AutoBriefing context injection is silently broken.

### G3: `mapToolName` incomplete (P1)

| opencode tool | Current map | Rules expect | Fix |
|--------------|------------|-------------|-----|
| `read` | `"read"` (unchanged) | `"view"` | Add `"read" → "view"` |
| `write` | `"create"` ✓ | `"create"` | OK |
| `apply_patch` | `"apply_patch"` (unchanged) | `"edit"` | Add `"apply_patch" → "edit"` |
| `skill` | `"skill"` (unchanged) | `"skill"` | OK (but no handler) |
| `edit` | `"edit"` ✓ | `"edit"` | OK |

### G4: `toolResult` too sparse (P1)

Rules expect `resultType: "success"|"error"`, `exitCode`, `isError` in `toolResult`.
Bridge only sets `title`, `output`, `filePath`.

### G5: Missing `cwd` in tool events (P1)

`ConstitutionGateRule` needs `data.cwd` to resolve `.copilot/constitution.md`.
Bridge doesn't pass it.

### G6: Missing session lifecycle events (P3)

| opencode hook | Maps to | Status |
|--------------|---------|--------|
| `experimental.session.compacting` | `preCompact`/`postCompact` | Not handled |
| Subagent lifecycle | `agentStop`/`subagentStop` | Not handled |

### G7: Missing tool coverage (P2)

| Tool | Handler status |
|------|---------------|
| `skill` | Not handled (SkillUsageRule, SkillNudgeRule silent) |
| `question` | Not handled |
| `webfetch`/`websearch` | Not handled (not critical) |
| `lsp` | Not handled (non-granular permission, low priority) |

### G8: Tool argument normalization (P5)

Rules expect Copilot CLI field names in `toolArgs`:
- `old_str` / `new_str` (edit tool changes)
- `file_text` (create tool full content)
- `path` (file path — `hook_runner.py` reads both `path` and `filePath`, but many rules read `toolArgs.path` directly; the bridge must pass `filePath` from opencode args under both keys for full coverage)

Need to verify opencode's actual parameter names and add mapping.

### G9: Output protocol gaps (P4)

| Hook runner output | Bridge support |
|-------------------|---------------|
| `{"permissionDecision": "deny", ...}` | ✓ (throws on deny) |
| `{"additionalContext": "..."}` | Buggy (string vs array) |
| `{"modifiedPrompt": "..."}` | Not handled |
| `{"sessionSummary": "..."}` | Not handled |

### G10: Rules compatibility audit

| Tier | Count | Confidence | Criteria |
|------|-------|-----------|----------|
| Works now | 3 rules | High | Generic logic, no CLI-specific shapes |
| P1 fixes unlocks | 9 rules | Med-High | Need `additionalContext`, cwd, resultType, read→view |
| P3+ events unlocks | 11 rules | Medium | Need lifecycle events + agentStop |
| P5 arg mapping | 5 rules | Low | Need CLI field name mapping |
| Deeply tied to CLI | 10 rules | Low | Major refactor needed |

---

## Priority Plan

### P1 — Critical Fixes (NOW)

| # | Fix | Confidence | Files |
|---|-----|-----------|-------|
| P1.1 | Handle multi-line JSON output | High | `copilot-tools-bridge.ts` |
| P1.2 | Fix `additionalContext` string vs array | High | `copilot-tools-bridge.ts` |
| P1.3 | `mapToolName` read→view, apply_patch→edit | High | `copilot-tools-bridge.ts` |
| P1.4 | Add `resultType` to `toolResult` | High | `copilot-tools-bridge.ts` |
| P1.5 | Add `cwd` to tool events | High | `copilot-tools-bridge.ts` |
| P1.6 | Update tests | High | `tests/test_opencode_bridge.py` |

Unlocks 11 rules: IntegrityRule, SkillNudgeRule, SkillImprovementAdvisorRule,
AutoBriefingRule, NewFileAdvisoryRule, BlockEditDistRule, SubagentGitGuardRule,
PostCommitBriefingRule, TrackEditsRule, WorkflowStateRule, EnforceBriefingRule.

### P2 — Tool Coverage (NEXT)

- Add `skill` tool handler (maps to hook_runner `"skill"` toolName)
- Add `question` handler (advisory-only)
- Verify `webfetch`/`websearch` don't need rules (likely not critical)

### P3 — Session Lifecycle Events

- Add `agentStop`/`subagentStop` handler
- Map `experimental.session.compacting` → `preCompact`/`postCompact`

### P4 — Output Protocol

- Handle `modifiedPrompt` in `chat.message`
- Handle `sessionSummary` in appropriate hooks
- Aggregate structured output from multi-rule results

### P5 — Tool Argument Normalization

- Map opencode edit args → `old_str`/`new_str` (SyntaxGateRule, BlockUnsafeHtmlRule, etc.)
- Map opencode write args → `file_text` (FileSizeAdvisoryRule)
- Need to verify actual opencode parameter names first (research task)

---

## Rules Compatibility Heatmap

```
                                     Now   P1    P3    P5   Deep
IntegrityRule                        ████
SkillNudgeRule                       ████
SkillImprovementAdvisorRule          ████
AutoBriefingRule                     ███░  ████
NewFileAdvisoryRule                  ███░  ████
BlockEditDistRule                    ███░  ████
PnpmLockfileGuardRule                ███░  ████
SubagentGitGuardRule                 ███░  ████
PostCommitBriefingRule               ███░  ████
TrackEditsRule                       ███░  ████
WorkflowStateRule                    ███░  ████
EnforceBriefingRule                  ██░░  ███░
ReadBeforeEditRule                   ██░░  ███░
EpisodeBatcherRule                   ██░░  ███░
ErrorFixNudgeRule                    ██░░  ███░
SessionEndRule                       ██░░  ███░
RecurrenceDetectorRule               ██░░  ███░
SessionCompilerRule                  ██░░  ███░
UserPromptContextRule                ██░░  ███░
SkillNudgeRule (skill)               ██░░  ███░
LoopDetectorRule                     ██░░  ███░   ███░
ReadTrackerRule                      █░░░  ██░░
EnforceLearnRule                     █░░░  ██░░
TentacleEnforceRule                  █░░░  ██░░
VerificationGateRule                 █░░░  █░██
ConfidenceGateRule                   █░░░  █░██
ConstitutionGateRule                 █░░░  █░██
SyntaxGateRule                       █░░░       ████
BlockUnsafeHtmlRule                  █░░░       ████
FileSizeAdvisoryRule                 █░░░       ████
AutoBugDetectorRule                  █░░░       ████
TestReminderRule                     █░░░       ███░
TentacleSuggestRule                  █░░░       ██░░
LearnReminderRule                    █░░░  ██░░
TokenTrackerRule                     █░░░  ██░░
ErrorKBRule                          █░░░  ██░░
SubagentStopRule                     ░░░░  ████
CompactLifecycleRule                 ░░░░  ████
SkillUsageRule                       ░░░░  ███░
```

Legend: █ = works, ░ = gap

---

## Research Notes

- `chat.message` and `task` hooks are NOT in public plugin API docs at opencode.ai/docs/plugins
  — may be internal/experimental, could break on upgrade
- opencode 1.16.2 installed at `/opt/homebrew/bin/opencode`, Bun 1.3.9
- Session knowledge DB at `~/.local/share/opencode/opencode.db`
  (separate from tools' `~/.copilot/session-state/knowledge.db`)
- 50+ hooks available in opencode plugin API, we only use ~9
- `write` tool uses `edit` permission (not `create`) in opencode's permission system
- Rules checked against `common.py` output protocol: `deny()`, `context()`, `info()`,
  `session_summary()`, `modified_prompt()`
