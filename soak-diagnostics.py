#!/usr/bin/env python3
"""
soak-diagnostics.py — Long-run health monitoring and soak diagnostics.

Fetches debug-log events from the browse backend, evaluates pass/fail thresholds,
and generates soak-report.json (and optionally soak-report.html).

Usage:
    python3 soak-diagnostics.py --smoke [OPTIONS]
    python3 soak-diagnostics.py --full  [OPTIONS]
    python3 soak-diagnostics.py --browser-stubs [--out-dir DIR]

Modes:
    --smoke          Synthetic fixture-based run; completes under 2 minutes.
    --full           30-minute real-session monitoring (manual invocation).
    --browser-stubs  Emit browser-side JS stubs only.

Common options:
    --session ID     Session ID to monitor (required for --full).
    --run     ID     Run ID to monitor (required for --full).
    --host    HOST   Browse backend host (default: localhost).
    --port    PORT   Browse backend port (default: 16100).
    --out-dir DIR    Output directory (default: ./soak-output).
    --html           Also write soak-report.html.
    --duration MINS  Override poll duration for --full (default: 30).

Outputs (in --out-dir):
    soak-report.json        Machine-readable pass/fail report (always).
    soak-report.html        Human-readable dashboard (with --html).
    soak-browser-stubs.js   Browser monitoring snippets (with --browser-stubs).
"""

import html
import json
import math
import os
import re
import statistics
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

if os.name == "nt":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_HOST = "localhost"
DEFAULT_PORT = 16100
DEFAULT_DURATION_MIN = 30
DEFAULT_OUT_DIR = Path("soak-output")
POLL_INTERVAL_SEC = 5
SMOKE_MAX_SECONDS = 110  # safety margin under the 2-minute limit

# ── Redaction ────────────────────────────────────────────────────────────────
# Inline redaction (standalone script — cannot import browse.core.redaction).

_TOKEN_PAT = re.compile(
    r"((?:bearer|token|api[_-]?key|secret|password)[=:\s]+)[^\s\"'&,]+",
    re.IGNORECASE,
)
_PATH_USER_PAT = re.compile(r"(/(?:Users|home)/)[^/\s\"']+(/)", re.IGNORECASE)
_URL_QUERY_TOKEN_PAT = re.compile(r"([?&](?:token|key|secret|auth)[=])[^&\s\"']+", re.IGNORECASE)
_SENSITIVE_KEY_PAT = re.compile(
    r"(password|token|secret|api[_-]?key|bearer|auth)", re.IGNORECASE
)


def _redact_string(value: str) -> tuple[str, bool]:
    """Scrub token/path patterns from a string; return (scrubbed, changed)."""
    original = value
    value = _TOKEN_PAT.sub(r"\1[REDACTED]", value)
    value = _PATH_USER_PAT.sub(r"\1[USER]\2", value)
    value = _URL_QUERY_TOKEN_PAT.sub(r"\1[REDACTED]", value)
    return value, value != original


def _redact_value(v: Any, key_name: str = "") -> tuple[Any, bool]:
    # If the dict key itself is sensitive, redact the value entirely
    if key_name and _SENSITIVE_KEY_PAT.search(key_name) and isinstance(v, str) and v:
        return "[REDACTED]", True
    if isinstance(v, str):
        return _redact_string(v)
    if isinstance(v, dict):
        changed = False
        out: dict = {}
        for k, val in v.items():
            rv, c = _redact_value(val, key_name=k)
            out[k] = rv
            changed = changed or c
        return out, changed
    if isinstance(v, list):
        changed = False
        out_list = []
        for item in v:
            ri, c = _redact_value(item)
            out_list.append(ri)
            changed = changed or c
        return out_list, changed
    return v, False


def redact_event(entry: dict) -> dict:
    """Return a shallow-copy of *entry* with sensitive content scrubbed."""
    result = dict(entry)
    was_redacted = bool(result.get("redacted", False))
    for field in ("message", "attrs"):
        if field in result:
            rv, changed = _redact_value(result[field])
            result[field] = rv
            if changed:
                was_redacted = True
    result["redacted"] = was_redacted
    return result


# ── Threshold definitions ────────────────────────────────────────────────────


class ThresholdEvaluator:
    """Evaluate per-metric pass/fail against soak thresholds."""

    # Thresholds (all times in milliseconds; rates per minute; counts)
    LLM_P95_MS: float = 30_000.0      # LLM p95 latency < 30 s
    TTFT_P95_MS: float = 10_000.0     # Time-to-first-token p95 < 10 s
    TOOL_P95_MS: float = 20_000.0     # Tool execution p95 < 20 s
    TOKEN_GROWTH_PER_MIN: float = 20_000.0  # Token growth <= 20k tokens/min
    SSE_RECONNECTS_PER_10MIN: float = 1.0   # SSE reconnects <= 1 per 10-min window
    TOOL_LOOP_MAX: int = 5             # Tool loops <= 5 consecutive
    PEAK_HEAP_MB: float = 250.0        # Peak JS heap <= 250 MB (where available)

    # Zero-tolerance checks (counts that must be 0)
    SECURITY_ERRORS_ALLOWED: int = 0
    SECRET_LEAKS_ALLOWED: int = 0

    def evaluate(self, metrics: dict) -> dict[str, dict]:
        """
        Evaluate *metrics* dict against all thresholds.

        Returns a mapping: check_name → {"pass": bool, "value": ..., "threshold": ...,
        "failures": [...]}  where *failures* contains {event_id, timestamp} dicts.
        """
        results: dict[str, dict] = {}

        def _check(name: str, value: Any, threshold: Any, passed: bool, failures: list) -> None:
            results[name] = {
                "pass": passed,
                "value": value,
                "threshold": threshold,
                "failures": failures,
            }

        llm_times = metrics.get("llm_latencies_ms", [])
        llm_p95 = _p95(llm_times) if llm_times else None
        _check(
            "llm_p95_latency_ms",
            llm_p95,
            self.LLM_P95_MS,
            llm_p95 is None or llm_p95 < self.LLM_P95_MS,
            [e for e in metrics.get("llm_slow_events", []) if e.get("duration_ms", 0) >= self.LLM_P95_MS],
        )

        ttft_times = metrics.get("ttft_ms", [])
        ttft_p95 = _p95(ttft_times) if ttft_times else None
        _check(
            "ttft_p95_ms",
            ttft_p95,
            self.TTFT_P95_MS,
            ttft_p95 is None or ttft_p95 < self.TTFT_P95_MS,
            [e for e in metrics.get("ttft_slow_events", []) if e.get("duration_ms", 0) >= self.TTFT_P95_MS],
        )

        tool_times = metrics.get("tool_latencies_ms", [])
        tool_p95 = _p95(tool_times) if tool_times else None
        _check(
            "tool_p95_latency_ms",
            tool_p95,
            self.TOOL_P95_MS,
            tool_p95 is None or tool_p95 < self.TOOL_P95_MS,
            [e for e in metrics.get("tool_slow_events", []) if e.get("duration_ms", 0) >= self.TOOL_P95_MS],
        )

        token_rate = metrics.get("token_growth_per_min", 0.0)
        _check(
            "token_growth_per_min",
            token_rate,
            self.TOKEN_GROWTH_PER_MIN,
            token_rate <= self.TOKEN_GROWTH_PER_MIN,
            metrics.get("token_growth_events", []),
        )

        sse_reconnects = metrics.get("sse_reconnects", 0)
        duration_min = max(metrics.get("duration_min", 1.0), 1.0)
        window_count = duration_min / 10.0
        allowed_reconnects = max(1, math.floor(self.SSE_RECONNECTS_PER_10MIN * window_count))
        _check(
            "sse_reconnects",
            sse_reconnects,
            f"<= {allowed_reconnects} for {duration_min:.0f}min",
            sse_reconnects <= allowed_reconnects,
            metrics.get("sse_reconnect_events", []),
        )

        security_errors = metrics.get("security_error_count", 0)
        _check(
            "security_errors",
            security_errors,
            self.SECURITY_ERRORS_ALLOWED,
            security_errors == self.SECURITY_ERRORS_ALLOWED,
            metrics.get("security_error_events", []),
        )

        secret_leaks = metrics.get("secret_leak_count", 0)
        _check(
            "secret_leaks",
            secret_leaks,
            self.SECRET_LEAKS_ALLOWED,
            secret_leaks == self.SECRET_LEAKS_ALLOWED,
            metrics.get("secret_leak_events", []),
        )

        max_loop = metrics.get("max_tool_loop", 0)
        _check(
            "tool_loop_depth",
            max_loop,
            self.TOOL_LOOP_MAX,
            max_loop <= self.TOOL_LOOP_MAX,
            metrics.get("tool_loop_events", []),
        )

        heap_mb = metrics.get("peak_heap_mb")
        if heap_mb is not None:
            _check(
                "peak_heap_mb",
                heap_mb,
                self.PEAK_HEAP_MB,
                heap_mb <= self.PEAK_HEAP_MB,
                metrics.get("heap_events", []),
            )
        else:
            results["peak_heap_mb"] = {"pass": True, "value": None, "threshold": self.PEAK_HEAP_MB,
                                        "failures": [], "note": "unavailable (browser API absent)"}

        # Balanced turns: agent:user ratio should be within 0.5–2.0
        agent_turns = metrics.get("agent_turns", 0)
        user_turns = metrics.get("user_turns", 0)
        if user_turns == 0:
            ratio = None
            balanced = agent_turns == 0
        else:
            ratio = round(agent_turns / user_turns, 3)
            balanced = 0.5 <= ratio <= 2.0
        results["balanced_turns"] = {
            "pass": balanced,
            "value": ratio,
            "threshold": "0.5 <= agent/user <= 2.0",
            "failures": [] if balanced else [{"note": f"ratio={ratio}, agent={agent_turns}, user={user_turns}"}],
        }

        return results


# ── Math helpers ─────────────────────────────────────────────────────────────


def _p95(values: list[float]) -> float | None:
    """Return the 95th-percentile of *values*, or None when list is empty."""
    if not values:
        return None
    if len(values) == 1:
        return values[0]
    return statistics.quantiles(sorted(values), n=100)[94]


# ── Debug-log data collector ─────────────────────────────────────────────────


class DebugLogCollector:
    """Fetch paginated debug events from the browse operator API."""

    def __init__(self, host: str, port: int, token: str | None = None) -> None:
        self.base_url = f"http://{host}:{port}"
        self.token = token

    def fetch_events(
        self,
        session_id: str,
        run_id: str,
        kind: str | None = None,
        since: str | None = None,
        max_events: int = 10_000,
    ) -> list[dict]:
        """Fetch all debug events for *session_id*/*run_id*, paginating as needed."""
        events: list[dict] = []
        from_idx = 0
        page_size = 100
        while True:
            params = [f"from={from_idx}", f"limit={page_size}"]
            if kind:
                params.append(f"kind={kind}")
            if since:
                params.append(f"since={since}")
            url = (
                f"{self.base_url}/api/operator/sessions/{session_id}"
                f"/runs/{run_id}/debug?{'&'.join(params)}"
            )
            try:
                req = urllib.request.Request(url)
                if self.token:
                    req.add_header("Authorization", f"Bearer {self.token}")
                with urllib.request.urlopen(req, timeout=15) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                if exc.code == 404:
                    break  # session/run not found — return what we have
                raise
            except (urllib.error.URLError, OSError) as exc:
                raise RuntimeError(f"Cannot connect to browse backend at {self.base_url}: {exc}") from exc

            page = data.get("events", [])
            events.extend(page)
            if not data.get("has_more", False) or len(events) >= max_events:
                break
            from_idx += len(page)
        return events


# ── Metrics aggregator ───────────────────────────────────────────────────────


_SECURITY_KEYWORDS = frozenset({"unauthorized", "forbidden", "auth_error", "security"})
_SECRET_FIELDS = re.compile(r"(password|token|secret|api[_-]?key|bearer)", re.IGNORECASE)


def _event_ref(event: dict) -> dict:
    """Return a lightweight failure-reference dict from a debug event."""
    return {"idx": event.get("idx"), "timestamp": event.get("timestamp"), "span_id": event.get("span_id")}


def aggregate_metrics(events: list[dict], duration_min: float) -> dict:
    """Compute soak metrics from a list of BrowseDebugEntry dicts."""
    llm_latencies: list[float] = []
    llm_slow: list[dict] = []
    tool_latencies: list[float] = []
    tool_slow: list[dict] = []
    ttft_ms_list: list[float] = []
    ttft_slow: list[dict] = []

    total_tokens: int = 0
    token_events: list[dict] = []
    agent_turns: int = 0
    user_turns: int = 0
    security_errors: list[dict] = []
    secret_leaks: list[dict] = []

    # Tool loop detection: track consecutive calls of the same tool
    max_tool_loop: int = 0
    tool_loop_events: list[dict] = []
    _prev_tool: str | None = None
    _loop_count: int = 0

    for event in events:
        kind = event.get("kind", "")
        level = event.get("level", "")
        attrs = event.get("attrs") or {}
        duration = event.get("duration_ms")
        ref = _event_ref(event)

        if kind == "llm_request" and duration is not None:
            llm_latencies.append(float(duration))
            if float(duration) >= ThresholdEvaluator.LLM_P95_MS:
                llm_slow.append({**ref, "duration_ms": duration})
            # TTFT heuristic: first_token_ms attr if present
            first_token = attrs.get("first_token_ms") or attrs.get("ttft_ms")
            if first_token is not None:
                ttft_ms_list.append(float(first_token))
                if float(first_token) >= ThresholdEvaluator.TTFT_P95_MS:
                    ttft_slow.append({**ref, "duration_ms": first_token})
            # Token accounting
            tokens = attrs.get("total_tokens") or attrs.get("tokens_used") or 0
            total_tokens += int(tokens) if tokens else 0
            if tokens:
                token_events.append({**ref, "tokens": tokens})

        elif kind == "tool_call" and event.get("status") in ("ok", "error") and duration is not None:
            tool_latencies.append(float(duration))
            if float(duration) >= ThresholdEvaluator.TOOL_P95_MS:
                tool_slow.append({**ref, "duration_ms": duration})
            # Loop detection
            tool_name = event.get("tool_name") or ""
            if tool_name == _prev_tool:
                _loop_count += 1
                if _loop_count > max_tool_loop:
                    max_tool_loop = _loop_count
                    tool_loop_events = [{**ref, "tool": tool_name, "consecutive": _loop_count + 1}]
            else:
                _prev_tool = tool_name
                _loop_count = 0

        elif kind == "turn_start":
            user_turns += 1
        elif kind == "agent_response":
            agent_turns += 1

        elif kind == "error":
            msg = (event.get("message") or "").lower()
            if any(kw in msg for kw in _SECURITY_KEYWORDS) or level == "error":
                # Check for security-specific errors
                if any(kw in msg for kw in _SECURITY_KEYWORDS):
                    security_errors.append(redact_event({**ref, "message": event.get("message", "")}))

        # Secret-leak scan: look for unredacted sensitive patterns in messages
        if not event.get("redacted", True):
            msg = event.get("message", "")
            if _SECRET_FIELDS.search(msg):
                leaked_ref = redact_event({**ref, "field": "message", "pattern": "[REDACTED]"})
                secret_leaks.append(leaked_ref)

    token_rate = (total_tokens / duration_min) if duration_min > 0 else 0.0

    return {
        "llm_latencies_ms": llm_latencies,
        "llm_slow_events": llm_slow,
        "ttft_ms": ttft_ms_list,
        "ttft_slow_events": ttft_slow,
        "tool_latencies_ms": tool_latencies,
        "tool_slow_events": tool_slow,
        "token_growth_per_min": token_rate,
        "token_growth_events": token_events,
        "sse_reconnects": 0,
        "sse_reconnect_events": [],
        "security_error_count": len(security_errors),
        "security_error_events": security_errors,
        "secret_leak_count": len(secret_leaks),
        "secret_leak_events": secret_leaks,
        "max_tool_loop": max_tool_loop,
        "tool_loop_events": tool_loop_events,
        "peak_heap_mb": None,
        "heap_events": [],
        "agent_turns": agent_turns,
        "user_turns": user_turns,
        "duration_min": duration_min,
        "total_events": len(events),
    }


# ── Soak-report.json builder ─────────────────────────────────────────────────


def build_report(
    session_id: str,
    run_id: str,
    events: list[dict],
    duration_min: float,
    mode: str,
) -> dict:
    """Return the soak-report dict (ready to JSON-serialize)."""
    metrics = aggregate_metrics(events, duration_min)
    evaluator = ThresholdEvaluator()
    checks = evaluator.evaluate(metrics)

    overall_pass = all(c["pass"] for c in checks.values())

    # Redact all embedded event references
    redacted_checks: dict = {}
    for name, check in checks.items():
        redacted_checks[name] = {
            **check,
            "failures": [redact_event(f) if isinstance(f, dict) else f for f in check.get("failures", [])],
        }

    return {
        "schema_version": "1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": mode,
        "session_id": session_id,
        "run_id": run_id,
        "duration_min": round(duration_min, 2),
        "total_events": metrics["total_events"],
        "overall_pass": overall_pass,
        "checks": redacted_checks,
        "summary": {
            "passed": sum(1 for c in checks.values() if c["pass"]),
            "failed": sum(1 for c in checks.values() if not c["pass"]),
            "total": len(checks),
        },
    }


# ── HTML report generator ────────────────────────────────────────────────────


def _badge(passed: bool) -> str:
    if passed:
        return '<span class="badge pass">PASS</span>'
    return '<span class="badge fail">FAIL</span>'


def build_html_report(report: dict) -> str:
    """Return HTML-escaped soak report dashboard. No inline scripts or event handlers."""

    def e(value: Any) -> str:
        return html.escape(str(value), quote=True)

    checks_rows = []
    for name, check in report.get("checks", {}).items():
        badge = _badge(check["pass"])
        val = e(check.get("value", "—"))
        thr = e(check.get("threshold", "—"))
        fails = len(check.get("failures", []))
        note = e(check.get("note", ""))
        checks_rows.append(
            f"<tr><td>{e(name)}</td><td>{badge}</td>"
            f"<td>{val}</td><td>{thr}</td>"
            f"<td>{fails}</td>"
            f"{'<td>' + note + '</td>' if note else '<td></td>'}</tr>"
        )

    overall = report.get("overall_pass", False)
    summary = report.get("summary", {})
    overall_badge = _badge(overall)
    generated = e(report.get("generated_at", ""))
    mode = e(report.get("mode", ""))
    session_id = e(report.get("session_id", ""))
    run_id = e(report.get("run_id", ""))
    duration = e(report.get("duration_min", 0))
    total_events = e(report.get("total_events", 0))
    passed_count = e(summary.get("passed", 0))
    failed_count = e(summary.get("failed", 0))
    total_count = e(summary.get("total", 0))

    checks_html = "\n".join(checks_rows)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Soak Diagnostics Report</title>
<style>
body{{font-family:system-ui,sans-serif;margin:2rem;color:#222;background:#f8f8f8}}
h1{{margin-bottom:.5rem}}
.meta{{color:#666;font-size:.9rem;margin-bottom:1.5rem}}
.badge{{padding:.2em .6em;border-radius:.3em;font-weight:bold;font-size:.85rem}}
.pass{{background:#d4edda;color:#155724}}
.fail{{background:#f8d7da;color:#721c24}}
table{{border-collapse:collapse;width:100%;background:#fff;box-shadow:0 1px 4px rgba(0,0,0,.1)}}
th,td{{padding:.5rem .75rem;border:1px solid #dee2e6;text-align:left}}
th{{background:#e9ecef;font-weight:600}}
.overall{{font-size:1.2rem;margin-bottom:1rem}}
</style>
</head>
<body>
<h1>Soak Diagnostics Report</h1>
<div class="meta">
  Generated: {generated} &nbsp;|&nbsp; Mode: {mode}
  &nbsp;|&nbsp; Session: {session_id} &nbsp;|&nbsp; Run: {run_id}
  &nbsp;|&nbsp; Duration: {duration} min &nbsp;|&nbsp; Events: {total_events}
</div>
<div class="overall">Overall: {overall_badge}
  &nbsp; {passed_count}/{total_count} checks passed
  &nbsp; ({failed_count} failed)
</div>
<table>
<thead><tr>
  <th>Check</th><th>Result</th><th>Value</th><th>Threshold</th>
  <th>Failures</th><th>Note</th>
</tr></thead>
<tbody>
{checks_html}
</tbody>
</table>
</body>
</html>"""


# ── Browser diagnostics stubs ────────────────────────────────────────────────


_BROWSER_STUBS_JS = r"""/**
 * soak-browser-stubs.js — Browser-side soak monitoring stubs.
 * Generated by soak-diagnostics.py. DO NOT edit manually.
 *
 * Attach with: <script src="soak-browser-stubs.js"></script>
 * All APIs degrade gracefully when unavailable.
 */
(function () {
  "use strict";

  /* ── Long-task detection via PerformanceObserver ────────────────────── */
  var longTasks = [];
  if (typeof PerformanceObserver !== "undefined") {
    try {
      var _ltObs = new PerformanceObserver(function (list) {
        list.getEntries().forEach(function (entry) {
          longTasks.push({ start: entry.startTime, duration: entry.duration });
        });
      });
      _ltObs.observe({ type: "longtask", buffered: true });
    } catch (e) {
      console.warn("[soak] PerformanceObserver/longtask unavailable:", e);
    }
  }

  /* ── SSE reconnect counting ─────────────────────────────────────────── */
  var sseReconnectCount = 0;
  var _origEventSource = typeof EventSource !== "undefined" ? EventSource : null;
  if (_origEventSource) {
    try {
      window.EventSource = function (url, cfg) {
        var es = new _origEventSource(url, cfg);
        var _firstOpen = true;
        es.addEventListener("open", function () {
          if (_firstOpen) { _firstOpen = false; return; }
          sseReconnectCount++;
        });
        return es;
      };
      window.EventSource.prototype = _origEventSource.prototype;
    } catch (e) {
      console.warn("[soak] EventSource wrapping unavailable:", e);
    }
  }

  /* ── Heap measurement (measureUserAgentSpecificMemory) ──────────────── */
  var heapSamplesMb = [];
  function measureHeap() {
    if (
      typeof performance !== "undefined" &&
      typeof performance.measureUserAgentSpecificMemory === "function" &&
      window.isSecureContext
    ) {
      performance.measureUserAgentSpecificMemory().then(function (result) {
        var mb = result.bytes / (1024 * 1024);
        heapSamplesMb.push(mb);
      }).catch(function (e) {
        console.warn("[soak] heap measurement failed:", e);
      });
    }
    // Else: gracefully unavailable — skip silently.
  }
  var _heapInterval = setInterval(measureHeap, 30000); // every 30 s

  /* ── Public API ──────────────────────────────────────────────────────── */
  window.__soakDiagnostics = {
    getLongTasks: function () { return longTasks.slice(); },
    getSseReconnectCount: function () { return sseReconnectCount; },
    getHeapSamplesMb: function () { return heapSamplesMb.slice(); },
    getPeakHeapMb: function () {
      return heapSamplesMb.length ? Math.max.apply(null, heapSamplesMb) : null;
    },
    stopHeapSampling: function () { clearInterval(_heapInterval); },
    getReport: function () {
      return {
        longTaskCount: longTasks.length,
        totalLongTaskMs: longTasks.reduce(function (s, t) { return s + t.duration; }, 0),
        sseReconnects: sseReconnectCount,
        peakHeapMb: window.__soakDiagnostics.getPeakHeapMb(),
        heapSampleCount: heapSamplesMb.length,
      };
    },
  };
})();
"""


def write_browser_stubs(out_dir: Path) -> Path:
    """Write soak-browser-stubs.js to *out_dir* and return the path."""
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / "soak-browser-stubs.js"
    out.write_text(_BROWSER_STUBS_JS, encoding="utf-8")
    return out


# ── Smoke mode: synthetic fixtures ──────────────────────────────────────────


def _make_smoke_events() -> list[dict]:
    """Return a synthetic event list that exercises all threshold checks."""
    now = datetime.now(timezone.utc)

    def _ts(offset_sec: float) -> str:
        return (now.replace(microsecond=0)).isoformat()

    events = [
        {"idx": 0, "kind": "session_start", "level": "info", "source": "operator_console",
         "message": "Smoke session started", "timestamp": _ts(0), "span_id": "a" * 16,
         "parent_span_id": None, "tool_name": None, "duration_ms": None,
         "status": None, "attrs": {}, "redacted": False},
        # user turn → agent response
        {"idx": 1, "kind": "turn_start", "level": "info", "source": "operator_console",
         "message": "Turn 1", "timestamp": _ts(1), "span_id": "b" * 16,
         "parent_span_id": "a" * 16, "tool_name": None, "duration_ms": None,
         "status": None, "attrs": {}, "redacted": False},
        {"idx": 2, "kind": "llm_request", "level": "debug", "source": "operator_console",
         "message": "LLM request dispatched", "timestamp": _ts(1.5), "span_id": "c" * 16,
         "parent_span_id": "b" * 16, "tool_name": None, "duration_ms": 1500.0,
         "status": "ok", "attrs": {"total_tokens": 500, "first_token_ms": 800.0}, "redacted": False},
        {"idx": 3, "kind": "tool_call", "level": "debug", "source": "operator_console",
         "message": "Tool call: list_files", "timestamp": _ts(3), "span_id": "d" * 16,
         "parent_span_id": "b" * 16, "tool_name": "list_files", "duration_ms": 350.0,
         "status": "ok", "attrs": {}, "redacted": False},
        {"idx": 4, "kind": "agent_response", "level": "info", "source": "operator_console",
         "message": "Assistant reply", "timestamp": _ts(4), "span_id": "e" * 16,
         "parent_span_id": "b" * 16, "tool_name": None, "duration_ms": None,
         "status": "ok", "attrs": {}, "redacted": False},
    ]
    return events


def run_smoke(out_dir: Path, write_html: bool) -> int:
    """Run smoke diagnostics on synthetic fixtures. Returns 0 on pass, 1 on fail."""
    t0 = time.monotonic()
    print("[soak] Smoke mode — using synthetic fixtures…")

    events = _make_smoke_events()
    report = build_report(
        session_id="smoke-session",
        run_id="smoke-run",
        events=events,
        duration_min=1.0,
        mode="smoke",
    )
    rc = _write_outputs(out_dir, report, write_html)

    elapsed = time.monotonic() - t0
    if elapsed > SMOKE_MAX_SECONDS:
        print(f"[soak] WARNING: smoke completed in {elapsed:.1f}s > {SMOKE_MAX_SECONDS}s limit")
    else:
        print(f"[soak] Smoke completed in {elapsed:.1f}s (limit {SMOKE_MAX_SECONDS}s)")
    return rc


# ── Full mode: real-session monitoring ──────────────────────────────────────


def run_full(
    session_id: str,
    run_id: str,
    host: str,
    port: int,
    duration_min: float,
    out_dir: Path,
    write_html: bool,
    token: str | None = None,
) -> int:
    """Poll the debug API for *duration_min*, then emit the soak report."""
    collector = DebugLogCollector(host=host, port=port, token=token)
    end_time = time.monotonic() + duration_min * 60
    t0 = time.monotonic()
    all_events: list[dict] = []
    seen_idxs: set[int] = set()

    print(f"[soak] Full mode: monitoring {session_id}/{run_id} for {duration_min:.0f} min…")
    try:
        while time.monotonic() < end_time:
            since = None
            if all_events:
                since = all_events[-1].get("timestamp")
            try:
                page = collector.fetch_events(session_id, run_id, since=since)
            except RuntimeError as exc:
                print(f"[soak] Warning: fetch failed: {exc}", file=sys.stderr)
                page = []

            for ev in page:
                idx = ev.get("idx")
                if idx not in seen_idxs:
                    seen_idxs.add(idx)
                    all_events.append(ev)

            remaining = end_time - time.monotonic()
            print(
                f"[soak] {int((time.monotonic() - t0) / 60)}min elapsed, "
                f"{len(all_events)} events, {int(remaining / 60)}min remaining",
                end="\r",
            )
            if remaining > 0:
                time.sleep(min(POLL_INTERVAL_SEC, max(0, remaining)))
    except KeyboardInterrupt:
        print("\n[soak] Interrupted — generating partial report…")

    actual_min = (time.monotonic() - t0) / 60.0
    print(f"\n[soak] Collected {len(all_events)} events in {actual_min:.1f} min")

    report = build_report(
        session_id=session_id,
        run_id=run_id,
        events=all_events,
        duration_min=actual_min,
        mode="full",
    )
    return _write_outputs(out_dir, report, write_html)


def _write_outputs(out_dir: Path, report: dict, write_html: bool) -> int:
    """Write soak-report.json (and optionally .html). Return 0 on pass, 1 on fail."""
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "soak-report.json"
    json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[soak] Wrote {json_path}")

    if write_html:
        html_path = out_dir / "soak-report.html"
        html_path.write_text(build_html_report(report), encoding="utf-8")
        print(f"[soak] Wrote {html_path}")

    overall = report.get("overall_pass", False)
    status = "PASS" if overall else "FAIL"
    print(f"[soak] Overall: {status}")
    for name, check in report.get("checks", {}).items():
        icon = "✅" if check["pass"] else "❌"
        print(f"  {icon} {name}: {check.get('value', '—')} (threshold: {check.get('threshold', '—')})")
    return 0 if overall else 1


# ── CLI ──────────────────────────────────────────────────────────────────────


def _parse_args(argv: list[str]) -> dict:
    args: dict = {
        "mode": None,
        "session": None,
        "run": None,
        "host": DEFAULT_HOST,
        "port": DEFAULT_PORT,
        "duration": DEFAULT_DURATION_MIN,
        "out_dir": DEFAULT_OUT_DIR,
        "html": False,
        "token": None,
    }
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--smoke":
            args["mode"] = "smoke"
        elif a == "--full":
            args["mode"] = "full"
        elif a == "--browser-stubs":
            args["mode"] = "browser-stubs"
        elif a == "--html":
            args["html"] = True
        elif a in ("--session", "--run", "--host", "--port", "--duration", "--out-dir", "--token"):
            i += 1
            val = argv[i] if i < len(argv) else ""
            key = a.lstrip("-").replace("-", "_")
            if key in ("port", "duration"):
                args[key] = float(val) if key == "duration" else int(val)
            elif key == "out_dir":
                args[key] = Path(val)
            else:
                args[key] = val
        i += 1
    return args


def main(argv: list[str] | None = None) -> int:
    argv = argv or sys.argv[1:]
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0

    args = _parse_args(argv)
    mode = args["mode"]
    out_dir: Path = args["out_dir"]

    if mode == "browser-stubs":
        path = write_browser_stubs(out_dir)
        print(f"[soak] Wrote {path}")
        return 0

    if mode == "smoke":
        return run_smoke(out_dir=out_dir, write_html=args["html"])

    if mode == "full":
        if not args["session"] or not args["run"]:
            print("Error: --full requires --session SESSION_ID and --run RUN_ID", file=sys.stderr)
            return 2
        return run_full(
            session_id=args["session"],
            run_id=args["run"],
            host=args["host"],
            port=int(args["port"]),
            duration_min=float(args["duration"]),
            out_dir=out_dir,
            write_html=args["html"],
            token=args.get("token"),
        )

    print("Error: specify --smoke, --full, or --browser-stubs", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
