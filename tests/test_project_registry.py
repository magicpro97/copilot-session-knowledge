#!/usr/bin/env python3
"""
test_project_registry.py — Tests for project-registry.py

Covers:
  - _entry_path() handles both string and dict entries
  - _load_raw_registry() returns empty list on missing/corrupt file
  - _load_project_paths() de-duplicates and filters
  - cmd_add() registers a new project (idempotent)
  - cmd_add() handles richer schema co-existence with legacy strings
  - cmd_remove() removes a registered project
  - cmd_remove() is graceful when path is not registered
  - cmd_list() human-readable output
  - cmd_list() --json output
  - _detect_project_root() .copilot/ detection
  - _detect_project_root() git fallback detection (mocked)
  - main() dispatch: add / remove / list
  - backward compatibility: mixed string+dict registry round-trips
  - _atomic_write_text() no .tmp leak

Run: python tests/test_project_registry.py
"""

import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

if os.name == "nt":
    for _stream in (sys.stdout, sys.stderr):
        if hasattr(_stream, "reconfigure"):
            _stream.reconfigure(encoding="utf-8", errors="replace")

TOOLS_DIR = Path(__file__).parent.parent
REG_PATH = TOOLS_DIR / "project-registry.py"

SCRATCH = TOOLS_DIR / ".test-scratch" / "project-registry-tests"
SCRATCH.mkdir(parents=True, exist_ok=True)


def _load_module(registry_path_override: Path | None = None):
    """Load project-registry as a fresh module, optionally overriding REGISTRY_PATH."""
    spec = importlib.util.spec_from_file_location("_proj_reg", REG_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    if registry_path_override is not None:
        mod.REGISTRY_PATH = registry_path_override
    return mod


PASS = 0
FAIL = 0


def _record(name: str, condition: bool, detail: str = "") -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  ✅ {name}")
    else:
        FAIL += 1
        print(f"  ❌ {name}" + (f" — {detail}" if detail else ""))


class TestEntryPath(unittest.TestCase):
    def setUp(self):
        self.mod = _load_module()

    def test_string_entry(self):
        self.assertEqual(self.mod._entry_path("/some/path"), "/some/path")

    def test_dict_entry(self):
        self.assertEqual(self.mod._entry_path({"path": "/some/path", "name": "x"}), "/some/path")

    def test_dict_missing_path(self):
        self.assertEqual(self.mod._entry_path({"name": "x"}), "")

    def test_none(self):
        self.assertEqual(self.mod._entry_path(None), "")

    def test_integer(self):
        self.assertEqual(self.mod._entry_path(42), "")


class TestLoadRawRegistry(unittest.TestCase):
    def setUp(self):
        self.reg = SCRATCH / "test-raw-registry.json"
        self.mod = _load_module(self.reg)
        if self.reg.exists():
            self.reg.unlink()

    def test_missing_file_returns_empty(self):
        self.assertEqual(self.mod._load_raw_registry(), [])

    def test_empty_projects_key(self):
        self.reg.write_text(json.dumps({"projects": []}), encoding="utf-8")
        self.assertEqual(self.mod._load_raw_registry(), [])

    def test_string_entries(self):
        self.reg.write_text(json.dumps({"projects": ["/a", "/b"]}), encoding="utf-8")
        self.assertEqual(self.mod._load_raw_registry(), ["/a", "/b"])

    def test_dict_entries(self):
        entries = [{"name": "x", "path": "/a", "created_at": "2025-01-01T00:00:00+00:00"}]
        self.reg.write_text(json.dumps({"projects": entries}), encoding="utf-8")
        self.assertEqual(self.mod._load_raw_registry(), entries)

    def test_mixed_entries(self):
        entries = ["/a", {"name": "b", "path": "/b", "created_at": "2025-01-01T00:00:00+00:00"}]
        self.reg.write_text(json.dumps({"projects": entries}), encoding="utf-8")
        self.assertEqual(self.mod._load_raw_registry(), entries)

    def test_corrupt_json_returns_empty(self):
        self.reg.write_text("NOT JSON", encoding="utf-8")
        self.assertEqual(self.mod._load_raw_registry(), [])

    def test_integer_entries_filtered_out(self):
        self.reg.write_text(json.dumps({"projects": ["/a", 42, None]}), encoding="utf-8")
        result = self.mod._load_raw_registry()
        self.assertEqual(result, ["/a"])


class TestLoadProjectPaths(unittest.TestCase):
    def setUp(self):
        self.reg = SCRATCH / "test-paths-registry.json"
        self.mod = _load_module(self.reg)
        if self.reg.exists():
            self.reg.unlink()

    def test_string_entries(self):
        self.reg.write_text(json.dumps({"projects": ["/a", "/b"]}), encoding="utf-8")
        self.assertEqual(self.mod._load_project_paths(), ["/a", "/b"])

    def test_dict_entries_extracted(self):
        entries = [{"name": "x", "path": "/c"}]
        self.reg.write_text(json.dumps({"projects": entries}), encoding="utf-8")
        self.assertEqual(self.mod._load_project_paths(), ["/c"])

    def test_deduplication(self):
        entries = ["/a", "/a", {"path": "/a"}]
        self.reg.write_text(json.dumps({"projects": entries}), encoding="utf-8")
        self.assertEqual(self.mod._load_project_paths(), ["/a"])


class TestCmdAdd(unittest.TestCase):
    def setUp(self):
        self.reg = SCRATCH / "test-add-registry.json"
        self.mod = _load_module(self.reg)
        if self.reg.exists():
            self.reg.unlink()
        # Use a real temp directory so paths resolve correctly on all platforms
        self._td = tempfile.mkdtemp(prefix="sk-add-test-")
        self.proj = Path(self._td) / "alpha"
        self.proj.mkdir()
        self.proj_key = str(self.proj.resolve())

    def tearDown(self):
        import shutil
        shutil.rmtree(self._td, ignore_errors=True)

    def test_add_new_path(self):
        rc = self.mod.cmd_add(self.proj_key, quiet=True)
        self.assertEqual(rc, 0)
        data = json.loads(self.reg.read_text(encoding="utf-8"))
        paths = [self.mod._entry_path(e) for e in data["projects"]]
        self.assertIn(self.proj_key, paths)

    def test_add_idempotent(self):
        self.mod.cmd_add(self.proj_key, quiet=True)
        self.mod.cmd_add(self.proj_key, quiet=True)
        data = json.loads(self.reg.read_text(encoding="utf-8"))
        paths = [self.mod._entry_path(e) for e in data["projects"]]
        self.assertEqual(paths.count(self.proj_key), 1)

    def test_add_creates_rich_entry(self):
        self.mod.cmd_add(self.proj_key, quiet=True)
        data = json.loads(self.reg.read_text(encoding="utf-8"))
        rich = [e for e in data["projects"] if isinstance(e, dict)]
        self.assertTrue(len(rich) >= 1)
        entry = rich[0]
        self.assertIn("name", entry)
        self.assertIn("path", entry)
        self.assertIn("created_at", entry)

    def test_add_preserves_existing_string_entries(self):
        # Pre-populate with a legacy string entry using a real path
        legacy = str(Path(self._td).resolve())
        self.reg.write_text(json.dumps({"projects": [legacy]}), encoding="utf-8")
        self.mod.cmd_add(self.proj_key, quiet=True)
        data = json.loads(self.reg.read_text(encoding="utf-8"))
        paths = [self.mod._entry_path(e) for e in data["projects"]]
        self.assertIn(legacy, paths)
        self.assertIn(self.proj_key, paths)

    def test_add_detects_existing_dict_entry(self):
        # If a dict entry already has the path, don't add again
        entries = [{"name": "x", "path": self.proj_key, "created_at": "2025-01-01T00:00:00+00:00"}]
        self.reg.write_text(json.dumps({"projects": entries}), encoding="utf-8")
        rc = self.mod.cmd_add(self.proj_key, quiet=True)
        self.assertEqual(rc, 0)
        data = json.loads(self.reg.read_text(encoding="utf-8"))
        paths = [self.mod._entry_path(e) for e in data["projects"]]
        self.assertEqual(paths.count(self.proj_key), 1)


class TestCmdRemove(unittest.TestCase):
    def setUp(self):
        self.reg = SCRATCH / "test-remove-registry.json"
        self.mod = _load_module(self.reg)
        # Use real temp dir so path resolution is consistent
        self._td = tempfile.mkdtemp(prefix="sk-remove-test-")
        self.keep_path = str((Path(self._td) / "keep").resolve())
        self.remove_path = str((Path(self._td) / "remove").resolve())
        Path(self.keep_path).mkdir(exist_ok=True)
        Path(self.remove_path).mkdir(exist_ok=True)

    def tearDown(self):
        import shutil
        shutil.rmtree(self._td, ignore_errors=True)

    def _populate(self, entries):
        self.reg.write_text(json.dumps({"projects": entries}), encoding="utf-8")

    def test_remove_string_entry(self):
        self._populate([self.keep_path, self.remove_path])
        rc = self.mod.cmd_remove(self.remove_path, quiet=True)
        self.assertEqual(rc, 0)
        data = json.loads(self.reg.read_text(encoding="utf-8"))
        paths = [self.mod._entry_path(e) for e in data["projects"]]
        self.assertNotIn(self.remove_path, paths)
        self.assertIn(self.keep_path, paths)

    def test_remove_dict_entry(self):
        self._populate([
            {"name": "keep", "path": self.keep_path, "created_at": "2025-01-01T00:00:00+00:00"},
            {"name": "gone", "path": self.remove_path, "created_at": "2025-01-01T00:00:00+00:00"},
        ])
        rc = self.mod.cmd_remove(self.remove_path, quiet=True)
        self.assertEqual(rc, 0)
        data = json.loads(self.reg.read_text(encoding="utf-8"))
        paths = [self.mod._entry_path(e) for e in data["projects"]]
        self.assertNotIn(self.remove_path, paths)
        self.assertIn(self.keep_path, paths)

    def test_remove_not_registered_returns_0(self):
        self._populate([self.keep_path])
        other = str((Path(self._td) / "other").resolve())
        rc = self.mod.cmd_remove(other, quiet=True)
        self.assertEqual(rc, 0)

    def test_remove_empty_registry_returns_0(self):
        self._populate([])
        rc = self.mod.cmd_remove(self.remove_path, quiet=True)
        self.assertEqual(rc, 0)


class TestCmdList(unittest.TestCase):
    def setUp(self):
        self.reg = SCRATCH / "test-list-registry.json"
        self.mod = _load_module(self.reg)

    def test_list_empty(self):
        self.reg.write_text(json.dumps({"projects": []}), encoding="utf-8")
        with patch("builtins.print") as mock_print:
            rc = self.mod.cmd_list()
        self.assertEqual(rc, 0)
        output = " ".join(str(c) for call in mock_print.call_args_list for c in call[0])
        self.assertIn("no projects", output)

    def test_list_string_entries(self):
        self.reg.write_text(json.dumps({"projects": ["/path/a", "/path/b"]}), encoding="utf-8")
        with patch("builtins.print") as mock_print:
            rc = self.mod.cmd_list()
        self.assertEqual(rc, 0)
        output = " ".join(str(c) for call in mock_print.call_args_list for c in call[0])
        self.assertIn("/path/a", output)
        self.assertIn("/path/b", output)

    def test_list_json_output(self):
        self.reg.write_text(json.dumps({"projects": ["/path/a"]}), encoding="utf-8")
        with patch("builtins.print") as mock_print:
            rc = self.mod.cmd_list(json_output=True)
        self.assertEqual(rc, 0)
        output = "".join(str(c) for call in mock_print.call_args_list for c in call[0])
        parsed = json.loads(output)
        self.assertIsInstance(parsed, list)
        self.assertTrue(any(item.get("path") == "/path/a" for item in parsed))

    def test_list_json_empty(self):
        self.reg.write_text(json.dumps({"projects": []}), encoding="utf-8")
        with patch("builtins.print") as mock_print:
            rc = self.mod.cmd_list(json_output=True)
        self.assertEqual(rc, 0)
        output = "".join(str(c) for call in mock_print.call_args_list for c in call[0])
        self.assertEqual(output.strip(), "[]")

    def test_list_deduplicates_human_readable(self):
        """Duplicate paths in the raw registry must appear only once in human output."""
        self.reg.write_text(
            json.dumps({"projects": ["/path/dup", "/path/other", "/path/dup"]}),
            encoding="utf-8",
        )
        with patch("builtins.print") as mock_print:
            rc = self.mod.cmd_list()
        self.assertEqual(rc, 0)
        all_lines = [str(c) for call in mock_print.call_args_list for c in call[0]]
        dup_count = sum(1 for line in all_lines if "/path/dup" in line)
        self.assertEqual(dup_count, 1, f"Expected 1 occurrence of /path/dup, got {dup_count}")
        self.assertTrue(any("/path/other" in line for line in all_lines))

    def test_list_json_deduplicates(self):
        """Duplicate paths in the raw registry must appear only once in --json output."""
        self.reg.write_text(
            json.dumps({"projects": ["/path/dup", "/path/other", "/path/dup"]}),
            encoding="utf-8",
        )
        with patch("builtins.print") as mock_print:
            rc = self.mod.cmd_list(json_output=True)
        self.assertEqual(rc, 0)
        raw = "".join(str(c) for call in mock_print.call_args_list for c in call[0])
        parsed = json.loads(raw)
        paths = [item["path"] for item in parsed]
        self.assertEqual(paths.count("/path/dup"), 1, f"Expected 1 /path/dup in JSON, got {paths}")
        self.assertIn("/path/other", paths)

    def test_list_deduplicates_mixed_entries(self):
        """Duplicate dict+string entries for same path appear only once in output."""
        entries = [
            "/path/x",
            {"name": "x", "path": "/path/x", "created_at": "2024-01-01T00:00:00"},
        ]
        self.reg.write_text(json.dumps({"projects": entries}), encoding="utf-8")
        with patch("builtins.print") as mock_print:
            rc = self.mod.cmd_list()
        self.assertEqual(rc, 0)
        all_lines = [str(c) for call in mock_print.call_args_list for c in call[0]]
        dup_count = sum(1 for line in all_lines if "/path/x" in line)
        self.assertEqual(dup_count, 1, f"Expected 1 occurrence, got {dup_count}: {all_lines}")


class TestDetectProjectRoot(unittest.TestCase):
    def setUp(self):
        self.mod = _load_module()

    def test_detects_copilot_dir(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / ".copilot").mkdir()
            subdir = root / "sub" / "deeper"
            subdir.mkdir(parents=True)
            detected = self.mod._detect_project_root(start=subdir)
            self.assertEqual(detected, root.resolve())

    def test_returns_none_if_no_copilot_no_git(self):
        """Mock both the .copilot dir walk and subprocess so neither fires."""
        with tempfile.TemporaryDirectory() as td:
            isolated = Path(td) / "iso"
            isolated.mkdir()
            # Patch Path.is_dir to return False for any path ending in .copilot
            orig_is_dir = Path.is_dir
            def _fake_is_dir(self_path):
                if self_path.name == ".copilot":
                    return False
                return orig_is_dir(self_path)
            with patch.object(Path, "is_dir", _fake_is_dir):
                with patch.object(self.mod.subprocess, "run") as mock_run:
                    mock_run.return_value = MagicMock(returncode=1, stdout="")
                    detected = self.mod._detect_project_root(start=isolated)
            self.assertIsNone(detected)

    def test_git_fallback(self):
        """With no .copilot dir, falls back to git rev-parse."""
        with tempfile.TemporaryDirectory() as td:
            isolated = Path(td) / "iso"
            isolated.mkdir()
            fake_root = str(isolated.resolve())
            orig_is_dir = Path.is_dir
            def _fake_is_dir(self_path):
                if self_path.name == ".copilot":
                    return False
                return orig_is_dir(self_path)
            with patch.object(Path, "is_dir", _fake_is_dir):
                with patch.object(self.mod.subprocess, "run") as mock_run:
                    mock_run.return_value = MagicMock(returncode=0, stdout=fake_root + "\n")
                    detected = self.mod._detect_project_root(start=isolated)
            self.assertEqual(detected, Path(fake_root).resolve())

    def test_home_copilot_not_treated_as_project_root(self):
        """
        Regression: global ~/.copilot must NOT cause the home directory to be
        detected as a project root when running from a subdirectory of home.

        PR #205 review thread: https://github.com/magicpro97/copilot-session-knowledge/pull/205#discussion_r3243436861
        """
        with tempfile.TemporaryDirectory() as td:
            fake_home = Path(td) / "home"
            fake_home.mkdir()
            # Only fake_home/.copilot "exists" (simulates global ~/.copilot)
            fake_home_copilot = (fake_home / ".copilot").resolve()
            subdir = fake_home / "work" / "myproject"
            subdir.mkdir(parents=True)

            orig_is_dir = Path.is_dir
            def _fake_is_dir(self_path):
                if self_path.name == ".copilot":
                    return self_path.resolve() == fake_home_copilot
                return orig_is_dir(self_path)

            with patch("pathlib.Path.home", return_value=fake_home.resolve()):
                with patch.object(Path, "is_dir", _fake_is_dir):
                    with patch.object(self.mod.subprocess, "run") as mock_run:
                        mock_run.return_value = MagicMock(returncode=1, stdout="")
                        detected = self.mod._detect_project_root(start=subdir)
            self.assertIsNone(
                detected,
                f"home dir should NOT be detected as project root, got: {detected}",
            )

    def test_project_copilot_under_home_is_detected(self):
        """
        A real project-local .copilot/ that lives *inside* the home directory
        (e.g. ~/work/myrepo/.copilot/) must still be detected correctly.
        """
        with tempfile.TemporaryDirectory() as td:
            fake_home = Path(td) / "home"
            fake_home.mkdir()
            fake_home_copilot = (fake_home / ".copilot").resolve()
            project = fake_home / "work" / "myrepo"
            project.mkdir(parents=True)
            project_copilot = (project / ".copilot").resolve()
            subdir = project / "src"
            subdir.mkdir()

            orig_is_dir = Path.is_dir
            def _fake_is_dir(self_path):
                if self_path.name == ".copilot":
                    # Both global and project-local exist; only project-local should win
                    return self_path.resolve() in (fake_home_copilot, project_copilot)
                return orig_is_dir(self_path)

            with patch("pathlib.Path.home", return_value=fake_home.resolve()):
                with patch.object(Path, "is_dir", _fake_is_dir):
                    with patch.object(self.mod.subprocess, "run") as mock_run:
                        mock_run.return_value = MagicMock(returncode=1, stdout="")
                        detected = self.mod._detect_project_root(start=subdir)
            self.assertEqual(
                detected,
                project.resolve(),
                f"project-local .copilot should be detected, got: {detected}",
            )


class TestMainDispatch(unittest.TestCase):
    """Test main() dispatches add/remove/list correctly."""

    def setUp(self):
        self.reg = SCRATCH / "test-main-registry.json"
        self.mod = _load_module(self.reg)
        if self.reg.exists():
            self.reg.unlink()
        self._td = tempfile.mkdtemp(prefix="sk-main-test-")
        self.proj_key = str((Path(self._td) / "proj").resolve())
        Path(self.proj_key).mkdir(exist_ok=True)

    def tearDown(self):
        import shutil
        shutil.rmtree(self._td, ignore_errors=True)

    def test_add_via_main(self):
        rc = self.mod.main(["add", self.proj_key])
        self.assertEqual(rc, 0)
        data = json.loads(self.reg.read_text(encoding="utf-8"))
        paths = [self.mod._entry_path(e) for e in data["projects"]]
        self.assertIn(self.proj_key, paths)

    def test_remove_via_main(self):
        self.reg.write_text(json.dumps({"projects": [self.proj_key]}), encoding="utf-8")
        rc = self.mod.main(["remove", self.proj_key])
        self.assertEqual(rc, 0)
        data = json.loads(self.reg.read_text(encoding="utf-8"))
        paths = [self.mod._entry_path(e) for e in data["projects"]]
        self.assertNotIn(self.proj_key, paths)

    def test_list_via_main(self):
        self.reg.write_text(json.dumps({"projects": [self.proj_key]}), encoding="utf-8")
        with patch("builtins.print") as mock_print:
            rc = self.mod.main(["list"])
        self.assertEqual(rc, 0)
        output = " ".join(str(c) for call in mock_print.call_args_list for c in call[0])
        self.assertIn(self.proj_key, output)

    def test_no_args_returns_0(self):
        with patch("builtins.print"):
            rc = self.mod.main([])
        self.assertEqual(rc, 0)

    def test_unknown_subcommand_exits_nonzero(self):
        with self.assertRaises(SystemExit) as cm:
            self.mod.main(["nonexistent"])
        self.assertNotEqual(cm.exception.code, 0)


class TestBackwardCompatibility(unittest.TestCase):
    """Mixed-mode registry: string and dict entries co-existing."""

    def setUp(self):
        self.reg = SCRATCH / "test-compat-registry.json"
        self.mod = _load_module(self.reg)
        self._td = tempfile.mkdtemp(prefix="sk-compat-test-")
        self.legacy_path = str((Path(self._td) / "legacy").resolve())
        self.rich_path = str((Path(self._td) / "rich").resolve())
        self.new_path = str((Path(self._td) / "new").resolve())
        Path(self.legacy_path).mkdir(exist_ok=True)
        Path(self.rich_path).mkdir(exist_ok=True)
        Path(self.new_path).mkdir(exist_ok=True)

    def tearDown(self):
        import shutil
        shutil.rmtree(self._td, ignore_errors=True)

    def test_mixed_registry_round_trip(self):
        # Simulate a registry with one legacy string and one rich dict entry
        mixed = [
            self.legacy_path,
            {"name": "rich", "path": self.rich_path, "created_at": "2025-01-01T00:00:00+00:00"},
        ]
        self.reg.write_text(json.dumps({"projects": mixed}), encoding="utf-8")

        # add() should preserve both existing entries and append new one
        rc = self.mod.cmd_add(self.new_path, quiet=True)
        self.assertEqual(rc, 0)

        data = json.loads(self.reg.read_text(encoding="utf-8"))
        paths = [self.mod._entry_path(e) for e in data["projects"]]
        self.assertIn(self.legacy_path, paths)
        self.assertIn(self.rich_path, paths)
        self.assertIn(self.new_path, paths)

    def test_add_does_not_duplicate_legacy_path(self):
        self.reg.write_text(json.dumps({"projects": [self.legacy_path]}), encoding="utf-8")
        self.mod.cmd_add(self.legacy_path, quiet=True)
        data = json.loads(self.reg.read_text(encoding="utf-8"))
        paths = [self.mod._entry_path(e) for e in data["projects"]]
        self.assertEqual(paths.count(self.legacy_path), 1)

    def test_list_shows_both_formats(self):
        mixed = [
            self.legacy_path,
            {"name": "dict-proj", "path": self.rich_path, "created_at": "2025-01-01T00:00:00+00:00"},
        ]
        self.reg.write_text(json.dumps({"projects": mixed}), encoding="utf-8")
        with patch("builtins.print") as mock_print:
            rc = self.mod.cmd_list()
        self.assertEqual(rc, 0)
        output = " ".join(str(c) for call in mock_print.call_args_list for c in call[0])
        self.assertIn(self.legacy_path, output)
        self.assertIn(self.rich_path, output)


class TestAtomicWrite(unittest.TestCase):
    def setUp(self):
        self.mod = _load_module()

    def test_creates_file(self):
        target = SCRATCH / "atomic-write-test.json"
        if target.exists():
            target.unlink()
        self.mod._atomic_write_text(target, '{"test": true}')
        self.assertTrue(target.exists())
        self.assertEqual(json.loads(target.read_text(encoding="utf-8")), {"test": True})

    def test_no_tmp_file_left(self):
        target = SCRATCH / "atomic-no-tmp.json"
        self.mod._atomic_write_text(target, "hello")
        tmp = target.with_suffix(target.suffix + ".tmp")
        self.assertFalse(tmp.exists(), "temp file should not be left behind")

    def test_overwrites_existing(self):
        target = SCRATCH / "atomic-overwrite.json"
        target.write_text("old", encoding="utf-8")
        self.mod._atomic_write_text(target, "new")
        self.assertEqual(target.read_text(encoding="utf-8"), "new")


if __name__ == "__main__":
    unittest.main(verbosity=2)
