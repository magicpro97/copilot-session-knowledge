#!/usr/bin/env python3
"""sk session compact — compress a session's knowledge into a structured checkpoint.

Creates a session_checkpoint entry with Goal/Progress/Key Decisions/Changed Files/
Next Steps/Critical Context sections that packs session knowledge into ~500 tokens.

Usage:
    python session-compact.py                   # compact most recent session
    python session-compact.py <session_id>       # compact specific session
    python session-compact.py --dry-run          # preview without writing
    python session-compact.py --list             # list existing checkpoints
    python session-compact.py --json             # JSON output
"""

import argparse
import collections
import json
import math
import os
import re
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

if os.name == "nt":
    sys.stdout.reconfigure(encoding="utf-8")


def _db_path() -> Path:
    if e := os.environ.get("SK_DB_PATH"):
        return Path(e)
    for p in [
        Path.home() / ".copilot/knowledge.db",
        Path(__file__).parent / "sessions.db",
        Path.home() / ".copilot/tools/sessions.db",
    ]:
        if p.exists():
            return p
    return Path.home() / ".copilot/knowledge.db"


def _most_recent_session(conn: sqlite3.Connection) -> str | None:
    row = conn.execute("SELECT id FROM sessions ORDER BY indexed_at DESC LIMIT 1").fetchone()
    return row[0] if row else None


def _get_entries(conn: sqlite3.Connection, session_id: str) -> list[dict]:
    """Get all knowledge entries for a session."""
    rows = conn.execute(
        """SELECT id, category, title, content, tags, priority
           FROM knowledge_entries
           WHERE session_id = ?
           ORDER BY category, priority""",
        (session_id,),
    ).fetchall()
    return [dict(zip(["id", "category", "title", "content", "tags", "priority"], r, strict=False)) for r in rows]


def _existing_checkpoint(conn: sqlite3.Connection, session_id: str) -> dict | None:
    """Return existing session_checkpoint for this session if any."""
    row = conn.execute(
        """SELECT id, title, content FROM knowledge_entries
           WHERE session_id = ? AND category = 'session_checkpoint'
           LIMIT 1""",
        (session_id,),
    ).fetchone()
    return dict(zip(["id", "title", "content"], row, strict=False)) if row else None


def _build_compact_prompt(session_id: str, entries: list[dict]) -> str:
    """Build the LLM prompt for structured compaction."""
    entry_text = "\n".join(
        f"[{e['category']}|{e['priority']}] {e['title']}: {e['content'][:200]}"
        for e in entries[:50]  # cap at 50 entries to stay within LLM context
    )
    return f"""You are compacting a coding session's knowledge entries into a structured checkpoint.
Session: {session_id}
Total entries: {len(entries)}

Knowledge entries:
{entry_text}

Create a structured checkpoint with EXACTLY these sections (keep each under 100 words):
## Goal
What was being built or fixed in this session?

## Progress
What was accomplished? What was merged/shipped?

## Key Decisions
What architecture/approach decisions were made?

## Changed Files
Which files were modified? (bullet list, file paths only)

## Next Steps
What remains to be done?

## Critical Context
What must a future agent know to continue this work correctly? (pitfalls, constraints, discoveries)

Be concise. The entire checkpoint must fit in 500 tokens."""


def _call_llm(prompt: str) -> str | None:
    """Call LLM via gh copilot suggest or fallback to template."""
    try:
        result = subprocess.run(
            ["gh", "copilot", "suggest", "-t", "shell", prompt[:4000]],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return None


def _template_compact(session_id: str, entries: list[dict]) -> str:
    """Create a template checkpoint without LLM (fallback)."""
    by_cat: dict[str, list] = {}
    for e in entries:
        by_cat.setdefault(e["category"], []).append(e)

    parts = [f"## Goal\nSession {session_id[:20]}... — {len(entries)} knowledge entries"]

    if "mistake" in by_cat:
        titles = [e["title"][:60] for e in by_cat["mistake"][:5]]
        parts.append("## Key Decisions\nMistakes/fixes: " + "; ".join(titles))

    if "feature" in by_cat or "pattern" in by_cat:
        cat = "feature" if "feature" in by_cat else "pattern"
        titles = [e["title"][:60] for e in by_cat[cat][:5]]
        parts.append("## Progress\nFeatures/patterns: " + "; ".join(titles))

    parts.append("## Next Steps\n(Not determined — run sk briefing for context)")
    parts.append("## Critical Context\n" + "; ".join(e["title"][:40] for e in entries[:3] if e.get("priority") == "P0"))
    return "\n\n".join(parts)


def _store_checkpoint(
    conn: sqlite3.Connection,
    session_id: str,
    content: str,
    existing_id: int | None,
) -> int:
    """Store or update the session_checkpoint entry."""
    now = datetime.now(timezone.utc).isoformat()
    title = f"Session checkpoint: {session_id[:30]}"
    if existing_id:
        conn.execute(
            "UPDATE knowledge_entries SET content=?, last_seen=? WHERE id=?",
            (content, now, existing_id),
        )
        conn.commit()
        return existing_id
    else:
        cur = conn.execute(
            """INSERT INTO knowledge_entries
               (session_id, category, title, content, first_seen, last_seen, priority, source)
               VALUES (?,?,?,?,?,?,'P0','compact')""",
            (session_id, "session_checkpoint", title, content, now, now),
        )
        conn.commit()
        return cur.lastrowid


# ---------------------------------------------------------------------------
# RAPTOR-style TF-IDF clustering helpers (pure stdlib — no scikit-learn)
# ---------------------------------------------------------------------------


def _tokenize(text: str) -> list[str]:
    """Simple word tokenizer — lowercase, alphanumeric only."""
    return re.findall(r"[a-z0-9]+", text.lower())


def _compute_tfidf(docs: list[str]) -> list[dict[str, float]]:
    """Compute TF-IDF vectors for a list of documents."""
    tokenized = [_tokenize(d) for d in docs]
    df: dict[str, int] = collections.Counter()
    for tokens in tokenized:
        df.update(set(tokens))
    N = len(docs)
    idf = {w: math.log((N + 1) / (df[w] + 1)) + 1.0 for w in df}
    vecs = []
    for tokens in tokenized:
        tf = collections.Counter(tokens)
        total = len(tokens) or 1
        vec = {w: (tf[w] / total) * idf[w] for w in tf}
        vecs.append(vec)
    return vecs


def _cosine(a: dict[str, float], b: dict[str, float]) -> float:
    """Cosine similarity between two TF-IDF vectors."""
    dot = sum(a.get(w, 0) * b.get(w, 0) for w in b)
    na = math.sqrt(sum(v * v for v in a.values())) or 1e-9
    nb = math.sqrt(sum(v * v for v in b.values())) or 1e-9
    return dot / (na * nb)


def _cluster_sessions(summaries: list[tuple[str, str]], threshold: float = 0.15) -> list[list[tuple[str, str]]]:
    """Group sessions by TF-IDF similarity (greedy single-linkage).

    summaries = [(session_id, text), ...]
    """
    if not summaries:
        return []
    docs = [s[1] for s in summaries]
    vecs = _compute_tfidf(docs)
    clusters: list[list[int]] = []
    assigned = [False] * len(summaries)
    for i in range(len(summaries)):
        if assigned[i]:
            continue
        cluster = [i]
        assigned[i] = True
        for j in range(i + 1, len(summaries)):
            if not assigned[j] and _cosine(vecs[i], vecs[j]) >= threshold:
                cluster.append(j)
                assigned[j] = True
        clusters.append(cluster)
    return [[summaries[i] for i in c] for c in clusters]


def _fetch_cross_session_summaries(conn: sqlite3.Connection, limit: int) -> list[tuple[str, str]]:
    """Return last *limit* session summaries suitable for clustering.

    Tries session_compact entries first, then falls back to session_checkpoint,
    and finally to raw session summaries from the sessions table.
    """
    for category in ("session_compact", "session_checkpoint"):
        try:
            rows = conn.execute(
                """SELECT session_id, title || ' ' || content
                   FROM knowledge_entries
                   WHERE category = ?
                   ORDER BY last_seen DESC
                   LIMIT ?""",
                (category, limit),
            ).fetchall()
        except sqlite3.OperationalError:
            break
        if rows:
            return [(r[0], r[1]) for r in rows]

    # Last resort: sessions table summary column (may not always exist)
    try:
        rows = conn.execute(
            "SELECT id, summary FROM sessions WHERE summary IS NOT NULL ORDER BY indexed_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        if rows:
            return [(r[0], r[1]) for r in rows]
    except sqlite3.OperationalError:
        pass

    return []


def _store_cross_session_cluster(conn: sqlite3.Connection, content: str, session_id: str = "cross_session") -> int:
    """Persist the cross-session cluster summary as a knowledge entry."""
    now = datetime.now(timezone.utc).isoformat()
    existing = conn.execute(
        "SELECT id FROM knowledge_entries WHERE category='cross_session_cluster' ORDER BY last_seen DESC LIMIT 1"
    ).fetchone()
    if existing:
        conn.execute(
            "UPDATE knowledge_entries SET content=?, last_seen=? WHERE id=?",
            (content, now, existing[0]),
        )
        conn.commit()
        return existing[0]
    cur = conn.execute(
        """INSERT INTO knowledge_entries
           (session_id, category, title, content, first_seen, last_seen, priority, source)
           VALUES (?,?,?,?,?,?,'P1','compact')""",
        (session_id, "cross_session_cluster", "Cross-session cluster summary", content, now, now),
    )
    conn.commit()
    return cur.lastrowid


def _format_cross_session_output(clusters: list[list[tuple[str, str]]], as_json: bool = False) -> str:
    """Format clustered session summaries for output."""
    if as_json:
        data = [
            {
                "cluster": i + 1,
                "sessions": [{"session_id": sid, "summary": text[:300]} for sid, text in c],
            }
            for i, c in enumerate(clusters)
        ]
        return json.dumps(data, indent=2)

    lines: list[str] = [f"Cross-session clusters ({len(clusters)} groups)\n"]
    for i, cluster in enumerate(clusters, 1):
        label = "session" if len(cluster) == 1 else "sessions"
        lines.append(f"## Cluster {i} ({len(cluster)} {label})")
        for sid, text in cluster:
            snippet = text[:200].replace("\n", " ")
            lines.append(f"  • [{sid[:20]}] {snippet}...")
        lines.append("")
    return "\n".join(lines)


def _list_checkpoints(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        """SELECT ke.id, ke.session_id, ke.title, ke.first_seen,
                  length(ke.content) AS chars
           FROM knowledge_entries ke
           WHERE ke.category = 'session_checkpoint'
           ORDER BY ke.first_seen DESC
           LIMIT 20"""
    ).fetchall()
    return [dict(zip(["id", "session_id", "title", "first_seen", "chars"], r, strict=False)) for r in rows]


def main() -> None:
    parser = argparse.ArgumentParser(description="Compact session knowledge into structured checkpoint")
    parser.add_argument("session_id", nargs="?", help="Session ID (default: most recent)")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--list", action="store_true", dest="list_mode")
    parser.add_argument("--json", action="store_true", dest="as_json")
    parser.add_argument(
        "--no-llm",
        action="store_true",
        dest="no_llm",
        help="Skip LLM call, use template-based compaction",
    )
    parser.add_argument(
        "--cross-session",
        nargs="?",
        const=20,
        type=int,
        metavar="N",
        dest="cross_session",
        help="Cluster last N session summaries using TF-IDF (default N=20)",
    )
    parser.add_argument(
        "--summary", dest="summary", default=None, help="Use provided text as checkpoint content directly (MCP path)"
    )
    parser.add_argument(
        "--session-id", dest="session_id_flag", default=None, help="Session ID (alternative to positional arg)"
    )
    args = parser.parse_args()

    db_path = _db_path()
    if not db_path.exists():
        print(f"DB not found: {db_path}", file=sys.stderr)
        sys.exit(1)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    if args.cross_session is not None:
        limit = max(2, args.cross_session)
        summaries = _fetch_cross_session_summaries(conn, limit)
        if not summaries:
            print("No session summaries found for cross-session clustering.", file=sys.stderr)
            conn.close()
            sys.exit(1)
        clusters = _cluster_sessions(summaries)
        output = _format_cross_session_output(clusters, as_json=args.as_json)
        print(output)
        if not args.dry_run:
            entry_id = _store_cross_session_cluster(conn, output)
            if not args.as_json:
                print(f"✅ Stored cross-session cluster summary as entry #{entry_id}")
        conn.close()
        return

    if args.list_mode:
        checkpoints = _list_checkpoints(conn)
        if args.as_json:
            print(json.dumps(checkpoints, indent=2))
        else:
            print(f"Session checkpoints ({len(checkpoints)}):")
            for c in checkpoints:
                print(f"  #{c['id']} {c['first_seen'][:10]} {c['title'][:60]} ({c['chars']} chars)")
        conn.close()
        return

    session_id = args.session_id_flag or args.session_id or _most_recent_session(conn)
    if not session_id:
        print("No sessions found.", file=sys.stderr)
        sys.exit(1)

    # MCP path: caller provides the summary text directly — store it without DB entry lookup
    if args.summary:
        content = args.summary.strip()
        if not content:
            print("--summary must not be empty.", file=sys.stderr)
            sys.exit(1)
        existing = _existing_checkpoint(conn, session_id)
        est_tokens = len(content) // 4
        if args.dry_run:
            print(f"DRY RUN — would store provided summary for session {session_id[:30]}...")
            print(f"Checkpoint: {len(content)} chars (~{est_tokens} tokens)")
            conn.close()
            return
        entry_id = _store_checkpoint(conn, session_id, content, existing["id"] if existing else None)
        if args.as_json:
            print(
                json.dumps(
                    {
                        "session_id": session_id,
                        "entry_id": entry_id,
                        "entries_compacted": 0,
                        "checkpoint_chars": len(content),
                        "est_tokens": est_tokens,
                        "updated": existing is not None,
                    }
                )
            )
        else:
            action = "Updated" if existing else "Created"
            print(f"✅ {action} checkpoint #{entry_id} for session {session_id[:30]}...")
            print(f"   Stored provided summary → {len(content)} chars (~{est_tokens} tokens)")
        conn.close()
        return

    entries = _get_entries(conn, session_id)
    if not entries:
        print(f"No knowledge entries found for session {session_id[:30]}...", file=sys.stderr)
        sys.exit(1)

    existing = _existing_checkpoint(conn, session_id)

    if args.no_llm:
        content = _template_compact(session_id, entries)
    else:
        prompt = _build_compact_prompt(session_id, entries)
        llm_result = _call_llm(prompt)
        content = llm_result if llm_result else _template_compact(session_id, entries)

    est_tokens = len(content) // 4

    if args.dry_run:
        print(f"DRY RUN — would compact session {session_id[:30]}...")
        print(f"Entries: {len(entries)}, Checkpoint: {len(content)} chars (~{est_tokens} tokens)")
        if existing:
            print(f"Would UPDATE existing checkpoint #{existing['id']}")
        else:
            print("Would CREATE new checkpoint entry")
        print("\n" + "─" * 40)
        print(content[:1000])
        conn.close()
        return

    entry_id = _store_checkpoint(conn, session_id, content, existing["id"] if existing else None)

    if args.as_json:
        print(
            json.dumps(
                {
                    "session_id": session_id,
                    "entry_id": entry_id,
                    "entries_compacted": len(entries),
                    "checkpoint_chars": len(content),
                    "est_tokens": est_tokens,
                    "updated": existing is not None,
                }
            )
        )
    else:
        action = "Updated" if existing else "Created"
        print(f"✅ {action} checkpoint #{entry_id} for session {session_id[:30]}...")
        print(f"   Compacted {len(entries)} entries → {len(content)} chars (~{est_tokens} tokens)")

    conn.close()


if __name__ == "__main__":
    main()
