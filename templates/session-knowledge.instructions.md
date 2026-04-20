---
applyTo: "**/*"
---

# Session Knowledge (AUTO-LOADED)

> Auto-injected into every context. Start minimal — escalate only when needed.

## Before Starting a Task

Use the lightest fetch that covers the task complexity:

```bash
# Trivial tasks (rename, formatting, single-line fix) — skip or ultra-compact
python3 ~/.copilot/tools/briefing.py --wakeup          # ~170 tokens, titles only

# Moderate tasks (bug fix, small feature) — compact is usually enough
python3 ~/.copilot/tools/briefing.py --auto --compact  # ~500 tokens, top results

# Complex or unfamiliar tasks — request full detail only after compact reveals a hit
python3 ~/.copilot/tools/query-session.py --detail <id>  # Expand one entry by ID
python3 ~/.copilot/tools/briefing.py "task" --full       # Full detail ~3K tokens
```

Read the output before acting. It surfaces past mistakes and proven patterns.

## Progressive Escalation

Start with `--compact` or `--wakeup`. Escalate to `--full` or `--detail <id>` only
when the compact output shows a directly relevant past mistake or decision.
This keeps context lean — escalating to full detail for every task defeats the purpose.

## After Completing Work

Record what you learned (choose the most specific type):

```bash
python3 ~/.copilot/tools/learn.py --mistake "Title"   "Root cause and fix"  --tags "module,tech" --wing <wing> --room <room>
python3 ~/.copilot/tools/learn.py --pattern "Title"   "What works well"     --tags "module,tech" --wing <wing> --room <room>
python3 ~/.copilot/tools/learn.py --feature "Title"   "What was built"      --tags "module,tech" --wing <wing> --room <room>
python3 ~/.copilot/tools/learn.py --discovery "Title" "Codebase insight"    --tags "module,tech" --wing <wing> --room <room>
```

## Rules

- ❌ NEVER skip briefing before a non-trivial task
- ❌ NEVER skip learn after fixing a non-trivial bug
- ❌ NEVER load `--full` briefing when `--compact` shows no relevant hits
- ✅ Start minimal; escalate to `--detail <id>` for specific entries only
- ✅ Keep learn entries concise (1–3 sentences)
- ✅ Use `--wing` / `--room` to organise entries by domain

## Avoiding Context Bloat

If session-knowledge instructions are already installed at user/global level
(`~/.github/instructions/session-knowledge.instructions.md`), remove the project-level
copy to avoid loading these rules twice. One always-loaded copy is sufficient.
