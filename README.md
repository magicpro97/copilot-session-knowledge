# 🧠 Copilot Session Knowledge Tools

> Turn your AI coding sessions into a searchable knowledge base.

**Copilot Session Knowledge** indexes all your [GitHub Copilot CLI](https://docs.github.com/en/copilot/github-copilot-in-the-cli) session-state data — checkpoints, plans, research, artifacts — into a fast SQLite database with **full-text search**, **semantic vector search**, and **knowledge extraction**.

Every mistake you've fixed, every pattern you've learned, every decision you've made — instantly searchable. AI agents can self-brief before starting tasks, avoiding repeated mistakes and leveraging past experience.

---

## ✨ Features

| Feature | Description |
|---|---|
| 🔍 **Full-Text Search** | FTS5 keyword search with BM25 ranking across all sessions |
| 🧬 **Semantic Search** | Vector embeddings via multi-provider API (Fireworks, OpenAI, etc.) |
| 🔀 **Hybrid Search** | Combines keyword + semantic via Reciprocal Rank Fusion (RRF) |
| 🧠 **Knowledge Extraction** | Auto-extracts mistakes, patterns, decisions, tools from sessions |
| 📋 **Pre-task Briefing** | AI agents self-brief from knowledge base before starting work |
| 📝 **Knowledge Recording** | Agents record new learnings as they work |
| 👀 **Watch Mode** | Auto-index new sessions in real-time |
| 📤 **Export** | JSON and Markdown export for any search results |
| 🌍 **Multilingual** | Trilingual support: English, Vietnamese, Japanese indicators & noise filters |
| ☁️ **Multi-Stack Tags** | AWS, TypeScript, React Native, Python, Java, and 50+ technology tags |
| 🖥️ **Cross-Platform** | Works on Windows, macOS, and Linux |

---

## 📦 Installation

### Quick Install

```bash
# Clone the repo
git clone https://github.com/magicpro97/copilot-session-knowledge.git

# Run the installer
python copilot-session-knowledge/install.py
```

The installer will:
1. Copy all tools to `~/.copilot/tools/`
2. Build the FTS5 index from existing sessions
3. Extract knowledge entries (mistakes, patterns, etc.)

### Manual Install

```bash
# Copy all .py files to ~/.copilot/tools/
mkdir -p ~/.copilot/tools
cp *.py ~/.copilot/tools/

# Build index
python ~/.copilot/tools/build-session-index.py

# Extract knowledge
python ~/.copilot/tools/extract-knowledge.py
```

### Verify Installation

```bash
python ~/.copilot/tools/install.py --check
```

---

## 🚀 Usage

### Search Your Sessions

```bash
# Keyword search (default)
python ~/.copilot/tools/query-session.py "docker compose error"

# Semantic search (requires embedding setup)
python ~/.copilot/tools/query-session.py "deployment failed" --semantic

# Search by category
python ~/.copilot/tools/query-session.py --mistakes    # Past errors & fixes
python ~/.copilot/tools/query-session.py --patterns    # Best practices
python ~/.copilot/tools/query-session.py --decisions   # Architecture choices
python ~/.copilot/tools/query-session.py --tools       # Tool configurations

# Filter by session or type
python ~/.copilot/tools/query-session.py "spring" --type research
python ~/.copilot/tools/query-session.py --session abc123 --list

# Export results
python ~/.copilot/tools/query-session.py "spring" --export json
python ~/.copilot/tools/query-session.py --mistakes --export markdown
```

### Pre-task Briefing (for AI Agents)

```bash
# Generate context briefing before starting a task
python ~/.copilot/tools/briefing.py "implement user authentication"

# Auto-detect context from git branch & plan
python ~/.copilot/tools/briefing.py --auto

# Compact XML format (best for AI agent injection)
python ~/.copilot/tools/briefing.py --auto --compact

# JSON output
python ~/.copilot/tools/briefing.py "fix Docker" --json
```

### Record New Knowledge

```bash
# Record a mistake and its fix
python ~/.copilot/tools/learn.py --mistake "FTS5 index malformed" \
  "Run REBUILD on the FTS table to fix: INSERT INTO fts(fts) VALUES('rebuild')"

# Record a useful pattern
python ~/.copilot/tools/learn.py --pattern "Hybrid search RRF merge" \
  "Combine FTS5 + vector search using Reciprocal Rank Fusion for best results"

# Record a decision
python ~/.copilot/tools/learn.py --decision "Use Fireworks AI for embeddings" \
  "Cheapest provider at \$0.008/1M tokens, nomic-embed-text-v1.5, 768-dim"
```

### Maintenance

```bash
# Rebuild index from scratch
python ~/.copilot/tools/build-session-index.py

# Incremental update (faster)
python ~/.copilot/tools/build-session-index.py --incremental

# Rebuild with embeddings
python ~/.copilot/tools/build-session-index.py --embed

# Show database stats
python ~/.copilot/tools/build-session-index.py --stats

# Re-extract knowledge entries
python ~/.copilot/tools/extract-knowledge.py

# Auto-index daemon (watches for new sessions)
python ~/.copilot/tools/watch-sessions.py
```

---

## 🧬 Semantic Search Setup (Optional)

Semantic search uses vector embeddings to find results by **meaning**, not just keywords. It's optional — keyword search works perfectly without it.

### 1. Choose a Provider

| Provider | Model | Cost | Notes |
|---|---|---|---|
| **Fireworks AI** | nomic-embed-text-v1.5 | $0.008/1M tokens | Cheapest, recommended |
| **OpenAI** | text-embedding-3-small | $0.02/1M tokens | Most popular |
| **OpenRouter** | varies | varies | Routes to multiple providers |
| **Custom** | any | any | Any OpenAI-compatible endpoint |
| **TF-IDF** (fallback) | local | free | No API key needed, `pip install scikit-learn` |

All providers use the standard OpenAI-compatible `/v1/embeddings` API.

### 2. Configure

**Option A: Environment Variable**
```bash
export FIREWORKS_API_KEY="your-key-here"
# or
export OPENAI_API_KEY="sk-..."
```

**Option B: Config File** (recommended)
```bash
# Copy and edit the example config
cp embedding-config.example.json ~/.copilot/tools/embedding-config.json
# Edit with your API key
```

### 3. Build Embeddings

```bash
# Interactive setup
python ~/.copilot/tools/embed.py --setup

# Test connectivity
python ~/.copilot/tools/embed.py --test

# Generate all embeddings
python ~/.copilot/tools/embed.py --build

# Check status
python ~/.copilot/tools/embed.py --status
```

### Fallback: TF-IDF (No API Key)

If you don't want to use any API, TF-IDF provides basic semantic search locally:

```bash
pip install scikit-learn
python ~/.copilot/tools/embed.py --build  # Uses TF-IDF automatically
```

---

## 🏗️ Architecture

```
~/.copilot/
├── session-state/
│   ├── {uuid}/                     # Raw session data (read-only)
│   │   ├── plan.md
│   │   ├── checkpoints/*.md        # AI context snapshots
│   │   ├── research/*.md           # AI research output
│   │   └── files/*                 # Artifacts
│   ├── knowledge.db                # ← SQLite FTS5 + vector index
│   └── .watch-state.json           # Watcher state
└── tools/
    ├── install.py                  # Setup & management
    ├── build-session-index.py      # Indexer: sessions → SQLite
    ├── query-session.py            # Search CLI (keyword + semantic)
    ├── extract-knowledge.py        # Knowledge extraction pipeline
    ├── embed.py                    # Multi-provider embedding engine
    ├── briefing.py                 # Pre-task context generator
    ├── learn.py                    # Knowledge recording API
    ├── watch-sessions.py           # Auto-index daemon
    ├── generate-summary.py         # KNOWLEDGE.md generator
    └── embedding-config.json       # Provider config (gitignored)
```

### Search Flow

```
Query: "deployment error"

┌──────────────────────────────────────────────────────┐
│                  Hybrid Search Engine                 │
│                                                      │
│  ┌──────────────┐    ┌───────────────────────────┐   │
│  │   FTS5       │    │  Vector / TF-IDF          │   │
│  │  (keyword)   │    │  (semantic meaning)        │   │
│  │  BM25 rank   │    │  cosine similarity         │   │
│  └──────┬───────┘    └──────────┬────────────────┘   │
│         └───────────┬───────────┘                     │
│                     ↓                                 │
│       Reciprocal Rank Fusion (RRF)                    │
│          (merge + deduplicate)                        │
│                     ↓                                 │
│            Top-K Hybrid Results                       │
└──────────────────────────────────────────────────────┘
```

### Database Schema

| Table | Purpose |
|---|---|
| `sessions` | 1 row per session UUID |
| `documents` | 1 row per .md/.txt file |
| `sections` | 1 row per content section |
| `knowledge_fts` | FTS5 index over documents + sections |
| `knowledge_entries` | Extracted mistakes, patterns, decisions |
| `ke_fts` | FTS5 index over knowledge entries |
| `embeddings` | Vector blobs for semantic search |
| `tfidf_model` | Pickled TF-IDF model (fallback) |
| `embedding_meta` | Embedding provider metadata |

---

## 🌍 Multilingual & Multi-Stack Support

Knowledge extraction and classification work across **three languages** and **multiple technology stacks**.

### Supported Languages

| Language | Indicators | Noise Filters | Example |
|---|---|---|---|
| 🇬🇧 **English** | ✅ Full | ✅ Full | "root cause", "best practice", "chose X because" |
| 🇻🇳 **Vietnamese** | ✅ Full | ✅ Full | "lỗi", "quy tắc", "quyết định", "cấu hình" |
| 🇯🇵 **Japanese** | ✅ Full | ✅ Full | "エラー", "パターン", "決定", "環境構築" |

### Supported Technology Tags (50+)

| Category | Tags |
|---|---|
| **Cloud & Infra** | `aws`, `aws-cdk`, `lambda`, `dynamodb`, `s3`, `sqs`, `sns`, `cognito`, `cloudwatch`, `api-gateway`, `eventbridge`, `cloudformation`, `step-functions`, `xray`, `websocket`, `docker`, `vpc` |
| **Languages** | `typescript`, `javascript`, `python`, `nodejs` |
| **Frontend** | `react-native`, `expo`, `react`, `css`, `ui` |
| **Testing** | `jest`, `playwright`, `e2e`, `tdd` |
| **Build Tools** | `eslint`, `prettier`, `package-manager`, `git`, `vscode`, `copilot` |
| **Data** | `spreadsheet`, `json`, `openapi`, `mermaid`, `sql` |
| **Security** | `tls`, `proxy`, `auth` |
| **Legacy** | `spring-boot`, `postgresql`, `redis`, `jpa`, `java-build` |

### Branch Name Parsing

Auto-detect understands common branch naming conventions:

```
dev/fpt/feature/5022-copy-to-group  →  keywords: "5022", "copy", "group"
feature/audit-export-websocket      →  keywords: "audit", "export", "websocket"
fix/4699-patient-search-pagination  →  keywords: "4699", "patient", "search", "pagination"
```

Filtered stopwords: `dev`, `fpt`, `feature`, `fix`, `bug`, `refactor`, `docs`, `chore`, `release`, `hotfix`, `main`, `master`

---

## 🤖 AI Agent Integration

These tools are designed to be used **by** AI agents. Add skill files to your project:

### For GitHub Copilot CLI

Create `.github/skills/session-knowledge/SKILL.md`:

```markdown
# Session Knowledge Skill

Before starting any task, generate a briefing:
python ~/.copilot/tools/briefing.py --auto --compact

Search for relevant past experience:
python ~/.copilot/tools/query-session.py "topic" --semantic

After completing work, record learnings:
python ~/.copilot/tools/learn.py --pattern "Title" "Description"
```

### For Claude Code

Create `.claude/skills/session-knowledge.md` with similar content.

### Agent Workflow

```
1. BRIEF  → briefing.py --auto --compact    (understand context)
2. SEARCH → query-session.py "topic" -s     (find relevant knowledge)
3. WORK   → ... do the actual task ...
4. LEARN  → learn.py --mistake/--pattern    (record new knowledge)
```

---

## 📋 Requirements

### Core (Zero Dependencies)

- **Python 3.10+**
- **SQLite with FTS5** (included in Python standard library)
- Works on **Windows**, **macOS**, and **Linux**

### Optional

| Package | Purpose | Install |
|---|---|---|
| `scikit-learn` | TF-IDF fallback search | `pip install scikit-learn` |
| Embedding API key | True semantic vector search | See [setup](#-semantic-search-setup-optional) |

---

## 📊 Performance

Tested with real-world data: 12 sessions, 24 checkpoints, 730K characters.

| Operation | Time | Notes |
|---|---|---|
| Full index build | ~5s | Parses all sessions |
| Keyword search | <50ms | FTS5 BM25 |
| Semantic search | ~200ms | Vector cosine similarity |
| Hybrid search | ~300ms | FTS5 + Vector + RRF merge |
| Briefing (compact) | <1s | Auto-detect + search |
| Embedding generation | ~2min | 732 items via API |

---

## 🙏 Acknowledgments

Built with:
- [SQLite FTS5](https://www.sqlite.org/fts5.html) — Full-text search
- [Fireworks AI](https://fireworks.ai/) — Affordable embeddings
- [nomic-embed-text](https://huggingface.co/nomic-ai/nomic-embed-text-v1.5) — Embedding model
- [Reciprocal Rank Fusion](https://plg.uwaterloo.ca/~grburt/rrf.pdf) — Result merging

---

## 📄 License

MIT License — see [LICENSE](LICENSE) for details.
