import { describe, expect, it } from "vitest";

import type { BrowseDebugEntry } from "@/lib/api/types";
import type { BrowseDebugSkeletonEntry } from "@/lib/api/types";
import {
  browseDebugEntrySchema,
  browseDebugSkeletonEntrySchema,
  sessionDebugLogResponseSchema,
  sessionDebugSkeletonResponseSchema,
} from "@/lib/api/schemas";
import {
  normalizeAndSortEntries,
  deriveMarkersFromEntries,
  derivePlaybackFrame,
  derivePlaybackLane,
  deriveSkeletonCategory,
  nextMarkerIndex,
  prevMarkerIndex,
  resolveMarkerIndex,
  binEntriesByTime,
  binEntriesByCount,
  filterByLane,
  PLAYBACK_LANE_MAP,
  PLAYBACK_LANE_ORDER,
} from "@/lib/debug-event-playback";

// ── Test fixtures ─────────────────────────────────────────────────────────────

function makeEntry(overrides: Partial<BrowseDebugEntry> & { idx: number }): BrowseDebugEntry {
  return {
    idx: overrides.idx,
    // Use explicit undefined check so null can be passed intentionally
    timestamp: overrides.timestamp !== undefined ? overrides.timestamp : "2024-01-01T12:00:00.000Z",
    kind: overrides.kind ?? "generic",
    level: overrides.level ?? "info",
    source: overrides.source ?? "operator_console",
    message: overrides.message ?? "msg",
    tool_name: overrides.tool_name ?? null,
    duration_ms: overrides.duration_ms !== undefined ? overrides.duration_ms : null,
    span_id: overrides.span_id !== undefined ? overrides.span_id : null,
    parent_span_id: overrides.parent_span_id !== undefined ? overrides.parent_span_id : null,
    status: overrides.status !== undefined ? overrides.status : null,
    attrs: overrides.attrs !== undefined ? overrides.attrs : null,
    redacted: overrides.redacted ?? false,
  };
}

function makeSkeleton(
  overrides: Partial<BrowseDebugSkeletonEntry> & { idx: number }
): BrowseDebugSkeletonEntry {
  return {
    idx: overrides.idx,
    timestamp: overrides.timestamp ?? "2024-01-01T12:00:00.000Z",
    kind: overrides.kind ?? "generic",
    duration_ms: overrides.duration_ms ?? null,
    status: overrides.status ?? null,
    span_id: overrides.span_id ?? null,
    parent_span_id: overrides.parent_span_id ?? null,
  };
}

const BASE_TS = new Date("2024-01-01T12:00:00.000Z").getTime();

function ts(offsetMs: number): string {
  return new Date(BASE_TS + offsetMs).toISOString();
}

// ── Schema tests ──────────────────────────────────────────────────────────────

describe("browseDebugSkeletonEntrySchema", () => {
  it("parses a valid skeleton entry", () => {
    const raw = {
      idx: 0,
      timestamp: "2024-01-01T12:00:00.000Z",
      kind: "tool_call",
      duration_ms: 42,
      status: "ok",
      span_id: "abcdef0123456789",
      parent_span_id: null,
    };
    const parsed = browseDebugSkeletonEntrySchema.parse(raw);
    expect(parsed.idx).toBe(0);
    expect(parsed.kind).toBe("tool_call");
    expect(parsed.duration_ms).toBe(42);
  });

  it("accepts null fields", () => {
    const raw = {
      idx: 5,
      timestamp: null,
      kind: "generic",
      duration_ms: null,
      status: null,
      span_id: null,
      parent_span_id: null,
    };
    expect(() => browseDebugSkeletonEntrySchema.parse(raw)).not.toThrow();
  });

  it("strips forbidden fields (no passthrough)", () => {
    const raw = {
      idx: 1,
      timestamp: "2024-01-01T12:00:00.000Z",
      kind: "tool_call",
      duration_ms: 10,
      status: null,
      span_id: null,
      parent_span_id: null,
      // Forbidden skeleton fields:
      message: "secret",
      source: "something",
      tool_name: "bash",
      attrs: { foo: "bar" },
      redacted: true,
      level: "info",
    };
    const parsed = browseDebugSkeletonEntrySchema.parse(raw);
    expect("message" in parsed).toBe(false);
    expect("source" in parsed).toBe(false);
    expect("tool_name" in parsed).toBe(false);
    expect("attrs" in parsed).toBe(false);
    expect("redacted" in parsed).toBe(false);
    expect("level" in parsed).toBe(false);
  });

  it("rejects missing idx", () => {
    expect(() =>
      browseDebugSkeletonEntrySchema.parse({
        timestamp: null,
        kind: "generic",
        duration_ms: null,
        status: null,
        span_id: null,
        parent_span_id: null,
      })
    ).toThrow();
  });
});

describe("sessionDebugSkeletonResponseSchema", () => {
  it("parses a valid skeleton response", () => {
    const raw = {
      schema_version: "1",
      session_id: "sess-abc",
      from: 0,
      limit: 100,
      total: 2,
      has_more: false,
      entries: [
        {
          idx: 0,
          timestamp: null,
          kind: "turn_start",
          duration_ms: null,
          status: null,
          span_id: null,
          parent_span_id: null,
        },
        {
          idx: 1,
          timestamp: "2024-01-01T12:00:01Z",
          kind: "tool_call",
          duration_ms: 50,
          status: "ok",
          span_id: "aa",
          parent_span_id: null,
        },
      ],
    };
    const parsed = sessionDebugSkeletonResponseSchema.parse(raw);
    expect(parsed.entries).toHaveLength(2);
    expect(parsed.entries[1].span_id).toBe("aa");
  });
});

describe("browseDebugEntrySchema (full — unchanged behavior)", () => {
  it("still parses a full entry correctly", () => {
    const raw = {
      idx: 0,
      timestamp: "2024-01-01T12:00:00.000Z",
      kind: "tool_call",
      level: "info",
      source: "operator_console",
      message: "running bash",
      tool_name: "bash",
      duration_ms: 100,
      span_id: "abc",
      parent_span_id: null,
      status: "ok",
      attrs: { tool_status: "ok" },
      redacted: false,
    };
    const parsed = browseDebugEntrySchema.parse(raw);
    expect(parsed.tool_name).toBe("bash");
    expect(parsed.attrs).toEqual({ tool_status: "ok" });
  });

  it("sessionDebugLogResponseSchema still accepts full entries", () => {
    const raw = {
      schema_version: "1",
      session_id: "s1",
      from: 0,
      limit: 10,
      total: 1,
      has_more: false,
      entries: [
        {
          idx: 0,
          timestamp: null,
          kind: "generic",
          level: null,
          source: "x",
          message: "m",
          tool_name: null,
          duration_ms: null,
          span_id: null,
          parent_span_id: null,
          status: null,
          attrs: null,
          redacted: false,
        },
      ],
    };
    expect(() => sessionDebugLogResponseSchema.parse(raw)).not.toThrow();
  });
});

// ── Skeleton category derivation ──────────────────────────────────────────────

describe("deriveSkeletonCategory", () => {
  it.each([
    ["tool_call", null, "tool"],
    ["hook", null, "hook"],
    ["subagent", null, "subagent"],
    ["agent_response", null, "model"],
    ["llm_request", null, "model"],
    ["session_start", null, "session"],
    ["turn_start", null, "turn"],
    ["error", null, "error"],
    ["generic", null, "generic"],
    ["raw", null, "generic"],
    ["unknown_kind", null, "generic"],
    ["generic", "error", "error"], // status escalation
    ["raw", "error", "error"], // status escalation for unknown kinds
  ] as const)("kind=%s status=%s → %s", (kind, status, expected) => {
    expect(deriveSkeletonCategory(kind, status)).toBe(expected);
  });
});

describe("derivePlaybackLane", () => {
  it("maps full entry tool_call to Tool/Hook/Skill", () => {
    const entry = makeEntry({ idx: 0, kind: "tool_call", tool_name: "bash" });
    expect(derivePlaybackLane(entry)).toBe("Tool/Hook/Skill");
  });

  it("maps skeleton hook to Tool/Hook/Skill", () => {
    const sk = makeSkeleton({ idx: 0, kind: "hook" });
    expect(derivePlaybackLane(sk)).toBe("Tool/Hook/Skill");
  });

  it("maps skeleton agent_response to Model", () => {
    expect(derivePlaybackLane(makeSkeleton({ idx: 0, kind: "agent_response" }))).toBe("Model");
  });

  it("maps full entry with notification_kind attr to Notification/Compaction", () => {
    const entry = makeEntry({
      idx: 0,
      kind: "generic",
      attrs: { notification_kind: "agent_completed" } as Record<string, unknown>,
    });
    expect(derivePlaybackLane(entry)).toBe("Notification/Compaction");
  });

  it("PLAYBACK_LANE_ORDER contains all lanes", () => {
    expect(PLAYBACK_LANE_ORDER.length).toBeGreaterThanOrEqual(7);
  });

  it("PLAYBACK_LANE_MAP covers all FlowNodeCategory values", () => {
    const expected = [
      "session",
      "turn",
      "model",
      "tool",
      "hook",
      "skill",
      "subagent",
      "notification",
      "compaction",
      "error",
      "generic",
    ];
    for (const cat of expected) {
      expect(PLAYBACK_LANE_MAP).toHaveProperty(cat);
    }
  });
});

// ── normalizeAndSortEntries ───────────────────────────────────────────────────

describe("normalizeAndSortEntries", () => {
  it("returns empty result and time mode for empty input", () => {
    const { normalized, mode } = normalizeAndSortEntries([]);
    expect(normalized).toHaveLength(0);
    expect(mode).toBe("time");
  });

  it("sorts entries by timestamp ascending", () => {
    const entries = [
      makeEntry({ idx: 2, timestamp: ts(2000) }),
      makeEntry({ idx: 0, timestamp: ts(0) }),
      makeEntry({ idx: 1, timestamp: ts(1000) }),
    ];
    const { normalized } = normalizeAndSortEntries(entries);
    expect(normalized.map((n) => n.entry.idx)).toEqual([0, 1, 2]);
  });

  it("uses idx as stable tiebreaker for equal timestamps", () => {
    const entries = [
      makeEntry({ idx: 3, timestamp: ts(0) }),
      makeEntry({ idx: 1, timestamp: ts(0) }),
      makeEntry({ idx: 2, timestamp: ts(0) }),
    ];
    const { normalized } = normalizeAndSortEntries(entries);
    expect(normalized.map((n) => n.entry.idx)).toEqual([1, 2, 3]);
  });

  it("places entries with null timestamps last (time mode)", () => {
    const entries = [
      makeEntry({ idx: 0, timestamp: ts(0) }),
      makeEntry({ idx: 1, timestamp: null }),
      makeEntry({ idx: 2, timestamp: ts(1000) }),
    ];
    const { normalized, mode } = normalizeAndSortEntries(entries);
    expect(mode).toBe("time"); // only 1 invalid out of 3 = 33% but only 1 entry invalid, threshold is 2 entries
    // idx 1 (null ts) should be last
    expect(normalized[normalized.length - 1].entry.idx).toBe(1);
    expect(normalized[0].entry.idx).toBe(0);
  });

  it("falls back to index mode when >5% entries have missing timestamps", () => {
    // 10 entries, 3 missing = 30% > 5% and >= 2
    const entries = Array.from({ length: 10 }, (_, i) =>
      makeEntry({ idx: i, timestamp: i < 3 ? null : ts(i * 1000) })
    );
    const { mode } = normalizeAndSortEntries(entries);
    expect(mode).toBe("index");
  });

  it("stays in time mode when exactly 1 entry has missing timestamp (< 2 required)", () => {
    const entries = [
      makeEntry({ idx: 0, timestamp: null }),
      makeEntry({ idx: 1, timestamp: ts(0) }),
      makeEntry({ idx: 2, timestamp: ts(1000) }),
    ];
    const { mode } = normalizeAndSortEntries(entries);
    expect(mode).toBe("time");
  });

  it("falls back to index mode when all timestamps are null", () => {
    const entries = [
      makeEntry({ idx: 0, timestamp: null }),
      makeEntry({ idx: 1, timestamp: null }),
      makeEntry({ idx: 2, timestamp: null }),
    ];
    const { mode } = normalizeAndSortEntries(entries);
    expect(mode).toBe("index");
  });

  it("sorts by idx in index mode", () => {
    const entries = [
      makeEntry({ idx: 5, timestamp: null }),
      makeEntry({ idx: 1, timestamp: null }),
      makeEntry({ idx: 3, timestamp: null }),
    ];
    const { normalized } = normalizeAndSortEntries(entries);
    expect(normalized.map((n) => n.entry.idx)).toEqual([1, 3, 5]);
  });

  it("works with out-of-order timestamps", () => {
    const entries = [
      makeEntry({ idx: 0, timestamp: ts(5000) }),
      makeEntry({ idx: 1, timestamp: ts(1000) }),
      makeEntry({ idx: 2, timestamp: ts(3000) }),
    ];
    const { normalized } = normalizeAndSortEntries(entries);
    expect(normalized[0].timestampMs).toBe(BASE_TS + 1000);
    expect(normalized[1].timestampMs).toBe(BASE_TS + 3000);
  });

  it("works with skeleton entries", () => {
    const entries = [
      makeSkeleton({ idx: 1, timestamp: ts(100) }),
      makeSkeleton({ idx: 0, timestamp: ts(0) }),
    ];
    const { normalized, mode } = normalizeAndSortEntries(entries);
    expect(mode).toBe("time");
    expect(normalized[0].entry.idx).toBe(0);
  });
});

// ── deriveMarkersFromEntries ──────────────────────────────────────────────────

describe("deriveMarkersFromEntries", () => {
  it("returns empty array for no entries", () => {
    expect(deriveMarkersFromEntries([])).toHaveLength(0);
  });

  it("emits turn_start marker", () => {
    const { normalized } = normalizeAndSortEntries([makeEntry({ idx: 0, kind: "turn_start" })]);
    const markers = deriveMarkersFromEntries(normalized);
    expect(markers).toHaveLength(1);
    expect(markers[0].kind).toBe("turn_start");
    expect(markers[0].entryIdx).toBe(0);
  });

  it("emits agent_response marker", () => {
    const { normalized } = normalizeAndSortEntries([makeEntry({ idx: 0, kind: "agent_response" })]);
    const markers = deriveMarkersFromEntries(normalized);
    expect(markers[0].kind).toBe("agent_response");
  });

  it("emits error marker for kind=error", () => {
    const { normalized } = normalizeAndSortEntries([makeEntry({ idx: 0, kind: "error" })]);
    expect(deriveMarkersFromEntries(normalized)[0].kind).toBe("error");
  });

  it("emits error marker for status=error regardless of kind", () => {
    const { normalized } = normalizeAndSortEntries([
      makeEntry({ idx: 0, kind: "tool_call", status: "error" }),
    ]);
    expect(deriveMarkersFromEntries(normalized)[0].kind).toBe("error");
  });

  it("emits compaction marker for full entry with attrs.compaction_kind", () => {
    const { normalized } = normalizeAndSortEntries([
      makeEntry({
        idx: 0,
        kind: "generic",
        attrs: { compaction_kind: "summarize" } as Record<string, unknown>,
      }),
    ]);
    const markers = deriveMarkersFromEntries(normalized);
    expect(markers).toHaveLength(1);
    expect(markers[0].kind).toBe("compaction");
  });

  it("does NOT emit compaction for skeleton entries (no attrs)", () => {
    const { normalized } = normalizeAndSortEntries([makeSkeleton({ idx: 0, kind: "generic" })]);
    expect(deriveMarkersFromEntries(normalized)).toHaveLength(0);
  });

  it("emits markers in sorted order and sets sortedIndex correctly", () => {
    const { normalized } = normalizeAndSortEntries([
      makeEntry({ idx: 2, kind: "agent_response", timestamp: ts(2000) }),
      makeEntry({ idx: 0, kind: "turn_start", timestamp: ts(0) }),
      makeEntry({ idx: 1, kind: "generic", timestamp: ts(1000) }),
    ]);
    const markers = deriveMarkersFromEntries(normalized);
    expect(markers).toHaveLength(2);
    expect(markers[0].kind).toBe("turn_start");
    expect(markers[0].sortedIndex).toBe(0);
    expect(markers[1].kind).toBe("agent_response");
    expect(markers[1].sortedIndex).toBe(2);
  });
});

// ── Marker navigation helpers ─────────────────────────────────────────────────

describe("nextMarkerIndex / prevMarkerIndex", () => {
  const fakeMarkers = [
    {
      sortedIndex: 0,
      entryIdx: 0,
      kind: "turn_start" as const,
      timestamp: null,
      timestampMs: null,
    },
    { sortedIndex: 2, entryIdx: 2, kind: "error" as const, timestamp: null, timestampMs: null },
    {
      sortedIndex: 4,
      entryIdx: 4,
      kind: "agent_response" as const,
      timestamp: null,
      timestampMs: null,
    },
  ];

  it("next increments by 1", () => {
    expect(nextMarkerIndex(fakeMarkers, 0)).toBe(1);
    expect(nextMarkerIndex(fakeMarkers, 1)).toBe(2);
  });

  it("next clamps at last marker", () => {
    expect(nextMarkerIndex(fakeMarkers, 2)).toBe(2);
    expect(nextMarkerIndex(fakeMarkers, 10)).toBe(2);
  });

  it("prev decrements by 1", () => {
    expect(prevMarkerIndex(fakeMarkers, 2)).toBe(1);
    expect(prevMarkerIndex(fakeMarkers, 1)).toBe(0);
  });

  it("prev clamps at 0", () => {
    expect(prevMarkerIndex(fakeMarkers, 0)).toBe(0);
    expect(prevMarkerIndex(fakeMarkers, -5)).toBe(0);
  });

  it("returns 0 for empty markers", () => {
    expect(nextMarkerIndex([], 0)).toBe(0);
    expect(prevMarkerIndex([], 0)).toBe(0);
  });
});

// ── derivePlaybackFrame ───────────────────────────────────────────────────────

describe("derivePlaybackFrame", () => {
  describe("empty entries", () => {
    it("returns null currentEvent and empty collections", () => {
      const frame = derivePlaybackFrame([], 0);
      expect(frame.currentEvent).toBeNull();
      expect(frame.currentIndex).toBe(-1);
      expect(frame.prefix).toHaveLength(0);
      expect(frame.activeSpans).toHaveLength(0);
      expect(frame.prefixCount).toBe(0);
      expect(frame.elapsedMs).toBeNull();
    });
  });

  describe("time mode", () => {
    const entries = [
      makeEntry({
        idx: 0,
        kind: "session_start",
        timestamp: ts(0),
        span_id: "s0",
        duration_ms: 5000,
      }),
      makeEntry({ idx: 1, kind: "turn_start", timestamp: ts(1000) }),
      makeEntry({
        idx: 2,
        kind: "tool_call",
        timestamp: ts(2000),
        span_id: "t2",
        duration_ms: 500,
      }),
      makeEntry({ idx: 3, kind: "agent_response", timestamp: ts(4000) }),
    ];

    it("playhead before first event returns null currentEvent", () => {
      const frame = derivePlaybackFrame(entries, BASE_TS - 1);
      expect(frame.currentEvent).toBeNull();
      expect(frame.prefix).toHaveLength(0);
    });

    it("playhead at first event returns idx=0", () => {
      const frame = derivePlaybackFrame(entries, BASE_TS);
      expect(frame.currentEvent?.idx).toBe(0);
      expect(frame.currentIndex).toBe(0);
    });

    it("prefix grows as playhead advances", () => {
      const frame = derivePlaybackFrame(entries, BASE_TS + 2500);
      expect(frame.prefixCount).toBe(3); // ts(0), ts(1000), ts(2000)
    });

    it("elapsedMs is difference from first event", () => {
      const frame = derivePlaybackFrame(entries, BASE_TS + 2000);
      expect(frame.elapsedMs).toBe(2000);
    });

    it("activeSpans includes spans whose interval contains playhead", () => {
      // session_start span: [BASE_TS, BASE_TS+5000)
      // tool_call span: [BASE_TS+2000, BASE_TS+2500)
      const frame = derivePlaybackFrame(entries, BASE_TS + 2200);
      const spanIds = frame.activeSpans.map((s) => s.spanId);
      expect(spanIds).toContain("s0"); // session still active
      expect(spanIds).toContain("t2"); // tool active
    });

    it("span not active after its duration ends", () => {
      const frame = derivePlaybackFrame(entries, BASE_TS + 2600);
      const spanIds = frame.activeSpans.map((s) => s.spanId);
      expect(spanIds).not.toContain("t2"); // tool_call ended at 2500
      expect(spanIds).toContain("s0"); // session still active
    });

    it("includes markers", () => {
      const frame = derivePlaybackFrame(entries, BASE_TS + 5000);
      const markerKinds = frame.markers.map((m) => m.kind);
      expect(markerKinds).toContain("turn_start");
      expect(markerKinds).toContain("agent_response");
    });

    it("currentMarkerIndex is last marker at or before playhead", () => {
      const frame = derivePlaybackFrame(entries, BASE_TS + 3000);
      // Markers: turn_start@1000, agent_response@4000
      // At playhead 3000: only turn_start is at-or-before
      expect(frame.currentMarkerIndex).toBe(0);
    });
  });

  describe("index mode (forced)", () => {
    const entries = [
      makeEntry({ idx: 10, kind: "turn_start", timestamp: null }),
      makeEntry({ idx: 11, kind: "tool_call", timestamp: null }),
      makeEntry({ idx: 12, kind: "agent_response", timestamp: null }),
    ];

    it("cursor=0 shows first entry", () => {
      const frame = derivePlaybackFrame(entries, 0, "index");
      expect(frame.mode).toBe("index");
      expect(frame.currentIndex).toBe(0);
      expect(frame.currentEvent?.idx).toBe(10);
    });

    it("cursor=1 shows second entry", () => {
      const frame = derivePlaybackFrame(entries, 1, "index");
      expect(frame.currentEvent?.idx).toBe(11);
    });

    it("cursor >= entries.length shows last entry", () => {
      const frame = derivePlaybackFrame(entries, 100, "index");
      expect(frame.currentIndex).toBe(entries.length - 1);
    });

    it("elapsedMs is null in index mode", () => {
      const frame = derivePlaybackFrame(entries, 2, "index");
      expect(frame.elapsedMs).toBeNull();
    });

    // Regression: markers must resolve correctly when entry.idx has gaps
    // (idx ≠ sorted-array position). Previously currentMarkerIndex compared
    // sorted position against entry.idx and always missed.
    it("currentMarkerIndex resolves correctly when idx has gaps (regression #543)", () => {
      // idx 0, 5, 10 — sorted positions 0, 1, 2
      const gappedEntries = [
        makeEntry({ idx: 0, kind: "turn_start", timestamp: null }),
        makeEntry({ idx: 5, kind: "tool_call", timestamp: null }),
        makeEntry({ idx: 10, kind: "agent_response", timestamp: null }),
      ];
      // Markers: turn_start @ sorted position 0 (idx=0), agent_response @ sorted position 2 (idx=10)
      const frameAt1 = derivePlaybackFrame(gappedEntries, 1, "index"); // cursor=1 → currentEvent idx=5
      expect(frameAt1.currentEvent?.idx).toBe(5);
      // turn_start (idx=0) is at-or-before; agent_response (idx=10) is after
      expect(frameAt1.currentMarkerIndex).toBe(0);

      const frameAt2 = derivePlaybackFrame(gappedEntries, 2, "index"); // cursor=2 → currentEvent idx=10
      expect(frameAt2.currentEvent?.idx).toBe(10);
      // Both markers are at-or-before the last entry
      expect(frameAt2.currentMarkerIndex).toBe(1);
    });

    it("currentMarkerIndex is -1 before first marker in index mode with gaps (regression #543)", () => {
      const gappedEntries = [
        makeEntry({ idx: 0, kind: "tool_call", timestamp: null }), // no marker
        makeEntry({ idx: 5, kind: "turn_start", timestamp: null }), // marker
      ];
      const frameAt0 = derivePlaybackFrame(gappedEntries, 0, "index"); // cursor=0 → idx=0 (no marker)
      expect(frameAt0.currentMarkerIndex).toBe(-1);

      const frameAt1 = derivePlaybackFrame(gappedEntries, 1, "index"); // cursor=1 → idx=5 (marker)
      expect(frameAt1.currentMarkerIndex).toBe(0);
    });
  });

  // Regression: time-mode cursor must NOT fall back to sorted index when the
  // playhead entry has a null timestamp. Using a sorted index (small integer) as
  // a ms-since-epoch cursor causes derivePlaybackFrame to find no events and
  // return a blank frame. The correct fallback is +Infinity (show all events).
  describe("time mode with null-timestamp playhead entry (regression #543)", () => {
    it("frame is non-empty when playhead lands on a null-timestamp entry", () => {
      // Mix: first two entries have valid timestamps, last one is null.
      const entries = [
        makeEntry({ idx: 0, kind: "session_start", timestamp: ts(0) }),
        makeEntry({ idx: 1, kind: "turn_start", timestamp: ts(1000) }),
        makeEntry({ idx: 2, kind: "tool_call", timestamp: null }),
      ];
      // normalizeAndSortEntries puts null-timestamp entries last in time mode.
      // Simulate what the component cursor would be: playhead index 2 → null ts.
      // derivePlaybackFrame is called with cursor = POSITIVE_INFINITY (after fix).
      const frame = derivePlaybackFrame(entries, Number.POSITIVE_INFINITY, "time");
      // All three entries are at-or-before +Infinity → frame is full
      expect(frame.currentEvent).not.toBeNull();
      expect(frame.prefix.length).toBeGreaterThan(0);
      expect(frame.prefixCount).toBe(3);
    });

    it("cursor fallback to POSITIVE_INFINITY shows all valid-timestamp events too", () => {
      const entries = [
        makeEntry({ idx: 0, kind: "session_start", timestamp: ts(0) }),
        makeEntry({ idx: 1, kind: "turn_start", timestamp: ts(500) }),
        makeEntry({ idx: 2, kind: "agent_response", timestamp: null }),
      ];
      const frame = derivePlaybackFrame(entries, Number.POSITIVE_INFINITY, "time");
      expect(frame.prefix.map((e) => e.idx)).toEqual([0, 1, 2]);
    });
  });

  // Regression: time-mode with MULTIPLE null-timestamp entries (#543 re-review).
  // With 41 entries and 2 null timestamps, null fraction = 2/41 ≈ 4.9% ≤ 5%,
  // so time mode stays active. Without exactSortedIndex, cursor=+Infinity causes
  // the scan to select the LAST null entry instead of the targeted first one.
  describe("time mode — multi-null timestamp playhead (#543 re-review)", () => {
    // Build 41 entries: indices 0–38 have valid timestamps, 39 and 40 have null.
    const makeMultiNullEntries = () => [
      ...Array.from({ length: 39 }, (_, i) =>
        makeEntry({ idx: i, kind: "tool_call", timestamp: ts(i * 100) })
      ),
      makeEntry({ idx: 39, kind: "generic", timestamp: null }),
      makeEntry({ idx: 40, kind: "error", timestamp: null }),
    ];

    it("remains in time mode when null fraction is ≤ 5%", () => {
      const { mode } = normalizeAndSortEntries(makeMultiNullEntries());
      expect(mode).toBe("time");
    });

    it("exactSortedIndex=39 selects first null entry (not last) with cursor=+Infinity", () => {
      const entries = makeMultiNullEntries();
      const frame = derivePlaybackFrame(entries, Number.POSITIVE_INFINITY, "time", 39);
      expect(frame.currentIndex).toBe(39);
      expect(frame.currentEvent?.idx).toBe(39);
    });

    it("exactSortedIndex=40 selects second null entry with cursor=+Infinity", () => {
      const entries = makeMultiNullEntries();
      const frame = derivePlaybackFrame(entries, Number.POSITIVE_INFINITY, "time", 40);
      expect(frame.currentIndex).toBe(40);
      expect(frame.currentEvent?.idx).toBe(40);
    });

    it("without exactSortedIndex, +Infinity cursor still selects last null entry (baseline)", () => {
      // Confirms the over-scan behavior that exactSortedIndex is designed to override.
      const entries = makeMultiNullEntries();
      const frame = derivePlaybackFrame(entries, Number.POSITIVE_INFINITY, "time");
      expect(frame.currentIndex).toBe(40); // last null entry
    });

    it("prefix for exactSortedIndex=39 contains exactly 40 entries (0–39)", () => {
      const entries = makeMultiNullEntries();
      const frame = derivePlaybackFrame(entries, Number.POSITIVE_INFINITY, "time", 39);
      expect(frame.prefixCount).toBe(40);
    });
  });

  describe("active spans — zero-width treatment", () => {
    it("span with null duration is active only at current event", () => {
      const entries = [
        makeEntry({ idx: 0, timestamp: ts(0), span_id: "s0", duration_ms: null }),
        makeEntry({ idx: 1, timestamp: ts(1000), span_id: "s1", duration_ms: null }),
      ];
      const frame0 = derivePlaybackFrame(entries, BASE_TS);
      expect(frame0.activeSpans.find((s) => s.spanId === "s0")?.isZeroWidth).toBe(true);
      expect(frame0.activeSpans.find((s) => s.spanId === "s1")).toBeUndefined();

      // Advance past idx 0 — s0 is no longer current, so no longer active
      const frame1 = derivePlaybackFrame(entries, BASE_TS + 1000);
      const s0Active = frame1.activeSpans.find((s) => s.spanId === "s0");
      expect(s0Active).toBeUndefined(); // s0 has no duration so not in active interval
      expect(frame1.activeSpans.find((s) => s.spanId === "s1")?.isZeroWidth).toBe(true);
    });
  });

  describe("repeated span_ids", () => {
    it("multiple entries with same span_id can both be active", () => {
      // A span that emits both start and a status=ok end event
      const entries = [
        makeEntry({ idx: 0, timestamp: ts(0), span_id: "s1", duration_ms: 2000 }),
        makeEntry({ idx: 1, timestamp: ts(500), span_id: "s1", duration_ms: null }),
      ];
      const frame = derivePlaybackFrame(entries, BASE_TS + 800);
      const s1Active = frame.activeSpans.filter((s) => s.spanId === "s1");
      // idx 0 has duration 2000ms so it covers the whole range → active
      // idx 1 has null duration → active only when current event (idx=1 is current here)
      expect(s1Active.length).toBe(2);
    });
  });

  describe("out-of-order input", () => {
    it("normalizes before computing prefix", () => {
      const entries = [
        makeEntry({ idx: 2, timestamp: ts(2000), kind: "tool_call" }),
        makeEntry({ idx: 0, timestamp: ts(0), kind: "session_start" }),
        makeEntry({ idx: 1, timestamp: ts(1000), kind: "turn_start" }),
      ];
      const frame = derivePlaybackFrame(entries, BASE_TS + 1500);
      expect(frame.prefixCount).toBe(2); // session_start and turn_start
      expect(frame.currentEvent?.kind).toBe("turn_start");
    });
  });
});

// ── binEntriesByTime ──────────────────────────────────────────────────────────

describe("binEntriesByTime", () => {
  it("returns empty for empty input", () => {
    expect(binEntriesByTime([], 1000)).toHaveLength(0);
  });

  it("returns empty for zero bin width", () => {
    const { normalized } = normalizeAndSortEntries([makeEntry({ idx: 0 })]);
    expect(binEntriesByTime(normalized, 0)).toHaveLength(0);
  });

  it("bins events by time window", () => {
    const entries = [
      makeEntry({ idx: 0, timestamp: ts(0) }),
      makeEntry({ idx: 1, timestamp: ts(500) }),
      makeEntry({ idx: 2, timestamp: ts(1500) }),
      makeEntry({ idx: 3, timestamp: ts(2500) }),
    ];
    const { normalized } = normalizeAndSortEntries(entries);
    const bins = binEntriesByTime(normalized, 1000);
    // Bin 0: [0, 1000) → idx 0 and 1
    // Bin 1: [1000, 2000) → idx 2
    // Bin 2: [2000, 3000) → idx 3
    expect(bins).toHaveLength(3);
    expect(bins[0].count).toBe(2);
    expect(bins[1].count).toBe(1);
  });

  it("counts errors", () => {
    const entries = [
      makeEntry({ idx: 0, timestamp: ts(0), kind: "error" }),
      makeEntry({ idx: 1, timestamp: ts(100), kind: "generic" }),
    ];
    const { normalized } = normalizeAndSortEntries(entries);
    const bins = binEntriesByTime(normalized, 1000);
    expect(bins[0].errorCount).toBe(1);
  });

  it("returns empty when all timestamps are null", () => {
    const { normalized } = normalizeAndSortEntries([
      makeEntry({ idx: 0, timestamp: null }),
      makeEntry({ idx: 1, timestamp: null }),
      makeEntry({ idx: 2, timestamp: null }),
    ]);
    // Mode will be index, first entry sortKey != real timestamp
    const bins = binEntriesByTime(normalized, 1000);
    // Entries have null timestampMs so they are skipped
    expect(bins).toHaveLength(0);
  });
});

// ── binEntriesByCount ─────────────────────────────────────────────────────────

describe("binEntriesByCount", () => {
  it("bins into groups of binSize", () => {
    const entries = Array.from({ length: 10 }, (_, i) =>
      makeEntry({ idx: i, timestamp: ts(i * 100) })
    );
    const { normalized } = normalizeAndSortEntries(entries);
    const bins = binEntriesByCount(normalized, 3);
    expect(bins).toHaveLength(4); // 3+3+3+1
    expect(bins[0].count).toBe(3);
    expect(bins[3].count).toBe(1);
  });

  it("includes error count and kinds", () => {
    const entries = [
      makeEntry({ idx: 0, timestamp: ts(0), kind: "error" }),
      makeEntry({ idx: 1, timestamp: ts(100), kind: "tool_call" }),
    ];
    const { normalized } = normalizeAndSortEntries(entries);
    const bins = binEntriesByCount(normalized, 2);
    expect(bins[0].errorCount).toBe(1);
    expect(bins[0].kinds).toContain("error");
    expect(bins[0].kinds).toContain("tool_call");
  });
});

// ── filterByLane ──────────────────────────────────────────────────────────────

describe("filterByLane", () => {
  it("filters to a single lane", () => {
    const entries = [
      makeEntry({ idx: 0, kind: "session_start", timestamp: ts(0) }),
      makeEntry({ idx: 1, kind: "tool_call", timestamp: ts(100) }),
      makeEntry({ idx: 2, kind: "turn_start", timestamp: ts(200) }),
    ];
    const { normalized } = normalizeAndSortEntries(entries);
    const sessionLane = filterByLane(normalized, "Session");
    expect(sessionLane).toHaveLength(1);
    expect(sessionLane[0].entry.kind).toBe("session_start");
  });
});
