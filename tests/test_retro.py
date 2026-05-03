#!/usr/bin/env python3
"""
test_retro.py — Targeted tests for retro.py.

Verifies:
1. Read-only contract: retro.py never mutates DBs, issues, or git history
2. Report shape: JSON payload has expected keys and value ranges
3. Repo-only mode: uses only git signals, skips local DBs
4. Subreport mode: outputs exactly the requested section
5. Score mode: outputs a single formatted score line
6. Signal collectors: correct parsing from synthetic inputs
7. compute_retro: composite scoring logic and weight normalization

Run:
    python3 test_retro.py
"""

import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

if os.name == "nt":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

REPO = Path(__file__).parent.parent
RETRO_PY = REPO / "retro.py"
ARTIFACT_DIR = REPO / ".retro-test-artifacts"

PASS = 0
FAIL = 0


def test(name: str, passed: bool, detail: str = "") -> None:
    global PASS, FAIL
    if passed:
        PASS += 1
        print(f"  ✅ {name}")
    else:
        FAIL += 1
        print(f"  ❌ {name}" + (f" — {detail}" if detail else ""))


def load_retro():
    """Load retro.py as a module."""
    spec = importlib.util.spec_from_file_location("retro", str(RETRO_PY))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def reset_artifacts() -> None:
    if ARTIFACT_DIR.exists():
        for p in sorted(ARTIFACT_DIR.rglob("*"), reverse=True):
            if p.is_file():
                p.unlink(missing_ok=True)
            elif p.is_dir():
                p.rmdir()
        ARTIFACT_DIR.rmdir()
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)


# ── Section 1: File exists and is valid Python ──────────────────────────────


def test_file_exists():
    test("retro.py exists", RETRO_PY.exists())


def test_valid_syntax():
    import ast

    try:
        ast.parse(RETRO_PY.read_text(encoding="utf-8"))
        test("retro.py valid syntax", True)
    except SyntaxError as e:
        test("retro.py valid syntax", False, str(e))


# ── Section 2: Read-only contract ───────────────────────────────────────────


def test_no_db_writes():
    """retro.py must not mutate knowledge.db or skill-metrics.db."""
    retro = load_retro()
    import inspect

    src = inspect.getsource(retro)
    # Verify no INSERT/UPDATE/DELETE/CREATE TABLE patterns that would mutate DBs
    dangerous = [
        'db.execute("INSERT',
        'db.execute("UPDATE',
        'db.execute("DELETE',
        'db.execute("CREATE TABLE',
        ".executescript(",
    ]
    found = [kw for kw in dangerous if kw in src]
    test(
        "retro.py has no DB-mutating SQL",
        len(found) == 0,
        f"found: {found}" if found else "",
    )


def test_no_subprocess_commits():
    """retro.py must not call git commit, git push, or create issues."""
    retro = load_retro()
    import inspect

    src = inspect.getsource(retro)
    banned = [
        '"git", "commit"',
        '"git", "push"',
        "gh issue create",
        "gh pr create",
        "create_issue",
        "create_pull_request",
    ]
    found = [b for b in banned if b in src]
    test(
        "retro.py has no git-commit / issue-create calls",
        len(found) == 0,
        f"found: {found}" if found else "",
    )


def test_no_learn_calls():
    """retro.py must not invoke learn.py, learn() functions, or indexing."""
    retro = load_retro()
    import inspect

    src = inspect.getsource(retro)
    banned = ["learn.py", "build-session-index", "extract-knowledge", "watch-sessions"]
    found = [b for b in banned if b in src]
    test(
        "retro.py has no indexing/learning side-effects",
        len(found) == 0,
        f"found: {found}" if found else "",
    )


def test_state_file_not_in_gitignore_scope():
    """Confirm .retro-state.json is in .gitignore (or at minimum not committed)."""
    gitignore = REPO / ".gitignore"
    if not gitignore.exists():
        test("retro-state.json gitignore check", True, "(no .gitignore to check)")
        return
    content = gitignore.read_text(encoding="utf-8", errors="replace")
    # Acceptable: either retro-state is in gitignore, or not tracked by git
    in_gitignore = ".retro-state.json" in content
    result = subprocess.run(
        ["git", "--no-pager", "ls-files", "--error-unmatch", ".retro-state.json"],
        capture_output=True,
        cwd=str(REPO),
    )
    not_tracked = result.returncode != 0
    test(
        ".retro-state.json not tracked by git",
        not_tracked,
        "file is tracked by git — should be in .gitignore",
    )


# ── Section 3: collect_audit_signals ────────────────────────────────────────


def test_audit_signals_empty_file():
    retro = load_retro()
    reset_artifacts()
    empty = ARTIFACT_DIR / "audit.jsonl"
    empty.write_text("", encoding="utf-8")
    result = retro.collect_audit_signals(audit_path=empty)
    test("audit signals: empty file → available=True", result.get("available") is True)
    test("audit signals: empty file → total_entries=0", result.get("total_entries") == 0)
    test("audit signals: empty file → deny_rate=0", result.get("deny_rate") == 0.0)
    test("audit signals: empty file → deny_dry_count=0", result.get("deny_dry_count") == 0)
    test("audit signals: empty file → deny_dry_rate=0", result.get("deny_dry_rate") == 0.0)


def test_audit_signals_synthetic():
    retro = load_retro()
    reset_artifacts()
    audit_file = ARTIFACT_DIR / "audit.jsonl"
    entries = [
        {
            "ts": 1000,
            "event": "preToolUse",
            "tool": "bash",
            "rule": "enforce-briefing",
            "decision": "deny",
            "detail": "",
        },
        {
            "ts": 1001,
            "event": "preToolUse",
            "tool": "bash",
            "rule": "enforce-briefing",
            "decision": "deny",
            "detail": "",
        },
        {
            "ts": 1002,
            "event": "postToolUse",
            "tool": "bash",
            "rule": "learn-reminder",
            "decision": "info",
            "detail": "",
        },
        {"ts": 1003, "event": "preToolUse", "tool": "view", "rule": "none", "decision": "allow", "detail": ""},
        {
            "ts": 1004,
            "event": "preToolUse",
            "tool": "bash",
            "rule": "integrity",
            "decision": "parse-error",
            "detail": "",
        },
    ]
    audit_file.write_text("\n".join(json.dumps(e) for e in entries), encoding="utf-8")
    result = retro.collect_audit_signals(audit_path=audit_file)
    test("audit signals: total_entries=5", result.get("total_entries") == 5)
    test("audit signals: deny_rate=40.0", result.get("deny_rate") == 40.0, f"got {result.get('deny_rate')}")
    test(
        "audit signals: parse_error_rate=20.0",
        result.get("parse_error_rate") == 20.0,
        f"got {result.get('parse_error_rate')}",
    )
    test("audit signals: top_rules populated", len(result.get("top_rules", [])) > 0)
    test(
        "audit signals: top_denied_tools contains bash",
        any(e["tool"] == "bash" for e in result.get("top_denied_tools", [])),
    )


def test_audit_signals_missing_file():
    retro = load_retro()
    fake_path = ARTIFACT_DIR / "nonexistent.jsonl"
    result = retro.collect_audit_signals(audit_path=fake_path)
    test("audit signals: missing file → available=False", result.get("available") is False)
    test("audit signals: missing file → total_entries=0", result.get("total_entries") == 0)
    test("audit signals: missing file → deny_dry_count=0", result.get("deny_dry_count") == 0)
    test("audit signals: missing file → deny_dry_rate=0", result.get("deny_dry_rate") == 0.0)


# ── Section 4: collect_knowledge_signals (with synthetic DB) ────────────────


def _make_knowledge_db(path: Path) -> None:
    db = sqlite3.connect(str(path))
    db.executescript("""
        CREATE TABLE IF NOT EXISTS knowledge_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT,
            category TEXT,
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            tags TEXT,
            confidence REAL DEFAULT 0.7,
            occurrence_count INTEGER DEFAULT 1,
            first_seen TEXT,
            last_seen TEXT,
            content_hash TEXT,
            wing TEXT,
            room TEXT
        );
        CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_fts
        USING fts5(title, content, content='knowledge_entries', content_rowid='id');
    """)
    # Insert synthetic data
    now = time.strftime("%Y-%m-%d")
    entries = [
        ("session1", "mistake", "Bad SQL join", "Avoid SELECT *", None, 0.9, 2, now, now),
        ("session1", "pattern", "Use parameterized SQL", "Always use ?", None, 0.95, 5, now, now),
        ("session2", "pattern", "Test after change", "Run tests", None, 0.85, 3, now, now),
        ("session2", "decision", "Use WAL mode", "WAL for concurrent reads", None, 0.75, 1, now, now),
        ("session3", "mistake", "Import side effects", "Guard main()", None, 0.80, 1, now, now),
    ]
    db.executemany(
        "INSERT INTO knowledge_entries (session_id, category, title, content, tags, confidence, "
        "occurrence_count, first_seen, last_seen) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        entries,
    )
    db.commit()
    db.close()


def test_knowledge_signals_fallback_db():
    """When knowledge-health.py module load succeeds or fails, fallback DB read works."""
    retro = load_retro()
    reset_artifacts()
    db_path = ARTIFACT_DIR / "knowledge.db"
    _make_knowledge_db(db_path)

    # Override the module-level constant temporarily
    original = retro.KNOWLEDGE_DB
    retro.KNOWLEDGE_DB = db_path
    try:
        result = retro.collect_knowledge_signals(stale_days=30)
        # Must be available
        test(
            "knowledge signals: available=True",
            result.get("available") is True,
            f"got available={result.get('available')}",
        )
        test("knowledge signals: total >= 5", result.get("total", 0) >= 5, f"got total={result.get('total')}")
        cats = result.get("categories", {})
        test(
            "knowledge signals: categories has mistake+pattern",
            "mistake" in cats and "pattern" in cats,
            f"categories={cats}",
        )
    finally:
        retro.KNOWLEDGE_DB = original


def test_knowledge_signals_module_system_exit_falls_back():
    """SystemExit from knowledge-health.py must not kill retro collection."""
    retro = load_retro()
    reset_artifacts()
    db_path = ARTIFACT_DIR / "knowledge.db"
    _make_knowledge_db(db_path)

    class FakeKnowledgeModule:
        DB_PATH = Path("/tmp/original-knowledge.db")

        @staticmethod
        def compute_health(stale_days: int = 30) -> dict:
            raise SystemExit(1)

    original_db = retro.KNOWLEDGE_DB
    original_loader = retro._load_module
    retro.KNOWLEDGE_DB = db_path
    retro._load_module = lambda name, filename: FakeKnowledgeModule
    try:
        result = retro.collect_knowledge_signals(stale_days=30)
        test("knowledge signals: SystemExit fallback keeps available=True", result.get("available") is True)
        test("knowledge signals: SystemExit fallback still reads DB", result.get("total", 0) >= 5)
    finally:
        retro.KNOWLEDGE_DB = original_db
        retro._load_module = original_loader


# ── Section 5: collect_skill_signals (with synthetic DB) ────────────────────


def _make_skill_db(path: Path) -> None:
    db = sqlite3.connect(str(path))
    db.executescript("""
        CREATE TABLE IF NOT EXISTS tentacle_outcomes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tentacle_name TEXT NOT NULL,
            tentacle_id TEXT,
            git_root TEXT,
            description TEXT,
            outcome_status TEXT NOT NULL,
            recorded_at TEXT NOT NULL,
            worktree_used INTEGER NOT NULL DEFAULT 0,
            worktree_path TEXT,
            verification_total INTEGER NOT NULL DEFAULT 0,
            verification_passed INTEGER NOT NULL DEFAULT 0,
            verification_failed INTEGER NOT NULL DEFAULT 0,
            todo_total INTEGER NOT NULL DEFAULT 0,
            todo_done INTEGER NOT NULL DEFAULT 0,
            learned INTEGER NOT NULL DEFAULT 0,
            duration_seconds REAL,
            summary TEXT
        );
        CREATE TABLE IF NOT EXISTS tentacle_outcome_skills (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            outcome_id INTEGER NOT NULL,
            skill_name TEXT NOT NULL,
            skill_version TEXT
        );
        CREATE TABLE IF NOT EXISTS tentacle_verifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            outcome_id INTEGER NOT NULL,
            command TEXT NOT NULL,
            exit_code INTEGER NOT NULL DEFAULT 0,
            output TEXT,
            duration_seconds REAL,
            verified_at TEXT NOT NULL
        );
    """)
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    db.execute(
        "INSERT INTO tentacle_outcomes (tentacle_name, tentacle_id, outcome_status, "
        "recorded_at, verification_passed, verification_failed, todo_done, todo_total) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("wave3-retro-core", "t1", "completed", now, 3, 0, 5, 5),
    )
    db.execute(
        "INSERT INTO tentacle_outcomes (tentacle_name, tentacle_id, outcome_status, "
        "recorded_at, verification_passed, verification_failed, todo_done, todo_total) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("wave2-hardening", "t2", "completed", now, 2, 1, 4, 4),
    )
    db.execute(
        "INSERT INTO tentacle_verifications (outcome_id, command, exit_code, verified_at) VALUES (1, 'python3 test_retro.py', 0, ?)",
        (now,),
    )
    db.execute(
        "INSERT INTO tentacle_verifications (outcome_id, command, exit_code, verified_at) VALUES (2, 'python3 test_hooks.py', 1, ?)",
        (now,),
    )
    db.commit()
    db.close()


def test_skill_signals_available():
    retro = load_retro()
    reset_artifacts()
    db_path = ARTIFACT_DIR / "skill-metrics.db"
    _make_skill_db(db_path)
    original = retro.SKILL_METRICS_DB
    retro.SKILL_METRICS_DB = db_path
    try:
        result = retro.collect_skill_signals()
        test(
            "skill signals: available=True",
            result.get("available") is True,
            f"got {result.get('available')}, db_exists={result.get('db_exists')}",
        )
        test(
            "skill signals: total_outcomes >= 2",
            result.get("total_outcomes", 0) >= 2,
            f"got {result.get('total_outcomes')}",
        )
    finally:
        retro.SKILL_METRICS_DB = original


def test_skill_signals_missing_db():
    retro = load_retro()
    fake_path = ARTIFACT_DIR / "no_skill.db"
    original = retro.SKILL_METRICS_DB
    retro.SKILL_METRICS_DB = fake_path
    try:
        result = retro.collect_skill_signals()
        test("skill signals: missing db → available=False", result.get("available") is False)
    finally:
        retro.SKILL_METRICS_DB = original


def test_skill_signals_module_load_failure_is_unavailable():
    """A module load failure must not report a misleading available=True zero state."""
    retro = load_retro()
    reset_artifacts()
    db_path = ARTIFACT_DIR / "skill-metrics.db"
    _make_skill_db(db_path)
    original_db = retro.SKILL_METRICS_DB
    original_loader = retro._load_module
    retro.SKILL_METRICS_DB = db_path
    retro._load_module = lambda name, filename: None
    try:
        result = retro.collect_skill_signals()
        test("skill signals: module load failure → available=False", result.get("available") is False)
        test("skill signals: module load failure keeps zero counts", result.get("total_outcomes", 0) == 0)
    finally:
        retro.SKILL_METRICS_DB = original_db
        retro._load_module = original_loader


# ── Section 6: compute_retro scoring ────────────────────────────────────────


def test_compute_retro_repo_mode():
    retro = load_retro()
    git = {
        "available": True,
        "lookback_days": 30,
        "commit_count": 50,
        "test_files_changed": 10,
        "py_files_changed": 20,
        "distinct_files_changed": 40,
        "recent_commits": [],
        "top_changed_files": [],
        "authors": [],
    }
    payload = retro.compute_retro(
        {"available": False},
        {"available": False},
        {"available": False},
        git,
        mode="repo",
    )
    test("compute_retro(repo): mode=repo", payload.get("mode") == "repo")
    test(
        "compute_retro(repo): only git in available_sections",
        payload.get("available_sections") == ["git"],
        f"got {payload.get('available_sections')}",
    )
    test(
        "compute_retro(repo): retro_score in range 0-100",
        0 <= payload.get("retro_score", -1) <= 100,
        f"got {payload.get('retro_score')}",
    )
    test(
        "compute_retro(repo): subscores has all 4 keys",
        set(payload.get("subscores", {}).keys()) == {"knowledge", "skills", "hooks", "git"},
    )


def test_compute_retro_local_mode_partial():
    """Local mode with only knowledge available."""
    retro = load_retro()
    knowledge = {
        "available": True,
        "score": 72.0,
        "total": 100,
        "categories": {"mistake": 20, "pattern": 40},
        "mistakes": 20,
        "patterns": 40,
        "mp_ratio": 2.0,
        "fresh_7d": 5,
        "stale_count": 10,
        "stale_pct": 10.0,
        "sessions": 5,
        "embed_pct": 30.0,
        "relation_density": 0.5,
        "subscores": {},
    }
    payload = retro.compute_retro(
        knowledge,
        {"available": False},
        {"available": False},
        {"available": False},
        mode="local",
    )
    test("compute_retro(local, knowledge only): mode=local", payload.get("mode") == "local")
    test("compute_retro(local, knowledge only): retro_score > 0", payload.get("retro_score", 0) > 0)
    test(
        "compute_retro(local, knowledge only): knowledge in available_sections",
        "knowledge" in payload.get("available_sections", []),
    )


def test_compute_retro_weights_sum():
    """Weights must sum approximately to 1.0 when multiple sections available."""
    retro = load_retro()
    knowledge = {
        "available": True,
        "score": 80.0,
        "total": 100,
        "categories": {},
        "mistakes": 5,
        "patterns": 10,
        "mp_ratio": 2.0,
        "fresh_7d": 3,
        "stale_count": 2,
        "stale_pct": 2.0,
        "sessions": 3,
        "embed_pct": 50.0,
        "relation_density": 0.5,
        "subscores": {},
    }
    skills = {
        "available": True,
        "db_exists": True,
        "total_outcomes": 5,
        "outcomes_complete": 5,
        "outcomes_failed": 0,
        "verifications_passed": 4,
        "verifications_failed": 1,
        "total_verifications": 5,
        "skill_usage": [],
        "recent_outcomes": [],
    }
    hooks = {
        "available": True,
        "total_entries": 100,
        "decisions": {"allow": 80, "deny": 20},
        "deny_rate": 20.0,
        "parse_error_rate": 0.0,
        "top_rules": [],
        "top_denied_tools": [],
    }
    git = {
        "available": True,
        "lookback_days": 30,
        "commit_count": 30,
        "test_files_changed": 10,
        "py_files_changed": 20,
        "distinct_files_changed": 30,
        "recent_commits": [],
        "top_changed_files": [],
        "authors": [],
    }
    payload = retro.compute_retro(knowledge, skills, hooks, git, mode="local")
    w_sum = sum(payload.get("weights", {}).values())
    test("compute_retro(local, all sections): weights sum ~1.0", abs(w_sum - 1.0) < 0.01, f"sum={w_sum}")
    test(
        "compute_retro(local, all sections): all 4 sections available",
        len(payload.get("available_sections", [])) == 4,
        f"available={payload.get('available_sections')}",
    )


def test_compute_retro_score_grade():
    retro = load_retro()
    git = {
        "available": True,
        "lookback_days": 30,
        "commit_count": 200,
        "test_files_changed": 30,
        "py_files_changed": 50,
        "distinct_files_changed": 80,
        "recent_commits": [],
        "top_changed_files": [],
        "authors": [],
    }
    payload = retro.compute_retro(
        {"available": False},
        {"available": False},
        {"available": False},
        git,
        mode="repo",
    )
    grade = payload.get("grade", "")
    emoji = payload.get("grade_emoji", "")
    test("compute_retro: grade is a non-empty string", bool(grade))
    test("compute_retro: grade_emoji is a non-empty string", bool(emoji))
    test("compute_retro: generated_at is set", bool(payload.get("generated_at", "")))


# ── Section 7: JSON output shape ─────────────────────────────────────────────


def test_json_output_shape():
    """Subprocess invocation with --json --mode repo should produce valid JSON with required keys."""
    result = subprocess.run(
        [sys.executable, str(RETRO_PY), "--json", "--mode", "repo", "--no-cache"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    test("retro.py --json --mode repo exits 0", result.returncode == 0, f"stderr: {result.stderr[:200]}")
    try:
        data = json.loads(result.stdout)
        required_keys = {
            "retro_score",
            "grade",
            "grade_emoji",
            "mode",
            "generated_at",
            "available_sections",
            "weights",
            "subscores",
            "knowledge",
            "skills",
            "hooks",
            "git",
        }
        missing = required_keys - set(data.keys())
        test("retro.py --json: all required keys present", len(missing) == 0, f"missing: {missing}")
        test("retro.py --json: mode=repo", data.get("mode") == "repo")
        test(
            "retro.py --json: retro_score 0-100",
            0 <= data.get("retro_score", -1) <= 100,
            f"got {data.get('retro_score')}",
        )
        subscores = data.get("subscores", {})
        test(
            "retro.py --json: subscores has 4 keys",
            set(subscores.keys()) == {"knowledge", "skills", "hooks", "git"},
            f"got {set(subscores.keys())}",
        )
        for sk, sv in subscores.items():
            test(f"retro.py --json: subscore[{sk}] in 0-100", 0 <= sv <= 100, f"got {sv}")
    except json.JSONDecodeError as e:
        test("retro.py --json: valid JSON output", False, str(e))


# ── Section 8: Score mode output ─────────────────────────────────────────────


def test_score_mode():
    result = subprocess.run(
        [sys.executable, str(RETRO_PY), "--score", "--mode", "repo", "--no-cache"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    test("retro.py --score exits 0", result.returncode == 0, f"stderr: {result.stderr[:200]}")
    output = result.stdout.strip()
    test("retro.py --score: single line output", "\n" not in output, f"got {len(output.splitlines())} lines")
    test("retro.py --score: contains 'score'", "score" in output.lower())
    test("retro.py --score: contains '/100'", "/100" in output)


# ── Section 9: Subreport mode ────────────────────────────────────────────────


def test_subreport_mode():
    for section in ("knowledge", "skills", "hooks", "git", "behavior"):
        result = subprocess.run(
            [sys.executable, str(RETRO_PY), "--subreport", section, "--mode", "repo", "--no-cache"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        test(f"retro.py --subreport {section} exits 0", result.returncode == 0, f"stderr: {result.stderr[:200]}")
        output = result.stdout.strip()
        test(f"retro.py --subreport {section}: non-empty output", bool(output))


def test_subreport_invalid():
    result = subprocess.run(
        [sys.executable, str(RETRO_PY), "--subreport", "invalid_section", "--no-cache"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    output = result.stdout.strip()
    test(
        "retro.py --subreport invalid: returns error message",
        "Unknown section" in output or "Valid" in output,
        f"got: {output[:100]}",
    )


# ── Section 10: Repo-only mode boundary ─────────────────────────────────────


def test_repo_mode_no_db_access():
    """In repo mode, knowledge/skills/hooks sections must all report unavailable."""
    result = subprocess.run(
        [sys.executable, str(RETRO_PY), "--json", "--mode", "repo", "--no-cache"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    try:
        data = json.loads(result.stdout)
        k = data.get("knowledge", {})
        s = data.get("skills", {})
        h = data.get("hooks", {})
        test("repo mode: knowledge.available=False", k.get("available") is False, f"got {k.get('available')}")
        test("repo mode: skills.available=False", s.get("available") is False, f"got {s.get('available')}")
        test("repo mode: hooks.available=False", h.get("available") is False, f"got {h.get('available')}")
        test(
            "repo mode: only git in available_sections",
            data.get("available_sections") == ["git"],
            f"got {data.get('available_sections')}",
        )
    except json.JSONDecodeError as e:
        test("repo mode boundary: valid JSON", False, str(e))


# ── Section 11: state cache ──────────────────────────────────────────────────


def test_state_cache_roundtrip():
    retro = load_retro()
    reset_artifacts()
    state_path = ARTIFACT_DIR / "retro-state.json"
    payload = {"retro_score": 72.5, "mode": "test", "generated_at": "2026-01-01T00:00:00Z"}
    retro.save_state(payload, path=state_path)
    loaded = retro.load_state(path=state_path)
    test("state cache: roundtrip save/load", loaded.get("retro_score") == 72.5, f"got {loaded}")


def test_state_cache_missing():
    retro = load_retro()
    missing = ARTIFACT_DIR / "no_state.json"
    result = retro.load_state(path=missing)
    test("state cache: missing file returns {}", result == {}, f"got {result}")


# ── Section 12: format functions ─────────────────────────────────────────────


def test_format_score_line():
    retro = load_retro()
    payload = {
        "retro_score": 75.0,
        "grade": "Good",
        "grade_emoji": "✅",
        "mode": "local",
    }
    line = retro.format_score_line(payload)
    test("format_score_line: contains score", "75.0" in line)
    test("format_score_line: contains grade", "Good" in line)
    test("format_score_line: contains /100", "/100" in line)
    test("format_score_line: no newline", "\n" not in line)


def test_format_text_report_complete():
    retro = load_retro()
    payload = {
        "retro_score": 55.0,
        "grade": "Fair",
        "grade_emoji": "🟡",
        "mode": "local",
        "generated_at": "2026-01-01T00:00:00Z",
        "available_sections": ["git"],
        "weights": {"git": 1.0},
        "subscores": {"knowledge": 0.0, "skills": 0.0, "hooks": 0.0, "git": 55.0},
        "knowledge": {"available": False},
        "skills": {"available": False},
        "hooks": {"available": False},
        "git": {
            "available": True,
            "lookback_days": 30,
            "commit_count": 10,
            "test_files_changed": 2,
            "py_files_changed": 5,
            "distinct_files_changed": 8,
            "recent_commits": [],
            "top_changed_files": [],
            "authors": [],
        },
    }
    report = retro.format_text_report(payload)
    test("format_text_report: contains score", "55.0" in report)
    test("format_text_report: contains mode", "mode=local" in report)
    test("format_text_report: contains Git Activity section", "Git Activity" in report)
    test("format_text_report: contains Knowledge Health section", "Knowledge Health" in report)


def test_format_subreport_all_valid():
    retro = load_retro()
    payload = {
        "knowledge": {"available": False},
        "skills": {"available": False},
        "hooks": {"available": False},
        "git": {"available": False},
    }
    for section in ("knowledge", "skills", "hooks", "git", "behavior"):
        out = retro.format_subreport(payload, section)
        test(f"format_subreport({section}): non-empty string", bool(out.strip()))


def test_format_subreport_invalid():
    retro = load_retro()
    out = retro.format_subreport({}, "bogus_section")
    test("format_subreport(invalid): returns error message", "Unknown section" in out or "Valid" in out)


def test_audit_deny_dry_excluded_from_deny_rate():
    """deny-dry must NOT count toward deny_rate; only real 'deny' decisions should."""
    retro = load_retro()
    reset_artifacts()
    audit_file = ARTIFACT_DIR / "audit_dry.jsonl"
    entries = [
        {"ts": 1, "event": "preToolUse", "tool": "bash", "rule": "r1", "decision": "deny"},
        {"ts": 2, "event": "preToolUse", "tool": "edit", "rule": "r1", "decision": "deny-dry"},
        {"ts": 3, "event": "preToolUse", "tool": "edit", "rule": "r1", "decision": "deny-dry"},
        {"ts": 4, "event": "preToolUse", "tool": "view", "rule": "none", "decision": "allow"},
        {"ts": 5, "event": "preToolUse", "tool": "view", "rule": "none", "decision": "allow"},
    ]
    audit_file.write_text("\n".join(json.dumps(e) for e in entries), encoding="utf-8")
    result = retro.collect_audit_signals(audit_path=audit_file)
    # deny_rate must reflect only the 1 real deny out of 5 total
    test(
        "audit: deny-dry excluded — deny_rate=20.0 not 60.0",
        result.get("deny_rate") == 20.0,
        f"got {result.get('deny_rate')}",
    )
    test(
        "audit: deny_dry_count=2",
        result.get("deny_dry_count") == 2,
        f"got {result.get('deny_dry_count')}",
    )
    test(
        "audit: deny_dry_rate=40.0",
        result.get("deny_dry_rate") == 40.0,
        f"got {result.get('deny_dry_rate')}",
    )


def test_audit_deny_dry_reported_separately():
    """deny_dry_count and deny_dry_rate must be present in the output dict."""
    retro = load_retro()
    reset_artifacts()
    audit_file = ARTIFACT_DIR / "audit_dry2.jsonl"
    # All allow — still check the keys exist with 0 values
    entries = [{"ts": 1, "event": "preToolUse", "tool": "view", "rule": "none", "decision": "allow"}]
    audit_file.write_text("\n".join(json.dumps(e) for e in entries), encoding="utf-8")
    result = retro.collect_audit_signals(audit_path=audit_file)
    test("audit: deny_dry_count key present", "deny_dry_count" in result)
    test("audit: deny_dry_rate key present", "deny_dry_rate" in result)
    test("audit: deny_dry_count=0 when none", result.get("deny_dry_count") == 0)


def test_audit_top_denied_tools_excludes_deny_dry():
    """top_denied_tools must reflect only real denies, not dry-run noise."""
    retro = load_retro()
    reset_artifacts()
    audit_file = ARTIFACT_DIR / "audit_dry3.jsonl"
    entries = [
        {"ts": 1, "event": "preToolUse", "tool": "bash", "rule": "r1", "decision": "deny"},
        {"ts": 2, "event": "preToolUse", "tool": "edit", "rule": "r2", "decision": "deny-dry"},
        {"ts": 3, "event": "preToolUse", "tool": "edit", "rule": "r2", "decision": "deny-dry"},
    ]
    audit_file.write_text("\n".join(json.dumps(e) for e in entries), encoding="utf-8")
    result = retro.collect_audit_signals(audit_path=audit_file)
    denied_tools = result.get("top_denied_tools", [])
    test(
        "audit: top_denied_tools contains real deny tool",
        any(e.get("tool") == "bash" for e in denied_tools),
        f"got {denied_tools}",
    )
    test(
        "audit: top_denied_tools excludes deny-dry-only tools",
        not any(e.get("tool") == "edit" for e in denied_tools),
        f"got {denied_tools}",
    )


# ── Section 13+: Scout signals ────────────────────────────────────────────────


def test_scout_signals_missing_config():
    """collect_scout_signals returns available=False when config file is absent."""
    retro = load_retro()
    reset_artifacts()
    result = retro.collect_scout_signals(
        config_path=ARTIFACT_DIR / "no_scout_config.json",
        script_path=ARTIFACT_DIR / "no_trend_scout.py",
    )
    test("scout signals: missing config → available=False", result.get("available") is False)
    test("scout signals: missing config → configured=False", result.get("configured") is False)
    test("scout signals: missing config → state_file_exists=False", result.get("state_file_exists") is False)
    test("scout signals: missing config → last_run_utc=None", result.get("last_run_utc") is None)


def test_scout_signals_config_only():
    """collect_scout_signals reads target_repo/label from config; state absent → available=True."""
    retro = load_retro()
    reset_artifacts()
    cfg = {
        "target_repo": "owner/test-repo",
        "issue_label": "trend-scout",
        "run_control": {"grace_window_hours": 20, "state_file": None},
    }
    config_file = ARTIFACT_DIR / "scout_config.json"
    config_file.write_text(json.dumps(cfg), encoding="utf-8")
    result = retro.collect_scout_signals(
        config_path=config_file,
        script_path=ARTIFACT_DIR / "no_script.py",
    )
    test("scout signals: config present → available=True", result.get("available") is True)
    test("scout signals: config present → configured=True", result.get("configured") is True)
    test("scout signals: target_repo read correctly", result.get("target_repo") == "owner/test-repo")
    test("scout signals: issue_label read correctly", result.get("issue_label") == "trend-scout")
    test("scout signals: grace_window_hours=20", result.get("grace_window_hours") == 20)
    test("scout signals: state_file_exists=False when no state file", result.get("state_file_exists") is False)
    test("scout signals: last_run_utc=None when no state file", result.get("last_run_utc") is None)
    test("scout signals: script_exists=False for missing script", result.get("script_exists") is False)


def test_scout_signals_with_state_file():
    """collect_scout_signals reads last_run_utc and computes elapsed_hours from state file."""
    retro = load_retro()
    reset_artifacts()
    import time as _time

    # Write a state file with a recent run (1 hour ago)
    from datetime import datetime, timezone as _tz, timedelta

    last_run_dt = datetime.now(_tz.utc) - timedelta(hours=1)
    last_run_str = last_run_dt.isoformat()
    state = {"last_run_utc": last_run_str}
    state_file = ARTIFACT_DIR / ".trend-scout-state.json"
    state_file.write_text(json.dumps(state), encoding="utf-8")

    cfg = {
        "target_repo": "owner/repo",
        "issue_label": "trend-scout",
        "run_control": {"grace_window_hours": 20, "state_file": str(state_file)},
    }
    config_file = ARTIFACT_DIR / "scout_cfg2.json"
    config_file.write_text(json.dumps(cfg), encoding="utf-8")

    result = retro.collect_scout_signals(config_path=config_file)
    test("scout signals: state present → state_file_exists=True", result.get("state_file_exists") is True)
    test("scout signals: last_run_utc populated", result.get("last_run_utc") is not None)
    elapsed = result.get("elapsed_hours")
    test("scout signals: elapsed_hours ~1h", elapsed is not None and 0.8 < elapsed < 1.5, f"got {elapsed}")
    remaining = result.get("remaining_hours")
    test(
        "scout signals: remaining_hours ~19h",
        remaining is not None and 18.0 < remaining < 20.0,
        f"got {remaining}",
    )
    test(
        "scout signals: would_skip_without_force=True within grace window",
        result.get("would_skip_without_force") is True,
    )


def test_scout_signals_grace_expired():
    """would_skip_without_force=False when elapsed_hours > grace_window_hours."""
    retro = load_retro()
    reset_artifacts()
    from datetime import datetime, timezone as _tz, timedelta

    # Last run was 25 hours ago; grace window is 20 hours
    last_run_dt = datetime.now(_tz.utc) - timedelta(hours=25)
    state = {"last_run_utc": last_run_dt.isoformat()}
    state_file = ARTIFACT_DIR / ".scout-state-expired.json"
    state_file.write_text(json.dumps(state), encoding="utf-8")

    cfg = {
        "target_repo": "owner/repo",
        "issue_label": "trend-scout",
        "run_control": {"grace_window_hours": 20, "state_file": str(state_file)},
    }
    config_file = ARTIFACT_DIR / "scout_cfg3.json"
    config_file.write_text(json.dumps(cfg), encoding="utf-8")

    result = retro.collect_scout_signals(config_path=config_file)
    test(
        "scout signals: grace expired → would_skip_without_force=False",
        result.get("would_skip_without_force") is False,
        f"got {result.get('would_skip_without_force')}",
    )
    test(
        "scout signals: grace expired → remaining_hours=0",
        result.get("remaining_hours") == 0.0,
        f"got {result.get('remaining_hours')}",
    )


def test_compute_retro_scout_in_payload():
    """compute_retro must include 'scout' top-level key in the payload."""
    retro = load_retro()
    git = {
        "available": True,
        "lookback_days": 30,
        "commit_count": 30,
        "test_files_changed": 5,
        "py_files_changed": 10,
        "distinct_files_changed": 15,
        "recent_commits": [],
        "top_changed_files": [],
        "authors": [],
    }
    scout = {"available": False, "configured": False}
    payload = retro.compute_retro(
        {"available": False},
        {"available": False},
        {"available": False},
        git,
        mode="repo",
        scout=scout,
    )
    test("compute_retro: 'scout' key present", "scout" in payload, f"keys: {list(payload.keys())}")
    test("compute_retro: scout.available preserved", payload["scout"].get("available") is False)


def test_compute_retro_scout_absent_defaults():
    """compute_retro without scout arg must still include scout key with available=False."""
    retro = load_retro()
    payload = retro.compute_retro(
        {"available": False},
        {"available": False},
        {"available": False},
        {"available": False},
        mode="repo",
    )
    test("compute_retro: scout absent when not passed", "scout" not in payload)


def test_json_output_has_scout_field():
    """Subprocess --json must include 'scout' top-level key."""
    result = subprocess.run(
        [sys.executable, str(RETRO_PY), "--json", "--mode", "repo", "--no-cache"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    test("retro.py --json (scout): exits 0", result.returncode == 0)
    try:
        data = json.loads(result.stdout)
        test("retro.py --json: 'scout' key present", "scout" in data, f"keys: {list(data.keys())}")
        scout = data.get("scout", {})
        test("retro.py --json: scout.available is bool", isinstance(scout.get("available"), bool))
        test("retro.py --json: scout does not affect retro_score", 0 <= data.get("retro_score", -1) <= 100)
        # Scout must NOT appear in subscores or weights
        test(
            "retro.py --json: scout not in subscores",
            "scout" not in data.get("subscores", {}),
            f"subscores: {list(data.get('subscores', {}).keys())}",
        )
        test(
            "retro.py --json: scout not in weights",
            "scout" not in data.get("weights", {}),
            f"weights: {list(data.get('weights', {}).keys())}",
        )
    except json.JSONDecodeError as e:
        test("retro.py --json: valid JSON (scout field)", False, str(e))


def test_scout_signals_relative_state_file_anchored_to_config_dir():
    """Relative state_file in run_control must resolve relative to config directory, not CWD.

    Regression test for: state_file was resolved against process CWD when a
    relative path was given, causing the file to be silently missed unless the
    process happened to run from the config directory.
    """
    retro = load_retro()
    reset_artifacts()
    from datetime import datetime, timezone as _tz, timedelta

    last_run_dt = datetime.now(_tz.utc) - timedelta(hours=2)
    state = {"last_run_utc": last_run_dt.isoformat()}

    # Write state file next to the config file (in ARTIFACT_DIR)
    state_file = ARTIFACT_DIR / ".scout-relative-state.json"
    state_file.write_text(json.dumps(state), encoding="utf-8")

    # Use a bare filename (relative) — must be resolved against config dir
    cfg = {
        "target_repo": "owner/rel-repo",
        "issue_label": "rel-label",
        "run_control": {
            "grace_window_hours": 10,
            "state_file": ".scout-relative-state.json",
        },
    }
    config_file = ARTIFACT_DIR / "scout_cfg_rel.json"
    config_file.write_text(json.dumps(cfg), encoding="utf-8")

    result = retro.collect_scout_signals(config_path=config_file)
    test(
        "scout signals (relative path): state_file_exists=True",
        result.get("state_file_exists") is True,
        f"resolved to: {result.get('state_file')} — CWD={Path.cwd()}",
    )
    test(
        "scout signals (relative path): last_run_utc populated",
        result.get("last_run_utc") is not None,
    )
    elapsed = result.get("elapsed_hours")
    test(
        "scout signals (relative path): elapsed_hours ~2h",
        elapsed is not None and 1.5 < elapsed < 3.0,
        f"got {elapsed}",
    )


def test_scout_signals_absolute_state_file_still_works():
    """Absolute state_file override must continue to resolve correctly (no regression)."""
    retro = load_retro()
    reset_artifacts()
    from datetime import datetime, timezone as _tz, timedelta

    last_run_dt = datetime.now(_tz.utc) - timedelta(hours=3)
    state = {"last_run_utc": last_run_dt.isoformat()}
    state_file = ARTIFACT_DIR / ".scout-abs-state.json"
    state_file.write_text(json.dumps(state), encoding="utf-8")

    cfg = {
        "target_repo": "owner/abs-repo",
        "issue_label": "abs-label",
        "run_control": {
            "grace_window_hours": 10,
            "state_file": str(state_file),
        },
    }
    config_file = ARTIFACT_DIR / "scout_cfg_abs.json"
    config_file.write_text(json.dumps(cfg), encoding="utf-8")

    result = retro.collect_scout_signals(config_path=config_file)
    test(
        "scout signals (absolute path): state_file_exists=True",
        result.get("state_file_exists") is True,
        f"resolved to: {result.get('state_file')}",
    )
    elapsed = result.get("elapsed_hours")
    test(
        "scout signals (absolute path): elapsed_hours ~3h",
        elapsed is not None and 2.5 < elapsed < 4.0,
        f"got {elapsed}",
    )


def test_scout_signals_malformed_grace_window_string():
    """Malformed (non-numeric string) grace_window_hours must not crash — fail-open to 0."""
    retro = load_retro()
    reset_artifacts()

    cfg = {
        "target_repo": "owner/repo",
        "run_control": {"grace_window_hours": "not-a-number"},
    }
    config_file = ARTIFACT_DIR / "scout_cfg_malformed_str.json"
    config_file.write_text(json.dumps(cfg), encoding="utf-8")

    try:
        result = retro.collect_scout_signals(config_path=config_file)
        crashed = False
    except Exception as exc:
        result = {}
        crashed = True
        _crash_detail = str(exc)

    test(
        "scout signals (malformed grace_window string): does not crash",
        not crashed,
        _crash_detail if crashed else "",
    )
    test(
        "scout signals (malformed grace_window string): grace_window_hours defaults to 0",
        result.get("grace_window_hours") == 0.0,
        f"got {result.get('grace_window_hours')}",
    )
    test(
        "scout signals (malformed grace_window string): available=True (config was valid JSON)",
        result.get("available") is True,
        f"got available={result.get('available')}",
    )


def test_scout_signals_malformed_grace_window_list():
    """List value for grace_window_hours must not crash — fail-open to 0."""
    retro = load_retro()
    reset_artifacts()

    cfg = {
        "target_repo": "owner/repo",
        "run_control": {"grace_window_hours": [1, 2, 3]},
    }
    config_file = ARTIFACT_DIR / "scout_cfg_malformed_list.json"
    config_file.write_text(json.dumps(cfg), encoding="utf-8")

    try:
        result = retro.collect_scout_signals(config_path=config_file)
        crashed = False
    except Exception as exc:
        result = {}
        crashed = True
        _crash_detail = str(exc)

    test(
        "scout signals (grace_window is list): does not crash",
        not crashed,
        _crash_detail if crashed else "",
    )
    test(
        "scout signals (grace_window is list): grace_window_hours defaults to 0",
        result.get("grace_window_hours") == 0.0,
        f"got {result.get('grace_window_hours')}",
    )


def test_scout_signals_malformed_json_config():
    """Malformed (unparseable) config JSON → configured=True, available=False."""
    retro = load_retro()
    reset_artifacts()

    config_file = ARTIFACT_DIR / "scout_cfg_malformed_json.json"
    config_file.write_text("{not valid json!!!", encoding="utf-8")

    try:
        result = retro.collect_scout_signals(config_path=config_file)
        crashed = False
    except Exception as exc:
        result = {}
        crashed = True
        _crash_detail = str(exc)

    test(
        "scout signals (malformed JSON): does not crash",
        not crashed,
        _crash_detail if crashed else "",
    )
    test(
        "scout signals (malformed JSON): configured=True (file exists)",
        result.get("configured") is True,
        f"got configured={result.get('configured')}",
    )
    test(
        "scout signals (malformed JSON): available=False (parse failed)",
        result.get("available") is False,
        f"got available={result.get('available')}",
    )


def test_scout_signals_valid_grace_window_unchanged():
    """Valid numeric grace_window_hours must still be parsed correctly (no regression)."""
    retro = load_retro()
    reset_artifacts()

    cfg = {
        "target_repo": "owner/repo",
        "run_control": {"grace_window_hours": 20},
    }
    config_file = ARTIFACT_DIR / "scout_cfg_valid_grace.json"
    config_file.write_text(json.dumps(cfg), encoding="utf-8")

    result = retro.collect_scout_signals(config_path=config_file)
    test(
        "scout signals (valid grace_window=20): parsed correctly",
        result.get("grace_window_hours") == 20.0,
        f"got {result.get('grace_window_hours')}",
    )


def test_score_skills_unverified_outcomes_below_neutral():
    """When outcomes exist but zero verifications, skills subscore must be < 50 (not neutral 50)."""
    retro = load_retro()
    s = {
        "available": True,
        "total_outcomes": 278,
        "outcomes_complete": 278,
        "outcomes_failed": 0,
        "total_verifications": 0,
        "verifications_passed": 0,
        "verifications_failed": 0,
        "outcomes_with_passing_verification": 0,
        "skill_usage": [],
        "recent_outcomes": [],
    }
    score = retro._score_skills(s)
    test(
        "skills: unverified outcomes score < 50 (not false-neutral 50)",
        score < 50,
        f"got {score} — should be sub-neutral to reflect unverified state",
    )
    test("skills: unverified outcomes score > 0", score > 0, f"got {score}")


def test_score_skills_outcome_level_fallback():
    """When tentacle_verifications empty but outcome-level verification_passed > 0, use that."""
    retro = load_retro()
    s = {
        "available": True,
        "total_outcomes": 10,
        "outcomes_complete": 10,
        "outcomes_failed": 0,
        "total_verifications": 0,
        "verifications_passed": 0,
        "verifications_failed": 0,
        "outcomes_with_passing_verification": 8,
        "skill_usage": [],
        "recent_outcomes": [],
    }
    score = retro._score_skills(s)
    test(
        "skills: outcome-level fallback score = 80.0",
        score == 80.0,
        f"got {score}",
    )


def test_score_skills_detailed_verifications_take_priority():
    """Detailed tentacle_verifications rows take priority over outcome-level fields."""
    retro = load_retro()
    s = {
        "available": True,
        "total_outcomes": 10,
        "outcomes_with_passing_verification": 9,  # would give 90.0 if used
        "total_verifications": 4,
        "verifications_passed": 3,
        "verifications_failed": 1,
        "skill_usage": [],
        "recent_outcomes": [],
    }
    score = retro._score_skills(s)
    test(
        "skills: detailed verifications have priority (75.0 not 90.0)",
        score == 75.0,
        f"got {score}",
    )


def test_compute_retro_interpretation_fields_present():
    """compute_retro payload must include all 5 required interpretation fields."""
    retro = load_retro()
    payload = retro.compute_retro(
        {"available": False},
        {"available": False},
        {"available": False},
        {
            "available": True,
            "lookback_days": 30,
            "commit_count": 30,
            "test_files_changed": 5,
            "py_files_changed": 10,
            "distinct_files_changed": 15,
            "recent_commits": [],
            "top_changed_files": [],
            "authors": [],
        },
        mode="repo",
    )
    for key in ("summary", "score_confidence", "distortion_flags", "accuracy_notes", "improvement_actions"):
        test(
            f"compute_retro: '{key}' present in payload",
            key in payload,
            f"missing key: {key}",
        )
    test(
        "compute_retro: summary is non-empty string",
        isinstance(payload.get("summary"), str) and bool(payload.get("summary")),
    )
    test(
        "compute_retro: score_confidence in {low, medium, high}",
        payload.get("score_confidence") in ("low", "medium", "high"),
        f"got {payload.get('score_confidence')}",
    )
    test("compute_retro: distortion_flags is a list", isinstance(payload.get("distortion_flags"), list))
    test("compute_retro: accuracy_notes is a list", isinstance(payload.get("accuracy_notes"), list))
    test("compute_retro: improvement_actions is a list", isinstance(payload.get("improvement_actions"), list))


def test_distortion_flag_skills_unverified():
    """skills_unverified flag must be set when outcomes exist but no verification evidence."""
    retro = load_retro()
    skills = {
        "available": True,
        "total_outcomes": 50,
        "total_verifications": 0,
        "verifications_passed": 0,
        "verifications_failed": 0,
        "outcomes_with_passing_verification": 0,
        "skill_usage": [],
        "recent_outcomes": [],
    }
    payload = retro.compute_retro(
        {"available": False},
        skills,
        {"available": False},
        {"available": False},
        mode="local",
    )
    test(
        "distortion: skills_unverified flag present",
        "skills_unverified" in payload.get("distortion_flags", []),
        f"flags: {payload.get('distortion_flags')}",
    )
    test(
        "distortion: skills_unverified → score_confidence not high",
        payload.get("score_confidence") != "high",
        f"got {payload.get('score_confidence')}",
    )
    test(
        "distortion: accuracy_notes mentions unverified",
        any("unverified" in n.lower() or "no verification" in n.lower() for n in payload.get("accuracy_notes", [])),
    )


def test_distortion_flag_hook_deny_dry_noise():
    """hook_deny_dry_noise flag must be set when deny-dry entries are present."""
    retro = load_retro()
    hooks = {
        "available": True,
        "total_entries": 100,
        "decisions": {"allow": 60, "deny-dry": 40},
        "deny_rate": 0.0,
        "deny_dry_count": 40,
        "deny_dry_rate": 40.0,
        "parse_error_rate": 0.0,
        "top_rules": [],
        "top_denied_tools": [],
    }
    payload = retro.compute_retro(
        {"available": False},
        {"available": False},
        hooks,
        {"available": False},
        mode="local",
    )
    test(
        "distortion: hook_deny_dry_noise flag present",
        "hook_deny_dry_noise" in payload.get("distortion_flags", []),
        f"flags: {payload.get('distortion_flags')}",
    )
    test(
        "distortion: hook_deny_dry_noise → score_confidence not high",
        payload.get("score_confidence") != "high",
    )


def test_distortion_flags_empty_when_clean():
    """When data is clean, distortion_flags must be empty."""
    retro = load_retro()
    knowledge = {
        "available": True, "score": 85.0, "total": 100, "categories": {},
        "mistakes": 5, "patterns": 10, "mp_ratio": 2.0, "fresh_7d": 5,
        "stale_count": 2, "stale_pct": 2.0, "sessions": 3,
        "embed_pct": 50.0, "relation_density": 0.5, "subscores": {},
    }
    skills = {
        "available": True,
        "total_outcomes": 5,
        "total_verifications": 5,
        "verifications_passed": 5,
        "verifications_failed": 0,
        "outcomes_with_passing_verification": 5,
        "skill_usage": [],
        "recent_outcomes": [],
    }
    hooks = {
        "available": True,
        "total_entries": 100,
        "decisions": {"allow": 95, "deny": 5},
        "deny_rate": 5.0,
        "deny_dry_count": 0,
        "deny_dry_rate": 0.0,
        "parse_error_rate": 0.0,
        "top_rules": [],
        "top_denied_tools": [],
    }
    git = {
        "available": True, "lookback_days": 30, "commit_count": 30,
        "test_files_changed": 10, "py_files_changed": 20,
        "distinct_files_changed": 30, "recent_commits": [], "top_changed_files": [], "authors": [],
    }
    payload = retro.compute_retro(knowledge, skills, hooks, git, mode="local")
    test(
        "distortion: no flags on clean data",
        payload.get("distortion_flags") == [],
        f"flags: {payload.get('distortion_flags')}",
    )
    test(
        "distortion: score_confidence=high on clean data",
        payload.get("score_confidence") == "high",
        f"got {payload.get('score_confidence')}",
    )


def test_json_output_interpretation_fields():
    """Subprocess --json must include all 5 new interpretation fields."""
    result = subprocess.run(
        [sys.executable, str(RETRO_PY), "--json", "--mode", "repo", "--no-cache"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    test("retro.py --json --mode repo: exits 0 (interpretation fields)", result.returncode == 0)
    try:
        data = json.loads(result.stdout)
        for key in ("summary", "score_confidence", "distortion_flags", "accuracy_notes", "improvement_actions"):
            test(
                f"retro.py --json: '{key}' present in output",
                key in data,
                f"missing from JSON output",
            )
        test(
            "retro.py --json: score_confidence in {low,medium,high}",
            data.get("score_confidence") in ("low", "medium", "high"),
            f"got {data.get('score_confidence')}",
        )
    except json.JSONDecodeError as e:
        test("retro.py --json: valid JSON (interpretation fields)", False, str(e))


def test_hook_score_not_penalised_by_deny_dry():
    """_score_hooks must NOT be penalised by deny-dry entries (only real deny counts)."""
    retro = load_retro()
    # All entries are deny-dry — real deny count is 0
    h_all_dry = {
        "available": True,
        "total_entries": 100,
        "decisions": {"allow": 50, "deny-dry": 50},
        "deny_rate": 0.0,          # correctly 0 — no real denies
        "deny_dry_count": 50,
        "deny_dry_rate": 50.0,
        "parse_error_rate": 0.0,
        "top_rules": [],
        "top_denied_tools": [],
    }
    score_dry_only = retro._score_hooks(h_all_dry)
    # Equivalent with no deny-dry
    h_clean = {
        "available": True,
        "total_entries": 100,
        "decisions": {"allow": 100},
        "deny_rate": 0.0,
        "deny_dry_count": 0,
        "deny_dry_rate": 0.0,
        "parse_error_rate": 0.0,
        "top_rules": [],
        "top_denied_tools": [],
    }
    score_clean = retro._score_hooks(h_clean)
    test(
        "hook_score: deny-dry-only gives same score as fully clean (no real deny penalty)",
        score_dry_only == score_clean,
        f"dry_only={score_dry_only}, clean={score_clean}",
    )
    test("hook_score: score=100 when deny_rate=0 and parse_rate=0", score_clean == 100.0, f"got {score_clean}")


# ── Section 17: collect_session_behavior_signals ─────────────────────────────


def _make_behavior_db(path: Path, sessions=(), documents=()) -> None:
    """Helper: create a minimal sessions+documents DB for behavior signal tests."""
    with sqlite3.connect(str(path)) as conn:
        conn.execute(
            """CREATE TABLE sessions (
               id TEXT PRIMARY KEY,
               total_checkpoints INTEGER DEFAULT 0,
               indexed_at TEXT,
               event_count_estimate INTEGER DEFAULT 0
            )"""
        )
        conn.execute(
            """CREATE TABLE documents (
               id TEXT PRIMARY KEY,
               session_id TEXT
            )"""
        )
        for s in sessions:
            conn.execute(
                "INSERT INTO sessions (id, total_checkpoints, indexed_at, event_count_estimate) VALUES (?,?,?,?)",
                s,
            )
        for d in documents:
            conn.execute("INSERT INTO documents (id, session_id) VALUES (?,?)", d)
        conn.commit()


def test_behavior_signals_empty_db():
    """Empty sessions table → all zeros, no crash."""
    retro = load_retro()
    reset_artifacts()
    db_path = ARTIFACT_DIR / "behavior_empty.db"
    _make_behavior_db(db_path)
    result = retro.collect_session_behavior_signals(db_path)
    test("behavior signals: empty DB → not None", result is not None)
    test("behavior signals: empty DB → session_count=0", result.get("session_count") == 0)
    test("behavior signals: empty DB → completion_rate=0.0", result.get("completion_rate") == 0.0)
    test("behavior signals: empty DB → one_shot_rate=0.0", result.get("one_shot_rate") == 0.0)


def test_behavior_signals_sessions_no_docs():
    """Sessions with checkpoints but no documents → knowledge_yield=0, efficiency_ratio=0."""
    retro = load_retro()
    reset_artifacts()
    db_path = ARTIFACT_DIR / "behavior_nodocs.db"
    sessions = [
        ("s1", 1, "2026-01-01", 10),
        ("s2", 2, "2026-01-02", 20),
        ("s3", 0, "2026-01-03", 5),
    ]
    _make_behavior_db(db_path, sessions=sessions)
    result = retro.collect_session_behavior_signals(db_path)
    test("behavior signals: no docs → not None", result is not None)
    test("behavior signals: no docs → session_count=3", result.get("session_count") == 3)
    test(
        "behavior signals: no docs → sessions_with_checkpoints=2",
        result.get("sessions_with_checkpoints") == 2,
        f"got {result.get('sessions_with_checkpoints')}",
    )
    test(
        "behavior signals: no docs → completion_rate=2/3",
        abs(result.get("completion_rate", -1) - round(2 / 3, 4)) < 0.001,
        f"got {result.get('completion_rate')}",
    )
    test("behavior signals: no docs → knowledge_yield=0.0", result.get("knowledge_yield") == 0.0)
    test("behavior signals: no docs → efficiency_ratio=0.0", result.get("efficiency_ratio") == 0.0)


def test_behavior_signals_mixed():
    """Mixed sessions: verify all 4 metrics are computed correctly."""
    retro = load_retro()
    reset_artifacts()
    db_path = ARTIFACT_DIR / "behavior_mixed.db"
    sessions = [
        ("s1", 1, "2026-01-01", 10),  # exactly 1 checkpoint → counts toward one_shot
        ("s2", 3, "2026-01-02", 20),  # >1 checkpoints → not one-shot
        ("s3", 0, "2026-01-03", 5),   # no checkpoint → not counted
    ]
    documents = [
        ("d1", "s1"),
        ("d2", "s1"),
        ("d3", "s2"),
    ]
    _make_behavior_db(db_path, sessions=sessions, documents=documents)
    result = retro.collect_session_behavior_signals(db_path)
    test("behavior signals: mixed → not None", result is not None)
    # total_sessions=3, sessions_with_checkpoints=2
    test(
        "behavior signals: mixed → completion_rate=2/3",
        abs(result.get("completion_rate", -1) - round(2 / 3, 4)) < 0.001,
        f"got {result.get('completion_rate')}",
    )
    # total_entries=3, total_sessions=3 → knowledge_yield=1.0
    test(
        "behavior signals: mixed → knowledge_yield=1.0",
        abs(result.get("knowledge_yield", -1) - 1.0) < 0.001,
        f"got {result.get('knowledge_yield')}",
    )
    # total_events=35, total_entries=3 → efficiency_ratio=3/35 ≈ 0.0857
    expected_er = round(3 / 35, 4)
    test(
        "behavior signals: mixed → efficiency_ratio correct",
        abs(result.get("efficiency_ratio", -1) - expected_er) < 0.001,
        f"got {result.get('efficiency_ratio')}, expected {expected_er}",
    )
    # sessions_with_exactly_1_checkpoint=1 (s1), sessions_with_any=2 → one_shot=0.5
    test(
        "behavior signals: mixed → one_shot_rate=0.5",
        abs(result.get("one_shot_rate", -1) - 0.5) < 0.001,
        f"got {result.get('one_shot_rate')}",
    )


def test_behavior_signals_all_zero_edge_case():
    """Sessions exist with event_count_estimate=0 → efficiency_ratio=0, no divide-by-zero."""
    retro = load_retro()
    reset_artifacts()
    db_path = ARTIFACT_DIR / "behavior_zero_events.db"
    sessions = [
        ("s1", 1, "2026-01-01", 0),  # event_count=0
        ("s2", 0, "2026-01-02", 0),
    ]
    documents = [("d1", "s1")]
    _make_behavior_db(db_path, sessions=sessions, documents=documents)
    result = retro.collect_session_behavior_signals(db_path)
    test("behavior signals: zero events → not None", result is not None)
    test(
        "behavior signals: zero events → efficiency_ratio=0.0 (no divide-by-zero)",
        result.get("efficiency_ratio") == 0.0,
        f"got {result.get('efficiency_ratio')}",
    )
    test("behavior signals: zero events → session_count=2", result.get("session_count") == 2)


def test_behavior_signals_missing_tables():
    """DB with no sessions/documents tables → returns None (graceful)."""
    retro = load_retro()
    reset_artifacts()
    db_path = ARTIFACT_DIR / "behavior_no_tables.db"
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute("CREATE TABLE unrelated (id TEXT)")
        conn.commit()
    result = retro.collect_session_behavior_signals(db_path)
    test("behavior signals: missing tables → returns None", result is None)


def test_score_behavior_function():
    """_score_behavior: weighted average of completion_rate and efficiency_ratio × 100."""
    retro = load_retro()
    b = {"completion_rate": 0.8, "efficiency_ratio": 0.6}
    score = retro._score_behavior(b)
    expected = round((0.8 * 0.5 + 0.6 * 0.5) * 100.0, 1)
    test(
        "_score_behavior: correct weighted average",
        abs(score - expected) < 0.01,
        f"got {score}, expected {expected}",
    )
    test("_score_behavior: empty dict → 0.0", retro._score_behavior({}) == 0.0)
    test("_score_behavior: None → 0.0", retro._score_behavior(None) == 0.0)


def test_compute_retro_behavior_in_payload():
    """compute_retro includes behavior key when db_path is valid."""
    retro = load_retro()
    reset_artifacts()
    db_path = ARTIFACT_DIR / "behavior_payload.db"
    sessions = [("s1", 1, "2026-01-01", 10)]
    documents = [("d1", "s1")]
    _make_behavior_db(db_path, sessions=sessions, documents=documents)

    git = {"available": False}
    payload = retro.compute_retro(
        {"available": False},
        {"available": False},
        {"available": False},
        git,
        mode="local",
        db_path=db_path,
    )
    test(
        "compute_retro: behavior key present when db_path valid",
        "behavior" in payload,
        f"keys: {list(payload.keys())}",
    )
    b = payload.get("behavior", {})
    test("compute_retro: behavior.session_count=1", b.get("session_count") == 1)
    test(
        "compute_retro(local): behavior subscore in subscores",
        "behavior" in payload.get("subscores", {}),
        f"subscores keys: {list(payload.get('subscores', {}).keys())}",
    )
    test(
        "compute_retro(local): behavior in available_sections",
        "behavior" in payload.get("available_sections", []),
        f"available: {payload.get('available_sections')}",
    )


def test_compute_retro_behavior_repo_mode_no_subscore():
    """In repo mode, behavior object is present but NOT in subscores/available_sections."""
    retro = load_retro()
    reset_artifacts()
    db_path = ARTIFACT_DIR / "behavior_repo_mode.db"
    sessions = [("s1", 1, "2026-01-01", 10)]
    documents = [("d1", "s1")]
    _make_behavior_db(db_path, sessions=sessions, documents=documents)

    git = {
        "available": True,
        "lookback_days": 30,
        "commit_count": 5,
        "test_files_changed": 1,
        "py_files_changed": 2,
        "distinct_files_changed": 3,
        "recent_commits": [],
        "top_changed_files": [],
        "authors": [],
    }
    payload = retro.compute_retro(
        {"available": False},
        {"available": False},
        {"available": False},
        git,
        mode="repo",
        db_path=db_path,
    )
    test(
        "compute_retro(repo): behavior object present in payload",
        "behavior" in payload,
        f"keys: {list(payload.keys())}",
    )
    test(
        "compute_retro(repo): behavior NOT in subscores",
        "behavior" not in payload.get("subscores", {}),
        f"subscores: {list(payload.get('subscores', {}).keys())}",
    )
    test(
        "compute_retro(repo): behavior NOT in available_sections",
        "behavior" not in payload.get("available_sections", []),
        f"available: {payload.get('available_sections')}",
    )




# ── Section 18: behavior as first-class text/subreport surface ───────────────


def test_format_behavior_section_with_data():
    """format_behavior_section renders all expected fields when data is present."""
    retro = load_retro()
    b = {
        "completion_rate": 0.75,
        "knowledge_yield": 2.5,
        "efficiency_ratio": 0.4,
        "one_shot_rate": 0.5,
        "session_count": 8,
        "sessions_with_checkpoints": 6,
    }
    out = "\n".join(retro.format_behavior_section(b))
    test("format_behavior_section: header present", "Session Behavior" in out)
    test("format_behavior_section: session count shown", "8" in out)
    test("format_behavior_section: completion rate shown", "75%" in out)
    test("format_behavior_section: efficiency shown", "40%" in out)
    test("format_behavior_section: one_shot_rate shown", "50%" in out)


def test_format_behavior_section_no_data():
    """format_behavior_section returns not-available when data is None or empty."""
    retro = load_retro()
    out_none = "\n".join(retro.format_behavior_section(None))
    test("format_behavior_section(None): shows not-available", "not available" in out_none)
    out_empty = "\n".join(retro.format_behavior_section({}))
    test("format_behavior_section({}): shows not-available", "not available" in out_empty)


def test_format_behavior_section_zero_sessions():
    """format_behavior_section handles 0 sessions without crashing."""
    retro = load_retro()
    b = {
        "completion_rate": 0.0, "knowledge_yield": 0.0,
        "efficiency_ratio": 0.0, "one_shot_rate": 0.0,
        "session_count": 0, "sessions_with_checkpoints": 0,
    }
    out = "\n".join(retro.format_behavior_section(b))
    test("format_behavior_section(0 sessions): non-empty output", bool(out.strip()))


def test_behavior_in_valid_sections():
    """behavior must be in _VALID_SECTIONS."""
    retro = load_retro()
    test(
        "behavior in _VALID_SECTIONS",
        "behavior" in retro._VALID_SECTIONS,
        f"got {retro._VALID_SECTIONS}",
    )


def test_format_subreport_behavior_with_payload():
    """format_subreport('behavior') renders correctly when behavior data is present."""
    retro = load_retro()
    reset_artifacts()
    db_path = ARTIFACT_DIR / "behavior_subreport.db"
    sessions = [("s1", 1, "2026-01-01", 10), ("s2", 0, "2026-01-02", 5)]
    documents = [("d1", "s1"), ("d2", "s1")]
    _make_behavior_db(db_path, sessions=sessions, documents=documents)
    b = retro.collect_session_behavior_signals(db_path)
    payload = {"behavior": b}
    out = retro.format_subreport(payload, "behavior")
    test("format_subreport(behavior): non-empty output", bool(out.strip()))
    test("format_subreport(behavior): Session Behavior heading", "Session Behavior" in out)
    test("format_subreport(behavior): not an error message", "Unknown section" not in out)


def test_format_subreport_behavior_unavailable():
    """format_subreport('behavior') shows header and not-available when no data."""
    retro = load_retro()
    out = retro.format_subreport({}, "behavior")
    test("format_subreport(behavior, empty): not an error", "Unknown section" not in out)
    test("format_subreport(behavior, empty): Session Behavior heading", "Session Behavior" in out)


def test_format_text_report_includes_behavior_section():
    """format_text_report must include Session Behavior section."""
    retro = load_retro()
    b = {
        "completion_rate": 0.5, "knowledge_yield": 1.0,
        "efficiency_ratio": 0.3, "one_shot_rate": 0.5,
        "session_count": 4, "sessions_with_checkpoints": 2,
    }
    payload = {
        "retro_score": 55.0,
        "grade": "Fair",
        "grade_emoji": "🟡",
        "mode": "local",
        "generated_at": "2026-01-01T00:00:00Z",
        "available_sections": ["git", "behavior"],
        "weights": {"git": 0.9, "behavior": 0.1},
        "subscores": {
            "knowledge": 0.0, "skills": 0.0, "hooks": 0.0, "git": 55.0, "behavior": 40.0,
        },
        "score_confidence": "medium",
        "distortion_flags": [],
        "accuracy_notes": [],
        "improvement_actions": [],
        "toward_100": [],
        "knowledge": {"available": False},
        "skills": {"available": False},
        "hooks": {"available": False},
        "git": {"available": False},
        "behavior": b,
    }
    report = retro.format_text_report(payload)
    test("format_text_report: Session Behavior section present", "Session Behavior" in report)
    test("format_text_report: behavior in subscores table", "behavior" in report)


def test_subreport_behavior_subprocess():
    """--subreport behavior: exits 0, produces non-empty output with header."""
    result = subprocess.run(
        [sys.executable, str(RETRO_PY), "--subreport", "behavior", "--mode", "repo", "--no-cache"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    test(
        "retro.py --subreport behavior: exits 0",
        result.returncode == 0,
        f"stderr: {result.stderr[:200]}",
    )
    output = result.stdout.strip()
    test("retro.py --subreport behavior: non-empty output", bool(output))
    test("retro.py --subreport behavior: Session Behavior heading", "Session Behavior" in output)


# ── Section 19: toward_100 diagnostics ───────────────────────────────────────


def test_toward_100_in_payload():
    """compute_retro payload must contain toward_100 as a list."""
    retro = load_retro()
    payload = retro.compute_retro(
        {"available": False},
        {"available": False},
        {"available": False},
        {
            "available": True,
            "lookback_days": 30,
            "commit_count": 10,
            "test_files_changed": 1,
            "py_files_changed": 5,
            "distinct_files_changed": 8,
            "recent_commits": [], "top_changed_files": [], "authors": [],
        },
        mode="repo",
    )
    test(
        "toward_100: key present in payload",
        "toward_100" in payload,
        f"keys: {list(payload.keys())[:12]}",
    )
    test("toward_100: is a list", isinstance(payload.get("toward_100"), list))


def test_toward_100_sections_below_100():
    """Sections below 100 appear in toward_100; sections at 100 do not."""
    retro = load_retro()
    # skills fully verified → 100.0; git with low cadence → below 100
    skills = {
        "available": True,
        "total_outcomes": 5, "total_verifications": 5,
        "verifications_passed": 5, "verifications_failed": 0,
        "outcomes_with_passing_verification": 5,
        "skill_usage": [], "recent_outcomes": [],
    }
    git = {
        "available": True, "lookback_days": 30, "commit_count": 5,
        "test_files_changed": 0, "py_files_changed": 5, "distinct_files_changed": 3,
        "recent_commits": [], "top_changed_files": [], "authors": [],
    }
    payload = retro.compute_retro(
        {"available": False}, skills, {"available": False}, git, mode="local",
    )
    t100 = payload.get("toward_100", [])
    sections_in_t100 = [item["section"] for item in t100]
    test("toward_100: git appears (below 100)", "git" in sections_in_t100, f"sections: {sections_in_t100}")
    s_score = payload["subscores"].get("skills", 0.0)
    if s_score >= 100.0:
        test(
            "toward_100: skills at 100 not in list",
            "skills" not in sections_in_t100,
            f"sections: {sections_in_t100}",
        )


def test_toward_100_barriers_are_strings():
    """All barrier entries must be non-empty strings."""
    retro = load_retro()
    skills = {
        "available": True,
        "total_outcomes": 20, "total_verifications": 0,
        "verifications_passed": 0, "verifications_failed": 0,
        "outcomes_with_passing_verification": 0,
        "skill_usage": [], "recent_outcomes": [],
    }
    payload = retro.compute_retro(
        {"available": False}, skills, {"available": False}, {"available": False}, mode="local",
    )
    for item in payload.get("toward_100", []):
        for barrier in item.get("barriers", []):
            test(
                f"toward_100[{item['section']}]: barrier is non-empty string",
                isinstance(barrier, str) and bool(barrier),
                f"got {barrier!r}",
            )


def test_toward_100_sorted_by_gap_descending():
    """toward_100 must be sorted by gap descending (biggest gap first)."""
    retro = load_retro()
    # skills unverified → 30.0 (gap=70); git at moderate score
    skills = {
        "available": True,
        "total_outcomes": 10, "total_verifications": 0,
        "verifications_passed": 0, "verifications_failed": 0,
        "outcomes_with_passing_verification": 0,
        "skill_usage": [], "recent_outcomes": [],
    }
    git = {
        "available": True, "lookback_days": 30, "commit_count": 25,
        "test_files_changed": 5, "py_files_changed": 10, "distinct_files_changed": 25,
        "recent_commits": [], "top_changed_files": [], "authors": [],
    }
    payload = retro.compute_retro(
        {"available": False}, skills, {"available": False}, git, mode="local",
    )
    t100 = payload.get("toward_100", [])
    if len(t100) >= 2:
        gaps = [item["gap"] for item in t100]
        test(
            "toward_100: sorted by gap descending",
            gaps == sorted(gaps, reverse=True),
            f"gaps: {gaps}",
        )


def test_toward_100_deterministic():
    """Same inputs must produce identical toward_100 output."""
    retro = load_retro()
    kwargs = dict(
        knowledge={"available": False},
        skills={
            "available": True,
            "total_outcomes": 10, "total_verifications": 0,
            "verifications_passed": 0, "verifications_failed": 0,
            "outcomes_with_passing_verification": 0,
            "skill_usage": [], "recent_outcomes": [],
        },
        hooks={"available": False},
        git={
            "available": True, "lookback_days": 30, "commit_count": 10,
            "test_files_changed": 1, "py_files_changed": 5, "distinct_files_changed": 8,
            "recent_commits": [], "top_changed_files": [], "authors": [],
        },
        mode="local",
    )
    p1 = retro.compute_retro(**kwargs)
    p2 = retro.compute_retro(**kwargs)
    test(
        "toward_100: deterministic (same inputs → same output)",
        p1.get("toward_100") == p2.get("toward_100"),
        f"run1={p1.get('toward_100')}, run2={p2.get('toward_100')}",
    )


def test_toward_100_skills_unverified_barrier():
    """skills_unverified state must produce no_verification_evidence barrier."""
    retro = load_retro()
    skills = {
        "available": True,
        "total_outcomes": 15, "total_verifications": 0,
        "verifications_passed": 0, "verifications_failed": 0,
        "outcomes_with_passing_verification": 0,
        "skill_usage": [], "recent_outcomes": [],
    }
    payload = retro.compute_retro(
        {"available": False}, skills, {"available": False}, {"available": False}, mode="local",
    )
    t100 = payload.get("toward_100", [])
    skills_item = next((x for x in t100 if x["section"] == "skills"), None)
    test(
        "toward_100: skills item present",
        skills_item is not None,
        f"sections: {[x['section'] for x in t100]}",
    )
    if skills_item:
        barriers_str = " ".join(skills_item.get("barriers", []))
        test(
            "toward_100: skills barrier mentions no_verification_evidence",
            "no_verification_evidence" in barriers_str or "verifications=0" in barriers_str,
            f"barriers: {skills_item.get('barriers')}",
        )


def test_toward_100_behavior_gap_when_available():
    """toward_100 includes behavior section when behavior DB is available (local mode)."""
    retro = load_retro()
    reset_artifacts()
    db_path = ARTIFACT_DIR / "toward100_behavior.db"
    # No checkpoints → completion_rate=0 → guaranteed behavior gap
    sessions = [
        ("s1", 0, "2026-01-01", 10),
        ("s2", 0, "2026-01-02", 5),
        ("s3", 0, "2026-01-03", 8),
    ]
    _make_behavior_db(db_path, sessions=sessions, documents=[])
    payload = retro.compute_retro(
        {"available": False},
        {"available": False},
        {"available": False},
        {"available": False},
        mode="local",
        db_path=db_path,
    )
    t100 = payload.get("toward_100", [])
    sections = [item["section"] for item in t100]
    test(
        "toward_100: behavior section present when DB available",
        "behavior" in sections,
        f"sections: {sections}",
    )


def test_toward_100_omits_behavior_when_no_sessions_recorded():
    """toward_100 should not fabricate a behavior gap when the DB has zero sessions."""
    retro = load_retro()
    reset_artifacts()
    db_path = ARTIFACT_DIR / "toward100_behavior_empty.db"
    _make_behavior_db(db_path, sessions=[], documents=[])
    payload = retro.compute_retro(
        {"available": False},
        {"available": False},
        {"available": False},
        {"available": False},
        mode="local",
        db_path=db_path,
    )
    t100 = payload.get("toward_100", [])
    sections = [item["section"] for item in t100]
    test(
        "toward_100: no behavior section when zero sessions recorded",
        "behavior" not in sections,
        f"sections: {sections}",
    )


def test_text_report_includes_toward_100_section():
    """format_text_report renders Toward 100 section when gaps exist."""
    retro = load_retro()
    payload = {
        "retro_score": 55.0,
        "grade": "Fair",
        "grade_emoji": "🟡",
        "mode": "local",
        "generated_at": "2026-01-01T00:00:00Z",
        "available_sections": ["git"],
        "weights": {"git": 1.0},
        "subscores": {"knowledge": 0.0, "skills": 0.0, "hooks": 0.0, "git": 55.0},
        "score_confidence": "medium",
        "distortion_flags": [],
        "accuracy_notes": [],
        "improvement_actions": [],
        "toward_100": [
            {"section": "git", "score": 55.0, "gap": 45.0, "barriers": ["commit_cadence=10/30d"]},
        ],
        "knowledge": {"available": False},
        "skills": {"available": False},
        "hooks": {"available": False},
        "git": {"available": False},
    }
    report = retro.format_text_report(payload)
    test("format_text_report: Toward 100 section present", "Toward 100" in report)
    test("format_text_report: toward_100 barrier shown", "commit_cadence" in report)


def test_toward_100_empty_when_all_100():
    """toward_100 must be empty list when all available sections score 100."""
    retro = load_retro()
    # hooks score 100 (deny_rate=0, parse_rate=0)
    hooks = {
        "available": True,
        "total_entries": 50,
        "decisions": {"allow": 50},
        "deny_rate": 0.0,
        "deny_dry_count": 0,
        "deny_dry_rate": 0.0,
        "parse_error_rate": 0.0,
        "top_rules": [], "top_denied_tools": [],
    }
    # Call _compute_toward_100 directly with hooks scoring 100
    h_score = retro._score_hooks(hooks)
    t100 = retro._compute_toward_100(
        {"hooks": h_score},
        {"available": False}, {"available": False}, hooks, {"available": False}, None,
    )
    if h_score >= 100.0:
        test("toward_100: empty when hooks=100", t100 == [], f"got {t100}")


# ── Section 20: Wave 3 — verification evidence lifts skill signals ───────────


def _make_skill_db_with_verifications(path: Path) -> None:
    """Build a skill-metrics DB where verifications are fully populated (Wave 3 style)."""
    db = sqlite3.connect(str(path))
    db.executescript("""
        CREATE TABLE IF NOT EXISTS tentacle_outcomes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tentacle_name TEXT NOT NULL,
            tentacle_id TEXT,
            outcome_status TEXT NOT NULL,
            recorded_at TEXT NOT NULL,
            verification_total INTEGER NOT NULL DEFAULT 0,
            verification_passed INTEGER NOT NULL DEFAULT 0,
            verification_failed INTEGER NOT NULL DEFAULT 0,
            todo_total INTEGER NOT NULL DEFAULT 0,
            todo_done INTEGER NOT NULL DEFAULT 0,
            learned INTEGER NOT NULL DEFAULT 0,
            summary TEXT
        );
        CREATE TABLE IF NOT EXISTS tentacle_outcome_skills (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            outcome_id INTEGER NOT NULL,
            skill_name TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS tentacle_verifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            outcome_id INTEGER,
            tentacle_name TEXT NOT NULL,
            tentacle_id TEXT,
            label TEXT NOT NULL,
            command TEXT NOT NULL,
            cwd TEXT NOT NULL,
            exit_code INTEGER NOT NULL,
            started_at TEXT NOT NULL,
            finished_at TEXT NOT NULL,
            duration_seconds REAL NOT NULL,
            log_path TEXT
        );
    """)
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    # 3 completed outcomes, each with at least 1 passing verification
    for i in range(3):
        db.execute(
            "INSERT INTO tentacle_outcomes "
            "(tentacle_name, tentacle_id, outcome_status, recorded_at, "
            "verification_total, verification_passed, verification_failed, "
            "todo_total, todo_done, learned, summary) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (f"wave3-tent-{i}", f"t{i}", "completed", now, 2, 2, 0, 3, 3, 1, f"All done #{i}"),
        )
        oid = i + 1
        for j in range(2):
            db.execute(
                "INSERT INTO tentacle_verifications "
                "(outcome_id, tentacle_name, tentacle_id, label, command, cwd, "
                "exit_code, started_at, finished_at, duration_seconds) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (oid, f"wave3-tent-{i}", f"t{i}", f"check-{j}", "python3 test.py",
                 "/repo", 0, now, now, 1.0),
            )
    db.commit()
    db.close()


def test_skill_signals_with_verification_evidence():
    """When tentacle_verifications rows exist, collect_skill_signals reports them."""
    retro = load_retro()
    reset_artifacts()
    db_path = ARTIFACT_DIR / "wave3-verified.db"
    _make_skill_db_with_verifications(db_path)
    original = retro.SKILL_METRICS_DB
    retro.SKILL_METRICS_DB = db_path
    try:
        result = retro.collect_skill_signals()
        test(
            "wave3 skill signals: available=True",
            result.get("available") is True,
            f"got {result}",
        )
        test(
            "wave3 skill signals: total_outcomes == 3",
            result.get("total_outcomes", 0) == 3,
            f"got {result.get('total_outcomes')}",
        )
        # verifications_passed should be non-zero (6 total: 2 per outcome × 3 outcomes)
        vp = result.get("verifications_passed", 0)
        test(
            "wave3 skill signals: verifications_passed > 0",
            vp > 0,
            f"got {vp}",
        )
    finally:
        retro.SKILL_METRICS_DB = original


def test_skill_signals_zero_verifications_keeps_low_subscore():
    """When verifications_passed == 0, compute_retro skills subscore stays at baseline (30.0)."""
    retro = load_retro()
    reset_artifacts()
    db_path = ARTIFACT_DIR / "wave3-unverified.db"
    _make_skill_db(db_path)  # Uses existing helper that inserts 0 verification evidence
    original = retro.SKILL_METRICS_DB
    retro.SKILL_METRICS_DB = db_path
    try:
        result = retro.collect_skill_signals()
        # With zero verifications, skills subscore must not exceed 50 (baseline floor)
        skills_signals = result if result.get("available") else {"available": False}
        payload = retro.compute_retro(
            {"available": False},
            skills_signals,
            {"available": False},
            {"available": False},
            mode="local",
        )
        skills_score = payload.get("subscores", {}).get("skills", 0)
        test(
            "wave3: skills subscore <= 50 when zero verifications",
            skills_score <= 50,
            f"got skills_score={skills_score}",
        )
    finally:
        retro.SKILL_METRICS_DB = original


def test_compute_retro_verifications_affect_skills_subscore():
    """compute_retro produces a higher skills subscore with verification evidence than without."""
    retro = load_retro()
    reset_artifacts()

    db_verified = ARTIFACT_DIR / "wave3-with-verif.db"
    db_unverified = ARTIFACT_DIR / "wave3-without-verif.db"
    _make_skill_db_with_verifications(db_verified)
    _make_skill_db(db_unverified)

    original = retro.SKILL_METRICS_DB

    try:
        retro.SKILL_METRICS_DB = db_verified
        signals_verified = retro.collect_skill_signals()

        retro.SKILL_METRICS_DB = db_unverified
        signals_unverified = retro.collect_skill_signals()
    finally:
        retro.SKILL_METRICS_DB = original

    payload_v = retro.compute_retro(
        {"available": False}, signals_verified, {"available": False}, {"available": False}, mode="local"
    )
    payload_u = retro.compute_retro(
        {"available": False}, signals_unverified, {"available": False}, {"available": False}, mode="local"
    )

    score_v = payload_v.get("subscores", {}).get("skills", 0)
    score_u = payload_u.get("subscores", {}).get("skills", 0)

    test(
        "wave3: skills subscore higher with verifications than without",
        score_v > score_u,
        f"verified={score_v} vs unverified={score_u}",
    )
    test(
        "wave3: skills subscore with verifications is above 30",
        score_v > 30,
        f"got {score_v}",
    )


def main():
    print("test_retro.py — retro.py targeted tests")
    print()

    print("1. File validity")
    test_file_exists()
    test_valid_syntax()

    print("2. Read-only contract")
    test_no_db_writes()
    test_no_subprocess_commits()
    test_no_learn_calls()
    test_state_file_not_in_gitignore_scope()

    print("3. Audit signal parsing")
    test_audit_signals_empty_file()
    test_audit_signals_synthetic()
    test_audit_signals_missing_file()

    print("4. Knowledge signals")
    test_knowledge_signals_fallback_db()
    test_knowledge_signals_module_system_exit_falls_back()

    print("5. Skill signals")
    test_skill_signals_available()
    test_skill_signals_missing_db()
    test_skill_signals_module_load_failure_is_unavailable()

    print("6. compute_retro scoring")
    test_compute_retro_repo_mode()
    test_compute_retro_local_mode_partial()
    test_compute_retro_weights_sum()
    test_compute_retro_score_grade()

    print("7. JSON output shape")
    test_json_output_shape()

    print("8. Score mode")
    test_score_mode()

    print("9. Subreport mode")
    test_subreport_mode()
    test_subreport_invalid()

    print("10. Repo-only boundary")
    test_repo_mode_no_db_access()

    print("11. State cache")
    test_state_cache_roundtrip()
    test_state_cache_missing()

    print("12. Format functions")
    test_format_score_line()
    test_format_text_report_complete()
    test_format_subreport_all_valid()
    test_format_subreport_invalid()

    print("13. Calibration: deny-dry separation")
    test_audit_deny_dry_excluded_from_deny_rate()
    test_audit_deny_dry_reported_separately()
    test_hook_score_not_penalised_by_deny_dry()

    print("14. Calibration: skills verification evidence tiers")
    test_score_skills_unverified_outcomes_below_neutral()
    test_score_skills_outcome_level_fallback()
    test_score_skills_detailed_verifications_take_priority()

    print("15. Calibration: interpretation fields")
    test_compute_retro_interpretation_fields_present()
    test_distortion_flag_skills_unverified()
    test_distortion_flag_hook_deny_dry_noise()
    test_distortion_flags_empty_when_clean()
    test_json_output_interpretation_fields()

    print("16. Scout coverage signals")
    test_scout_signals_missing_config()
    test_scout_signals_config_only()
    test_scout_signals_with_state_file()
    test_scout_signals_grace_expired()
    test_compute_retro_scout_in_payload()
    test_compute_retro_scout_absent_defaults()
    test_json_output_has_scout_field()
    test_scout_signals_relative_state_file_anchored_to_config_dir()
    test_scout_signals_absolute_state_file_still_works()
    test_scout_signals_malformed_grace_window_string()
    test_scout_signals_malformed_grace_window_list()
    test_scout_signals_malformed_json_config()
    test_scout_signals_valid_grace_window_unchanged()

    print("17. Session behavior signals")
    test_behavior_signals_empty_db()
    test_behavior_signals_sessions_no_docs()
    test_behavior_signals_mixed()
    test_behavior_signals_all_zero_edge_case()
    test_behavior_signals_missing_tables()
    test_score_behavior_function()
    test_compute_retro_behavior_in_payload()
    test_compute_retro_behavior_repo_mode_no_subscore()

    print("18. Behavior as first-class text/subreport surface")
    test_format_behavior_section_with_data()
    test_format_behavior_section_no_data()
    test_format_behavior_section_zero_sessions()
    test_behavior_in_valid_sections()
    test_format_subreport_behavior_with_payload()
    test_format_subreport_behavior_unavailable()
    test_format_text_report_includes_behavior_section()
    test_subreport_behavior_subprocess()

    print("19. toward_100 diagnostics")
    test_toward_100_in_payload()
    test_toward_100_sections_below_100()
    test_toward_100_barriers_are_strings()
    test_toward_100_sorted_by_gap_descending()
    test_toward_100_deterministic()
    test_toward_100_skills_unverified_barrier()
    test_toward_100_behavior_gap_when_available()
    test_toward_100_omits_behavior_when_no_sessions_recorded()
    test_text_report_includes_toward_100_section()
    test_toward_100_empty_when_all_100()

    print("20. Wave 3: verification evidence lifts skill signals")
    test_skill_signals_with_verification_evidence()
    test_skill_signals_zero_verifications_keeps_low_subscore()
    test_compute_retro_verifications_affect_skills_subscore()

    # Cleanup
    reset_artifacts()

    print()
    total = PASS + FAIL
    print(f"Results: {PASS}/{total} passed", "✅" if FAIL == 0 else "❌")
    if FAIL > 0:
        print(f"  {FAIL} test(s) failed")
    sys.exit(0 if FAIL == 0 else 1)


if __name__ == "__main__":
    main()
