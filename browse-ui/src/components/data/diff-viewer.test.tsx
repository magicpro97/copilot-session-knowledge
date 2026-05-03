import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { DiffViewer, FileDiffViewer } from "@/components/data/diff-viewer";
import type { DiffResult, FileDiffResponse } from "@/lib/api/types";

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

const SAMPLE_FILE_DIFF: FileDiffResponse = {
  path_a: "~/project/src/main.ts",
  path_b: "~/project/src/main.ts",
  unified_diff: [
    "--- ~/project/src/main.ts",
    "+++ ~/project/src/main.ts",
    "@@ -1,3 +1,3 @@",
    " unchanged line",
    "-const old = 1;",
    "+const updated = 2;",
  ].join("\n"),
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

describe("FileDiffViewer", () => {
  it("renders the file path in the title", () => {
    render(<FileDiffViewer result={SAMPLE_FILE_DIFF} />);
    expect(screen.getByText("~/project/src/main.ts")).toBeInTheDocument();
  });

  it("shows stat counts", () => {
    render(<FileDiffViewer result={SAMPLE_FILE_DIFF} />);
    expect(screen.getByText(/1 added/)).toBeInTheDocument();
    expect(screen.getByText(/1 removed/)).toBeInTheDocument();
  });

  it("highlights add and remove lines in unified mode", () => {
    render(<FileDiffViewer result={SAMPLE_FILE_DIFF} />);

    const added = screen.getByText("+const updated = 2;");
    const removed = screen.getByText("-const old = 1;");

    expect(added).toHaveAttribute("data-diff-kind", "add");
    expect(added.className).toContain("bg-emerald-500/10");
    expect(removed).toHaveAttribute("data-diff-kind", "remove");
    expect(removed.className).toContain("bg-rose-500/10");
  });

  it("shows side-by-side mode when toggled", () => {
    render(<FileDiffViewer result={SAMPLE_FILE_DIFF} />);

    fireEvent.click(screen.getByRole("button", { name: "Side-by-side" }));

    const added = screen.getByText("+const updated = 2;");
    const removed = screen.getByText("-const old = 1;");

    expect(added).toHaveAttribute("data-diff-side", "right");
    expect(removed).toHaveAttribute("data-diff-side", "left");
  });
});
