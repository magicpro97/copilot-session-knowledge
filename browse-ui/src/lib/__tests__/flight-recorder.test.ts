import { describe, expect, it } from "vitest";

import type {
  BrowseCheckpointSummary,
  BrowseDebugEntry,
  BrowseDebugSkeletonEntry,
  BrowseRewindSnapshotSummary,
} from "@/lib/api/types";
import {
  clampDurationMs,
  deriveChapters,
  deriveHotspots,
  deriveMissionRollup,
  deriveResumeAnchors,
  HOTSPOT_GAP_THRESHOLD_MS,
  MAX_HOTSPOT_GAPS,
  MAX_HOTSPOT_SLOWEST,
} from "@/lib/flight-recorder";

// ── Fixture helpers ──────────────────────────────────────────────────────────

function skel(
  overrides: Partial<BrowseDebugSkeletonEntry> & { idx: number }
): BrowseDebugSkeletonEntry {
  return {
    idx: overrides.idx,
    timestamp:
      overrides.timestamp !== undefined
        ? overrides.timestamp
        : new Date(1_700_000_000_000 + overrides.idx * 1000).toISOString(),
    kind: overrides.kind ?? "generic",
    duration_ms: overrides.duration_ms !== undefined ? overrides.duration_ms : null,
    status: overrides.status !== undefined ? overrides.status : null,
    span_id: overrides.span_id ?? null,
    parent_span_id: overrides.parent_span_id ?? null,
  };
}

function full(overrides: Partial<BrowseDebugEntry> & { idx: number }): BrowseDebugEntry {
  return {
    idx: overrides.idx,
    timestamp:
      overrides.timestamp !== undefined
        ? overrides.timestamp
        : new Date(1_700_000_000_000 + overrides.idx * 1000).toISOString(),
    kind: overrides.kind ?? "generic",
    level: overrides.level ?? "info",
    source: overrides.source ?? "operator_console",
    message: overrides.message ?? "",
    tool_name: overrides.tool_name ?? null,
    duration_ms: overrides.duration_ms !== undefined ? overrides.duration_ms : null,
    span_id: overrides.span_id ?? null,
    parent_span_id: overrides.parent_span_id ?? null,
    status: overrides.status !== undefined ? overrides.status : null,
    attrs: overrides.attrs !== undefined ? overrides.attrs : null,
    redacted: overrides.redacted ?? false,
  };
}

function buildCheckpoints(
  n: number,
  opts?: { totalEvents?: number; baseMs?: number; stepMs?: number; nullMtime?: boolean }
): BrowseCheckpointSummary[] {
  const totalEvents = opts?.totalEvents ?? 0;
  const baseMs = opts?.baseMs ?? 1_700_000_000_000;
  const stepMs = opts?.stepMs ?? 1000;
  // Place each checkpoint's mtime at the event corresponding to the end of
  // its even slice, so the timestamp-anchored builder reproduces an even tile
  // when callers ask for it. Tests that want non-even mapping override.
  return Array.from({ length: n }, (_, i) => {
    let mtime: string | null = null;
    if (!opts?.nullMtime && totalEvents > 0) {
      const targetIdx =
        i === n - 1 ? totalEvents - 1 : Math.max(0, Math.floor(((i + 1) * totalEvents) / n) - 1);
      mtime = new Date(baseMs + targetIdx * stepMs).toISOString();
    }
    return {
      seq: i + 1,
      title: `Checkpoint ${i + 1}`,
      file_basename: `${String(i + 1).padStart(3, "0")}-chapter.md`,
      byte_size: 1024 * (i + 1),
      mtime_iso: mtime,
      sections: {
        overview: true,
        history: true,
        work_done: true,
        technical_details: true,
        important_files: true,
        next_steps: true,
      },
    };
  });
}

// ── clampDurationMs ──────────────────────────────────────────────────────────

describe("clampDurationMs", () => {
  it("returns null for non-number / NaN / Infinity", () => {
    expect(clampDurationMs(null)).toBeNull();
    expect(clampDurationMs(undefined)).toBeNull();
    expect(clampDurationMs(Number.NaN)).toBeNull();
    expect(clampDurationMs(Number.POSITIVE_INFINITY)).toBeNull();
    expect(clampDurationMs(Number.NEGATIVE_INFINITY)).toBeNull();
  });

  it("clamps negative finite numbers to 0", () => {
    expect(clampDurationMs(-1)).toBe(0);
    expect(clampDurationMs(-9999)).toBe(0);
  });

  it("passes through finite non-negative durations", () => {
    expect(clampDurationMs(0)).toBe(0);
    expect(clampDurationMs(42)).toBe(42);
    expect(clampDurationMs(123456.7)).toBe(123456.7);
  });
});

// ── deriveChapters ───────────────────────────────────────────────────────────

describe("deriveChapters", () => {
  it("returns [] for empty entries", () => {
    expect(deriveChapters({ entries: [], taskCompletes: [] })).toEqual([]);
  });

  it("uses checkpoint mode and preserves task_complete ticks per chapter", () => {
    // 200 events evenly distributed across 29 checkpoints, with 38 task_complete
    // markers spread linearly.
    const total = 200;
    const entries: BrowseDebugSkeletonEntry[] = [];
    for (let i = 0; i < total; i++) {
      entries.push(skel({ idx: i }));
    }
    const taskCompletes: BrowseDebugSkeletonEntry[] = [];
    for (let i = 0; i < 38; i++) {
      // Re-use the same idx so the marker resolves to that sortedIndex.
      const idx = Math.floor((i / 38) * total);
      taskCompletes.push(entries[idx]);
    }
    const checkpoints = buildCheckpoints(29, { totalEvents: total });

    const chapters = deriveChapters({ entries, taskCompletes, checkpoints });
    expect(chapters).toHaveLength(29);
    for (const c of chapters) {
      expect(c.mode).toBe("checkpoint");
      expect(typeof c.checkpointSeq).toBe("number");
      expect(c.checkpointTitle).toMatch(/^Checkpoint \d+$/);
      expect(c.startSortedIndex).toBeLessThanOrEqual(c.endSortedIndex);
    }
    // Ranges must tile [0, total-1] contiguously.
    expect(chapters[0].startSortedIndex).toBe(0);
    expect(chapters[chapters.length - 1].endSortedIndex).toBe(total - 1);
    for (let i = 1; i < chapters.length; i++) {
      expect(chapters[i].startSortedIndex).toBe(chapters[i - 1].endSortedIndex + 1);
    }
    // All 38 ticks accounted for across chapters.
    const tickCount = chapters.reduce((acc, c) => acc + c.taskCompleteSortedIndexes.length, 0);
    expect(tickCount).toBe(38);
  });

  it("anchors checkpoint boundaries to non-even mtime_iso timestamps", () => {
    // 100 events at 1-second cadence; three checkpoints whose mtimes land at
    // event 9, event 49, event 99. Even tiling would give 33/33/34, but the
    // timestamp-anchored builder must tile 0-9 / 10-49 / 50-99.
    const total = 100;
    const baseMs = 1_700_000_000_000;
    const entries: BrowseDebugSkeletonEntry[] = [];
    for (let i = 0; i < total; i++) entries.push(skel({ idx: i }));
    const checkpoints: BrowseCheckpointSummary[] = [
      {
        seq: 1,
        title: "Early",
        file_basename: "001.md",
        byte_size: 1,
        mtime_iso: new Date(baseMs + 9 * 1000).toISOString(),
        sections: {
          overview: true,
          history: false,
          work_done: false,
          technical_details: false,
          important_files: false,
          next_steps: false,
        },
      },
      {
        seq: 2,
        title: "Mid",
        file_basename: "002.md",
        byte_size: 1,
        mtime_iso: new Date(baseMs + 49 * 1000).toISOString(),
        sections: {
          overview: true,
          history: false,
          work_done: false,
          technical_details: false,
          important_files: false,
          next_steps: false,
        },
      },
      {
        seq: 3,
        title: "Late",
        file_basename: "003.md",
        byte_size: 1,
        mtime_iso: new Date(baseMs + 99 * 1000).toISOString(),
        sections: {
          overview: true,
          history: false,
          work_done: false,
          technical_details: false,
          important_files: false,
          next_steps: false,
        },
      },
    ];
    const chapters = deriveChapters({ entries, taskCompletes: [], checkpoints });
    expect(chapters).toHaveLength(3);
    expect(chapters[0].mode).toBe("checkpoint");
    expect(chapters[0].startSortedIndex).toBe(0);
    expect(chapters[0].endSortedIndex).toBe(9);
    expect(chapters[1].startSortedIndex).toBe(10);
    expect(chapters[1].endSortedIndex).toBe(49);
    expect(chapters[2].startSortedIndex).toBe(50);
    expect(chapters[2].endSortedIndex).toBe(99);
  });

  it("falls back to turn mode when all checkpoints have null mtime_iso", () => {
    const entries: BrowseDebugSkeletonEntry[] = [
      skel({ idx: 0, kind: "turn_start" }),
      skel({ idx: 1, kind: "tool_call", duration_ms: 5 }),
      skel({ idx: 2, kind: "agent_response", status: "ok" }),
      skel({ idx: 3, kind: "turn_start" }),
      skel({ idx: 4, kind: "tool_call", duration_ms: 10 }),
      skel({ idx: 5, kind: "agent_response", status: "ok" }),
    ];
    const checkpoints = buildCheckpoints(2, { nullMtime: true });
    const chapters = deriveChapters({ entries, taskCompletes: [], checkpoints });
    // Must NOT enter checkpoint mode without a usable mtime.
    expect(chapters.every((c) => c.mode === "turn")).toBe(true);
  });

  it("falls back to single mode when checkpoint mtimes all fall outside event window", () => {
    const entries: BrowseDebugSkeletonEntry[] = [
      skel({ idx: 0, kind: "generic" }),
      skel({ idx: 1, kind: "generic" }),
    ];
    // Checkpoint mtimes years in the future — outside the event window.
    const checkpoints: BrowseCheckpointSummary[] = [
      {
        seq: 1,
        title: "Future",
        file_basename: "001.md",
        byte_size: 1,
        mtime_iso: "2099-01-01T00:00:00.000Z",
        sections: {
          overview: true,
          history: false,
          work_done: false,
          technical_details: false,
          important_files: false,
          next_steps: false,
        },
      },
    ];
    const chapters = deriveChapters({ entries, taskCompletes: [], checkpoints });
    expect(chapters).toHaveLength(1);
    expect(chapters[0].mode).toBe("single");
  });

  it("drops a pre-window checkpoint so it cannot claim the first event", () => {
    // Regression: previously a checkpoint whose mtime sat before the first
    // visible event timestamp would map to `upperBoundIdx = -1`, then `end =
    // max(prevEnd + 1, 0) = 0`, falsely anchoring itself onto event 0. With
    // a second in-window checkpoint present, `canAnchorCheckpoints` flipped
    // the whole derivation into checkpoint mode and the pre-window chapter
    // appeared. The fix: filter pre-window checkpoints out entirely.
    const base = 1_700_000_000_000;
    const entries: BrowseDebugSkeletonEntry[] = [
      skel({ idx: 0, timestamp: new Date(base + 0).toISOString() }),
      skel({ idx: 1, timestamp: new Date(base + 1000).toISOString() }),
      skel({ idx: 2, timestamp: new Date(base + 2000).toISOString() }),
      skel({ idx: 3, timestamp: new Date(base + 3000).toISOString(), status: "ok" }),
    ];
    const checkpoints: BrowseCheckpointSummary[] = [
      {
        seq: 1,
        title: "Pre-Window",
        file_basename: "001.md",
        byte_size: 1,
        mtime_iso: new Date(base - 5000).toISOString(),
        sections: {
          overview: true,
          history: false,
          work_done: false,
          technical_details: false,
          important_files: false,
          next_steps: false,
        },
      },
      {
        seq: 2,
        title: "In-Window",
        file_basename: "002.md",
        byte_size: 1,
        mtime_iso: new Date(base + 2000).toISOString(),
        sections: {
          overview: true,
          history: false,
          work_done: false,
          technical_details: false,
          important_files: false,
          next_steps: false,
        },
      },
    ];
    const chapters = deriveChapters({ entries, taskCompletes: [], checkpoints });
    expect(chapters).toHaveLength(1);
    expect(chapters[0].mode).toBe("checkpoint");
    expect(chapters[0].checkpointTitle).toBe("In-Window");
    expect(chapters[0].startSortedIndex).toBe(0);
    // Final clamp extends the lone in-window checkpoint through the last
    // event — that is the existing accepted behavior; the regression we are
    // guarding against is the pre-window checkpoint appearing at all.
    expect(chapters[0].endSortedIndex).toBe(3);
  });

  it("drops a post-window checkpoint so it cannot absorb tail events", () => {
    // Regression: previously a checkpoint whose mtime sat after the last
    // visible event timestamp would still pass through `buildCheckpointChapters`
    // and either receive its own chapter or, via the final clamp that
    // extends the last chapter to `total - 1`, grab any trailing events.
    // The fix: filter post-window checkpoints out so only in-window
    // checkpoints anchor chapters.
    const base = 1_700_000_000_000;
    const entries: BrowseDebugSkeletonEntry[] = [
      skel({ idx: 0, timestamp: new Date(base + 0).toISOString() }),
      skel({ idx: 1, timestamp: new Date(base + 1000).toISOString() }),
      skel({ idx: 2, timestamp: new Date(base + 2000).toISOString() }),
      skel({ idx: 3, timestamp: new Date(base + 3000).toISOString(), status: "ok" }),
    ];
    const checkpoints: BrowseCheckpointSummary[] = [
      {
        seq: 1,
        title: "In-Window",
        file_basename: "001.md",
        byte_size: 1,
        mtime_iso: new Date(base + 1000).toISOString(),
        sections: {
          overview: true,
          history: false,
          work_done: false,
          technical_details: false,
          important_files: false,
          next_steps: false,
        },
      },
      {
        seq: 2,
        title: "Post-Window",
        file_basename: "002.md",
        byte_size: 1,
        // Years past the last event timestamp.
        mtime_iso: "2099-01-01T00:00:00.000Z",
        sections: {
          overview: true,
          history: false,
          work_done: false,
          technical_details: false,
          important_files: false,
          next_steps: false,
        },
      },
    ];
    const chapters = deriveChapters({ entries, taskCompletes: [], checkpoints });
    expect(chapters).toHaveLength(1);
    expect(chapters[0].mode).toBe("checkpoint");
    expect(chapters[0].checkpointTitle).toBe("In-Window");
    expect(chapters.some((c) => c.checkpointTitle === "Post-Window")).toBe(false);
    expect(chapters[0].startSortedIndex).toBe(0);
    expect(chapters[0].endSortedIndex).toBe(3);
  });

  it("falls back to turn mode when every checkpoint mtime is outside the event window", () => {
    // Regression: ensure the in-window filter does not accidentally keep the
    // derivation in checkpoint mode when none of the checkpoints land inside
    // the visible event window — and that the existing turn/single fallback
    // path is reached instead.
    const base = 1_700_000_000_000;
    const entries: BrowseDebugSkeletonEntry[] = [
      skel({ idx: 0, kind: "turn_start", timestamp: new Date(base + 0).toISOString() }),
      skel({
        idx: 1,
        kind: "tool_call",
        duration_ms: 5,
        timestamp: new Date(base + 1000).toISOString(),
      }),
      skel({
        idx: 2,
        kind: "agent_response",
        status: "ok",
        timestamp: new Date(base + 2000).toISOString(),
      }),
    ];
    const checkpoints: BrowseCheckpointSummary[] = [
      {
        seq: 1,
        title: "Pre",
        file_basename: "001.md",
        byte_size: 1,
        mtime_iso: new Date(base - 60_000).toISOString(),
        sections: {
          overview: true,
          history: false,
          work_done: false,
          technical_details: false,
          important_files: false,
          next_steps: false,
        },
      },
      {
        seq: 2,
        title: "Post",
        file_basename: "002.md",
        byte_size: 1,
        mtime_iso: "2099-01-01T00:00:00.000Z",
        sections: {
          overview: true,
          history: false,
          work_done: false,
          technical_details: false,
          important_files: false,
          next_steps: false,
        },
      },
    ];
    const chapters = deriveChapters({ entries, taskCompletes: [], checkpoints });
    expect(chapters.every((c) => c.mode === "turn")).toBe(true);
    expect(chapters).toHaveLength(1);
    expect(chapters[0].label).toBe("Turn 1");
  });

  it("falls back to turn mode when checkpoints absent and turn_start events present", () => {
    const entries: BrowseDebugSkeletonEntry[] = [
      skel({ idx: 0, kind: "turn_start" }),
      skel({ idx: 1, kind: "tool_call", duration_ms: 5 }),
      skel({ idx: 2, kind: "agent_response", status: "ok" }),
      skel({ idx: 3, kind: "turn_start" }),
      skel({ idx: 4, kind: "tool_call", duration_ms: 10 }),
      skel({ idx: 5, kind: "agent_response", status: "ok" }),
    ];
    const chapters = deriveChapters({ entries, taskCompletes: [] });
    expect(chapters).toHaveLength(2);
    expect(chapters[0].mode).toBe("turn");
    expect(chapters[0].label).toBe("Turn 1");
    expect(chapters[1].label).toBe("Turn 2");
    expect(chapters[0].startSortedIndex).toBe(0);
    expect(chapters[0].endSortedIndex).toBe(2);
    expect(chapters[1].startSortedIndex).toBe(3);
    expect(chapters[1].endSortedIndex).toBe(5);
  });

  it("emits a Turn 0 pre-roll when entries precede the first turn_start", () => {
    const entries: BrowseDebugSkeletonEntry[] = [
      skel({ idx: 0, kind: "session_start" }),
      skel({ idx: 1, kind: "turn_start" }),
      skel({ idx: 2, kind: "agent_response", status: "ok" }),
    ];
    const chapters = deriveChapters({ entries, taskCompletes: [] });
    expect(chapters.map((c) => c.label)).toEqual(["Turn 0", "Turn 1"]);
  });

  it("uses single mode when no markers exist at all", () => {
    const entries: BrowseDebugSkeletonEntry[] = [
      skel({ idx: 0, kind: "generic" }),
      skel({ idx: 1, kind: "generic", status: "ok" }),
    ];
    const chapters = deriveChapters({ entries, taskCompletes: [] });
    expect(chapters).toHaveLength(1);
    expect(chapters[0].mode).toBe("single");
    expect(chapters[0].label).toBe("Session");
    expect(chapters[0].startSortedIndex).toBe(0);
    expect(chapters[0].endSortedIndex).toBe(1);
  });

  it("derives chapter status: error > cancelled > ok > incomplete", () => {
    const errChapter = deriveChapters({
      entries: [
        skel({ idx: 0, kind: "tool_call", duration_ms: 5 }),
        skel({ idx: 1, kind: "error" }),
      ],
      taskCompletes: [],
    });
    expect(errChapter[0].status).toBe("error");

    const cancelledChapter = deriveChapters({
      entries: [skel({ idx: 0, kind: "tool_call", status: "cancelled", duration_ms: 5 })],
      taskCompletes: [],
    });
    expect(cancelledChapter[0].status).toBe("cancelled");

    const okChapter = deriveChapters({
      entries: [
        skel({ idx: 0, kind: "tool_call", duration_ms: 5 }),
        skel({ idx: 1, kind: "agent_response", status: "ok" }),
      ],
      taskCompletes: [],
    });
    expect(okChapter[0].status).toBe("ok");

    const incompleteChapter = deriveChapters({
      entries: [skel({ idx: 0, kind: "tool_call" })], // no terminal status
      taskCompletes: [],
    });
    expect(incompleteChapter[0].status).toBe("incomplete");
  });

  it("computes startMs / durationMs (clamped) and eventCount", () => {
    const base = 1_700_000_000_000;
    const entries: BrowseDebugSkeletonEntry[] = [
      skel({ idx: 0, timestamp: new Date(base).toISOString() }),
      skel({ idx: 1, timestamp: new Date(base + 1500).toISOString(), status: "ok" }),
    ];
    const [chapter] = deriveChapters({ entries, taskCompletes: [] });
    expect(chapter.startMs).toBe(base);
    expect(chapter.durationMs).toBe(1500);
    expect(chapter.eventCount).toBe(2);
  });
});

// ── deriveHotspots ───────────────────────────────────────────────────────────

describe("deriveHotspots", () => {
  it("returns errors stable-ordered by sortedIndex", () => {
    const entries: BrowseDebugSkeletonEntry[] = [
      skel({ idx: 0, kind: "tool_call", duration_ms: 5 }),
      skel({ idx: 1, kind: "error" }),
      skel({ idx: 2, kind: "tool_call", status: "error", duration_ms: 7 }),
      skel({ idx: 3, kind: "agent_response", status: "ok" }),
    ];
    const h = deriveHotspots(entries);
    expect(h.errors.map((e) => e.entryIdx)).toEqual([1, 2]);
  });

  it("returns top slowest finite nonnegative durations, ignoring NaN/Infinity/negatives ⇒ null", () => {
    const entries: BrowseDebugSkeletonEntry[] = [
      skel({ idx: 0, duration_ms: 100 }),
      skel({ idx: 1, duration_ms: Number.NaN }),
      skel({ idx: 2, duration_ms: Number.POSITIVE_INFINITY }),
      skel({ idx: 3, duration_ms: -50 }), // clamps to 0 → still candidate, but tiny
      skel({ idx: 4, duration_ms: 500 }),
      skel({ idx: 5, duration_ms: 300 }),
      skel({ idx: 6, duration_ms: 700 }),
      skel({ idx: 7, duration_ms: 200 }),
      skel({ idx: 8, duration_ms: 250 }),
    ];
    const h = deriveHotspots(entries);
    expect(h.slowest).toHaveLength(MAX_HOTSPOT_SLOWEST);
    expect(h.slowest.map((s) => s.durationMs)).toEqual([700, 500, 300, 250, 200]);
    // All values clamped to ≥0, finite.
    for (const s of h.slowest) {
      expect(Number.isFinite(s.durationMs)).toBe(true);
      expect(s.durationMs).toBeGreaterThanOrEqual(0);
    }
  });

  it("returns long gaps and ignores null-timestamp neighbours", () => {
    const base = 1_700_000_000_000;
    const big = HOTSPOT_GAP_THRESHOLD_MS + 5_000;
    const entries: BrowseDebugSkeletonEntry[] = [
      skel({ idx: 0, timestamp: new Date(base).toISOString() }),
      skel({ idx: 1, timestamp: null }), // null timestamp — must not break gap detection
      skel({ idx: 2, timestamp: new Date(base + big).toISOString() }), // gap vs idx 0
      skel({ idx: 3, timestamp: new Date(base + big + 100).toISOString() }),
      skel({ idx: 4, timestamp: new Date(base + big * 3).toISOString() }),
    ];
    const h = deriveHotspots(entries);
    // Expected gaps (above threshold):
    //   sorted[0]→sorted[1]: big
    //   sorted[2]→sorted[3]: big*2 - 100  (largest)
    expect(h.gaps.length).toBe(2);
    expect(h.gaps.length).toBeLessThanOrEqual(MAX_HOTSPOT_GAPS);
    // Sorted descending by gapMs.
    expect(h.gaps[0].gapMs).toBeGreaterThan(h.gaps[1].gapMs);
    expect(h.gaps[0].gapMs).toBe(big * 2 - 100);
    expect(h.gaps[1].gapMs).toBe(big);
    // Every emitted gap has finite, positive endpoints from non-null timestamps.
    for (const g of h.gaps) {
      expect(Number.isFinite(g.startMs)).toBe(true);
      expect(Number.isFinite(g.endMs)).toBe(true);
      expect(g.endMs).toBeGreaterThan(g.startMs);
    }
  });

  it("returns empty buckets for empty input", () => {
    expect(deriveHotspots([])).toEqual({ errors: [], slowest: [], gaps: [] });
  });
});

// ── deriveMissionRollup ──────────────────────────────────────────────────────

describe("deriveMissionRollup", () => {
  it("rolls up chips by category and label, with errors counted once", () => {
    const entries: BrowseDebugEntry[] = [
      full({ idx: 0, kind: "tool_call", tool_name: "Read", duration_ms: 5, status: "ok" }),
      full({ idx: 1, kind: "tool_call", tool_name: "Read", duration_ms: 5, status: "ok" }),
      full({ idx: 2, kind: "tool_call", tool_name: "Edit", duration_ms: 5, status: "ok" }),
      full({ idx: 3, kind: "hook", attrs: { hook_type: "preToolUse" }, status: "ok" }),
      full({ idx: 4, kind: "hook", attrs: { hook_type: "preToolUse" }, status: "ok" }),
      full({
        idx: 5,
        kind: "generic",
        attrs: { skill_name: "code-reviewer" },
        status: "ok",
      }),
      full({ idx: 6, kind: "subagent", message: "Reviewer", status: "ok" }),
      full({
        idx: 7,
        kind: "generic",
        attrs: { compaction_kind: "auto" },
        status: "ok",
      }),
      full({
        idx: 8,
        kind: "generic",
        attrs: { compaction_kind: "manual" },
        status: "ok",
      }),
      full({ idx: 9, kind: "error", message: "boom" }),
      full({
        idx: 10,
        kind: "tool_call",
        tool_name: "Edit",
        status: "error",
        duration_ms: 5,
      }),
    ];
    const r = deriveMissionRollup(entries);
    expect(r.tools).toEqual([
      { name: "Edit", count: 2 },
      { name: "Read", count: 2 },
    ]);
    expect(r.hooks).toEqual([{ name: "preToolUse", count: 2 }]);
    expect(r.skills).toEqual([{ name: "code-reviewer", count: 1 }]);
    expect(r.subagents).toEqual([{ name: "Reviewer", count: 1 }]);
    expect(r.compactions).toBe(2); // count only, never compaction_kind string
    expect(r.errorCount).toBe(2); // idx 9 (kind=error) + idx 10 (status=error)
  });

  it("handles empty entries", () => {
    expect(deriveMissionRollup([])).toEqual({
      tools: [],
      hooks: [],
      skills: [],
      subagents: [],
      compactions: 0,
      errorCount: 0,
    });
  });
});

// ── deriveResumeAnchors ──────────────────────────────────────────────────────

describe("deriveResumeAnchors", () => {
  function snap(o: Partial<BrowseRewindSnapshotSummary> & { snapshot_id: string }) {
    return {
      snapshot_id: o.snapshot_id,
      timestamp: o.timestamp ?? null,
      git_commit: o.git_commit ?? null,
      git_branch: o.git_branch ?? null,
      file_count: o.file_count ?? 0,
      user_message_present: o.user_message_present ?? false,
      user_message_byte_size: o.user_message_byte_size ?? 0,
      event_span_id: o.event_span_id ?? null,
    } as BrowseRewindSnapshotSummary;
  }

  it("sorts anchors by timestamp desc and never carries userMessage text", () => {
    const snapshots: BrowseRewindSnapshotSummary[] = [
      snap({
        snapshot_id: "a",
        timestamp: "2024-01-01T10:00:00.000Z",
        git_commit: "a".repeat(40),
        user_message_present: true,
        user_message_byte_size: 42,
      }),
      snap({
        snapshot_id: "b",
        timestamp: "2024-01-01T12:00:00.000Z",
        git_commit: "b".repeat(40),
      }),
      snap({
        snapshot_id: "c",
        timestamp: "2024-01-01T11:00:00.000Z",
        git_commit: "c".repeat(40),
      }),
    ];
    const anchors = deriveResumeAnchors(snapshots, []);
    expect(anchors.map((a) => a.snapshotId)).toEqual(["b", "c", "a"]);
    // No userMessage field/text leaks through.
    for (const a of anchors) {
      expect(Object.keys(a)).not.toContain("userMessage");
      expect(Object.keys(a)).not.toContain("user_message");
    }
    expect(anchors[2].userMessagePresent).toBe(true);
    expect(anchors[2].userMessageByteSize).toBe(42);
  });

  it("associates anchors with task_complete by event_span_id when available", () => {
    const span = "0123456789abcdef";
    const tc = skel({ idx: 99, kind: "generic", span_id: span });
    const snapshots = [
      snap({
        snapshot_id: "z",
        timestamp: "2024-01-01T01:00:00.000Z",
        event_span_id: span,
      }),
      snap({ snapshot_id: "y", timestamp: "2024-01-01T02:00:00.000Z" }),
    ];
    const anchors = deriveResumeAnchors(snapshots, [tc]);
    const byId = new Map(anchors.map((a) => [a.snapshotId, a]));
    expect(byId.get("z")!.taskCompleteSpanId).toBe(span);
    expect(byId.get("z")!.taskCompleteEntryIdx).toBe(99);
    // "y" has no span match → falls back to nearest task_complete timestamp (only one).
    expect(byId.get("y")!.taskCompleteEntryIdx).toBe(99);
  });

  it("sorts anchors with null timestamps last and resolves by snapshotId tiebreak", () => {
    const snapshots = [
      snap({ snapshot_id: "m", timestamp: null }),
      snap({ snapshot_id: "a", timestamp: null }),
      snap({ snapshot_id: "n", timestamp: "2024-01-01T00:00:00.000Z" }),
    ];
    const anchors = deriveResumeAnchors(snapshots, []);
    expect(anchors.map((a) => a.snapshotId)).toEqual(["n", "a", "m"]);
  });

  it("reports association='exact' when event_span_id matches a task_complete", () => {
    const span = "0123456789abcdef";
    const tc = skel({ idx: 99, kind: "generic", span_id: span });
    const anchors = deriveResumeAnchors(
      [snap({ snapshot_id: "z", timestamp: "2024-01-01T01:00:00.000Z", event_span_id: span })],
      [tc]
    );
    expect(anchors[0].association).toBe("exact");
    expect(anchors[0].associationDistanceMs).toBeNull();
  });

  it("reports association='nearest' with distance when only timestamps match", () => {
    const tc = skel({
      idx: 7,
      kind: "generic",
      span_id: "deadbeefdeadbeef",
      // task_complete @ T=10_000ms
      timestamp: new Date(10_000).toISOString(),
    });
    const anchors = deriveResumeAnchors(
      [
        snap({
          snapshot_id: "y",
          timestamp: new Date(12_500).toISOString(),
          event_span_id: "ffffffffffffffff", // no span match
        }),
      ],
      [tc]
    );
    expect(anchors[0].association).toBe("nearest");
    expect(anchors[0].associationDistanceMs).toBe(2500);
    expect(anchors[0].taskCompleteEntryIdx).toBe(7);
  });

  it("reports association='unavailable' when no task_completes exist", () => {
    const anchors = deriveResumeAnchors(
      [snap({ snapshot_id: "u", timestamp: "2024-01-01T00:00:00.000Z" })],
      []
    );
    expect(anchors[0].association).toBe("unavailable");
    expect(anchors[0].taskCompleteEntryIdx).toBeNull();
    expect(anchors[0].associationDistanceMs).toBeNull();
  });
});
