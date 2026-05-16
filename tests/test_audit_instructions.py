#!/usr/bin/env python3
"""
test_audit_instructions.py - Focused tests for audit-instructions.py.

Run: python tests/test_audit_instructions.py
"""

import importlib.util
import io
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

if os.name == "nt":
    for _stream in (sys.stdout, sys.stderr):
        if hasattr(_stream, "reconfigure"):
            _stream.reconfigure(encoding="utf-8", errors="replace")

TOOLS_DIR = Path(__file__).parent.parent
SCRIPT_PATH = TOOLS_DIR / "audit-instructions.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("audit_instructions", SCRIPT_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ai = _load_module()


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _seed_db(path: Path, rows: list[tuple[str, str, str, str]], *, timestamp_column: str = "created_at") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(str(path))
    try:
        db.execute(
            f"""
            CREATE TABLE knowledge_entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                category TEXT,
                title TEXT,
                content TEXT,
                {timestamp_column} TEXT
            )
            """
        )
        db.executemany(
            f"""
            INSERT INTO knowledge_entries (category, title, content, {timestamp_column})
            VALUES (?, ?, ?, ?)
            """,
            rows,
        )
        db.commit()
    finally:
        db.close()


class TempRepoMixin:
    def setUp(self):
        self._tmpdir = tempfile.mkdtemp(prefix="audit-instructions-")
        self.repo_root = Path(self._tmpdir)
        self.db_path = self.repo_root / "knowledge.db"

    def tearDown(self):
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def write_default_docs(self):
        _write_text(
            self.repo_root / ".github" / "copilot-instructions.md",
            """# Copilot

### 1. Investigate Before Acting

Read the target files before editing.

### 2. Briefing Before Complex Tasks

Run `sk briefing` before touching unfamiliar code.
Recent briefing misses should cause emphasis.

### 3. Claims Require Evidence

Provide evidence for every verification claim.
""",
        )
        _write_text(
            self.repo_root / "docs" / "AGENT-RULES.md",
            """# Agent Rules

## Rule 1 - Investigate Before Acting

Read the target files before editing.

## Rule 2 - Briefing Before Complex Tasks

Run `sk briefing` before touching unfamiliar code.

## Rule 3 - Claims Require Evidence

Provide evidence for every verification claim.
""",
        )
        _write_text(
            self.repo_root / "AGENTS.md",
            """# AGENTS

## Mandatory Rules

1. **Investigate Before Acting** - summary
2. **Briefing Before Complex Tasks** - summary
""",
        )


class TestScriptExists(unittest.TestCase):
    def test_script_exists(self):
        self.assertTrue(SCRIPT_PATH.exists(), f"audit-instructions.py not found at {SCRIPT_PATH}")


class TestRuleParsing(TempRepoMixin, unittest.TestCase):
    def test_extract_heading_rules(self):
        self.write_default_docs()
        rules = ai._extract_heading_rules(self.repo_root / ".github" / "copilot-instructions.md")
        self.assertEqual([rule["number"] for rule in rules], [1, 2, 3])
        self.assertEqual(rules[1]["title"], "Briefing Before Complex Tasks")

    def test_extract_agents_rules(self):
        self.write_default_docs()
        rules = ai._extract_agents_rules(self.repo_root / "AGENTS.md")
        self.assertEqual([rule["number"] for rule in rules], [1, 2])
        self.assertEqual(rules[0]["title"], "Investigate Before Acting")


class TestKnowledgeLoading(TempRepoMixin, unittest.TestCase):
    def test_missing_db_returns_not_available(self):
        entries, available = ai._load_knowledge_entries(self.db_path)
        self.assertEqual(entries, [])
        self.assertFalse(available)

    def test_existing_db_loads_entries(self):
        _seed_db(
            self.db_path,
            [("mistake", "Briefing missed", "Forgot to run briefing before edits", "2026-05-01")],
        )
        entries, available = ai._load_knowledge_entries(self.db_path)
        self.assertTrue(available)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["category"], "mistake")

    def test_existing_db_uses_last_seen_when_created_at_absent(self):
        _seed_db(
            self.db_path,
            [("mistake", "Briefing missed", "Forgot to run briefing before edits", "2026-05-01")],
            timestamp_column="last_seen",
        )
        entries, available = ai._load_knowledge_entries(self.db_path)
        self.assertTrue(available)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["created_at"], "2026-05-01")


class TestAuditReport(TempRepoMixin, unittest.TestCase):
    def test_build_report_flags_emphasis_removal_and_mirror_gap(self):
        self.write_default_docs()
        _seed_db(
            self.db_path,
            [
                ("mistake", "Briefing missed", "Forgot to run briefing before edits", "2026-05-01"),
                ("decision", "Evidence policy", "Claims require evidence in closeouts", "2026-05-02"),
            ],
        )

        report = ai._build_report(self.repo_root, self.db_path)
        self.assertIsNotNone(report)
        ineffective = {rule["number"]: rule for rule in report["ineffective_rules"]}
        self.assertIn(2, ineffective)
        self.assertIn("needs_emphasis", ineffective[2]["signals"])
        self.assertIn(1, ineffective)
        self.assertIn("candidate_for_removal", ineffective[1]["signals"])
        mirror_missing = [item for item in report["mirror_findings"] if item["kind"] == "missing_rule"]
        self.assertTrue(any(item["rule_number"] == 3 for item in mirror_missing))

    def test_detect_rule_drift_finds_title_mismatch(self):
        self.write_default_docs()
        _write_text(
            self.repo_root / ".github" / "copilot-instructions.md",
            """# Copilot

### 1. Investigate Before Editing

Read files first.
""",
        )
        _write_text(
            self.repo_root / "docs" / "AGENT-RULES.md",
            """# Agent Rules

## Rule 1 - Investigate Before Acting

Read files first.
""",
        )
        _write_text(
            self.repo_root / "AGENTS.md",
            "# AGENTS\n\n## Mandatory Rules\n\n1. **Investigate Before Acting** - summary\n",
        )

        report = ai._build_report(self.repo_root, self.db_path)
        self.assertIsNotNone(report)
        drift = report["drift_findings"]
        self.assertEqual(len(drift), 1)
        self.assertEqual(drift[0]["kind"], "title_mismatch")


class TestMain(TempRepoMixin, unittest.TestCase):
    def test_main_text_output(self):
        self.write_default_docs()
        _seed_db(
            self.db_path,
            [("mistake", "Briefing missed", "Forgot to run briefing before edits", "2026-05-01")],
        )
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = ai.main(["--repo-root", str(self.repo_root), "--db-path", str(self.db_path)])
        self.assertEqual(rc, 0)
        output = buf.getvalue()
        self.assertIn("Instruction Effectiveness Audit", output)
        self.assertIn("Briefing Before Complex Tasks", output)

    def test_main_json_output(self):
        self.write_default_docs()
        _seed_db(
            self.db_path,
            [("mistake", "Briefing missed", "Forgot to run briefing before edits", "2026-05-01")],
        )
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = ai.main(["--repo-root", str(self.repo_root), "--db-path", str(self.db_path), "--json"])
        self.assertEqual(rc, 0)
        payload = json.loads(buf.getvalue())
        self.assertIn("summary", payload)
        self.assertIn("ineffective_rules", payload)

    def test_main_missing_required_files(self):
        buf = io.StringIO()
        with redirect_stdout(buf), patch("sys.stderr", new=io.StringIO()):
            rc = ai.main(["--repo-root", str(self.repo_root), "--db-path", str(self.db_path), "--json"])
        self.assertEqual(rc, 1)
        payload = json.loads(buf.getvalue())
        self.assertIn("error", payload)


if __name__ == "__main__":
    unittest.main()
