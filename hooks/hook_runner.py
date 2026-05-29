#!/usr/bin/env python3
"""hook_runner.py — Unified Copilot CLI hook dispatcher.

Replaces 11 separate Python scripts with 1 process per event.
Performance: ~11 processes per tool call → 1 process.

Usage: python3 hook_runner.py <event>
Platform events (8 total, per GitHub Copilot docs):
  sessionStart, sessionEnd, userPromptSubmitted,
  preToolUse, postToolUse,
  agentStop, subagentStop,
  errorOccurred

This runner handles: sessionStart, sessionEnd, preToolUse, postToolUse,
agentStop, subagentStop, errorOccurred, userPromptSubmitted

Environment variables:
  HOOK_DRY_RUN=1       — Log denials but allow through (testing mode)
  HOOK_LOG_LEVEL=DEBUG — Enable verbose audit logging
"""

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Windows encoding fix (once, not per-hook)
if os.name == "nt":
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8", errors="replace")

# Add hooks dir to path for marker_auth imports
sys.path.insert(0, str(Path(__file__).resolve().parent))

MARKERS_DIR = Path.home() / ".copilot" / "markers"
SYNC_NUDGE_MARKER = MARKERS_DIR / "sync-nudge.json"
SYNC_FLUSH_MARKER = MARKERS_DIR / "sync-flush.json"


def _audit_log(event, tool, rule_name, decision, detail=""):
    """Append to audit log (best-effort, never blocks)."""
    try:
        MARKERS_DIR.mkdir(parents=True, exist_ok=True)
        log_file = MARKERS_DIR / "audit.jsonl"
        # Rotate if > 100KB
        if log_file.is_file() and log_file.stat().st_size > 100_000:
            rotated = MARKERS_DIR / "audit.jsonl.old"
            try:
                log_file.rename(rotated)
            except Exception:
                pass
        entry = json.dumps(
            {
                "ts": int(time.time()),
                "event": event,
                "tool": tool,
                "rule": rule_name,
                "decision": decision,
                "detail": str(detail)[:200],
            }
        )
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(entry + "\n")
    except Exception:
        pass


def _write_json_marker(path: Path, payload: dict) -> None:
    """Best-effort atomic marker write."""
    try:
        MARKERS_DIR.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        os.replace(str(tmp), str(path))
    except Exception:
        pass


def _record_sync_signal(event: str, data: dict) -> None:
    """Record local-first sync nudge/flush markers (never blocks, never throws)."""
    try:
        now = datetime.now(timezone.utc).isoformat()
        tool_name = data.get("toolName", "")
        session_id = data.get("sessionId") or os.environ.get("COPILOT_AGENT_SESSION_ID", "")
        payload = {
            "event": event,
            "session_id": session_id,
            "tool_name": tool_name,
            "ts": now,
        }

        if event == "postToolUse":
            _write_json_marker(SYNC_NUDGE_MARKER, payload)
        elif event == "sessionEnd":
            _write_json_marker(SYNC_FLUSH_MARKER, payload)

    except Exception:
        pass


def _check_and_set_dedup(event: str, payload_hash: str = "") -> bool:
    """Return True if this event+payload should be skipped as a double-fire duplicate.

    Uses a content hash of the payload so that the same event with different
    payloads is NOT considered a duplicate.  Only identical (event, payload)
    pairs fired within 500 ms are deduplicated (issue #348).

    Fail-open: any I/O error → returns False (process normally).
    """
    try:
        key = f"{event}-{payload_hash}" if payload_hash else event
        # Sanitise key to a safe filename: keep only alphanumeric + dash + dot
        safe_key = "".join(c if c.isalnum() or c in "-." else "_" for c in key)
        dedup_path = MARKERS_DIR / f"hook-dedup-{safe_key}"
        now_ms = int(time.time() * 1000)
        if dedup_path.is_file():
            try:
                last_ms = int(dedup_path.read_text(encoding="utf-8").strip())
                if now_ms - last_ms < 500:
                    return True  # duplicate within 500 ms window
            except Exception:
                pass
        MARKERS_DIR.mkdir(parents=True, exist_ok=True)
        dedup_path.write_text(str(now_ms), encoding="utf-8")
    except Exception:
        pass
    return False


def main():
    event = sys.argv[1] if len(sys.argv) > 1 else ""
    if not event:
        return

    # Recursion guard (issue #396): prevent hook from re-entering itself when
    # subprocesses spawned by rules (e.g. briefing.py) trigger further tool
    # calls that fire this hook again.  Fail-open: if the env var is not set we
    # proceed normally.
    if os.environ.get("SK_HOOK_ACTIVE") == "1":
        return

    # Mark active before dispatching so any subprocess we spawn inherits the guard.
    os.environ["SK_HOOK_ACTIVE"] = "1"

    # Parse stdin once (shared across all rules)
    try:
        raw = sys.stdin.read()
        data = json.loads(raw) if raw.strip() else {}
    except Exception:
        # Fail-open: parse error → allow through
        _audit_log(event, "", "", "parse-error")
        return

    # Double-fire deduplication (issue #348): skip duplicate invocations of the
    # same event+payload pair within a 500 ms window.  Payload hash is included
    # so that legitimate different-payload calls for the same event type are
    # processed normally.
    try:
        import hashlib as _hashlib

        _payload_hash = _hashlib.md5(raw.encode("utf-8", errors="replace")).hexdigest()[:8]
    except Exception:
        _payload_hash = ""
    if _check_and_set_dedup(event, _payload_hash):
        return

    dry_run = os.environ.get("HOOK_DRY_RUN", "") == "1"
    verbose = os.environ.get("HOOK_LOG_LEVEL", "") == "DEBUG"
    tool_name = data.get("toolName", "")

    # Import rules for this event
    try:
        from rules import get_rules_for_event

        rules = get_rules_for_event(event)
    except Exception as e:
        # Fail-open: import error → allow through
        _audit_log(event, tool_name, "", "import-error", str(e))
        return

    for rule in rules:
        # Tool matching (empty tools list = match all)
        if rule.tools and tool_name not in rule.tools:
            continue

        try:
            result = rule.evaluate(event, data)
        except Exception as e:
            # Fail-open: rule error → skip this rule
            _audit_log(event, tool_name, rule.name, "error", str(e))
            if verbose:
                print(f"  [HOOK DEBUG] Rule {rule.name} error: {e}", file=sys.stderr)
            continue

        if result is None:
            continue

        if event == "preToolUse":
            decision = result.get("permissionDecision", "")
            if decision == "deny":
                reason = result.get("permissionDecisionReason", "")
                if dry_run:
                    print(f"  [DRY RUN] {rule.name} would deny: {reason}")
                    _audit_log(event, tool_name, rule.name, "deny-dry", reason)
                else:
                    print(json.dumps(result))
                    _audit_log(event, tool_name, rule.name, "deny", reason)
                    return  # First deny wins
            else:
                # Non-deny results may carry a user-visible warning (e.g. ReadTrackerRule
                # info()).  For preToolUse, stdout is the JSON permission channel, so send
                # info messages to stderr to keep stdout parseable for deny decisions.
                msg = result.get("message", "")
                if msg:
                    print(msg, file=sys.stderr)
                # Preserve audit contract: preToolUse non-deny outcomes are "allow".
                # Verbose gate: only write audit entry when DEBUG logging is enabled.
                if verbose:
                    _audit_log(event, tool_name, rule.name, "allow", msg[:100] if msg else "")
        else:
            # postToolUse/sessionStart/sessionEnd/agentStop/subagentStop/errorOccurred
            # Structured JSON keys the Copilot CLI SDK expects on stdout:
            _STRUCTURED_KEYS = ("additionalContext", "sessionSummary", "modifiedPrompt")
            has_structured = any(k in result for k in _STRUCTURED_KEYS)
            if has_structured:
                # Emit structured JSON so the SDK can inject context into the LLM
                print(json.dumps(result))
                detail = next((result[k][:100] for k in _STRUCTURED_KEYS if k in result), "")
                _audit_log(event, tool_name, rule.name, "context", detail)
            else:
                # Backward-compatible: plain text info() results display as-is
                msg = result.get("message", "")
                if msg:
                    print(msg)
                _audit_log(event, tool_name, rule.name, "info", msg[:100] if msg else "")

    if event in {"postToolUse", "sessionEnd"}:
        _record_sync_signal(event, data)


if __name__ == "__main__":
    main()
