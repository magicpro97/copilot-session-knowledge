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

REPO = Path(__file__).parent
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
    for section in ("knowledge", "skills", "hooks", "git"):
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
    for section in ("knowledge", "skills", "hooks", "git"):
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
