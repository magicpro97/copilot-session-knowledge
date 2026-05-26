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

import { Fragment, forwardRef, useEffect, useRef, useState } from "react";

import type {
  SubagentActivitySummary,
  SubagentExecution,
  SubagentFilter,
  SubagentInternalsBySpanId,
  SubagentInternalsSummary,
} from "@/lib/flight-recorder";
import { filterSubagentExecutions } from "@/lib/flight-recorder";
import type { SubagentInternalsEntry } from "@/lib/api/types";

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

/** Format byte counts for compact tool I/O chips. */
function formatBytes(bytes: number | null): string {
  if (bytes === null || !Number.isFinite(bytes) || bytes < 0) return "";
  if (bytes < 1024) return `${bytes}B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)}KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)}MB`;
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

/**
 * Human-readable rendering for failed subagent executions.
 *
 * Static lookup — never renders backend-provided text. All copy is hard-coded
 * here so the panel cannot accidentally surface raw error messages.
 *
 * Returns:
 *  - `label`: short badge text shown in the outcome cell.
 *  - `guidance`: longer explanation surfaced via tooltip / aria-label for
 *    operators (cause + recovery steps).
 */
function failedOutcomeCopy(category: SubagentExecution["errorCategory"]): {
  label: string;
  guidance: string;
} {
  switch (category) {
    case "rate_limited":
      return {
        label: "Rate limited",
        guidance:
          "Provider or CLI rate limit reached. Wait a moment, reduce sub-agent concurrency, or switch model/settings before retrying.",
      };
    case "timeout":
      return {
        label: "Timed out",
        guidance:
          "The sub-agent did not respond in time. Retry, or split the task into smaller steps.",
      };
    case "api_error":
      return {
        label: "API error",
        guidance:
          "The model provider returned an error. Retry shortly; if it persists, check provider status.",
      };
    case "internal_error":
      return {
        label: "Internal error",
        guidance:
          "An internal error occurred while running the sub-agent. Retry; if it persists, check the debug log.",
      };
    case "cancelled":
      return {
        label: "Cancelled",
        guidance: "The sub-agent run was cancelled before completion.",
      };
    case "unknown":
      return {
        label: "Failed",
        guidance:
          "The sub-agent failed for an uncategorised reason. Check the debug log for details.",
      };
    default:
      // Defensive fallback for any future category not yet mapped here.
      return {
        label: "Failed",
        guidance: "The sub-agent failed. Check the debug log for details.",
      };
  }
}

/** Derive outcome text — never renders raw error text. */
function outcomeText(exec: SubagentExecution): string {
  if (exec.status === "completed") return "Completed";
  if (exec.status === "failed") {
    if (!exec.errorCategory) return "Failed";
    return failedOutcomeCopy(exec.errorCategory).label;
  }
  if (exec.status === "running") return "Running...";
  return "Unknown";
}

/** Static, render-time guidance for a failed execution. Returns null when not applicable. */
function outcomeGuidance(exec: SubagentExecution): string | null {
  if (exec.status !== "failed" || !exec.errorCategory) return null;
  return failedOutcomeCopy(exec.errorCategory).guidance;
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
  /** Lookup from activity span_id to safe backend internals row. */
  internalsBySpanId?: SubagentInternalsBySpanId;
  /** Envelope-level internals metadata, including session-level skill attribution status. */
  internalsSummary?: SubagentInternalsSummary;
  /** True while the internals query is loading. */
  internalsLoading?: boolean;
  /** Error object when internals query failed; raw message is never rendered. */
  internalsError?: Error | null;
}

type SubagentInternalsDetailsProps = {
  execution: SubagentExecution;
  row: SubagentInternalsEntry | null;
  loading?: boolean;
  error?: Error | null;
  summary?: SubagentInternalsSummary;
  onOpenDebugLog?: (idx: number) => void;
  rowId: string;
};

function SubagentInternalsDetails({
  execution,
  row,
  loading,
  error,
  summary,
  onOpenDebugLog,
  rowId,
}: SubagentInternalsDetailsProps) {
  const jumpTo = (idx: number) => {
    if (onOpenDebugLog) onOpenDebugLog(idx);
  };

  if (loading) {
    return (
      <div className="text-muted-foreground px-3 py-2 text-xs" data-testid={`${rowId}-trace`}>
        Loading sub-agent internals...
      </div>
    );
  }

  if (error) {
    return (
      <div
        className="text-muted-foreground px-3 py-2 text-xs"
        data-testid={`${rowId}-trace`}
        role="status"
      >
        Sub-agent internals unavailable.
      </div>
    );
  }

  if (!row) {
    return (
      <div className="text-muted-foreground px-3 py-2 text-xs" data-testid={`${rowId}-trace`}>
        No correlated internals found for {execution.displayName}.
      </div>
    );
  }

  const { internals } = row;
  const firstErrorTool = internals.tools.find((tool) => tool.status === "failed");

  return (
    <div
      className="bg-muted/20 space-y-2 px-3 py-2 text-xs"
      data-testid={`${rowId}-trace`}
      aria-label={`Internals for ${execution.displayName}`}
    >
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-medium">Internals</span>
        <span className="text-muted-foreground" data-testid={`${rowId}-trace-summary`}>
          {internals.tool_call_count} tools · {internals.llm_turn_count} model messages ·{" "}
          {internals.output_tokens_total.toLocaleString()} output tokens ·{" "}
          {internals.internal_event_count} correlated events
        </span>
        {internals.tool_failure_count > 0 ? (
          <span className="text-red-600 dark:text-red-400">
            {internals.tool_failure_count} tool failure
            {internals.tool_failure_count === 1 ? "" : "s"}
          </span>
        ) : null}
      </div>

      {internals.tool_names.length > 0 ? (
        <div className="flex flex-wrap gap-1" data-testid={`${rowId}-trace-tool-names`}>
          {internals.tool_names.map((name) => (
            <span key={name} className="border-border bg-background rounded border px-1.5 py-0.5">
              {name}
            </span>
          ))}
        </div>
      ) : null}

      {internals.tools.length > 0 ? (
        <div className="space-y-1" data-testid={`${rowId}-trace-tools`}>
          <p className="text-muted-foreground font-medium">Tool timeline</p>
          {internals.tools.slice(0, 30).map((tool) => {
            const statusClass =
              tool.status === "failed"
                ? "bg-red-500"
                : tool.status === "completed"
                  ? "bg-green-500"
                  : tool.status === "running"
                    ? "bg-yellow-500"
                    : "bg-muted-foreground";
            const input = formatBytes(tool.input_bytes);
            const output = formatBytes(tool.output_bytes);
            return (
              <div
                key={`${tool.idx}-${tool.end_idx ?? "running"}`}
                className="border-border bg-background/60 flex flex-wrap items-center gap-2 rounded border px-2 py-1"
                data-testid={`${rowId}-trace-tool-${tool.idx}`}
              >
                <span className={`size-2 rounded-full ${statusClass}`} aria-hidden />
                <span className="font-medium">{tool.tool_name ?? "tool"}</span>
                <span className="text-muted-foreground">{formatTime(tool.started_at)}</span>
                {tool.duration_ms !== null ? (
                  <span className="text-muted-foreground">{formatDur(tool.duration_ms)}</span>
                ) : null}
                {input ? <span className="text-muted-foreground">in {input}</span> : null}
                {output ? <span className="text-muted-foreground">out {output}</span> : null}
                {onOpenDebugLog ? (
                  <button
                    type="button"
                    className="border-border hover:bg-muted ml-auto rounded border px-1.5 py-0.5"
                    onClick={(e) => {
                      e.stopPropagation();
                      jumpTo(tool.idx);
                    }}
                  >
                    Jump
                  </button>
                ) : null}
              </div>
            );
          })}
          {internals.tools_truncated ? (
            <p className="text-muted-foreground">
              Tool timeline truncated; jump to the debug log for full detail.
            </p>
          ) : null}
        </div>
      ) : null}

      {internals.model_events.length > 0 ? (
        <div className="space-y-1" data-testid={`${rowId}-trace-model-events`}>
          <p className="text-muted-foreground font-medium">Model messages</p>
          {internals.model_events.slice(0, 20).map((event) => (
            <div
              key={event.idx}
              className="border-border bg-background/60 flex flex-wrap items-center gap-2 rounded border px-2 py-1"
              data-testid={`${rowId}-trace-model-${event.idx}`}
            >
              <span className="font-medium">assistant.message</span>
              <span className="text-muted-foreground">{formatTime(event.timestamp)}</span>
              {event.output_tokens !== null ? (
                <span className="text-muted-foreground">
                  {event.output_tokens.toLocaleString()} tok
                </span>
              ) : null}
              {event.tool_request_count !== null ? (
                <span className="text-muted-foreground">{event.tool_request_count} tool req</span>
              ) : null}
              {onOpenDebugLog ? (
                <button
                  type="button"
                  className="border-border hover:bg-muted ml-auto rounded border px-1.5 py-0.5"
                  onClick={(e) => {
                    e.stopPropagation();
                    jumpTo(event.idx);
                  }}
                >
                  Jump
                </button>
              ) : null}
            </div>
          ))}
          {internals.model_events_truncated ? (
            <p className="text-muted-foreground">
              Model event list truncated; jump to debug log for full detail.
            </p>
          ) : null}
        </div>
      ) : null}

      {summary && !summary.skillCorrelationSupported && summary.uncorrelatedSkillInvocations > 0 ? (
        <div
          className="border-border bg-background/60 rounded border px-2 py-1"
          data-testid={`${rowId}-trace-skills`}
        >
          <span className="font-medium">Skills:</span>{" "}
          <span className="text-muted-foreground">
            {summary.uncorrelatedSkillInvocations} session-level skill load
            {summary.uncorrelatedSkillInvocations === 1 ? "" : "s"} recorded, but current CLI events
            do not expose per-agent skill attribution.
          </span>
          {summary.sessionSkillNames.length > 0 ? (
            <span className="text-muted-foreground">
              {" "}
              Seen: {summary.sessionSkillNames.join(", ")}.
            </span>
          ) : null}
        </div>
      ) : null}

      {firstErrorTool && onOpenDebugLog ? (
        <button
          type="button"
          className="border-border hover:bg-muted rounded border px-2 py-1 text-red-600 dark:text-red-400"
          onClick={(e) => {
            e.stopPropagation();
            jumpTo(firstErrorTool.idx);
          }}
        >
          Jump to first failed tool
        </button>
      ) : null}
    </div>
  );
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
      internalsBySpanId,
      internalsSummary,
      internalsLoading,
      internalsError,
    },
    ref
  ) {
    const [filter, setFilter] = useState<SubagentFilter>("all");
    const [isFlashing, setIsFlashing] = useState(false);
    const [expandedId, setExpandedId] = useState<string | null>(null);
    const flashTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

    // React to flash tick — reset filter to "all" and briefly set data-flash.
    useEffect(() => {
      if (flashTick === 0) return;
      setFilter("all");
      setExpandedId(null);
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
              onClick={() => {
                setFilter(f);
                setExpandedId(null);
              }}
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
                  const isExpanded = expandedId === exec.id;
                  const internalsRow =
                    internalsBySpanId && exec.id ? (internalsBySpanId.get(exec.id) ?? null) : null;

                  const isActive =
                    activeTimeMs !== null &&
                    activeTimeMs !== undefined &&
                    exec.startedAtMs !== null &&
                    exec.endedAtMs !== null &&
                    activeTimeMs >= exec.startedAtMs &&
                    activeTimeMs < exec.endedAtMs;

                  const canSeek = Boolean(onSeekToExecution && exec.startedAtMs !== null);
                  const canExpand = Boolean(
                    internalsBySpanId || internalsSummary || internalsLoading || internalsError
                  );

                  return (
                    <Fragment key={exec.id}>
                      <tr
                        data-testid={rowId}
                        data-status={rowDataStatus(exec)}
                        data-agent={exec.agentName ?? exec.displayName}
                        aria-current={isActive ? "true" : undefined}
                        aria-expanded={canExpand ? isExpanded : undefined}
                        className={[
                          "border-border border-b transition-colors last:border-0",
                          exec.status === "failed" ? "border-l-2 border-l-red-500" : "",
                          isActive ? "bg-primary/5" : "hover:bg-muted/30",
                          canSeek || canExpand ? "cursor-pointer" : "",
                        ]
                          .filter(Boolean)
                          .join(" ")}
                        onClick={() => {
                          if (canExpand)
                            setExpandedId((prev) => (prev === exec.id ? null : exec.id));
                          if (canSeek && exec.startedAtMs !== null)
                            onSeekToExecution!(exec.startedAtMs!);
                        }}
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
                          {(() => {
                            const text = outcomeText(exec);
                            const guidance = outcomeGuidance(exec);
                            if (exec.status === "failed" && exec.errorCategory) {
                              return (
                                <span
                                  data-testid={`${rowId}-outcome-badge`}
                                  data-error-category={exec.errorCategory}
                                  role="status"
                                  aria-label={guidance ?? text}
                                  title={guidance ?? undefined}
                                  className="inline-flex items-center rounded border border-amber-500/60 bg-amber-500/10 px-1.5 py-0.5 text-xs text-amber-700 dark:text-amber-300"
                                >
                                  {text}
                                </span>
                              );
                            }
                            return text;
                          })()}
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
                      {isExpanded ? (
                        <tr key={`${exec.id}-trace`} className="border-border border-b">
                          <td colSpan={9} className="p-0">
                            <SubagentInternalsDetails
                              execution={exec}
                              row={internalsRow}
                              loading={internalsLoading}
                              error={internalsError}
                              summary={internalsSummary}
                              onOpenDebugLog={onOpenDebugLog}
                              rowId={rowId}
                            />
                          </td>
                        </tr>
                      ) : null}
                    </Fragment>
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
