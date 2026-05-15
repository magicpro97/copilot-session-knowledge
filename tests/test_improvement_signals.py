#!/usr/bin/env python3
"""
test_improvement_signals.py — Regression tests for improvement-signals.py.

Covers:
  - Table creation via _open_db
  - record: accepted/rejected signal_type values, required fields, consumed=0 default
  - list: filtering by consumed, type, limit
  - consume: single ID, --all, with type filter
  - stats: counts and by_type breakdown
  - DB-absent edge cases (fail-open)
  - CLI argument parsing (--help exits 0, subcommands dispatch correctly)

Run: python tests/test_improvement_signals.py
"""

import importlib.util
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

if os.name == "nt":
    for _stream in (sys.stdout, sys.stderr):
        if hasattr(_stream, "reconfigure"):
            _stream.reconfigure(encoding="utf-8", errors="replace")

REPO = Path(__file__).parent.parent
SCRIPT_PATH = REPO / "improvement-signals.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("improvement_signals", str(SCRIPT_PATH))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


mod = _load_module()


class _TmpDB:
    """Context manager that yields a temp Path with no DB file (or an empty one)."""

    def __init__(self, create: bool = False):
        self._dir = None
        self._create = create

    def __enter__(self):
        self._dir = tempfile.mkdtemp()
        p = Path(self._dir) / "knowledge.db"
        if self._create:
            db = mod._open_db(p)
            db.close()
        return p

    def __exit__(self, *_):
        import shutil
        if self._dir:
            shutil.rmtree(self._dir, ignore_errors=True)


class TestOpenDb(unittest.TestCase):
    def test_creates_table(self):
        with _TmpDB() as db_path:
            db = mod._open_db(db_path)
            row = db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='improvement_signals'"
            ).fetchone()
            db.close()
            self.assertIsNotNone(row)

    def test_idempotent(self):
        with _TmpDB(create=True) as db_path:
            db = mod._open_db(db_path)  # second open, table already exists
            count = db.execute("SELECT COUNT(*) FROM improvement_signals").fetchone()[0]
            db.close()
            self.assertEqual(count, 0)


class TestRecord(unittest.TestCase):
    def test_record_creates_row(self):
        with _TmpDB() as db_path:
            rc = mod.cmd_record(
                _args(type="missed_match", query="docker run fails", skill="", session_id="s1"),
                db_path,
            )
            self.assertEqual(rc, 0)
            db = mod._open_db(db_path)
            rows = db.execute("SELECT * FROM improvement_signals").fetchall()
            db.close()
            self.assertEqual(len(rows), 1)
            r = rows[0]
            self.assertEqual(r["signal_type"], "missed_match")
            self.assertEqual(r["query"], "docker run fails")
            self.assertEqual(r["consumed"], 0)
            self.assertEqual(r["session_id"], "s1")

    def test_record_with_skill(self):
        with _TmpDB() as db_path:
            rc = mod.cmd_record(
                _args(type="wrong_skill", query="k8s deploy", skill="kubernetes", session_id=""),
                db_path,
            )
            self.assertEqual(rc, 0)
            db = mod._open_db(db_path)
            r = db.execute("SELECT * FROM improvement_signals").fetchone()
            db.close()
            self.assertEqual(r["mentioned_skill"], "kubernetes")

    def test_record_outdated_skill(self):
        with _TmpDB() as db_path:
            rc = mod.cmd_record(
                _args(type="outdated_skill", query="old rust patterns", skill="rust-dev", session_id=""),
                db_path,
            )
            self.assertEqual(rc, 0)

    def test_record_invalid_type_returns_2(self):
        with _TmpDB() as db_path:
            rc = mod.cmd_record(
                _args(type="bogus_type", query="something", skill="", session_id=""),
                db_path,
            )
            self.assertEqual(rc, 2)

    def test_record_empty_query_returns_2(self):
        with _TmpDB() as db_path:
            rc = mod.cmd_record(
                _args(type="missed_match", query="", skill="", session_id=""),
                db_path,
            )
            self.assertEqual(rc, 2)

    def test_record_all_valid_types(self):
        with _TmpDB() as db_path:
            for st in mod.VALID_SIGNAL_TYPES:
                rc = mod.cmd_record(
                    _args(type=st, query=f"query for {st}", skill="s", session_id=""),
                    db_path,
                )
                self.assertEqual(rc, 0, f"Failed for type={st}")
            db = mod._open_db(db_path)
            count = db.execute("SELECT COUNT(*) FROM improvement_signals").fetchone()[0]
            db.close()
            self.assertEqual(count, 3)


class TestList(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.mkdtemp()
        self.db_path = Path(self._dir) / "knowledge.db"
        db = mod._open_db(self.db_path)
        db.execute(
            "INSERT INTO improvement_signals (query, signal_type, mentioned_skill, consumed) VALUES (?,?,?,?)",
            ("q1", "missed_match", "sk1", 0),
        )
        db.execute(
            "INSERT INTO improvement_signals (query, signal_type, mentioned_skill, consumed) VALUES (?,?,?,?)",
            ("q2", "wrong_skill", "sk2", 0),
        )
        db.execute(
            "INSERT INTO improvement_signals (query, signal_type, mentioned_skill, consumed) VALUES (?,?,?,?)",
            ("q3", "missed_match", "sk3", 1),
        )
        db.commit()
        db.close()

    def tearDown(self):
        import shutil
        shutil.rmtree(self._dir, ignore_errors=True)

    def test_list_unconsumed_default(self):
        with capture_stdout() as out:
            rc = mod.cmd_list(_list_args(consumed=False), self.db_path)
        self.assertEqual(rc, 0)
        self.assertIn("q1", out.getvalue())
        self.assertIn("q2", out.getvalue())
        self.assertNotIn("q3", out.getvalue())

    def test_list_consumed(self):
        with capture_stdout() as out:
            rc = mod.cmd_list(_list_args(consumed=True), self.db_path)
        self.assertEqual(rc, 0)
        self.assertIn("q3", out.getvalue())
        self.assertNotIn("q1", out.getvalue())

    def test_list_filter_type(self):
        with capture_stdout() as out:
            rc = mod.cmd_list(_list_args(consumed=False, filter_type="wrong_skill"), self.db_path)
        self.assertEqual(rc, 0)
        self.assertIn("q2", out.getvalue())
        self.assertNotIn("q1", out.getvalue())

    def test_list_json(self):
        with capture_stdout() as out:
            rc = mod.cmd_list(_list_args(consumed=False, output_format="json"), self.db_path)
        self.assertEqual(rc, 0)
        data = json.loads(out.getvalue())
        self.assertIn("signals", data)
        self.assertEqual(len(data["signals"]), 2)

    def test_list_no_db(self):
        missing = Path(self._dir) / "missing.db"
        with capture_stdout():
            rc = mod.cmd_list(_list_args(), missing)
        self.assertEqual(rc, 0)


class TestConsume(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.mkdtemp()
        self.db_path = Path(self._dir) / "knowledge.db"
        db = mod._open_db(self.db_path)
        for i in range(4):
            db.execute(
                "INSERT INTO improvement_signals (query, signal_type, mentioned_skill, consumed) VALUES (?,?,?,?)",
                (f"q{i}", "missed_match" if i % 2 == 0 else "wrong_skill", "", 0),
            )
        db.commit()
        db.close()

    def tearDown(self):
        import shutil
        shutil.rmtree(self._dir, ignore_errors=True)

    def _get_consumed_count(self):
        db = mod._open_db(self.db_path)
        count = db.execute("SELECT COUNT(*) FROM improvement_signals WHERE consumed=1").fetchone()[0]
        db.close()
        return count

    def test_consume_single_id(self):
        with capture_stdout():
            rc = mod.cmd_consume(_consume_args(id=1), self.db_path)
        self.assertEqual(rc, 0)
        self.assertEqual(self._get_consumed_count(), 1)

    def test_consume_all(self):
        with capture_stdout():
            rc = mod.cmd_consume(_consume_args(all_flag=True), self.db_path)
        self.assertEqual(rc, 0)
        self.assertEqual(self._get_consumed_count(), 4)

    def test_consume_all_with_type(self):
        with capture_stdout():
            rc = mod.cmd_consume(_consume_args(all_flag=True, filter_type="missed_match"), self.db_path)
        self.assertEqual(rc, 0)
        # 2 missed_match rows (id 1 and 3 → q0, q2)
        self.assertEqual(self._get_consumed_count(), 2)

    def test_consume_unknown_id_returns_1(self):
        with capture_stdout():
            rc = mod.cmd_consume(_consume_args(id=9999), self.db_path)
        self.assertEqual(rc, 1)

    def test_consume_missing_both_id_and_all_returns_2(self):
        with capture_stdout():
            rc = mod.cmd_consume(_consume_args(), self.db_path)
        self.assertEqual(rc, 2)

    def test_consumed_rows_not_in_list(self):
        with capture_stdout():
            mod.cmd_consume(_consume_args(id=1), self.db_path)
        with capture_stdout() as out:
            mod.cmd_list(_list_args(consumed=False, output_format="json"), self.db_path)
        data = json.loads(out.getvalue())
        ids = [r["id"] for r in data["signals"]]
        self.assertNotIn(1, ids)


class TestStats(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.mkdtemp()
        self.db_path = Path(self._dir) / "knowledge.db"
        db = mod._open_db(self.db_path)
        for st in ("missed_match", "missed_match", "wrong_skill"):
            db.execute(
                "INSERT INTO improvement_signals (query, signal_type, mentioned_skill, consumed) VALUES (?,?,?,?)",
                (f"q-{st}", st, "", 0),
            )
        db.execute(
            "INSERT INTO improvement_signals (query, signal_type, mentioned_skill, consumed) VALUES (?,?,?,?)",
            ("consumed-q", "outdated_skill", "", 1),
        )
        db.commit()
        db.close()

    def tearDown(self):
        import shutil
        shutil.rmtree(self._dir, ignore_errors=True)

    def test_stats_json(self):
        with capture_stdout() as out:
            rc = mod.cmd_stats(_stats_args(output_format="json"), self.db_path)
        self.assertEqual(rc, 0)
        data = json.loads(out.getvalue())
        self.assertEqual(data["total"], 4)
        self.assertEqual(data["unconsumed"], 3)
        self.assertEqual(data["consumed"], 1)
        self.assertEqual(data["by_type"]["missed_match"], 2)

    def test_stats_no_db(self):
        missing = Path(self._dir) / "missing.db"
        with capture_stdout() as out:
            rc = mod.cmd_stats(_stats_args(output_format="json"), missing)
        self.assertEqual(rc, 0)
        data = json.loads(out.getvalue())
        self.assertFalse(data["db_exists"])


class TestMainDispatch(unittest.TestCase):
    def test_help_exits_0(self):
        with self.assertRaises(SystemExit) as cm:
            mod.main(["--help"])
        self.assertEqual(cm.exception.code, 0)

    def test_no_subcommand_prints_help(self):
        with capture_stdout():
            rc = mod.main([])
        self.assertEqual(rc, 0)

    def test_record_subcommand_dispatches(self):
        with _TmpDB() as db_path:
            with capture_stdout():
                rc = mod.main(["--db", str(db_path), "record", "--query", "test q", "--type", "missed_match"])
            self.assertEqual(rc, 0)

    def test_list_subcommand_dispatches(self):
        with _TmpDB(create=True) as db_path:
            with capture_stdout():
                rc = mod.main(["--db", str(db_path), "list"])
            self.assertEqual(rc, 0)

    def test_stats_subcommand_dispatches(self):
        with _TmpDB(create=True) as db_path:
            with capture_stdout():
                rc = mod.main(["--db", str(db_path), "stats", "--format", "json"])
            self.assertEqual(rc, 0)

    def test_consume_subcommand_dispatches(self):
        with _TmpDB() as db_path:
            mod.main(["--db", str(db_path), "record", "--query", "q", "--type", "missed_match"])
            with capture_stdout():
                rc = mod.main(["--db", str(db_path), "consume", "--id", "1"])
            self.assertEqual(rc, 0)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

import contextlib
import io


@contextlib.contextmanager
def capture_stdout():
    buf = io.StringIO()
    with patch("builtins.print", side_effect=lambda *a, **kw: buf.write(" ".join(str(x) for x in a) + "\n")):
        yield buf


class _SimpleNamespace:
    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)


def _args(**kw):
    defaults = dict(type="missed_match", query="q", skill="", session_id="")
    defaults.update(kw)
    return _SimpleNamespace(**defaults)


def _list_args(consumed=False, filter_type=None, limit=50, output_format="text"):
    ns = _SimpleNamespace(consumed=consumed, type=filter_type, limit=limit, output_format=output_format)
    return ns


def _consume_args(id=None, all_flag=False, filter_type=None):
    ns = _SimpleNamespace(id=id, all=all_flag, type=filter_type)
    return ns


def _stats_args(output_format="text"):
    return _SimpleNamespace(output_format=output_format)


if __name__ == "__main__":
    unittest.main(verbosity=2)
