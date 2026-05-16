#!/usr/bin/env python3
"""
test_events.py — Focused tests for events.py (knowledge event log).

Covers:
  - ULID generation: format, uniqueness, monotonic ordering
  - append_event: writes JSONL, returns event dict, fail_open behavior
  - load_events: round-trips written events, skips malformed lines
  - _build_status / materialize_status: deterministic materialization
  - replay_events: rebuild state from stream, dry_run flag
  - CLI: append / status / replay / tail subcommands
  - sk.py routing: sk events <sub> → events.py

Run:  python3 tests/test_events.py
"""

import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

if os.name == "nt":
    for _stream in (sys.stdout, sys.stderr):
        if hasattr(_stream, "reconfigure"):
            _stream.reconfigure(encoding="utf-8", errors="replace")

TESTS_DIR = Path(__file__).parent
TOOLS_DIR = TESTS_DIR.parent
EVENTS_PATH = TOOLS_DIR / "events.py"
SK_PATH = TOOLS_DIR / "sk.py"


def _load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


events = _load_module(EVENTS_PATH, "events")
sk = _load_module(SK_PATH, "sk")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _tmp_log(td: str) -> Path:
    return Path(td) / "knowledge-events.jsonl"


def _tmp_status(td: str) -> Path:
    return Path(td) / "status.json"


# ---------------------------------------------------------------------------
# ULID
# ---------------------------------------------------------------------------


class TestULID(unittest.TestCase):
    def test_length_is_26(self):
        uid = events._generate_ulid()
        self.assertEqual(len(uid), 26)

    def test_only_crockford_chars(self):
        alphabet = set(events._ULID_CHARS)
        uid = events._generate_ulid()
        for ch in uid:
            self.assertIn(ch, alphabet, f"unexpected char '{ch}' in ULID {uid}")

    def test_uniqueness(self):
        ids = {events._generate_ulid() for _ in range(200)}
        self.assertEqual(len(ids), 200)

    def test_monotonic_ordering(self):
        """ULIDs generated 10 ms apart should sort in creation order."""
        import time

        first = events._generate_ulid()
        time.sleep(0.015)  # 15 ms gap
        second = events._generate_ulid()
        self.assertLess(first, second)

    def test_uppercase_only(self):
        uid = events._generate_ulid()
        self.assertEqual(uid, uid.upper())


# ---------------------------------------------------------------------------
# append_event / load_events
# ---------------------------------------------------------------------------


class TestAppendLoad(unittest.TestCase):
    def test_append_returns_dict_with_required_keys(self):
        with tempfile.TemporaryDirectory() as td:
            log = _tmp_log(td)
            evt = events.append_event("pattern_learned", log_path=log, fail_open=False)
            self.assertIsNotNone(evt)
            for key in ("event_id", "at", "actor", "event", "data"):
                self.assertIn(key, evt)

    def test_append_writes_jsonl(self):
        with tempfile.TemporaryDirectory() as td:
            log = _tmp_log(td)
            events.append_event("briefing_served", log_path=log)
            self.assertTrue(log.exists())
            lines = [l.strip() for l in log.read_text().splitlines() if l.strip()]
            self.assertEqual(len(lines), 1)
            parsed = json.loads(lines[0])
            self.assertEqual(parsed["event"], "briefing_served")

    def test_append_multiple_events_in_order(self):
        with tempfile.TemporaryDirectory() as td:
            log = _tmp_log(td)
            for etype in ["skill_extracted", "pattern_learned", "sync_pushed"]:
                events.append_event(etype, log_path=log, fail_open=False)
            loaded = events.load_events(log)
            self.assertEqual(len(loaded), 3)
            self.assertEqual([e["event"] for e in loaded], ["skill_extracted", "pattern_learned", "sync_pushed"])

    def test_append_custom_actor_and_data(self):
        with tempfile.TemporaryDirectory() as td:
            log = _tmp_log(td)
            evt = events.append_event(
                "skill_extracted",
                actor="test-agent",
                data={"confidence": 0.95, "skill": "auth"},
                log_path=log,
            )
            self.assertEqual(evt["actor"], "test-agent")
            self.assertEqual(evt["data"]["confidence"], 0.95)

    def test_load_empty_log_returns_empty_list(self):
        with tempfile.TemporaryDirectory() as td:
            result = events.load_events(_tmp_log(td))
            self.assertEqual(result, [])

    def test_load_skips_malformed_lines(self):
        with tempfile.TemporaryDirectory() as td:
            log = _tmp_log(td)
            log.write_text(
                "not json\n"
                '{"event_id":"X01","at":"2024-01-01T00:00:00+00:00","actor":"copilot","event":"sync_pushed","data":{}}\n'
                '{"broken":\n'
                '{"event_id":"X02","at":"2024-01-01T00:00:01+00:00","actor":"copilot","event":"pattern_learned","data":{}}\n',
                encoding="utf-8",
            )
            loaded = events.load_events(log)
            self.assertEqual(len(loaded), 2)
            self.assertEqual(loaded[0]["event_id"], "X01")
            self.assertEqual(loaded[1]["event_id"], "X02")

    def test_fail_open_returns_none_on_bad_path(self):
        with patch("builtins.open", side_effect=OSError("simulated write error")):
            result = events.append_event("briefing_served", fail_open=True)
        self.assertIsNone(result)

    def test_fail_closed_raises_on_bad_path(self):
        with self.assertRaises(OSError):
            with patch("builtins.open", side_effect=OSError("simulated write error")):
                events.append_event("briefing_served", fail_open=False)

    def test_event_id_is_ulid(self):
        with tempfile.TemporaryDirectory() as td:
            log = _tmp_log(td)
            evt = events.append_event("knowledge_decayed", log_path=log, fail_open=False)
            uid = evt["event_id"]
            self.assertEqual(len(uid), 26)
            for ch in uid:
                self.assertIn(ch, events._ULID_CHARS)

    def test_at_is_iso8601(self):
        with tempfile.TemporaryDirectory() as td:
            log = _tmp_log(td)
            evt = events.append_event("sync_pushed", log_path=log, fail_open=False)
            # Should parse without error
            from datetime import datetime

            datetime.fromisoformat(evt["at"])


# ---------------------------------------------------------------------------
# _build_status / materialize_status
# ---------------------------------------------------------------------------


class TestBuildStatus(unittest.TestCase):
    def _sample_events(self, td: str) -> list[dict]:
        log = _tmp_log(td)
        event_types = [
            "skill_extracted",
            "pattern_learned",
            "briefing_served",
            "knowledge_decayed",
            "sync_pushed",
        ]
        for etype in event_types:
            data = {"confidence": 0.8} if etype == "skill_extracted" else {}
            events.append_event(etype, data=data, log_path=log, fail_open=False)
        return events.load_events(log)

    def test_total_events(self):
        with tempfile.TemporaryDirectory() as td:
            evts = self._sample_events(td)
            status = events._build_status(evts)
            self.assertEqual(status["total_events"], 5)

    def test_by_type_counts(self):
        with tempfile.TemporaryDirectory() as td:
            evts = self._sample_events(td)
            status = events._build_status(evts)
            by_type = status["by_type"]
            for etype in events.EVENT_TYPES:
                self.assertIn(etype, by_type)
                self.assertEqual(by_type[etype], 1)

    def test_actors_tracked(self):
        with tempfile.TemporaryDirectory() as td:
            evts = self._sample_events(td)
            status = events._build_status(evts)
            self.assertIn("copilot", status["actors"])

    def test_empty_stream_returns_zero_totals(self):
        status = events._build_status([])
        self.assertEqual(status["total_events"], 0)
        self.assertIsNone(status["first_event_at"])
        self.assertEqual(status["metrics"]["knowledge_decay_rate"], 0.0)

    def test_decay_rate_computed(self):
        with tempfile.TemporaryDirectory() as td:
            log = _tmp_log(td)
            events.append_event("knowledge_decayed", log_path=log)
            events.append_event("knowledge_decayed", log_path=log)
            events.append_event("briefing_served", log_path=log)
            evts = events.load_events(log)
            status = events._build_status(evts)
            # 2 decayed / 3 total = 0.6667
            self.assertAlmostEqual(status["metrics"]["knowledge_decay_rate"], 0.6667, places=3)

    def test_confidence_distribution_populated(self):
        with tempfile.TemporaryDirectory() as td:
            log = _tmp_log(td)
            events.append_event("skill_extracted", data={"confidence": 0.7}, log_path=log)
            events.append_event("skill_extracted", data={"confidence": 0.9}, log_path=log)
            evts = events.load_events(log)
            status = events._build_status(evts)
            dist = status["metrics"]["extraction_confidence"]
            self.assertEqual(dist["count"], 2)
            self.assertAlmostEqual(dist["mean"], 0.8, places=5)
            self.assertAlmostEqual(dist["min"], 0.7, places=5)
            self.assertAlmostEqual(dist["max"], 0.9, places=5)

    def test_confidence_empty_when_no_skill_events(self):
        with tempfile.TemporaryDirectory() as td:
            log = _tmp_log(td)
            events.append_event("briefing_served", log_path=log)
            evts = events.load_events(log)
            status = events._build_status(evts)
            self.assertEqual(status["metrics"]["extraction_confidence"], {})

    def test_deterministic_output(self):
        """Same event stream should produce identical status."""
        with tempfile.TemporaryDirectory() as td:
            log = _tmp_log(td)
            for etype in ["skill_extracted", "pattern_learned"]:
                events.append_event(etype, log_path=log)
            evts = events.load_events(log)
            s1 = events._build_status(evts)
            s2 = events._build_status(evts)
            # Remove generated_at (time-stamped) before comparing
            for s in (s1, s2):
                s.pop("generated_at", None)
            self.assertEqual(s1, s2)

    def test_materialize_writes_file(self):
        with tempfile.TemporaryDirectory() as td:
            log = _tmp_log(td)
            out = _tmp_status(td)
            events.append_event("briefing_served", log_path=log)
            events.materialize_status(log_path=log, output_path=out)
            self.assertTrue(out.exists())
            data = json.loads(out.read_text())
            self.assertIn("total_events", data)
            self.assertEqual(data["total_events"], 1)

    def test_last_by_type_populated(self):
        with tempfile.TemporaryDirectory() as td:
            log = _tmp_log(td)
            events.append_event("pattern_learned", log_path=log)
            events.append_event("pattern_learned", log_path=log)
            evts = events.load_events(log)
            status = events._build_status(evts)
            self.assertIn("pattern_learned", status["last_by_type"])


# ---------------------------------------------------------------------------
# Replay
# ---------------------------------------------------------------------------


class TestReplay(unittest.TestCase):
    def test_replay_rebuilds_state(self):
        with tempfile.TemporaryDirectory() as td:
            log = _tmp_log(td)
            out = _tmp_status(td)
            for etype in events.EVENT_TYPES:
                events.append_event(etype, log_path=log)

            state = events.replay_events(log_path=log, output_path=out)
            self.assertEqual(state["total_events"], 5)

    def test_replay_writes_status_file(self):
        with tempfile.TemporaryDirectory() as td:
            log = _tmp_log(td)
            events.append_event("sync_pushed", log_path=log)
            out = _tmp_status(td)
            events.replay_events(log_path=log, output_path=out)
            self.assertTrue(out.exists())

    def test_replay_dry_run_does_not_write(self):
        with tempfile.TemporaryDirectory() as td:
            log = _tmp_log(td)
            out = _tmp_status(td)
            events.append_event("briefing_served", log_path=log)

            events.replay_events(log_path=log, output_path=out, dry_run=True)
            # dry_run → file should NOT have been written
            self.assertFalse(out.exists())

    def test_replay_empty_log(self):
        with tempfile.TemporaryDirectory() as td:
            log = _tmp_log(td)
            out = _tmp_status(td)
            state = events.replay_events(log_path=log, output_path=out, dry_run=True)
            self.assertEqual(state["total_events"], 0)


# ---------------------------------------------------------------------------
# CLI: main()
# ---------------------------------------------------------------------------


class TestCLIAppend(unittest.TestCase):
    def test_append_valid_event_with_log_after_subcommand(self):
        with tempfile.TemporaryDirectory() as td:
            log = str(_tmp_log(td))
            with patch("builtins.print") as mock_print:
                rc = events.main(["append", "pattern_learned", "--log", log])
            self.assertEqual(rc, 0)
            output = " ".join(str(c) for call in mock_print.call_args_list for c in call[0])
            self.assertIn("pattern_learned", output)

    def test_append_valid_event(self):
        with tempfile.TemporaryDirectory() as td:
            log = str(_tmp_log(td))
            with patch("builtins.print") as mock_print:
                rc = events.main(["--log", log, "append", "pattern_learned"])
            self.assertEqual(rc, 0)
            output = " ".join(str(c) for call in mock_print.call_args_list for c in call[0])
            self.assertIn("pattern_learned", output)

    def test_append_unknown_event_type_returns_2(self):
        with tempfile.TemporaryDirectory() as td:
            log = str(_tmp_log(td))
            # argparse raises SystemExit(2) on invalid choices — treat it as rc=2
            try:
                rc = events.main(["--log", log, "append", "nonexistent_type"])
            except SystemExit as exc:
                rc = exc.code
            self.assertEqual(rc, 2)

    def test_append_with_data(self):
        with tempfile.TemporaryDirectory() as td:
            log = str(_tmp_log(td))
            with patch("builtins.print") as mock_print:
                rc = events.main(
                    [
                        "--log",
                        log,
                        "append",
                        "skill_extracted",
                        "--data",
                        '{"confidence": 0.95}',
                    ]
                )
            self.assertEqual(rc, 0)
            output = " ".join(str(c) for call in mock_print.call_args_list for c in call[0])
            self.assertIn("0.95", output)

    def test_append_invalid_json_data_returns_2(self):
        with tempfile.TemporaryDirectory() as td:
            log = str(_tmp_log(td))
            rc = events.main(["--log", log, "append", "briefing_served", "--data", "not json"])
            self.assertEqual(rc, 2)

    def test_append_all_five_event_types(self):
        with tempfile.TemporaryDirectory() as td:
            log = str(_tmp_log(td))
            for etype in sorted(events.EVENT_TYPES):
                with patch("builtins.print"):
                    rc = events.main(["--log", log, "append", etype])
                self.assertEqual(rc, 0, f"append {etype} returned non-zero")
            loaded = events.load_events(Path(log))
            self.assertEqual(len(loaded), 5)


class TestCLIStatus(unittest.TestCase):
    def test_status_prints_json_with_log_after_subcommand(self):
        with tempfile.TemporaryDirectory() as td:
            log = str(_tmp_log(td))
            out = str(_tmp_status(td))
            events.append_event("briefing_served", log_path=Path(log))
            with patch("builtins.print") as mock_print:
                rc = events.main(["status", "--log", log, "--output", out])
            self.assertEqual(rc, 0)
            all_output = " ".join(str(c) for call in mock_print.call_args_list for c in call[0])
            self.assertIn("total_events", all_output)

    def test_status_prints_json(self):
        with tempfile.TemporaryDirectory() as td:
            log = str(_tmp_log(td))
            out = str(_tmp_status(td))
            events.append_event("briefing_served", log_path=Path(log))
            with patch("builtins.print") as mock_print:
                rc = events.main(["--log", log, "status", "--output", out])
            self.assertEqual(rc, 0)
            all_output = " ".join(str(c) for call in mock_print.call_args_list for c in call[0])
            self.assertIn("total_events", all_output)

    def test_status_writes_file(self):
        with tempfile.TemporaryDirectory() as td:
            log = str(_tmp_log(td))
            out = str(_tmp_status(td))
            events.append_event("sync_pushed", log_path=Path(log))
            with patch("builtins.print"):
                events.main(["--log", log, "status", "--output", out])
            self.assertTrue(Path(out).exists())


class TestCLIReplay(unittest.TestCase):
    def test_replay_writes_requested_output_path(self):
        with tempfile.TemporaryDirectory() as td:
            log = str(_tmp_log(td))
            out = str(_tmp_status(td))
            events.append_event("knowledge_decayed", log_path=Path(log))
            with patch("builtins.print"):
                rc = events.main(["replay", "--log", log, "--output", out])
            self.assertEqual(rc, 0)
            self.assertTrue(Path(out).exists())

    def test_replay_exits_0(self):
        with tempfile.TemporaryDirectory() as td:
            log = str(_tmp_log(td))
            out = _tmp_status(td)
            events.append_event("knowledge_decayed", log_path=Path(log))
            with patch("builtins.print"):
                rc = events.main(["--log", log, "replay", "--output", str(out)])
            self.assertEqual(rc, 0)

    def test_replay_dry_run_does_not_write_file(self):
        with tempfile.TemporaryDirectory() as td:
            log = str(_tmp_log(td))
            out = _tmp_status(td)
            events.append_event("pattern_learned", log_path=Path(log))
            with patch("builtins.print"):
                rc = events.main(["--log", log, "replay", "--output", str(out), "--dry-run"])
            self.assertEqual(rc, 0)
            self.assertFalse(out.exists())


class TestCLITail(unittest.TestCase):
    def test_tail_default_n(self):
        with tempfile.TemporaryDirectory() as td:
            log = str(_tmp_log(td))
            for etype in ["briefing_served", "sync_pushed", "pattern_learned"]:
                events.append_event(etype, log_path=Path(log))
            with patch("builtins.print") as mock_print:
                rc = events.main(["--log", log, "tail"])
            self.assertEqual(rc, 0)
            self.assertEqual(mock_print.call_count, 3)

    def test_tail_n_limits_output(self):
        with tempfile.TemporaryDirectory() as td:
            log = str(_tmp_log(td))
            for etype in ["briefing_served", "sync_pushed", "pattern_learned"]:
                events.append_event(etype, log_path=Path(log))
            with patch("builtins.print") as mock_print:
                rc = events.main(["--log", log, "tail", "--n", "2"])
            self.assertEqual(rc, 0)
            self.assertEqual(mock_print.call_count, 2)

    def test_tail_empty_log(self):
        with tempfile.TemporaryDirectory() as td:
            log = str(_tmp_log(td))
            with patch("builtins.print") as mock_print:
                rc = events.main(["--log", log, "tail"])
            self.assertEqual(rc, 0)
            self.assertEqual(mock_print.call_count, 0)


class TestCLINoArgs(unittest.TestCase):
    def test_no_args_prints_help_and_exits_0(self):
        with patch("builtins.print") as mock_print:
            rc = events.main([])
        self.assertEqual(rc, 0)


# ---------------------------------------------------------------------------
# sk.py routing: sk events ...
# ---------------------------------------------------------------------------


class TestSkEventsRouting(unittest.TestCase):
    def test_events_help_exits_0(self):
        with patch("builtins.print"):
            rc = sk.main(["events", "--help"])
        self.assertEqual(rc, 0)

    def test_events_no_subcommand_exits_0(self):
        with patch("builtins.print"):
            rc = sk.main(["events"])
        self.assertEqual(rc, 0)

    def test_events_unknown_subcmd_exits_2(self):
        rc = sk.main(["events", "notasubcmd"])
        self.assertEqual(rc, 2)

    def test_events_replay_routes_to_events_py(self):
        with patch.object(sk, "_run", return_value=0) as mock_run:
            rc = sk.main(["events", "replay"])
        self.assertEqual(rc, 0)
        mock_run.assert_called_once_with("events.py", ["replay"])

    def test_events_append_routes_to_events_py(self):
        with patch.object(sk, "_run", return_value=0) as mock_run:
            rc = sk.main(["events", "append", "briefing_served"])
        self.assertEqual(rc, 0)
        mock_run.assert_called_once_with("events.py", ["append", "briefing_served"])

    def test_events_status_routes_to_events_py(self):
        with patch.object(sk, "_run", return_value=0) as mock_run:
            rc = sk.main(["events", "status"])
        self.assertEqual(rc, 0)
        mock_run.assert_called_once_with("events.py", ["status"])

    def test_events_status_with_log_routes_to_events_py(self):
        with patch.object(sk, "_run", return_value=0) as mock_run:
            rc = sk.main(["events", "status", "--log", "custom.jsonl"])
        self.assertEqual(rc, 0)
        mock_run.assert_called_once_with("events.py", ["status", "--log", "custom.jsonl"])

    def test_events_tail_routes_to_events_py(self):
        with patch.object(sk, "_run", return_value=0) as mock_run:
            rc = sk.main(["events", "tail"])
        self.assertEqual(rc, 0)
        mock_run.assert_called_once_with("events.py", ["tail"])

    def test_events_in_groups_dict(self):
        self.assertIn("events", sk._GROUPS)
        self.assertEqual(
            set(sk._GROUPS["events"].keys()),
            {"append", "status", "replay", "tail"},
        )

    def test_events_not_in_direct_dict(self):
        # events should be handled via _run_events, not _DIRECT
        self.assertNotIn("events", sk._DIRECT)


# ---------------------------------------------------------------------------
# Five required event types are all defined
# ---------------------------------------------------------------------------


class TestEventTypes(unittest.TestCase):
    REQUIRED = {"skill_extracted", "pattern_learned", "briefing_served", "knowledge_decayed", "sync_pushed"}

    def test_all_five_required_types_present(self):
        for etype in self.REQUIRED:
            self.assertIn(etype, events.EVENT_TYPES, f"missing required event type: {etype}")

    def test_at_least_five_types(self):
        self.assertGreaterEqual(len(events.EVENT_TYPES), 5)


if __name__ == "__main__":
    unittest.main(verbosity=2)
