import { describe, expect, it } from "vitest";

import { findRecoverableActiveRun, visibleHistoricalRuns } from "./run-state";
import type { OperatorRunInfo } from "@/lib/api/types";

function makeRun(overrides: Partial<OperatorRunInfo>): OperatorRunInfo {
  return {
    id: "run-1",
    session_id: "session-1",
    prompt: "Prompt",
    status: "done",
    exit_code: 0,
    started_at: "2026-05-06T01:00:00Z",
    finished_at: "2026-05-06T01:00:10Z",
    events: [],
    ...overrides,
  };
}

describe("chat run-state helpers", () => {
  it("recovers the latest non-terminal run after a browser reload", () => {
    const recovered = findRecoverableActiveRun([
      makeRun({ id: "done", status: "done", started_at: "2026-05-06T01:00:00Z" }),
      makeRun({ id: "old-running", status: "running", started_at: "2026-05-06T01:01:00Z" }),
      makeRun({
        id: "latest-running",
        status: "running",
        prompt: "Keep going",
        started_at: "2026-05-06T01:02:00Z",
        files: [{ name: "notes.txt", type: "text/plain", size: 12 }],
      }),
    ]);

    expect(recovered).toEqual({
      id: "latest-running",
      prompt: "Keep going",
      files: [{ name: "notes.txt", type: "text/plain", size: 12 }],
    });
  });

  it("does not recover a terminal run", () => {
    expect(findRecoverableActiveRun([makeRun({ id: "done", status: "done" })])).toBeNull();
  });

  it("does not recover a suppressed non-terminal run after a stream error", () => {
    expect(
      findRecoverableActiveRun(
        [makeRun({ id: "errored-stream", status: "running", finished_at: null })],
        "errored-stream"
      )
    ).toBeNull();
  });

  it("hides the active run from historical rows to avoid duplicate transcripts", () => {
    const runs = [
      makeRun({ id: "persisted" }),
      makeRun({ id: "active", status: "running", finished_at: null }),
    ];

    expect(visibleHistoricalRuns(runs, { id: "active", prompt: "Prompt" })).toEqual([runs[0]]);
  });
});
