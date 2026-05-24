"""browse/routes/mission_atlas.py — Mission Atlas aggregate visualization endpoint.

Routes:
  GET /api/session/{id}/mission-atlas
  GET /api/sessions/{id}/mission-atlas   (plural alias)

One-pass aggregation of session-state/events.jsonl into a Mission Atlas
response suitable for a full-session visualization, including time/index
bucketed lanes, top-N ranked tool/skill/agent names, milestones, artifact
counts, and safe error sample.

Security invariants (same as debug_log.py):
  - session_id validated against strict lowercase UUID4 before any path
    construction; bad input → 404 with no value/path leakage.
  - Path confined via resolve_safe_child() + lstat guard.
  - events.jsonl read line-by-line; only a lightweight tuple (idx, ts_ms, lane)
    is retained per event in memory — raw payloads are never stored.
  - Skill events: path/content/description NEVER emitted; skill name only if it
    passes _SHORT_ENUM_RE (same as debug_log _SHORT_ENUM_RE).
  - Subagent events: raw agentId/toolCallId/parentToolCallId NEVER emitted;
    agent_name only if it passes _safe_subagent_str from debug_log.
  - Error events: no raw error text emitted; only derived safe category enum.
  - All string fields are bounded by strict length caps.
  - Artifact queries: session.db opened read-only with PRAGMA query_only=ON;
    only parametrized queries against known-safe tables are issued.
  - files/ directory: only count of regular non-symlink files emitted.

Query parameters:
  buckets  int  default=120, min=8, max=200

Auth: debug=True — Bearer/cookie only; ?token= rejected by dispatcher.
"""

import json
import os
import re
import sqlite3
import stat as _stat
import sys
from datetime import datetime, timezone
from pathlib import Path

if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")

from browse.api._common import json_error, json_ok, parse_int_param
from browse.core.registry import route
from browse.routes._checkpoint_index import (
    UUID4_RE,
    IndexOversize,
    is_safe_file_basename,
    parse_checkpoint_index,
    resolve_safe_child,
)

# ── Constants ──────────────────────────────────────────────────────────────────

_SCHEMA_VERSION = "1"

# Bucket constraints
_BUCKETS_DEFAULT = 120
_BUCKETS_MIN = 8
_BUCKETS_MAX = 200

# Hard caps
_TOP_N_CAP = 20  # max entries in top_tools / top_skills / top_agent_names
_MILESTONE_CAP = 200  # max milestones retained
_ERROR_SAMPLE_CAP = 5  # max error_sample entries
_LABEL_MAX = 80  # max milestone label length

# Gap threshold: flag bucket as gap when adjacent valid timestamps differ by >60s
_GAP_THRESHOLD_MS = 60_000.0

# Single-line parse threshold.
# Parse normal-sized JSONL events so skill/sub-agent metadata is counted even
# when the event contains large redacted-away bodies. Extremely large single
# lines still fall back to generic to avoid pathological memory spikes.
_MAX_PARSE_LINE_BYTES = 2 * 1024 * 1024

# Safe-string pattern for skill names and short enums (mirrors debug_log._SHORT_ENUM_RE)
_SHORT_ENUM_RE = re.compile(r"^[a-zA-Z0-9._-]{1,64}$")
_TIMESTAMP_SAFE_RE = re.compile(r"^[0-9T:Z+\-.]{1,64}$")

# Compaction event type indicators
_COMPACTION_KEYS = frozenset({"compaction", "compact", "context_window_compaction"})

# All valid lane keys
_ALL_LANES: tuple = ("tool", "hook", "skill", "subagent", "model", "turn", "system", "error", "generic")

# Kind → atlas lane mapping (same kind taxonomy as debug_log)
_KIND_TO_LANE: dict = {
    "tool_call": "tool",
    "hook": "hook",
    "subagent": "subagent",
    "llm_request": "model",
    "agent_response": "model",
    "turn_start": "turn",
    "session_start": "system",
    "error": "error",
    "generic": "generic",
    "raw": "generic",
}

# Milestone kind enum values (safe bounded set)
_MILESTONE_KINDS = frozenset(
    {
        "session_start",
        "turn_start",
        "subagent_start",
        "subagent_fail",
        "skill_invoked",
        "task_complete",
        "compaction",
        "error",
        "checkpoint",
        "rewind_snapshot",
    }
)

_MILESTONE_KIND_CAPS = {
    "session_start": 1,
    "turn_start": 20,
    "subagent_start": 50,
    "subagent_fail": 20,
    "skill_invoked": 50,
    "task_complete": 20,
    "compaction": 20,
    "error": 20,
    "checkpoint": 50,
    "rewind_snapshot": 50,
}


# ── Safe helpers ──────────────────────────────────────────────────────────────


def _ms_to_iso(ts_ms: float) -> str:
    """Convert epoch milliseconds to ISO-8601 UTC string. Returns '' on error."""
    try:
        dt = datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc)
        return dt.isoformat().replace("+00:00", "Z")
    except (ValueError, OSError, OverflowError):
        return ""


def _safe_timestamp(raw: object) -> "str | None":
    """Return a bounded ISO-like timestamp string or None."""
    if not isinstance(raw, str):
        return None
    candidate = raw[:64]
    if not _TIMESTAMP_SAFE_RE.match(candidate):
        return None
    return candidate


def _top_n(counts: "dict[str, int]", n: int) -> "list[dict]":
    """Return top-N name/count entries from a counts dict, sorted by count desc."""
    return [{"name": k, "count": v} for k, v in sorted(counts.items(), key=lambda x: x[1], reverse=True)[:n]]


# ── Single-pass accumulator ───────────────────────────────────────────────────


class _AtlasAccumulator:
    """Single-pass accumulator for Mission Atlas metrics.

    Only lightweight per-event tuples (line_idx, ts_ms|None, lane) are kept in
    memory.  Raw event payloads (args/results/content/prompts/errors/paths) are
    never retained.
    """

    __slots__ = (
        "total_events",
        "first_ts_ms",
        "last_ts_ms",
        "lane_totals",
        "tool_counts",
        "skill_counts",
        "agent_counts",
        "milestones",
        "_milestone_count",
        "_milestone_seen",
        "_milestone_kind_counts",
        "error_sample",
        "_error_count",
        "compaction_count",
        "_event_tuples",  # list of (line_idx, ts_ms|None, lane)
    )

    def __init__(self) -> None:
        self.total_events: int = 0
        self.first_ts_ms: float | None = None
        self.last_ts_ms: float | None = None
        self.lane_totals: dict[str, int] = {k: 0 for k in _ALL_LANES}
        self.tool_counts: dict[str, int] = {}
        self.skill_counts: dict[str, int] = {}
        self.agent_counts: dict[str, int] = {}
        self.milestones: list[dict] = []
        self._milestone_count: int = 0
        self._milestone_seen: int = 0
        self._milestone_kind_counts: dict[str, int] = {}
        self.error_sample: list[dict] = []
        self._error_count: int = 0
        self.compaction_count: int = 0
        self._event_tuples: list[tuple] = []

    def feed(self, line_idx: int, raw_line: str) -> None:
        """Process one raw line from events.jsonl.

        Helpers from debug_log are imported lazily inside this method to avoid
        circular import at module load.  All imports are from the already-loaded
        debug_log module (same process) so they are cheap after the first call.
        """
        from browse.routes.debug_log import (  # noqa: PLC0415
            _classify_cli_event_type,
            _derive_error_category,
            _extract_tool_name,
            _parse_ts_ms,
            _safe_subagent_str,
        )

        self.total_events += 1

        # Truncation guard — skip pathological event bodies.
        raw_bytes = len(raw_line.encode("utf-8", errors="replace"))
        if raw_bytes > _MAX_PARSE_LINE_BYTES:
            self._event_tuples.append((line_idx, None, "generic"))
            self.lane_totals["generic"] += 1
            return

        try:
            event = json.loads(raw_line)
        except (json.JSONDecodeError, ValueError):
            self._event_tuples.append((line_idx, None, "generic"))
            self.lane_totals["generic"] += 1
            return

        if not isinstance(event, dict):
            self._event_tuples.append((line_idx, None, "generic"))
            self.lane_totals["generic"] += 1
            return

        event_type = event.get("type")
        if not isinstance(event_type, str) or not event_type:
            self._event_tuples.append((line_idx, None, "generic"))
            self.lane_totals["generic"] += 1
            return

        # Timestamp
        ts_ms = _parse_ts_ms(event.get("timestamp"))
        if ts_ms is not None:
            if self.first_ts_ms is None:
                self.first_ts_ms = ts_ms
            self.last_ts_ms = ts_ms

        # Kind + lane
        kind = _classify_cli_event_type(event_type)
        et_lower = event_type.lower()
        is_skill = et_lower.startswith("skill.")
        lane = "skill" if is_skill else _KIND_TO_LANE.get(kind, "generic")

        self._event_tuples.append((line_idx, ts_ms, lane))
        self.lane_totals[lane] = self.lane_totals.get(lane, 0) + 1

        data = event.get("data") if isinstance(event.get("data"), dict) else {}

        # Tool name counting
        if kind == "tool_call":
            tool_name = _extract_tool_name(event)
            if tool_name:
                self.tool_counts[tool_name] = self.tool_counts.get(tool_name, 0) + 1

        # Skill name counting (only if passes _SHORT_ENUM_RE)
        if is_skill:
            raw_skill_name = data.get("name")
            if isinstance(raw_skill_name, str) and _SHORT_ENUM_RE.match(raw_skill_name):
                sk = raw_skill_name[:64]
                self.skill_counts[sk] = self.skill_counts.get(sk, 0) + 1

        # Agent name counting
        if kind == "subagent":
            raw_agent = data.get("agentName") or data.get("agentDisplayName")
            if isinstance(raw_agent, str) and raw_agent:
                safe_name, _ = _safe_subagent_str(raw_agent)
                if safe_name:
                    self.agent_counts[safe_name] = self.agent_counts.get(safe_name, 0) + 1

        # Compaction detection
        if any(kw in et_lower for kw in _COMPACTION_KEYS):
            self.compaction_count += 1

        # Error sample (safe: derived category only, no raw error text)
        if kind == "error":
            self._error_count += 1
            if len(self.error_sample) < _ERROR_SAMPLE_CAP:
                raw_err = data.get("error") or data.get("message")
                err_cat = _derive_error_category(raw_err) if isinstance(raw_err, str) else "unknown"
                # Safe event_type: only emit if it passes the enum pattern
                et_safe = event_type if _SHORT_ENUM_RE.match(event_type) else "error"
                self.error_sample.append(
                    {
                        "idx": line_idx,
                        "timestamp": _safe_timestamp(event.get("timestamp")),
                        "event_type": et_safe,
                        "error_category": err_cat,
                    }
                )

        # Milestones
        ms = self._classify_milestone(line_idx, event_type, et_lower, kind, data, event.get("timestamp"))
        if ms is not None:
            self._record_milestone(ms)

    def _record_milestone(self, milestone: dict) -> None:
        """Retain a bounded, per-kind balanced milestone sample."""
        kind = milestone.get("kind")
        if not isinstance(kind, str) or kind not in _MILESTONE_KINDS:
            return
        self._milestone_seen += 1
        if self._milestone_count >= _MILESTONE_CAP:
            return
        kind_count = self._milestone_kind_counts.get(kind, 0)
        if kind_count >= _MILESTONE_KIND_CAPS.get(kind, 10):
            return
        self.milestones.append(milestone)
        self._milestone_kind_counts[kind] = kind_count + 1
        self._milestone_count += 1

    def _classify_milestone(
        self,
        idx: int,
        event_type: str,
        et_lower: str,
        kind: str,
        data: dict,
        raw_ts: object,
    ) -> "dict | None":
        """Classify an event as a milestone; return a safe bounded dict or None."""
        ts = _safe_timestamp(raw_ts)

        # Skill invocation
        if et_lower == "skill.invoked":
            raw_name = data.get("name")
            if isinstance(raw_name, str) and _SHORT_ENUM_RE.match(raw_name):
                label = f"skill:{raw_name[:64]}"
            else:
                label = "skill"
            return {
                "idx": idx,
                "timestamp": ts,
                "kind": "skill_invoked",
                "label": label[:_LABEL_MAX],
                "bucket_idx": None,
            }

        # Subagent start
        if et_lower in ("subagent.started", "subagent.start"):
            from browse.routes.debug_log import _safe_subagent_str  # noqa: PLC0415

            raw_name = data.get("agentName")
            safe_name, _ = _safe_subagent_str(raw_name) if isinstance(raw_name, str) else (None, False)
            label = f"subagent:{safe_name}" if safe_name else "subagent.start"
            return {
                "idx": idx,
                "timestamp": ts,
                "kind": "subagent_start",
                "label": label[:_LABEL_MAX],
                "bucket_idx": None,
            }

        # Subagent fail
        if et_lower == "subagent.failed":
            from browse.routes.debug_log import _derive_error_category  # noqa: PLC0415

            raw_err = data.get("error")
            err_cat = _derive_error_category(raw_err) if isinstance(raw_err, str) else "unknown"
            return {
                "idx": idx,
                "timestamp": ts,
                "kind": "subagent_fail",
                "label": f"failed:{err_cat}"[:_LABEL_MAX],
                "bucket_idx": None,
            }

        # Session start
        if kind == "session_start" and et_lower in ("session.start", "session_start"):
            return {"idx": idx, "timestamp": ts, "kind": "session_start", "label": "session.start", "bucket_idx": None}

        # Turn start
        if kind == "turn_start" and et_lower in ("turn.start", "turn_start", "assistant.turn_start"):
            return {"idx": idx, "timestamp": ts, "kind": "turn_start", "label": "turn.start", "bucket_idx": None}

        # Error events
        if kind == "error":
            from browse.routes.debug_log import _derive_error_category  # noqa: PLC0415

            raw_err = data.get("error") or data.get("message")
            err_cat = _derive_error_category(raw_err) if isinstance(raw_err, str) else "unknown"
            return {
                "idx": idx,
                "timestamp": ts,
                "kind": "error",
                "label": f"error:{err_cat}"[:_LABEL_MAX],
                "bucket_idx": None,
            }

        # Compaction
        if any(kw in et_lower for kw in _COMPACTION_KEYS):
            comp_kind_raw = data.get("compactionKind")
            if isinstance(comp_kind_raw, str) and _SHORT_ENUM_RE.match(comp_kind_raw):
                label = f"compaction:{comp_kind_raw[:32]}"
            else:
                label = "compaction"
            return {"idx": idx, "timestamp": ts, "kind": "compaction", "label": label[:_LABEL_MAX], "bucket_idx": None}

        # Task complete (system.notification agent_completed)
        if et_lower.startswith("system.notification"):
            raw_kind_obj = data.get("kind")
            if isinstance(raw_kind_obj, dict):
                kt = raw_kind_obj.get("type")
                if kt == "agent_completed":
                    return {
                        "idx": idx,
                        "timestamp": ts,
                        "kind": "task_complete",
                        "label": "agent_completed",
                        "bucket_idx": None,
                    }

        return None


# ── Bucket builder ────────────────────────────────────────────────────────────


def _build_buckets(
    event_tuples: "list[tuple]",
    bucket_count: int,
    first_ts_ms: "float | None",
    last_ts_ms: "float | None",
) -> "list[dict]":
    """Build bucket array from event tuples.

    event_tuples: list of (line_idx, ts_ms|None, lane)

    Two modes:
      - Time bucketing: when first_ts_ms/last_ts_ms are both available, split
        [first_ts_ms, last_ts_ms] into bucket_count equal-width time slices.
        Events without a timestamp are omitted from bucket assignment in this mode.
      - Index bucketing: when timestamps are absent, split the event stream by
        line_idx into bucket_count index-based slices.

    Gap detection (time mode only): a bucket is marked is_gap=True when the
    minimum timestamp of its events is more than _GAP_THRESHOLD_MS after the
    maximum timestamp of all preceding non-empty buckets.
    """
    if not event_tuples:
        # Return empty skeleton buckets
        return [_empty_bucket(i, None, None, None, None, None, None) for i in range(bucket_count)]

    use_time = first_ts_ms is not None and last_ts_ms is not None

    if use_time:
        duration_ms = max(last_ts_ms - first_ts_ms, 1.0)
        bucket_width = duration_ms / bucket_count
    else:
        # Derive bucket_width from max line_idx so line_idx-based assignment
        # stays within [0, bucket_count).
        max_line_idx = max(t[0] for t in event_tuples)
        bucket_width = max((max_line_idx + 1) / bucket_count, 1.0)

    # Initialise buckets
    buckets: list[dict] = []
    for i in range(bucket_count):
        if use_time:
            start_rel = i * bucket_width
            end_rel = (i + 1) * bucket_width
            ts_s = _ms_to_iso(first_ts_ms + start_rel) or None
            ts_e = _ms_to_iso(first_ts_ms + end_rel) or None
        else:
            start_rel = None
            end_rel = None
            ts_s = None
            ts_e = None
        buckets.append(_empty_bucket(i, start_rel, end_rel, ts_s, ts_e, None, None))

    # Per-bucket min/max ts_ms for gap detection (time mode only)
    # We track these separately to avoid storing them in the final output dict.
    bucket_min_ts: list[float | None] = [None] * bucket_count
    bucket_max_ts: list[float | None] = [None] * bucket_count

    # Assign events to buckets
    for line_idx, ts_ms, lane in event_tuples:
        if use_time:
            if ts_ms is None:
                continue  # omit timestampless events in time mode
            rel = ts_ms - first_ts_ms
            bucket_i = min(int(rel / bucket_width), bucket_count - 1)
        else:
            bucket_i = min(int(line_idx / bucket_width), bucket_count - 1)

        b = buckets[bucket_i]
        b["event_count"] += 1
        b["lanes"][lane] = b["lanes"].get(lane, 0) + 1
        if lane == "error":
            b["error_count"] += 1

        # Track start/end idx
        if b["start_idx"] is None or line_idx < b["start_idx"]:
            b["start_idx"] = line_idx
        if b["end_idx"] is None or line_idx > b["end_idx"]:
            b["end_idx"] = line_idx

        # Track min/max ts for gap detection
        if use_time and ts_ms is not None:
            if bucket_min_ts[bucket_i] is None or ts_ms < bucket_min_ts[bucket_i]:
                bucket_min_ts[bucket_i] = ts_ms
            if bucket_max_ts[bucket_i] is None or ts_ms > bucket_max_ts[bucket_i]:
                bucket_max_ts[bucket_i] = ts_ms

    # Dominant lane
    for b in buckets:
        lanes = b["lanes"]
        best_lane, best_count = "generic", 0
        for ln, cnt in lanes.items():
            if cnt > best_count:
                best_lane, best_count = ln, cnt
        b["dominant_lane"] = best_lane

    # Gap detection (time mode): bucket is gap when its earliest timestamp is
    # more than _GAP_THRESHOLD_MS after the latest timestamp of all preceding
    # non-empty buckets.
    if use_time:
        prev_max: float | None = None
        for i, b in enumerate(buckets):
            if b["event_count"] == 0:
                continue
            cur_min = bucket_min_ts[i]
            if prev_max is not None and cur_min is not None:
                if cur_min - prev_max > _GAP_THRESHOLD_MS:
                    b["is_gap"] = True
            cur_max = bucket_max_ts[i]
            if cur_max is not None:
                if prev_max is None or cur_max > prev_max:
                    prev_max = cur_max

    return buckets


def _empty_bucket(
    i: int,
    start_rel: "float | None",
    end_rel: "float | None",
    ts_s: "str | None",
    ts_e: "str | None",
    start_idx: "int | None",
    end_idx: "int | None",
) -> dict:
    return {
        "bucket_idx": i,
        "start_idx": start_idx,
        "end_idx": end_idx,
        "event_count": 0,
        "start_rel_ms": round(start_rel, 3) if start_rel is not None else None,
        "end_rel_ms": round(end_rel, 3) if end_rel is not None else None,
        "ts_start": ts_s,
        "ts_end": ts_e,
        "lanes": {k: 0 for k in _ALL_LANES},
        "dominant_lane": "generic",
        "error_count": 0,
        "is_gap": False,
    }


def _bucket_idx_for(
    line_idx: int,
    ts_ms: "float | None",
    first_ts_ms: "float | None",
    bucket_width: float,
    bucket_count: int,
    use_time: bool,
) -> "int | None":
    """Compute bucket_idx for a given event (used to annotate milestones)."""
    if use_time:
        if ts_ms is None or first_ts_ms is None:
            return None
        rel = ts_ms - first_ts_ms
        return min(int(rel / bucket_width), bucket_count - 1)
    else:
        return min(int(line_idx / bucket_width), bucket_count - 1)


def _bucket_idx_for_timestamp(
    timestamp: object,
    first_event_at: object,
    last_event_at: object,
    bucket_count: int,
) -> "int | None":
    """Map an artifact timestamp to a bucket without exposing artifact content."""
    if not isinstance(timestamp, str) or not isinstance(first_event_at, str) or not isinstance(last_event_at, str):
        return None
    try:
        from browse.routes.debug_log import _parse_ts_ms  # noqa: PLC0415

        ts_ms = _parse_ts_ms(timestamp)
        first_ms = _parse_ts_ms(first_event_at)
        last_ms = _parse_ts_ms(last_event_at)
    except (ImportError, AttributeError):
        return None
    if ts_ms is None or first_ms is None or last_ms is None:
        return None
    bucket_width = max(last_ms - first_ms, 1.0) / bucket_count
    return _bucket_idx_for(0, ts_ms, first_ms, bucket_width, bucket_count, True)


def _sort_milestones(milestones: "list[dict]") -> "list[dict]":
    """Sort milestones chronologically when timestamps exist, then by idx."""
    try:
        from browse.routes.debug_log import _parse_ts_ms  # noqa: PLC0415
    except (ImportError, AttributeError):
        _parse_ts_ms = None

    def key(milestone: dict) -> tuple:
        ts_ms = _parse_ts_ms(milestone.get("timestamp")) if _parse_ts_ms else None
        idx = milestone.get("idx")
        bucket_idx = milestone.get("bucket_idx")
        return (
            ts_ms is None,
            ts_ms if ts_ms is not None else float("inf"),
            idx if isinstance(idx, int) else 10**12,
            bucket_idx if isinstance(bucket_idx, int) else 10**12,
        )

    return sorted(milestones, key=key)


# ── Artifact counters ─────────────────────────────────────────────────────────


def _collect_checkpoint_artifacts(
    session_dir: Path,
    first_event_at: object,
    last_event_at: object,
    bucket_count: int,
    milestone_slots: int,
) -> "tuple[int, list[dict], bool]":
    """Count checkpoint index entries and return safe checkpoint milestones."""
    index_path = session_dir / "checkpoints" / "index.md"
    try:
        entries = parse_checkpoint_index(index_path)
    except IndexOversize:
        return 0, [], False

    milestones: list[dict] = []
    safe_count = 0
    truncated = False
    for entry in entries:
        seq = entry.get("seq")
        file_basename = entry.get("file_basename")
        if not isinstance(file_basename, str) or not is_safe_file_basename(file_basename):
            continue
        safe_count += 1
        if len(milestones) >= milestone_slots:
            truncated = True
            continue
        label = f"checkpoint:{seq}" if isinstance(seq, int) else "checkpoint"
        timestamp: str | None = None
        raw_path = session_dir / "checkpoints" / file_basename
        try:
            lst = raw_path.lstat()
            if _stat.S_ISREG(lst.st_mode) and not _stat.S_ISLNK(lst.st_mode):
                timestamp = (
                    datetime.fromtimestamp(float(lst.st_mtime), tz=timezone.utc).isoformat().replace("+00:00", "Z")
                )
        except (OSError, OverflowError, ValueError):
            timestamp = None
        milestones.append(
            {
                "idx": None,
                "timestamp": timestamp,
                "kind": "checkpoint",
                "label": label[:_LABEL_MAX],
                "bucket_idx": _bucket_idx_for_timestamp(timestamp, first_event_at, last_event_at, bucket_count),
            }
        )
    return safe_count, milestones, truncated


def _collect_rewind_artifacts(
    session_dir: Path,
    first_event_at: object,
    last_event_at: object,
    bucket_count: int,
    milestone_slots: int,
) -> "tuple[int, list[dict], bool]":
    """Count rewind snapshots and return safe rewind milestones."""
    index_path = session_dir / "rewind-snapshots" / "index.json"
    try:
        from browse.routes.rewind_snapshots import (  # noqa: PLC0415
            SNAPSHOTS_MAX,
            _build_snapshot_summary,
            _read_index_json,
        )

        data = _read_index_json(index_path)
        if data is None or data == "__OVERSIZE__":
            return 0, [], False
        if isinstance(data, list):
            raw_snapshots = data
        elif isinstance(data, dict) and isinstance(data.get("snapshots"), list):
            raw_snapshots = data["snapshots"]
        else:
            raw_snapshots = []
    except (ImportError, OSError, ValueError):
        return 0, [], False

    capped = raw_snapshots[:SNAPSHOTS_MAX]
    milestones: list[dict] = []
    truncated = False
    for idx, raw in enumerate(capped):
        if len(milestones) >= milestone_slots:
            truncated = True
            break
        summary = _build_snapshot_summary(raw, idx)
        if summary is None:
            continue
        timestamp = summary.get("timestamp")
        label = f"rewind:{idx + 1}"
        milestones.append(
            {
                "idx": None,
                "timestamp": timestamp if isinstance(timestamp, str) else None,
                "kind": "rewind_snapshot",
                "label": label[:_LABEL_MAX],
                "bucket_idx": _bucket_idx_for_timestamp(timestamp, first_event_at, last_event_at, bucket_count),
            }
        )
    return len(capped), milestones, truncated


def _count_session_db_todos(session_dir: Path) -> dict:
    """Query session.db (read-only) for todo/dep counts.

    Returns a safe bounded dict.  Fails silently to zero on any error.
    The database is opened in read-only URI mode; only parameterless queries
    against known-safe table names are issued.
    """
    result = {
        "todos_total": 0,
        "todos_done": 0,
        "todos_blocked": 0,
        "todo_deps": 0,
    }
    db_path = session_dir / "session.db"

    # lstat check: reject symlinks and non-regular files
    try:
        lst = db_path.lstat()
    except (OSError, FileNotFoundError):
        return result
    if _stat.S_ISLNK(lst.st_mode) or not _stat.S_ISREG(lst.st_mode):
        return result

    try:
        uri = db_path.as_uri() + "?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=2.0, check_same_thread=True)
        try:
            conn.execute("PRAGMA query_only = ON")
            tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name IN ('todos','todo_deps')"
                )
            }
            if "todos" in tables:
                row = conn.execute("SELECT COUNT(*) FROM todos").fetchone()
                result["todos_total"] = int(row[0]) if row else 0
                row = conn.execute("SELECT COUNT(*) FROM todos WHERE status='done'").fetchone()
                result["todos_done"] = int(row[0]) if row else 0
                row = conn.execute("SELECT COUNT(*) FROM todos WHERE status='blocked'").fetchone()
                result["todos_blocked"] = int(row[0]) if row else 0
            if "todo_deps" in tables:
                row = conn.execute("SELECT COUNT(*) FROM todo_deps").fetchone()
                result["todo_deps"] = int(row[0]) if row else 0
        finally:
            conn.close()
    except (sqlite3.Error, OSError, ValueError):
        pass

    return result


def _count_files_dir(session_dir: Path) -> int:
    """Count regular non-symlink files in session-state/files/. Returns 0 on error."""
    files_dir = session_dir / "files"
    try:
        lst_dir = files_dir.lstat()
        if not _stat.S_ISDIR(lst_dir.st_mode):
            return 0
    except (OSError, FileNotFoundError):
        return 0

    count = 0
    try:
        for entry in files_dir.iterdir():
            try:
                entry_st = entry.lstat()
                if _stat.S_ISREG(entry_st.st_mode) and not _stat.S_ISLNK(entry_st.st_mode):
                    count += 1
            except OSError:
                continue
    except OSError:
        return 0
    return count


# ── Main streaming aggregator ─────────────────────────────────────────────────


def _stream_mission_atlas(events_path: Path, bucket_count: int) -> dict:
    """One-pass streaming aggregator for mission atlas.

    Reads events.jsonl line-by-line.  Only lightweight per-event tuples are
    kept in memory.  Returns a complete aggregate dict (session_id not included;
    caller injects it before responding).
    """
    # File size for event_file_bytes (safe: only the integer size)
    try:
        file_bytes = events_path.stat().st_size
    except OSError:
        file_bytes = 0

    acc = _AtlasAccumulator()
    line_no = 0

    with events_path.open(encoding="utf-8", errors="replace") as fh:
        for raw_line in fh:
            raw_line = raw_line.rstrip("\r\n")
            if not raw_line:
                line_no += 1
                continue
            acc.feed(line_no, raw_line)
            line_no += 1

    total_events = acc.total_events
    first_ts_ms = acc.first_ts_ms
    last_ts_ms = acc.last_ts_ms

    # Duration
    duration_ms: float | None = None
    if first_ts_ms is not None and last_ts_ms is not None:
        duration_ms = max(last_ts_ms - first_ts_ms, 0.0)

    # Timestamps as ISO strings
    first_event_at = _ms_to_iso(first_ts_ms) if first_ts_ms is not None else None
    last_event_at = _ms_to_iso(last_ts_ms) if last_ts_ms is not None else None

    # Buckets
    use_time = first_ts_ms is not None and last_ts_ms is not None
    if use_time:
        bucket_width = max(last_ts_ms - first_ts_ms, 1.0) / bucket_count
    else:
        max_line_idx = max((t[0] for t in acc._event_tuples), default=0)
        bucket_width = max((max_line_idx + 1) / bucket_count, 1.0)

    buckets = _build_buckets(acc._event_tuples, bucket_count, first_ts_ms, last_ts_ms)

    # Annotate milestones with their bucket_idx
    for ms in acc.milestones:
        ms_idx = ms.get("idx") or 0
        ms_ts_ms: float | None = None
        if isinstance(ms.get("timestamp"), str):
            from browse.routes.debug_log import _parse_ts_ms as _pts  # noqa: PLC0415

            ms_ts_ms = _pts(ms["timestamp"])
        ms["bucket_idx"] = _bucket_idx_for(ms_idx, ms_ts_ms, first_ts_ms, bucket_width, bucket_count, use_time)

    # Top-N aggregates
    top_tools = _top_n(acc.tool_counts, _TOP_N_CAP)
    top_skills = _top_n(acc.skill_counts, _TOP_N_CAP)
    top_agent_names = _top_n(acc.agent_counts, _TOP_N_CAP)

    tools_truncated = len(acc.tool_counts) > _TOP_N_CAP
    skills_truncated = len(acc.skill_counts) > _TOP_N_CAP
    agents_truncated = len(acc.agent_counts) > _TOP_N_CAP
    milestones_truncated = acc._milestone_seen > acc._milestone_count

    return {
        "schema_version": _SCHEMA_VERSION,
        # "session_id" injected by caller
        "total_events": total_events,
        "event_file_bytes": file_bytes,
        "first_event_at": first_event_at,
        "last_event_at": last_event_at,
        "duration_ms": round(duration_ms, 1) if duration_ms is not None else None,
        "bucket_count": len(buckets),
        "buckets": buckets,
        "lane_totals": acc.lane_totals,
        "top_tools": top_tools,
        "top_skills": top_skills,
        "top_agent_names": top_agent_names,
        "milestones": acc.milestones,
        "error_count": acc._error_count,
        "error_sample": acc.error_sample,
        "_compaction_count_internal": acc.compaction_count,
        "caps": {
            "top_n": _TOP_N_CAP,
            "milestones": _MILESTONE_CAP,
            "error_sample": _ERROR_SAMPLE_CAP,
            "buckets_min": _BUCKETS_MIN,
            "buckets_max": _BUCKETS_MAX,
        },
        "truncated": {
            "tools": tools_truncated,
            "skills": skills_truncated,
            "agents": agents_truncated,
            "milestones": milestones_truncated,
        },
    }


# ── Handler ───────────────────────────────────────────────────────────────────


def _handle_mission_atlas(db, params, token, nonce, session_id: str = "") -> tuple:
    """Shared handler for GET /api/session/{id}/mission-atlas.

    Auth is enforced upstream by the dispatcher (debug=True gate).  Do NOT add
    a handler-level token check here — it would break the legitimate open-auth
    loopback flow used by the default hosted launcher.
    """
    # 1. Validate session_id (uniform 404, no value leakage)
    if not session_id or not UUID4_RE.match(session_id):
        return json_error("mission atlas not found", "NOT_FOUND", 404)

    # 2. Resolve and confine events.jsonl path (lstat rejects final symlink)
    events_path = resolve_safe_child(session_id, "events.jsonl")
    if events_path is None:
        return json_error("mission atlas not found", "NOT_FOUND", 404)

    # 3. Parse query parameters
    bucket_count = parse_int_param(params, "buckets", _BUCKETS_DEFAULT, _BUCKETS_MIN, _BUCKETS_MAX)

    # 4. Stream and aggregate
    try:
        result = _stream_mission_atlas(events_path, bucket_count)
    except OSError:
        return json_error("mission atlas not found", "NOT_FOUND", 404)

    # 5. Inject session_id
    result["session_id"] = session_id

    # 6. Artifact counts (safe; no content emitted)
    session_dir = events_path.parent
    remaining_milestones = max(0, _MILESTONE_CAP - len(result["milestones"]))
    cp_count, cp_milestones, cp_truncated = _collect_checkpoint_artifacts(
        session_dir,
        result.get("first_event_at"),
        result.get("last_event_at"),
        result["bucket_count"],
        remaining_milestones,
    )
    remaining_milestones = max(0, _MILESTONE_CAP - len(result["milestones"]) - len(cp_milestones))
    rw_count, rw_milestones, rw_truncated = _collect_rewind_artifacts(
        session_dir,
        result.get("first_event_at"),
        result.get("last_event_at"),
        result["bucket_count"],
        remaining_milestones,
    )
    todo_counts = _count_session_db_todos(session_dir)
    files_count = _count_files_dir(session_dir)

    compaction_count = result.pop("_compaction_count_internal", 0)
    result["milestones"] = _sort_milestones((result["milestones"] + cp_milestones + rw_milestones)[:_MILESTONE_CAP])
    result["truncated"]["milestones"] = bool(result["truncated"].get("milestones") or cp_truncated or rw_truncated)

    result["artifact_counts"] = {
        "checkpoint_files": cp_count,
        "rewind_snapshots": rw_count,
        "todos_total": todo_counts["todos_total"],
        "todos_done": todo_counts["todos_done"],
        "todos_blocked": todo_counts["todos_blocked"],
        "todo_deps": todo_counts["todo_deps"],
        "files": files_count,
        "compactions": compaction_count,
    }

    return json_ok(result)


# ── Route registration ────────────────────────────────────────────────────────


@route("/api/session/{id}/mission-atlas", methods=["GET"], debug=True)
def handle_mission_atlas_singular(db, params, token, nonce, session_id: str = "") -> tuple:
    """GET /api/session/{id}/mission-atlas — Mission Atlas (singular form)."""
    return _handle_mission_atlas(db, params, token, nonce, session_id)


@route("/api/sessions/{id}/mission-atlas", methods=["GET"], debug=True)
def handle_mission_atlas_plural(db, params, token, nonce, session_id: str = "") -> tuple:
    """GET /api/sessions/{id}/mission-atlas — Mission Atlas (plural alias)."""
    return _handle_mission_atlas(db, params, token, nonce, session_id)
