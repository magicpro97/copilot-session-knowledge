/**
 * Component tests for SubagentActivityPanel.
 *
 * Data source: derived `SubagentExecution[]` and `SubagentActivitySummary` from
 * flight-recorder helpers — NOT raw API entries.
 */
import "@testing-library/jest-dom";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import {
  deriveSubagentActivitySummary,
  deriveSubagentExecutions,
  type SubagentExecution,
} from "@/lib/flight-recorder";
import type { SubagentActivityEntry, SubagentActivityResponse } from "@/lib/api/types";
import { SubagentActivityPanel } from "@/components/data/subagent-activity-panel";

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

/** Convenience: build executions + summary from entries. */
function fromEntries(
  entries: SubagentActivityEntry[],
  responseOverrides: Partial<Omit<SubagentActivityResponse, "entries" | "schema_version">> = {}
) {
  const response = makeResponse(entries, responseOverrides);
  return {
    executions: deriveSubagentExecutions(response),
    summary: deriveSubagentActivitySummary(response),
  };
}

// ── Loading state ─────────────────────────────────────────────────────────────

describe("SubagentActivityPanel — loading state", () => {
  it("renders subagent-loading indicator", () => {
    const { executions, summary } = fromEntries([]);
    render(
      <SubagentActivityPanel
        executions={executions}
        summary={summary}
        loading
        onOpenDebugLog={vi.fn()}
      />
    );
    expect(screen.getByTestId("subagent-activity-panel")).toBeInTheDocument();
    expect(screen.getByTestId("subagent-loading")).toBeInTheDocument();
    expect(screen.queryByTestId("subagent-empty")).not.toBeInTheDocument();
    expect(screen.queryByTestId("subagent-error")).not.toBeInTheDocument();
  });

  it("loading takes precedence over error prop", () => {
    const { executions, summary } = fromEntries([]);
    render(
      <SubagentActivityPanel
        executions={executions}
        summary={summary}
        loading
        error={new Error("oops")}
      />
    );
    expect(screen.getByTestId("subagent-loading")).toBeInTheDocument();
    expect(screen.queryByTestId("subagent-error")).not.toBeInTheDocument();
  });
});

// ── Error state ───────────────────────────────────────────────────────────────

describe("SubagentActivityPanel — error state", () => {
  it("renders subagent-error when error is set and not loading", () => {
    const { executions, summary } = fromEntries([]);
    render(
      <SubagentActivityPanel
        executions={executions}
        summary={summary}
        error={new Error("fetch failed")}
      />
    );
    expect(screen.getByTestId("subagent-activity-panel")).toBeInTheDocument();
    expect(screen.getByTestId("subagent-error")).toBeInTheDocument();
    expect(screen.queryByTestId("subagent-loading")).not.toBeInTheDocument();
    expect(screen.queryByTestId("subagent-empty")).not.toBeInTheDocument();
  });

  it("subagent-error does not render raw error message text", () => {
    const rawError = "Bearer abc.def.ghi — raw token leaked";
    const { executions, summary } = fromEntries([]);
    render(
      <SubagentActivityPanel
        executions={executions}
        summary={summary}
        error={new Error(rawError)}
      />
    );
    // The panel must not render the raw error message.
    expect(screen.queryByText(rawError)).not.toBeInTheDocument();
    expect(screen.getByTestId("subagent-error")).toBeInTheDocument();
  });
});

// ── Empty state ───────────────────────────────────────────────────────────────

describe("SubagentActivityPanel — empty state", () => {
  it("renders subagent-empty when executions array is empty", () => {
    const { executions, summary } = fromEntries([]);
    render(<SubagentActivityPanel executions={executions} summary={summary} />);
    expect(screen.getByTestId("subagent-activity-panel")).toBeInTheDocument();
    expect(screen.getByTestId("subagent-empty")).toBeInTheDocument();
    expect(screen.queryByTestId("subagent-loading")).not.toBeInTheDocument();
    expect(screen.queryByTestId("subagent-error")).not.toBeInTheDocument();
    expect(screen.queryByTestId("subagent-activity-table")).not.toBeInTheDocument();
  });

  it("has correct role and aria-label on root element", () => {
    const { executions, summary } = fromEntries([]);
    render(<SubagentActivityPanel executions={executions} summary={summary} />);
    const panel = screen.getByTestId("subagent-activity-panel");
    expect(panel).toHaveAttribute("role", "region");
    expect(panel).toHaveAttribute("aria-label", "Sub-agent activity");
  });
});

// ── Completed row ─────────────────────────────────────────────────────────────

describe("SubagentActivityPanel — completed execution row", () => {
  it("renders row with correct testids and content", () => {
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
      start_idx: 10,
      end_idx: 25,
    });
    const { executions, summary } = fromEntries([entry]);
    render(
      <SubagentActivityPanel executions={executions} summary={summary} onOpenDebugLog={vi.fn()} />
    );

    const row = screen.getByTestId("subagent-activity-row-span-abc");
    expect(row).toBeInTheDocument();
    expect(row).toHaveAttribute("data-status", "ok");
    expect(row).toHaveAttribute("data-agent", "coder");

    // Cell content checks
    expect(screen.getByTestId("subagent-activity-row-span-abc-agent")).toHaveTextContent(
      "Coder Agent"
    );
    expect(screen.getByTestId("subagent-activity-row-span-abc-model")).toHaveTextContent(
      "claude-3-sonnet"
    );
    expect(screen.getByTestId("subagent-activity-row-span-abc-tools")).toHaveTextContent("5");
    expect(screen.getByTestId("subagent-activity-row-span-abc-tokens")).toHaveTextContent("2,000");
    expect(screen.getByTestId("subagent-activity-row-span-abc-duration")).toHaveTextContent(
      "10.0s"
    );
    expect(screen.getByTestId("subagent-activity-row-span-abc-outcome")).toHaveTextContent(
      "Completed"
    );
  });

  it("renders summary-total with executions count", () => {
    const { executions, summary } = fromEntries([makeEntry()]);
    render(<SubagentActivityPanel executions={executions} summary={summary} />);
    expect(screen.getByTestId("subagent-summary-total")).toHaveTextContent("1 run");
  });

  it("renders plural for multiple runs", () => {
    const { executions, summary } = fromEntries([makeEntry(), makeEntry({ span_id: "span-002" })]);
    render(<SubagentActivityPanel executions={executions} summary={summary} />);
    expect(screen.getByTestId("subagent-summary-total")).toHaveTextContent("2 runs");
  });
});

// ── Failed row ────────────────────────────────────────────────────────────────

describe("SubagentActivityPanel — failed execution row", () => {
  it("has data-status=error and shows Failed: rate_limited in outcome cell", () => {
    const entry = makeEntry({
      span_id: "span-fail",
      status: "failed",
      error_category: "rate_limited",
      // error_preview is redacted server-side; panel must not render it
      error_preview: "[REDACTED]",
    });
    const { executions, summary } = fromEntries([entry]);
    render(
      <SubagentActivityPanel executions={executions} summary={summary} onOpenDebugLog={vi.fn()} />
    );

    const row = screen.getByTestId("subagent-activity-row-span-fail");
    expect(row).toHaveAttribute("data-status", "error");
    expect(screen.getByTestId("subagent-activity-row-span-fail-outcome")).toHaveTextContent(
      "Failed: rate_limited"
    );
    // error_preview must NOT be rendered
    expect(screen.queryByText("[REDACTED]")).not.toBeInTheDocument();
  });

  it("shows summary-failed count when failures exist", () => {
    const entry = makeEntry({ span_id: "span-f", status: "failed", error_category: "timeout" });
    const { executions, summary } = fromEntries([entry]);
    render(<SubagentActivityPanel executions={executions} summary={summary} />);
    expect(screen.getByTestId("subagent-summary-failed")).toBeInTheDocument();
    expect(screen.getByTestId("subagent-summary-failed")).toHaveTextContent("1 failed");
  });

  it("hides summary-failed when no failures", () => {
    const { executions, summary } = fromEntries([makeEntry({ status: "completed" })]);
    render(<SubagentActivityPanel executions={executions} summary={summary} />);
    expect(screen.queryByTestId("subagent-summary-failed")).not.toBeInTheDocument();
  });

  it("shows Failed without category when error_category is null", () => {
    const entry = makeEntry({ span_id: "span-fnocat", status: "failed", error_category: null });
    const { executions, summary } = fromEntries([entry]);
    render(<SubagentActivityPanel executions={executions} summary={summary} />);
    expect(screen.getByTestId("subagent-activity-row-span-fnocat-outcome")).toHaveTextContent(
      "Failed"
    );
  });
});

// ── Running row ───────────────────────────────────────────────────────────────

describe("SubagentActivityPanel — running execution row", () => {
  it("has data-status=running and shows Running... in outcome cell", () => {
    const entry = makeEntry({
      span_id: "span-run",
      status: "running",
      ended_at: null,
      duration_ms: null,
      error_category: null,
    });
    const { executions, summary } = fromEntries([entry]);
    render(
      <SubagentActivityPanel executions={executions} summary={summary} onOpenDebugLog={vi.fn()} />
    );

    const row = screen.getByTestId("subagent-activity-row-span-run");
    expect(row).toHaveAttribute("data-status", "running");
    expect(screen.getByTestId("subagent-activity-row-span-run-outcome")).toHaveTextContent(
      "Running..."
    );
  });

  it("shows summary-running count when running executions exist", () => {
    const entry = makeEntry({ span_id: "span-r", status: "running" });
    const { executions, summary } = fromEntries([entry]);
    render(<SubagentActivityPanel executions={executions} summary={summary} />);
    expect(screen.getByTestId("subagent-summary-running")).toBeInTheDocument();
  });

  it("hides summary-running when no running executions", () => {
    const { executions, summary } = fromEntries([makeEntry({ status: "completed" })]);
    render(<SubagentActivityPanel executions={executions} summary={summary} />);
    expect(screen.queryByTestId("subagent-summary-running")).not.toBeInTheDocument();
  });
});

// ── Null model cell ───────────────────────────────────────────────────────────

describe("SubagentActivityPanel — null model cell", () => {
  it("renders empty string in model cell when model is null (not 'null')", () => {
    const entry = makeEntry({ span_id: "span-nomodel", model: null });
    const { executions, summary } = fromEntries([entry]);
    render(<SubagentActivityPanel executions={executions} summary={summary} />);
    const modelCell = screen.getByTestId("subagent-activity-row-span-nomodel-model");
    expect(modelCell).toBeInTheDocument();
    expect(modelCell.textContent).toBe("");
    expect(modelCell.textContent).not.toBe("null");
    expect(modelCell.textContent).not.toBe("undefined");
  });

  it("renders empty string for tools and tokens when null", () => {
    const entry = makeEntry({
      span_id: "span-nullmetrics",
      total_tool_calls: null,
      total_tokens: null,
    });
    const { executions, summary } = fromEntries([entry]);
    render(<SubagentActivityPanel executions={executions} summary={summary} />);
    expect(screen.getByTestId("subagent-activity-row-span-nullmetrics-tools").textContent).toBe("");
    expect(screen.getByTestId("subagent-activity-row-span-nullmetrics-tokens").textContent).toBe(
      ""
    );
  });
});

// ── Jump callback ─────────────────────────────────────────────────────────────

describe("SubagentActivityPanel — jump callback", () => {
  it("jump button calls onOpenDebugLog with startIdx when available", () => {
    const onJump = vi.fn();
    const entry = makeEntry({ span_id: "span-jump", start_idx: 42, end_idx: 99 });
    const { executions, summary } = fromEntries([entry]);
    render(
      <SubagentActivityPanel executions={executions} summary={summary} onOpenDebugLog={onJump} />
    );
    fireEvent.click(
      screen.getByTestId("subagent-activity-row-span-jump-jump").querySelector("button")!
    );
    expect(onJump).toHaveBeenCalledWith(42);
  });

  it("jump button calls onOpenDebugLog with endIdx when startIdx is null", () => {
    const onJump = vi.fn();
    const entry = makeEntry({ span_id: "span-endonly", start_idx: null, end_idx: 77 });
    const { executions, summary } = fromEntries([entry]);
    render(
      <SubagentActivityPanel executions={executions} summary={summary} onOpenDebugLog={onJump} />
    );
    // For entries with start_idx null, the id uses end_idx in the id name
    const jumpCell = screen.getByTestId(/subagent-activity-row.*-jump$/);
    fireEvent.click(jumpCell.querySelector("button")!);
    expect(onJump).toHaveBeenCalledWith(77);
  });

  it("jump cell has no button when both startIdx and endIdx are null", () => {
    const entry = makeEntry({ span_id: "span-nojump", start_idx: null, end_idx: null });
    const { executions, summary } = fromEntries([entry]);
    render(
      <SubagentActivityPanel executions={executions} summary={summary} onOpenDebugLog={vi.fn()} />
    );
    const jumpCell = screen.getByTestId("subagent-activity-row-span-nojump-jump");
    expect(jumpCell.querySelector("button")).toBeNull();
  });

  it("jump cell has no button when onOpenDebugLog is not provided", () => {
    const entry = makeEntry({ span_id: "span-noop", start_idx: 10, end_idx: 20 });
    const { executions, summary } = fromEntries([entry]);
    render(<SubagentActivityPanel executions={executions} summary={summary} />);
    const jumpCell = screen.getByTestId("subagent-activity-row-span-noop-jump");
    expect(jumpCell.querySelector("button")).toBeNull();
  });

  it("jump button stopPropagation — does not call onSeekToExecution", () => {
    const onJump = vi.fn();
    const onSeek = vi.fn();
    const entry = makeEntry({ span_id: "span-stoprop", start_idx: 10 });
    const { executions, summary } = fromEntries([entry]);
    render(
      <SubagentActivityPanel
        executions={executions}
        summary={summary}
        onOpenDebugLog={onJump}
        onSeekToExecution={onSeek}
      />
    );
    fireEvent.click(
      screen.getByTestId("subagent-activity-row-span-stoprop-jump").querySelector("button")!
    );
    expect(onJump).toHaveBeenCalled();
    // seek must NOT be called (stopPropagation prevents row click)
    expect(onSeek).not.toHaveBeenCalled();
  });
});

// ── Filter behavior ───────────────────────────────────────────────────────────

describe("SubagentActivityPanel — filter behavior", () => {
  function renderWithMixed() {
    const completed = makeEntry({ span_id: "span-c", status: "completed" });
    const failed = makeEntry({
      span_id: "span-f",
      status: "failed",
      error_category: "api_error",
    });
    const running = makeEntry({ span_id: "span-r", status: "running" });
    const { executions, summary } = fromEntries([completed, failed, running]);
    render(<SubagentActivityPanel executions={executions} summary={summary} />);
    return { completedId: "span-c", failedId: "span-f", runningId: "span-r" };
  }

  it("default filter 'all' shows all rows", () => {
    const { completedId, failedId, runningId } = renderWithMixed();
    expect(screen.getByTestId(`subagent-activity-row-${completedId}`)).toBeInTheDocument();
    expect(screen.getByTestId(`subagent-activity-row-${failedId}`)).toBeInTheDocument();
    expect(screen.getByTestId(`subagent-activity-row-${runningId}`)).toBeInTheDocument();
  });

  it("filter 'failed' hides completed and running rows", () => {
    const { completedId, failedId, runningId } = renderWithMixed();
    fireEvent.click(screen.getByTestId("subagent-filter-failed"));
    expect(screen.queryByTestId(`subagent-activity-row-${completedId}`)).not.toBeInTheDocument();
    expect(screen.getByTestId(`subagent-activity-row-${failedId}`)).toBeInTheDocument();
    expect(screen.queryByTestId(`subagent-activity-row-${runningId}`)).not.toBeInTheDocument();
  });

  it("filter 'running' hides completed and failed rows", () => {
    const { completedId, failedId, runningId } = renderWithMixed();
    fireEvent.click(screen.getByTestId("subagent-filter-running"));
    expect(screen.queryByTestId(`subagent-activity-row-${completedId}`)).not.toBeInTheDocument();
    expect(screen.queryByTestId(`subagent-activity-row-${failedId}`)).not.toBeInTheDocument();
    expect(screen.getByTestId(`subagent-activity-row-${runningId}`)).toBeInTheDocument();
  });

  it("filter pill has aria-pressed=true when active", () => {
    renderWithMixed();
    expect(screen.getByTestId("subagent-filter-all")).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByTestId("subagent-filter-failed")).toHaveAttribute("aria-pressed", "false");
    fireEvent.click(screen.getByTestId("subagent-filter-failed"));
    expect(screen.getByTestId("subagent-filter-all")).toHaveAttribute("aria-pressed", "false");
    expect(screen.getByTestId("subagent-filter-failed")).toHaveAttribute("aria-pressed", "true");
  });

  it("filter with no matches shows subagent-empty", () => {
    const { executions, summary } = fromEntries([makeEntry({ status: "completed" })]);
    render(<SubagentActivityPanel executions={executions} summary={summary} />);
    fireEvent.click(screen.getByTestId("subagent-filter-failed"));
    expect(screen.getByTestId("subagent-empty")).toBeInTheDocument();
    expect(screen.queryByTestId("subagent-activity-table")).not.toBeInTheDocument();
  });
});

// ── Truncated banner ──────────────────────────────────────────────────────────

describe("SubagentActivityPanel — truncated banner", () => {
  it("renders subagent-summary-truncated when response is capped", () => {
    const entry = makeEntry();
    const { executions, summary } = fromEntries([entry], {
      truncated: true,
      total_subagents_seen: 1500,
      cap: 1000,
      returned: 1,
    });
    render(<SubagentActivityPanel executions={executions} summary={summary} />);
    const banner = screen.getByTestId("subagent-summary-truncated");
    expect(banner).toBeInTheDocument();
    expect(banner).toHaveTextContent(/1,000/);
    expect(banner).toHaveTextContent(/1,500/);
  });

  it("does not render subagent-summary-truncated when not capped", () => {
    const { executions, summary } = fromEntries([makeEntry()]);
    render(<SubagentActivityPanel executions={executions} summary={summary} />);
    expect(screen.queryByTestId("subagent-summary-truncated")).not.toBeInTheDocument();
  });
});

// ── Flash behavior ────────────────────────────────────────────────────────────

describe("SubagentActivityPanel — flash behavior", () => {
  it("sets data-flash=true when flashTick increments above 0", async () => {
    vi.useFakeTimers();
    const { executions, summary } = fromEntries([makeEntry()]);
    const { rerender } = render(
      <SubagentActivityPanel executions={executions} summary={summary} flashTick={0} />
    );
    expect(screen.getByTestId("subagent-activity-panel")).not.toHaveAttribute("data-flash");

    rerender(<SubagentActivityPanel executions={executions} summary={summary} flashTick={1} />);
    expect(screen.getByTestId("subagent-activity-panel")).toHaveAttribute("data-flash", "true");

    // After 1500ms the flash should clear
    act(() => {
      vi.advanceTimersByTime(1500);
    });
    expect(screen.getByTestId("subagent-activity-panel")).not.toHaveAttribute("data-flash");
    vi.useRealTimers();
  });

  it("resets filter to all when flashTick increments", () => {
    const { executions, summary } = fromEntries([
      makeEntry({ span_id: "s1", status: "completed" }),
      makeEntry({ span_id: "s2", status: "failed", error_category: "timeout" }),
    ]);
    const { rerender } = render(
      <SubagentActivityPanel executions={executions} summary={summary} flashTick={0} />
    );
    // Set filter to failed
    fireEvent.click(screen.getByTestId("subagent-filter-failed"));
    expect(screen.getByTestId("subagent-filter-failed")).toHaveAttribute("aria-pressed", "true");

    // Flash tick resets filter to all
    rerender(<SubagentActivityPanel executions={executions} summary={summary} flashTick={1} />);
    expect(screen.getByTestId("subagent-filter-all")).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByTestId("subagent-activity-row-s1")).toBeInTheDocument();
    expect(screen.getByTestId("subagent-activity-row-s2")).toBeInTheDocument();
  });
});

// ── Seek callback ─────────────────────────────────────────────────────────────

describe("SubagentActivityPanel — seek callback", () => {
  it("calls onSeekToExecution with startedAtMs when row body is clicked", () => {
    const onSeek = vi.fn();
    const entry = makeEntry({
      span_id: "span-seek",
      started_at: "2024-01-01T10:00:00.000Z",
    });
    const { executions, summary } = fromEntries([entry]);
    render(
      <SubagentActivityPanel executions={executions} summary={summary} onSeekToExecution={onSeek} />
    );
    fireEvent.click(screen.getByTestId("subagent-activity-row-span-seek"));
    expect(onSeek).toHaveBeenCalledWith(Date.parse("2024-01-01T10:00:00.000Z"));
  });

  it("does not call onSeekToExecution when it is not provided", () => {
    const entry = makeEntry({ span_id: "span-noseek" });
    const { executions, summary } = fromEntries([entry]);
    // Should not throw when clicking row without onSeekToExecution
    expect(() => {
      render(<SubagentActivityPanel executions={executions} summary={summary} />);
      fireEvent.click(screen.getByTestId("subagent-activity-row-span-noseek"));
    }).not.toThrow();
  });
});

// ── Active playhead highlighting ──────────────────────────────────────────────

describe("SubagentActivityPanel — activeTimeMs highlighting", () => {
  it("sets aria-current=true on row whose time window contains activeTimeMs", () => {
    const entry = makeEntry({
      span_id: "span-active",
      started_at: "2024-01-01T10:00:00.000Z",
      ended_at: "2024-01-01T10:00:10.000Z",
      duration_ms: 10000,
    });
    const { executions, summary } = fromEntries([entry]);
    const activeMs = Date.parse("2024-01-01T10:00:05.000Z");
    render(
      <SubagentActivityPanel executions={executions} summary={summary} activeTimeMs={activeMs} />
    );
    expect(screen.getByTestId("subagent-activity-row-span-active")).toHaveAttribute(
      "aria-current",
      "true"
    );
  });

  it("does not set aria-current on row outside time window", () => {
    const entry = makeEntry({
      span_id: "span-inactive",
      started_at: "2024-01-01T10:00:00.000Z",
      ended_at: "2024-01-01T10:00:10.000Z",
    });
    const { executions, summary } = fromEntries([entry]);
    const activeMs = Date.parse("2024-01-01T10:01:00.000Z"); // outside window
    render(
      <SubagentActivityPanel executions={executions} summary={summary} activeTimeMs={activeMs} />
    );
    expect(screen.getByTestId("subagent-activity-row-span-inactive")).not.toHaveAttribute(
      "aria-current"
    );
  });
});

// ── Redacted banner ───────────────────────────────────────────────────────────

describe("SubagentActivityPanel — redacted entry", () => {
  it("renders row normally for redacted=true entry (redaction is server-side, no special client treatment required)", () => {
    const entry = makeEntry({ span_id: "span-redacted", redacted: true });
    const { executions, summary } = fromEntries([entry]);
    render(<SubagentActivityPanel executions={executions} summary={summary} />);
    expect(screen.getByTestId("subagent-activity-row-span-redacted")).toBeInTheDocument();
  });
});

// ── Multiple rows ─────────────────────────────────────────────────────────────

describe("SubagentActivityPanel — multiple rows", () => {
  it("renders one row per execution", () => {
    const entries = [
      makeEntry({ span_id: "s1" }),
      makeEntry({ span_id: "s2" }),
      makeEntry({ span_id: "s3" }),
    ];
    const { executions, summary } = fromEntries(entries);
    render(<SubagentActivityPanel executions={executions} summary={summary} />);
    expect(screen.getByTestId("subagent-summary-total")).toHaveTextContent("3 runs");
    expect(screen.getByTestId("subagent-activity-row-s1")).toBeInTheDocument();
    expect(screen.getByTestId("subagent-activity-row-s2")).toBeInTheDocument();
    expect(screen.getByTestId("subagent-activity-row-s3")).toBeInTheDocument();
  });
});

// ── SubagentExecution type compatibility ──────────────────────────────────────

describe("SubagentActivityPanel — direct SubagentExecution props", () => {
  it("accepts SubagentExecution[] directly without re-deriving", () => {
    const exec: SubagentExecution = {
      id: "direct-id",
      agentName: "direct-agent",
      agentDisplayName: "Direct Agent",
      displayName: "Direct Agent",
      model: "gpt-4",
      status: "completed",
      errorCategory: null,
      startedAt: "2024-01-01T10:00:00.000Z",
      endedAt: "2024-01-01T10:00:05.000Z",
      startedAtMs: Date.parse("2024-01-01T10:00:00.000Z"),
      endedAtMs: Date.parse("2024-01-01T10:00:05.000Z"),
      durationMs: 5000,
      totalToolCalls: 2,
      totalTokens: 500,
      startIdx: 5,
      endIdx: 10,
      redacted: false,
    };
    const summary = {
      totalSeen: 1,
      returned: 1,
      cap: 1000,
      truncated: false,
      droppedPendingStarts: 0,
    };
    render(
      <SubagentActivityPanel executions={[exec]} summary={summary} onOpenDebugLog={vi.fn()} />
    );
    expect(screen.getByTestId("subagent-activity-row-direct-id")).toBeInTheDocument();
    expect(screen.getByTestId("subagent-activity-row-direct-id-agent")).toHaveTextContent(
      "Direct Agent"
    );
  });
});
