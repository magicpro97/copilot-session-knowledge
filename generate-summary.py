#!/usr/bin/env python3
"""Generate curated knowledge summary from extracted entries."""

import os
import sqlite3
import sys
from pathlib import Path

if os.name == "nt":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


if __name__ == "__main__":
    DB = Path.home() / ".copilot" / "session-state" / "knowledge.db"
    db = None

    if not DB.exists():
        print(f"❌ knowledge.db not found at {DB}. Run build-session-index.py first.")
        sys.exit(1)

    try:
        db = sqlite3.connect(str(DB))
        db.row_factory = sqlite3.Row

        # Verify the required table exists
        tables = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
        if "knowledge_entries" not in tables:
            print("❌ knowledge_entries table not found. Run extract-knowledge.py first.")
            db.close()
            sys.exit(1)

        out = []
        out.append("# B.R.A.I.N. Project — Curated Knowledge Base\n")
        out.append("Auto-generated from 12 Copilot sessions, 24 checkpoints, 565 knowledge entries.\n")
        out.append("Read this before starting any task on the B.R.A.I.N. project.\n")
        out.append("---\n")

        # MISTAKES
        out.append("## 🚫 Common Mistakes & Lessons Learned\n")
        seen = set()
        for r in db.execute("""
            SELECT title, content, tags, confidence, occurrence_count
            FROM knowledge_entries WHERE category = 'mistake'
            ORDER BY confidence DESC, occurrence_count DESC LIMIT 30
        """):
            title = r["title"][:80]
            if title in seen:
                continue
            seen.add(title)
            content = r["content"]
            lines = [l.strip() for l in content.split("\n") if l.strip() and len(l.strip()) > 20]
            code_lines = sum(1 for l in lines if l.startswith(("```", "|", "//", "/*", "import ", "package ")))
            if len(lines) > 0 and code_lines / len(lines) > 0.7:
                continue
            tags = f" `{r['tags']}`" if r["tags"] else ""
            out.append(f"### {title}{tags}\n")
            for l in lines[:5]:
                if len(l) > 200:
                    l = l[:200] + "..."
                out.append(f"- {l}")
            out.append("")

        # PATTERNS
        out.append("\n## ✅ Patterns & Best Practices\n")
        seen = set()
        for r in db.execute("""
            SELECT title, content, tags, confidence
            FROM knowledge_entries WHERE category = 'pattern'
            ORDER BY confidence DESC LIMIT 20
        """):
            title = r["title"][:80]
            if title in seen:
                continue
            seen.add(title)
            content = r["content"]
            lines = [l.strip() for l in content.split("\n") if l.strip() and len(l.strip()) > 20]
            code_lines = sum(1 for l in lines if l.startswith(("```", "|", "//", "/*")))
            if len(lines) > 0 and code_lines / len(lines) > 0.7:
                continue
            tags = f" `{r['tags']}`" if r["tags"] else ""
            out.append(f"### {title}{tags}\n")
            for l in lines[:5]:
                if len(l) > 200:
                    l = l[:200] + "..."
                out.append(f"- {l}")
            out.append("")

        # DECISIONS
        out.append("\n## 🎯 Architecture Decisions\n")
        for r in db.execute("""
            SELECT title, content, tags, confidence
            FROM knowledge_entries WHERE category = 'decision'
            ORDER BY confidence DESC LIMIT 15
        """):
            title = r["title"][:80]
            content = r["content"]
            lines = [l.strip() for l in content.split("\n") if l.strip() and len(l.strip()) > 20]
            code_lines = sum(1 for l in lines if l.startswith(("```", "|", "//", "/*")))
            if len(lines) > 0 and code_lines / len(lines) > 0.7:
                continue
            tags = f" `{r['tags']}`" if r["tags"] else ""
            out.append(f"### {title}{tags}\n")
            for l in lines[:4]:
                if len(l) > 200:
                    l = l[:200] + "..."
                out.append(f"- {l}")
            out.append("")

        # TOP TOOLS/CONFIG
        out.append("\n## 🔧 Key Tool Configurations\n")
        seen = set()
        for r in db.execute("""
            SELECT title, content, tags, confidence
            FROM knowledge_entries WHERE category = 'tool' AND tags != ''
            ORDER BY confidence DESC, LENGTH(tags) DESC LIMIT 20
        """):
            title = r["title"][:80]
            if title in seen:
                continue
            seen.add(title)
            content = r["content"]
            lines = [l.strip() for l in content.split("\n") if l.strip() and len(l.strip()) > 15]
            tags = f" `{r['tags']}`" if r["tags"] else ""
            out.append(f"### {title}{tags}\n")
            for l in lines[:6]:
                if len(l) > 200:
                    l = l[:200] + "..."
                out.append(f"- {l}")
            out.append("")

        out.append("\n---\n")
        out.append("*Auto-generated by extract-knowledge.py from ~/.copilot/session-state/*\n")
        out.append("*Refresh: `python ~/.copilot/tools/generate-summary.py`*\n")

        text = "\n".join(out)
        outpath = Path.home() / ".copilot" / "tools" / "KNOWLEDGE.md"
        outpath.write_text(text, encoding="utf-8")
        print(f"Generated {outpath} ({len(text)} chars, {len(out)} lines)")

        mistakes = text.count("### ")
        print(f"Sections: ~{mistakes} entries across 4 categories")

    except sqlite3.OperationalError as e:
        print(f"❌ Database error: {e}")
        sys.exit(1)
    finally:
        if db is not None:
            db.close()
