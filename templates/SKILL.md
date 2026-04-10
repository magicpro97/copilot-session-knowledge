# Session Knowledge

You have access to a knowledge base built from past Copilot and Claude sessions.
Use it to avoid repeating mistakes, reuse proven patterns, and recall past decisions.

All tools are Python scripts in `~/.copilot/tools/` (cross-platform: `~` = home directory).

## When to Use

- **Starting a complex task** → run `briefing.py` to check for relevant past experience
- **Hitting an error** → search for the error message, someone may have solved it before
- **Making a design decision** → check `--decisions` for past architectural choices
- **Unsure about a tool/config** → search for the tool name

**Skip** when the task is trivial (renaming a variable, formatting code, etc.)

## Core Commands

### 1. Briefing (recommended first step)

```bash
python3 ~/.copilot/tools/briefing.py "your task description"    # Compact ~500 tokens
python3 ~/.copilot/tools/briefing.py "your task" --full         # Full detail ~3K tokens
python3 ~/.copilot/tools/briefing.py --auto                     # Auto-detect from git/plan
```

Output includes: relevant mistakes to avoid, patterns to follow, related past work.
**Read entry IDs in the output** — use them to drill down.

### 1b. Sub-agent Context Injection

When launching sub-agents (explore, task, general-purpose) for complex tasks,
inject knowledge context into their prompts:

```bash
python3 ~/.copilot/tools/briefing.py "task description" --for-subagent
```

This outputs a compact `[KNOWLEDGE CONTEXT]` block (~200 tokens) designed to be
embedded directly into sub-agent prompts. Example workflow:

1. Run `briefing.py "fix Docker networking" --for-subagent` → get context block
2. Prepend the context block to the sub-agent's prompt
3. Sub-agent now knows past mistakes/patterns without querying KB directly

### 2. Search

```bash
python3 ~/.copilot/tools/query-session.py "search terms"              # Compact results
python3 ~/.copilot/tools/query-session.py "docker error" --verbose    # Full content
python3 ~/.copilot/tools/query-session.py "spring" --source copilot   # Filter by agent
python3 ~/.copilot/tools/query-session.py "gradle" --type research    # Filter by doc type
```

### 3. Drill Down (use entry IDs from search/briefing results)

```bash
python3 ~/.copilot/tools/query-session.py --detail <id>     # Full content of one entry
python3 ~/.copilot/tools/query-session.py --context <id>    # Entry + same-session entries
python3 ~/.copilot/tools/query-session.py --related <id>    # Entry + graph connections
```

### 4. Browse by Category

```bash
python3 ~/.copilot/tools/query-session.py --mistakes    # Past errors and how they were fixed
python3 ~/.copilot/tools/query-session.py --patterns    # Reusable best practices
python3 ~/.copilot/tools/query-session.py --decisions   # Architecture/design choices
python3 ~/.copilot/tools/query-session.py --tools       # Tool configs and usage notes
```

### 5. Knowledge Graph

```bash
python3 ~/.copilot/tools/query-session.py --graph "topic"   # Visual: entries + connections
```

Shows how knowledge entries relate to each other:
- **RESOLVED_BY** — a mistake linked to the pattern/tool that fixed it
- **TAG_OVERLAP** — entries sharing similar tags (related domain)
- **SAME_SESSION** — entries discovered together in one session
- **SAME_TOPIC** — same topic tracked across multiple sessions

## Interpreting Results

- **`[mistake]`** entries = things that went wrong → read carefully to avoid repeating
- **`[pattern]`** entries = proven solutions → consider applying directly
- **`[decision]`** entries = past choices with rationale → check if still valid
- **`[tool]`** entries = configurations, commands → copy-paste ready
- **Confidence score** (0.3–1.0) = how reliable the entry is. Below 0.5 = verify before using.
- **Entry ID `#1234`** = use with `--detail 1234` to see full content

## Workflow Example

```
1. briefing.py "fix Docker compose networking"
   → shows 2 past mistakes about Docker DNS, 1 pattern about compose networks

2. query-session.py --detail 2045
   → reads the full mistake: was using wrong network driver

3. Apply the fix using the pattern from the briefing
```

## Semantic Search (if embeddings configured)

```bash
python3 ~/.copilot/tools/query-session.py "deployment error" --semantic
```

Works with meaning, not just keywords. Requires API key setup via `embed.py --setup`.
