#!/usr/bin/env python3
"""tests/test_retry_stats.py — Unit tests for retry-stats.py.

Test cases:
1. EmptyQueue  — no events → exit 0, prints "No retry data found"
2. BalancedTraffic — 10 mixed events → normal table output, exit 0
3. Burst429 — 30 429s in 1h → exit 2 with --threshold-429-per-hour 20
4. RotatedFiles — events split across 2 fixture files → total count correct

Run:
    python3 tests/test_retry_stats.py
"""

import ast
import importlib.util
import io
import json
import os
import sys
import tempfile
import time
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path

# ---------------------------------------------------------------------------
# Test harness
# ---------------------------------------------------------------------------

_PASS = 0
_FAIL = 0


def test(name: str, condition: bool, msg: str = "") -> None:
    global _PASS, _FAIL
    if condition:
        _PASS += 1
        print(f"  ✓ {name}")
    else:
        _FAIL += 1
        detail = f": {msg}" if msg else ""
        print(f"  ✗ {name}{detail}")


# ---------------------------------------------------------------------------
# Load module under test
# ---------------------------------------------------------------------------

_TOOLS_DIR = Path(__file__).parent.parent
_SCRIPT = _TOOLS_DIR / "retry-stats.py"

spec = importlib.util.spec_from_file_location("retry_stats", _SCRIPT)
assert spec and spec.loader
rs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rs)  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_event(
    *,
    ts_epoch: int | None = None,
    agent: str = "copilot",
    detected_pattern: str = "rate_limit",
    status_code: int | None = None,
    outcome: str = "attempt",
    computed_delay_seconds: float = 0.5,
) -> dict:
    if ts_epoch is None:
        ts_epoch = int(time.time())
    return {
        "ts": f"unix:{ts_epoch}",
        "hook": "429-retry",
        "agent": agent,
        "attempt": 1,
        "max_attempts": 5,
        "detected_pattern": detected_pattern,
        "status_code": status_code,
        "retry_after_hint_seconds": None,
        "computed_delay_seconds": computed_delay_seconds,
        "delay_source": "backoff",
        "elapsed_total_seconds": 1.0,
        "stop_reason": "",
        "exit_code_prev": None,
        "outcome": outcome,
        "redacted_error_preview": "{}",
        "hmac": "aabbcc",
    }


def _write_jsonl(path: Path, events: list[dict]) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        for ev in events:
            fh.write(json.dumps(ev) + "\n")


def _run_main(argv: list[str], monkeypatch_markers: Path) -> tuple[int, str, str]:
    """Run rs.main with a patched _MARKERS_DIR; return (exit_code, stdout, stderr)."""
    orig_dir = rs._MARKERS_DIR
    rs._MARKERS_DIR = monkeypatch_markers
    stdout_buf = io.StringIO()
    stderr_buf = io.StringIO()
    try:
        with redirect_stdout(stdout_buf), redirect_stderr(stderr_buf):
            code = rs.main(argv)
    except SystemExit as exc:
        code = int(exc.code) if exc.code is not None else 0
    finally:
        rs._MARKERS_DIR = orig_dir
    return code, stdout_buf.getvalue(), stderr_buf.getvalue()


# ---------------------------------------------------------------------------
# Test suite
# ---------------------------------------------------------------------------


def test_syntax() -> None:
    print("Syntax / importability")
    src = _SCRIPT.read_text(encoding="utf-8")
    try:
        ast.parse(src)
        ok = True
    except SyntaxError as exc:
        ok = False
        print(f"    SyntaxError: {exc}")
    test("retry-stats.py parses without syntax errors", ok)


def test_empty_queue(markers_dir: Path) -> None:
    print("EmptyQueue — no events")
    code, out, _err = _run_main(["--since", "7d"], markers_dir)
    test("exit 0 on empty data", code == 0, f"got {code}")
    test("prints 'No retry data found'", "No retry data found" in out, repr(out[:120]))


def test_balanced_traffic(markers_dir: Path) -> None:
    print("BalancedTraffic — 10 mixed events")
    now = int(time.time())
    events = [_make_event(ts_epoch=now - i * 60, status_code=429, detected_pattern="rate_limit") for i in range(3)] + [
        _make_event(ts_epoch=now - i * 60, detected_pattern="server_error") for i in range(7)
    ]
    fpath = markers_dir / "retry-queue.jsonl"
    _write_jsonl(fpath, events)

    code, out, _err = _run_main(["--since", "1h"], markers_dir)
    test("exit 0 on normal traffic", code == 0, f"got {code}")
    test("table contains group column header", "group" in out.lower(), repr(out[:120]))
    test("table shows events count", "10" in out or any(str(i) in out for i in range(8, 11)), repr(out[:200]))

    # --json mode
    code_j, out_j, _err_j = _run_main(["--since", "1h", "--json"], markers_dir)
    test("--json exit 0", code_j == 0, f"got {code_j}")
    lines = [l for l in out_j.strip().splitlines() if l]
    test("--json produces at least one NDJSON line", len(lines) >= 1, repr(out_j[:200]))
    obj = json.loads(lines[0])
    for field in ("group", "total", "rate_429_per_h", "p50_ms", "p95_ms", "exhausted_pct"):
        test(f"--json row has field '{field}'", field in obj, repr(obj))

    fpath.unlink()


def test_burst_429(markers_dir: Path) -> None:
    print("Burst429 — 30 rate-limit events in 1h → threshold exceeded")
    now = int(time.time())
    events = [_make_event(ts_epoch=now - i * 60, status_code=429, detected_pattern="rate_limit") for i in range(30)]
    fpath = markers_dir / "retry-queue.jsonl"
    _write_jsonl(fpath, events)

    code, _out, err = _run_main(["--since", "1h", "--threshold-429-per-hour", "20"], markers_dir)
    test("exit 2 when threshold exceeded", code == 2, f"got {code}")
    # stderr warning
    test("stderr warns about threshold", "threshold" in err.lower() or "429" in err, repr(err[:200]))

    fpath.unlink()


def test_rotated_files(markers_dir: Path) -> None:
    print("RotatedFiles — events split across 2 files")
    now = int(time.time())
    events_a = [_make_event(ts_epoch=now - i * 120) for i in range(5)]
    events_b = [_make_event(ts_epoch=now - i * 120 - 600) for i in range(7)]
    fpath_a = markers_dir / "retry-queue.jsonl"
    fpath_b = markers_dir / "retry-queue.1.jsonl"
    _write_jsonl(fpath_a, events_a)
    _write_jsonl(fpath_b, events_b)

    code, out_j, _err = _run_main(["--since", "24h", "--json"], markers_dir)
    test("exit 0 on rotated files", code == 0, f"got {code}")
    total = sum(json.loads(l)["total"] for l in out_j.strip().splitlines() if l)
    test(f"total across rotated files = 12 (got {total})", total == 12, f"got {total}")

    fpath_a.unlink()
    fpath_b.unlink()


def test_agent_filter(markers_dir: Path) -> None:
    print("AgentFilter — --agent filters by agent name")
    now = int(time.time())
    events = [_make_event(ts_epoch=now - i * 60, agent="copilot") for i in range(4)] + [
        _make_event(ts_epoch=now - i * 60, agent="gemini") for i in range(6)
    ]
    fpath = markers_dir / "retry-queue.jsonl"
    _write_jsonl(fpath, events)

    code, out_j, _err = _run_main(["--since", "1h", "--agent", "copilot", "--json"], markers_dir)
    test("exit 0 with --agent filter", code == 0, f"got {code}")
    total = sum(json.loads(l)["total"] for l in out_j.strip().splitlines() if l)
    test(f"agent filter shows only copilot events (got {total})", total == 4, f"got {total}")

    fpath.unlink()


def test_by_pattern(markers_dir: Path) -> None:
    print("ByPattern — --by pattern groups by detected_pattern")
    now = int(time.time())
    events = [_make_event(ts_epoch=now - i * 60, detected_pattern="rate_limit") for i in range(3)] + [
        _make_event(ts_epoch=now - i * 60, detected_pattern="server_error") for i in range(2)
    ]
    fpath = markers_dir / "retry-queue.jsonl"
    _write_jsonl(fpath, events)

    code, out_j, _err = _run_main(["--since", "1h", "--by", "pattern", "--json"], markers_dir)
    test("exit 0 with --by pattern", code == 0, f"got {code}")
    groups_found = {json.loads(l)["group"] for l in out_j.strip().splitlines() if l}
    test("rate_limit group present", "rate_limit" in groups_found, repr(groups_found))
    test("server_error group present", "server_error" in groups_found, repr(groups_found))

    fpath.unlink()


def test_malformed_lines(markers_dir: Path) -> None:
    print("MalformedLines — invalid JSON lines are skipped gracefully")
    now = int(time.time())
    fpath = markers_dir / "retry-queue.jsonl"
    with open(fpath, "w", encoding="utf-8") as fh:
        fh.write("not-json\n")
        fh.write("{broken\n")
        fh.write(json.dumps(_make_event(ts_epoch=now)) + "\n")
        fh.write("\n")
        fh.write(json.dumps(_make_event(ts_epoch=now - 60)) + "\n")

    code, out_j, _err = _run_main(["--since", "1h", "--json"], markers_dir)
    test("exit 0 despite malformed lines", code == 0, f"got {code}")
    total = sum(json.loads(l)["total"] for l in out_j.strip().splitlines() if l)
    test(f"only valid events counted (got {total})", total == 2, f"got {total}")

    fpath.unlink()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> int:
    print("=" * 60)
    print("retry-stats.py tests")
    print("=" * 60)

    test_syntax()
    print()

    # Create a temporary directory used as the mock markers dir for all tests
    with tempfile.TemporaryDirectory() as tmpdir:
        markers_dir = Path(tmpdir)

        test_empty_queue(markers_dir)
        print()
        test_balanced_traffic(markers_dir)
        print()
        test_burst_429(markers_dir)
        print()
        test_rotated_files(markers_dir)
        print()
        test_agent_filter(markers_dir)
        print()
        test_by_pattern(markers_dir)
        print()
        test_malformed_lines(markers_dir)

    print()
    print("=" * 60)
    print(f"Results: {_PASS} passed, {_FAIL} failed")
    print("=" * 60)
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
