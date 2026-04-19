# Handoff Notes

## [2026-04-19 16:01 UTC]

Added two new skills: code-reviewer (skeptical signal-over-noise code review, 3-phase Orient/Investigate/Report, severity levels, hard exclusion of style comments) and task-step-generator (STEPS.md generation grounded in phased workflow, fills gap between single-prompt and full tentacle orchestration). Both pass validate-skill.py and lint-skills.py (20/20 clean). Added to setup-project.py INSTALL_ITEMS and docs/SKILLS.md. step-file-template.md in references/ provides annotated template with done-condition guide and phase selection table. No new executor runtime invented - just SKILL.md surfaces and a reference template.

## [2026-04-19 16:01 UTC]

Key learnings: (1) validate-skill.py treats missing <example> tags as ERROR (not warning) — always include at least 1; (2) heavy-handed directives (MUST/ALWAYS/NEVER) count against a limit of 5 — use reasoning instead; (3) lint-skills.py auto-detects CLI version and parses schemas from app.js — clean exit 0 is the target; (4) setup-project.py INSTALL_ITEMS is the canonical list for skill deployment — add new skills there; (5) step-file concept fills a genuine gap between single-prompt tasks and full tentacle orchestration for 3+ module work.

## [Post-review fix]

**Bug fixed:** `setup-project.py install_skills()` only copied `SKILL.md`, leaving any `references/` subdirectory absent in deployed projects. `task-step-generator/SKILL.md` references `references/step-file-template.md` — that file was never deployed, causing a dangling reference.

**Fix (surgical, two files):**
1. `setup-project.py` `install_skills()` — after copying `SKILL.md`, now iterates `<skill>/references/` (if present) and copies each file into `.github/skills/<skill>/references/`. All skills with references/ dirs (agent-creator, hook-creator, tentacle-orchestration, task-step-generator, workflow-creator) now deploy their reference files automatically.
2. `validate-skill.py` — added check (WARNING, not ERROR) that any relative `references/<file>` mention in a SKILL.md has the file present locally. Uses negative lookbehind to avoid matching full paths like `shared/references/` or `~/.../references/`. Catches this class of bug at author time going forward.

**Tests:** 9/9 security, 65/65 fixes — no regressions. `task-step-generator` passes validate-skill.py clean (exit 0).

**Learning:** When adding a skill that ships a `references/` template file, `install_skills()` must deploy those files too — SKILL.md references are relative and must resolve in the deployed location.
