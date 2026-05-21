import "@testing-library/jest-dom";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { Mock } from "vitest";
import type { BrowseDebugEntry, DebugLogResponse } from "@/lib/api/types";
import { DebugLogTab } from "../debug-log-tab";

// ── Mock useDebugLog ──────────────────────────────────────────────────────────

vi.mock("@/lib/api/hooks", () => ({
  useDebugLog: vi.fn(() => ({
    data: null,
    error: null,
    isLoading: false,
  })),
}));

import { useDebugLog } from "@/lib/api/hooks";

// ── Mock lucide-react icons ───────────────────────────────────────────────────

vi.mock("lucide-react", async (importOriginal) => {
  const actual = await importOriginal<typeof import("lucide-react")>();
  return {
    ...actual,
    ChevronDown: () => <span data-testid="icon-chevron-down" />,
    ChevronRight: () => <span data-testid="icon-chevron-right" />,
    Copy: () => <span data-testid="icon-copy" />,
    Check: () => <span data-testid="icon-check" />,
    Filter: () => <span data-testid="icon-filter" />,
  };
});

// ── Fixtures ──────────────────────────────────────────────────────────────────

const HOST = {
  id: "local",
  label: "Local",
  base_url: "",
  token: "",
  cli_kind: "copilot" as const,
  is_default: true,
};

function makeEntry(overrides: Partial<BrowseDebugEntry> = {}): BrowseDebugEntry {
  return {
    idx: 0,
    timestamp: "2024-01-01T12:00:00.000Z",
    kind: "tool_call",
    level: "info",
    source: "operator_console",
    message: "Tool execution started",
    tool_name: "bash",
    duration_ms: 42,
    span_id: "abcdef1234567890",
    parent_span_id: null,
    status: "ok",
    attrs: { cwd: "/tmp" },
    redacted: false,
    ...overrides,
  };
}

function makeResponse(
  events: BrowseDebugEntry[],
  overrides?: Partial<DebugLogResponse>
): DebugLogResponse {
  return {
    schema_version: "1",
    session_id: "sess-1",
    run_id: "run-1",
    total: events.length,
    from: 0,
    limit: 100,
    has_more: false,
    events,
    ...overrides,
  };
}

// ── Tests ─────────────────────────────────────────────────────────────────────

describe("DebugLogTab – no run available", () => {
  it("shows 'No run available' when runId is null", () => {
    render(<DebugLogTab sessionId="sess-1" runId={null} host={HOST} />);
    expect(screen.getByText(/no run available/i)).toBeInTheDocument();
  });

  it("shows 'No run available' when runId is empty string", () => {
    render(<DebugLogTab sessionId="sess-1" runId="" host={HOST} />);
    expect(screen.getByText(/no run available/i)).toBeInTheDocument();
  });
});

describe("DebugLogTab – loading state", () => {
  beforeEach(() => {
    (useDebugLog as Mock).mockReturnValue({ data: null, error: null, isLoading: true });
  });

  it("renders loading message", () => {
    render(<DebugLogTab sessionId="sess-1" runId="run-1" host={HOST} />);
    expect(screen.getByText(/loading debug log/i)).toBeInTheDocument();
  });
});

describe("DebugLogTab – error state", () => {
  beforeEach(() => {
    (useDebugLog as Mock).mockReturnValue({
      data: null,
      error: new Error("Network error"),
      isLoading: false,
    });
  });

  it("renders error banner with message", () => {
    render(<DebugLogTab sessionId="sess-1" runId="run-1" host={HOST} />);
    expect(screen.getByText(/failed to load debug log/i)).toBeInTheDocument();
    expect(screen.getByText(/network error/i)).toBeInTheDocument();
  });
});

describe("DebugLogTab – empty state", () => {
  beforeEach(() => {
    (useDebugLog as Mock).mockReturnValue({
      data: makeResponse([]),
      error: null,
      isLoading: false,
    });
  });

  it("renders empty state", () => {
    render(<DebugLogTab sessionId="sess-1" runId="run-1" host={HOST} />);
    expect(screen.getByText(/no debug log entries/i)).toBeInTheDocument();
  });
});

describe("DebugLogTab – populated state", () => {
  const entries = [
    makeEntry({ idx: 0, kind: "tool_call", source: "operator_console", message: "Alpha" }),
    makeEntry({
      idx: 1,
      kind: "error",
      level: "error",
      source: "hook_runner",
      message: "Beta error",
      status: "error",
      tool_name: null,
    }),
    makeEntry({
      idx: 2,
      kind: "session_start",
      level: "info",
      source: "sk_watch",
      message: "Gamma start",
      tool_name: null,
      status: null,
    }),
  ];

  beforeEach(() => {
    (useDebugLog as Mock).mockReturnValue({
      data: makeResponse(entries),
      error: null,
      isLoading: false,
    });
  });

  it("renders event table with correct number of rows", () => {
    render(<DebugLogTab sessionId="sess-1" runId="run-1" host={HOST} />);
    const table = screen.getByRole("grid", { name: /debug log events/i });
    // thead row + 3 data rows
    expect(within(table).getAllByRole("button")).toHaveLength(3);
  });

  it("renders message text as text — no dangerouslySetInnerHTML", () => {
    render(<DebugLogTab sessionId="sess-1" runId="run-1" host={HOST} />);
    // All three messages appear as plain text
    expect(screen.getByText("Alpha")).toBeInTheDocument();
    expect(screen.getByText("Beta error")).toBeInTheDocument();
    expect(screen.getByText("Gamma start")).toBeInTheDocument();
  });

  it("renders tool name when present", () => {
    render(<DebugLogTab sessionId="sess-1" runId="run-1" host={HOST} />);
    expect(screen.getByText("bash")).toBeInTheDocument();
  });

  it("marks redacted entries with [redacted] label", () => {
    const redactedEntries = [makeEntry({ idx: 0, redacted: true })];
    (useDebugLog as Mock).mockReturnValue({
      data: makeResponse(redactedEntries),
      error: null,
      isLoading: false,
    });
    render(<DebugLogTab sessionId="sess-1" runId="run-1" host={HOST} />);
    expect(screen.getByText("[redacted]")).toBeInTheDocument();
  });
});

describe("DebugLogTab – detail expansion", () => {
  const entry = makeEntry({
    idx: 0,
    kind: "tool_call",
    message: "Tool started",
    attrs: { key: "value", nested: { a: 1 } },
    redacted: false,
  });

  beforeEach(() => {
    (useDebugLog as Mock).mockReturnValue({
      data: makeResponse([entry]),
      error: null,
      isLoading: false,
    });
  });

  it("detail drawer is hidden by default", () => {
    render(<DebugLogTab sessionId="sess-1" runId="run-1" host={HOST} />);
    expect(screen.queryByRole("dialog", { name: /debug event detail/i })).not.toBeInTheDocument();
  });

  it("clicking a row opens the detail drawer", () => {
    render(<DebugLogTab sessionId="sess-1" runId="run-1" host={HOST} />);
    const row = screen.getByRole("button", {
      name: /debug event 0: tool_call from operator_console/i,
    });
    fireEvent.click(row);
    expect(screen.getByRole("dialog", { name: /debug event detail/i })).toBeInTheDocument();
  });

  it("detail drawer shows all entry fields as text", () => {
    render(<DebugLogTab sessionId="sess-1" runId="run-1" host={HOST} />);
    fireEvent.click(
      screen.getByRole("button", { name: /debug event 0: tool_call from operator_console/i })
    );
    const drawer = screen.getByRole("dialog", { name: /debug event detail/i });
    expect(within(drawer).getByText("tool_call")).toBeInTheDocument();
    expect(within(drawer).getByText("operator_console")).toBeInTheDocument();
    // message full text
    expect(within(drawer).getByText("Tool started")).toBeInTheDocument();
  });

  it("detail drawer renders attrs as pre-formatted JSON text — no raw HTML", () => {
    render(<DebugLogTab sessionId="sess-1" runId="run-1" host={HOST} />);
    fireEvent.click(
      screen.getByRole("button", { name: /debug event 0: tool_call from operator_console/i })
    );
    const drawer = screen.getByRole("dialog", { name: /debug event detail/i });
    const preEl = drawer.querySelector("pre");
    expect(preEl).toBeInTheDocument();
    // JSON text contains the key
    expect(preEl?.textContent).toContain('"key"');
    expect(preEl?.textContent).toContain('"value"');
    // No innerHTML injection — textContent equals JSON.stringify output
    expect(preEl?.innerHTML).not.toContain("<script");
  });

  it("clicking row again closes the detail drawer (toggle)", () => {
    render(<DebugLogTab sessionId="sess-1" runId="run-1" host={HOST} />);
    const row = screen.getByRole("button", {
      name: /debug event 0: tool_call from operator_console/i,
    });
    fireEvent.click(row);
    fireEvent.click(row);
    expect(screen.queryByRole("dialog", { name: /debug event detail/i })).not.toBeInTheDocument();
  });

  it("close button in detail drawer dismisses the drawer", () => {
    render(<DebugLogTab sessionId="sess-1" runId="run-1" host={HOST} />);
    fireEvent.click(
      screen.getByRole("button", { name: /debug event 0: tool_call from operator_console/i })
    );
    const closeBtn = screen.getByRole("button", { name: /close detail panel/i });
    fireEvent.click(closeBtn);
    expect(screen.queryByRole("dialog", { name: /debug event detail/i })).not.toBeInTheDocument();
  });
});

describe("DebugLogTab – filter behavior", () => {
  const entries = [
    makeEntry({ idx: 0, kind: "tool_call", message: "Alpha bash tool", tool_name: "bash" }),
    makeEntry({
      idx: 1,
      kind: "error",
      message: "Beta error happened",
      tool_name: null,
      level: "error",
      status: "error",
    }),
    makeEntry({
      idx: 2,
      kind: "session_start",
      message: "Session began",
      source: "sk_watch",
      tool_name: null,
    }),
  ];

  beforeEach(() => {
    (useDebugLog as Mock).mockReturnValue({
      data: makeResponse(entries),
      error: null,
      isLoading: false,
    });
  });

  it("text filter reduces visible rows (substring match, no regex)", () => {
    render(<DebugLogTab sessionId="sess-1" runId="run-1" host={HOST} />);
    const textInput = screen.getByRole("textbox", { name: /filter by text/i });
    fireEvent.change(textInput, { target: { value: "bash" } });
    // Only row with "bash" in message/tool_name should remain
    expect(screen.getByText("Alpha bash tool")).toBeInTheDocument();
    expect(screen.queryByText("Beta error happened")).not.toBeInTheDocument();
    expect(screen.queryByText("Session began")).not.toBeInTheDocument();
  });

  it("text filter is case-insensitive", () => {
    render(<DebugLogTab sessionId="sess-1" runId="run-1" host={HOST} />);
    const textInput = screen.getByRole("textbox", { name: /filter by text/i });
    fireEvent.change(textInput, { target: { value: "BASH" } });
    expect(screen.getByText("Alpha bash tool")).toBeInTheDocument();
    expect(screen.queryByText("Beta error happened")).not.toBeInTheDocument();
  });

  it("shows 'No events match' message when all filtered out", () => {
    render(<DebugLogTab sessionId="sess-1" runId="run-1" host={HOST} />);
    const textInput = screen.getByRole("textbox", { name: /filter by text/i });
    fireEvent.change(textInput, { target: { value: "zzz-nomatch-zzz" } });
    expect(screen.getByText(/no events match/i)).toBeInTheDocument();
  });

  it("text filter uses substring, not regex — special chars do not throw", () => {
    render(<DebugLogTab sessionId="sess-1" runId="run-1" host={HOST} />);
    const textInput = screen.getByRole("textbox", { name: /filter by text/i });
    // This would throw if the input were treated as a regex
    expect(() => {
      fireEvent.change(textInput, { target: { value: "(((" } });
    }).not.toThrow();
  });

  it("filter toolbar shows result/total count", () => {
    render(<DebugLogTab sessionId="sess-1" runId="run-1" host={HOST} />);
    // All 3 visible: "3 of 3"
    expect(screen.getByText(/3 of 3/)).toBeInTheDocument();
  });

  it("result count updates after text filter", () => {
    render(<DebugLogTab sessionId="sess-1" runId="run-1" host={HOST} />);
    const textInput = screen.getByRole("textbox", { name: /filter by text/i });
    fireEvent.change(textInput, { target: { value: "bash" } });
    expect(screen.getByText(/1 of 3/)).toBeInTheDocument();
  });

  it("filters reset the detail drawer", () => {
    render(<DebugLogTab sessionId="sess-1" runId="run-1" host={HOST} />);
    // Open drawer for first row
    fireEvent.click(
      screen.getByRole("button", { name: /debug event 0: tool_call from operator_console/i })
    );
    expect(screen.getByRole("dialog", { name: /debug event detail/i })).toBeInTheDocument();
    // Change filter
    const textInput = screen.getByRole("textbox", { name: /filter by text/i });
    fireEvent.change(textInput, { target: { value: "any" } });
    expect(screen.queryByRole("dialog", { name: /debug event detail/i })).not.toBeInTheDocument();
  });
});

describe("DebugLogTab – pagination", () => {
  it("shows Previous/Next when has_more=true", () => {
    (useDebugLog as Mock).mockReturnValue({
      data: makeResponse([makeEntry({ idx: 0 })], { has_more: true, total: 200 }),
      error: null,
      isLoading: false,
    });
    render(<DebugLogTab sessionId="sess-1" runId="run-1" host={HOST} />);
    expect(screen.getByRole("button", { name: /next/i })).toBeInTheDocument();
    // No Previous on page 0
    expect(screen.queryByRole("button", { name: /previous/i })).not.toBeInTheDocument();
  });

  it("does not show pagination controls when all events on one page", () => {
    (useDebugLog as Mock).mockReturnValue({
      data: makeResponse([makeEntry({ idx: 0 })], { has_more: false, total: 1 }),
      error: null,
      isLoading: false,
    });
    render(<DebugLogTab sessionId="sess-1" runId="run-1" host={HOST} />);
    expect(screen.queryByRole("button", { name: /next/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /previous/i })).not.toBeInTheDocument();
  });
});
