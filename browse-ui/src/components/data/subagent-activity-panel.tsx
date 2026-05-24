"use client";

/**
 * SubagentActivityPanel — renders sub-agent executions from the dedicated
 * /api/session/{id}/subagent-activity route.
 *
 * Data source MUST be derived executions from `deriveSubagentExecutions` and
 * a summary from `deriveSubagentActivitySummary` — never paginated debug-log
 * entries. Raw error text is never rendered; only the error_category enum.
 *
 * Selectors:
 *   - data-testid="subagent-activity-panel"           (root region)
 *   - data-testid="subagent-loading"                   (loading state)
 *   - data-testid="subagent-error"                     (error state)
 *   - data-testid="subagent-empty"                     (empty state)
 *   - data-testid="subagent-summary-total"
 *   - data-testid="subagent-summary-failed"            (hidden when 0)
 *   - data-testid="subagent-summary-running"           (hidden when 0)
 *   - data-testid="subagent-summary-truncated"         (when capped)
 *   - data-testid="subagent-filter-all|failed|running" (filter pills)
 *   - data-testid="subagent-activity-table"
 *   - data-testid="subagent-activity-row-<slug>"       per row
 *       data-status="ok|error|running|unknown"
 *       data-agent="<name>"
 *   - data-testid="subagent-activity-row-<slug>-status|started|agent|model|
 *                  tools|tokens|duration|outcome|jump"
 */

import { forwardRef, useEffect, useRef, useState } from "react";

import type {
  SubagentActivitySummary,
  SubagentExecution,
  SubagentFilter,
} from "@/lib/flight-recorder";
import { filterSubagentExecutions } from "@/lib/flight-recorder";

// ── Helpers ───────────────────────────────────────────────────────────────────

/**
 * Produce a safe test-id slug from a subagent execution id.
 * IDs produced by deriveSubagentId are already alphanumeric+dash; this is a
 * defensive pass that strips anything else and clamps length to 64 chars.
 */
function safeTestSlug(id: string): string {
  const slug = id
    .replace(/[^a-zA-Z0-9_-]/g, "-")
    .replace(/-{2,}/g, "-")
    .replace(/^-|-$/g, "");
  return (slug.length > 64 ? slug.slice(0, 64) : slug) || "row";
}

/** Format duration for display (empty string for null/invalid). */
function formatDur(ms: number | null): string {
  if (ms === null || !Number.isFinite(ms) || ms < 0) return "";
  if (ms < 1000) return `${Math.round(ms)}ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`;
  const minutes = Math.floor(ms / 60_000);
  const seconds = Math.floor((ms % 60_000) / 1000);
  return `${minutes}m${seconds.toString().padStart(2, "0")}s`;
}

/** Format ISO timestamp as locale time string (or "—"). */
function formatTime(ts: string | null): string {
  if (!ts) return "—";
  try {
    return new Date(ts).toLocaleTimeString(undefined, {
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    });
  } catch {
    return ts;
  }
}

/** Derive outcome text — never renders raw error text. */
function outcomeText(exec: SubagentExecution): string {
  if (exec.status === "completed") return "Completed";
  if (exec.status === "failed") {
    return exec.errorCategory ? `Failed: ${exec.errorCategory}` : "Failed";
  }
  if (exec.status === "running") return "Running...";
  return "Unknown";
}

/** Map execution status to data-status attribute value. */
function rowDataStatus(exec: SubagentExecution): "ok" | "error" | "running" | "unknown" {
  if (exec.status === "completed") return "ok";
  if (exec.status === "failed") return "error";
  if (exec.status === "running") return "running";
  return "unknown";
}

// ── Types ─────────────────────────────────────────────────────────────────────

export interface SubagentActivityPanelProps {
  /** Derived executions from deriveSubagentExecutions — NOT raw API entries. */
  executions: SubagentExecution[];
  /** Derived summary from deriveSubagentActivitySummary. */
  summary: SubagentActivitySummary;
  /** True while the subagent activity query is loading. */
  loading?: boolean;
  /** Error object when the query has failed. */
  error?: Error | null;
  /**
   * Called when the user clicks a row's Jump button.
   * Argument is start_idx ?? end_idx of the execution.
   */
  onOpenDebugLog?: (idx: number) => void;
  /**
   * Current playhead time (ms since epoch). When set, rows whose
   * [startedAtMs, endedAtMs) window contains this value get
   * aria-current="true" and a highlight style.
   */
  activeTimeMs?: number | null;
  /**
   * Called when the user clicks a row's body (not the Jump button).
   * Argument is the execution's startedAtMs — for Timeline seek.
   * When undefined, row body clicks do nothing.
   */
  onSeekToExecution?: (startedAtMs: number) => void;
  /**
   * Incremented by the parent each time a flash+reset is requested
   * (e.g., when the MissionStrip sub-agents chip is clicked).
   * The panel responds by resetting the filter to "all" and briefly
   * showing data-flash="true".
   */
  flashTick?: number;
}

// ── Component ─────────────────────────────────────────────────────────────────

export const SubagentActivityPanel = forwardRef<HTMLDivElement, SubagentActivityPanelProps>(
  function SubagentActivityPanel(
    {
      executions,
      summary,
      loading,
      error,
      onOpenDebugLog,
      activeTimeMs,
      onSeekToExecution,
      flashTick = 0,
    },
    ref
  ) {
    const [filter, setFilter] = useState<SubagentFilter>("all");
    const [isFlashing, setIsFlashing] = useState(false);
    const flashTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

    // React to flash tick — reset filter to "all" and briefly set data-flash.
    useEffect(() => {
      if (flashTick === 0) return;
      setFilter("all");
      setIsFlashing(true);
      if (flashTimerRef.current !== null) clearTimeout(flashTimerRef.current);
      flashTimerRef.current = setTimeout(() => {
        setIsFlashing(false);
        flashTimerRef.current = null;
      }, 1500);
    }, [flashTick]);

    // Cleanup on unmount.
    useEffect(() => {
      return () => {
        if (flashTimerRef.current !== null) clearTimeout(flashTimerRef.current);
      };
    }, []);

    const rootProps = {
      ref,
      role: "region" as const,
      "aria-label": "Sub-agent activity",
      "data-testid": "subagent-activity-panel",
      ...(isFlashing ? { "data-flash": "true" } : {}),
    };

    // ── Loading ──────────────────────────────────────────────────────────────

    if (loading) {
      return (
        <div {...rootProps} className="border-border bg-card rounded-xl border p-4">
          <div data-testid="subagent-loading" className="text-muted-foreground text-sm">
            Loading sub-agent activity…
          </div>
        </div>
      );
    }

    // ── Error ────────────────────────────────────────────────────────────────

    if (error) {
      return (
        <div {...rootProps} className="border-border bg-card rounded-xl border p-4">
          <div
            data-testid="subagent-error"
            role="alert"
            className="border-border rounded-lg border border-yellow-500/30 bg-yellow-50/20 px-3 py-2 text-sm dark:bg-yellow-950/20"
          >
            <p className="font-medium">Sub-agent activity unavailable</p>
            <p className="text-muted-foreground mt-0.5 text-xs">
              Could not load sub-agent activity data.
            </p>
          </div>
        </div>
      );
    }

    // ── Empty ────────────────────────────────────────────────────────────────

    if (executions.length === 0) {
      return (
        <div {...rootProps} className="border-border bg-card rounded-xl border p-4">
          <div data-testid="subagent-empty" className="text-muted-foreground text-sm">
            No sub-agent activity recorded for this session yet.
          </div>
        </div>
      );
    }

    // ── Success ──────────────────────────────────────────────────────────────

    const failedCount = executions.filter((e) => e.status === "failed").length;
    const runningCount = executions.filter((e) => e.status === "running").length;
    const filtered = filterSubagentExecutions(executions, filter);

    return (
      <div {...rootProps} className="border-border bg-card space-y-2 rounded-xl border p-3">
        {/* Summary bar */}
        <div className="flex flex-wrap items-center gap-3 text-xs">
          <span data-testid="subagent-summary-total" className="font-medium">
            {executions.length} run{executions.length !== 1 ? "s" : ""}
          </span>
          {failedCount > 0 && (
            <span data-testid="subagent-summary-failed" className="text-red-600 dark:text-red-400">
              {failedCount} failed
            </span>
          )}
          {runningCount > 0 && (
            <span
              data-testid="subagent-summary-running"
              className="text-yellow-600 dark:text-yellow-400"
            >
              {runningCount} running
            </span>
          )}
          {summary.truncated && (
            <span data-testid="subagent-summary-truncated" className="text-muted-foreground">
              Showing first {summary.cap.toLocaleString()} of {summary.totalSeen.toLocaleString()}{" "}
              runs
            </span>
          )}
        </div>

        {/* Filter pills */}
        <div role="group" aria-label="Filter by status" className="flex gap-1">
          {(["all", "failed", "running"] as const).map((f) => (
            <button
              key={f}
              type="button"
              data-testid={`subagent-filter-${f}`}
              aria-pressed={filter === f}
              className={`rounded-full border px-2 py-0.5 text-xs transition-colors ${
                filter === f
                  ? "border-primary bg-primary text-primary-foreground"
                  : "border-border text-muted-foreground hover:text-foreground"
              }`}
              onClick={() => setFilter(f)}
            >
              {f === "all" ? "All" : f === "failed" ? "Failed" : "Running"}
            </button>
          ))}
        </div>

        {/* Table or filter-empty */}
        {filtered.length === 0 ? (
          <div
            data-testid="subagent-empty"
            className="text-muted-foreground py-4 text-center text-xs"
          >
            No executions match the current filter.
          </div>
        ) : (
          <div
            role="grid"
            aria-label="Sub-agent executions"
            data-testid="subagent-activity-table"
            className="border-border overflow-x-auto rounded-lg border"
          >
            <table className="w-full text-xs">
              <thead>
                <tr className="border-border bg-muted/40 border-b">
                  <th className="text-muted-foreground w-6 px-2 py-1.5 text-left font-medium">
                    <span className="sr-only">Status</span>
                  </th>
                  <th className="text-muted-foreground px-2 py-1.5 text-left font-medium whitespace-nowrap">
                    Started
                  </th>
                  <th className="text-muted-foreground px-2 py-1.5 text-left font-medium">Agent</th>
                  <th className="text-muted-foreground px-2 py-1.5 text-left font-medium">Model</th>
                  <th className="text-muted-foreground px-2 py-1.5 text-right font-medium">
                    Tools
                  </th>
                  <th className="text-muted-foreground px-2 py-1.5 text-right font-medium">
                    Tokens
                  </th>
                  <th className="text-muted-foreground px-2 py-1.5 text-right font-medium whitespace-nowrap">
                    Duration
                  </th>
                  <th className="text-muted-foreground px-2 py-1.5 text-left font-medium">
                    Outcome
                  </th>
                  <th
                    className="text-muted-foreground w-12 px-2 py-1.5 text-left font-medium"
                    aria-label="Jump to debug log"
                  />
                </tr>
              </thead>
              <tbody>
                {filtered.map((exec) => {
                  const slug = safeTestSlug(exec.id);
                  const rowId = `subagent-activity-row-${slug}`;
                  const jumpIdx = exec.startIdx ?? exec.endIdx;

                  const isActive =
                    activeTimeMs !== null &&
                    activeTimeMs !== undefined &&
                    exec.startedAtMs !== null &&
                    exec.endedAtMs !== null &&
                    activeTimeMs >= exec.startedAtMs &&
                    activeTimeMs < exec.endedAtMs;

                  const canSeek = Boolean(onSeekToExecution && exec.startedAtMs !== null);

                  return (
                    <tr
                      key={exec.id}
                      data-testid={rowId}
                      data-status={rowDataStatus(exec)}
                      data-agent={exec.agentName ?? exec.displayName}
                      aria-current={isActive ? "true" : undefined}
                      className={[
                        "border-border border-b transition-colors last:border-0",
                        exec.status === "failed" ? "border-l-2 border-l-red-500" : "",
                        isActive ? "bg-primary/5" : "hover:bg-muted/30",
                        canSeek ? "cursor-pointer" : "",
                      ]
                        .filter(Boolean)
                        .join(" ")}
                      onClick={
                        canSeek && exec.startedAtMs !== null
                          ? () => onSeekToExecution!(exec.startedAtMs!)
                          : undefined
                      }
                    >
                      {/* Status icon */}
                      <td data-testid={`${rowId}-status`} className="px-2 py-1.5 text-center">
                        {exec.status === "completed" ? (
                          <span
                            className="text-green-600 dark:text-green-400"
                            aria-label="Completed"
                          >
                            ✓
                          </span>
                        ) : exec.status === "failed" ? (
                          <span className="text-red-600 dark:text-red-400" aria-label="Failed">
                            ✕
                          </span>
                        ) : exec.status === "running" ? (
                          <span
                            className="text-yellow-600 dark:text-yellow-400"
                            aria-label="Running"
                          >
                            ⟳
                          </span>
                        ) : (
                          <span className="text-muted-foreground" aria-label="Unknown">
                            ?
                          </span>
                        )}
                      </td>

                      {/* Started */}
                      <td
                        data-testid={`${rowId}-started`}
                        className="px-2 py-1.5 whitespace-nowrap"
                        title={exec.startedAt ?? ""}
                      >
                        {formatTime(exec.startedAt)}
                      </td>

                      {/* Agent display name */}
                      <td
                        data-testid={`${rowId}-agent`}
                        className="max-w-[120px] truncate px-2 py-1.5 font-medium"
                        title={exec.displayName}
                      >
                        {exec.displayName}
                      </td>

                      {/* Model — empty string when null (never "null") */}
                      <td
                        data-testid={`${rowId}-model`}
                        className="text-muted-foreground px-2 py-1.5"
                      >
                        {exec.model ?? ""}
                      </td>

                      {/* Tool calls — empty string when null */}
                      <td
                        data-testid={`${rowId}-tools`}
                        className="px-2 py-1.5 text-right tabular-nums"
                      >
                        {exec.totalToolCalls !== null ? exec.totalToolCalls : ""}
                      </td>

                      {/* Tokens — empty string when null */}
                      <td
                        data-testid={`${rowId}-tokens`}
                        className="px-2 py-1.5 text-right tabular-nums"
                      >
                        {exec.totalTokens !== null ? exec.totalTokens.toLocaleString() : ""}
                      </td>

                      {/* Duration */}
                      <td
                        data-testid={`${rowId}-duration`}
                        className="px-2 py-1.5 text-right whitespace-nowrap tabular-nums"
                      >
                        {formatDur(exec.durationMs)}
                      </td>

                      {/* Outcome — never raw error text */}
                      <td data-testid={`${rowId}-outcome`} className="px-2 py-1.5">
                        {outcomeText(exec)}
                      </td>

                      {/* Jump button */}
                      <td data-testid={`${rowId}-jump`} className="px-2 py-1.5">
                        {jumpIdx !== null && onOpenDebugLog ? (
                          <button
                            type="button"
                            aria-label={`Jump to debug log entry for ${exec.displayName}`}
                            className="border-border hover:bg-muted rounded border px-1.5 py-0.5 text-xs"
                            onClick={(e) => {
                              e.stopPropagation();
                              onOpenDebugLog(jumpIdx);
                            }}
                          >
                            Jump
                          </button>
                        ) : null}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    );
  }
);
