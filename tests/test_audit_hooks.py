#!/usr/bin/env python3
"""
test_audit_hooks.py — Focused tests for audit-hooks.py.

Covers:
  - _load_entries: empty file, missing file, valid entries, malformed lines
  - _classify: each decision value
  - _global_summary: counts and rates
  - _per_hook_metrics: ranking, rate computation, top-N cap
  - _trend_analysis: day bucketing and deny_rate
  - main(): no-entries → exit 1; with entries → exit 0
  - main(): --json flag emits valid JSON with expected keys
  - main(): --days filter
  - main(): --top N cap
  - script is importable and audit-hooks.py exists in tools dir

Run: python tests/test_audit_hooks.py
"""

import importlib.util
import json
import os
import sys
import time
import unittest
from pathlib import Path

if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")

TOOLS_DIR = Path(__file__).parent.parent
SCRIPT_PATH = TOOLS_DIR / "audit-hooks.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("audit_hooks", SCRIPT_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ah = _load_module()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_entry(decision: str, rule: str = "test-rule", event: str = "preToolUse",
                tool: str = "edit", ts: float | None = None) -> dict:
    return {
        "ts": ts if ts is not None else time.time(),
        "event": event,
        "tool": tool,
        "rule": rule,
        "decision": decision,
        "detail": "",
    }


def _write_jsonl(path: Path, entries: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        for e in entries:
            fh.write(json.dumps(e) + "\n")


# ---------------------------------------------------------------------------
# Existence check
# ---------------------------------------------------------------------------


class TestScriptExists(unittest.TestCase):
    def test_audit_hooks_py_exists(self):
        self.assertTrue(
            SCRIPT_PATH.exists(),
            f"audit-hooks.py not found at {SCRIPT_PATH}",
        )


# ---------------------------------------------------------------------------
# _load_entries
# ---------------------------------------------------------------------------


class TestLoadEntries(unittest.TestCase):
    def setUp(self):
        import tempfile
        self._tmpdir = tempfile.mkdtemp(prefix="ah-test-")
        self._audit = Path(self._tmpdir) / "audit.jsonl"

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_missing_file_returns_empty(self):
        result = ah._load_entries(Path(self._tmpdir) / "missing.jsonl")
        self.assertEqual(result, [])

    def test_empty_file_returns_empty(self):
        self._audit.write_text("", encoding="utf-8")
        result = ah._load_entries(self._audit)
        self.assertEqual(result, [])

    def test_valid_entries_loaded(self):
        entries = [_make_entry("allow"), _make_entry("deny")]
        _write_jsonl(self._audit, entries)
        result = ah._load_entries(self._audit)
        self.assertEqual(len(result), 2)

    def test_malformed_lines_skipped(self):
        self._audit.write_text(
            '{"decision":"allow","ts":1}\nnot json\n{"decision":"deny","ts":2}\n',
            encoding="utf-8",
        )
        result = ah._load_entries(self._audit)
        self.assertEqual(len(result), 2)

    def test_blank_lines_skipped(self):
        self._audit.write_text(
            '\n{"decision":"allow","ts":1}\n\n\n{"decision":"deny","ts":2}\n\n',
            encoding="utf-8",
        )
        result = ah._load_entries(self._audit)
        self.assertEqual(len(result), 2)

    def test_days_filter_excludes_old_entries(self):
        old_ts = time.time() - 10 * 86_400  # 10 days ago
        new_ts = time.time() - 1 * 86_400   # 1 day ago
        entries = [
            _make_entry("allow", ts=old_ts),
            _make_entry("deny", ts=new_ts),
        ]
        _write_jsonl(self._audit, entries)
        result = ah._load_entries(self._audit, days=5)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["decision"], "deny")

    def test_days_none_returns_all(self):
        old_ts = time.time() - 100 * 86_400
        entries = [_make_entry("allow", ts=old_ts), _make_entry("deny")]
        _write_jsonl(self._audit, entries)
        result = ah._load_entries(self._audit, days=None)
        self.assertEqual(len(result), 2)

    def test_large_log_days_reads_from_tail(self):
        """--days must find recent entries even when total lines exceed the cap."""
        original_cap = ah._AUDIT_MAX_LINES
        ah._AUDIT_MAX_LINES = 10  # lower cap so 20-entry log triggers the old bug
        try:
            old_ts = time.time() - 20 * 86_400   # 20 days ago — outside --days 5
            new_ts = time.time() - 1 * 86_400    # 1 day ago  — inside  --days 5
            # 15 old entries (would fill & overflow old head-read cap of 10)
            # followed by 5 new entries at the tail
            entries = (
                [_make_entry("allow", ts=old_ts)] * 15
                + [_make_entry("deny", ts=new_ts)] * 5
            )
            _write_jsonl(self._audit, entries)
            result = ah._load_entries(self._audit, days=5)
            self.assertEqual(
                len(result), 5,
                "Tail-read must surface 5 recent entries even when log exceeds safety cap",
            )
            self.assertTrue(
                all(e["decision"] == "deny" for e in result),
                "All returned entries should be the recent 'deny' entries",
            )
        finally:
            ah._AUDIT_MAX_LINES = original_cap

    def test_large_log_no_days_returns_tail(self):
        """Without --days, safety cap keeps the most-recent (tail) entries."""
        original_cap = ah._AUDIT_MAX_LINES
        ah._AUDIT_MAX_LINES = 5
        try:
            old_ts = time.time() - 30 * 86_400
            new_ts = time.time()
            # 8 old + 5 new; cap=5 → should keep the 5 newest
            entries = (
                [_make_entry("allow", ts=old_ts)] * 8
                + [_make_entry("deny", ts=new_ts)] * 5
            )
            _write_jsonl(self._audit, entries)
            result = ah._load_entries(self._audit, days=None)
            self.assertEqual(len(result), 5)
            self.assertTrue(all(e["decision"] == "deny" for e in result))
        finally:
            ah._AUDIT_MAX_LINES = original_cap


# ---------------------------------------------------------------------------
# _classify
# ---------------------------------------------------------------------------


class TestClassify(unittest.TestCase):
    def test_deny_is_useful_block(self):
        self.assertEqual(ah._classify("deny"), "useful-block")

    def test_deny_dry_is_false_positive(self):
        self.assertEqual(ah._classify("deny-dry"), "false-positive")

    def test_allow_is_other(self):
        self.assertEqual(ah._classify("allow"), "other")

    def test_empty_is_other(self):
        self.assertEqual(ah._classify(""), "other")

    def test_parse_error_is_other(self):
        self.assertEqual(ah._classify("parse-error"), "other")


# ---------------------------------------------------------------------------
# _global_summary
# ---------------------------------------------------------------------------


class TestGlobalSummary(unittest.TestCase):
    def test_empty_entries(self):
        s = ah._global_summary([])
        self.assertEqual(s["total_entries"], 0)
        self.assertEqual(s["useful_block_count"], 0)
        self.assertEqual(s["deny_rate_pct"], 0.0)

    def test_all_allow(self):
        entries = [_make_entry("allow")] * 5
        s = ah._global_summary(entries)
        self.assertEqual(s["total_entries"], 5)
        self.assertEqual(s["useful_block_count"], 0)
        self.assertEqual(s["deny_rate_pct"], 0.0)
        self.assertIsNone(s["useful_block_rate"])

    def test_mixed_decisions(self):
        entries = (
            [_make_entry("deny")] * 3
            + [_make_entry("deny-dry")] * 1
            + [_make_entry("allow")] * 6
        )
        s = ah._global_summary(entries)
        self.assertEqual(s["total_entries"], 10)
        self.assertEqual(s["useful_block_count"], 3)
        self.assertEqual(s["false_positive_count"], 1)
        self.assertAlmostEqual(s["deny_rate_pct"], 30.0)
        self.assertAlmostEqual(s["fp_rate_pct"], 10.0)
        # useful_block_rate = 3 / (3+1) * 100 = 75.0
        self.assertAlmostEqual(s["useful_block_rate"], 75.0)

    def test_deny_only(self):
        entries = [_make_entry("deny")] * 4
        s = ah._global_summary(entries)
        self.assertEqual(s["useful_block_count"], 4)
        self.assertEqual(s["false_positive_count"], 0)
        self.assertEqual(s["deny_rate_pct"], 100.0)
        # no FPs so useful_block_rate = 100%
        self.assertAlmostEqual(s["useful_block_rate"], 100.0)


# ---------------------------------------------------------------------------
# _per_hook_metrics
# ---------------------------------------------------------------------------


class TestPerHookMetrics(unittest.TestCase):
    def test_empty_entries(self):
        rows = ah._per_hook_metrics([])
        self.assertEqual(rows, [])

    def test_single_rule(self):
        entries = (
            [_make_entry("deny", rule="rule-a")] * 2
            + [_make_entry("deny-dry", rule="rule-a")] * 1
            + [_make_entry("allow", rule="rule-a")] * 7
        )
        rows = ah._per_hook_metrics(entries)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["rule"], "rule-a")
        self.assertEqual(row["fire_count"], 10)
        self.assertEqual(row["block_count"], 2)
        self.assertEqual(row["fp_count"], 1)
        self.assertAlmostEqual(row["block_rate_pct"], 20.0)
        # useful_block_rate = 2/(2+1)*100 = 66.7
        self.assertAlmostEqual(row["useful_block_rate"], 66.7, places=0)

    def test_sorted_by_fire_count_descending(self):
        entries = (
            [_make_entry("allow", rule="rare")] * 1
            + [_make_entry("allow", rule="common")] * 9
        )
        rows = ah._per_hook_metrics(entries)
        self.assertEqual(rows[0]["rule"], "common")
        self.assertEqual(rows[1]["rule"], "rare")

    def test_top_n_cap(self):
        entries = [_make_entry("allow", rule=f"rule-{i}") for i in range(20)]
        rows = ah._per_hook_metrics(entries, top_n=5)
        self.assertEqual(len(rows), 5)

    def test_no_rule_key_uses_fallback(self):
        entry = {"ts": time.time(), "event": "preToolUse", "tool": "edit",
                 "decision": "deny", "detail": ""}
        # No "rule" key
        rows = ah._per_hook_metrics([entry])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["rule"], "(no-rule)")

    def test_fire_rate_sums_to_100_for_single_rule(self):
        entries = [_make_entry("allow", rule="only")] * 10
        rows = ah._per_hook_metrics(entries)
        self.assertAlmostEqual(rows[0]["fire_rate_pct"], 100.0)

    def test_useful_block_rate_none_when_no_deny(self):
        entries = [_make_entry("allow", rule="harmless")] * 5
        rows = ah._per_hook_metrics(entries)
        self.assertIsNone(rows[0]["useful_block_rate"])


# ---------------------------------------------------------------------------
# _trend_analysis
# ---------------------------------------------------------------------------


class TestTrendAnalysis(unittest.TestCase):
    def test_empty_entries(self):
        rows = ah._trend_analysis([])
        self.assertEqual(rows, [])

    def test_single_day(self):
        ts = time.time()
        entries = [
            _make_entry("deny", ts=ts),
            _make_entry("allow", ts=ts),
            _make_entry("deny-dry", ts=ts),
        ]
        rows = ah._trend_analysis(entries)
        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertEqual(r["total"], 3)
        self.assertEqual(r["deny"], 1)
        self.assertEqual(r["deny_dry"], 1)
        self.assertEqual(r["allow"], 1)
        self.assertAlmostEqual(r["deny_rate_pct"], 33.3, places=0)

    def test_sorted_by_date_ascending(self):
        ts_now = time.time()
        ts_old = ts_now - 2 * 86_400
        entries = [
            _make_entry("allow", ts=ts_now),
            _make_entry("deny", ts=ts_old),
        ]
        rows = ah._trend_analysis(entries)
        self.assertEqual(len(rows), 2)
        self.assertLess(rows[0]["date"], rows[1]["date"])

    def test_deny_rate_zero_when_no_deny(self):
        ts = time.time()
        entries = [_make_entry("allow", ts=ts)] * 5
        rows = ah._trend_analysis(entries)
        self.assertEqual(rows[0]["deny_rate_pct"], 0.0)


# ---------------------------------------------------------------------------
# main() integration
# ---------------------------------------------------------------------------


class TestMain(unittest.TestCase):
    def setUp(self):
        import tempfile
        self._tmpdir = tempfile.mkdtemp(prefix="ah-main-test-")
        self._audit = Path(self._tmpdir) / "audit.jsonl"

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_missing_file_returns_1(self):
        rc = ah.main(["--audit-file", str(Path(self._tmpdir) / "missing.jsonl")])
        self.assertEqual(rc, 1)

    def test_empty_file_returns_1(self):
        self._audit.write_text("", encoding="utf-8")
        rc = ah.main(["--audit-file", str(self._audit)])
        self.assertEqual(rc, 1)

    def test_with_entries_returns_0(self):
        entries = [_make_entry("deny"), _make_entry("allow")]
        _write_jsonl(self._audit, entries)
        rc = ah.main(["--audit-file", str(self._audit)])
        self.assertEqual(rc, 0)

    def test_json_flag_emits_valid_json(self):
        import io
        entries = [_make_entry("deny"), _make_entry("deny-dry"), _make_entry("allow")]
        _write_jsonl(self._audit, entries)
        captured = io.StringIO()
        old_stdout = sys.stdout
        sys.stdout = captured
        try:
            rc = ah.main(["--audit-file", str(self._audit), "--json"])
        finally:
            sys.stdout = old_stdout
        self.assertEqual(rc, 0)
        payload = json.loads(captured.getvalue())
        self.assertIn("summary", payload)
        self.assertIn("per_hook", payload)
        self.assertIn("trend", payload)

    def test_json_summary_keys_present(self):
        import io
        entries = [_make_entry("deny")] * 3 + [_make_entry("allow")] * 7
        _write_jsonl(self._audit, entries)
        captured = io.StringIO()
        old_stdout = sys.stdout
        sys.stdout = captured
        try:
            ah.main(["--audit-file", str(self._audit), "--json"])
        finally:
            sys.stdout = old_stdout
        payload = json.loads(captured.getvalue())
        s = payload["summary"]
        for key in ("total_entries", "useful_block_count", "false_positive_count",
                    "deny_rate_pct", "fp_rate_pct", "useful_block_rate"):
            self.assertIn(key, s, f"Missing key in summary: {key}")

    def test_days_filter_works(self):
        import io
        old_ts = time.time() - 10 * 86_400
        new_ts = time.time()
        entries = [
            _make_entry("deny", ts=old_ts),
            _make_entry("allow", ts=new_ts),
        ]
        _write_jsonl(self._audit, entries)
        captured = io.StringIO()
        old_stdout = sys.stdout
        sys.stdout = captured
        try:
            rc = ah.main(["--audit-file", str(self._audit), "--json", "--days", "5"])
        finally:
            sys.stdout = old_stdout
        self.assertEqual(rc, 0)
        payload = json.loads(captured.getvalue())
        # Only the new entry should be present
        self.assertEqual(payload["summary"]["total_entries"], 1)

    def test_top_n_respected(self):
        import io
        entries = [_make_entry("allow", rule=f"rule-{i}") for i in range(20)]
        _write_jsonl(self._audit, entries)
        captured = io.StringIO()
        old_stdout = sys.stdout
        sys.stdout = captured
        try:
            ah.main(["--audit-file", str(self._audit), "--json", "--top", "3"])
        finally:
            sys.stdout = old_stdout
        payload = json.loads(captured.getvalue())
        self.assertLessEqual(len(payload["per_hook"]), 3)

    def test_missing_file_json_flag_returns_1(self):
        rc = ah.main(["--audit-file", str(Path(self._tmpdir) / "nope.jsonl"), "--json"])
        self.assertEqual(rc, 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
