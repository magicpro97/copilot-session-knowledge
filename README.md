# Copilot Session Knowledge Tools

Instant search across all Copilot CLI and Claude session-state data.  
Turns ~80MB of raw checkpoints into a queryable SQLite knowledge base.
Supports **hybrid search**: FTS5 keyword + semantic vector (multi-provider API).

### Phase 6 Highlights

- **Progressive Disclosure** — Default compact output (~50 tokens/entry vs ~300 before). Use `--verbose` for full content, `--detail <id>` for a single entry, `--context <id>` for entry + related.
- **Knowledge Dedup** — Hash-based deduplication prevents re-extracting the same content. Topic keys group entries across sessions.
- **Knowledge Graph** — 4500+ auto-detected relations (SAME_SESSION, TAG_OVERLAP, RESOLVED_BY, SAME_TOPIC). Query with `--related <id>` or `--graph "<topic>"`.
- **Compact Briefing** — Default ~500 token briefing. Use `--full` for complete output.
- **Robust Watcher** — Lock file prevents multiple instances. Watches both Copilot and Claude sessions. `--install-hint` for auto-start setup.
- **Smart Installer** — `install.py` with auto-detect, self-test, skill deployment, and uninstall.

## Architecture

```
~/.copilot/
├── session-state/
│   ├── {uuid}/                    # Raw session data (read-only)
│   │   ├── plan.md
│   │   ├── checkpoints/*.md       # AI context snapshots
│   │   ├── research/*.md          # AI research output
│   │   └── files/*                # Artifacts
│   ├── knowledge.db               # ← SQLite FTS5 + vector index + knowledge graph
│   └── .watch-state.json          # Watcher state (auto-generated)
└── tools/
    ├── build-session-index.py     # Indexer: sessions → SQLite
    ├── query-session.py           # Search CLI (keyword + semantic + graph)
    ├── embed.py                   # Multi-provider embedding engine
    ├── watch-sessions.py          # Auto-index daemon (lock-file protected)
    ├── extract-knowledge.py       # Knowledge extraction + dedup + graph builder
    ├── briefing.py                # Compact context briefing generator
    ├── learn.py                   # Manual knowledge capture
    ├── claude-adapter.py          # Claude session importer
    ├── sync-knowledge.py          # Cross-tool sync
    ├── install.py                 # Setup, self-test, skill deploy, uninstall
    ├── embedding-config.json      # Provider config (auto-generated)
    └── README.md                  # This file
```

## Setup

```bash
python ~/.copilot/tools/install.py              # Install + build index + self-test + skill deploy
python ~/.copilot/tools/install.py --check      # Check installation status
python ~/.copilot/tools/install.py --uninstall  # Remove tools (keeps knowledge.db)
```

### Embedding Setup (optional, enables semantic search)

```bash
python ~/.copilot/tools/embed.py --setup    # Interactive provider config
python ~/.copilot/tools/embed.py --build    # Generate embeddings
python ~/.copilot/tools/embed.py --test     # Test provider connectivity
python ~/.copilot/tools/embed.py --status   # Show embedding stats
```

**Supported providers** (all OpenAI-compatible):
| Provider | Model | Cost | Env Variable |
|---|---|---|---|
| Fireworks AI | nomic-embed-text-v1.5 | $0.008/1M tokens | `FIREWORKS_API_KEY` |
| OpenAI | text-embedding-3-small | $0.02/1M tokens | `OPENAI_API_KEY` |
| OpenRouter | (routes to providers) | varies | `OPENROUTER_API_KEY` |
| Custom | any | any | `EMBEDDING_API_KEY` |

**Fallback**: TF-IDF (requires `pip install scikit-learn`). Works without any API key.

## Usage

### Keyword Search (default, compact output)

```bash
python ~/.copilot/tools/query-session.py "search terms"              # Compact results (~50 tokens/entry)
python ~/.copilot/tools/query-session.py "search terms" --verbose    # Full content per entry
python ~/.copilot/tools/query-session.py "docker" --type research
python ~/.copilot/tools/query-session.py "docker" --source claude    # Filter by source (copilot/claude/all)
python ~/.copilot/tools/query-session.py --list
python ~/.copilot/tools/query-session.py --list --source copilot     # List only Copilot sessions
python ~/.copilot/tools/query-session.py --session de828552
python ~/.copilot/tools/query-session.py --recent                    # Show recent activity
```

### Semantic / Hybrid Search

```bash
python ~/.copilot/tools/query-session.py "lỗi triển khai" --semantic
python ~/.copilot/tools/query-session.py "how to fix Docker" --semantic -v
python ~/.copilot/tools/embed.py --search "deployment error"
```

### Knowledge Categories

```bash
python ~/.copilot/tools/query-session.py --mistakes    # Past errors + fixes (compact)
python ~/.copilot/tools/query-session.py --patterns    # Best practices (compact)
python ~/.copilot/tools/query-session.py --decisions   # Architecture choices (compact)
python ~/.copilot/tools/query-session.py --tools       # Tool configs (compact)
python ~/.copilot/tools/query-session.py --mistakes --verbose  # Full content
```

### Progressive Disclosure (drill-down)

```bash
python ~/.copilot/tools/query-session.py --detail <id>    # Full detail of a single entry
python ~/.copilot/tools/query-session.py --context <id>   # Entry + related entries (same session/category)
```

### Knowledge Graph

```bash
python ~/.copilot/tools/query-session.py --related <id>      # Show graph connections for an entry
python ~/.copilot/tools/query-session.py --graph "spring boot"  # Mini knowledge graph for a topic
```

Relation types (auto-detected during extraction):
- **SAME_SESSION** — entries from the same session
- **TAG_OVERLAP** — entries sharing tags
- **RESOLVED_BY** — mistakes linked to patterns that fix them
- **SAME_TOPIC** — entries with the same topic key across sessions

### Briefing (context injection for AI agents)

```bash
python ~/.copilot/tools/briefing.py "implement user CRUD"          # Compact briefing (~500 tokens)
python ~/.copilot/tools/briefing.py "implement user CRUD" --full   # Full markdown briefing
python ~/.copilot/tools/briefing.py "fix Docker compose" --compact # XML compact for AI context
python ~/.copilot/tools/briefing.py "fix Docker compose" --json    # JSON output
python ~/.copilot/tools/briefing.py "spring boot" --limit 5        # More results per category
python ~/.copilot/tools/briefing.py --auto                         # Auto-detect from git/plan
python ~/.copilot/tools/briefing.py --auto --full                  # Full briefing with auto-detect
```

### Export

```bash
python ~/.copilot/tools/query-session.py "spring" --export json
python ~/.copilot/tools/query-session.py --mistakes --export markdown
```

### Maintenance

```bash
python ~/.copilot/tools/build-session-index.py                # Full rebuild
python ~/.copilot/tools/build-session-index.py --incremental  # Update only
python ~/.copilot/tools/build-session-index.py --embed        # Rebuild + embeddings
python ~/.copilot/tools/build-session-index.py --stats        # Show stats
python ~/.copilot/tools/extract-knowledge.py                  # Re-extract (with dedup)
python ~/.copilot/tools/extract-knowledge.py --stats          # Show extraction stats
python ~/.copilot/tools/extract-knowledge.py --list           # List all extracted entries
python ~/.copilot/tools/extract-knowledge.py --category mistakes  # Show specific category
python ~/.copilot/tools/watch-sessions.py                     # Auto-index daemon (lock-file protected)
python ~/.copilot/tools/watch-sessions.py --interval 30       # Custom poll interval (seconds)
python ~/.copilot/tools/watch-sessions.py --once              # Single check then exit
python ~/.copilot/tools/watch-sessions.py --daemon            # Run as background process
python ~/.copilot/tools/watch-sessions.py --install-hint      # Print auto-start setup instructions
```

## Search Modes

```
Query: "lỗi deploy Docker"

┌─────────────────────────────────────────────────────────┐
│                 Hybrid Search Engine                     │
│                                                          │
│  ┌──────────────┐    ┌────────────────────────────────┐  │
│  │   FTS5       │    │  Vector / TF-IDF               │  │
│  │ (keyword)    │    │  (semantic meaning)             │  │
│  │              │    │                                 │  │
│  │ BM25 rank    │    │ cosine similarity               │  │
│  └──────┬───────┘    └──────────┬─────────────────────┘  │
│         │                       │                         │
│         └───────────┬───────────┘                         │
│                     ↓                                     │
│         Reciprocal Rank Fusion (RRF)                      │
│              (merge + deduplicate)                         │
│                     ↓                                     │
│            Top-K Hybrid Results                           │
└─────────────────────────────────────────────────────────┘
```

## Database Schema

```
sessions            → 1 row per session UUID
documents           → 1 row per .md/.txt file (checkpoint, research, artifact, plan)
sections            → 1 row per XML section (<overview>, <history>, etc.)
knowledge_fts       → FTS5 index over documents + sections
knowledge_entries   → Extracted patterns, mistakes, decisions, tools
                      (with content_hash for dedup, topic_key for cross-session grouping)
ke_fts              → FTS5 index over knowledge entries
knowledge_relations → Graph edges: SAME_SESSION, TAG_OVERLAP, RESOLVED_BY, SAME_TOPIC
embeddings          → Vector blobs for semantic search
tfidf_model         → Pickled TF-IDF model (fallback)
embedding_meta      → Embedding metadata (provider, timestamps)
```

## Requirements

**Core** (zero external dependencies):
- Python 3.10+
- SQLite with FTS5 (included in Python)
- Cross-platform: Windows, macOS, Linux

**Optional** (for enhanced features):
- `scikit-learn` — TF-IDF fallback (`pip install scikit-learn`)
- Any API key above — Vector embeddings for true semantic search

## AI Agent Integration

Skills that teach agents to use these tools:
- **Copilot CLI**: `.github/skills/session-knowledge/SKILL.md`
- **Claude Code**: `.claude/skills/session-knowledge.md`

Agents can search the knowledge base before starting tasks to leverage past experience.
