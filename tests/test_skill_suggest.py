#!/usr/bin/env python3
"""
test_skill_suggest.py — Regression tests for skill-suggest.py.

Tests: pattern mining, dedup/overlap, SKILL.md draft generation,
validate-skill.py compatibility, and CLI argument parsing.

Run:
    python tests/test_skill_suggest.py
"""

import importlib.util
import json
import os
import sqlite3
import sys
import unittest
from pathlib import Path

if os.name == "nt":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

REPO = Path(__file__).parent.parent
ARTIFACT_DIR = REPO / ".skill-suggest-test-artifacts"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_module(mod_name: str, filename: str):
    spec = importlib.util.spec_from_file_location(mod_name, str(REPO / filename))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _reset_artifacts() -> None:
    if ARTIFACT_DIR.exists():
        for p in sorted(ARTIFACT_DIR.rglob("*"), reverse=True):
            if p.is_file():
                p.unlink(missing_ok=True)
            elif p.is_dir():
                p.rmdir()
        ARTIFACT_DIR.rmdir()
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)


def _make_knowledge_db(db_path: Path, entries: list[dict]) -> None:
    """Create a minimal knowledge.db with the given entries."""
    db = sqlite3.connect(str(db_path))
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS knowledge_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL DEFAULT 'test-session',
            category TEXT NOT NULL,
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            tags TEXT DEFAULT '',
            confidence REAL DEFAULT 1.0,
            occurrence_count INTEGER DEFAULT 1,
            topic_key TEXT DEFAULT NULL,
            wing TEXT DEFAULT '',
            room TEXT DEFAULT ''
        )
        """
    )
    for e in entries:
        db.execute(
            """
            INSERT INTO knowledge_entries
                (session_id, category, title, content, tags,
                 confidence, occurrence_count, topic_key)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                e.get("session_id", "sess-1"),
                e.get("category", "pattern"),
                e.get("title", "Untitled"),
                e.get("content", ""),
                e.get("tags", ""),
                e.get("confidence", 1.0),
                e.get("occurrence_count", 1),
                e.get("topic_key", None),
            ),
        )
    db.commit()
    db.close()


# ---------------------------------------------------------------------------
# Test suite
# ---------------------------------------------------------------------------

class TestSlugify(unittest.TestCase):
    """Unit tests for the _slugify helper."""

    @classmethod
    def setUpClass(cls):
        cls.mod = _load_module("skill_suggest_slugify", "skill-suggest.py")

    def test_simple_lower(self):
        self.assertEqual(self.mod._slugify("Docker"), "docker")

    def test_spaces_become_hyphens(self):
        self.assertEqual(self.mod._slugify("hello world"), "hello-world")

    def test_consecutive_specials_collapse(self):
        self.assertEqual(self.mod._slugify("foo---bar"), "foo-bar")

    def test_leading_trailing_stripped(self):
        self.assertEqual(self.mod._slugify("--foo--"), "foo")

    def test_digit_prefix_gets_prefix(self):
        s = self.mod._slugify("1bad")
        self.assertTrue(s.startswith("skill-"))

    def test_empty_returns_unnamed(self):
        self.assertEqual(self.mod._slugify(""), "unnamed-skill")

    def test_max_length_64(self):
        long = "a" * 100
        self.assertLessEqual(len(self.mod._slugify(long)), 64)

    def test_unicode_stripped(self):
        s = self.mod._slugify("résumé builder")
        self.assertRegex(s, r"^[a-z0-9][a-z0-9\-]*[a-z0-9]$")


class TestTokenOverlap(unittest.TestCase):
    """Unit tests for _token_overlap."""

    @classmethod
    def setUpClass(cls):
        cls.mod = _load_module("skill_suggest_overlap", "skill-suggest.py")

    def test_identical(self):
        self.assertAlmostEqual(self.mod._token_overlap("docker", "docker"), 1.0)

    def test_no_overlap(self):
        self.assertAlmostEqual(self.mod._token_overlap("docker", "python"), 0.0)

    def test_partial(self):
        score = self.mod._token_overlap("docker-compose", "compose-network")
        self.assertGreater(score, 0.0)
        self.assertLess(score, 1.0)

    def test_empty_strings(self):
        self.assertAlmostEqual(self.mod._token_overlap("", "anything"), 0.0)


class TestFindOverlap(unittest.TestCase):
    """Unit tests for _find_overlap dedup logic."""

    @classmethod
    def setUpClass(cls):
        cls.mod = _load_module("skill_suggest_dedup", "skill-suggest.py")

    def test_exact_match(self):
        existing = [{"name": "docker", "path": "/x/SKILL.md"}]
        result = self.mod._find_overlap("docker", existing)
        self.assertIn("docker", result)

    def test_high_token_overlap(self):
        existing = [{"name": "docker-compose", "path": "/x/SKILL.md"}]
        result = self.mod._find_overlap("docker-compose-tutorial", existing)
        self.assertIn("docker-compose", result)

    def test_no_overlap(self):
        existing = [{"name": "react-hooks", "path": "/x/SKILL.md"}]
        result = self.mod._find_overlap("dynamodb-patterns", existing)
        self.assertEqual(result, [])


class TestMakeSkillDraft(unittest.TestCase):
    """Unit tests for _make_skill_draft — verifies against validate-skill.py."""

    @classmethod
    def setUpClass(cls):
        _reset_artifacts()
        cls.suggest_mod = _load_module("skill_suggest_draft", "skill-suggest.py")
        cls.validate_mod = _load_module("validate_skill_draft", "validate-skill.py")

    def _validate_draft(self, draft: str, skill_name: str = "test-skill") -> tuple[list, list]:
        """Write draft to artifact file and validate with validate-skill.py."""
        skill_dir = ARTIFACT_DIR / skill_name
        skill_dir.mkdir(parents=True, exist_ok=True)
        skill_md = skill_dir / "SKILL.md"
        skill_md.write_text(draft, encoding="utf-8")
        errors, warnings = self.validate_mod.validate(skill_md)
        return errors, warnings

    def test_basic_draft_no_errors(self):
        draft = self.suggest_mod._make_skill_draft(
            name="docker-patterns",
            title="Docker Patterns",
            top_tags=["docker", "container", "compose"],
            sample_entries=[
                {"title": "Use bind mounts for dev", "content": "Bind mounts sync host and container.", "tags": "docker"},
            ],
        )
        errors, _warnings = self._validate_draft(draft, "docker-patterns")
        self.assertEqual(errors, [], f"Draft validation errors: {errors}")

    def test_draft_has_required_sections(self):
        draft = self.suggest_mod._make_skill_draft(
            name="python-patterns",
            title="Python Patterns",
            top_tags=["python", "stdlib", "patterns"],
            sample_entries=[],
        )
        self.assertIn("## When to use", draft)
        self.assertIn("## Workflow", draft)
        self.assertIn("<example>", draft)
        self.assertIn("</example>", draft)
        self.assertIn("# Python Patterns", draft)

    def test_draft_frontmatter_has_name_and_description(self):
        draft = self.suggest_mod._make_skill_draft(
            name="api-design",
            title="API Design",
            top_tags=["api", "rest"],
            sample_entries=[],
        )
        self.assertIn("name: api-design", draft)
        self.assertIn("description:", draft)

    def test_draft_description_has_trigger_phrases(self):
        draft = self.suggest_mod._make_skill_draft(
            name="ci-patterns",
            title="CI Patterns",
            top_tags=["ci", "pipeline"],
            sample_entries=[],
        )
        desc_lower = draft.lower()
        has_trigger = any(
            p in desc_lower
            for p in ["use when", "trigger", "activat", "invoke", "keyword"]
        )
        self.assertTrue(has_trigger, "Description must contain a trigger phrase")

    def test_draft_under_500_lines(self):
        draft = self.suggest_mod._make_skill_draft(
            name="test-skill",
            title="Test Skill",
            top_tags=["test", "unit", "pytest"],
            sample_entries=[
                {"title": f"Pattern {i}", "content": "Some content.", "tags": "test"}
                for i in range(5)
            ],
        )
        self.assertLessEqual(len(draft.splitlines()), 500)

    def test_draft_name_slug_is_valid(self):
        """The name in the draft must satisfy the validator's name rules."""
        draft = self.suggest_mod._make_skill_draft(
            name="git-workflow",
            title="Git Workflow",
            top_tags=["git", "branch"],
            sample_entries=[],
        )
        errors, _ = self._validate_draft(draft, "git-workflow")
        # Only errors about name validation should be absent
        name_errors = [e for e in errors if "name" in e.lower()]
        self.assertEqual(name_errors, [], f"Name validation errors: {name_errors}")

    def test_no_security_violations_in_draft(self):
        """Generated SKILL.md drafts must not trigger security rules."""
        draft = self.suggest_mod._make_skill_draft(
            name="auth-patterns",
            title="Auth Patterns",
            top_tags=["auth", "jwt", "token"],
            sample_entries=[
                {"title": "JWT expiry", "content": "Set short TTL for access tokens.", "tags": "auth,jwt"}
            ],
        )
        findings = self.validate_mod.validate_security(draft)
        critical_high = [f for f in findings if f.severity in ("critical", "high")]
        self.assertEqual(critical_high, [], f"Security violations in draft: {critical_high}")


class TestMinePatterns(unittest.TestCase):
    """Integration tests for mine_patterns against a real SQLite test DB."""

    @classmethod
    def setUpClass(cls):
        _reset_artifacts()
        cls.mod = _load_module("skill_suggest_mine", "skill-suggest.py")
        cls.db_path = ARTIFACT_DIR / "test-knowledge.db"

        # Create DB with enough entries to pass min_occurrences=3
        _make_knowledge_db(
            cls.db_path,
            [
                # Topic-key cluster: "docker" with 4 occurrences
                {
                    "title": "Docker bind mounts",
                    "content": "Use bind mounts in dev.",
                    "tags": "docker,container",
                    "category": "pattern",
                    "occurrence_count": 2,
                    "topic_key": "docker",
                },
                {
                    "title": "Docker volumes for data",
                    "content": "Named volumes persist data.",
                    "tags": "docker,volume",
                    "category": "pattern",
                    "occurrence_count": 2,
                    "topic_key": "docker",
                },
                # Tag cluster (no topic_key): "python" with 5 occurrences
                {
                    "title": "Python type hints",
                    "content": "Use type hints for clarity.",
                    "tags": "python,typing",
                    "category": "pattern",
                    "occurrence_count": 3,
                    "topic_key": None,
                },
                {
                    "title": "Python dataclasses",
                    "content": "Prefer dataclasses over dicts.",
                    "tags": "python,dataclasses",
                    "category": "decision",
                    "occurrence_count": 2,
                    "topic_key": None,
                },
                # Below threshold (occurrence_count=1, topic_key not set)
                {
                    "title": "Single occurrence",
                    "content": "Rare entry.",
                    "tags": "rare",
                    "category": "mistake",
                    "occurrence_count": 1,
                    "topic_key": None,
                },
            ],
        )

    def test_mine_returns_list(self):
        result = self.mod.mine_patterns(self.db_path, min_occurrences=3)
        self.assertIsInstance(result, list)

    def test_docker_topic_key_cluster_found(self):
        result = self.mod.mine_patterns(self.db_path, min_occurrences=3)
        names = [c["candidate_name"] for c in result]
        self.assertIn("docker", names, f"Expected 'docker' cluster in {names}")

    def test_python_tag_cluster_found(self):
        result = self.mod.mine_patterns(self.db_path, min_occurrences=3)
        names = [c["candidate_name"] for c in result]
        self.assertIn("python", names, f"Expected 'python' tag cluster in {names}")

    def test_below_threshold_excluded(self):
        result = self.mod.mine_patterns(self.db_path, min_occurrences=3)
        names = [c["candidate_name"] for c in result]
        self.assertNotIn("rare", names, "Entries below threshold must be excluded")

    def test_missing_db_returns_empty(self):
        result = self.mod.mine_patterns(ARTIFACT_DIR / "nonexistent.db", min_occurrences=1)
        self.assertEqual(result, [])

    def test_candidates_have_required_fields(self):
        result = self.mod.mine_patterns(self.db_path, min_occurrences=3)
        required = {
            "candidate_name", "source_cluster", "cluster_type",
            "score", "total_occurrences", "entry_count",
            "avg_confidence", "category", "top_tags",
            "representative_title", "sample_entries",
        }
        for c in result:
            missing = required - set(c.keys())
            self.assertEqual(missing, set(), f"Candidate missing fields: {missing}")

    def test_results_sorted_by_score_desc(self):
        result = self.mod.mine_patterns(self.db_path, min_occurrences=1)
        scores = [c["score"] for c in result]
        self.assertEqual(scores, sorted(scores, reverse=True))

    def test_limit_respected(self):
        result = self.mod.mine_patterns(self.db_path, min_occurrences=1, limit=1)
        self.assertLessEqual(len(result), 1)


class TestSuggest(unittest.TestCase):
    """Tests for the suggest() pipeline function."""

    @classmethod
    def setUpClass(cls):
        _reset_artifacts()
        cls.mod = _load_module("skill_suggest_pipeline", "skill-suggest.py")
        cls.db_path = ARTIFACT_DIR / "suggest-knowledge.db"
        cls.skills_dir = ARTIFACT_DIR / "test-skills"

        _make_knowledge_db(
            cls.db_path,
            [
                {
                    "title": "Git rebase workflow",
                    "content": "Use interactive rebase to clean commits.",
                    "tags": "git,rebase,workflow",
                    "category": "pattern",
                    "occurrence_count": 4,
                    "topic_key": "git-workflow",
                },
                {
                    "title": "Git commit hygiene",
                    "content": "One logical change per commit.",
                    "tags": "git,commit",
                    "category": "decision",
                    "occurrence_count": 2,
                    "topic_key": "git-workflow",
                },
            ],
        )

        # Create a fake existing skill for dedup testing
        existing_skill_dir = cls.skills_dir / "git-workflow"
        existing_skill_dir.mkdir(parents=True, exist_ok=True)
        (existing_skill_dir / "SKILL.md").write_text(
            "---\nname: git-workflow\ndescription: Git workflow skill.\n---\n# Git Workflow\n",
            encoding="utf-8",
        )

    def test_suggest_returns_dict_with_required_keys(self):
        result = self.mod.suggest(
            db_path=self.db_path,
            skills_dir=ARTIFACT_DIR / "empty-skills",
            min_occurrences=3,
        )
        required = {
            "generated_at", "min_occurrences", "db_path", "db_exists",
            "skills_dir", "existing_skills_checked",
            "suggestion_count", "suggestions",
        }
        missing = required - set(result.keys())
        self.assertEqual(missing, set(), f"Missing keys: {missing}")

    def test_db_exists_true_when_present(self):
        result = self.mod.suggest(db_path=self.db_path, min_occurrences=1)
        self.assertTrue(result["db_exists"])

    def test_db_exists_false_when_missing(self):
        result = self.mod.suggest(db_path=ARTIFACT_DIR / "missing.db", min_occurrences=1)
        self.assertFalse(result["db_exists"])
        self.assertEqual(result["suggestions"], [])

    def test_suggestions_have_required_fields(self):
        result = self.mod.suggest(
            db_path=self.db_path,
            skills_dir=ARTIFACT_DIR / "empty-skills",
            min_occurrences=3,
        )
        required = {
            "candidate_name", "source_cluster", "cluster_type",
            "score", "total_occurrences", "entry_count", "avg_confidence",
            "top_tags", "overlap_with_existing", "skill_draft",
        }
        for sug in result["suggestions"]:
            missing = required - set(sug.keys())
            self.assertEqual(missing, set(), f"Suggestion missing fields: {missing}")

    def test_overlap_detected_against_existing_skill(self):
        result = self.mod.suggest(
            db_path=self.db_path,
            skills_dir=self.skills_dir,
            min_occurrences=3,
        )
        # "git-workflow" topic_key maps to "git-workflow" candidate — exact match with existing
        overlapping = [
            s for s in result["suggestions"]
            if s["candidate_name"] == "git-workflow"
        ]
        if overlapping:
            self.assertIn("git-workflow", overlapping[0]["overlap_with_existing"])

    def test_skill_draft_in_each_suggestion(self):
        result = self.mod.suggest(
            db_path=self.db_path,
            skills_dir=ARTIFACT_DIR / "empty-skills",
            min_occurrences=3,
        )
        for sug in result["suggestions"]:
            self.assertIn("---", sug["skill_draft"])
            self.assertIn("name:", sug["skill_draft"])
            self.assertIn("<example>", sug["skill_draft"])

    def test_json_serializable(self):
        result = self.mod.suggest(db_path=self.db_path, min_occurrences=1)
        # Must not raise
        serialized = json.dumps(result)
        decoded = json.loads(serialized)
        self.assertEqual(decoded["suggestion_count"], result["suggestion_count"])

    def test_existing_skills_loaded(self):
        result = self.mod.suggest(
            db_path=self.db_path,
            skills_dir=self.skills_dir,
            min_occurrences=1,
        )
        self.assertIn("git-workflow", result["existing_skills_checked"])


class TestValidatorCompatibility(unittest.TestCase):
    """End-to-end: generate a draft and verify it passes validate-skill.py."""

    @classmethod
    def setUpClass(cls):
        _reset_artifacts()
        cls.suggest_mod = _load_module("skill_suggest_e2e", "skill-suggest.py")
        cls.validate_mod = _load_module("validate_skill_e2e", "validate-skill.py")
        cls.db_path = ARTIFACT_DIR / "e2e-knowledge.db"

        _make_knowledge_db(
            cls.db_path,
            [
                {
                    "title": "Async Python patterns",
                    "content": "Use asyncio.gather for concurrent IO-bound tasks.",
                    "tags": "python,asyncio,async,concurrency",
                    "category": "pattern",
                    "occurrence_count": 3,
                    "topic_key": "async-python",
                },
                {
                    "title": "Avoid blocking calls in async",
                    "content": "Never call time.sleep() inside an async coroutine.",
                    "tags": "python,asyncio,async",
                    "category": "mistake",
                    "occurrence_count": 2,
                    "topic_key": "async-python",
                },
            ],
        )

    def test_generated_draft_passes_validator(self):
        result = self.suggest_mod.suggest(
            db_path=self.db_path,
            skills_dir=ARTIFACT_DIR / "no-skills",
            min_occurrences=3,
        )
        self.assertGreater(result["suggestion_count"], 0, "Expected at least one suggestion")

        sug = result["suggestions"][0]
        name = sug["candidate_name"]
        draft = sug["skill_draft"]

        skill_dir = ARTIFACT_DIR / "e2e-skill" / name
        skill_dir.mkdir(parents=True, exist_ok=True)
        skill_md = skill_dir / "SKILL.md"
        skill_md.write_text(draft, encoding="utf-8")

        errors, _warnings = self.validate_mod.validate(skill_md)
        self.assertEqual(errors, [], f"Validator errors for '{name}': {errors}")


class TestCliMain(unittest.TestCase):
    """Tests for the CLI main() entry point."""

    @classmethod
    def setUpClass(cls):
        _reset_artifacts()
        cls.mod = _load_module("skill_suggest_cli", "skill-suggest.py")
        cls.db_path = ARTIFACT_DIR / "cli-knowledge.db"

        _make_knowledge_db(
            cls.db_path,
            [
                {
                    "title": "CI caching",
                    "content": "Cache node_modules in CI.",
                    "tags": "ci,cache,devops",
                    "category": "pattern",
                    "occurrence_count": 4,
                    "topic_key": "ci-caching",
                },
            ],
        )

    def test_main_json_format_exits_0(self):
        import io
        from contextlib import redirect_stdout

        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = self.mod.main(
                [
                    "--db", str(self.db_path),
                    "--format", "json",
                    "--min-occurrences", "3",
                ]
            )
        self.assertEqual(rc, 0)
        output = buf.getvalue()
        data = json.loads(output)
        self.assertIn("suggestions", data)
        self.assertIn("min_occurrences", data)

    def test_main_text_format_exits_0(self):
        import io
        from contextlib import redirect_stdout

        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = self.mod.main(
                [
                    "--db", str(self.db_path),
                    "--format", "text",
                    "--min-occurrences", "3",
                ]
            )
        self.assertEqual(rc, 0)

    def test_main_missing_db_exits_0(self):
        """Missing DB should not crash — just report 0 suggestions."""
        import io
        from contextlib import redirect_stdout

        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = self.mod.main(
                [
                    "--db", str(ARTIFACT_DIR / "does-not-exist.db"),
                    "--format", "json",
                    "--min-occurrences", "1",
                ]
            )
        self.assertEqual(rc, 0)
        data = json.loads(buf.getvalue())
        self.assertFalse(data["db_exists"])

    def test_main_json_min_occurrences_respected(self):
        """With min-occurrences=999, no suggestions should be returned."""
        import io
        from contextlib import redirect_stdout

        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = self.mod.main(
                [
                    "--db", str(self.db_path),
                    "--format", "json",
                    "--min-occurrences", "999",
                ]
            )
        self.assertEqual(rc, 0)
        data = json.loads(buf.getvalue())
        self.assertEqual(data["suggestion_count"], 0)

    def test_main_limit_respected(self):
        """--limit 1 should cap suggestions at 1."""
        import io
        from contextlib import redirect_stdout

        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = self.mod.main(
                [
                    "--db", str(self.db_path),
                    "--format", "json",
                    "--min-occurrences", "1",
                    "--limit", "1",
                ]
            )
        self.assertEqual(rc, 0)
        data = json.loads(buf.getvalue())
        self.assertLessEqual(len(data["suggestions"]), 1)


# ---------------------------------------------------------------------------
if __name__ == "__main__":
    unittest.main(verbosity=2)
