#!/usr/bin/env python3
"""
clarify.py - Generate bounded clarification questions for underspecified tasks.

Usage:
    python clarify.py "implement auth flow"
    python clarify.py "implement auth flow" --json
    python clarify.py "implement auth flow" --no-store
    python clarify.py "implement auth flow" --repo C:\\path\\to\\repo
"""

import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path

if os.name == "nt":
    for _stream in (sys.stdout, sys.stderr):
        if hasattr(_stream, "reconfigure"):
            _stream.reconfigure(encoding="utf-8", errors="replace")

SESSION_STATE = Path.home() / ".copilot" / "session-state"
STORE_PATH = SESSION_STATE / "clarifications.json"
MAX_QUESTIONS = 5
STORE_LIMIT = 50

_TOKEN_RE = re.compile(r"[A-Za-z0-9_.:/-]+")
_NUMBER_RE = re.compile(r"\b\d+(?:\.\d+)?\b")
_PATH_RE = re.compile(r"[\\/]|`[^`]+`|[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+")
_VAGUE_TERMS = {
    "add",
    "change",
    "clean",
    "create",
    "enhance",
    "fix",
    "handle",
    "improve",
    "manage",
    "optimize",
    "refactor",
    "support",
    "update",
}
_DOMAIN_MARKERS = {
    "agent",
    "api",
    "auth",
    "backend",
    "briefing",
    "browser",
    "cli",
    "config",
    "context",
    "copilot",
    "database",
    "db",
    "frontend",
    "hook",
    "knowledge",
    "skill",
    "sync",
    "ui",
    "watcher",
}
_CATEGORY_DEFAULTS = {
    "Functional Scope": 0.9,
    "Domain": 0.4,
    "UX": 0.2,
    "Security": 0.3,
    "Performance": 0.2,
    "Integration": 0.5,
    "Error Handling": 0.5,
    "Testing": 0.8,
    "Deployment": 0.2,
    "Business Rules": 0.4,
}

TAXONOMY = [
    {
        "name": "Functional Scope",
        "keywords": ["add", "build", "change", "create", "fix", "implement", "refactor", "support", "update"],
        "specificity": ["only", "minimal", "mvp", "single", "full", "end-to-end", "all", "just"],
        "question": "What exact functional boundary should this task cover?",
        "options": [
            "A. Minimal happy-path only",
            "B. Full user-facing flow",
            "C. End-to-end plus edge cases",
            "Custom",
        ],
    },
    {
        "name": "Domain",
        "keywords": ["agent", "api", "auth", "backend", "briefing", "browser", "cli", "config", "database", "ui"],
        "specificity": ["file", "module", "repo", "script", "service", "workflow"],
        "question": "Which concrete repo area, module, or system boundary is in scope?",
        "options": [
            "A. One known file/module",
            "B. One subsystem across a few files",
            "C. Cross-cutting change across multiple systems",
            "Custom",
        ],
    },
    {
        "name": "UX",
        "keywords": ["copy", "prompt", "screen", "settings", "ui", "ux", "user", "workflow"],
        "specificity": ["accessibility", "layout", "message", "screen", "text", "tone"],
        "question": "What user-facing behavior or interaction should this feel like?",
        "options": [
            "A. Keep current UX unchanged",
            "B. Small UX improvement only",
            "C. New user flow or prompt shape",
            "Custom",
        ],
    },
    {
        "name": "Security",
        "keywords": ["auth", "credential", "permission", "secret", "security", "token"],
        "specificity": ["encrypt", "least-privilege", "sanitize", "validate", "verify"],
        "question": "Are there security or trust boundaries that this task must preserve?",
        "options": [
            "A. No new security-sensitive surface",
            "B. Existing auth/trust rules must stay unchanged",
            "C. New validation / auth / secret-handling is required",
            "Custom",
        ],
    },
    {
        "name": "Performance",
        "keywords": ["fast", "latency", "optimize", "performance", "perf", "slow"],
        "specificity": ["budget", "ms", "seconds", "throughput", "target"],
        "question": "Is there a performance budget or latency target for this work?",
        "options": [
            "A. Correctness only; no explicit perf target",
            "B. Keep current performance envelope",
            "C. Hit a specific performance target",
            "Custom",
        ],
    },
    {
        "name": "Integration",
        "keywords": ["api", "bridge", "briefing", "hook", "import", "integrate", "merge", "surface", "sync"],
        "specificity": ["consumer", "producer", "upstream", "downstream", "read", "write"],
        "question": "What other command, file, or subsystem must consume or produce this result?",
        "options": [
            "A. Standalone behavior only",
            "B. One downstream integration",
            "C. Multiple integrations must stay in sync",
            "Custom",
        ],
    },
    {
        "name": "Error Handling",
        "keywords": ["error", "fail", "guard", "invalid", "retry", "validation"],
        "specificity": ["message", "raise", "return", "warning", "exit"],
        "question": "How should invalid input or failure conditions be surfaced?",
        "options": [
            "A. Fail loudly with explicit error",
            "B. Return structured validation feedback",
            "C. Retry/recover with user-visible guidance",
            "Custom",
        ],
    },
    {
        "name": "Testing",
        "keywords": ["acceptance", "assert", "coverage", "pytest", "test", "tests", "verification"],
        "specificity": ["fixture", "integration", "regression", "unit"],
        "question": "What test evidence is required before this is considered done?",
        "options": [
            "A. Focused unit tests only",
            "B. Focused plus existing regression suites",
            "C. Full end-to-end / operator verification",
            "Custom",
        ],
    },
    {
        "name": "Deployment",
        "keywords": ["ci", "deploy", "install", "launcher", "release", "ship"],
        "specificity": ["migration", "rollout", "windows", "linux", "macos", "version"],
        "question": "Does this change need install, rollout, or environment-specific handling?",
        "options": [
            "A. No deployment/install impact",
            "B. Update one install/runtime path",
            "C. Multi-environment rollout considerations",
            "Custom",
        ],
    },
    {
        "name": "Business Rules",
        "keywords": ["budget", "policy", "priority", "quota", "rule", "rules", "validation"],
        "specificity": ["allow", "deny", "must", "never", "only", "threshold"],
        "question": "What non-technical rule or policy determines the correct behavior?",
        "options": [
            "A. Follow current behavior unchanged",
            "B. Add one explicit rule/constraint",
            "C. Introduce a new policy matrix or thresholds",
            "Custom",
        ],
    },
]


def _atomic_write_text(path: Path, content: str, encoding: str = "utf-8") -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_bytes(content.encode(encoding))
        os.replace(str(tmp), str(path))
    except Exception:
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass
        raise


def _find_git_root(start: Path | None = None) -> Path:
    current = (start or Path.cwd()).resolve()
    for candidate in [current, *current.parents]:
        if (candidate / ".git").exists():
            return candidate
    return current


def _normalize_query(query: str) -> str:
    normalized = (query or "").lower().strip()
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized[:500]


def _query_tokens(query: str) -> set[str]:
    return {match.group(0).lower() for match in _TOKEN_RE.finditer(query or "") if len(match.group(0)) >= 3}


def _contains_any(tokens: set[str], values: list[str]) -> bool:
    return any(value in tokens for value in values)


def _category_score(category: dict, query: str, tokens: set[str]) -> float:
    name = category["name"]
    score = _CATEGORY_DEFAULTS.get(name, 0.0)
    relevant = _contains_any(tokens, category["keywords"])
    specific = any(spec in query for spec in category["specificity"])

    if len(tokens) < 10:
        score += 0.2
    if tokens & _VAGUE_TERMS:
        score += 0.25
    if _NUMBER_RE.search(query):
        score -= 0.15
    if _PATH_RE.search(query):
        score -= 0.1

    if name == "Functional Scope":
        if relevant:
            score += 0.35
        if not specific:
            score += 0.45
    elif name == "Domain":
        if not (tokens & _DOMAIN_MARKERS):
            score += 0.8
        elif not specific:
            score += 0.2
    elif name == "UX":
        if relevant and not specific:
            score += 0.7
        elif relevant:
            score += 0.2
        else:
            score -= 0.2
    elif name == "Security":
        if relevant and not specific:
            score += 0.8
        elif relevant:
            score += 0.3
        else:
            score -= 0.15
    elif name == "Performance":
        if relevant and not (_NUMBER_RE.search(query) or specific):
            score += 0.8
        elif relevant:
            score += 0.15
        else:
            score -= 0.25
    elif name == "Integration":
        if relevant and not specific:
            score += 0.75
        elif relevant:
            score += 0.25
    elif name == "Error Handling":
        if relevant and not specific:
            score += 0.7
        elif not relevant:
            score += 0.15
    elif name == "Testing":
        if not _contains_any(tokens, category["keywords"]):
            score += 0.9
        elif not specific:
            score += 0.25
    elif name == "Deployment":
        if relevant and not specific:
            score += 0.65
        elif not relevant:
            score -= 0.1
    elif name == "Business Rules":
        if relevant and not specific:
            score += 0.7
        elif not relevant:
            score += 0.15

    return score


def _build_questions(query: str, max_questions: int = MAX_QUESTIONS) -> list[dict]:
    normalized = _normalize_query(query)
    tokens = _query_tokens(normalized)
    ranked = []
    for category in TAXONOMY:
        score = _category_score(category, normalized, tokens)
        ranked.append(
            {
                "category": category["name"],
                "question": category["question"],
                "options": list(category["options"]),
                "score": round(score, 3),
            }
        )
    ranked.sort(key=lambda item: (-item["score"], item["category"]))
    return [
        {k: v for k, v in item.items() if k != "score"} for item in ranked[: max(1, min(MAX_QUESTIONS, max_questions))]
    ]


def _build_clarified_task(query: str, questions: list[dict]) -> str:
    task = " ".join((query or "").split())
    if not questions:
        return (
            f"Implement {task} using existing repo conventions and validate the change with the existing focused gates."
        )
    categories = ", ".join(question["category"] for question in questions[:3])
    return (
        f"Implement {task} with explicit confirmation of the key open areas before coding. "
        f"Resolve clarification around {categories} so the work stays bounded and verifiable."
    )


def _load_store(path: Path | None = None) -> dict:
    target = path or STORE_PATH
    try:
        if target.exists():
            data = json.loads(target.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("entries", []), list):
                return data
    except Exception:
        pass
    return {"entries": []}


def _store_entry(entry: dict, path: Path | None = None) -> None:
    target = path or STORE_PATH
    payload = _load_store(target)
    entries = [item for item in payload.get("entries", []) if isinstance(item, dict) and item.get("id") != entry["id"]]
    entries.insert(0, entry)
    payload["entries"] = entries[:STORE_LIMIT]
    target.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write_text(target, json.dumps(payload, indent=2, ensure_ascii=False))


def _entry_id(repo_root: str, normalized_query: str) -> str:
    digest = hashlib.sha1(f"{repo_root}\n{normalized_query}".encode()).hexdigest()
    return digest[:16]


def _serialize_result(query: str, repo_root: str, questions: list[dict]) -> dict:
    normalized = _normalize_query(query)
    tokens = sorted(_query_tokens(normalized))
    entry = {
        "id": _entry_id(repo_root, normalized),
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "repo_root": repo_root,
        "raw_query": query,
        "normalized_query": normalized,
        "tokens": tokens,
        "taxonomy_categories": [category["name"] for category in TAXONOMY],
        "questions": questions[:MAX_QUESTIONS],
        "clarified_task": _build_clarified_task(query, questions),
    }
    return entry


def _format_text(result: dict, store_path: Path, stored: bool) -> str:
    lines = [
        f"❓ Clarify Gate: {result['raw_query']}",
        "",
        "Clarified task",
        f"  {result['clarified_task']}",
        "",
        f"Questions ({len(result['questions'])}/{len(result['taxonomy_categories'])} categories scanned)",
    ]
    for index, question in enumerate(result["questions"], 1):
        lines.append(f"  {index}. [{question['category']}] {question['question']}")
        lines.append("     " + " | ".join(question["options"]))
    if stored:
        lines.extend(["", f"Stored: {store_path}"])
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    if not args or "--help" in args or "-h" in args:
        print(__doc__)
        return 0

    emit_json = "--json" in args
    store = "--no-store" not in args
    repo_root = None
    if "--repo" in args:
        idx = args.index("--repo")
        if idx + 1 >= len(args):
            print("clarify: --repo requires a path", file=sys.stderr)
            return 2
        repo_root = _find_git_root(Path(args[idx + 1]))

    consumed = set()
    for flag in ("--json", "--no-store"):
        if flag in args:
            consumed.add(args.index(flag))
    if "--repo" in args:
        idx = args.index("--repo")
        consumed.update({idx, idx + 1})

    query_parts = [part for index, part in enumerate(args) if index not in consumed and not part.startswith("--")]
    query = " ".join(query_parts).strip()
    if not query:
        print("clarify: provide a task description", file=sys.stderr)
        return 2

    root = (repo_root or _find_git_root()).resolve().as_posix()
    questions = _build_questions(query, max_questions=MAX_QUESTIONS)
    result = _serialize_result(query, root, questions)
    if store:
        _store_entry(result)

    if emit_json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print(_format_text(result, STORE_PATH, stored=store))
    return 0


if __name__ == "__main__":
    sys.exit(main())
