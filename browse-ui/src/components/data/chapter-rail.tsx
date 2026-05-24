/**
 * Flight Recorder v3 — Chapter rail (synthesis §4d/§4e).
 *
 * Renders a horizontal rail between the existing overview strip and the
 * scrubber. Source of truth is `deriveChapters` from `@/lib/flight-recorder`
 * — this component never inspects raw entries, attrs, or messages.
 *
 *  Selector contract:
 *    - root:    data-testid="chapter-rail"
 *    - chapter: data-testid="chapter-rail-chapter-<seq>"
 *               data-chapter-status, data-chapter-event-count,
 *               data-chapter-duration-ms, data-chapter-mode,
 *               data-checkpoint-seq (only when mode="checkpoint")
 *    - tick:    data-testid="chapter-rail-task-complete-<idx>"
 *               data-task-complete-idx
 */
import { useMemo } from "react";

import type { Chapter } from "@/lib/flight-recorder";
import { cn } from "@/lib/utils";

export type ChapterRailProps = {
  chapters: Chapter[];
  /** Sorted index of the playhead (used to highlight the active chapter). */
  playheadSortedIndex?: number;
  /** Called with the start sorted index of the clicked chapter. */
  onSelectChapter?: (sortedIndex: number) => void;
  /** Called with the sorted index of the clicked task_complete tick. */
  onSelectTaskComplete?: (sortedIndex: number) => void;
};

const STATUS_DOT_CLS: Record<Chapter["status"], string> = {
  ok: "bg-emerald-400",
  error: "bg-red-500",
  cancelled: "bg-amber-400",
  incomplete: "bg-slate-400",
};

const STATUS_RAIL_CLS: Record<Chapter["status"], string> = {
  ok: "bg-emerald-700/40 hover:bg-emerald-700/60",
  error: "bg-red-700/50 hover:bg-red-700/70",
  cancelled: "bg-amber-700/40 hover:bg-amber-700/60",
  incomplete: "bg-slate-700/40 hover:bg-slate-700/60",
};

export function ChapterRail({
  chapters,
  playheadSortedIndex,
  onSelectChapter,
  onSelectTaskComplete,
}: ChapterRailProps) {
  const totalEvents = useMemo(() => {
    if (chapters.length === 0) return 0;
    let last = 0;
    for (const c of chapters) if (c.endSortedIndex > last) last = c.endSortedIndex;
    return last + 1;
  }, [chapters]);

  if (chapters.length === 0 || totalEvents <= 0) return null;

  return (
    <div
      role="group"
      aria-label="Session chapters"
      data-testid="chapter-rail"
      className="border-border bg-card relative flex h-6 w-full overflow-hidden rounded border"
    >
      {chapters.map((chapter, i) => {
        const seq = chapter.checkpointSeq ?? i;
        const widthPct =
          ((chapter.endSortedIndex - chapter.startSortedIndex + 1) / totalEvents) * 100;
        const isActive =
          typeof playheadSortedIndex === "number" &&
          playheadSortedIndex >= chapter.startSortedIndex &&
          playheadSortedIndex <= chapter.endSortedIndex;
        const title = `${chapter.label} — ${chapter.eventCount} event${
          chapter.eventCount === 1 ? "" : "s"
        }`;

        return (
          <div
            key={chapter.id}
            className="relative h-full"
            style={{ width: `${widthPct}%`, minWidth: 4 }}
          >
            <button
              type="button"
              data-testid={`chapter-rail-chapter-${seq}`}
              data-chapter-status={chapter.status}
              data-chapter-event-count={chapter.eventCount}
              data-chapter-duration-ms={chapter.durationMs ?? ""}
              data-chapter-mode={chapter.mode}
              {...(chapter.mode === "checkpoint" && typeof chapter.checkpointSeq === "number"
                ? { "data-checkpoint-seq": chapter.checkpointSeq }
                : {})}
              aria-label={`Chapter ${seq}: ${chapter.label} (${chapter.status})`}
              aria-pressed={isActive}
              title={title}
              onClick={() => onSelectChapter?.(chapter.startSortedIndex)}
              className={cn(
                "flex h-full w-full items-center justify-start gap-1 truncate px-1 text-left text-[10px]",
                STATUS_RAIL_CLS[chapter.status],
                isActive && "ring-foreground/40 ring-2 ring-inset"
              )}
            >
              <span
                aria-hidden
                className={cn(
                  "inline-block size-1.5 shrink-0 rounded-full",
                  STATUS_DOT_CLS[chapter.status]
                )}
              />
              <span className="truncate font-mono">{chapter.label}</span>
            </button>

            {chapter.taskCompleteSortedIndexes.map((sortedIdx) => {
              // Position within this chapter's local span.
              const span = chapter.endSortedIndex - chapter.startSortedIndex + 1;
              const localPct =
                span <= 1
                  ? 50
                  : ((sortedIdx - chapter.startSortedIndex) / Math.max(1, span - 1)) * 100;
              return (
                <button
                  key={`tc-${sortedIdx}`}
                  type="button"
                  data-testid={`chapter-rail-task-complete-${sortedIdx}`}
                  data-task-complete-idx={sortedIdx}
                  aria-label={`Task complete marker at event ${sortedIdx}`}
                  title="task_complete"
                  onClick={(e) => {
                    e.stopPropagation();
                    onSelectTaskComplete?.(sortedIdx);
                  }}
                  className="pointer-events-auto absolute top-0 h-full w-1 -translate-x-1/2 bg-yellow-300/90 hover:bg-yellow-200"
                  style={{ left: `${Math.min(Math.max(localPct, 0), 100)}%` }}
                />
              );
            })}
          </div>
        );
      })}
    </div>
  );
}
