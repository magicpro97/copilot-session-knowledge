---
name: session-knowledge
description: Search past session knowledge, get pre-task briefings, and record learnings. Use at session start, before new tasks, and after completing work.
---

# Session Knowledge Tools

> **Source**: [copilot-session-knowledge](https://github.com/magicpro97/copilot-session-knowledge)
> **Location**: `~/.copilot/tools/`
> **Data**: `~/.copilot/session-state/knowledge.db` (SQLite FTS5)

Tools that index all Copilot and Claude Code sessions into a searchable knowledge base.

## When to Use

### 🟢 MUST Use (Mandatory)

| Trigger | Tool | Command |
|---------|------|---------|
| **Starting a new task/feature** | `briefing.py` | `python3 ~/.copilot/tools/briefing.py --auto --compact` |
| **After fixing a non-trivial bug** | `learn.py` | `python3 ~/.copilot/tools/learn.py --mistake "Title" "What happened and fix"` |
| **After discovering a useful pattern** | `learn.py` | `python3 ~/.copilot/tools/learn.py --pattern "Title" "What works well"` |
| **After making architecture decision** | `learn.py` | `python3 ~/.copilot/tools/learn.py --decision "Title" "Why this approach"` |

### 🟡 Should Use (Recommended)

| Trigger | Tool | Command |
|---------|------|---------|
| **Investigating unfamiliar code area** | `query-session.py` | `python3 ~/.copilot/tools/query-session.py "search terms"` |
| **Before repeating a task done before** | `query-session.py` | `python3 ~/.copilot/tools/query-session.py "task description"` |
| **Reviewing past mistakes in an area** | `query-session.py` | `python3 ~/.copilot/tools/query-session.py --mistakes` |
| **Checking what patterns were established** | `query-session.py` | `python3 ~/.copilot/tools/query-session.py --patterns` |

### 🔴 Do NOT Use

- For simple/obvious tasks (e.g., formatting, typo fix)
- When task is completely new with no past sessions
- To replace reading actual source code — this is supplementary context

## Tools Reference

### `briefing.py` — Pre-task Context

```bash
# Auto-detect from git branch + recent commits (BEST for AI agents)
python3 ~/.copilot/tools/briefing.py --auto --compact

# Manual topic
python3 ~/.copilot/tools/briefing.py "database migration" --compact

# Full markdown output (for human reading)
python3 ~/.copilot/tools/briefing.py "user authentication flow"
```

### `query-session.py` — Search Knowledge Base

```bash
# Full-text search
python3 ~/.copilot/tools/query-session.py "API rate limiting"

# Knowledge categories
python3 ~/.copilot/tools/query-session.py --mistakes     # Past bugs and fixes
python3 ~/.copilot/tools/query-session.py --patterns     # Established patterns
python3 ~/.copilot/tools/query-session.py --decisions    # Architecture decisions

# Filter by source (copilot, claude, all)
python3 ~/.copilot/tools/query-session.py "search" --source claude
python3 ~/.copilot/tools/query-session.py --list --source copilot

# Filter and format
python3 ~/.copilot/tools/query-session.py "search" --limit 5
python3 ~/.copilot/tools/query-session.py "search" --verbose

# Session management
python3 ~/.copilot/tools/query-session.py --list         # All sessions
python3 ~/.copilot/tools/query-session.py --recent       # Recent activity
```

### `learn.py` — Record Knowledge

```bash
# Record a mistake (bug fix, wrong approach)
python3 ~/.copilot/tools/learn.py --mistake "Title" "What happened and fix" --tags "relevant,tags"

# Record a pattern (best practice, convention)
python3 ~/.copilot/tools/learn.py --pattern "Title" "What works well" --tags "tags"

# Record a decision (architecture choice)
python3 ~/.copilot/tools/learn.py --decision "Title" "Why this approach" --tags "tags"

# Record a tool discovery
python3 ~/.copilot/tools/learn.py --tool "Title" "Tool/config that was useful" --tags "tags"

# View stats
python3 ~/.copilot/tools/learn.py --stats
```

## Re-indexing

```bash
python3 ~/.copilot/tools/build-session-index.py          # Rebuild Copilot index
python3 ~/.copilot/tools/build-session-index.py --all    # Copilot + Claude Code
python3 ~/.copilot/tools/extract-knowledge.py             # Re-extract knowledge entries
python3 ~/.copilot/tools/sync-knowledge.py --auto         # Sync DBs across Win/WSL
```
