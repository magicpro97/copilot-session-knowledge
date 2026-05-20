---
name: multi-agent-workflow
description: Multi-agent orchestration rules with tiered model selection and multi-layer verification. MANDATORY for all code tasks dispatching sub-agents.
---

# Multi-Agent Workflow Skill

> **⚠️ BẮT BUỘC**: Skill này áp dụng cho MỌI task dispatch sub-agents.
> Mục đích: Đảm bảo code quality qua model selection đúng + verification nhiều lớp.

## Tại sao cần Multi-Agent?

| Vấn đề Single-Agent | Multi-Agent giải quyết |
|---------------------|----------------------|
| Context bloat → quality giảm | Mỗi agent có context riêng, focused |
| 1 agent miss bugs | Multiple perspectives, cross-check |
| Default model (haiku) → code kém | Tiered models: premium cho code/QA |
| Review sau push = vô nghĩa | Verification pipeline TRƯỚC khi finalize |
| Merge khi còn review comment | Fix/resolve comments trên đúng PR head trước merge |

## Model Selection Matrix (BẮT BUỘC)

### By Agent Type

| Agent | Minimum Model | Recommended | Lý do |
|-------|--------------|-------------|-------|
| `lambda-developer` | `claude-sonnet-4.6` | `claude-sonnet-4.6` | Code gen cần reasoning mạnh |
| `test-engineer` | `claude-sonnet-4.6` | `claude-sonnet-4.6` | Edge cases, complex mocking |
| `frontend-developer` | `claude-sonnet-4.6` | `claude-sonnet-4.6` | Component logic, hooks |
| `code-reviewer` | `claude-sonnet-4.6` | `claude-opus-4.6` | Catch subtle bugs |
| `dynamodb-reviewer` | `claude-sonnet-4.6` | `claude-sonnet-4.6` | Complex query patterns |
| `architecture-analyst` | `claude-sonnet-4.6` | `claude-opus-4.6` | Deep reasoning |
| `code-analyst` | `claude-sonnet-4.6` | `claude-sonnet-4.6` | 5-lens analysis |
| `explore` | `claude-haiku-4.5` | `claude-haiku-4.5` | Read-only, speed matters |
| `task` (build/test) | `claude-haiku-4.5` | `claude-haiku-4.5` | Command execution |
| `doc-generator` | `claude-sonnet-4` | `claude-sonnet-4` | Formatting, not reasoning |

### By Task Risk Level

| Risk | Examples | Model | Verification Layers |
|------|----------|-------|-------------------|
| 🟢 Low | Docs, comments, formatting | haiku/sonnet-4 | 1 (self-check) |
| 🟡 Medium | Feature code, tests | sonnet-4.6 | 3 (build → test → review) |
| 🔴 High | Auth, PII, data migration | sonnet-4.6 + opus | 4 (+ specialist) |
| ⚫ Critical | Prod deploy, schema change | opus-4.6 | 5 (+ cross-check + human) |

## Verification Pipeline

### Minimum (3 Layers) — Cho mọi code task

```
┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│  L1: BUILD   │────▶│ L2: TEST     │────▶│ L3: REVIEW   │
│  sonnet-4.6  │     │ verify pass  │     │ sonnet-4.6   │
│  code gen    │     │ compile OK   │     │ code-reviewer │
└──────────────┘     └──────┬───────┘     └──────┬───────┘
                            │                     │
                        FAIL? Fix              Issues?
                        and retry              Auto-fix
                                               loop ×5
```

### Extended (5 Layers) — Cho high-risk code

```
L1-L3 (same as above)
        │
        ▼
┌──────────────┐     ┌──────────────┐
│ L4: SPECIALIST│────▶│ L5: INTEGRATE│
│ opus/sonnet  │     │ full suite   │
│ DynamoDB/sec │     │ regression   │
└──────────────┘     └──────────────┘
```

### Cross-Check (cho critical decisions)

```
┌── Agent A (sonnet-4.6) ──▶ Solution A ──┐
│                                          ├──▶ Compare & Merge
└── Agent B (opus-4.6)   ──▶ Solution B ──┘
```

## Confidence-Triggered Escalation

Bất kỳ output/plan/routing nào có confidence `< 1.0` đều chưa được coi là quyết định.
Orchestrator không được nhận output đó làm final, không được implement, delete, merge, hoặc
close task dựa trên output đó.

Khi confidence `< 1.0`:

1. Tách ý mơ hồ/nhiễu thành các câu hỏi nhỏ độc lập.
2. Dispatch `research-planner`, `doublecheck`, hoặc reviewer độc lập trên model mạnh nhất
   sẵn có (`claude-opus-4.7` nếu có; nếu không thì opus-class mới nhất).
3. Ghi evidence, rejected alternatives, và remaining gaps.
4. Chỉ tiếp tục khi synthesized confidence = `1.0` hoặc user override rõ ràng được ghi lại.

Rule này áp dụng cho mọi wave, kể cả khi task không phải security. Security vẫn dùng opus,
nhưng low-confidence decision cũng phải escalate lên opus-class research/validation.

### PR Evidence Gate

For PR-driven work, verify the exact commit being promoted:

1. Inspect CI and review threads for the current PR head.
2. Fix or explicitly resolve every substantive review comment before merge.
3. Re-run the relevant build/test/review gate after the final fix.
4. After merge, verify the target branch and build any user-facing release artifact
   from the merged commit, not from a stale PR branch.

## Workflow Presets

### 1. API Feature (`/fleet` compatible)

```
Wave 1 (parallel):
  @lambda-developer (model: sonnet-4.6) → handler + mapping + repository
  @test-engineer (model: sonnet-4.6) → unit tests (wait for handler skeleton)

Wave 2 (after Wave 1):
  Run tests: cd backend && yarn test --maxWorkers=1

Wave 3 (after tests pass):
  @code-reviewer (model: sonnet-4.6) → review all changes
  Auto-fix loop until CLEAN
```

### 2. Frontend Feature

```
Wave 1: @frontend-developer (model: sonnet-4.6) → screen + components
Wave 2: @test-engineer (model: sonnet-4.6) → component tests
Wave 3: @code-reviewer (model: sonnet-4.6) → review
```

### 3. Cross-Layer Feature

```
Wave 1 (parallel):
  @lambda-developer (model: sonnet-4.6) → backend
  @frontend-developer (model: sonnet-4.6) → frontend

Wave 2: @test-engineer (model: sonnet-4.6) → all layers

Wave 3 (parallel):
  @code-reviewer (model: sonnet-4.6) → code quality
  @dynamodb-reviewer (model: sonnet-4.6) → DynamoDB patterns

Wave 4: Integration test suite
```

### 4. PR Review (Multi-perspective)

```
Parallel:
  @code-reviewer (model: opus-4.6) → skeptical quality + security
  @dynamodb-reviewer (model: sonnet-4.6) → DynamoDB (if applicable)
  @code-analyst (model: sonnet-4.6) → 5-lens analysis (if complex)

Synthesize: Main agent merges findings, verifies review comments are resolved on
the current PR head, then re-runs the merge gate
```

### 5. Security Audit (Cross-Check)

```
@code-reviewer (model: opus-4.6) → security §4 audit
@code-reviewer (model: sonnet-4.6) → independent security check
Compare results → flag discrepancies for human review
```

### 6. Low-Confidence Decision

```
Wave N output confidence < 1.0
  → split ambiguity into atomic questions
  → @research-planner (model: opus-4.7) gathers evidence
  → @code-reviewer/@qa-auditor (model: opus-4.7) validates
  → continue only when confidence = 1.0 or explicit override exists
```

## Implementation Template

Khi dispatch agents, dùng format này:

```python
# Step 1: BUILD
task(
    agent_type="lambda-developer",
    model="claude-sonnet-4.6",      # ← BẮT BUỘC cho code
    name="build-feature",
    description="Implement handler",
    prompt="...",
    mode="background"
)

# Step 2: TEST (after build)
# Run tests in bash, verify pass

# Step 3: REVIEW
task(
    agent_type="code-reviewer",
    model="claude-sonnet-4.6",      # ← BẮT BUỘC cho QA
    name="review-feature",
    description="Review changes",
    prompt="Review files: ... Focus on logic bugs, security, conventions.",
    mode="background"
)
```

## Anti-Patterns (TRÁNH)

| ❌ Anti-pattern | ✅ Correct |
|----------------|-----------|
| Dùng haiku cho code gen | Dùng sonnet-4.6+ |
| Dùng haiku cho code review | Dùng sonnet-4.6+ |
| Push trước, review sau | Review → CLEAN → push |
| Merge khi còn review comment | Fix/resolve review threads → re-test exact head |
| 1 agent làm hết | Decompose: build → test → review |
| Skip verification layers | Luôn ≥3 layers cho code |
| Review xong không fix | Auto-fix loop đến CLEAN |
| Không specify model | Luôn specify model= parameter |

## Monitoring

```bash
/tasks                    # View background agents
/fleet                    # Enable parallel execution
/context                  # Check token usage
```
