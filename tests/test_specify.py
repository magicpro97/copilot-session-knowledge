#!/usr/bin/env python3
"""
test_specify.py - Focused tests for specify.py.

Run: python tests/test_specify.py
"""

import importlib.util
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

if os.name == "nt":
    for _stream in (sys.stdout, sys.stderr):
        if hasattr(_stream, "reconfigure"):
            _stream.reconfigure(encoding="utf-8", errors="replace")

REPO = Path(__file__).parent.parent
SCRIPT_PATH = REPO / "specify.py"

if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


def _load_module():
    spec = importlib.util.spec_from_file_location("specify", SCRIPT_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


specify = _load_module()


class TestSpecifyWorkflow(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.mkdtemp(prefix="specify-")
        self.repo_root = Path(self._tmpdir) / "repo"
        self.repo_root.mkdir(parents=True, exist_ok=True)
        (self.repo_root / ".git").mkdir()

    def tearDown(self):
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _run(self, *args: str):
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            rc = specify.main(list(args))
        return rc, stdout.getvalue(), stderr.getvalue()

    def _create_spec_bundle(self, title: str = "Hosted shell bootstrap"):
        rc, stdout, stderr = self._run(title, "--json", "--repo", str(self.repo_root))
        self.assertEqual(rc, 0, stderr)
        return json.loads(stdout)

    def test_specify_creates_structured_spec_bundle(self):
        payload = self._create_spec_bundle()
        spec_path = Path(payload["spec_path"])
        self.assertTrue(spec_path.exists())
        text = spec_path.read_text(encoding="utf-8")
        self.assertIn("## User Stories", text)
        self.assertIn("### US1 — P1 — Primary user journey", text)
        self.assertIn("- Given", text)
        self.assertIn("- When", text)
        self.assertIn("- Then", text)
        self.assertIn("- FR-001", text)
        self.assertIn("- SC-001", text)
        self.assertEqual(payload["artifacts"]["spec"], "specs/hosted-shell-bootstrap/spec.md")

    def test_plan_generates_architecture_decisions(self):
        self._create_spec_bundle()
        rc, stdout, stderr = self._run("plan", "hosted-shell-bootstrap", "--json", "--repo", str(self.repo_root))
        self.assertEqual(rc, 0, stderr)
        payload = json.loads(stdout)
        plan_path = Path(payload["plan_path"])
        self.assertTrue(plan_path.exists())
        text = plan_path.read_text(encoding="utf-8")
        self.assertIn("## Architecture Decisions", text)
        self.assertIn("AD-001", text)
        self.assertIn("FR-001", text)
        self.assertIn("SC-001", text)

    def test_tasks_generates_t001_checklist_with_parallel_markers(self):
        self._create_spec_bundle()
        rc, stdout, stderr = self._run("tasks", "hosted-shell-bootstrap", "--json", "--repo", str(self.repo_root))
        self.assertEqual(rc, 0, stderr)
        payload = json.loads(stdout)
        tasks_path = Path(payload["tasks_path"])
        self.assertTrue(tasks_path.exists())
        text = tasks_path.read_text(encoding="utf-8")
        self.assertIn("- [ ] T001 [US1]", text)
        self.assertIn("[P] [US1]", text)
        self.assertIn("FR-001", text)
        self.assertIn("SC-001", text)

    def test_plan_requires_existing_spec_bundle(self):
        rc, stdout, stderr = self._run("plan", "missing-spec", "--repo", str(self.repo_root))
        self.assertEqual(rc, 1)
        self.assertEqual(stdout, "")
        self.assertIn("spec.md not found", stderr)

    def test_plan_rejects_mismatched_story_requirement_counts(self):
        payload = self._create_spec_bundle()
        spec_path = Path(payload["spec_path"])
        extra_story = (
            "### US4 — P4 — Extra story\n"
            "- Story: Add one more story.\n"
            "- Given extra scope exists\n"
            "- When the spec is edited manually\n"
            "- Then the renderer should fail cleanly\n\n"
        )
        spec_path.write_text(
            spec_path.read_text(encoding="utf-8").replace(
                "## Functional Requirements\n", extra_story + "## Functional Requirements\n"
            ),
            encoding="utf-8",
        )
        rc, stdout, stderr = self._run("plan", "hosted-shell-bootstrap", "--repo", str(self.repo_root))
        self.assertEqual(rc, 1)
        self.assertEqual(stdout, "")
        self.assertIn("counts must match", stderr)


if __name__ == "__main__":
    unittest.main()
