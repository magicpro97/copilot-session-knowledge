import "@testing-library/jest-dom";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { Mock } from "vitest";
import type { BrowseDebugEntry, DebugLogResponse, SessionDebugLogResponse } from "@/lib/api/types";
import { DebugLogTab } from "../debug-log-tab";

// ── Mock useDebugLog and useSessionDebugLog ───────────────────────────────────

vi.mock("@/lib/api/hooks", () => ({
  useDebugLog: vi.fn(() => ({
    data: null,
    error: null,
    isLoading: false,
  })),
  useSessionDebugLog: vi.fn(() => ({
    data: null,
    error: null,
    isLoading: false,
  })),
}));

import { useDebugLog, useSessionDebugLog } from "@/lib/api/hooks";

// Reset both mocks to their default no-data state before every test so that
// mock state set inside individual `it` blocks does not leak to later tests.
beforeEach(() => {
  (useDebugLog as Mock).mockReturnValue({ data: null, error: null, isLoading: false });
  (useSessionDebugLog as Mock).mockReturnValue({ data: null, error: null, isLoading: false });
});

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
    Loader2: () => <span data-testid="icon-loader2" />,
    Terminal: () => <span data-testid="icon-terminal" />,
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

function makeSessionResponse(
  entries: BrowseDebugEntry[],
  overrides?: Partial<SessionDebugLogResponse>
): SessionDebugLogResponse {
  return {
    schema_version: "1",
    session_id: "sess-1",
    from: 0,
    limit: 100,
    total: entries.length,
    has_more: false,
    entries,
    ...overrides,
  };
}

// ── Tests ─────────────────────────────────────────────────────────────────────

describe("DebugLogTab – no run available (session-scoped path)", () => {
  it("shows 'No debug events found' when runId is null and session API has no data", () => {
    render(<DebugLogTab sessionId="sess-1" runId={null} host={HOST} />);
    expect(screen.getByText(/no debug events found/i)).toBeInTheDocument();
  });

  it("shows 'No debug events found' when runId is empty string", () => {
    render(<DebugLogTab sessionId="sess-1" runId="" host={HOST} />);
    expect(screen.getByText(/no debug events found/i)).toBeInTheDocument();
  });

  it("useSessionDebugLog is called with enabled=true when runId is null", () => {
    render(<DebugLogTab sessionId="sess-1" runId={null} host={HOST} />);
    expect((useSessionDebugLog as Mock).mock.calls.at(-1)?.[2]).toBe(true);
  });

  it("useSessionDebugLog is called with enabled=false when runId is provided (operator path)", () => {
    render(<DebugLogTab sessionId="sess-1" runId="run-1" host={HOST} />);
    expect((useSessionDebugLog as Mock).mock.calls.at(-1)?.[2]).toBe(false);
  });

  it("useDebugLog is called with enabled=false when runId is null (session path)", () => {
    render(<DebugLogTab sessionId="sess-1" runId={null} host={HOST} />);
    // operatorQuery enabled = hasRunId && Boolean(sessionId) = false
    expect((useDebugLog as Mock).mock.calls.at(-1)?.[3]).toBe(false);
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

  it("renders loading message while resolving the latest operator run", () => {
    render(<DebugLogTab sessionId="sess-1" runId={null} runsLoading host={HOST} />);
    expect(screen.getByText(/loading debug log/i)).toBeInTheDocument();
    expect(screen.queryByText(/no debug events found/i)).not.toBeInTheDocument();
  });

  it("renders loading message for session-scoped path when runId is null", () => {
    (useSessionDebugLog as Mock).mockReturnValue({ data: null, error: null, isLoading: true });
    render(<DebugLogTab sessionId="sess-1" runId={null} host={HOST} />);
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
    // thead row + 3 data rows (data rows use role="row" with aria-label)
    const dataRows = within(table).getAllByRole("row", { name: /debug event/i });
    expect(dataRows).toHaveLength(3);
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
    const row = screen.getByRole("row", {
      name: /debug event 0: tool_call from operator_console/i,
    });
    fireEvent.click(row);
    expect(screen.getByRole("dialog", { name: /debug event detail/i })).toBeInTheDocument();
  });

  it("detail drawer shows all entry fields as text", () => {
    render(<DebugLogTab sessionId="sess-1" runId="run-1" host={HOST} />);
    fireEvent.click(
      screen.getByRole("row", { name: /debug event 0: tool_call from operator_console/i })
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
      screen.getByRole("row", { name: /debug event 0: tool_call from operator_console/i })
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
    const row = screen.getByRole("row", {
      name: /debug event 0: tool_call from operator_console/i,
    });
    fireEvent.click(row);
    fireEvent.click(row);
    expect(screen.queryByRole("dialog", { name: /debug event detail/i })).not.toBeInTheDocument();
  });

  it("close button in detail drawer dismisses the drawer", () => {
    render(<DebugLogTab sessionId="sess-1" runId="run-1" host={HOST} />);
    fireEvent.click(
      screen.getByRole("row", { name: /debug event 0: tool_call from operator_console/i })
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
      screen.getByRole("row", { name: /debug event 0: tool_call from operator_console/i })
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

// ── CLI-adoptable empty state (session path — secondary CTA only) ─────────────

describe("DebugLogTab – CLI-adoptable empty state", () => {
  it("shows 'No debug events found' when noRunEmptyState=cli-adoptable and runId is null", () => {
    render(
      <DebugLogTab sessionId="sess-1" runId={null} host={HOST} noRunEmptyState="cli-adoptable" />
    );
    expect(screen.getByText(/no debug events found/i)).toBeInTheDocument();
    expect(screen.getByText(/no debug events were recorded/i)).toBeInTheDocument();
  });

  it("shows Adopt in Chat button as secondary CTA in CLI-adoptable empty state", () => {
    const onAdoptInChat = vi.fn();
    render(
      <DebugLogTab
        sessionId="sess-1"
        runId={null}
        host={HOST}
        noRunEmptyState="cli-adoptable"
        onAdoptInChat={onAdoptInChat}
      />
    );
    expect(screen.getByTestId("debug-log-adopt-btn")).toBeInTheDocument();
  });

  it("clicking Adopt in Chat button calls onAdoptInChat handler", () => {
    const onAdoptInChat = vi.fn();
    render(
      <DebugLogTab
        sessionId="sess-1"
        runId={null}
        host={HOST}
        noRunEmptyState="cli-adoptable"
        onAdoptInChat={onAdoptInChat}
      />
    );
    fireEvent.click(screen.getByTestId("debug-log-adopt-btn"));
    expect(onAdoptInChat).toHaveBeenCalledOnce();
  });

  it("Adopt in Chat button is disabled when adoptPending=true", () => {
    render(
      <DebugLogTab
        sessionId="sess-1"
        runId={null}
        host={HOST}
        noRunEmptyState="cli-adoptable"
        onAdoptInChat={vi.fn()}
        adoptPending
      />
    );
    const btn = screen.getByTestId("debug-log-adopt-btn");
    expect(btn).toBeDisabled();
  });

  it("shows loader icon when adoptPending=true", () => {
    render(
      <DebugLogTab
        sessionId="sess-1"
        runId={null}
        host={HOST}
        noRunEmptyState="cli-adoptable"
        onAdoptInChat={vi.fn()}
        adoptPending
      />
    );
    expect(screen.getByTestId("icon-loader2")).toBeInTheDocument();
  });

  it("does not block debug display on adoption — entries are shown when session API has data", () => {
    const entry = makeEntry({ idx: 0, message: "CLI session event" });
    (useSessionDebugLog as Mock).mockReturnValue({
      data: makeSessionResponse([entry]),
      error: null,
      isLoading: false,
    });
    render(
      <DebugLogTab sessionId="sess-1" runId={null} host={HOST} noRunEmptyState="cli-adoptable" />
    );
    // Entries are shown — no adoption required to see them
    expect(screen.getByText("CLI session event")).toBeInTheDocument();
    // No Adopt button when there are entries
    expect(screen.queryByTestId("debug-log-adopt-btn")).not.toBeInTheDocument();
  });

  it("does not show generic 'No run available' in CLI-adoptable state", () => {
    render(
      <DebugLogTab sessionId="sess-1" runId={null} host={HOST} noRunEmptyState="cli-adoptable" />
    );
    expect(screen.queryByText(/no run available/i)).not.toBeInTheDocument();
  });
});

// ── Knowledge-only empty state ────────────────────────────────────────────────

describe("DebugLogTab – knowledge-only empty state", () => {
  it("shows 'No debug events found' when noRunEmptyState=knowledge-only and runId is null", () => {
    render(
      <DebugLogTab sessionId="sess-1" runId={null} host={HOST} noRunEmptyState="knowledge-only" />
    );
    expect(screen.getByText(/no debug events found/i)).toBeInTheDocument();
    expect(screen.getByText(/no debug events were recorded/i)).toBeInTheDocument();
  });

  it("does not show Adopt in Chat button in knowledge-only state", () => {
    render(
      <DebugLogTab sessionId="sess-1" runId={null} host={HOST} noRunEmptyState="knowledge-only" />
    );
    expect(screen.queryByTestId("debug-log-adopt-btn")).not.toBeInTheDocument();
  });

  it("does not show generic 'No run available' in knowledge-only state", () => {
    render(
      <DebugLogTab sessionId="sess-1" runId={null} host={HOST} noRunEmptyState="knowledge-only" />
    );
    expect(screen.queryByText(/no run available/i)).not.toBeInTheDocument();
  });
});

// ── Generic fallback (noRunEmptyState=null) ────────────────────────────────────

describe("DebugLogTab – generic no-run fallback (noRunEmptyState=null)", () => {
  it("shows 'No debug events found' when noRunEmptyState is not provided", () => {
    render(<DebugLogTab sessionId="sess-1" runId={null} host={HOST} />);
    expect(screen.getByText(/no debug events found/i)).toBeInTheDocument();
  });

  it("shows 'No debug events found' when noRunEmptyState=null explicitly", () => {
    render(<DebugLogTab sessionId="sess-1" runId={null} host={HOST} noRunEmptyState={null} />);
    expect(screen.getByText(/no debug events found/i)).toBeInTheDocument();
  });

  it("does not show Adopt in Chat button when noRunEmptyState=null", () => {
    render(<DebugLogTab sessionId="sess-1" runId={null} host={HOST} noRunEmptyState={null} />);
    expect(screen.queryByTestId("debug-log-adopt-btn")).not.toBeInTheDocument();
  });
});

// ── Session-scoped path — entries render when no run exists ──────────────────

describe("DebugLogTab – session-scoped entries (no operator run)", () => {
  const sessionEntries = [
    makeEntry({
      idx: 0,
      kind: "session_start",
      message: "Session started via CLI",
      tool_name: null,
    }),
    makeEntry({ idx: 1, kind: "tool_call", message: "Tool invocation", tool_name: "bash" }),
  ];

  beforeEach(() => {
    (useSessionDebugLog as Mock).mockReturnValue({
      data: makeSessionResponse(sessionEntries),
      error: null,
      isLoading: false,
    });
  });

  it("renders event table entries when session API returns data and runId is null", () => {
    render(<DebugLogTab sessionId="sess-1" runId={null} host={HOST} />);
    expect(screen.getByText("Session started via CLI")).toBeInTheDocument();
    expect(screen.getByText("Tool invocation")).toBeInTheDocument();
  });

  it("renders the correct number of rows from session API", () => {
    render(<DebugLogTab sessionId="sess-1" runId={null} host={HOST} />);
    const table = screen.getByRole("grid", { name: /debug log events/i });
    // Each entry produces a row with aria-label starting "Debug event"
    const labelledRows = within(table)
      .getAllByRole("row")
      .filter((r) => r.getAttribute("aria-label")?.startsWith("Debug event"));
    expect(labelledRows).toHaveLength(2);
  });

  it("does not show empty state when session API has entries", () => {
    render(<DebugLogTab sessionId="sess-1" runId={null} host={HOST} />);
    expect(screen.queryByText(/no debug events found/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/no run available/i)).not.toBeInTheDocument();
  });

  it("does not show Adopt in Chat button when entries exist (even for cli-adoptable)", () => {
    render(
      <DebugLogTab sessionId="sess-1" runId={null} host={HOST} noRunEmptyState="cli-adoptable" />
    );
    expect(screen.queryByTestId("debug-log-adopt-btn")).not.toBeInTheDocument();
  });
});

// ── Session-scoped path — error treated as empty (graceful fallback) ──────────

describe("DebugLogTab – session-scoped error fallback", () => {
  it("shows 'No debug events found' on session API error (no error banner)", () => {
    (useSessionDebugLog as Mock).mockReturnValue({
      data: null,
      error: new Error("API 404: not found"),
      isLoading: false,
    });
    render(<DebugLogTab sessionId="sess-1" runId={null} host={HOST} />);
    expect(screen.getByText(/no debug events found/i)).toBeInTheDocument();
    expect(screen.queryByText(/failed to load debug log/i)).not.toBeInTheDocument();
  });

  it("shows error banner for operator path errors (runId present)", () => {
    (useDebugLog as Mock).mockReturnValue({
      data: null,
      error: new Error("Network error"),
      isLoading: false,
    });
    render(<DebugLogTab sessionId="sess-1" runId="run-1" host={HOST} />);
    expect(screen.getByText(/failed to load debug log/i)).toBeInTheDocument();
    expect(screen.getByText(/network error/i)).toBeInTheDocument();
  });
});

// ── Tree view toggle ──────────────────────────────────────────────────────────

describe("DebugLogTab – tree view toggle visibility", () => {
  it("shows List/Tree toggle buttons when entries have span_ids", () => {
    (useDebugLog as Mock).mockReturnValue({
      data: makeResponse([
        makeEntry({ idx: 0, span_id: "aabbccddeeff0011", parent_span_id: null }),
      ]),
      error: null,
      isLoading: false,
    });
    render(<DebugLogTab sessionId="sess-1" runId="run-1" host={HOST} />);
    expect(screen.getByRole("button", { name: /^list$/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^tree$/i })).toBeInTheDocument();
  });

  it("does NOT show toggle when all entries have span_id=null", () => {
    (useDebugLog as Mock).mockReturnValue({
      data: makeResponse([makeEntry({ idx: 0, span_id: null, parent_span_id: null })]),
      error: null,
      isLoading: false,
    });
    render(<DebugLogTab sessionId="sess-1" runId="run-1" host={HOST} />);
    expect(screen.queryByRole("button", { name: /^list$/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^tree$/i })).not.toBeInTheDocument();
  });

  it("default view is list — event table is rendered initially", () => {
    (useDebugLog as Mock).mockReturnValue({
      data: makeResponse([makeEntry({ idx: 0, span_id: "aabbccddeeff0011" })]),
      error: null,
      isLoading: false,
    });
    render(<DebugLogTab sessionId="sess-1" runId="run-1" host={HOST} />);
    expect(screen.getByRole("grid", { name: /debug log events/i })).toBeInTheDocument();
    expect(screen.queryByRole("tree", { name: /debug span tree/i })).not.toBeInTheDocument();
  });

  it("clicking Tree button switches to tree view", () => {
    (useDebugLog as Mock).mockReturnValue({
      data: makeResponse([
        makeEntry({ idx: 0, span_id: "aabbccddeeff0011", parent_span_id: null }),
      ]),
      error: null,
      isLoading: false,
    });
    render(<DebugLogTab sessionId="sess-1" runId="run-1" host={HOST} />);
    fireEvent.click(screen.getByRole("button", { name: /^tree$/i }));
    expect(screen.getByRole("tree", { name: /debug span tree/i })).toBeInTheDocument();
    expect(screen.queryByRole("grid", { name: /debug log events/i })).not.toBeInTheDocument();
  });

  it("clicking List button switches back to list view", () => {
    (useDebugLog as Mock).mockReturnValue({
      data: makeResponse([
        makeEntry({ idx: 0, span_id: "aabbccddeeff0011", parent_span_id: null }),
      ]),
      error: null,
      isLoading: false,
    });
    render(<DebugLogTab sessionId="sess-1" runId="run-1" host={HOST} />);
    fireEvent.click(screen.getByRole("button", { name: /^tree$/i }));
    fireEvent.click(screen.getByRole("button", { name: /^list$/i }));
    expect(screen.getByRole("grid", { name: /debug log events/i })).toBeInTheDocument();
    expect(screen.queryByRole("tree", { name: /debug span tree/i })).not.toBeInTheDocument();
  });
});

// ── Tree view – expand/collapse ───────────────────────────────────────────────

describe("DebugLogTab – tree view expand/collapse", () => {
  const parentEntry = makeEntry({
    idx: 10,
    span_id: "parent0000000010",
    parent_span_id: null,
    message: "Parent event",
    kind: "tool_call",
  });
  const childEntry = makeEntry({
    idx: 11,
    span_id: "child00000000011",
    parent_span_id: "parent0000000010",
    message: "Child event",
    kind: "tool_call",
  });

  beforeEach(() => {
    (useDebugLog as Mock).mockReturnValue({
      data: makeResponse([parentEntry, childEntry]),
      error: null,
      isLoading: false,
    });
  });

  it("child node is not visible before expanding parent", () => {
    render(<DebugLogTab sessionId="sess-1" runId="run-1" host={HOST} />);
    fireEvent.click(screen.getByRole("button", { name: /^tree$/i }));
    expect(screen.queryByText("Child event")).not.toBeInTheDocument();
  });

  it("clicking parent row expands and shows child", () => {
    render(<DebugLogTab sessionId="sess-1" runId="run-1" host={HOST} />);
    fireEvent.click(screen.getByRole("button", { name: /^tree$/i }));

    const parentRow = screen.getByRole("treeitem", {
      name: /span event 10: tool_call from operator_console/i,
    });
    fireEvent.click(parentRow);
    expect(screen.getByText("Child event")).toBeInTheDocument();
  });

  it("clicking parent row again collapses and hides child", () => {
    render(<DebugLogTab sessionId="sess-1" runId="run-1" host={HOST} />);
    fireEvent.click(screen.getByRole("button", { name: /^tree$/i }));

    const parentRow = screen.getByRole("treeitem", {
      name: /span event 10: tool_call from operator_console/i,
    });
    fireEvent.click(parentRow);
    expect(screen.getByText("Child event")).toBeInTheDocument();
    fireEvent.click(parentRow);
    expect(screen.queryByText("Child event")).not.toBeInTheDocument();
  });

  it("expanded parent row has aria-expanded=true", () => {
    render(<DebugLogTab sessionId="sess-1" runId="run-1" host={HOST} />);
    fireEvent.click(screen.getByRole("button", { name: /^tree$/i }));

    const parentRow = screen.getByRole("treeitem", {
      name: /span event 10: tool_call from operator_console/i,
    });
    expect(parentRow).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(parentRow);
    expect(parentRow).toHaveAttribute("aria-expanded", "true");
  });

  it("leaf node does not have aria-expanded attribute", () => {
    render(<DebugLogTab sessionId="sess-1" runId="run-1" host={HOST} />);
    fireEvent.click(screen.getByRole("button", { name: /^tree$/i }));

    const parentRow = screen.getByRole("treeitem", {
      name: /span event 10: tool_call from operator_console/i,
    });
    fireEvent.click(parentRow); // expand to reveal child
    const childRow = screen.getByRole("treeitem", {
      name: /span event 11: tool_call from operator_console/i,
    });
    expect(childRow).not.toHaveAttribute("aria-expanded");
  });
});

// ── Tree view – keyboard navigation ──────────────────────────────────────────

describe("DebugLogTab – tree view keyboard navigation", () => {
  const parentEntry = makeEntry({
    idx: 20,
    span_id: "parent0000000020",
    parent_span_id: null,
    message: "KB parent event",
    kind: "tool_call",
  });
  const childEntry = makeEntry({
    idx: 21,
    span_id: "child00000000021",
    parent_span_id: "parent0000000020",
    message: "KB child event",
    kind: "tool_call",
  });

  beforeEach(() => {
    (useDebugLog as Mock).mockReturnValue({
      data: makeResponse([parentEntry, childEntry]),
      error: null,
      isLoading: false,
    });
  });

  it("Enter key expands a collapsed node", () => {
    render(<DebugLogTab sessionId="sess-1" runId="run-1" host={HOST} />);
    fireEvent.click(screen.getByRole("button", { name: /^tree$/i }));

    const parentRow = screen.getByRole("treeitem", {
      name: /span event 20: tool_call from operator_console/i,
    });
    fireEvent.keyDown(parentRow, { key: "Enter" });
    expect(screen.getByText("KB child event")).toBeInTheDocument();
  });

  it("Space key expands a collapsed node", () => {
    render(<DebugLogTab sessionId="sess-1" runId="run-1" host={HOST} />);
    fireEvent.click(screen.getByRole("button", { name: /^tree$/i }));

    const parentRow = screen.getByRole("treeitem", {
      name: /span event 20: tool_call from operator_console/i,
    });
    fireEvent.keyDown(parentRow, { key: " " });
    expect(screen.getByText("KB child event")).toBeInTheDocument();
  });

  it("ArrowRight expands a collapsed node", () => {
    render(<DebugLogTab sessionId="sess-1" runId="run-1" host={HOST} />);
    fireEvent.click(screen.getByRole("button", { name: /^tree$/i }));

    const parentRow = screen.getByRole("treeitem", {
      name: /span event 20: tool_call from operator_console/i,
    });
    fireEvent.keyDown(parentRow, { key: "ArrowRight" });
    expect(screen.getByText("KB child event")).toBeInTheDocument();
  });

  it("ArrowLeft collapses an expanded node", () => {
    render(<DebugLogTab sessionId="sess-1" runId="run-1" host={HOST} />);
    fireEvent.click(screen.getByRole("button", { name: /^tree$/i }));

    const parentRow = screen.getByRole("treeitem", {
      name: /span event 20: tool_call from operator_console/i,
    });
    // Expand first
    fireEvent.keyDown(parentRow, { key: "ArrowRight" });
    expect(screen.getByText("KB child event")).toBeInTheDocument();
    // Now collapse
    fireEvent.keyDown(parentRow, { key: "ArrowLeft" });
    expect(screen.queryByText("KB child event")).not.toBeInTheDocument();
  });
});

// ── Tree view – error node styling ───────────────────────────────────────────

describe("DebugLogTab – tree view error node styling", () => {
  it("error entries get a red background class in tree view", () => {
    const errorEntry = makeEntry({
      idx: 30,
      span_id: "error00000000030",
      parent_span_id: null,
      status: "error",
      message: "Error event",
      kind: "error",
    });

    (useDebugLog as Mock).mockReturnValue({
      data: makeResponse([errorEntry]),
      error: null,
      isLoading: false,
    });

    render(<DebugLogTab sessionId="sess-1" runId="run-1" host={HOST} />);
    fireEvent.click(screen.getByRole("button", { name: /^tree$/i }));

    const row = screen.getByRole("treeitem", {
      name: /span event 30: error from operator_console/i,
    });
    // Error rows should have the red background class
    expect(row.className).toMatch(/red/);
  });

  it("non-error entries do NOT get the red background class", () => {
    const okEntry = makeEntry({
      idx: 31,
      span_id: "ok000000000000031",
      parent_span_id: null,
      status: "ok",
      message: "Ok event",
    });

    (useDebugLog as Mock).mockReturnValue({
      data: makeResponse([okEntry]),
      error: null,
      isLoading: false,
    });

    render(<DebugLogTab sessionId="sess-1" runId="run-1" host={HOST} />);
    fireEvent.click(screen.getByRole("button", { name: /^tree$/i }));

    const row = screen.getByRole("treeitem", {
      name: /span event 31: tool_call from operator_console/i,
    });
    expect(row.className).not.toMatch(/red-50|red-950/);
  });
});

// ── Tree view – filter integration ───────────────────────────────────────────

describe("DebugLogTab – tree view shares filter state", () => {
  it("text filter applies in tree view — only matching entries shown", () => {
    const entries = [
      makeEntry({
        idx: 40,
        span_id: "span40aaaaaaaaaa",
        parent_span_id: null,
        message: "Alpha tool call",
      }),
      makeEntry({
        idx: 41,
        span_id: "span41aaaaaaaaaa",
        parent_span_id: null,
        message: "Beta event",
        tool_name: null,
      }),
    ];

    (useDebugLog as Mock).mockReturnValue({
      data: makeResponse(entries),
      error: null,
      isLoading: false,
    });

    render(<DebugLogTab sessionId="sess-1" runId="run-1" host={HOST} />);
    fireEvent.click(screen.getByRole("button", { name: /^tree$/i }));

    const textInput = screen.getByRole("textbox", { name: /filter by text/i });
    fireEvent.change(textInput, { target: { value: "Alpha" } });

    expect(screen.getByText("Alpha tool call")).toBeInTheDocument();
    expect(screen.queryByText("Beta event")).not.toBeInTheDocument();
  });
});

// ── Flow chart view ───────────────────────────────────────────────────────────

describe("DebugLogTab – flow chart view", () => {
  const entries = [
    makeEntry({
      idx: 0,
      span_id: "rootaaaaaaaaaaaa",
      parent_span_id: null,
      kind: "turn_start",
      message: "Turn root",
      tool_name: null,
    }),
    makeEntry({
      idx: 1,
      span_id: "childbbbbbbbbbbb",
      parent_span_id: "rootaaaaaaaaaaaa",
      kind: "tool_call",
      message: "Child tool",
      tool_name: "bash",
    }),
    makeEntry({
      idx: 2,
      span_id: "siblingccccccccc",
      parent_span_id: "rootaaaaaaaaaaaa",
      kind: "agent_response",
      message: "Sibling response",
      tool_name: null,
      duration_ms: null,
      timestamp: null,
    }),
  ];

  beforeEach(() => {
    (useDebugLog as Mock).mockReturnValue({
      data: makeResponse(entries),
      error: null,
      isLoading: false,
    });
  });

  it("Flow toggle is visible when entries have span_ids", () => {
    render(<DebugLogTab sessionId="sess-1" runId="run-1" host={HOST} />);
    expect(screen.getByTestId("debug-log-view-flow")).toBeInTheDocument();
  });

  it("renders a flow chart with one node per entry (plus parent edges)", () => {
    render(<DebugLogTab sessionId="sess-1" runId="run-1" host={HOST} />);
    fireEvent.click(screen.getByTestId("debug-log-view-flow"));

    expect(screen.getByTestId("debug-log-flow-chart")).toBeInTheDocument();
    expect(screen.getByTestId("debug-log-flow-node-0")).toBeInTheDocument();
    expect(screen.getByTestId("debug-log-flow-node-1")).toBeInTheDocument();
    expect(screen.getByTestId("debug-log-flow-node-2")).toBeInTheDocument();
  });

  it("clicking a flow node opens the existing detail drawer", () => {
    render(<DebugLogTab sessionId="sess-1" runId="run-1" host={HOST} />);
    fireEvent.click(screen.getByTestId("debug-log-view-flow"));

    fireEvent.click(screen.getByTestId("debug-log-flow-node-1"));
    expect(screen.getByRole("dialog", { name: /debug event detail/i })).toBeInTheDocument();
  });

  it("filter narrows the chart to matching nodes", () => {
    render(<DebugLogTab sessionId="sess-1" runId="run-1" host={HOST} />);
    fireEvent.click(screen.getByTestId("debug-log-view-flow"));

    const textInput = screen.getByRole("textbox", { name: /filter by text/i });
    fireEvent.change(textInput, { target: { value: "Sibling" } });

    // Filter strips out parent → all three become orphans; only the matching one remains as a node.
    expect(screen.getByTestId("debug-log-flow-node-2")).toBeInTheDocument();
    expect(screen.queryByTestId("debug-log-flow-node-0")).not.toBeInTheDocument();
    expect(screen.queryByTestId("debug-log-flow-node-1")).not.toBeInTheDocument();
  });

  it("does not crash when entries are missing parent_span_id, duration, or timestamp", () => {
    const messyEntries = [
      makeEntry({
        idx: 10,
        span_id: null,
        parent_span_id: null,
        duration_ms: null,
        timestamp: null,
        tool_name: null,
        message: "Bare event",
      }),
      makeEntry({
        idx: 11,
        span_id: "orphanaaaaaaaaaa",
        parent_span_id: "missing000000000",
        duration_ms: null,
        timestamp: null,
        tool_name: null,
        message: "Orphan event",
      }),
    ];
    (useDebugLog as Mock).mockReturnValue({
      data: makeResponse(messyEntries),
      error: null,
      isLoading: false,
    });

    render(<DebugLogTab sessionId="sess-1" runId="run-1" host={HOST} />);
    // Flow toggle is only shown when at least one entry has a span_id.
    fireEvent.click(screen.getByTestId("debug-log-view-flow"));

    expect(screen.getByTestId("debug-log-flow-chart")).toBeInTheDocument();
    // Orphan entry rendered, plus synthetic orphans group.
    expect(screen.getByTestId("debug-log-flow-node-11")).toBeInTheDocument();
    expect(screen.getByTestId("debug-log-flow-node-orphan")).toBeInTheDocument();
  });
});
