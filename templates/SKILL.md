---
name: session-knowledge
description: >-
  Search past Copilot/Claude session knowledge before complex tasks. Run briefing.py
  for relevant mistakes, patterns, decisions. Use query-session.py to search errors,
  tools, architecture choices. Supports semantic search with embeddings.
---

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

## Context Budget

Always start with the lightest fetch, then escalate only when a hit is relevant:

| Task complexity | Recommended command | Approx tokens |
|----------------|---------------------|---------------|
| Trivial / session start | `briefing.py --wakeup` | ~170 |
| Moderate (bug fix, small feature) | `briefing.py --auto --compact` | ~500 |
| Complex / unfamiliar area | `briefing.py "task" --full` | ~3K |
| Drill into one entry | `query-session.py --detail <id>` | varies |

**Do not load `--full` when `--compact` shows no relevant hits.** A large briefing that
surfaces nothing useful costs tokens without benefit.

## Core Commands

### 1. Briefing (recommended first step)

```bash
python3 ~/.copilot/tools/briefing.py "your task description"    # Compact ~500 tokens
python3 ~/.copilot/tools/briefing.py "your task" --full         # Full detail ~3K tokens
python3 ~/.copilot/tools/briefing.py --auto                     # Auto-detect from git/plan
python3 ~/.copilot/tools/briefing.py --wakeup                   # Ultra-compact ~170 tokens for session start
python3 ~/.copilot/tools/briefing.py --titles-only              # Index only ~10 tok/entry — progressive disclosure
python3 ~/.copilot/tools/briefing.py --titles-only "topic"      # Filtered titles
python3 ~/.copilot/tools/briefing.py "task" --wing ui --room settings  # Filter by wing/room
python3 ~/.copilot/tools/briefing.py "task" --min-confidence 0.7       # High-quality entries only
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

### 6. Record Knowledge (learn.py)

```bash
# 7 observation types
python3 ~/.copilot/tools/learn.py --mistake "Title"   "What went wrong and fix"         --tags "tag1,tag2"
python3 ~/.copilot/tools/learn.py --pattern "Title"   "What works well / best practice" --tags "tag1"
python3 ~/.copilot/tools/learn.py --decision "Title"  "Architecture decision rationale" --tags "tag1"
python3 ~/.copilot/tools/learn.py --tool "Title"      "Tool/config that was useful"     --tags "tag1"
python3 ~/.copilot/tools/learn.py --feature "Title"   "New feature implementation"      --tags "tag1"
python3 ~/.copilot/tools/learn.py --refactor "Title"  "Code improvement description"    --tags "tag1"
python3 ~/.copilot/tools/learn.py --discovery "Title" "Codebase finding or insight"     --tags "tag1"

# Structured facts (discrete, verifiable statements)
python3 ~/.copilot/tools/learn.py --pattern "Title" "Description" \
  --fact "max retries is 3" --fact "timeout is 30s"

# Palace categorization (wing/room)
python3 ~/.copilot/tools/learn.py --mistake "Title" "Description" --wing ui --room settings

# Knowledge graph relations
python3 ~/.copilot/tools/learn.py --relate "ScreenA" "navigates_to" "ScreenB"
python3 ~/.copilot/tools/learn.py --relate "ComponentX" "uses" "ThemeToken"

# Bulk import / view
python3 ~/.copilot/tools/learn.py --from-file notes.md    # Bulk import from markdown
python3 ~/.copilot/tools/learn.py --list                   # List recent entries
python3 ~/.copilot/tools/learn.py --stats                  # Knowledge base statistics
```

### 7. Auto-Update Tools

```bash
python3 ~/.copilot/tools/auto-update-tools.py              # Auto-update (24h cooldown)
python3 ~/.copilot/tools/auto-update-tools.py --force       # Force update now
python3 ~/.copilot/tools/auto-update-tools.py --status      # Show version info
python3 ~/.copilot/tools/auto-update-tools.py --doctor      # Health check
```

## Interpreting Results

- **`[mistake]`** entries = things that went wrong → read carefully to avoid repeating
- **`[pattern]`** entries = proven solutions → consider applying directly
- **`[decision]`** entries = past choices with rationale → check if still valid
- **`[tool]`** entries = configurations, commands → copy-paste ready
- **`[feature]`** entries = feature implementation details → reference for similar work
- **`[refactor]`** entries = code improvements → reuse approach
- **`[discovery]`** entries = codebase insights → context for decisions
- **Confidence score** (0.3–1.0) = how reliable the entry is. Below 0.5 = verify before using.
- **Entry ID `#1234`** = use with `--detail 1234` to see full content

## Workflow Example

```
1. briefing.py "fix Docker compose networking"
   → shows 2 past mistakes about Docker DNS, 1 pattern about compose networks

2. query-session.py --detail 2045
   → reads the full mistake: was using wrong network driver

3. Apply the fix using the pattern from the briefing

4. learn.py --pattern "Docker DNS Fix" "Use bridge network with explicit DNS" \
     --fact "compose DNS uses service names" --wing infrastructure --room docker
```

<example>
User: "I need to add retry logic to the payment service. Where should I start?"

1. Run briefing before touching anything:
   ```
   python3 ~/.copilot/tools/briefing.py "add retry logic payment service" --auto --compact
   ```
   → Output surfaces a past mistake: "Exponential backoff not applied to idempotent endpoints"
   and a pattern: "Use tenacity library with max_attempts=3, wait=wait_exponential(min=1, max=10)"

2. Drill into the pattern entry shown in results:
   ```
   python3 ~/.copilot/tools/query-session.py --detail 1842
   ```
   → Full entry: exact tenacity config that worked in the order service

3. Implement retry logic using the pattern, avoiding the known mistake.

4. Record what was learned:
   ```
   python3 ~/.copilot/tools/learn.py --pattern "Payment retry with tenacity" \
     "Use tenacity with max_attempts=3, wait_exponential(min=1, max=10) on POST /charge" \
     --fact "idempotency key required on retry" --wing backend --room payments
   ```
</example>

<example>
User: "Getting 'SSL: CERTIFICATE_VERIFY_FAILED' on CI — has this come up before?"

1. Search for the error message:
   ```
   python3 ~/.copilot/tools/query-session.py "SSL CERTIFICATE_VERIFY_FAILED"
   ```
   → Finds a past mistake entry explaining that the corporate proxy strips certs and the fix
   was to set `REQUESTS_CA_BUNDLE` to the internal CA bundle path.

2. Apply the fix directly from the KB entry — no need to debug from scratch.

3. If it was a new variant, record it:
   ```
   python3 ~/.copilot/tools/learn.py --mistake "SSL verify failed behind proxy" \
     "Corporate proxy strips SSL — set REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-bundle.crt" \
     --tags "ssl,ci,proxy" --wing devops --room ci
   ```
</example>

## Semantic Search (if embeddings configured)

```bash
python3 ~/.copilot/tools/query-session.py "deployment error" --semantic
```

Works with meaning, not just keywords. Requires API key setup via `embed.py --setup`.
