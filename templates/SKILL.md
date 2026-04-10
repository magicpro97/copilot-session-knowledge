# Session Knowledge — Copilot CLI Skill

Search past Copilot and Claude sessions before starting any task.
This avoids repeating mistakes and leverages proven patterns.

## Quick Start

```bash
# Get a compact briefing before starting work (~500 tokens)
python ~/.copilot/tools/briefing.py "your task description"

# Full briefing with complete content
python ~/.copilot/tools/briefing.py "your task description" --full

# Auto-detect context from git state / plan.md
python ~/.copilot/tools/briefing.py --auto
```

## Search Commands

### Compact Search (default — ~50 tokens/entry)

```bash
python ~/.copilot/tools/query-session.py "search terms"
python ~/.copilot/tools/query-session.py "docker" --type research
python ~/.copilot/tools/query-session.py "spring" --source copilot
```

### Full Content

```bash
python ~/.copilot/tools/query-session.py "search terms" --verbose    # All results expanded
```

### Progressive Disclosure (drill-down by entry ID)

```bash
python ~/.copilot/tools/query-session.py --detail <id>     # Full detail of one entry
python ~/.copilot/tools/query-session.py --context <id>    # Entry + related entries
```

### Knowledge Categories

```bash
python ~/.copilot/tools/query-session.py --mistakes    # Past errors + fixes
python ~/.copilot/tools/query-session.py --patterns    # Reusable best practices
python ~/.copilot/tools/query-session.py --decisions   # Architecture choices
python ~/.copilot/tools/query-session.py --tools       # Tool configs
```

### Knowledge Graph

```bash
python ~/.copilot/tools/query-session.py --related <id>        # Graph connections for an entry
python ~/.copilot/tools/query-session.py --graph "spring boot"  # Mini knowledge graph for a topic
```

Relation types: SAME_SESSION, TAG_OVERLAP, RESOLVED_BY, SAME_TOPIC.

### Semantic / Hybrid Search

```bash
python ~/.copilot/tools/query-session.py "deployment error" --semantic
python ~/.copilot/tools/query-session.py "how to fix Docker" --semantic --verbose
```

## Briefing Formats

| Flag | Output | Token Budget |
|------|--------|-------------|
| *(default)* | Compact titles + 1-line summaries with entry IDs | ~500 tokens |
| `--full` | Complete content with tags, confidence, full text | ~3000 tokens |
| `--compact` | XML compact for AI context injection | ~500 tokens |
| `--json` | JSON structured output | varies |

## Token Budget

Default compact output uses ~6x fewer tokens than previous versions:
- Search results: ~50 tokens/entry (was ~300)
- Briefing: ~500 tokens total (was ~3000)
- Use `--verbose` or `--full` only when you need deep detail

## Workflow

1. **Before any task**: `briefing.py "task description"` or `briefing.py --auto`
2. **If briefing mentions relevant entries**: `query-session.py --detail <id>` to drill down
3. **To explore connections**: `query-session.py --related <id>` or `--graph "topic"`
4. **During work**: search for specific errors or patterns as needed
5. **Export for sharing**: `query-session.py "query" --export json`
