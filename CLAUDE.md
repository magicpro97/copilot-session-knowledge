@AGENTS.md


## Pre-Task Briefing (BẮT BUỘC)

> **⚠️ MANDATORY**: Before starting a new task, run briefing to get context from past sessions.
> After fixing bugs or completing tasks, record learnings.
> Details: `.github/skills/session-knowledge/SKILL.md`

```bash
# Before starting a task — get context from past sessions
sk briefing --auto --compact
# Fallbacks: macOS/Linux `python3 ~/.copilot/tools/briefing.py --auto --compact`;
# Windows PowerShell `python "$env:USERPROFILE\.copilot\tools\briefing.py" --auto --compact`

# After fixing a bug — record the mistake
sk learn --mistake "Title" "What happened and fix" --tags "relevant,tags"

# After completing work — record pattern/decision
sk learn --pattern "Title" "What works well" --tags "tags"
```
