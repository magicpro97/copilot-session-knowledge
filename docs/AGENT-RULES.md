# Agent Rules

> Canonical reference for AI agent behavior rules in copilot-session-knowledge.
>
> These rules are **non-negotiable** and apply to every agent — main session, sub-agent, explore, task, or general-purpose. They are also enforced via `.github/copilot-instructions.md` (injected into every Copilot CLI session) and partially enforced at the hook level.

## Rule 1 — Investigate Before Acting

**NEVER modify code without reading it first.** Before any edit:

1. Use `grep` / `glob` / `view` / LSP tools to read the target file(s)
2. Understand the existing logic, dependencies, and callers
3. Check related files that may be affected by your change
4. Only then make your edit

```
❌ BAD:  User says "fix the search" → immediately edit query-session.py
✅ GOOD: User says "fix the search" → grep for search functions → view the code → check callers → edit
```

## Rule 2 — Briefing Before Complex Tasks

Before starting any task that touches >1 file or involves unfamiliar code:

```bash
python3 ~/.copilot/tools/briefing.py "your task description"
```

This surfaces past mistakes, proven patterns, and relevant decisions. Skip only for trivial changes (typo fix, renaming, formatting).

The `auto-briefing` hook fires automatically at `sessionStart` and writes a marker. If it has not fired (e.g., in a sub-agent context), run `briefing.py` manually before editing.

## Rule 3 — Test After Every Change

After modifying any Python file, run the relevant tests:

```bash
python3 test_security.py    # If touching: embed.py, sync-knowledge.py, watch-sessions.py, learn.py
python3 test_fixes.py       # If touching: any script
```

Do NOT mark a task complete until the relevant tests pass. If you hit a baseline failure, separate pre-existing breakage from regressions you introduced before proceeding.

Python validation runs through `run_all_tests.py`, but individual files use a mix of the custom `test()` helper and `unittest`/`test_*` style. The repo also has GitHub Actions CI and browse-ui quality gates (`pnpm typecheck`, `pnpm lint`, `pnpm format:check`, `pnpm test`, `pnpm build`); run the surfaces relevant to the files you changed.

## Rule 4 — Verify Before Committing

Before `git commit`:
1. `python3 -c "import ast; ast.parse(open('file.py').read())"` for every modified `.py` file
2. Run both test suites (Rule 3)
3. `git diff --stat` to review what you are about to commit

The `syntax_gate.py` preToolUse hook catches syntax errors in `edit`/`create` payloads before they land, but AST-parse verification before commit is a second safety net.

## Rule 5 — Sub-Agent Model Selection

When dispatching sub-agents via the `task` tool, use the appropriate model:

| Task type | Minimum model | Example |
|-----------|--------------|---------|
| Code generation | `claude-sonnet-4.6` | Writing/modifying Python scripts |
| Code review | `claude-sonnet-4.6` | Reviewing changes for bugs |
| Security audit | `claude-opus-4.6` | Auth, data handling, injection risks |
| Exploration | `claude-haiku-4.5` (default OK) | Finding files, reading code |
| Documentation | `claude-sonnet-4` or `haiku` | Writing docs, README |

```
❌ task(agent_type="general-purpose", prompt="fix the search bug...")   # default haiku model!
✅ task(agent_type="general-purpose", model="claude-sonnet-4.6", prompt="fix the search bug...")
```

## Rule 6 — No Guessing

- Don't assume table names — check with `sqlite3 ... ".tables"` or read `migrate.py`
- Don't assume function signatures — use `grep` or LSP to verify
- Don't assume file paths — use `glob` to find them
- If unsure about behavior, write a small test or read the source

## Rule 7 — Docs Output Quality

Agent-authored docs, tentacle handoffs, operator reports, and research outputs must
distinguish four layers. Mixing layers silently or presenting interpretation as fact
is a documentation defect.

| Layer | What it contains | Marking convention |
|-------|-----------------|-------------------|
| **Facts** | Verified, reproducible data: row counts, timestamps, test results, git refs | State directly; cite the source or command that produced it |
| **Interpretation** | Reasoning based on facts: patterns, risks, root causes, inferences | Qualify explicitly: "suggests", "indicates", "likely" |
| **Actions** | Concrete next steps: commands to run, tickets to file, follow-up tentacles | Use imperative; include the executable command |
| **Verification evidence** | Proof that work was done: test log output, CI status, measured diffs | Link or inline the evidence; do not claim verified without it |

**Rules:**

1. Do not present interpretation as fact. Every non-trivial causal claim must be qualified.
2. Every action item must be executable — include the actual command or URL.
3. Every verification claim must include evidence (test log excerpt, CI link, git ref, or pass/fail count).
4. Keep operator/research docs concise. Move lengthy context into appendices or collapsible sections.
5. Operator/research outputs (tentacle handoffs, retro summaries, knowledge-health reports, research-pack summaries) must follow all four layers. Contributor docs (`CONTRIBUTING.md`) keep their existing concise tone.

## Rule 8 — Tentacle Execution Obligations

When running inside a tentacle (dispatched by the orchestrator via `tentacle.py`):

1. **Read the bundle first** — before any edit, read `manifest.json`, `session-metadata.md`, `recall-pack.json`, and `instructions.md` from the bundle path provided in the dispatch prompt.
2. **Stay in scope** — only edit files listed in the tentacle's declared scope. Any edit outside that scope requires a scope escalation note written to the handoff before proceeding.
3. **Mark todos as you complete them** — after completing each task:
   ```bash
   python3 ~/.copilot/tools/tentacle.py todo <tentacle-name> done <index>
   ```
4. **No git operations** — do NOT run `git commit` or `git push`; the orchestrator owns all git operations.
5. **Write a structured handoff before stopping**:
   ```bash
   python3 ~/.copilot/tools/tentacle.py handoff <tentacle-name> "<summary>" \
     --status <STATUS> [--changed-file <path>] --learn
   ```
   Use one of `DONE`, `BLOCKED`, `TOO_BIG`, `AMBIGUOUS`, or `REGRESSED` for `<STATUS>`. Add one `--changed-file` per modified file; omit it when no files changed. The handoff must list: which rules changed, which file is source of truth for each rule, and any remaining ambiguity.
6. **Review-ready handoff** — the handoff must include enough detail for an independent reviewer to verify all claims independently.

> The orchestrator runtime injects the core tentacle workflow (bundle, scope, todo, handoff, and git-operation guidance) per tentacle. The canonical full text lives here.

---

## Orchestrator Goal-Loop

When acting as an orchestrator with an active goal, the lifecycle is iterative, not linear. After all tentacle handoffs are collected and verification gates pass, the orchestrator evaluates the goal before closing:

1. **State success criteria upfront** — before dispatching any tentacle, write the goal's success criteria explicitly in `CONTEXT.md` or a shared artifact. Weak criteria ("make it work") prevent clean goal evaluation; strong criteria ("all 137 tests pass, benchmark score ≥ 90") enable independent verification.
2. **Evaluate after each Verify phase** — once Build → Lint → Test → Review gates pass, evaluate whether the overarching goal is met. Record evidence:
   ```bash
   python3 ~/.copilot/tools/tentacle.py verify <name> "<check-command>" --label "goal-eval"
   ```
3. **Loop if unmet** — if the goal is not satisfied, return to Phase 1 (Plan). Create new tentacles scoped to the remaining gap. Do not re-open completed tentacles; create new ones.
4. **Close only when verified** — proceed to commit and close only when goal success criteria are verifiably met and evidence is recorded.
5. **Sub-agents do not loop** — sub-agents report via handoff and stop. The orchestrator reads handoff statuses, evaluates the goal, and decides whether to loop or close. Never dispatch sub-agents with an implicit expectation that they will self-continue.

**Typical pattern:**
```
Plan → Execute tentacles → Verify gates → Goal Eval
                                              ↓ not met
                               Plan new tentacles for remaining gaps
                                              ↓ met
                               Commit + Close
```

This is the **loop-until-verified** semantic applied at the orchestrator level. At the task level, Karpathy Guideline 4 applies the same principle: define success criteria, loop until verified.

---

## Hook Enforcement

These rules are partially enforced at the tool level. All hooks **fail-open**: if a hook itself crashes or is unavailable, the guarded operation proceeds rather than blocking the agent. Hook failures are logged but do not interrupt work.

| Rule | Hook | Enforcement |
|------|------|-------------|
| Briefing before edits | `enforce-briefing` (preToolUse) | Blocks `edit`/`create`/`bash` until briefing marker is present |
| Learn after code edits | `enforce-learn` (preToolUse) | Blocks `git commit` / `task_complete` after ≥3 code edits without `learn.py` |
| Tentacle for broad changes | `tentacle-enforce` (preToolUse) | Blocks edits when ≥3 files across ≥2 modules without tentacle setup |
| No git ops in sub-agents | `subagent-git-guard` (preToolUse + git hooks) | Blocks `git commit`/`git push` while dispatched-subagent marker is active |
| Syntax errors | `syntax-gate` (preToolUse) | Blocks `.py` edit/create payloads that fail `py_compile` |
| Tentacle todo progress | runtime injection | Orchestrator expects `tentacle.py todo done` calls as tasks complete |
| Tentacle handoff | runtime injection | Orchestrator expects `tentacle.py handoff` before agent stops |

> Full hook rule inventory: **[docs/HOOKS.md](HOOKS.md)**

---

## Copilot CLI Enforcement Surface

The file `.github/copilot-instructions.md` injects these rules into every Copilot CLI session context. That file is the **runtime enforcement surface** — keep it in sync with this document. Changes to agent rules should be reflected in both places.

For Claude Code, equivalent guidance lives in `CLAUDE.md` (project root or user home) and `.claude/` instruction files.
