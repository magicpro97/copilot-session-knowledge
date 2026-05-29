#!/usr/bin/env python3
"""
test_taxonomy.py — Tests for taxonomy.py

Covers:
  - _load_taxonomy: returns DEFAULT_TAXONOMY when no custom file
  - cmd_list: prints a table of wing/room counts
  - cmd_validate: exits 0 when all combos are known
  - cmd_validate: exits 1 when unknown wing/room combos exist
  - cmd_add: appends to custom registry file

Run: python tests/test_taxonomy.py
"""

import importlib.util
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")

TOOLS_DIR = Path(__file__).parent.parent
SCRIPT_PATH = TOOLS_DIR / "taxonomy.py"


def _load_module(db_path: str, registry_path: str | None = None):
    """Load taxonomy module with patched paths."""
    spec = importlib.util.spec_from_file_location("taxonomy", SCRIPT_PATH)
    mod = importlib.util.module_from_spec(spec)
    mod.__spec__ = spec
    spec.loader.exec_module(mod)
    mod.DB_PATH = Path(db_path)
    if registry_path is not None:
        mod.CUSTOM_REGISTRY_PATH = Path(registry_path)
    return mod


def _make_db(entries):
    """Create a temp SQLite DB with knowledge_entries rows. Returns path string."""
    fd, path = tempfile.mkstemp(suffix=".db", prefix="taxonomy_test_", dir=TOOLS_DIR)
    os.close(fd)
    conn = sqlite3.connect(path)
    conn.execute(
        """CREATE TABLE knowledge_entries (
            id INTEGER PRIMARY KEY,
            wing TEXT DEFAULT '',
            room TEXT DEFAULT '',
            deleted_at TEXT DEFAULT NULL
        )"""
    )
    conn.executemany(
        "INSERT INTO knowledge_entries (wing, room) VALUES (?, ?)",
        entries,
    )
    conn.commit()
    conn.close()
    return path


class TestLoadTaxonomy(unittest.TestCase):
    def test_defaults_present(self):
        mod = _load_module(":memory:", registry_path="/nonexistent/taxonomy.json")
        tx = mod._load_taxonomy()
        self.assertIn("backend", tx)
        self.assertIn("shared", tx)
        self.assertIn("api", tx["backend"])

    def test_custom_registry_merged(self):
        fd, reg_path = tempfile.mkstemp(suffix=".json", prefix="txreg_", dir=TOOLS_DIR)
        os.close(fd)
        try:
            Path(reg_path).write_text(json.dumps({"custom-wing": ["custom-room"]}), encoding="utf-8")
            mod = _load_module(":memory:", registry_path=reg_path)
            tx = mod._load_taxonomy()
            self.assertIn("custom-wing", tx)
            self.assertIn("custom-room", tx["custom-wing"])
            self.assertIn("backend", tx)
        finally:
            Path(reg_path).unlink(missing_ok=True)


class TestCmdList(unittest.TestCase):
    def test_list_prints_table(self):
        db_path = _make_db(
            [
                ("backend", "api"),
                ("backend", "api"),
                ("shared", "docs"),
            ]
        )
        try:
            mod = _load_module(db_path, registry_path="/nonexistent/taxonomy.json")
            import io
            from contextlib import redirect_stdout

            buf = io.StringIO()
            with redirect_stdout(buf):
                mod.cmd_list()
            output = buf.getvalue()
            self.assertIn("backend", output)
            self.assertIn("api", output)
            self.assertIn("2", output)
            self.assertIn("shared", output)
        finally:
            Path(db_path).unlink(missing_ok=True)

    def test_list_empty(self):
        db_path = _make_db([])
        try:
            mod = _load_module(db_path, registry_path="/nonexistent/taxonomy.json")
            import io
            from contextlib import redirect_stdout

            buf = io.StringIO()
            with redirect_stdout(buf):
                mod.cmd_list()
            self.assertIn("No entries", buf.getvalue())
        finally:
            Path(db_path).unlink(missing_ok=True)


class TestCmdValidate(unittest.TestCase):
    def test_validate_all_known_exits_0(self):
        db_path = _make_db(
            [
                ("backend", "api"),
                ("shared", "docs"),
                ("devops", "ci"),
            ]
        )
        try:
            mod = _load_module(db_path, registry_path="/nonexistent/taxonomy.json")
            try:
                mod.cmd_validate()
            except SystemExit as e:
                self.fail(f"cmd_validate raised SystemExit({e.code}) for known combos")
        finally:
            Path(db_path).unlink(missing_ok=True)

    def test_validate_unknown_wing_exits_1(self):
        db_path = _make_db(
            [
                ("unknown-wing", "some-room"),
                ("another-unknown", "stuff"),
            ]
        )
        try:
            mod = _load_module(db_path, registry_path="/nonexistent/taxonomy.json")
            with self.assertRaises(SystemExit) as ctx:
                mod.cmd_validate()
            self.assertEqual(ctx.exception.code, 1)
        finally:
            Path(db_path).unlink(missing_ok=True)

    def test_validate_mixed_exits_1(self):
        db_path = _make_db(
            [
                ("backend", "api"),
                ("mystery-wing", "mystery-room"),
            ]
        )
        try:
            mod = _load_module(db_path, registry_path="/nonexistent/taxonomy.json")
            with self.assertRaises(SystemExit) as ctx:
                mod.cmd_validate()
            self.assertEqual(ctx.exception.code, 1)
        finally:
            Path(db_path).unlink(missing_ok=True)

    def test_validate_empty_wing_room_skipped(self):
        db_path = _make_db([("", ""), ("", "")])
        try:
            mod = _load_module(db_path, registry_path="/nonexistent/taxonomy.json")
            try:
                mod.cmd_validate()
            except SystemExit as e:
                self.fail(f"Empty wing/room should be allowed, got SystemExit({e.code})")
        finally:
            Path(db_path).unlink(missing_ok=True)

    def test_validate_five_unknown_combos(self):
        db_path = _make_db(
            [
                ("wing-a", "room-1"),
                ("wing-b", "room-2"),
                ("wing-c", "room-3"),
                ("wing-d", "room-4"),
                ("wing-e", "room-5"),
            ]
        )
        try:
            mod = _load_module(db_path, registry_path="/nonexistent/taxonomy.json")
            with self.assertRaises(SystemExit) as ctx:
                mod.cmd_validate()
            self.assertEqual(ctx.exception.code, 1)
        finally:
            Path(db_path).unlink(missing_ok=True)


class TestCmdAdd(unittest.TestCase):
    def test_add_new_wing_room(self):
        fd, reg_path = tempfile.mkstemp(suffix=".json", prefix="txreg_add_", dir=TOOLS_DIR)
        os.close(fd)
        Path(reg_path).unlink()
        try:
            mod = _load_module(":memory:", registry_path=reg_path)
            mod.cmd_add("new-wing", "new-room")
            data = json.loads(Path(reg_path).read_text(encoding="utf-8"))
            self.assertIn("new-wing", data)
            self.assertIn("new-room", data["new-wing"])
        finally:
            Path(reg_path).unlink(missing_ok=True)

    def test_add_duplicate_is_idempotent(self):
        fd, reg_path = tempfile.mkstemp(suffix=".json", prefix="txreg_dup_", dir=TOOLS_DIR)
        os.close(fd)
        Path(reg_path).unlink()
        try:
            mod = _load_module(":memory:", registry_path=reg_path)
            mod.cmd_add("mywing", "myroom")
            mod.cmd_add("mywing", "myroom")
            data = json.loads(Path(reg_path).read_text(encoding="utf-8"))
            self.assertEqual(data["mywing"].count("myroom"), 1)
        finally:
            Path(reg_path).unlink(missing_ok=True)


class TestScriptExists(unittest.TestCase):
    def test_script_file_exists(self):
        self.assertTrue(SCRIPT_PATH.is_file(), f"{SCRIPT_PATH} does not exist")

    def test_script_parses(self):
        import ast

        src = SCRIPT_PATH.read_text(encoding="utf-8")
        try:
            ast.parse(src)
        except SyntaxError as e:
            self.fail(f"taxonomy.py has syntax error: {e}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
