import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { DiffViewer } from "@/components/data/diff-viewer";
import type { DiffResult } from "@/lib/api/types";

const SAMPLE_DIFF: DiffResult = {
  session_id: "session-1",
  from: { seq: 1, title: "Checkpoint 1", file: "checkpoint_001.md" },
  to: { seq: 3, title: "Checkpoint 3", file: "checkpoint_003.md" },
  unified_diff: [
    "--- checkpoint_001.md",
    "+++ checkpoint_003.md",
    "@@ -1,2 +1,2 @@",
    "-Removed detail",
    " context line",
    "+Added detail",
  ].join("\n"),
  files: [{ from: "checkpoint_001.md", to: "checkpoint_003.md" }],
  stats: { added: 1, removed: 1 },
};

describe("DiffViewer", () => {
  it("highlights added and removed lines in unified mode", () => {
    render(<DiffViewer result={SAMPLE_DIFF} />);

    const added = screen.getByText("+Added detail");
    const removed = screen.getByText("-Removed detail");

    expect(added).toHaveAttribute("data-diff-kind", "add");
    expect(added.className).toContain("bg-emerald-500/10");
    expect(removed).toHaveAttribute("data-diff-kind", "remove");
    expect(removed.className).toContain("bg-rose-500/10");
  });

  it("preserves add/remove highlighting in side-by-side mode", () => {
    render(<DiffViewer result={SAMPLE_DIFF} />);

    fireEvent.click(screen.getByRole("button", { name: "Side-by-side" }));

    const added = screen.getByText("+Added detail");
    const removed = screen.getByText("-Removed detail");

    expect(added).toHaveAttribute("data-diff-kind", "add");
    expect(added).toHaveAttribute("data-diff-side", "right");
    expect(removed).toHaveAttribute("data-diff-kind", "remove");
    expect(removed).toHaveAttribute("data-diff-side", "left");
  });
});
