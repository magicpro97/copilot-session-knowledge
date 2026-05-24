/**
 * Component tests for ChapterRail (Flight Recorder v3 §4d).
 */
import "@testing-library/jest-dom";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ChapterRail } from "@/components/data/chapter-rail";
import type { Chapter } from "@/lib/flight-recorder";

function chap(overrides: Partial<Chapter> & { id: string }): Chapter {
  return {
    mode: "turn",
    label: "Turn 1",
    status: "ok",
    startSortedIndex: 0,
    endSortedIndex: 10,
    startMs: null,
    durationMs: null,
    eventCount: 11,
    taskCompleteSortedIndexes: [],
    ...overrides,
  };
}

describe("ChapterRail", () => {
  it("renders nothing when no chapters", () => {
    const { container } = render(<ChapterRail chapters={[]} />);
    expect(container.firstChild).toBeNull();
  });

  it("renders chapter buttons with required data attributes", () => {
    const chapters: Chapter[] = [
      chap({
        id: "a",
        mode: "checkpoint",
        label: "Bootstrap",
        status: "ok",
        startSortedIndex: 0,
        endSortedIndex: 4,
        eventCount: 5,
        durationMs: 1500,
        checkpointSeq: 7,
      }),
      chap({
        id: "b",
        mode: "checkpoint",
        label: "Plan",
        status: "error",
        startSortedIndex: 5,
        endSortedIndex: 9,
        eventCount: 5,
        durationMs: 2500,
        checkpointSeq: 8,
      }),
    ];
    render(<ChapterRail chapters={chapters} />);
    const a = screen.getByTestId("chapter-rail-chapter-7");
    expect(a).toHaveAttribute("data-chapter-status", "ok");
    expect(a).toHaveAttribute("data-chapter-event-count", "5");
    expect(a).toHaveAttribute("data-chapter-duration-ms", "1500");
    expect(a).toHaveAttribute("data-chapter-mode", "checkpoint");
    expect(a).toHaveAttribute("data-checkpoint-seq", "7");

    const b = screen.getByTestId("chapter-rail-chapter-8");
    expect(b).toHaveAttribute("data-chapter-status", "error");
  });

  it("invokes onSelectChapter with the chapter's start sortedIndex", () => {
    const onClick = vi.fn();
    const chapters = [chap({ id: "a", startSortedIndex: 12, endSortedIndex: 20 })];
    render(<ChapterRail chapters={chapters} onSelectChapter={onClick} />);
    fireEvent.click(screen.getByTestId("chapter-rail-chapter-0"));
    expect(onClick).toHaveBeenCalledWith(12);
  });

  it("renders task_complete ticks and exposes data-task-complete-idx", () => {
    const onTC = vi.fn();
    const chapters = [
      chap({
        id: "a",
        startSortedIndex: 0,
        endSortedIndex: 9,
        eventCount: 10,
        taskCompleteSortedIndexes: [3, 7],
      }),
    ];
    render(<ChapterRail chapters={chapters} onSelectTaskComplete={onTC} />);
    const t3 = screen.getByTestId("chapter-rail-task-complete-3");
    const t7 = screen.getByTestId("chapter-rail-task-complete-7");
    expect(t3).toHaveAttribute("data-task-complete-idx", "3");
    expect(t7).toHaveAttribute("data-task-complete-idx", "7");
    fireEvent.click(t7);
    expect(onTC).toHaveBeenCalledWith(7);
  });

  it("supports turn-fallback mode without checkpoint seq attribute", () => {
    const chapters = [
      chap({ id: "a", mode: "turn", label: "Turn 0", startSortedIndex: 0, endSortedIndex: 4 }),
    ];
    render(<ChapterRail chapters={chapters} />);
    const c = screen.getByTestId("chapter-rail-chapter-0");
    expect(c).toHaveAttribute("data-chapter-mode", "turn");
    expect(c).not.toHaveAttribute("data-checkpoint-seq");
  });
});
