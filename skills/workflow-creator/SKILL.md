---
name: workflow-creator
description: >
  Create a phased development workflow (WORKFLOW.md) with quality gates for any project.
  Use when setting up a new project, improving development process, or when the user mentions
  "create workflow", "setup phases", "quality gates", "development process", "CI pipeline",
  or wants a structured multi-phase approach to UI/feature changes.
---

# Workflow Creator

Generate a `WORKFLOW.md` file that defines a phased development lifecycle with quality gates,
phase dependencies, and evidence requirements.

## When to Use

- Setting up a new project that needs a structured development process
- User mentions "create workflow", "quality gates", "development phases", or "CI pipeline"
- AI agents are skipping steps (testing, review) or working out of order
- A feature involves multiple stages (design → build → test → QA) that must be gated

## Why Phased Workflows Matter

Without phases, AI agents make common mistakes:
- Code before understanding requirements → rework
- Skip design verification → ship broken UI
- Skip testing → broken in production
- No visual QA → pixel-level bugs users notice
- No review gate → architecture drift

A phased workflow with **blocking gates** prevents these by enforcing order.

## Workflow Template

Every workflow follows this pattern:

```
Phase 0 → Phase 1 → Phase 2 → ... → Phase N
         ↑ gate ↑  ↑ gate ↑        ↑ gate ↑
```

**Gates are BLOCKING** — cannot proceed until previous phase produces its required artifact.

### Base Phases (adapt for your project)

| Phase | Name | Purpose | Artifact |
|-------|------|---------|----------|
| 0 | CLARIFY | Make requirements implementation-ready | Spec Health Report |
| 1 | DESIGN | Generate visual/technical design | Design files or specs |
| 2 | VERIFY | Review design before coding | Review verdicts (PASS/FAIL) |
| 3 | BUILD | Implement code | Compiling code + passing tests |
| 4 | TEST | Functional verification | Test results (all pass) |
| 5 | REVIEW | Code quality check | Review approval |
| 6 | QA | Visual/manual verification | Screenshots/evidence |
| 7 | COMMIT | Ship it | Clean git commit |

### Customization by Project Type

**Backend/API**: Drop DESIGN + QA, strengthen TEST with integration + load tests.
**Mobile/Desktop**: Keep all phases, add per-platform QA.
**Libraries**: Drop DESIGN/VERIFY, strengthen REVIEW (API surface), add DOCS phase.
**Data pipelines**: Replace DESIGN with SCHEMA REVIEW, QA with DATA VALIDATION.

## Creating a Workflow

### Step 1: Understand the Project

Examine project type, existing CI/CD, test infrastructure, and agents.

### Step 2: Select Phases

Each phase needs: purpose, input, activities, gate artifact, owner, skip conditions.

### Step 3: Define Blocking Wait Rule

```markdown
### ⛔ BLOCKING WAIT Rule
NEVER start Phase N+1 while Phase N is running.
Parallelism ONLY within a single phase (e.g., 3 test suites in parallel).
```

### Step 4: Define Phase Gate Evidence Table

Map each phase to its required evidence, verification method, and when skipping is blocked.

### Step 5: Add Self-Check Protocol

At every phase transition, verify artifacts exist and meet quality criteria.

## Integration

- Store as `.github/WORKFLOW.md`
- Reference from `AGENTS.md` and project instructions
- Hooks enforce phases (e.g., `commit-gate.sh`)
- Conductor agent uses workflow as playbook

### Deploying via project profiles

Instead of building a workflow from scratch, use `setup-project.py --profile` or `install-project-hooks.py --profile` to install a pre-built hook bundle and starter `WORKFLOW.md`:

```bash
python3 ~/.copilot/tools/setup-project.py --profile python      # Python: TDD, test-reminder, commit-gate
python3 ~/.copilot/tools/setup-project.py --profile typescript  # TypeScript: coding-standards, test-reminder
python3 ~/.copilot/tools/setup-project.py --profile mobile      # Mobile: architecture-guard, QA phase
python3 ~/.copilot/tools/setup-project.py --profile fullstack   # Full-stack: architecture-guard, session-banner

# Hooks only (no full project setup)
python3 ~/.copilot/tools/install-project-hooks.py --profile python --workflow  # hooks + WORKFLOW.md
python3 ~/.copilot/tools/install-project-hooks.py --list-profiles              # show available profiles

# Build a custom profile and deploy it
python3 ~/.copilot/tools/profile-builder.py --name myteam \
  --hooks dangerous-blocker.sh commit-gate.sh \
  --phases CLARIFY BUILD TEST COMMIT                             # creates presets/myteam.json
python3 ~/.copilot/tools/profile-export.py --profile myteam --output myteam.json   # export to share
python3 ~/.copilot/tools/profile-import.py --file myteam.json                      # import on another machine
python3 ~/.copilot/tools/setup-project.py --profile myteam                         # deploy
```

Profile bundles are defined in `presets/` (`default`, `python`, `typescript`, `mobile`, `fullstack`).
Use `--workflow` with `install-project-hooks.py` to also generate a starter `WORKFLOW.md`.

## Anti-Patterns

| Anti-Pattern | Why It Fails |
|-------------|-------------|
| Too many phases (>9) | Overhead kills velocity |
| No skip conditions | Trivial changes take forever |
| Soft gates ("should") | AI rationalizes skipping |
| No evidence requirements | "Done" without proof |
| Phase overlap allowed | Defeats gate purpose |

<example>
**Project:** React dashboard (TypeScript + Jest + Playwright)

**Selected phases:** CLARIFY → DESIGN → VERIFY → BUILD → TEST → REVIEW → QA → COMMIT

**Customizations:**
- Skipped DESIGN for bug-fix tasks (`skip_if: bug_fix: true`)
- TEST requires both Jest (unit) and Playwright (e2e) passing
- QA captures Playwright screenshots as visual evidence
- COMMIT blocked by `commit-gate.sh` until QA artifact exists

**Gate table (excerpt):**
| Phase | Evidence | Command |
|-------|---------|---------|
| BUILD | TypeScript compiles | `npx tsc --noEmit` |
| TEST | All tests green | `yarn test --ci` |
| QA | Screenshot in `qa-evidence/` | Playwright visual run |

**Output:** `.github/WORKFLOW.md` (referenced from `AGENTS.md`)
</example>
