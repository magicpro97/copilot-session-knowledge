#!/usr/bin/env python3
"""
tests/test_soak_diagnostics.py — Unit tests for soak-diagnostics.py.

Tests:
 1. Syntax / importability
 2. ThresholdEvaluator — PASS: all metrics within thresholds
 3. ThresholdEvaluator — FAIL: LLM p95 exceeds 30 s
 4. ThresholdEvaluator — FAIL: TTFT p95 exceeds 10 s
 5. ThresholdEvaluator — FAIL: Tool p95 exceeds 20 s
 6. ThresholdEvaluator — FAIL: Token growth > 20k/min
 7. ThresholdEvaluator — FAIL: SSE reconnects exceed threshold
 8. ThresholdEvaluator — FAIL: Security error → immediate FAIL
 9. ThresholdEvaluator — FAIL: Secret leak → immediate FAIL
10. ThresholdEvaluator — FAIL: Tool loop > 5
11. ThresholdEvaluator — PASS: Peak heap unavailable → graceful degradation
12. build_report: fixture-PASS report structure correct
13. build_report: fixture-FAIL contains failure references
14. build_report: security-leak causes overall FAIL
15. Redaction: token scrubbed from message
16. Redaction: path with username scrubbed
17. Redaction: nested attrs scrubbed
18. build_html_report: no inline scripts, no event handlers
19. build_html_report: HTML-escaped content
20. write_browser_stubs: JS file written, measureUserAgentSpecificMemory degrades
21. aggregate_metrics: loop detection
22. aggregate_metrics: balanced turns
23. _p95: single value, multiple values, empty list
24. _parse_args: smoke mode, full mode, browser-stubs, flags

Run:
    python3 tests/test_soak_diagnostics.py
"""

import ast
import importlib.util
import io
import json
import os
import sys
import tempfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

if os.name == "nt":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

REPO = Path(__file__).parent.parent
SOAK_PY = REPO / "soak-diagnostics.py"

PASS_COUNT = 0
FAIL_COUNT = 0


def test(name: str, passed: bool, detail: str = "") -> None:
    global PASS_COUNT, FAIL_COUNT
    if passed:
        PASS_COUNT += 1
        print(f"  ✅ {name}")
    else:
        FAIL_COUNT += 1
        print(f"  ❌ {name}" + (f" — {detail}" if detail else ""))


# ── Load module ──────────────────────────────────────────────────────────────

_soak_mod = None


def _load_soak() -> object:
    global _soak_mod
    if _soak_mod is not None:
        return _soak_mod
    spec = importlib.util.spec_from_file_location("soak_diagnostics", SOAK_PY)
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    _soak_mod = mod
    return mod


# ── 1. Syntax ────────────────────────────────────────────────────────────────

print("\n── Syntax / importability ──")

src = SOAK_PY.read_text(encoding="utf-8")
try:
    ast.parse(src)
    test("soak-diagnostics.py parses without syntax errors", True)
except SyntaxError as exc:
    test("soak-diagnostics.py parses without syntax errors", False, str(exc))

try:
    mod = _load_soak()
    test("soak-diagnostics.py importable without error", True)
except Exception as exc:
    test("soak-diagnostics.py importable without error", False, str(exc))
    print("FATAL: cannot load module; aborting tests")
    sys.exit(1)

# Ensure Windows UTF-8 block is present
test("Windows UTF-8 block present", 'os.name == "nt"' in src and "reconfigure" in src)
# Ensure no inter-script imports from browse.*
test("No inter-script imports at module level",
     "from browse" not in src or "# noqa" in src or "importlib" in src)

# ── Helpers ──────────────────────────────────────────────────────────────────

ThresholdEvaluator = mod.ThresholdEvaluator
aggregate_metrics = mod.aggregate_metrics
build_report = mod.build_report
build_html_report = mod.build_html_report
write_browser_stubs = mod.write_browser_stubs
redact_event = mod.redact_event
_p95 = mod._p95
_parse_args = mod._parse_args


def _make_good_metrics(duration_min: float = 5.0) -> dict:
    """Return a metrics dict that passes all thresholds."""
    return {
        "llm_latencies_ms": [1000.0, 1200.0, 1500.0],
        "llm_slow_events": [],
        "ttft_ms": [200.0, 300.0, 250.0],
        "ttft_slow_events": [],
        "tool_latencies_ms": [100.0, 200.0, 150.0],
        "tool_slow_events": [],
        "token_growth_per_min": 500.0,
        "token_growth_events": [],
        "sse_reconnects": 0,
        "sse_reconnect_events": [],
        "security_error_count": 0,
        "security_error_events": [],
        "secret_leak_count": 0,
        "secret_leak_events": [],
        "max_tool_loop": 2,
        "tool_loop_events": [],
        "peak_heap_mb": None,
        "heap_events": [],
        "agent_turns": 3,
        "user_turns": 3,
        "duration_min": duration_min,
        "total_events": 12,
    }


# ── 2. ThresholdEvaluator — All PASS ────────────────────────────────────────

print("\n── ThresholdEvaluator — PASS ──")
evaluator = ThresholdEvaluator()
good = _make_good_metrics()
checks = evaluator.evaluate(good)
test("all checks pass with good metrics", all(c["pass"] for c in checks.values()))
test("llm_p95_latency_ms present", "llm_p95_latency_ms" in checks)
test("tool_loop_depth PASS", checks["tool_loop_depth"]["pass"])
test("balanced_turns PASS", checks["balanced_turns"]["pass"])
test("peak_heap_mb PASS (unavailable)", checks["peak_heap_mb"]["pass"])
test("peak_heap_mb has note when None", "note" in checks.get("peak_heap_mb", {}))


# ── 3–10. ThresholdEvaluator — individual FAIL ──────────────────────────────

print("\n── ThresholdEvaluator — FAIL per threshold ──")


def _check_fail(metrics_override: dict, check_name: str, label: str) -> None:
    m = {**_make_good_metrics(), **metrics_override}
    c = evaluator.evaluate(m)
    test(f"FAIL: {label}", not c[check_name]["pass"])


# LLM p95 > 30 s
_check_fail(
    {"llm_latencies_ms": [31_000.0] * 20},
    "llm_p95_latency_ms",
    "LLM p95 > 30s",
)

# TTFT p95 > 10 s
_check_fail(
    {"ttft_ms": [11_000.0] * 20},
    "ttft_p95_ms",
    "TTFT p95 > 10s",
)

# Tool p95 > 20 s
_check_fail(
    {"tool_latencies_ms": [21_000.0] * 20},
    "tool_p95_latency_ms",
    "Tool p95 > 20s",
)

# Token growth > 20k/min
_check_fail(
    {"token_growth_per_min": 25_000.0},
    "token_growth_per_min",
    "Token growth > 20k/min",
)

# SSE reconnects (5 reconnects in 1-min window → threshold is 1 per 10min → allowed=1)
_check_fail(
    {"sse_reconnects": 5, "duration_min": 1.0},
    "sse_reconnects",
    "SSE reconnects > threshold",
)

# Security errors
_check_fail(
    {"security_error_count": 1, "security_error_events": [{"idx": 0}]},
    "security_errors",
    "Security error > 0",
)

# Secret leaks — immediate FAIL
_check_fail(
    {"secret_leak_count": 1, "secret_leak_events": [{"idx": 1, "field": "message"}]},
    "secret_leaks",
    "Secret leak > 0",
)

# Tool loop > 5
_check_fail(
    {"max_tool_loop": 6, "tool_loop_events": [{"idx": 5}]},
    "tool_loop_depth",
    "Tool loop > 5",
)

# Imbalanced turns
_check_fail(
    {"agent_turns": 10, "user_turns": 1},
    "balanced_turns",
    "Imbalanced turns (ratio > 2.0)",
)

# Peak heap exceeds 250 MB
metrics_heap = {**_make_good_metrics(), "peak_heap_mb": 300.0}
c_heap = evaluator.evaluate(metrics_heap)
test("FAIL: peak heap > 250MB", not c_heap["peak_heap_mb"]["pass"])


# ── 12. build_report: fixture-PASS structure ────────────────────────────────

print("\n── build_report ──")
events_pass = mod._make_smoke_events()
report_pass = build_report("s1", "r1", events_pass, 1.0, "smoke")

test("overall_pass is bool", isinstance(report_pass.get("overall_pass"), bool))
test("schema_version == '1'", report_pass.get("schema_version") == "1")
test("checks is dict", isinstance(report_pass.get("checks"), dict))
test("summary has passed/failed/total", all(
    k in report_pass.get("summary", {}) for k in ("passed", "failed", "total")
))
test("fixture-PASS: overall pass", report_pass["overall_pass"])
test("generated_at present", "generated_at" in report_pass)
test("mode == 'smoke'", report_pass.get("mode") == "smoke")


# ── 13. build_report: fixture-FAIL linkage ──────────────────────────────────

events_fail = list(events_pass)
# Add a slow LLM event (>30s)
events_fail.append({
    "idx": 99, "kind": "llm_request", "level": "debug", "source": "operator_console",
    "message": "Slow LLM", "timestamp": None, "span_id": "f" * 16,
    "parent_span_id": None, "tool_name": None, "duration_ms": 35_000.0,
    "status": "ok", "attrs": {"total_tokens": 1000}, "redacted": False,
})
report_fail = build_report("s2", "r2", events_fail, 1.0, "smoke")
test("fixture-FAIL: overall FAIL", not report_fail["overall_pass"])
slow_check = report_fail["checks"].get("llm_p95_latency_ms", {})
test("fixture-FAIL: failures list non-empty for slow LLM", len(slow_check.get("failures", [])) > 0)


# ── 14. Security leak → immediate FAIL ──────────────────────────────────────

events_sec = list(events_pass) + [{
    "idx": 100, "kind": "error", "level": "error", "source": "operator_console",
    "message": "unauthorized access token=secret123 detected",
    "timestamp": None, "span_id": None, "parent_span_id": None,
    "tool_name": None, "duration_ms": None, "status": "error",
    "attrs": {}, "redacted": False,  # unredacted to trigger leak scan
}]
report_sec = build_report("s3", "r3", events_sec, 1.0, "smoke")
sec_check = report_sec["checks"].get("security_errors", {})
test("security error detected in event stream", not sec_check.get("pass", True))


# ── 15–17. Redaction ────────────────────────────────────────────────────────

print("\n── Redaction ──")

entry_token = {"idx": 0, "kind": "generic", "message": "Bearer abc123token", "attrs": {}, "redacted": False}
r_token = redact_event(entry_token)
test("Bearer token scrubbed from message", "[REDACTED]" in r_token["message"])
test("redacted flag set when token found", r_token["redacted"] is True)

entry_path = {"idx": 1, "kind": "generic", "message": "/Users/johndoe/project/file.py",
              "attrs": {}, "redacted": False}
r_path = redact_event(entry_path)
test("Username path scrubbed", "johndoe" not in r_path["message"])

entry_attrs = {"idx": 2, "kind": "generic", "message": "ok",
               "attrs": {"api_key": "super-secret", "other": "safe"}, "redacted": False}
r_attrs = redact_event(entry_attrs)
test("api_key value in attrs scrubbed", "[REDACTED]" in r_attrs["attrs"].get("api_key", ""))
test("non-sensitive attr preserved", r_attrs["attrs"].get("other") == "safe")


# ── 18–19. HTML report ──────────────────────────────────────────────────────

print("\n── HTML report ──")
html_out = build_html_report(report_pass)
test("HTML report is a string", isinstance(html_out, str))
test("No inline onclick/onerror/onload handlers", all(
    h not in html_out.lower() for h in ("onclick", "onerror", "onload", "onmouseover")
))
test("No <script> tags", "<script" not in html_out.lower())
test("Overall PASS badge present", "pass" in html_out.lower())

# Inject a dangerous string and confirm it gets HTML-escaped
evil_report = dict(report_pass, session_id='<script>alert("xss")</script>')
evil_html = build_html_report(evil_report)
test("XSS payload HTML-escaped in session_id", '<script>' not in evil_html
     and '&lt;script&gt;' in evil_html)


# ── 20. Browser stubs ────────────────────────────────────────────────────────

print("\n── Browser stubs ──")
import tempfile as _tmpmod
_tmp_dir = Path(REPO / "soak-output-test-tmp")
try:
    _tmp_dir.mkdir(parents=True, exist_ok=True)
    js_path = write_browser_stubs(_tmp_dir)
    test("soak-browser-stubs.js written", js_path.exists())
    js_content = js_path.read_text(encoding="utf-8")
    test("Long-task PerformanceObserver present", "PerformanceObserver" in js_content)
    test("SSE reconnect counter present", "sseReconnect" in js_content)
    test("measureUserAgentSpecificMemory present", "measureUserAgentSpecificMemory" in js_content)
    test("Graceful degradation: try/catch around PerformanceObserver",
         "catch" in js_content and "PerformanceObserver" in js_content)
    test("Graceful degradation: heap API checks isSecureContext",
         "isSecureContext" in js_content)
    test("No inline event handlers in JS stub",
         "onclick=" not in js_content and "onerror=" not in js_content)
finally:
    # Clean up temp dir
    import shutil
    if _tmp_dir.exists():
        shutil.rmtree(_tmp_dir, ignore_errors=True)


# ── 21. Loop detection ───────────────────────────────────────────────────────

print("\n── aggregate_metrics loop detection ──")
loop_events = [
    {"idx": i, "kind": "tool_call", "level": "debug", "source": "operator_console",
     "message": f"Tool {i}", "timestamp": None, "span_id": None, "parent_span_id": None,
     "tool_name": "bash", "duration_ms": 100.0, "status": "ok", "attrs": {}, "redacted": False}
    for i in range(10)
]
m_loop = aggregate_metrics(loop_events, 1.0)
test("Tool loop depth detected (>=5 consecutive)", m_loop["max_tool_loop"] >= 5)
test("Tool loop events populated", len(m_loop["tool_loop_events"]) > 0)


# ── 22. Balanced turns ───────────────────────────────────────────────────────

print("\n── aggregate_metrics balanced turns ──")
turn_events = (
    [{"idx": i, "kind": "turn_start", "level": "info", "source": "x",
      "message": f"Turn {i}", "timestamp": None, "span_id": None, "parent_span_id": None,
      "tool_name": None, "duration_ms": None, "status": None, "attrs": {}, "redacted": False}
     for i in range(3)]
    +
    [{"idx": i + 10, "kind": "agent_response", "level": "info", "source": "x",
      "message": f"Reply {i}", "timestamp": None, "span_id": None, "parent_span_id": None,
      "tool_name": None, "duration_ms": None, "status": "ok", "attrs": {}, "redacted": False}
     for i in range(3)]
)
m_turns = aggregate_metrics(turn_events, 1.0)
test("user_turns == 3", m_turns["user_turns"] == 3)
test("agent_turns == 3", m_turns["agent_turns"] == 3)


# ── 23. _p95 ────────────────────────────────────────────────────────────────

print("\n── _p95 ──")
test("_p95([]) returns None", _p95([]) is None)
test("_p95([5.0]) returns 5.0", _p95([5.0]) == 5.0)
vals = list(range(1, 101))  # 1..100
p = _p95([float(v) for v in vals])
test("_p95(1..100) is approximately 95", 94 <= p <= 96)


# ── 24. _parse_args ─────────────────────────────────────────────────────────

print("\n── _parse_args ──")
a_smoke = _parse_args(["--smoke"])
test("--smoke sets mode=smoke", a_smoke["mode"] == "smoke")

a_full = _parse_args(["--full", "--session", "abc", "--run", "xyz", "--duration", "10"])
test("--full sets mode=full", a_full["mode"] == "full")
test("--session parsed", a_full["session"] == "abc")
test("--run parsed", a_full["run"] == "xyz")
test("--duration parsed as float", a_full["duration"] == 10.0)

a_bs = _parse_args(["--browser-stubs", "--out-dir", "my-output"])
test("--browser-stubs sets mode=browser-stubs", a_bs["mode"] == "browser-stubs")
test("--out-dir parsed as Path", str(a_bs["out_dir"]) == "my-output")

a_html = _parse_args(["--smoke", "--html"])
test("--html flag set", a_html["html"] is True)

a_port = _parse_args(["--smoke", "--port", "9999"])
test("--port parsed as int", a_port["port"] == 9999)


# ── Summary ──────────────────────────────────────────────────────────────────

print(f"\n{'='*50}")
total = PASS_COUNT + FAIL_COUNT
print(f"Results: {PASS_COUNT}/{total} passed, {FAIL_COUNT} failed")
if FAIL_COUNT:
    print("FAIL")
    sys.exit(1)
else:
    print("PASS")
    sys.exit(0)
