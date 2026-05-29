#!/usr/bin/env python3
"""
learn.py — Record new knowledge from AI agent sessions

Allows AI agents (Copilot, Claude, Cursor) to write learnings back to the
shared knowledge base during or after work.

Usage:
    python learn.py --mistake "Title" "Description of what went wrong and fix"
    python learn.py --pattern "Title" "Description of what works well"
    python learn.py --decision "Title" "Architecture decision and rationale"
    python learn.py --tool "Title" "Tool/config that was useful"
    python learn.py --feature "Title" "New feature implementation details"
    python learn.py --refactor "Title" "Code improvement description"
    python learn.py --discovery "Title" "Codebase finding or insight"

    python learn.py --mistake "Title" "Description" --tags "docker,compose"
    python learn.py --mistake "Title" "Description" --session abc123
    python learn.py --mistake "Title" "Description" --confidence 0.8
    python learn.py --mistake "Title" "Description" --wing backend --room dynamodb
    python learn.py --mistake "Title" "Description" --priority P0
    python learn.py --pattern "Title" "Description" --priority P1 --fact "batch limit is 25" --fact "GSI eventual"
    python learn.py --mistake "Title" "Description" --task "memory-surface" --file "briefing.py" --file "learn.py"
    python learn.py --pattern "Title" "Description" --code-location "path/to/file.py:50-75"

    python learn.py --relate "copyToGroup" "reads_from" "patient-dynamic-form.json"
    python learn.py --relate "addPatient Lambda" "writes_to" "dataTable"

    python learn.py --from-file notes.md          # Bulk import from markdown
    python learn.py --from-checkpoint path/to/checkpoint.md   # Batch ingest by ## heading
    python learn.py --from-checkpoint path/to/file.md --dry-run  # Preview without inserting
    python learn.py --from-pr 576                 # Batch ingest from PR body
    python learn.py --from-pr https://github.com/org/repo/pull/576  # PR URL also works
    python learn.py --from-file notes.md --as-category discovery   # Insert whole file as one entry
    python learn.py --flush-inbox                 # Replay entries queued while DB was locked
    python learn.py --list                        # List recent entries
    python learn.py --stats                       # Show knowledge stats

Auto-update cerebrum snapshot (opt-in):
    python learn.py --pattern "Title" "Desc" --update-cerebrum
    python learn.py --mistake "Title" "Desc" --update-cerebrum --cerebrum-output CEREBRUM.md
    python learn.py --decision "Title" "Desc" --update-cerebrum --cerebrum-sections mistakes,decisions
"""

import hashlib
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

if os.name == "nt":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

TOOLS_DIR = Path(__file__).parent
SESSION_STATE = Path.home() / ".copilot" / "session-state"
DB_PATH = Path(os.environ.get("SK_DB_PATH", str(SESSION_STATE / "knowledge.db"))).expanduser()
LEARN_INBOX = Path(os.environ.get("SK_LEARN_INBOX", str(SESSION_STATE / "learn-inbox"))).expanduser()
DEFAULT_DB_BUSY_TIMEOUT_MS = 30_000
DEFAULT_LEARN_QUEUE_BUSY_TIMEOUT_MS = 250


def _emit_knowledge_event_fail_open(event_type: str, data: dict) -> None:
    try:
        events_script = Path(__file__).with_name("events.py")
        if not events_script.is_file():
            return
        subprocess.call(
            [
                sys.executable,
                str(events_script),
                "append",
                event_type,
                "--data",
                json.dumps(data, ensure_ascii=False),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
    except Exception:
        return


def _queue_learn_payload(payload: dict) -> Path:
    """Persist a learn write for later replay when SQLite is temporarily locked."""
    LEARN_INBOX.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
    name = f"{time.strftime('%Y%m%dT%H%M%S')}-{time.time_ns()}-{digest}.json"
    final_path = LEARN_INBOX / name
    tmp_path = LEARN_INBOX / f".{name}.tmp"
    tmp_path.write_bytes(raw.encode("utf-8") + b"\n")
    os.replace(tmp_path, final_path)
    return final_path


def _build_learn_payload(
    argv: list[str],
    entry_kwargs: dict,
    *,
    update_cerebrum: bool = False,
    cerebrum_output: str = "CEREBRUM.md",
    cerebrum_sections: str | None = None,
) -> dict:
    return {
        "schema_version": 1,
        "queued_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "reason": "database_locked",
        "argv": list(argv),
        "entry": entry_kwargs,
        "cerebrum": {
            "update": update_cerebrum,
            "output": cerebrum_output,
            "sections": cerebrum_sections,
        },
    }


def _replay_queued_payload(payload: dict) -> tuple[int, int]:
    entry = dict(payload.get("entry") or {})
    entry_id = _write_learn_entry(entry)
    if entry_id >= 0 and entry.get("category") == "pattern":
        _emit_knowledge_event_fail_open(
            "pattern_learned",
            {
                "entry_id": entry_id,
                "title": entry.get("title", ""),
                "task_id": entry.get("task_id", ""),
                "wing": entry.get("wing", ""),
                "room": entry.get("room", ""),
                "priority": entry.get("priority") or "P2",
                "confidence": entry.get("confidence"),
            },
        )
    cerebrum_rc = 0
    cerebrum = payload.get("cerebrum") or {}
    if entry_id >= 0 and cerebrum.get("update"):
        cerebrum_rc = _auto_update_cerebrum(
            cerebrum.get("output") or "CEREBRUM.md",
            cerebrum.get("sections"),
            json_mode=True,
        )
    return entry_id, cerebrum_rc


def _write_learn_entry(
    entry_kwargs: dict,
    *,
    max_attempts: int = 5,
    base_delay: float = 0.5,
    db_busy_timeout_ms: int | None = None,
) -> int:
    """Write an entry through the standard retry path, preserving CLI call shape."""
    entry = dict(entry_kwargs)
    category = entry.pop("category")
    title = entry.pop("title")
    content = entry.pop("content")
    return with_retry(
        add_entry,
        category,
        title,
        content,
        max_attempts=max_attempts,
        base_delay=base_delay,
        db_busy_timeout_ms=db_busy_timeout_ms,
        **entry,
    )


_INBOX_FILENAME_HASH_RE = re.compile(r"-([0-9a-f]{16})\.json$")


def _inbox_filename_hash(path: Path) -> str | None:
    """Return the 16-hex hash component embedded in a queue filename, or None."""
    m = _INBOX_FILENAME_HASH_RE.search(path.name)
    return m.group(1) if m else None


def _validate_inbox_file(path: Path) -> tuple[bool, str | None, dict | None]:
    """Return (ok, reason, payload).

    Security gate for issue #573: every queued JSON file must:
      1. Have a filename of the form `<ts>-<ns>-<16hex>.json` where `<16hex>`
         is the first 16 hex chars of sha256(file_bytes.rstrip(b"\\n")).
         Both the Python and Rust producers strip exactly one trailing newline,
         so this single check covers both writers without coupling to either's
         JSON serialization choices.
      2. Parse as JSON with a dict body containing schema_version==1 and a
         dict `entry` carrying string category/title/content.

    The hash check rejects tampered/poisoned files (e.g. a file dropped into
    the inbox with a hand-crafted payload that does not match its filename).
    On validation failure the file is treated by the caller as `.rejected`.
    """
    try:
        raw_bytes = path.read_bytes()
    except OSError as exc:
        return False, f"read_error:{exc}", None

    expected_hash = _inbox_filename_hash(path)
    if expected_hash is None:
        return False, "filename_format_invalid", None

    # Producers append exactly one trailing newline; strip CRLF (Windows
    # legacy) and bare LF so files survive any cross-platform transport.
    if raw_bytes.endswith(b"\r\n"):
        stripped = raw_bytes[:-2]
    elif raw_bytes.endswith(b"\n"):
        stripped = raw_bytes[:-1]
    else:
        stripped = raw_bytes
    actual_hash = hashlib.sha256(stripped).hexdigest()[:16]
    if actual_hash != expected_hash:
        return False, f"hash_mismatch:{actual_hash}!={expected_hash}", None

    try:
        payload = json.loads(stripped.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return False, f"json_decode_error:{exc}", None

    if not isinstance(payload, dict):
        return False, "payload_not_object", None
    if payload.get("schema_version") != 1:
        return False, f"schema_version!=1 got={payload.get('schema_version')!r}", None
    entry = payload.get("entry")
    if not isinstance(entry, dict):
        return False, "entry_not_object", None
    for required in ("category", "title", "content"):
        if not isinstance(entry.get(required), str) or not entry.get(required):
            return False, f"missing_field:{required}", None
    return True, None, payload


def flush_learn_inbox(
    limit: int = 100,
    min_age_s: float | None = None,
    per_entry_timeout_s: float | None = None,
    wall_budget_s: float | None = None,
) -> dict:
    """Replay queued learn writes in FIFO order by mtime.

    Args:
        limit: maximum number of files to process this call.
        min_age_s: when set, only process files whose mtime is at least this
            many seconds in the past. Used by auto-flush retry-stale-only mode
            (`SK_AUTOFLUSH_MAX_AGE_S`); unset = process all ages.
        per_entry_timeout_s: soft per-entry budget. Replay itself is not
            cancellable (SQLite call), but elapsed >= budget causes the loop
            to stop scheduling new entries.
        wall_budget_s: soft total budget across all entries.

    Returns dict with status, processed, remaining, failed, rejected counts.
    Backwards-compatible — additional `rejected` key is new (defaults to 0).
    """
    if not LEARN_INBOX.exists():
        return {
            "status": "ok",
            "processed": 0,
            "remaining": 0,
            "failed": 0,
            "rejected": 0,
            "cerebrum_failed": 0,
        }

    # FIFO by mtime (DoD #573). Falls back to name if mtime is unavailable.
    def _sort_key(p: Path) -> tuple[float, str]:
        try:
            return (p.stat().st_mtime, p.name)
        except OSError:
            return (0.0, p.name)

    files = sorted(LEARN_INBOX.glob("*.json"), key=_sort_key)
    now = time.time()
    if min_age_s is not None and min_age_s > 0:
        files = [p for p in files if (now - _sort_key(p)[0]) >= min_age_s]

    processed = 0
    failed = 0
    rejected = 0
    cerebrum_failed = 0
    busy = False
    started = time.monotonic()

    for path in files[: max(0, limit)]:
        if wall_budget_s is not None and (time.monotonic() - started) >= wall_budget_s:
            break

        ok, reason, payload = _validate_inbox_file(path)
        if not ok:
            try:
                path.rename(path.with_suffix(path.suffix + ".rejected"))
            except OSError:
                pass
            rejected += 1
            continue

        entry_started = time.monotonic()
        try:
            _, cerebrum_rc = _replay_queued_payload(payload)
            path.unlink()
            processed += 1
            if cerebrum_rc != 0:
                cerebrum_failed += 1
        except sqlite3.OperationalError as exc:
            if _is_busy_error(exc):
                busy = True
                break
            failed += 1
            try:
                path.rename(path.with_suffix(path.suffix + ".failed"))
            except OSError:
                pass
        except Exception:
            failed += 1
            try:
                path.rename(path.with_suffix(path.suffix + ".failed"))
            except OSError:
                pass

        if per_entry_timeout_s is not None and (time.monotonic() - entry_started) >= per_entry_timeout_s:
            # Soft budget exceeded for this entry: stop scheduling new work.
            break

    remaining = len(list(LEARN_INBOX.glob("*.json"))) if LEARN_INBOX.exists() else 0
    return {
        "status": "busy" if busy else "ok",
        "processed": processed,
        "remaining": remaining,
        "failed": failed,
        "rejected": rejected,
        "cerebrum_failed": cerebrum_failed,
    }


# Wing auto-detection rules: tag patterns → wing
_WING_RULES = [
    (
        {
            "lambda",
            "dynamodb",
            "sqs",
            "cdk",
            "api",
            "cognito",
            "s3",
            "eventbridge",
            "cloudwatch",
            "sns",
            "websocket",
            "nefoap",
            "database",
            "sql",
            "sqlite",
            "postgres",
            "mysql",
            "mongo",
            "redis",
            "rest",
            "graphql",
            "http",
            "webhook",
            "grpc",
            "microservice",
            "endpoint",
            "server",
        },
        "backend",
    ),
    (
        {
            "expo",
            "react",
            "react-native",
            "screen",
            "component",
            "css",
            "ui",
            "navigation",
            "hook",
            "vue",
            "angular",
            "svelte",
            "tailwind",
            "nextjs",
            "nuxt",
        },
        "frontend",
    ),
    (
        {"jest", "playwright", "e2e", "test", "testing", "coverage", "pytest", "vitest", "unittest", "mock", "fixture"},
        "testing",
    ),
    (
        {
            "vpc",
            "cloudwatch",
            "cdk",
            "cloudformation",
            "infrastructure",
            "deploy",
            "pipeline",
            "kubernetes",
            "helm",
            "terraform",
            "ansible",
            "k8s",
        },
        "infrastructure",
    ),
    (
        {
            "git",
            "ci",
            "cd",
            "docker",
            "devops",
            "proxy",
            "tls",
            "npm",
            "yarn",
            "package-manager",
            "github-actions",
            "gitlab-ci",
            "makefile",
            "build",
        },
        "devops",
    ),
    (
        {
            "typescript",
            "javascript",
            "eslint",
            "prettier",
            "i18n",
            "mermaid",
            "openapi",
            "python",
            "rust",
            "golang",
            "java",
            "kotlin",
            "swift",
        },
        "shared",
    ),
    ({"knowledge", "memory", "briefing", "learn", "recall", "extract", "session", "sk", "session-knowledge"}, "memory"),
]

# Room auto-detection rules: tag/title patterns → room
_ROOM_RULES = [
    ({"patient", "patient-search", "傷病者"}, "patient"),
    ({"hospital", "病院"}, "hospital"),
    ({"copytogroup", "copy-to-group", "傷病者追加"}, "copyToGroup"),
    ({"websocket", "ws"}, "websocket"),
    ({"dynamodb", "dao", "repository"}, "dynamodb"),
    ({"auth", "cognito", "login"}, "auth"),
    ({"s3", "media", "upload", "presigned"}, "s3-media"),
    ({"sqs", "queue", "consumer"}, "sqs"),
    ({"notification", "通知"}, "notification"),
    ({"audit", "audit-log"}, "audit-log"),
    ({"nefoap", "指令"}, "nefoap"),
    ({"lambda", "handler"}, "lambda"),
    ({"playwright", "e2e"}, "e2e"),
    ({"excel", "spreadsheet", "tsv", "csv"}, "data-export"),
    ({"cdk", "cloudformation", "stack"}, "cdk"),
    ({"sqlite", "postgres", "mysql", "mongo", "redis", "database", "sql"}, "database"),
    ({"rest", "graphql", "api", "endpoint", "http", "webhook"}, "api"),
    ({"kubernetes", "helm", "k8s", "pod", "deployment"}, "kubernetes"),
    ({"knowledge", "memory", "briefing", "learn", "recall"}, "memory"),
    ({"session", "session-knowledge", "sk"}, "session"),
    ({"docker", "dockerfile", "container", "compose"}, "docker"),
    ({"github-actions", "gitlab-ci", "pipeline", "ci", "cd"}, "ci-cd"),
    ({"typescript", "javascript", "eslint", "prettier"}, "js-ts"),
    ({"python", "pip", "venv", "conda"}, "python"),
    ({"rust", "cargo", "tokio", "wasm"}, "rust"),
]


def _detect_wing(tags: str, title: str, content: str) -> str:
    """Auto-detect wing from tags/title/content."""
    tag_set = {t.strip().lower() for t in tags.split(",") if t.strip()}
    text_lower = f"{title} {content[:200]}".lower()
    for patterns, wing in _WING_RULES:
        if tag_set & patterns:
            return wing
        if any(p in text_lower for p in patterns):
            return wing
    return ""


def _detect_room(tags: str, title: str, content: str) -> str:
    """Auto-detect room from tags/title/content."""
    tag_set = {t.strip().lower() for t in tags.split(",") if t.strip()}
    text_lower = f"{title} {content[:300]}".lower()
    for patterns, room in _ROOM_RULES:
        if tag_set & patterns:
            return room
        if any(p in text_lower for p in patterns):
            return room
    return ""


# Stopwords for concept tag extraction (pure stdlib, no ML imports)
_CONCEPT_STOPWORDS = frozenset(
    {
        "the",
        "a",
        "an",
        "and",
        "or",
        "but",
        "in",
        "on",
        "at",
        "to",
        "for",
        "of",
        "with",
        "by",
        "from",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "have",
        "has",
        "had",
        "do",
        "does",
        "did",
        "will",
        "would",
        "could",
        "should",
        "may",
        "might",
        "can",
        "it",
        "this",
        "that",
        "these",
        "those",
        "i",
        "we",
        "you",
        "he",
        "she",
        "they",
        "not",
        "no",
        "so",
        "if",
        "then",
        "when",
        "where",
        "what",
        "which",
        "who",
        "how",
        "all",
        "any",
        "each",
        "more",
        "most",
        "also",
        "just",
        "up",
        "out",
        "as",
        "into",
        "than",
        "their",
        "its",
        "our",
        "my",
        "your",
        "his",
        "her",
        "them",
        "us",
        "me",
        "after",
        "before",
        "during",
        "while",
        "since",
        "until",
        "too",
        "very",
        "about",
        "above",
        "below",
        "between",
        "through",
        "use",
        "used",
        "using",
        "run",
        "running",
        "make",
        "new",
        "only",
        "now",
        "time",
        "way",
        "need",
        "needs",
        "see",
        "get",
        "set",
        "add",
        "put",
        "let",
        "say",
        "one",
        "two",
        "per",
        "via",
        "etc",
        "yet",
        "got",
    }
)

# Injection scanning patterns (inspired by Hermes Agent memory security)
# Block prompt injection, role hijacking, credential exfiltration, invisible Unicode
# WBS-019: expanded with JWT, bearer, and AWS-like secret patterns
import re


class _ContextAwareHexMatcher:
    """Matches long lowercase-hex strings only when NOT in a git/checksum context.

    Prevents false-positive blocking of 40-char git commit SHAs and 64-char
    SHA-256 checksums while still catching bare secret hex tokens that appear
    without any identifying reference keyword nearby.

    Duck-types the compiled regex interface: implements .search(text) returning
    a match object (or None) so it can be used transparently in _INJECTION_PATTERNS.
    """

    _HEX_RE = re.compile(r"(?<![A-Za-z0-9])([0-9a-f]{40,})(?![A-Za-z0-9])")
    # Keywords that indicate the hex string is a commit SHA or checksum reference,
    # not a raw secret.  Checked in the 100-char window *before* the hex run.
    _SAFE_CTX_RE = re.compile(r"(?i)\b(?:commit|sha\d*|hash|checksum|digest|fingerprint)\b")

    def search(self, text: str):
        """Return the first match for a secret-context hex run; None if all safe."""
        for m in self._HEX_RE.finditer(text):
            pre = text[max(0, m.start() - 100) : m.start()]
            if self._SAFE_CTX_RE.search(pre):
                continue  # preceded by commit/hash/checksum keyword — likely legitimate
            return m  # no safe context — treat as potential credential
        return None


_INJECTION_PATTERNS = [
    (
        re.compile(r"(?i)\bignore\s+(all\s+)?previous\s+instructions?\b"),
        "prompt injection: 'ignore previous instructions'",
    ),
    (re.compile(r"(?i)\byou\s+are\s+now\b"), "role hijacking: 'you are now'"),
    (re.compile(r"(?i)\bsystem\s*:\s*"), "role injection: 'system:' prefix"),
    (re.compile(r"(?i)\b(assistant|user|human)\s*:\s*"), "role injection: fake role prefix"),
    (re.compile(r"(?i)\bforget\s+(everything|all|your)\b"), "memory manipulation: 'forget everything'"),
    (re.compile(r"(?i)\bdo\s+not\s+follow\b"), "instruction override: 'do not follow'"),
    (
        re.compile(r"(?i)\b(api[_-]?key|secret[_-]?key|password|token)\s*[:=]\s*\S+"),
        "credential leak: API key/password/token",
    ),
    (re.compile(r"(?i)ssh-rsa\s+AAAA"), "credential leak: SSH public key"),
    (re.compile(r"(?i)-----BEGIN\s+(RSA\s+)?PRIVATE\s+KEY-----"), "credential leak: private key"),
    (re.compile(r"(?i)\beval\s*\("), "code injection: eval()"),
    (re.compile(r"(?i)\bexec\s*\("), "code injection: exec()"),
    (re.compile(r"[\u200b\u200c\u200d\u2060\ufeff]"), "invisible Unicode characters (zero-width)"),
    (re.compile(r"(?i)\bACT\s+AS\b"), "role hijacking: 'act as'"),
    (re.compile(r"(?i)\bpretend\s+(you\s+are|to\s+be)\b"), "role hijacking: 'pretend to be'"),
    (re.compile(r"(?i)\b(curl|wget|nc|ncat)\s+.*\|\s*(ba)?sh\b"), "remote code execution pattern"),
    # WBS-019: JWT tokens (3-part base64url separated by dots, header starts with eyJ)
    (
        re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"),
        "credential leak: JWT token",
    ),
    # WBS-019: Authorization header with bearer token
    (
        re.compile(r"(?i)\bAuthorization\s*:\s*Bearer\s+\S{16,}"),
        "credential leak: Authorization Bearer token",
    ),
    # WBS-019: AWS-style secret keys (AKIA... access key or 40-char base62 secret)
    (
        re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
        "credential leak: AWS access key ID",
    ),
    (
        re.compile(r"(?i)\b(aws[_-]?secret[_-]?access[_-]?key|aws[_-]?secret)\s*[:=]\s*[A-Za-z0-9/+]{30,}"),
        "credential leak: AWS secret access key",
    ),
    # WBS-019: generic long hex token (context-aware — skips git commit SHAs and checksums)
    # See _ContextAwareHexMatcher below for the false-positive mitigation logic.
    (
        _ContextAwareHexMatcher(),
        "credential leak: long hex secret/token",
    ),
]

_CODE_LANGUAGE_MAP = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "jsx",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".kt": "kotlin",
    ".swift": "swift",
    ".sh": "bash",
    ".md": "markdown",
}


def _stable_sha256(*parts) -> str:
    payload = "\0".join("" if p is None else str(p) for p in parts)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def extract_concept_tags(text: str, top_k: int = 5) -> list:
    """Extract top_k concept tags from text using pure-stdlib term frequency.

    Distinct from existing tag parsing that reads explicit user-supplied tags.
    This performs automatic keyword extraction from free-form text using
    stopword-filtered term frequency, with no numpy/sklearn/ML imports.

    Args:
        text: Combined title and content text to analyze.
        top_k: Maximum number of concept tags to return.

    Returns:
        List of up to top_k lowercase concept tag strings, sorted by frequency desc.
    """
    if not text:
        return []
    tokens = re.findall(r"[a-zA-Z][a-zA-Z0-9_-]{2,}", text.lower())
    freq: dict = {}
    for tok in tokens:
        if tok not in _CONCEPT_STOPWORDS:
            freq[tok] = freq.get(tok, 0) + 1
    ranked = sorted(freq.items(), key=lambda x: (-x[1], x[0]))
    return [tag for tag, _ in ranked[:top_k]]


def _auto_tag_entry(db: sqlite3.Connection, entry_id: int, title: str, content: str) -> None:
    """Insert auto-generated concept tags for an entry, replacing any stale auto tags.

    Safe to call for both insert and update paths:
    - On update: deletes existing source='auto' rows first, then inserts fresh tags.
    - On insert: simply inserts fresh tags (no prior rows exist).
    - Gracefully no-ops if the entry_concept_tags table does not exist yet.
    """
    try:
        has_table = db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='entry_concept_tags'"
        ).fetchone()
        if not has_table:
            return
        tags = extract_concept_tags(f"{title} {content}", top_k=5)
        if not tags:
            return
        now = time.strftime("%Y-%m-%dT%H:%M:%S")
        # Atomic delete-then-insert via SAVEPOINT so a failed insert cannot
        # commit the deletion and silently erase existing tags.
        db.execute("SAVEPOINT _auto_tag")
        try:
            db.execute(
                "DELETE FROM entry_concept_tags WHERE entry_id = ? AND source = 'auto'",
                (entry_id,),
            )
            db.executemany(
                """
                INSERT INTO entry_concept_tags (entry_id, tag, source, tagged_at)
                VALUES (?, ?, 'auto', ?)
                ON CONFLICT(entry_id, tag) DO UPDATE SET tagged_at = excluded.tagged_at
                """,
                [(entry_id, tag, now) for tag in tags],
            )
            db.execute("RELEASE _auto_tag")
        except Exception:
            db.execute("ROLLBACK TO _auto_tag")
            db.execute("RELEASE _auto_tag")
    except Exception:
        pass  # Never break add_entry convergence for tagging failures


def _knowledge_stable_id(session_id: str, category: str, title: str, topic_key: str = "") -> str:
    return _stable_sha256("knowledge", session_id or "", category or "", title or "", topic_key or "")


def _default_local_replica_id() -> str:
    host = os.environ.get("HOSTNAME") or os.environ.get("COMPUTERNAME") or ""
    user = os.environ.get("USER") or os.environ.get("USERNAME") or ""
    return f"replica-{_stable_sha256('local-replica', host, user, str(Path.home()))[:16]}"


def _get_local_replica_id(db: sqlite3.Connection) -> str:
    try:
        row = db.execute("SELECT value FROM sync_state WHERE key='local_replica_id'").fetchone()
        current = str(row[0]) if row and row[0] else ""
        if current and current != "local":
            return current
        replica_id = _default_local_replica_id()
        db.execute(
            """
            INSERT INTO sync_state (key, value)
            VALUES ('local_replica_id', ?)
            ON CONFLICT(key) DO UPDATE SET
                value = excluded.value,
                updated_at = datetime('now')
        """,
            (replica_id,),
        )
        try:
            db.execute(
                """
                INSERT INTO sync_metadata (key, value)
                VALUES ('local_replica_id', ?)
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value,
                    updated_at = datetime('now')
            """,
                (replica_id,),
            )
        except sqlite3.OperationalError:
            pass
        return replica_id
    except Exception:
        return ""


def _enqueue_sync_op_fail_open(
    db: sqlite3.Connection,
    table_name: str,
    row_stable_id: str,
    row_payload: dict,
    op_type: str = "upsert",
):
    try:
        tools_dir = str(Path(__file__).resolve().parent)
        if tools_dir not in sys.path:
            sys.path.insert(0, tools_dir)
        from sync_enqueue import enqueue_sync_op_fail_open

        enqueue_sync_op_fail_open(db, table_name, row_stable_id, row_payload, op_type)
    except Exception:
        return


def scan_content_for_injection(title: str, content: str) -> list:
    """Scan title + content for injection patterns. Returns list of warnings."""
    warnings = []
    text = f"{title}\n{content}"
    for pattern, description in _INJECTION_PATTERNS:
        if pattern.search(text):
            warnings.append(description)
    return warnings


# Patterns that identify operational status notes, not actionable knowledge.
# Entries whose *title* matches these patterns are rejected at write time so
# they don't pollute compact briefing output with noise like
# "Wave19 verification is complete … tou: Wave19 … touched …".
_STATUS_NOTE_PATTERNS = [
    re.compile(r"(?i)^wave\d+\s+verification\s+is\s+complete"),
    re.compile(r"(?i)\bverification\s+is\s+complete\s+on\s+this\s+workstation"),
    re.compile(r"(?i)^wave\d+\s+\S.*\s+is\s+complete\b"),
]


def _is_status_note_title(title: str) -> str:
    """Return a rejection reason if *title* is an operational status note.

    Operational status notes (e.g. "Wave19 verification is complete …") are
    progress markers, not reusable knowledge.  Storing them as mistake/pattern/
    discovery entries pollutes briefing output without adding agent value.

    Returns an empty string when the title is acceptable.
    """
    for pat in _STATUS_NOTE_PATTERNS:
        if pat.search(title):
            return (
                f"status-note title rejected: '{title[:80]}' matches "
                "an operational progress-report pattern (WaveN complete, "
                "verification complete on workstation, …). "
                "Record the specific finding instead, or use a discovery/milestone note for progress tracking."
            )
    return ""


def _parse_code_location(value: str) -> tuple[str, int, int]:
    """Parse <path>:<line> or <path>:<start>-<end> from rightmost numeric suffix."""
    m = re.match(r"^(?P<path>.+):(?P<start>\d+)(?:-(?P<end>\d+))?$", value or "")
    if not m:
        raise ValueError(f"Invalid --code-location: {value!r}. Expected path:line or path:start-end")
    source_file = m.group("path")
    start_line = int(m.group("start"))
    end_line = int(m.group("end") or m.group("start"))
    if start_line <= 0 or end_line <= 0 or end_line < start_line:
        raise ValueError(f"Invalid --code-location line range: {value!r}")
    return source_file, start_line, end_line


def _detect_code_language(source_file: str) -> str:
    return _CODE_LANGUAGE_MAP.get(Path(source_file).suffix.lower(), "")


def _extract_code_snippet(source_file: str, start_line: int, end_line: int, quiet: bool = False) -> tuple[str, str]:
    """Best-effort snippet extraction; never raises for unreadable files."""
    code_language = _detect_code_language(source_file)
    path = Path(source_file)
    try:
        if not path.exists() or not path.is_file():
            raise OSError("missing or not a regular file")
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        print(f"  [warn] Could not read code location '{source_file}': {e}", file=sys.stderr if quiet else sys.stdout)
        return "", code_language

    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = normalized.split("\n")
    if start_line > len(lines):
        return "", code_language
    snippet = "\n".join(lines[start_line - 1 : end_line])
    if len(snippet) > 2000:
        snippet = snippet[:1999] + "…"
    return snippet, code_language


def _auto_update_cerebrum(output_path: str, sections: str | None, *, json_mode: bool = False) -> int:
    """Invoke export-cerebrum.py to regenerate the cerebrum snapshot after a learn write.

    This is an opt-in path triggered only when --update-cerebrum is passed.  It spawns
    export-cerebrum.py as a subprocess so the two standalone scripts remain decoupled.

    Args:
        output_path: Destination file path for the cerebrum snapshot (e.g. CEREBRUM.md).
        sections: Comma-separated section names to include, or None for all sections.
        json_mode: When True, redirect subprocess stdout to DEVNULL so the
            caller-visible JSON stream is not contaminated by exporter output.

    Returns:
        The subprocess exit code.  Non-zero indicates export failure.
    """
    exporter = TOOLS_DIR / "export-cerebrum.py"
    cmd = [sys.executable, str(exporter), "--output", output_path]
    if sections:
        cmd += ["--sections", sections]
    print(f"  Updating cerebrum snapshot → {output_path}", file=sys.stderr)
    # In JSON mode the caller-visible stdout stream must stay clean; redirect
    # subprocess stdout away from it.  In non-JSON mode stdout is inherited so
    # the user can see exporter progress.
    stdout_arg = subprocess.DEVNULL if json_mode else None
    try:
        result = subprocess.run(cmd, stdout=stdout_arg, timeout=120)
        if result.returncode != 0:
            print(
                f"  ⚠ export-cerebrum failed (exit {result.returncode})",
                file=sys.stderr,
            )
        return result.returncode
    except subprocess.TimeoutExpired:
        print("  ⚠ export-cerebrum timed out (120 s)", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"  ⚠ export-cerebrum error: {exc}", file=sys.stderr)
        return 1


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except ValueError:
        return default


def _learn_queue_on_lock_enabled() -> bool:
    return os.environ.get("SK_LEARN_QUEUE_ON_LOCK", "1") != "0"


def _learn_queue_busy_timeout_ms() -> int:
    return max(0, _env_int("SK_LEARN_BUSY_TIMEOUT_MS", DEFAULT_LEARN_QUEUE_BUSY_TIMEOUT_MS))


def get_db(busy_timeout_ms: int | None = None) -> sqlite3.Connection:
    if not DB_PATH.exists():
        print("Error: Knowledge DB not found. Run build-session-index.py first.", file=sys.stderr)
        sys.exit(1)
    timeout_ms = DEFAULT_DB_BUSY_TIMEOUT_MS if busy_timeout_ms is None else max(0, int(busy_timeout_ms))
    # Per-connection timeout controls SQLite-level busy waiting before raising
    # OperationalError. Normal fail-open learn writes use a short timeout and
    # queue quickly; explicit flush/replay keeps the longer default window.
    db = sqlite3.connect(str(DB_PATH), timeout=timeout_ms / 1000.0)
    db.row_factory = sqlite3.Row
    db.execute(f"PRAGMA busy_timeout={timeout_ms}")
    db.execute("PRAGMA journal_mode=WAL")
    return db


def _is_busy_error(exc: sqlite3.OperationalError) -> bool:
    """Return True if exc is a SQLITE_BUSY / database-is-locked error."""
    msg = str(exc).lower()
    return "locked" in msg or "busy" in msg


def with_retry(func, *args, max_attempts: int = 5, base_delay: float = 0.5, **kwargs):
    """Run a DB-writing callable with exponential backoff on SQLITE_BUSY.

    The indexer service (watch-sessions.py) holds the writer lock during
    embedding flushes; even with busy_timeout=30s, very large batches on a
    ~800 MB DB can exceed it. This wrapper retries transient lock errors
    with delays of 0.5s, 1s, 2s, 4s, 8s before giving up.
    """
    last_exc: sqlite3.OperationalError | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            return func(*args, **kwargs)
        except sqlite3.OperationalError as exc:
            if not _is_busy_error(exc):
                raise
            last_exc = exc
            if attempt == max_attempts:
                break
            delay = base_delay * (2 ** (attempt - 1))
            print(
                f"  ⏳ DB busy (attempt {attempt}/{max_attempts}), retrying in {delay:.1f}s...",
                file=sys.stderr,
            )
            time.sleep(delay)
    assert last_exc is not None
    raise last_exc


def detect_session_id() -> str:
    """Try to detect current session ID from environment or recent sessions."""
    # Check if there's a session env var
    sid = os.environ.get("COPILOT_SESSION_ID", "")
    if sid:
        return sid

    # Find most recently modified session
    if SESSION_STATE.exists():
        sessions = []
        for d in SESSION_STATE.iterdir():
            if d.is_dir() and len(d.name) > 8 and "-" in d.name:
                try:
                    mtime = max(f.stat().st_mtime for f in d.rglob("*") if f.is_file())
                    sessions.append((mtime, d.name))
                except (ValueError, OSError) as e:
                    print(f"⚠ Error reading session dir: {e}", file=sys.stderr)
        if sessions:
            sessions.sort(reverse=True)
            return sessions[0][1]

    return "manual"


def add_entry(
    category: str,
    title: str,
    content: str,
    tags: str = "",
    session_id: str = None,
    confidence: float = None,
    wing: str = "",
    room: str = "",
    facts: list = None,
    skip_gate: bool = False,
    skip_scan: bool = False,
    task_id: str = "",
    affected_files: list = None,
    source_file: str = "",
    start_line: int = 0,
    end_line: int = 0,
    code_language: str = "",
    code_snippet: str = "",
    code_location_set: bool = False,
    quiet: bool = False,
    error_type: str = "",
    root_cause: str = "",
    severity: str = "",
    fix_steps: str = "",
    valence: str = "",
    intensity: float = None,
    priority: str = "",
    agent_id: str = "",
    certainty: str = "",
    caveats: str = "",
    db_busy_timeout_ms: int | None = None,
) -> int:
    """Add a knowledge entry to the database. Returns entry ID.

    Quality gate (for mistake/pattern/discovery): 3 questions must all be YES:
    1. "Could someone Google this in 5 minutes?" → NO (otherwise not worth recording)
    2. "Is this specific to THIS codebase?" → YES (generic knowledge doesn't belong)
    3. "Did this require real debugging/investigation?" → YES (trivial findings = noise)

    Gate is auto-skipped for decision/tool/feature/refactor (always worth recording)
    and for bulk imports (--from-file). Use --skip-gate to bypass manually.

    Injection scanning: All entries are scanned for prompt injection, role hijacking,
    credential leaks, and invisible Unicode. Matching entries are REJECTED unless
    --skip-scan is passed (for documenting injection patterns themselves).
    """
    db = get_db(db_busy_timeout_ms)
    ke_columns = {row[1] for row in db.execute("PRAGMA table_info(knowledge_entries)").fetchall()}
    has_code_location_columns = all(
        c in ke_columns for c in ("source_file", "start_line", "end_line", "code_language", "code_snippet")
    )
    has_stable_id_column = "stable_id" in ke_columns
    has_topic_key_column = "topic_key" in ke_columns
    has_error_lifecycle_columns = all(c in ke_columns for c in ("error_type", "root_cause", "severity", "fix_steps"))
    has_valence_intensity_columns = all(c in ke_columns for c in ("valence", "intensity"))
    has_priority_column = "priority" in ke_columns
    has_agent_id_column = "agent_id" in ke_columns
    has_epistemic_humility_columns = all(c in ke_columns for c in ("certainty", "caveats"))
    has_deleted_at_column = "deleted_at" in ke_columns
    if code_location_set and not has_code_location_columns:
        print(
            "  [warn] DB schema missing code-location columns; run migrate.py to persist snippets",
            file=sys.stderr if quiet else sys.stdout,
        )
        code_location_set = False

    # Injection scanning (before any DB writes) — always runs unless --skip-scan
    if not skip_scan:
        injection_warnings = scan_content_for_injection(title, content)
        if injection_warnings:
            print("  ⚠ REJECTED — injection pattern detected:", file=sys.stderr)
            for w in injection_warnings:
                print(f"    ✗ {w}", file=sys.stderr)
            print("  Use --skip-scan to bypass (only for documenting injection patterns)", file=sys.stderr)
            return -1

    # Status-note guard: reject operational progress-report titles stored as
    # mistake/pattern/discovery — they produce repeated-prefix noise in briefings.
    if category in ("mistake", "pattern", "discovery") and not skip_gate:
        status_note_reason = _is_status_note_title(title)
        if status_note_reason:
            print(f"  ⚠ REJECTED — {status_note_reason}", file=sys.stderr)
            print("  Use --skip-gate to bypass if you intentionally want to record this.", file=sys.stderr)
            return -1

    if not session_id:
        session_id = detect_session_id()

    if confidence is None:
        confidence = {
            "mistake": 0.7,
            "pattern": 0.7,
            "decision": 0.8,
            "tool": 0.5,
            "feature": 0.7,
            "refactor": 0.6,
            "discovery": 0.6,
        }.get(category, 0.5)

    # Auto-detect wing/room if not provided
    if not wing:
        wing = _detect_wing(tags, title, content)
    if not room:
        room = _detect_room(tags, title, content)

    # Serialize facts and affected_files to JSON
    facts_json = json.dumps(facts or [], ensure_ascii=False)
    # Enforce path length limit for each file
    files_list = [(f[:256] if f else "") for f in (affected_files or [])]
    files_json = json.dumps(files_list, ensure_ascii=False)

    # Enforce task_id length limit
    task_id = (task_id or "")[:200]

    now = time.strftime("%Y-%m-%dT%H:%M:%S")

    # Check for existing entry with same title in same category (exclude soft-deleted rows)
    existing_sql = """
        SELECT id, occurrence_count, content, session_id
    """
    if has_topic_key_column:
        existing_sql += ", COALESCE(topic_key, '') AS topic_key"
    else:
        existing_sql += ", '' AS topic_key"
    existing_sql += """
        FROM knowledge_entries
        WHERE category = ? AND title = ?
    """
    if has_deleted_at_column:
        existing_sql += " AND deleted_at IS NULL"
    existing_sql += """
        ORDER BY confidence DESC LIMIT 1
    """
    existing = db.execute(existing_sql, (category, title)).fetchone()

    if existing:
        # Update existing: bump occurrence count, update content if longer
        new_count = existing["occurrence_count"] + 1
        new_content = content if len(content) > len(existing["content"]) else existing["content"]
        new_confidence = min(1.0, confidence + 0.05 * (new_count - 1))

        # Estimate token cost from the content that will actually be stored
        est_tokens = len(f"{title} {new_content}") // 4

        update_sql = """
            UPDATE knowledge_entries
            SET content = ?, occurrence_count = ?, confidence = ?,
                last_seen = ?, tags = CASE WHEN ? != '' THEN ? ELSE tags END,
                wing = CASE WHEN ? != '' THEN ? ELSE wing END,
                room = CASE WHEN ? != '' THEN ? ELSE room END,
                facts = CASE WHEN ? != '[]' THEN ? ELSE facts END,
                task_id = CASE WHEN ? != '' THEN ? ELSE task_id END,
                affected_files = CASE WHEN ? != '[]' THEN ? ELSE affected_files END,
        """
        update_params = [
            new_content,
            new_count,
            new_confidence,
            now,
            tags,
            tags,
            wing,
            wing,
            room,
            room,
            facts_json,
            facts_json,
            task_id,
            task_id,
            files_json,
            files_json,
        ]
        if has_stable_id_column:
            stable_id = _knowledge_stable_id(
                existing["session_id"], category, title, existing["topic_key"] if has_topic_key_column else ""
            )
            update_sql += " stable_id = ?,"
            update_params.append(stable_id)
        if has_code_location_columns:
            update_sql += """
                source_file = CASE WHEN ? THEN ? ELSE source_file END,
                start_line = CASE WHEN ? THEN ? ELSE start_line END,
                end_line = CASE WHEN ? THEN ? ELSE end_line END,
                code_language = CASE WHEN ? THEN ? ELSE code_language END,
                code_snippet = CASE WHEN ? THEN ? ELSE code_snippet END,
            """
            update_params.extend(
                [
                    int(code_location_set),
                    source_file,
                    int(code_location_set),
                    start_line,
                    int(code_location_set),
                    end_line,
                    int(code_location_set),
                    code_language,
                    int(code_location_set),
                    code_snippet,
                ]
            )
        if has_valence_intensity_columns and (valence or intensity is not None):
            update_sql += (
                " valence = CASE WHEN ? != '' THEN ? ELSE valence END,"
                " intensity = CASE WHEN ? IS NOT NULL THEN ? ELSE intensity END,"
            )
            update_params.extend([valence or "", valence or "", intensity, intensity])
        if has_priority_column and priority:
            update_sql += " priority = CASE WHEN ? != '' THEN ? ELSE priority END,"
            update_params.extend([priority, priority])
        if has_agent_id_column and agent_id:
            update_sql += " agent_id = CASE WHEN ? != '' THEN ? ELSE agent_id END,"
            update_params.extend([agent_id, agent_id])
        if has_epistemic_humility_columns and (certainty or caveats):
            update_sql += (
                " certainty = CASE WHEN ? != '' THEN ? ELSE certainty END,"
                " caveats = CASE WHEN ? != '' THEN ? ELSE caveats END,"
            )
            update_params.extend([certainty or "", certainty or "", caveats or "", caveats or ""])
        update_sql += " est_tokens = ? WHERE id = ?"
        update_params.extend([est_tokens, existing["id"]])
        db.execute(update_sql, update_params)
        entry_id = existing["id"]
        if has_stable_id_column:
            _enqueue_sync_op_fail_open(
                db,
                "knowledge_entries",
                stable_id,
                {
                    "category": category,
                    "title": title,
                    "stable_id": stable_id,
                    "content": new_content,
                    "tags": tags,
                    "confidence": new_confidence,
                    "session_id": existing["session_id"],
                    "occurrence_count": new_count,
                    "last_seen": now,
                    "wing": wing,
                    "room": room,
                    "facts": facts_json,
                    "task_id": task_id,
                    "affected_files": files_json,
                    "est_tokens": est_tokens,
                },
            )
        loc = f" [{wing}/{room}]" if wing or room else ""
        msg = f"  Updated existing entry #{entry_id} (seen {new_count}x, confidence → {new_confidence:.2f}){loc}"
        print(msg, file=sys.stderr if quiet else sys.stdout)
    else:
        # Estimate token cost for new entry
        est_tokens = len(f"{title} {content}") // 4

        # Insert new entry
        if has_code_location_columns:
            if has_stable_id_column:
                stable_id = _knowledge_stable_id(session_id, category, title, "")
                db.execute(
                    """
                INSERT INTO knowledge_entries
                    (category, title, stable_id, content, tags, confidence, session_id,
                     occurrence_count, first_seen, last_seen, wing, room,
                     facts, est_tokens, task_id, affected_files,
                     source_file, start_line, end_line, code_language, code_snippet)
                VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                    (
                        category,
                        title,
                        stable_id,
                        content,
                        tags,
                        confidence,
                        session_id,
                        now,
                        now,
                        wing,
                        room,
                        facts_json,
                        est_tokens,
                        task_id,
                        files_json,
                        source_file,
                        start_line,
                        end_line,
                        code_language,
                        code_snippet,
                    ),
                )
            else:
                db.execute(
                    """
                INSERT INTO knowledge_entries
                    (category, title, content, tags, confidence, session_id,
                     occurrence_count, first_seen, last_seen, wing, room,
                     facts, est_tokens, task_id, affected_files,
                     source_file, start_line, end_line, code_language, code_snippet)
                VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                    (
                        category,
                        title,
                        content,
                        tags,
                        confidence,
                        session_id,
                        now,
                        now,
                        wing,
                        room,
                        facts_json,
                        est_tokens,
                        task_id,
                        files_json,
                        source_file,
                        start_line,
                        end_line,
                        code_language,
                        code_snippet,
                    ),
                )
        else:
            if has_stable_id_column:
                stable_id = _knowledge_stable_id(session_id, category, title, "")
                db.execute(
                    """
                INSERT INTO knowledge_entries
                    (category, title, stable_id, content, tags, confidence, session_id,
                     occurrence_count, first_seen, last_seen, wing, room,
                     facts, est_tokens, task_id, affected_files)
                VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                    (
                        category,
                        title,
                        stable_id,
                        content,
                        tags,
                        confidence,
                        session_id,
                        now,
                        now,
                        wing,
                        room,
                        facts_json,
                        est_tokens,
                        task_id,
                        files_json,
                    ),
                )
            else:
                db.execute(
                    """
                INSERT INTO knowledge_entries
                    (category, title, content, tags, confidence, session_id,
                     occurrence_count, first_seen, last_seen, wing, room,
                     facts, est_tokens, task_id, affected_files)
                VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                    (
                        category,
                        title,
                        content,
                        tags,
                        confidence,
                        session_id,
                        now,
                        now,
                        wing,
                        room,
                        facts_json,
                        est_tokens,
                        task_id,
                        files_json,
                    ),
                )
        entry_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
        # Set error lifecycle columns if available
        if has_error_lifecycle_columns and any([error_type, root_cause, severity, fix_steps]):
            db.execute(
                """UPDATE knowledge_entries SET error_type = ?, root_cause = ?, severity = ?, fix_steps = ?
                   WHERE id = ?""",
                (error_type or "", root_cause or "", severity or "medium", fix_steps or "", entry_id),
            )
        # Set valence/intensity columns if available
        if has_valence_intensity_columns and (valence or intensity is not None):
            _intensity = intensity if intensity is not None else 0.5
            db.execute(
                "UPDATE knowledge_entries SET valence = ?, intensity = ? WHERE id = ?",
                (valence or "", _intensity, entry_id),
            )
        # Set priority column if available
        if has_priority_column and priority:
            db.execute(
                "UPDATE knowledge_entries SET priority = ? WHERE id = ?",
                (priority, entry_id),
            )
        # Set agent_id column if available (#351)
        if has_agent_id_column and agent_id:
            db.execute(
                "UPDATE knowledge_entries SET agent_id = ? WHERE id = ?",
                (agent_id, entry_id),
            )
        # Set epistemic humility columns if available (#402)
        if has_epistemic_humility_columns and (certainty or caveats):
            db.execute(
                "UPDATE knowledge_entries SET certainty = ?, caveats = ? WHERE id = ?",
                (certainty or "", caveats or "", entry_id),
            )
        if has_stable_id_column:
            inserted_stable_id = db.execute(
                "SELECT COALESCE(stable_id, '') FROM knowledge_entries WHERE id = ?",
                (entry_id,),
            ).fetchone()[0]
            _enqueue_sync_op_fail_open(
                db,
                "knowledge_entries",
                inserted_stable_id,
                {
                    "category": category,
                    "title": title,
                    "stable_id": inserted_stable_id,
                    "content": content,
                    "tags": tags,
                    "confidence": confidence,
                    "session_id": session_id,
                    "occurrence_count": 1,
                    "first_seen": now,
                    "last_seen": now,
                    "wing": wing,
                    "room": room,
                    "facts": facts_json,
                    "est_tokens": est_tokens,
                    "task_id": task_id,
                    "affected_files": files_json,
                    "source_file": source_file if has_code_location_columns else "",
                    "start_line": start_line if has_code_location_columns else 0,
                    "end_line": end_line if has_code_location_columns else 0,
                    "code_language": code_language if has_code_location_columns else "",
                    "code_snippet": code_snippet if has_code_location_columns else "",
                },
            )
        loc = f" [{wing}/{room}]" if wing or room else ""
        task_note = f" task={task_id}" if task_id else ""
        msg = f"  Added new {category} #{entry_id}{loc}{task_note}"
        print(msg, file=sys.stderr if quiet else sys.stdout)

    # Update FTS index
    _update_fts(
        db,
        entry_id,
        title,
        content,
        tags,
        category,
        wing,
        room,
        facts_json,
        error_type=error_type or "",
        root_cause=root_cause or "",
    )

    # Auto-tag with concept tags (local_only; replace stale tags on update)
    _auto_tag_entry(db, entry_id, title, content)

    # Generate embedding for the new entry
    _embed_entry(db, entry_id, title, content, quiet=quiet)

    db.commit()
    db.close()
    return entry_id


def _update_fts(
    db: sqlite3.Connection,
    entry_id: int,
    title: str,
    content: str,
    tags: str,
    category: str,
    wing: str = "",
    room: str = "",
    facts_json: str = "[]",
    error_type: str = "",
    root_cause: str = "",
):
    """Update the standalone FTS5 table for this entry."""
    try:
        db.execute("DELETE FROM ke_fts WHERE rowid = ?", (entry_id,))
        # Try new schema with error_type, root_cause first
        try:
            db.execute(
                """
                INSERT INTO ke_fts (rowid, title, content, tags, category, wing, room, facts, error_type, root_cause)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (entry_id, title, content, tags, category, wing, room, facts_json, error_type, root_cause),
            )
        except sqlite3.OperationalError:
            # Fall back to old schema without error_type, root_cause
            db.execute(
                """
                INSERT INTO ke_fts (rowid, title, content, tags, category, wing, room, facts)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (entry_id, title, content, tags, category, wing, room, facts_json),
            )
    except sqlite3.OperationalError:
        pass  # ke_fts might not exist yet


def _embed_entry(db: sqlite3.Connection, entry_id: int, title: str, content: str, quiet: bool = False):
    """Generate and store embedding for a single entry."""
    # Fast-path bypass used by auto-flush (issue #573) so a queued-entry
    # drain isn't blocked by per-entry embedding API latency. The next
    # scheduled embed run will backfill missing embeddings.
    if os.environ.get("SK_LEARN_SKIP_EMBED") == "1":
        return
    try:
        sys.path.insert(0, str(TOOLS_DIR))
        from embed import call_embedding_api, ensure_embedding_tables, load_config, resolve_provider, serialize_vector

        config = load_config()
        provider_name, provider_config = resolve_provider(config)

        if not provider_name:
            return

        ensure_embedding_tables(db)
        text = f"{title}: {content[:2000]}"
        vecs = call_embedding_api([text], provider_config)

        if vecs:
            blob = serialize_vector(vecs[0])
            now = time.strftime("%Y-%m-%dT%H:%M:%S")
            db.execute(
                """
                INSERT OR REPLACE INTO embeddings
                    (source_type, source_id, provider, model, dimensions,
                     vector, text_preview, created_at)
                VALUES ('knowledge', ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    entry_id,
                    provider_name,
                    provider_config["model"],
                    provider_config.get("dimensions", 768),
                    blob,
                    title[:200],
                    now,
                ),
            )
            print(f"  Embedded with {provider_name}", file=sys.stderr if quiet else sys.stdout)
    except Exception as e:
        print(f"  [info] Embedding skipped: {e}", file=sys.stderr)


def import_from_file(filepath: str) -> int:
    """Bulk import knowledge entries from a markdown file.

    Expected format:
    ## mistake: Title Here
    Content describing the mistake...

    ## pattern: Title Here
    Content describing the pattern...

    Returns the number of entries actually imported (0 on failure or no entries).
    """
    path = Path(filepath)
    if not path.exists():
        print(f"Error: File not found: {filepath}", file=sys.stderr)
        return 0

    content = path.read_text(encoding="utf-8", errors="replace")
    entries = []
    current = None

    for line in content.splitlines():
        if line.startswith("## "):
            if current:
                entries.append(current)
            # Parse "## category: Title"
            rest = line[3:].strip()
            if ":" in rest:
                cat, title = rest.split(":", 1)
                cat = cat.strip().lower()
                if cat in ("mistake", "pattern", "decision", "tool", "feature", "refactor", "discovery"):
                    current = {"category": cat, "title": title.strip(), "lines": []}
                else:
                    current = None
            else:
                current = None
        elif current is not None:
            current["lines"].append(line)

    if current:
        entries.append(current)

    if not entries:
        print("No entries found. Use format: ## category: Title", file=sys.stderr)
        return 0

    print(f"Importing {len(entries)} entries from {filepath}...")
    imported = 0
    for entry in entries:
        content = "\n".join(entry["lines"]).strip()
        if content:
            with_retry(add_entry, entry["category"], entry["title"], content)
            imported += 1

    print(f"Done. Imported {imported} entries.")
    if imported == 0:
        # Headers parsed successfully but all entries had empty body content.
        # This is not the same error as "file not found" or "no headers found";
        # return -1 so the caller can exit 0 rather than treating it as a hard failure.
        return -1
    return imported


# ── Batch ingest helpers (issue #576) ─────────────────────────────────────────

_HEADING_CATEGORY_MAP: dict[str, str] = {
    "decision": "decision",
    "decisions": "decision",
    "pattern": "pattern",
    "patterns": "pattern",
    "mistake": "mistake",
    "mistakes": "mistake",
    "error": "mistake",
    "errors": "mistake",
    "technical details": "discovery",
    "technical detail": "discovery",
    "next steps": "discovery",
    "next step": "discovery",
    "feature": "feature",
    "features": "feature",
    "discovery": "discovery",
    "discoveries": "discovery",
    "tool": "tool",
    "tools": "tool",
    "refactor": "refactor",
}


def _heading_to_category(heading: str) -> str:
    """Map a markdown heading to a knowledge category; fallback to 'discovery'."""
    return _HEADING_CATEGORY_MAP.get(heading.strip().lower(), "discovery")


def _heading_slug(heading: str) -> str:
    """Convert a heading to a URL-safe lowercase slug."""
    return re.sub(r"[^a-z0-9]+", "-", heading.strip().lower()).strip("-")


def _batch_stable_id(source_key: str, heading: str) -> str:
    """Derive a 12-char hex stable_id: sha256(source_key:heading_slug)[:12]."""
    slug = _heading_slug(heading)
    payload = f"{source_key}:{slug}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


def _parse_markdown_sections(text: str) -> list[dict]:
    """Split text by ## headings into sections with heading, content, and category.

    Only returns sections whose content is non-empty after stripping.
    """
    sections: list[dict] = []
    current_heading: str | None = None
    current_lines: list[str] = []

    for line in text.splitlines():
        if line.startswith("## "):
            if current_heading is not None:
                body = "\n".join(current_lines).strip()
                if body:
                    sections.append(
                        {
                            "heading": current_heading,
                            "content": body,
                            "category": _heading_to_category(current_heading),
                        }
                    )
            current_heading = line[3:].strip()
            current_lines = []
        elif current_heading is not None:
            current_lines.append(line)

    if current_heading is not None:
        body = "\n".join(current_lines).strip()
        if body:
            sections.append(
                {
                    "heading": current_heading,
                    "content": body,
                    "category": _heading_to_category(current_heading),
                }
            )

    return sections


def _stable_id_exists(stable_id: str) -> bool:
    """Return True if a knowledge entry with this stable_id exists in the DB."""
    if not DB_PATH.exists():
        return False
    try:
        db = get_db()
        ke_columns = {row[1] for row in db.execute("PRAGMA table_info(knowledge_entries)").fetchall()}
        if "stable_id" not in ke_columns:
            db.close()
            return False
        row = db.execute("SELECT id FROM knowledge_entries WHERE stable_id = ?", (stable_id,)).fetchone()
        db.close()
        return row is not None
    except Exception:
        return False


def _set_entry_stable_id(entry_id: int, stable_id: str) -> None:
    """Overwrite the stable_id for a knowledge entry after insertion (best-effort)."""
    try:
        db = get_db()
        db.execute("UPDATE knowledge_entries SET stable_id = ? WHERE id = ?", (stable_id, entry_id))
        db.commit()
        db.close()
    except Exception:
        pass


def batch_ingest_sections(
    sections: list[dict],
    source_key: str,
    *,
    dry_run: bool = False,
    session_id: str | None = None,
    tags: str = "",
) -> dict:
    """Insert parsed sections as knowledge entries with stable_id idempotency.

    Args:
        sections: List of {heading, content, category} from _parse_markdown_sections().
        source_key: Prefix for stable_id derivation (e.g. filepath or 'pr-123').
        dry_run: If True, print a preview table and return without inserting.
        session_id: Optional session ID for entries (defaults to 'batch-ingest').
        tags: Optional comma-separated tags to attach to all entries.

    Returns dict with keys: inserted, skipped, total, dry_run, rows.
    """
    total = len(sections)
    rows = []
    for sec in sections:
        sid = _batch_stable_id(source_key, sec["heading"])
        word_count = len(sec["content"].split())
        rows.append(
            {
                "stable_id": sid,
                "heading": sec["heading"],
                "category": sec["category"],
                "content": sec["content"],
                "word_count": word_count,
            }
        )

    if dry_run:
        print(f"\nDry run — {total} section(s) from '{source_key}'")
        print(f"  {'stable_id':>12}  {'heading':<32}  {'category':<12}  words")
        print("  " + "-" * 68)
        for r in rows:
            print(f"  {r['stable_id']:>12}  {r['heading'][:32]:<32}  {r['category']:<12}  {r['word_count']}")
        print()
        return {"inserted": 0, "skipped": 0, "total": total, "dry_run": True, "rows": rows}

    inserted = 0
    skipped = 0
    for r in rows:
        if _stable_id_exists(r["stable_id"]):
            print(f"  — skipping '{r['heading']}' (stable_id {r['stable_id']} already exists)")
            skipped += 1
            continue

        entry_id = with_retry(
            add_entry,
            r["category"],
            r["heading"],
            r["content"],
            tags=tags,
            session_id=session_id or "batch-ingest",
            skip_gate=True,
        )
        if entry_id >= 0:
            _set_entry_stable_id(entry_id, r["stable_id"])
            inserted += 1
        else:
            print(f"  ⚠ Skipped '{r['heading']}' (rejected by injection scan or other guard)")
            skipped += 1

    return {"inserted": inserted, "skipped": skipped, "total": total, "dry_run": False, "rows": rows}


def batch_ingest_from_checkpoint(filepath: str, *, dry_run: bool = False, tags: str = "") -> int:
    """Ingest a checkpoint/markdown file as multiple knowledge entries by heading.

    Returns number of entries inserted (0 if nothing to import or all already ingested).
    """
    path = Path(filepath)
    if not path.exists():
        print(f"Error: File not found: {filepath}", file=sys.stderr)
        sys.exit(1)

    text = path.read_text(encoding="utf-8", errors="replace")
    sections = _parse_markdown_sections(text)

    if not sections:
        print(f"  No ## headings with content found in {filepath}", file=sys.stderr)
        return 0

    source_key = str(path.resolve())
    result = batch_ingest_sections(sections, source_key, dry_run=dry_run, tags=tags)

    if not dry_run:
        print(f"  Checkpoint ingest: {result['inserted']} inserted, {result['skipped']} skipped from {filepath}")

    return result["inserted"]


def batch_ingest_from_pr(pr_ref: str, *, dry_run: bool = False, tags: str = "") -> int:
    """Ingest knowledge entries from a GitHub PR body.

    pr_ref: PR number (int or string) or PR URL containing /pull/<number>.
    Returns number of entries inserted.
    """
    pr_num_str = str(pr_ref)
    url_match = re.search(r"/pull/(\d+)", pr_num_str)
    if url_match:
        pr_num_str = url_match.group(1)

    try:
        pr_num_int = int(pr_num_str)
    except (ValueError, TypeError):
        print(f"Error: Invalid PR number or URL: {pr_ref!r}", file=sys.stderr)
        sys.exit(1)

    try:
        proc = subprocess.run(
            ["gh", "pr", "view", str(pr_num_int), "--json", "body,title,number,headRefName"],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except FileNotFoundError:
        print(
            "Error: 'gh' CLI not found. Install the GitHub CLI (https://cli.github.com/) and authenticate.",
            file=sys.stderr,
        )
        sys.exit(1)
    except subprocess.TimeoutExpired:
        print("Error: gh CLI timed out after 30 seconds.", file=sys.stderr)
        sys.exit(1)

    if proc.returncode != 0:
        print(f"Error: gh pr view failed: {proc.stderr.strip()}", file=sys.stderr)
        sys.exit(1)

    try:
        pr_data = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        print(f"Error: Failed to parse gh output: {exc}", file=sys.stderr)
        sys.exit(1)

    body = pr_data.get("body") or ""
    title = pr_data.get("title") or f"PR #{pr_num_int}"

    if not body.strip():
        print(f"  PR #{pr_num_int} '{title}' has no body content.", file=sys.stderr)
        return 0

    sections = _parse_markdown_sections(body)
    if not sections:
        sections = [{"heading": title, "content": body.strip(), "category": "discovery"}]

    source_key = f"pr-{pr_num_int}"
    ingest_tags = f"pr,pr-{pr_num_int}" + (f",{tags}" if tags else "")
    result = batch_ingest_sections(sections, source_key, dry_run=dry_run, tags=ingest_tags)

    if not dry_run:
        print(f"  PR #{pr_num_int} ingest: {result['inserted']} inserted, {result['skipped']} skipped")

    return result["inserted"]


def list_recent(limit: int = 10):
    """List recently added/updated knowledge entries (excludes soft-deleted)."""
    db = get_db()
    ke_columns = {row[1] for row in db.execute("PRAGMA table_info(knowledge_entries)").fetchall()}
    _nd = "AND (deleted_at IS NULL)" if "deleted_at" in ke_columns else ""

    print(f"\nRecent Knowledge Entries (last {limit})\n")
    rows = db.execute(
        f"""
        SELECT id, category, title, confidence, occurrence_count,
               last_seen, session_id, est_tokens
        FROM knowledge_entries
        WHERE 1=1 {_nd}
        ORDER BY last_seen DESC
        LIMIT ?
    """,
        (limit,),
    ).fetchall()

    for r in rows:
        sid = r["session_id"][:8] if r["session_id"] else "?"
        count = f" ×{r['occurrence_count']}" if r["occurrence_count"] > 1 else ""
        tok = f"  ~{r['est_tokens']}tok" if r["est_tokens"] else ""
        print(f"  #{r['id']:3d} [{r['category']:8s}] {r['title'][:60]}")
        print(f"       conf={r['confidence']:.2f}{count}{tok}  session={sid}..  {r['last_seen'] or '?'}")

    db.close()


def show_stats():
    """Show knowledge base statistics."""
    db = get_db()

    print("\nKnowledge Base Statistics\n")

    total = db.execute("SELECT COUNT(*) FROM knowledge_entries").fetchone()[0]
    print(f"Total entries: {total}")

    for row in db.execute("""
        SELECT category, COUNT(*) as cnt,
               ROUND(AVG(confidence), 2) as avg_conf,
               SUM(occurrence_count) as total_seen
        FROM knowledge_entries
        GROUP BY category ORDER BY cnt DESC
    """):
        print(
            f"  {row['category']:10s}: {row['cnt']:3d} entries  "
            f"avg_conf={row['avg_conf']}  total_seen={row['total_seen']}"
        )

    # Wing breakdown
    wings = db.execute("""
        SELECT wing, COUNT(*) as cnt FROM knowledge_entries
        WHERE wing != '' GROUP BY wing ORDER BY cnt DESC
    """).fetchall()
    if wings:
        print("\nWings:")
        for w in wings:
            print(f"  {w['wing']:15s}: {w['cnt']:3d}")

    # Room breakdown (top 10)
    rooms = db.execute("""
        SELECT room, COUNT(*) as cnt FROM knowledge_entries
        WHERE room != '' GROUP BY room ORDER BY cnt DESC LIMIT 10
    """).fetchall()
    if rooms:
        print("\nTop rooms:")
        for r in rooms:
            print(f"  {r['room']:15s}: {r['cnt']:3d}")

    # Knowledge graph stats
    try:
        rel_count = db.execute("SELECT COUNT(*) FROM entity_relations").fetchone()[0]
        print(f"\nKnowledge graph: {rel_count} relations")
    except sqlite3.OperationalError:
        pass

    # Embedding coverage
    try:
        emb_count = db.execute("SELECT COUNT(*) FROM embeddings WHERE source_type='knowledge'").fetchone()[0]
        print(f"Embedded: {emb_count}/{total}")
    except sqlite3.OperationalError:
        pass

    db.close()


def add_relation(subject: str, predicate: str, obj: str, session_id: str = None):
    """Add a knowledge relation (lightweight knowledge graph)."""
    db = get_db()
    er_columns = {row[1] for row in db.execute("PRAGMA table_info(entity_relations)").fetchall()}
    has_stable_id_column = "stable_id" in er_columns
    if not session_id:
        session_id = detect_session_id()
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    stable_id = _stable_sha256("entity_relation", subject or "", predicate or "", obj or "")

    try:
        if has_stable_id_column:
            db.execute(
                """
            INSERT OR IGNORE INTO entity_relations
                (subject, predicate, object, stable_id, noted_at, session_id)
            VALUES (?, ?, ?, ?, ?, ?)
        """,
                (subject, predicate, obj, stable_id, now, session_id),
            )
        else:
            db.execute(
                """
            INSERT OR IGNORE INTO entity_relations
                (subject, predicate, object, noted_at, session_id)
            VALUES (?, ?, ?, ?, ?)
        """,
                (subject, predicate, obj, now, session_id),
            )
        inserted = db.execute("SELECT changes()").fetchone()[0] > 0
        if inserted and has_stable_id_column:
            _enqueue_sync_op_fail_open(
                db,
                "entity_relations",
                stable_id,
                {
                    "subject": subject,
                    "predicate": predicate,
                    "object": obj,
                    "stable_id": stable_id,
                    "noted_at": now,
                    "session_id": session_id,
                },
            )
        db.commit()
        if db.total_changes:
            print(f"  ✅ Relation: {subject} --[{predicate}]--> {obj}")
        else:
            print("  — Relation already exists")
    except sqlite3.OperationalError as e:
        print(f"  ❌ Error: {e}. Run migrate-knowledge-v2.py first.")
    db.close()


def soft_delete_entry(entry_id: int) -> bool:
    """Soft-delete a knowledge entry by setting deleted_at timestamp.

    Returns True if the entry was found and marked deleted, False otherwise.
    The entry is not physically removed from the DB and can be restored by
    clearing the deleted_at column.  All read paths filter WHERE deleted_at IS NULL.
    """
    db = get_db()
    ke_columns = {row[1] for row in db.execute("PRAGMA table_info(knowledge_entries)").fetchall()}
    if "deleted_at" not in ke_columns:
        print("  ⚠ DB schema missing deleted_at column. Run migrate.py to enable soft-delete.", file=sys.stderr)
        db.close()
        return False
    row = db.execute("SELECT id, title, category FROM knowledge_entries WHERE id = ?", (entry_id,)).fetchone()
    if not row:
        print(f"  ⚠ Entry #{entry_id} not found.", file=sys.stderr)
        db.close()
        return False
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    db.execute("UPDATE knowledge_entries SET deleted_at = ? WHERE id = ?", (now, entry_id))
    db.commit()
    db.close()
    print(f"  🗑 Soft-deleted #{entry_id} [{row['category']}] {row['title'][:60]}")
    return True


def _insert_supersedes_relation(source_id: int, target_id: int, session_id: str | None = None) -> None:
    """Insert a SUPERSEDES relation from source_id to target_id in knowledge_relations.

    Validates that target_id exists. Idempotent via INSERT OR IGNORE.
    source_id is the new (superseding) entry; target_id is the old (superseded) entry.
    """
    db = get_db()
    try:
        target_row = db.execute(
            "SELECT id, title FROM knowledge_entries WHERE id = ?",
            (target_id,),
        ).fetchone()
        if not target_row:
            print(
                f"Error: --supersedes target ID {target_id} not found in knowledge_entries",
                file=sys.stderr,
            )
            db.close()
            sys.exit(1)

        now = __import__("datetime").datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S")
        sid = session_id or ""
        db.execute(
            """
            INSERT OR IGNORE INTO knowledge_relations
                (source_id, target_id, relation_type, confidence, created_at, session_id)
            VALUES (?, ?, 'SUPERSEDES', 1.0, ?, ?)
            """,
            (source_id, target_id, now, sid),
        )
        db.commit()
        print(f"  ↩ Supersedes #{target_id}: {target_row['title'][:60]}")
    except Exception as exc:  # noqa: BLE001
        print(f"  ⚠ Could not record SUPERSEDES relation: {exc}", file=sys.stderr)
    finally:
        db.close()


def main():
    args = sys.argv[1:]

    if not args or "--help" in args or "-h" in args:
        print(__doc__)
        return

    if "--list" in args:
        limit = 10
        if "--limit" in args:
            idx = args.index("--limit")
            limit = int(args[idx + 1]) if idx + 1 < len(args) else 10
        list_recent(limit)
        return

    if "--stats" in args:
        show_stats()
        return

    if "--flush-inbox" in args:
        limit = 100
        if "--limit" in args:
            idx = args.index("--limit")
            limit = int(args[idx + 1]) if idx + 1 < len(args) else 100
        min_age_s: float | None = None
        if "--min-age-s" in args:
            idx = args.index("--min-age-s")
            if idx + 1 < len(args):
                try:
                    min_age_s = float(args[idx + 1])
                except ValueError:
                    min_age_s = None
        result = flush_learn_inbox(limit, min_age_s=min_age_s)
        if "--json" in args:
            print(json.dumps(result, indent=2))
        else:
            print(
                "Learn inbox flush: "
                f"{result['processed']} processed, {result['remaining']} remaining, "
                f"{result['failed']} failed, {result.get('rejected', 0)} rejected"
            )
            if result["status"] == "busy":
                print("  DB still busy; retry later.", file=sys.stderr)
            if result.get("cerebrum_failed"):
                print("  Cerebrum refresh failed for one or more replayed entries.", file=sys.stderr)
        if result["status"] == "busy" or result["failed"] or result.get("cerebrum_failed"):
            sys.exit(1)
        return

    if "--from-checkpoint" in args:
        idx = args.index("--from-checkpoint")
        if idx + 1 >= len(args) or args[idx + 1].startswith("--"):
            print("Error: --from-checkpoint requires a filepath", file=sys.stderr)
            sys.exit(1)
        _cp_file = args[idx + 1]
        _cp_dry = "--dry-run" in args
        _cp_tags = ""
        if "--tags" in args:
            _ti = args.index("--tags")
            _cp_tags = args[_ti + 1] if _ti + 1 < len(args) else ""
        batch_ingest_from_checkpoint(_cp_file, dry_run=_cp_dry, tags=_cp_tags)
        return

    if "--from-pr" in args:
        idx = args.index("--from-pr")
        if idx + 1 >= len(args) or args[idx + 1].startswith("--"):
            print("Error: --from-pr requires a PR number or URL", file=sys.stderr)
            sys.exit(1)
        _pr_ref = args[idx + 1]
        _pr_dry = "--dry-run" in args
        _pr_tags = ""
        if "--tags" in args:
            _ti = args.index("--tags")
            _pr_tags = args[_ti + 1] if _ti + 1 < len(args) else ""
        batch_ingest_from_pr(_pr_ref, dry_run=_pr_dry, tags=_pr_tags)
        return

    if "--from-file" in args:
        idx = args.index("--from-file")
        if idx + 1 < len(args):
            _ff_path = args[idx + 1]
            # --as-category: single-entry mode — ingest whole file as one entry
            if "--as-category" in args:
                _ac_idx = args.index("--as-category")
                _as_cat = args[_ac_idx + 1] if _ac_idx + 1 < len(args) else ""
                if not _as_cat or _as_cat.startswith("--"):
                    print("Error: --as-category requires a category value", file=sys.stderr)
                    sys.exit(1)
                _valid_cats = ("mistake", "pattern", "decision", "tool", "feature", "refactor", "discovery")
                if _as_cat not in _valid_cats:
                    print(f"Error: --as-category must be one of: {', '.join(_valid_cats)}", file=sys.stderr)
                    sys.exit(1)
                _fpath = Path(_ff_path)
                if not _fpath.exists():
                    print(f"Error: File not found: {_ff_path}", file=sys.stderr)
                    sys.exit(1)
                _file_body = _fpath.read_text(encoding="utf-8", errors="replace")
                _file_title = _fpath.stem
                _source_key = str(_fpath.resolve())
                _batch_sid = _batch_stable_id(_source_key, _file_title)
                _dry = "--dry-run" in args
                if _dry:
                    _wc = len(_file_body.split())
                    print(f"\nDry run — single entry from '{_ff_path}'")
                    print(f"  stable_id: {_batch_sid}")
                    print(f"  title:     {_file_title}")
                    print(f"  category:  {_as_cat}")
                    print(f"  words:     {_wc}")
                    return
                if _stable_id_exists(_batch_sid):
                    print(f"  — skipping '{_file_title}' (already ingested, stable_id={_batch_sid})")
                    return
                _ff_tags = ""
                if "--tags" in args:
                    _ti = args.index("--tags")
                    _ff_tags = args[_ti + 1] if _ti + 1 < len(args) else ""
                _eid = with_retry(
                    add_entry,
                    _as_cat,
                    _file_title,
                    _file_body[:10000],
                    tags=_ff_tags,
                    session_id="batch-ingest",
                    skip_gate=True,
                )
                if _eid >= 0:
                    _set_entry_stable_id(_eid, _batch_sid)
                    print(f"  Inserted entry #{_eid} [{_as_cat}] from {_ff_path}")
                return
            imported = import_from_file(args[idx + 1])
            # -1 means "headers parsed but no body content" — valid but empty; exit 0.
            if imported == -1:
                return
            # 0 means "file not found" or "no headers found" — hard failure.
            if not imported:
                sys.exit(1)
            # Support --update-cerebrum after bulk import (same opt-in contract as
            # single-entry writes; cerebrum refresh runs once after all entries land).
            # Only run if the import actually succeeded (imported > 0).
            if "--update-cerebrum" in args:
                _co = "CEREBRUM.md"
                if "--cerebrum-output" in args:
                    _ci = args.index("--cerebrum-output")
                    _cnext = args[_ci + 1] if _ci + 1 < len(args) else None
                    _co = _cnext if _cnext and not _cnext.startswith("--") else "CEREBRUM.md"
                _cs: str | None = None
                if "--cerebrum-sections" in args:
                    _ci = args.index("--cerebrum-sections")
                    _cnext = args[_ci + 1] if _ci + 1 < len(args) else None
                    _cs = _cnext if _cnext and not _cnext.startswith("--") else None
                _json_mode = "--json" in args
                rc = _auto_update_cerebrum(_co, _cs, json_mode=_json_mode)
                if rc != 0:
                    sys.exit(rc)
        else:
            print("Error: --from-file requires a filepath")
            sys.exit(1)
        return

    # Handle --relate command
    if "--relate" in args:
        idx = args.index("--relate")
        positional = [a for a in args[idx + 1 :] if not a.startswith("--")]
        if len(positional) < 3:
            print("Error: --relate needs 3 args: subject predicate object")
            print('  Example: python learn.py --relate "copyToGroup" "reads_from" "config.json"')
            return
        add_relation(positional[0], positional[1], positional[2])
        return

    # Handle --soft-delete command (#387)
    if "--soft-delete" in args:
        idx = args.index("--soft-delete")
        raw_id = args[idx + 1] if idx + 1 < len(args) else ""
        try:
            entry_id = int(raw_id)
        except (ValueError, TypeError):
            print(f"Error: --soft-delete requires an integer entry ID (got {raw_id!r})", file=sys.stderr)
            sys.exit(1)
        result = soft_delete_entry(entry_id)
        if not result:
            sys.exit(1)
        return

    # Parse category flag
    category = None
    for flag, cat in [
        ("--mistake", "mistake"),
        ("--error-pattern", "mistake"),  # #403 shorthand for structured error mistakes
        ("--pattern", "pattern"),
        ("--decision", "decision"),
        ("--tool", "tool"),
        ("--feature", "feature"),
        ("--refactor", "refactor"),
        ("--discovery", "discovery"),
    ]:
        if flag in args:
            category = cat
            break

    if not category:
        print("Error: Specify a category: --mistake, --pattern, --decision, --tool, --feature, --refactor, --discovery")
        print("Or use --relate for knowledge graph. Run --help for usage.")
        return

    # Parse optional flags
    tags = ""
    session_id = None
    confidence = None
    wing = ""
    room = ""
    facts = []
    task_id = ""
    affected_files = []
    source_file = ""
    start_line = 0
    end_line = 0
    code_language = ""
    code_snippet = ""
    code_location_set = False
    error_type = ""
    root_cause = ""
    severity = ""
    fix_steps = ""
    valence = ""
    intensity = None
    priority = ""
    agent_id = ""
    certainty = ""
    caveats = ""

    if "--tags" in args:
        idx = args.index("--tags")
        tags = args[idx + 1] if idx + 1 < len(args) else ""

    if "--session" in args:
        idx = args.index("--session")
        session_id = args[idx + 1] if idx + 1 < len(args) else None

    if "--confidence" in args:
        idx = args.index("--confidence")
        confidence = float(args[idx + 1]) if idx + 1 < len(args) else None

    if "--wing" in args:
        idx = args.index("--wing")
        wing = args[idx + 1] if idx + 1 < len(args) else ""

    if "--room" in args:
        idx = args.index("--room")
        room = args[idx + 1] if idx + 1 < len(args) else ""

    if "--task" in args:
        idx = args.index("--task")
        task_id = args[idx + 1] if idx + 1 < len(args) else ""

    if "--code-location" in args:
        idx = args.index("--code-location")
        if idx + 1 >= len(args):
            print("Error: --code-location requires a value", file=sys.stderr)
            sys.exit(1)
        raw_code_location = args[idx + 1]
        try:
            source_file, start_line, end_line = _parse_code_location(raw_code_location)
        except ValueError as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)
        code_snippet, code_language = _extract_code_snippet(source_file, start_line, end_line)
        code_location_set = True

    if "--error-type" in args:
        idx = args.index("--error-type")
        error_type = args[idx + 1] if idx + 1 < len(args) else ""

    if "--root-cause" in args:
        idx = args.index("--root-cause")
        root_cause = args[idx + 1] if idx + 1 < len(args) else ""

    if "--severity" in args:
        idx = args.index("--severity")
        severity = args[idx + 1] if idx + 1 < len(args) else ""

    if "--fix-step" in args:
        fix_steps_parts = []
        for i, a in enumerate(args):
            if a == "--fix-step" and i + 1 < len(args):
                fix_steps_parts.append(args[i + 1])
        fix_steps = " → ".join(fix_steps_parts)

    if "--valence" in args:
        idx = args.index("--valence")
        raw_valence = args[idx + 1] if idx + 1 < len(args) else ""
        _valid_valences = ("reward", "neutral", "penalty", "trauma", "")
        if raw_valence not in _valid_valences:
            print(
                f"Error: --valence must be one of: reward, neutral, penalty, trauma (got {raw_valence!r})",
                file=sys.stderr,
            )
            sys.exit(1)
        valence = raw_valence

    if "--intensity" in args:
        idx = args.index("--intensity")
        raw_intensity = args[idx + 1] if idx + 1 < len(args) else ""
        try:
            intensity = float(raw_intensity)
            if not (0.0 <= intensity <= 1.0):
                raise ValueError("out of range")
        except (ValueError, TypeError):
            print(
                f"Error: --intensity must be a float between 0.0 and 1.0 (got {raw_intensity!r})",
                file=sys.stderr,
            )
            sys.exit(1)

    if "--priority" in args:
        idx = args.index("--priority")
        raw_priority = args[idx + 1] if idx + 1 < len(args) else ""
        _valid_priorities = ("P0", "P1", "P2", "P3")
        if raw_priority not in _valid_priorities:
            print(
                f"Error: --priority must be one of: P0, P1, P2, P3 (got {raw_priority!r})",
                file=sys.stderr,
            )
            sys.exit(1)
        priority = raw_priority

    if "--agent-id" in args:
        idx = args.index("--agent-id")
        agent_id = args[idx + 1] if idx + 1 < len(args) else ""

    if "--certainty" in args:
        idx = args.index("--certainty")
        raw_certainty = args[idx + 1] if idx + 1 < len(args) else ""
        _valid_certainties = ("high", "medium", "low", "uncertain", "")
        if raw_certainty not in _valid_certainties:
            print(
                f"Error: --certainty must be one of: high, medium, low, uncertain (got {raw_certainty!r})",
                file=sys.stderr,
            )
            sys.exit(1)
        certainty = raw_certainty

    if "--caveats" in args:
        idx = args.index("--caveats")
        caveats = args[idx + 1] if idx + 1 < len(args) else ""

    supersedes_id = None
    if "--supersedes" in args:
        idx = args.index("--supersedes")
        raw_sup = args[idx + 1] if idx + 1 < len(args) else ""
        if not raw_sup or raw_sup.startswith("--"):
            print("Error: --supersedes requires a knowledge entry ID", file=sys.stderr)
            sys.exit(1)
        try:
            supersedes_id = int(raw_sup)
        except ValueError:
            print(f"Error: --supersedes value must be an integer ID (got {raw_sup!r})", file=sys.stderr)
            sys.exit(1)

    # Collect all --fact and --file values (repeatable flags)
    for i, a in enumerate(args):
        if a == "--fact" and i + 1 < len(args):
            facts.append(args[i + 1])
        elif a == "--file" and i + 1 < len(args):
            affected_files.append(args[i + 1])

    # Extract title and content (positional args after flag)
    positional = []
    skip_next = False
    for i, a in enumerate(args):
        if skip_next:
            skip_next = False
            continue
        if a in (
            "--mistake",
            "--error-pattern",
            "--pattern",
            "--decision",
            "--tool",
            "--feature",
            "--refactor",
            "--discovery",
        ):
            continue
        if a in (
            "--tags",
            "--session",
            "--confidence",
            "--limit",
            "--wing",
            "--room",
            "--fact",
            "--task",
            "--file",
            "--code-location",
            "--error-type",
            "--root-cause",
            "--severity",
            "--fix-step",
            "--valence",
            "--intensity",
            "--priority",
            "--agent-id",
            "--certainty",
            "--caveats",
            "--supersedes",
            "--cerebrum-output",
            "--cerebrum-sections",
        ):
            _next = args[i + 1] if i + 1 < len(args) else None
            skip_next = bool(_next and not _next.startswith("--"))
            continue
        if a.startswith("--"):
            continue
        positional.append(a)

    if len(positional) < 2:
        print("Error: Need title and content. Example:")
        print(f'  python learn.py --{category} "Title" "Description"')
        return

    title = positional[0][:200]  # Limit title length
    content = " ".join(positional[1:])[:10000]  # Limit content to 10KB

    # Quality gate for mistake/pattern/discovery
    skip_gate = "--skip-gate" in args
    skip_scan = "--skip-scan" in args
    json_mode = "--json" in args
    update_cerebrum = "--update-cerebrum" in args

    cerebrum_output = "CEREBRUM.md"
    if "--cerebrum-output" in args:
        idx = args.index("--cerebrum-output")
        _cnext = args[idx + 1] if idx + 1 < len(args) else None
        cerebrum_output = _cnext if _cnext and not _cnext.startswith("--") else "CEREBRUM.md"

    cerebrum_sections: str | None = None
    if "--cerebrum-sections" in args:
        idx = args.index("--cerebrum-sections")
        _cnext = args[idx + 1] if idx + 1 < len(args) else None
        cerebrum_sections = _cnext if _cnext and not _cnext.startswith("--") else None
    gate_categories = {"mistake", "pattern", "discovery"}
    if not json_mode:
        if category in gate_categories and not skip_gate:
            print(f"Recording {category}...")
            print("  ℹ Quality gate (bypass with --skip-gate):")
            print("    ✓ Could someone Google this in 5 min? → Must be NO")
            print("    ✓ Specific to THIS codebase/project? → Must be YES")
            print("    ✓ Required real debugging/investigation? → Must be YES")
            print("  Gate passed (agent responsibility — record honestly)")
        else:
            print(f"Recording {category}...")

    # Strict taxonomy check: validate provided wing/room against registered taxonomy
    if "--strict-taxonomy" in args and (wing or room):
        _taxonomy_script = Path(__file__).with_name("taxonomy.py")
        if _taxonomy_script.is_file():
            import subprocess as _sp

            _tx_result = _sp.run(
                [sys.executable, str(_taxonomy_script), "validate"],
                capture_output=True,
                text=True,
            )
            if _tx_result.returncode != 0:
                print(f"Taxonomy validation failed: {_tx_result.stderr.strip()}", file=sys.stderr)
                print(
                    "Use `python taxonomy.py add <wing> <room>` to register, or omit --strict-taxonomy.",
                    file=sys.stderr,
                )
                sys.exit(1)
        else:
            print("Warning: taxonomy.py not found; --strict-taxonomy skipped.", file=sys.stderr)

    entry_kwargs = {
        "category": category,
        "title": title,
        "content": content,
        "tags": tags,
        "session_id": session_id,
        "confidence": confidence,
        "wing": wing,
        "room": room,
        "facts": facts,
        "skip_gate": skip_gate,
        "skip_scan": skip_scan,
        "task_id": task_id,
        "affected_files": affected_files,
        "source_file": source_file,
        "start_line": start_line,
        "end_line": end_line,
        "code_language": code_language,
        "code_snippet": code_snippet,
        "code_location_set": code_location_set,
        "quiet": json_mode,
        "error_type": error_type,
        "root_cause": root_cause,
        "severity": severity,
        "fix_steps": fix_steps,
        "valence": valence,
        "intensity": intensity,
        "priority": priority,
        "agent_id": agent_id,
        "certainty": certainty,
        "caveats": caveats,
    }

    try:
        queue_on_lock = _learn_queue_on_lock_enabled()
        entry_id = _write_learn_entry(
            entry_kwargs,
            max_attempts=1 if queue_on_lock else 5,
            db_busy_timeout_ms=_learn_queue_busy_timeout_ms() if queue_on_lock else None,
        )
    except sqlite3.OperationalError as exc:
        if _is_busy_error(exc) and queue_on_lock:
            queued_path = _queue_learn_payload(
                _build_learn_payload(
                    args,
                    entry_kwargs,
                    update_cerebrum=update_cerebrum,
                    cerebrum_output=cerebrum_output,
                    cerebrum_sections=cerebrum_sections,
                )
            )
            if json_mode:
                print(
                    json.dumps(
                        {
                            "status": "queued",
                            "reason": "database_locked",
                            "queue_path": str(queued_path),
                            "flush_command": "sk learn --flush-inbox",
                        },
                        indent=2,
                        ensure_ascii=False,
                    )
                )
            else:
                print(
                    f"  DB busy; queued learn entry for later flush: {queued_path.name}",
                    file=sys.stderr,
                )
                print("  Run `sk learn --flush-inbox` to replay queued entries.", file=sys.stderr)
            return
        raise

    if entry_id >= 0 and category == "pattern":
        _emit_knowledge_event_fail_open(
            "pattern_learned",
            {
                "entry_id": entry_id,
                "title": title,
                "task_id": task_id,
                "wing": wing,
                "room": room,
                "priority": priority or "P2",
                "confidence": confidence,
            },
        )

    if supersedes_id is not None and entry_id >= 0:
        _insert_supersedes_relation(entry_id, supersedes_id, session_id)

    if json_mode:
        # Machine-readable output: emit structured JSON with write result
        if entry_id < 0:
            print(json.dumps({"status": "rejected", "reason": "injection_scan_failed"}, indent=2))
            return
        db = get_db()
        # Guard against pre-v21 DBs that lack valence/intensity columns.
        _json_cols = {r[1] for r in db.execute("PRAGMA table_info(knowledge_entries)").fetchall()}
        _has_vi = all(c in _json_cols for c in ("valence", "intensity"))
        _has_priority_col = "priority" in _json_cols
        _vi_select = (
            ",\n                   COALESCE(valence, '') AS valence,"
            "\n                   COALESCE(intensity, 0.5) AS intensity"
            if _has_vi
            else ""
        )
        _priority_select = ",\n                   COALESCE(priority, 'P2') AS priority" if _has_priority_col else ""
        row = db.execute(
            f"""
            SELECT id, category, title, confidence, session_id, task_id,
                   affected_files, facts, occurrence_count, last_seen{_vi_select}{_priority_select}
            FROM knowledge_entries WHERE id = ?
        """,
            (entry_id,),
        ).fetchone()
        db.close()
        if row:
            try:
                files = json.loads(row["affected_files"] or "[]")
            except Exception:
                files = []
            try:
                facts_out = json.loads(row["facts"] or "[]")
            except Exception:
                facts_out = []
            status = "added" if row["occurrence_count"] == 1 else "updated"
            out_dict = {
                "status": status,
                "id": row["id"],
                "category": row["category"],
                "title": row["title"],
                "confidence": row["confidence"],
                "session_id": row["session_id"],
                "task_id": row["task_id"] or "",
                "affected_files": files,
                "facts": facts_out,
                "occurrence_count": row["occurrence_count"],
                "last_seen": row["last_seen"],
            }
            if _has_vi:
                try:
                    out_dict["valence"] = row["valence"]
                    out_dict["intensity"] = row["intensity"]
                except (IndexError, KeyError):
                    pass
            if _has_priority_col:
                try:
                    out_dict["priority"] = row["priority"]
                except (IndexError, KeyError):
                    pass
            print(json.dumps(out_dict, indent=2, ensure_ascii=False))
        else:
            print(json.dumps({"status": "error", "id": entry_id, "reason": "entry_not_found_after_write"}, indent=2))
        if update_cerebrum and entry_id >= 0:
            rc = _auto_update_cerebrum(cerebrum_output, cerebrum_sections, json_mode=True)
            if rc != 0:
                sys.exit(rc)
        return

    if facts:
        print(f"  With {len(facts)} fact(s)")
    if affected_files:
        print(f"  Affecting {len(affected_files)} file(s): {', '.join(affected_files[:3])}")
    print("Done.")
    if update_cerebrum and entry_id >= 0:
        rc = _auto_update_cerebrum(cerebrum_output, cerebrum_sections)
        if rc != 0:
            sys.exit(rc)


if __name__ == "__main__":
    main()
