/**
 * Tests for sub-agent activity model helpers in flight-recorder.ts.
 *
 * Data source: SubagentActivityResponse from the dedicated route
 * GET /api/session/{id}/subagent-activity — NOT paginated debug-log entries.
 */
import { describe, expect, it } from "vitest";

import type { SubagentActivityEntry, SubagentActivityResponse } from "@/lib/api/types";
import {
  deriveSubagentActivitySummary,
  deriveSubagentChipsFromActivity,
  deriveSubagentExecutions,
  filterSubagentExecutions,
} from "@/lib/flight-recorder";

// ── Fixture helpers ───────────────────────────────────────────────────────────

function makeEntry(overrides: Partial<SubagentActivityEntry> = {}): SubagentActivityEntry {
  return {
    span_id: overrides.span_id !== undefined ? overrides.span_id : "span-001",
    agent_name: overrides.agent_name !== undefined ? overrides.agent_name : "my-agent",
    agent_display_name:
      overrides.agent_display_name !== undefined ? overrides.agent_display_name : "My Agent",
    model: overrides.model !== undefined ? overrides.model : "gpt-4o",
    status: overrides.status ?? "completed",
    started_at:
      overrides.started_at !== undefined ? overrides.started_at : "2024-01-01T10:00:00.000Z",
    ended_at: overrides.ended_at !== undefined ? overrides.ended_at : "2024-01-01T10:00:05.000Z",
    duration_ms: overrides.duration_ms !== undefined ? overrides.duration_ms : 5000,
    total_tool_calls: overrides.total_tool_calls !== undefined ? overrides.total_tool_calls : 3,
    total_tokens: overrides.total_tokens !== undefined ? overrides.total_tokens : 1200,
    error_category: overrides.error_category !== undefined ? overrides.error_category : null,
    error_preview: overrides.error_preview !== undefined ? overrides.error_preview : null,
    start_idx: overrides.start_idx !== undefined ? overrides.start_idx : 10,
    end_idx: overrides.end_idx !== undefined ? overrides.end_idx : 25,
    redacted: overrides.redacted ?? false,
  };
}

function makeResponse(
  entries: SubagentActivityEntry[],
  overrides: Partial<Omit<SubagentActivityResponse, "entries" | "schema_version">> = {}
): SubagentActivityResponse {
  return {
    schema_version: "1",
    session_id: "test-session",
    total_subagents_seen: overrides.total_subagents_seen ?? entries.length,
    returned: overrides.returned ?? entries.length,
    cap: overrides.cap ?? 1000,
    truncated: overrides.truncated ?? false,
    dropped_pending_starts: overrides.dropped_pending_starts ?? 0,
    entries,
  };
}

// ── deriveSubagentExecutions: empty / null input ──────────────────────────────

describe("deriveSubagentExecutions – empty / null input", () => {
  it("returns empty array for undefined", () => {
    expect(deriveSubagentExecutions(undefined)).toEqual([]);
  });

  it("returns empty array for null", () => {
    expect(deriveSubagentExecutions(null)).toEqual([]);
  });

  it("returns empty array for response with zero entries", () => {
    const r = makeResponse([]);
    expect(deriveSubagentExecutions(r)).toEqual([]);
  });
});

// ── deriveSubagentExecutions: completed execution ─────────────────────────────

describe("deriveSubagentExecutions – completed execution", () => {
  it("maps fields correctly for a completed execution", () => {
    const entry = makeEntry({
      span_id: "span-abc",
      agent_name: "coder",
      agent_display_name: "Coder Agent",
      model: "claude-3-sonnet",
      status: "completed",
      started_at: "2024-01-01T10:00:00.000Z",
      ended_at: "2024-01-01T10:00:10.000Z",
      duration_ms: 10000,
      total_tool_calls: 5,
      total_tokens: 2000,
      error_category: null,
      error_preview: null,
      start_idx: 5,
      end_idx: 20,
      redacted: false,
    });
    const [exec] = deriveSubagentExecutions(makeResponse([entry]));

    expect(exec.id).toBe("span-abc");
    expect(exec.agentName).toBe("coder");
    expect(exec.agentDisplayName).toBe("Coder Agent");
    expect(exec.displayName).toBe("Coder Agent");
    expect(exec.model).toBe("claude-3-sonnet");
    expect(exec.status).toBe("completed");
    expect(exec.errorCategory).toBeNull();
    expect(exec.startedAt).toBe("2024-01-01T10:00:00.000Z");
    expect(exec.endedAt).toBe("2024-01-01T10:00:10.000Z");
    expect(exec.startedAtMs).toBe(Date.parse("2024-01-01T10:00:00.000Z"));
    expect(exec.endedAtMs).toBe(Date.parse("2024-01-01T10:00:10.000Z"));
    expect(exec.durationMs).toBe(10000);
    expect(exec.totalToolCalls).toBe(5);
    expect(exec.totalTokens).toBe(2000);
    expect(exec.startIdx).toBe(5);
    expect(exec.endIdx).toBe(20);
    expect(exec.redacted).toBe(false);
  });

  it("uses wall-clock diff when duration_ms is null", () => {
    const entry = makeEntry({
      started_at: "2024-01-01T10:00:00.000Z",
      ended_at: "2024-01-01T10:00:03.000Z",
      duration_ms: null,
    });
    const [exec] = deriveSubagentExecutions(makeResponse([entry]));
    expect(exec.durationMs).toBe(3000);
  });

  it("clamps negative duration from wall-clock diff to 0", () => {
    // ended_at before started_at should clamp to 0
    const entry = makeEntry({
      started_at: "2024-01-01T10:00:05.000Z",
      ended_at: "2024-01-01T10:00:00.000Z",
      duration_ms: null,
    });
    const [exec] = deriveSubagentExecutions(makeResponse([entry]));
    expect(exec.durationMs).toBe(0);
  });

  it("returns null durationMs when timestamps are missing and duration_ms is null", () => {
    const entry = makeEntry({
      started_at: null,
      ended_at: null,
      duration_ms: null,
    });
    const [exec] = deriveSubagentExecutions(makeResponse([entry]));
    expect(exec.durationMs).toBeNull();
  });
});

// ── deriveSubagentExecutions: failed with rate_limited ────────────────────────

describe("deriveSubagentExecutions – failed execution", () => {
  it("exposes errorCategory for failed status with rate_limited", () => {
    const entry = makeEntry({
      status: "failed",
      error_category: "rate_limited",
      ended_at: "2024-01-01T10:00:06.000Z",
    });
    const [exec] = deriveSubagentExecutions(makeResponse([entry]));

    expect(exec.status).toBe("failed");
    expect(exec.errorCategory).toBe("rate_limited");
  });

  it("exposes errorCategory for failed status with api_error", () => {
    const entry = makeEntry({ status: "failed", error_category: "api_error" });
    const [exec] = deriveSubagentExecutions(makeResponse([entry]));
    expect(exec.errorCategory).toBe("api_error");
  });

  it("exposes errorCategory for timeout", () => {
    const entry = makeEntry({ status: "failed", error_category: "timeout" });
    const [exec] = deriveSubagentExecutions(makeResponse([entry]));
    expect(exec.errorCategory).toBe("timeout");
  });

  it("exposes errorCategory for internal_error", () => {
    const entry = makeEntry({ status: "failed", error_category: "internal_error" });
    const [exec] = deriveSubagentExecutions(makeResponse([entry]));
    expect(exec.errorCategory).toBe("internal_error");
  });

  it("exposes errorCategory for cancelled", () => {
    const entry = makeEntry({ status: "failed", error_category: "cancelled" });
    const [exec] = deriveSubagentExecutions(makeResponse([entry]));
    expect(exec.errorCategory).toBe("cancelled");
  });

  it("exposes errorCategory for unknown", () => {
    const entry = makeEntry({ status: "failed", error_category: "unknown" });
    const [exec] = deriveSubagentExecutions(makeResponse([entry]));
    expect(exec.errorCategory).toBe("unknown");
  });
});

// ── deriveSubagentExecutions: running / orphan ────────────────────────────────

describe("deriveSubagentExecutions – running / orphan execution", () => {
  it("maps a running execution with no end fields", () => {
    const entry = makeEntry({
      status: "running",
      ended_at: null,
      end_idx: null,
      error_category: null,
      duration_ms: null,
      total_tool_calls: null,
      total_tokens: null,
    });
    const [exec] = deriveSubagentExecutions(makeResponse([entry]));

    expect(exec.status).toBe("running");
    expect(exec.endedAt).toBeNull();
    expect(exec.endedAtMs).toBeNull();
    expect(exec.endIdx).toBeNull();
    expect(exec.errorCategory).toBeNull();
    // durationMs: started_at is present but ended_at is null; cannot derive
    expect(exec.durationMs).toBeNull();
  });
});

// ── deriveSubagentExecutions: unmatched completion (no start_idx) ─────────────

describe("deriveSubagentExecutions – unmatched completion", () => {
  it("handles entry with null start_idx", () => {
    const entry = makeEntry({ start_idx: null, end_idx: 42 });
    const [exec] = deriveSubagentExecutions(makeResponse([entry]));
    expect(exec.startIdx).toBeNull();
    expect(exec.endIdx).toBe(42);
    // id falls back to eidx-based when span_id is also null
    const entryNoSpan = makeEntry({ span_id: null, start_idx: null, end_idx: 42 });
    const [exec2] = deriveSubagentExecutions(makeResponse([entryNoSpan]));
    expect(exec2.id).toBe("subagent-eidx-42");
  });
});

// ── deriveSubagentExecutions: id stability ────────────────────────────────────

describe("deriveSubagentExecutions – stable ids", () => {
  it("prefers span_id as the stable id", () => {
    const entry = makeEntry({ span_id: "span-xyz", start_idx: 1 });
    const [exec] = deriveSubagentExecutions(makeResponse([entry]));
    expect(exec.id).toBe("span-xyz");
  });

  it("falls back to start_idx when span_id is null", () => {
    const entry = makeEntry({ span_id: null, start_idx: 77 });
    const [exec] = deriveSubagentExecutions(makeResponse([entry]));
    expect(exec.id).toBe("subagent-sidx-77");
  });

  it("falls back to end_idx when span_id and start_idx are null", () => {
    const entry = makeEntry({ span_id: null, start_idx: null, end_idx: 99 });
    const [exec] = deriveSubagentExecutions(makeResponse([entry]));
    expect(exec.id).toBe("subagent-eidx-99");
  });

  it("falls back to positional id when all identity fields are null", () => {
    const entry = makeEntry({ span_id: null, start_idx: null, end_idx: null });
    const [exec] = deriveSubagentExecutions(makeResponse([entry]));
    expect(exec.id).toBe("subagent-pos-0");
  });

  it("produces distinct ids for entries at different positions with null identity", () => {
    const e1 = makeEntry({ span_id: null, start_idx: null, end_idx: null });
    const e2 = makeEntry({ span_id: null, start_idx: null, end_idx: null });
    const [ex1, ex2] = deriveSubagentExecutions(makeResponse([e1, e2]));
    expect(ex1.id).not.toBe(ex2.id);
    expect(ex1.id).toBe("subagent-pos-0");
    expect(ex2.id).toBe("subagent-pos-1");
  });

  it("produces distinct ids for two executions of the same agent (different span_ids)", () => {
    const e1 = makeEntry({ span_id: "span-A", agent_name: "worker" });
    const e2 = makeEntry({ span_id: "span-B", agent_name: "worker" });
    const [ex1, ex2] = deriveSubagentExecutions(makeResponse([e1, e2]));
    expect(ex1.id).toBe("span-A");
    expect(ex2.id).toBe("span-B");
    expect(ex1.id).not.toBe(ex2.id);
  });
});

// ── deriveSubagentExecutions: displayName / fallback labels ───────────────────

describe("deriveSubagentExecutions – display name fallbacks", () => {
  it("prefers agent_display_name over agent_name", () => {
    const entry = makeEntry({ agent_name: "raw-name", agent_display_name: "Pretty Name" });
    const [exec] = deriveSubagentExecutions(makeResponse([entry]));
    expect(exec.displayName).toBe("Pretty Name");
  });

  it("falls back to agent_name when agent_display_name is null", () => {
    const entry = makeEntry({ agent_display_name: null, agent_name: "raw-name" });
    const [exec] = deriveSubagentExecutions(makeResponse([entry]));
    expect(exec.displayName).toBe("raw-name");
  });

  it("falls back to 'Sub-agent' when both names are null", () => {
    const entry = makeEntry({ agent_display_name: null, agent_name: null });
    const [exec] = deriveSubagentExecutions(makeResponse([entry]));
    expect(exec.displayName).toBe("Sub-agent");
  });

  it("falls back to 'Sub-agent' when both names are empty strings", () => {
    const entry = makeEntry({ agent_display_name: "", agent_name: "" });
    const [exec] = deriveSubagentExecutions(makeResponse([entry]));
    expect(exec.displayName).toBe("Sub-agent");
  });

  it("cleans control characters from display name", () => {
    const entry = makeEntry({ agent_display_name: "Agent\u0000\u001f Name" });
    const [exec] = deriveSubagentExecutions(makeResponse([entry]));
    expect(exec.displayName).toBe("Agent Name");
  });

  it("agentName is null (not string 'null') when agent_name is null", () => {
    const entry = makeEntry({ agent_name: null });
    const [exec] = deriveSubagentExecutions(makeResponse([entry]));
    expect(exec.agentName).toBeNull();
    expect(exec.agentName).not.toBe("null");
  });

  it("model is null (not string 'null') when model is null", () => {
    const entry = makeEntry({ model: null });
    const [exec] = deriveSubagentExecutions(makeResponse([entry]));
    expect(exec.model).toBeNull();
    expect(exec.model).not.toBe("null");
  });

  it("startedAt is null (not string 'null') when started_at is null", () => {
    const entry = makeEntry({ started_at: null });
    const [exec] = deriveSubagentExecutions(makeResponse([entry]));
    expect(exec.startedAt).toBeNull();
    expect(exec.startedAt).not.toBe("null");
  });
});

// ── deriveSubagentActivitySummary ─────────────────────────────────────────────

describe("deriveSubagentActivitySummary", () => {
  it("returns zero-values for undefined", () => {
    const s = deriveSubagentActivitySummary(undefined);
    expect(s.totalSeen).toBe(0);
    expect(s.returned).toBe(0);
    expect(s.cap).toBe(0);
    expect(s.truncated).toBe(false);
    expect(s.droppedPendingStarts).toBe(0);
  });

  it("returns zero-values for null", () => {
    const s = deriveSubagentActivitySummary(null);
    expect(s.truncated).toBe(false);
    expect(s.totalSeen).toBe(0);
  });

  it("preserves truncated=true from response", () => {
    const r = makeResponse([makeEntry()], {
      total_subagents_seen: 1500,
      returned: 1000,
      cap: 1000,
      truncated: true,
      dropped_pending_starts: 5,
    });
    const s = deriveSubagentActivitySummary(r);
    expect(s.truncated).toBe(true);
    expect(s.totalSeen).toBe(1500);
    expect(s.returned).toBe(1000);
    expect(s.cap).toBe(1000);
    expect(s.droppedPendingStarts).toBe(5);
  });

  it("preserves truncated=false from response", () => {
    const r = makeResponse([makeEntry()], { truncated: false });
    const s = deriveSubagentActivitySummary(r);
    expect(s.truncated).toBe(false);
  });

  it("includes droppedPendingStarts count", () => {
    const r = makeResponse([], { dropped_pending_starts: 3 });
    const s = deriveSubagentActivitySummary(r);
    expect(s.droppedPendingStarts).toBe(3);
  });
});

// ── filterSubagentExecutions ──────────────────────────────────────────────────

describe("filterSubagentExecutions", () => {
  const completed = makeEntry({ status: "completed", span_id: "c1" });
  const failed = makeEntry({
    status: "failed",
    span_id: "f1",
    error_category: "rate_limited",
  });
  const running = makeEntry({
    status: "running",
    span_id: "r1",
    ended_at: null,
    end_idx: null,
    duration_ms: null,
  });
  const response = makeResponse([completed, failed, running]);

  it("returns all executions for filter=all", () => {
    const execs = deriveSubagentExecutions(response);
    const result = filterSubagentExecutions(execs, "all");
    expect(result).toHaveLength(3);
  });

  it("returns all executions when filter is omitted (default=all)", () => {
    const execs = deriveSubagentExecutions(response);
    const result = filterSubagentExecutions(execs);
    expect(result).toHaveLength(3);
  });

  it("returns only failed executions for filter=failed", () => {
    const execs = deriveSubagentExecutions(response);
    const result = filterSubagentExecutions(execs, "failed");
    expect(result).toHaveLength(1);
    expect(result[0].status).toBe("failed");
    expect(result[0].id).toBe("f1");
  });

  it("returns only running executions for filter=running", () => {
    const execs = deriveSubagentExecutions(response);
    const result = filterSubagentExecutions(execs, "running");
    expect(result).toHaveLength(1);
    expect(result[0].status).toBe("running");
    expect(result[0].id).toBe("r1");
  });

  it("returns empty array when no executions match filter", () => {
    const onlyCompleted = makeResponse([completed]);
    const execs = deriveSubagentExecutions(onlyCompleted);
    const result = filterSubagentExecutions(execs, "failed");
    expect(result).toEqual([]);
  });

  it("returns empty array for filter on empty executions", () => {
    expect(filterSubagentExecutions([], "failed")).toEqual([]);
    expect(filterSubagentExecutions([], "running")).toEqual([]);
    expect(filterSubagentExecutions([], "all")).toEqual([]);
  });
});

// ── deriveSubagentChipsFromActivity ───────────────────────────────────────────

describe("deriveSubagentChipsFromActivity", () => {
  it("returns empty array for undefined", () => {
    expect(deriveSubagentChipsFromActivity(undefined)).toEqual([]);
  });

  it("returns empty array for null", () => {
    expect(deriveSubagentChipsFromActivity(null)).toEqual([]);
  });

  it("returns empty array for response with zero entries", () => {
    expect(deriveSubagentChipsFromActivity(makeResponse([]))).toEqual([]);
  });

  it("builds chips using agent identity (displayName), not event_type strings", () => {
    const entries = [
      makeEntry({ span_id: "a1", agent_display_name: "Code Writer", agent_name: "code" }),
      makeEntry({ span_id: "a2", agent_display_name: "Code Writer", agent_name: "code" }),
      makeEntry({ span_id: "a3", agent_display_name: "Researcher", agent_name: "research" }),
    ];
    const chips = deriveSubagentChipsFromActivity(makeResponse(entries));

    expect(chips).toHaveLength(2);
    // Sorted by count desc
    expect(chips[0].name).toBe("Code Writer");
    expect(chips[0].count).toBe(2);
    expect(chips[1].name).toBe("Researcher");
    expect(chips[1].count).toBe(1);
  });

  it("uses fallback 'Sub-agent' label when names are null", () => {
    const entries = [
      makeEntry({ span_id: "s1", agent_display_name: null, agent_name: null }),
      makeEntry({ span_id: "s2", agent_display_name: null, agent_name: null }),
    ];
    const chips = deriveSubagentChipsFromActivity(makeResponse(entries));
    expect(chips).toHaveLength(1);
    expect(chips[0].name).toBe("Sub-agent");
    expect(chips[0].count).toBe(2);
  });

  it("sorts chips by count descending, name ascending for ties", () => {
    const entries = [
      makeEntry({ span_id: "x1", agent_display_name: "Bravo" }),
      makeEntry({ span_id: "x2", agent_display_name: "Alpha" }),
      makeEntry({ span_id: "x3", agent_display_name: "Alpha" }),
    ];
    const chips = deriveSubagentChipsFromActivity(makeResponse(entries));
    expect(chips[0].name).toBe("Alpha");
    expect(chips[0].count).toBe(2);
    expect(chips[1].name).toBe("Bravo");
    expect(chips[1].count).toBe(1);
  });
});
