#!/usr/bin/env python3
"""
extract-knowledge.py — Extract structured knowledge from session checkpoints

Parses checkpoint sections to identify and categorize:
  - Patterns: Reusable coding/architecture best practices
  - Mistakes: Errors made and lessons learned
  - Decisions: Technical choices and their rationale
  - Tools: Tool configurations and usage notes

Usage:
    python extract-knowledge.py                # Extract from all checkpoints
    python extract-knowledge.py --stats        # Show extraction statistics
    python extract-knowledge.py --list         # List all extracted entries
    python extract-knowledge.py --category mistakes  # Show specific category

Cross-platform: Windows, macOS, Linux. Pure Python stdlib.
"""

import sqlite3
import re
import sys
import os
from pathlib import Path
from datetime import datetime

# Fix Windows console encoding for Unicode output
if os.name == "nt":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

SESSION_STATE = Path.home() / ".copilot" / "session-state"
DB_PATH = SESSION_STATE / "knowledge.db"

# Extraction patterns — regex + heuristics for each category
MISTAKE_INDICATORS = [
    r"(?:mistake|error|bug|wrong|incorrect|broken|fail|crash|fix(?:ed)?)\b",
    r"(?:should\s+(?:have|not)|shouldn't|don't|avoid|never|careful)",
    r"(?:root\s+cause|caused\s+by|problem\s+was|issue\s+was)",
    # Vietnamese
    r"(?:lỗi|sai|sửa|tránh|không\s+nên|nguyên\s+nhân)",
    # Japanese
    r"(?:エラー|バグ|不具合|障害|修正|原因|間違い|注意)",
]

PATTERN_INDICATORS = [
    r"(?:always|must|should|convention|pattern|best\s+practice|rule)\b",
    r"(?:use\s+\w+\s+instead\s+of|prefer|recommend)",
    r"(?:standard|template|reusable|common\s+(?:pattern|style|approach))",
    # Vietnamese
    r"(?:luôn|nên|quy\s+tắc|mẫu|chuẩn)",
    # Japanese
    r"(?:パターン|ルール|規約|推奨|必須|ベストプラクティス|テンプレート)",
]

DECISION_INDICATORS = [
    r"(?:chose|decided|selected|picked|went\s+with|opted)\b",
    r"(?:because|reason|rationale|trade-off|tradeoff)",
    r"(?:option\s+[A-C]|alternative|compared|versus|vs\.?)\b",
    # Vietnamese
    r"(?:chọn|quyết\s+định|lý\s+do|so\s+sánh)",
    # Japanese
    r"(?:決定|選択|理由|比較|トレードオフ|代替案|方針)",
]

TOOL_INDICATORS = [
    r"(?:install|configure|setup|version|upgrade|dependency)\b",
    r"(?:yarn|npm|cdk|playwright|jest|eslint|prettier)\b",
    r"(?:docker|redis|postgres|dynamodb|lambda|s3)\b",
    r"(?:SDK|IDE|VSCode|extension|plugin|MCP)\b",
    # Vietnamese
    r"(?:cài|cấu\s+hình|phiên\s+bản|nâng\s+cấp)",
    # Japanese
    r"(?:インストール|設定|バージョン|依存関係|ツール|環境構築)",
]


def ensure_tables(db: sqlite3.Connection):
    """Create knowledge_entries table if not exists."""
    db.executescript("""
        CREATE TABLE IF NOT EXISTS knowledge_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            document_id INTEGER,
            category TEXT NOT NULL,
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            tags TEXT DEFAULT '',
            confidence REAL DEFAULT 1.0,
            occurrence_count INTEGER DEFAULT 1,
            first_seen TEXT,
            last_seen TEXT,
            UNIQUE(category, title, session_id)
        );

        CREATE INDEX IF NOT EXISTS idx_ke_category ON knowledge_entries(category);
        CREATE INDEX IF NOT EXISTS idx_ke_session ON knowledge_entries(session_id);
    """)

    # Recreate FTS table (standalone, no content= sync issues)
    try:
        db.execute("DROP TABLE IF EXISTS ke_fts")
    except Exception:
        pass
    db.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS ke_fts USING fts5(
            title, content, tags, category,
            tokenize='unicode61 remove_diacritics 2'
        )
    """)


def classify_paragraph(text: str) -> list[tuple[str, float]]:
    """Classify a paragraph into knowledge categories with confidence."""
    # Skip noise: interview Q&A, pure tables, pure code
    if _is_noise(text):
        return []

    text_lower = text.lower()
    results = []

    for category, indicators in [
        ("mistake", MISTAKE_INDICATORS),
        ("pattern", PATTERN_INDICATORS),
        ("decision", DECISION_INDICATORS),
        ("tool", TOOL_INDICATORS),
    ]:
        score = 0
        for pattern in indicators:
            matches = len(re.findall(pattern, text_lower, re.IGNORECASE))
            score += matches
        if score >= 2:  # At least 2 indicator matches
            confidence = min(1.0, score / 5.0)
            results.append((category, confidence))

    return results


# Noise detection patterns
_NOISE_PATTERNS = [
    r"(?:phỏng\s*vấn|interview|câu\s*hỏi|bộ\s*câu)",
    r"(?:đáp\s*án|mong\s*đợi|tiêu\s*chí|ghi\s*điểm)",
    r"(?:bảng\s*đánh\s*giá|evaluation\s*rubric)",
    r"(?:trọng\s*số|scoring|rubric|interviewer)",
    # Japanese noise
    r"(?:面接|インタビュー|採点|評価基準)",
]

# Strong noise — single match is enough to discard
_STRONG_NOISE_PATTERNS = [
    r"đáp\s*án\s*(mong\s*đợi|chi\s*tiết)",
    r"bảng\s*(đánh\s*giá|ghi\s*điểm)",
    r"câu\s*hỏi\s*phỏng\s*vấn",
    r"interview\s*question",
    r"nội\s*dung\s*cần\s*đề\s*cập",
    # Japanese strong noise
    r"面接\s*質問",
    r"採点\s*基準",
]


def _is_noise(text: str) -> bool:
    """Check if text is interview Q&A, scoring rubric, or other noise."""
    text_lower = text.lower()

    # Strong noise — single match enough
    for pattern in _STRONG_NOISE_PATTERNS:
        if re.search(pattern, text_lower):
            return True

    # Weak noise — need 2+ matches
    noise_score = 0
    for pattern in _NOISE_PATTERNS:
        if re.search(pattern, text_lower):
            noise_score += 1
    if noise_score >= 2:
        return True

    # Pure markdown table (>70% of lines are table rows)
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    if lines:
        table_lines = sum(1 for l in lines if l.startswith("|") and l.endswith("|"))
        if table_lines / len(lines) > 0.7 and len(lines) > 3:
            return True

    # Pure code block (>70% of content inside ```)
    code_chars = sum(len(m.group(0)) for m in re.finditer(r"```[\s\S]*?```", text))
    if len(text) > 100 and code_chars / len(text) > 0.7:
        return True

    return False


def extract_title(text: str, max_len: int = 100) -> str:
    """Extract a meaningful title from paragraph text."""
    lines = text.strip().split("\n")

    for line in lines[:5]:  # Try first 5 lines
        line = line.strip()
        if not line:
            continue
        # Skip table rows, code markers, empty headings
        if line.startswith("|") or line.startswith("```") or line.startswith("---"):
            continue
        # Skip lines that are just separators
        if re.match(r"^[-=_]{3,}$", line):
            continue

        # Remove markdown formatting
        title = re.sub(r"[#*_`\[\]]", "", line).strip()
        # Remove leading bullets/numbers/emoji
        title = re.sub(r"^[\d.)\-•]+\s*", "", title).strip()
        title = re.sub(r"^[^\w\s]{1,3}\s*", "", title).strip()  # emoji prefix

        if len(title) >= 10:
            if len(title) > max_len:
                title = title[:max_len - 3] + "..."
            return title

    # Fallback: first 100 chars of text
    fallback = re.sub(r"\s+", " ", text[:max_len]).strip()
    return fallback or "Untitled"


def extract_tags(text: str) -> str:
    """Extract relevant tags from text."""
    tag_patterns = [
        # Cloud & Infrastructure
        (r"\b(?:AWS|Amazon\s+Web\s+Services)\b", "aws"),
        (r"\b(?:CDK|Cloud\s+Development\s+Kit)\b", "aws-cdk"),
        (r"\b(?:Lambda)\b", "lambda"),
        (r"\b(?:DynamoDB|dynamo)\b", "dynamodb"),
        (r"\b(?:S3|s3\s+bucket)\b", "s3"),
        (r"\b(?:SQS|Simple\s+Queue)\b", "sqs"),
        (r"\b(?:SNS|Simple\s+Notification)\b", "sns"),
        (r"\b(?:Cognito)\b", "cognito"),
        (r"\b(?:CloudWatch)\b", "cloudwatch"),
        (r"\b(?:API\s+Gateway|APIGW)\b", "api-gateway"),
        (r"\b(?:EventBridge)\b", "eventbridge"),
        (r"\b(?:CloudFormation|CFN)\b", "cloudformation"),
        (r"\b(?:Step\s+Functions?)\b", "step-functions"),
        (r"\b(?:X-Ray|XRay)\b", "xray"),
        (r"\b(?:WebSocket|wss?://)\b", "websocket"),
        (r"\b(?:Docker|docker-compose)\b", "docker"),
        (r"\b(?:VPC|subnet|security\s+group)\b", "vpc"),
        # Languages & Runtimes
        (r"\b(?:TypeScript|\.tsx?)\b", "typescript"),
        (r"\b(?:JavaScript|jQuery|\.jsx?)\b", "javascript"),
        (r"\b(?:Python|\.py)\b", "python"),
        (r"\b(?:Node\.?js)\b", "nodejs"),
        # Frontend
        (r"\b(?:React\s+Native|RN)\b", "react-native"),
        (r"\b(?:Expo)\b", "expo"),
        (r"\b(?:React)\b", "react"),
        (r"\b(?:CSS|styles?\.css)\b", "css"),
        (r"\b(?:modal|dialog)\b", "ui"),
        # Testing
        (r"\b(?:Jest)\b", "jest"),
        (r"\b(?:Playwright)\b", "playwright"),
        (r"\b(?:E2E|end-to-end)\b", "e2e"),
        (r"\b(?:TDD|test-driven)\b", "tdd"),
        # Build & Tools
        (r"\b(?:ESLint|eslint)\b", "eslint"),
        (r"\b(?:Prettier)\b", "prettier"),
        (r"\b(?:yarn|npm)\b", "package-manager"),
        (r"\b(?:Git|git\s+hook|git\s+worktree)\b", "git"),
        (r"\b(?:VSCode|VS\s+Code)\b", "vscode"),
        (r"\b(?:Copilot)\b", "copilot"),
        # Data & Formats
        (r"\b(?:Excel|xlsx|csv|tsv)\b", "spreadsheet"),
        (r"\b(?:JSON|json)\b", "json"),
        (r"\b(?:OpenAPI|Swagger)\b", "openapi"),
        (r"\b(?:Mermaid)\b", "mermaid"),
        # Patterns & Concepts
        (r"\b(?:i18n|internationalization|locales?)\b", "i18n"),
        (r"\b(?:CRUD)\b", "crud"),
        (r"\b(?:pagination)\b", "pagination"),
        (r"\b(?:SQL|native\s+SQL)\b", "sql"),
        (r"\b(?:TLS|SSL|certificate|cert)\b", "tls"),
        (r"\b(?:proxy|MITM)\b", "proxy"),
        (r"\b(?:SAML|OAuth|JWT|token)\b", "auth"),
        # Legacy (keep for backward compatibility)
        (r"\b(?:Spring\s+Boot|SpringBoot)\b", "spring-boot"),
        (r"\b(?:PostgreSQL|Postgres|PG)\b", "postgresql"),
        (r"\b(?:Redis)\b", "redis"),
        (r"\b(?:JPA|Hibernate)\b", "jpa"),
        (r"\b(?:Gradle|Maven)\b", "java-build"),
    ]

    tags = set()
    for pattern, tag in tag_patterns:
        if re.search(pattern, text, re.IGNORECASE):
            tags.add(tag)
    return ",".join(sorted(tags))


def split_into_knowledge_chunks(content: str) -> list[str]:
    """Split section content into meaningful chunks for classification."""
    chunks = []

    # Split by numbered items, bullet points, or double newlines
    # Prefer structured items (numbered lists, bullets)
    items = re.split(r"\n(?=\d+\.\s|\-\s|\*\s|#{1,3}\s)", content)

    for item in items:
        item = item.strip()
        if len(item) < 30:  # Skip very short fragments
            continue
        if len(item) > 2000:  # Split long chunks by paragraphs
            paragraphs = item.split("\n\n")
            for p in paragraphs:
                if len(p.strip()) >= 30:
                    chunks.append(p.strip())
        else:
            chunks.append(item)

    return chunks


def extract_from_sections(db: sqlite3.Connection):
    """Extract knowledge entries from all indexed sections."""
    now = datetime.now().isoformat()
    extracted = 0
    skipped = 0

    # Focus on the most knowledge-rich sections
    target_sections = ["technical_details", "history", "work_done", "next_steps", "full"]

    rows = db.execute("""
        SELECT s.id, s.document_id, s.section_name, s.content, d.session_id
        FROM sections s
        JOIN documents d ON s.document_id = d.id
        WHERE s.section_name IN ({})
        ORDER BY d.session_id, d.seq
    """.format(",".join(f"'{s}'" for s in target_sections))).fetchall()

    for section_id, doc_id, section_name, content, session_id in rows:
        chunks = split_into_knowledge_chunks(content)

        for chunk in chunks:
            classifications = classify_paragraph(chunk)

            for category, confidence in classifications:
                title = extract_title(chunk)
                tags = extract_tags(chunk)

                try:
                    db.execute("""
                        INSERT INTO knowledge_entries
                        (session_id, document_id, category, title, content, tags, confidence, first_seen, last_seen)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(category, title, session_id) DO UPDATE SET
                            confidence = MAX(knowledge_entries.confidence, excluded.confidence),
                            occurrence_count = knowledge_entries.occurrence_count + 1,
                            last_seen = excluded.last_seen
                    """, (session_id, doc_id, category, title, chunk[:3000], tags, confidence, now, now))
                    extracted += 1
                except sqlite3.IntegrityError:
                    skipped += 1

    # Rebuild FTS
    db.execute("DELETE FROM ke_fts")
    db.execute("""
        INSERT INTO ke_fts (rowid, title, content, tags, category)
        SELECT id, title, content, tags, category FROM knowledge_entries
    """)

    db.commit()
    return extracted, skipped


def show_stats(db: sqlite3.Connection):
    """Show extraction statistics."""
    print(f"\n{'='*50}")
    print(f"  Knowledge Extraction Statistics")
    print(f"{'='*50}")

    total = db.execute("SELECT COUNT(*) FROM knowledge_entries").fetchone()[0]
    print(f"  Total entries: {total}")

    print("\n  By category:")
    for row in db.execute("""
        SELECT category, COUNT(*), ROUND(AVG(confidence), 2)
        FROM knowledge_entries GROUP BY category ORDER BY COUNT(*) DESC
    """):
        print(f"    {row[0]:12s}: {row[1]:3d} entries (avg confidence: {row[2]})")

    print("\n  Top tags:")
    # Manually count tags since they're comma-separated
    tag_counts = {}
    for row in db.execute("SELECT tags FROM knowledge_entries WHERE tags != ''"):
        for tag in row[0].split(","):
            tag = tag.strip()
            if tag:
                tag_counts[tag] = tag_counts.get(tag, 0) + 1
    for tag, count in sorted(tag_counts.items(), key=lambda x: -x[1])[:15]:
        print(f"    {tag:20s}: {count}")

    print(f"\n  Cross-session patterns (appearing in 2+ sessions):")
    for row in db.execute("""
        SELECT title, category, COUNT(DISTINCT session_id) as sessions
        FROM knowledge_entries
        GROUP BY title, category
        HAVING sessions >= 2
        ORDER BY sessions DESC
        LIMIT 10
    """):
        print(f"    [{row[1]}] {row[0][:60]} ({row[2]} sessions)")


def list_entries(db: sqlite3.Connection, category: str = None, limit: int = 20):
    """List knowledge entries."""
    sql = "SELECT id, category, title, tags, confidence, session_id FROM knowledge_entries"
    params = []
    if category:
        sql += " WHERE category = ?"
        params.append(category)
    sql += " ORDER BY confidence DESC, category LIMIT ?"
    params.append(limit)

    print(f"\n{'ID':>4s} {'Category':12s} {'Conf':>5s} {'Session':10s} Title")
    print(f"{'─'*4} {'─'*12} {'─'*5} {'─'*10} {'─'*50}")

    for row in db.execute(sql, params):
        sid = row[5][:8] + ".."
        print(f"{row[0]:4d} {row[1]:12s} {row[4]:5.2f} {sid:10s} {row[2][:50]}")


def main():
    args = sys.argv[1:]

    if "--help" in args or "-h" in args:
        print(__doc__)
        return

    if not DB_PATH.exists():
        print(f"Error: Knowledge database not found at {DB_PATH}")
        print("Run 'python build-session-index.py' first.")
        sys.exit(1)

    db = sqlite3.connect(str(DB_PATH))
    ensure_tables(db)

    if "--stats" in args:
        show_stats(db)
        db.close()
        return

    if "--list" in args:
        category = None
        if "--category" in args:
            idx = args.index("--category")
            category = args[idx + 1] if idx + 1 < len(args) else None
        limit = 20
        if "--limit" in args:
            idx = args.index("--limit")
            limit = int(args[idx + 1]) if idx + 1 < len(args) else 20
        list_entries(db, category, limit)
        db.close()
        return

    # Default: run extraction
    print("Extracting knowledge from indexed sessions...")
    extracted, skipped = extract_from_sections(db)
    print(f"Extracted {extracted} entries ({skipped} duplicates skipped)")
    show_stats(db)
    db.close()


if __name__ == "__main__":
    main()
