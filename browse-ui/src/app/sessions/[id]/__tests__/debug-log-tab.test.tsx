import "@testing-library/jest-dom";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { Mock } from "vitest";
import type {
  BrowseDebugEntry,
  DebugLogResponse,
  SessionDebugLogResponse,
  SessionMissionAtlasResponse,
  SubagentActivityResponse,
  SubagentInternalsResponse,
} from "@/lib/api/types";
import { DebugLogTab } from "../debug-log-tab";

// ── Mock debug-log API hooks ──────────────────────────────────────────────────

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
  useSubagentActivity: vi.fn(() => ({
    data: null,
    error: null,
    isLoading: false,
  })),
  useSubagentInternals: vi.fn(() => ({
    data: null,
    error: null,
    isLoading: false,
  })),
  useSessionMissionAtlas: vi.fn(() => ({
    data: null,
    error: null,
    isLoading: false,
  })),
}));

import {
  useDebugLog,
  useSessionDebugLog,
  useSubagentActivity,
  useSubagentInternals,
  useSessionMissionAtlas,
} from "@/lib/api/hooks";

// Reset all mocks to their default no-data state before every test.
beforeEach(() => {
  (useDebugLog as Mock).mockReturnValue({ data: null, error: null, isLoading: false });
  (useSessionDebugLog as Mock).mockReturnValue({ data: null, error: null, isLoading: false });
  (useSubagentActivity as Mock).mockReturnValue({ data: null, error: null, isLoading: false });
  (useSubagentInternals as Mock).mockReturnValue({ data: null, error: null, isLoading: false });
  (useSessionMissionAtlas as Mock).mockReturnValue({ data: null, error: null, isLoading: false });
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
    // Scope to the event-table grid so the mission-strip "bash" chip
    // (introduced for FR v3 P2) doesn't ambiguate the assertion.
    const table = screen.getByRole("grid", { name: /debug log events/i });
    expect(within(table).getByText("bash")).toBeInTheDocument();
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

  it("hides Tree but keeps Flow available when all current-page entries have span_id=null", () => {
    (useDebugLog as Mock).mockReturnValue({
      data: makeResponse([makeEntry({ idx: 0, span_id: null, parent_span_id: null })]),
      error: null,
      isLoading: false,
    });
    render(<DebugLogTab sessionId="sess-1" runId="run-1" host={HOST} />);
    expect(screen.getByRole("button", { name: /^list$/i })).toBeInTheDocument();
    expect(screen.getByTestId("debug-log-view-flow")).toBeInTheDocument();
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

  it("keeps Flow available even when the current page has no span_ids", () => {
    (useDebugLog as Mock).mockReturnValue({
      data: makeResponse([
        makeEntry({
          span_id: null,
          parent_span_id: null,
          kind: "generic",
          message: "Page event without span metadata",
        }),
      ]),
      error: null,
      isLoading: false,
    });

    render(<DebugLogTab sessionId="sess-1" runId="run-1" host={HOST} />);

    expect(screen.getByTestId("debug-log-view-flow")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^tree$/i })).not.toBeInTheDocument();
  });

  it("renders the Flight Recorder mission strip in List view (P2)", () => {
    render(<DebugLogTab sessionId="sess-1" runId="run-1" host={HOST} />);
    // Default view is "list" — mission strip must already be present.
    expect(screen.getByTestId("flight-recorder-mission-strip")).toBeInTheDocument();
  });

  it("keeps the mission strip visible after switching to Tree and Flow views (P2)", () => {
    render(<DebugLogTab sessionId="sess-1" runId="run-1" host={HOST} />);
    // Switch to Tree.
    const tree = screen.getByRole("button", { name: /^tree$/i });
    fireEvent.click(tree);
    expect(screen.getByTestId("flight-recorder-mission-strip")).toBeInTheDocument();
    // Switch to Flow.
    fireEvent.click(screen.getByTestId("debug-log-view-flow"));
    // Mission strip must still be visible exactly once (no duplicate in flow chart).
    expect(screen.getAllByTestId("flight-recorder-mission-strip")).toHaveLength(1);
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

// ── Flow chart view: rich render model & wheel zoom ───────────────────────────

describe("DebugLogTab – flow chart rich render", () => {
  const richEntries: BrowseDebugEntry[] = [
    makeEntry({
      idx: 0,
      span_id: "rootaaaaaaaaaaaa",
      parent_span_id: null,
      kind: "turn_start",
      message: "Turn 1",
      tool_name: null,
      duration_ms: null,
      timestamp: "2024-01-01T12:00:00.000Z",
      attrs: { event_type: "turn", mode: "agent" },
    }),
    makeEntry({
      idx: 1,
      span_id: "toolbbbbbbbbbbbb",
      parent_span_id: "rootaaaaaaaaaaaa",
      kind: "tool_call",
      message: "ran bash",
      tool_name: "bash",
      duration_ms: 1234,
      timestamp: "2024-01-01T12:00:05.000Z",
      status: "ok",
      attrs: {
        event_type: "tool_call",
        tool_status: "ok",
        tool_result_type: "stdout",
        tool_metric_duration_ms: 1234,
      },
    }),
    makeEntry({
      idx: 2,
      span_id: "hookcccccccccccc",
      parent_span_id: "rootaaaaaaaaaaaa",
      kind: "hook",
      message: "preToolUse",
      tool_name: null,
      duration_ms: 8,
      timestamp: "2024-01-01T12:00:06.000Z",
      status: "error",
      attrs: {
        event_type: "hook",
        hook_type: "preToolUse",
        hook_status: "error",
        event_phase: "complete",
      },
    }),
  ];

  beforeEach(() => {
    (useDebugLog as Mock).mockReturnValue({
      data: makeResponse(richEntries),
      error: null,
      isLoading: false,
    });
  });

  it("renders rich label, sublabel, timestamp, and layer from render model", () => {
    render(<DebugLogTab sessionId="sess-1" runId="run-1" host={HOST} />);
    fireEvent.click(screen.getByTestId("debug-log-view-flow"));

    // Tool node: label = tool_name, sublabel includes status / result_type / duration.
    expect(screen.getByTestId("debug-log-flow-node-1-label")).toHaveTextContent("bash");
    expect(screen.getByTestId("debug-log-flow-node-1-sublabel")).toHaveTextContent(/ok/);
    expect(screen.getByTestId("debug-log-flow-node-1-sublabel")).toHaveTextContent(/stdout/);
    expect(screen.getByTestId("debug-log-flow-node-1-sublabel")).toHaveTextContent(/1\.23s/);
    expect(screen.getByTestId("debug-log-flow-node-1-timestamp")).toHaveTextContent("12:00:05");
    // Hook node: label = hook_type.
    expect(screen.getByTestId("debug-log-flow-node-2-label")).toHaveTextContent("preToolUse");
    // Turn node carries the safe `mode` as layer header.
    expect(screen.getByTestId("debug-log-flow-node-0-layer")).toHaveTextContent("agent");
  });

  it("shows error indicator and error-tinted sublabel on error nodes", () => {
    render(<DebugLogTab sessionId="sess-1" runId="run-1" host={HOST} />);
    fireEvent.click(screen.getByTestId("debug-log-view-flow"));

    const errorNode = screen.getByTestId("debug-log-flow-node-2");
    expect(errorNode).toHaveAttribute("data-flow-status", "error");
    expect(screen.getByTestId("debug-log-flow-node-2-error")).toBeInTheDocument();
  });

  it("renders the tooltip body (multi-line) from render.tooltipLines", () => {
    render(<DebugLogTab sessionId="sess-1" runId="run-1" host={HOST} />);
    fireEvent.click(screen.getByTestId("debug-log-view-flow"));

    const toolNode = screen.getByTestId("debug-log-flow-node-1");
    const title = toolNode.querySelector("title");
    expect(title).not.toBeNull();
    const text = title?.textContent ?? "";
    expect(text).toMatch(/kind: tool_call/);
    expect(text).toMatch(/tool: bash/);
    expect(text).toMatch(/status: ok/);
    expect(text).toMatch(/duration: 1\.23s/);
    expect(text).toMatch(/time: 12:00:05/);
  });

  it("exposes safe layer info in the toolbar layers summary", () => {
    render(<DebugLogTab sessionId="sess-1" runId="run-1" host={HOST} />);
    fireEvent.click(screen.getByTestId("debug-log-view-flow"));

    const layers = screen.getByTestId("debug-log-flow-layers");
    // Turn carries mode=agent; the operator_console source becomes the layer
    // for the tool/hook entries (deriveLayer falls back to entry.source).
    expect(layers).toHaveTextContent(/agent/);
  });

  it("shows the has_more hint when more events are available", () => {
    (useDebugLog as Mock).mockReturnValue({
      data: makeResponse(richEntries, { has_more: true, total: 123 }),
      error: null,
      isLoading: false,
    });
    render(<DebugLogTab sessionId="sess-1" runId="run-1" host={HOST} />);
    fireEvent.click(screen.getByTestId("debug-log-view-flow"));

    const hint = screen.getByTestId("debug-log-flow-has-more");
    expect(hint).toHaveTextContent(/More events available/);
    expect(hint).toHaveTextContent(/123/);
  });

  it("does not render the has_more hint when has_more is false", () => {
    render(<DebugLogTab sessionId="sess-1" runId="run-1" host={HOST} />);
    fireEvent.click(screen.getByTestId("debug-log-view-flow"));
    expect(screen.queryByTestId("debug-log-flow-has-more")).not.toBeInTheDocument();
  });

  it("clicking a rich flow node still opens the detail drawer", () => {
    render(<DebugLogTab sessionId="sess-1" runId="run-1" host={HOST} />);
    fireEvent.click(screen.getByTestId("debug-log-view-flow"));

    fireEvent.click(screen.getByTestId("debug-log-flow-node-1"));
    expect(screen.getByRole("dialog", { name: /debug event detail/i })).toBeInTheDocument();
  });

  it("attaches a non-passive wheel listener that zooms and updates the level", () => {
    render(<DebugLogTab sessionId="sess-1" runId="run-1" host={HOST} />);
    fireEvent.click(screen.getByTestId("debug-log-view-flow"));

    const svg = screen.getByRole("img", { name: /debug log flow chart/i });
    const zoom = screen.getByTestId("debug-log-flow-zoom");
    expect(zoom).toHaveTextContent("100%");

    // Dispatch a native, cancelable wheel event so the non-passive listener
    // runs `preventDefault` without the jsdom passive-listener warning.
    // fireEvent wraps dispatch in act() so React state flushes synchronously.
    const wheelDown = new WheelEvent("wheel", {
      deltaY: 500,
      clientX: 10,
      clientY: 10,
      bubbles: true,
      cancelable: true,
    });
    fireEvent(svg, wheelDown);

    // preventDefault must have been honoured (non-passive listener).
    expect(wheelDown.defaultPrevented).toBe(true);

    // Zooming out → scale < 100%. The percentage label must change.
    expect(zoom).not.toHaveTextContent("100%");
  });

  it("removes the wheel listener on unmount (no leaks)", () => {
    const { unmount } = render(<DebugLogTab sessionId="sess-1" runId="run-1" host={HOST} />);
    fireEvent.click(screen.getByTestId("debug-log-view-flow"));
    const svg = screen.getByRole("img", { name: /debug log flow chart/i });
    const removeSpy = vi.spyOn(svg, "removeEventListener");
    unmount();
    const calls = removeSpy.mock.calls.map((c) => c[0]);
    expect(calls).toContain("wheel");
  });
});

// ── focusEntryIdx — Timeline → Debug Log sync handoff (#541) ─────────────────

describe("DebugLogTab – focusEntryIdx handoff", () => {
  const page0Entries = [
    makeEntry({ idx: 0, kind: "session_start", message: "Session start" }),
    makeEntry({ idx: 1, kind: "tool_call", message: "First tool" }),
    makeEntry({ idx: 5, kind: "error", message: "Error entry", status: "error" }),
  ];

  beforeEach(() => {
    (useDebugLog as Mock).mockReturnValue({
      data: makeResponse(page0Entries),
      error: null,
      isLoading: false,
    });
  });

  it("default behavior unchanged when focusEntryIdx is not provided", () => {
    render(<DebugLogTab sessionId="sess-1" runId="run-1" host={HOST} />);
    // No detail drawer open by default
    expect(screen.queryByRole("dialog", { name: /debug event detail/i })).not.toBeInTheDocument();
    // All 3 entries visible
    expect(screen.getByText("Session start")).toBeInTheDocument();
    expect(screen.getByText("First tool")).toBeInTheDocument();
    expect(screen.getByText("Error entry")).toBeInTheDocument();
  });

  it("default behavior unchanged when focusEntryIdx is null", () => {
    render(<DebugLogTab sessionId="sess-1" runId="run-1" host={HOST} focusEntryIdx={null} />);
    expect(screen.queryByRole("dialog", { name: /debug event detail/i })).not.toBeInTheDocument();
  });

  it("opens detail drawer for the requested idx when entry is on current page", () => {
    render(<DebugLogTab sessionId="sess-1" runId="run-1" host={HOST} focusEntryIdx={1} />);
    // Entry idx=1 should be selected and detail drawer open
    expect(screen.getByRole("dialog", { name: /debug event detail/i })).toBeInTheDocument();
  });

  it("detail drawer shows the correct entry when focusEntryIdx is provided", () => {
    render(<DebugLogTab sessionId="sess-1" runId="run-1" host={HOST} focusEntryIdx={5} />);
    const drawer = screen.getByRole("dialog", { name: /debug event detail/i });
    // Entry idx=5 is "Error entry"
    expect(within(drawer).getByText("Error entry")).toBeInTheDocument();
  });

  it("clears filters when focusEntryIdx is set (so filters do not hide target)", () => {
    render(<DebugLogTab sessionId="sess-1" runId="run-1" host={HOST} focusEntryIdx={5} />);
    // Filters should have been cleared — all 3 entries should be in the page-level data
    // The filter toolbar should show "3 of 3"
    expect(screen.getByText(/3 of 3/)).toBeInTheDocument();
  });

  it("calls onFocusEntryHandled after selecting the entry", () => {
    const onFocusEntryHandled = vi.fn();
    render(
      <DebugLogTab
        sessionId="sess-1"
        runId="run-1"
        host={HOST}
        focusEntryIdx={1}
        onFocusEntryHandled={onFocusEntryHandled}
      />
    );
    expect(onFocusEntryHandled).toHaveBeenCalledOnce();
  });

  it("calls onFocusEntryHandled even when target idx not found in data", () => {
    const onFocusEntryHandled = vi.fn();
    // Request idx=999 which is not in page0Entries
    render(
      <DebugLogTab
        sessionId="sess-1"
        runId="run-1"
        host={HOST}
        focusEntryIdx={999}
        onFocusEntryHandled={onFocusEntryHandled}
      />
    );
    // No detail drawer since idx=999 not found
    expect(screen.queryByRole("dialog", { name: /debug event detail/i })).not.toBeInTheDocument();
    // But handler still called to avoid repeated loops
    expect(onFocusEntryHandled).toHaveBeenCalledOnce();
  });

  it("navigates to target page when focusEntryIdx is on a different page", () => {
    // Simulate idx=105 on page 1 (PAGE_SIZE=100); page 0 has different entries
    // First mock returns page 0 data (no idx=105)
    const page1Entry = makeEntry({ idx: 105, kind: "llm_request", message: "Page 1 entry" });
    (useDebugLog as Mock)
      .mockReturnValueOnce({
        // Initial call: page 0 data
        data: makeResponse(page0Entries),
        error: null,
        isLoading: false,
      })
      .mockReturnValue({
        // After page change to page 1
        data: makeResponse([page1Entry], { from: 100, total: 200, has_more: true }),
        error: null,
        isLoading: false,
      });

    render(<DebugLogTab sessionId="sess-1" runId="run-1" host={HOST} focusEntryIdx={105} />);
    // After re-render with page 1 data, detail drawer should open for idx=105
    expect(screen.getByRole("dialog", { name: /debug event detail/i })).toBeInTheDocument();
  });
});

// ── Flow chart view: v2 trace inspector (lanes, ruler, inspector card) ───────

describe("DebugLogTab – flow chart v2 trace inspector", () => {
  const v2Entries: BrowseDebugEntry[] = [
    makeEntry({
      idx: 0,
      span_id: "v2rootaaaaaaaaaa",
      parent_span_id: null,
      kind: "turn_start",
      message: "Turn started",
      tool_name: null,
      duration_ms: null,
      timestamp: "2024-01-01T12:00:00.000Z",
      attrs: { event_type: "turn", mode: "agent" },
    }),
    makeEntry({
      idx: 1,
      span_id: "v2toolbbbbbbbbbb",
      parent_span_id: "v2rootaaaaaaaaaa",
      kind: "tool_call",
      message: "ran bash",
      tool_name: "bash",
      duration_ms: 1200,
      timestamp: "2024-01-01T12:00:01.000Z",
      status: "ok",
      attrs: {
        event_type: "tool_call",
        tool_status: "ok",
        tool_result_type: "stdout",
        output_tokens: 42,
      },
    }),
    makeEntry({
      idx: 2,
      span_id: "v2modelccccccccc",
      parent_span_id: "v2rootaaaaaaaaaa",
      kind: "agent_response",
      message: "assistant",
      tool_name: null,
      duration_ms: 300,
      timestamp: "2024-01-01T12:00:03.000Z",
      attrs: { output_tokens: 17 },
    }),
  ];

  beforeEach(() => {
    (useDebugLog as Mock).mockReturnValue({
      data: makeResponse(v2Entries),
      error: null,
      isLoading: false,
    });
  });

  it("renders the trace overview strip with a time ruler and lane rows", () => {
    render(<DebugLogTab sessionId="sess-1" runId="run-1" host={HOST} />);
    fireEvent.click(screen.getByTestId("debug-log-view-flow"));

    expect(screen.getByTestId("debug-log-flow-trace")).toBeInTheDocument();
    expect(screen.getByTestId("debug-log-flow-time-ruler")).toBeInTheDocument();
    // Tool/Hook/Skill and Model lanes both have entries; Turn too.
    expect(screen.getByTestId("debug-log-flow-lane-tool-hook-skill")).toBeInTheDocument();
    expect(screen.getByTestId("debug-log-flow-lane-model")).toBeInTheDocument();
    expect(screen.getByTestId("debug-log-flow-lane-turn")).toBeInTheDocument();
  });

  it("shows >=2 ruler ticks with '+'-prefixed labels in time mode", () => {
    render(<DebugLogTab sessionId="sess-1" runId="run-1" host={HOST} />);
    fireEvent.click(screen.getByTestId("debug-log-view-flow"));

    const ruler = screen.getByTestId("debug-log-flow-time-ruler");
    const ticks = within(ruler).getAllByText(/^[+#]/);
    expect(ticks.length).toBeGreaterThanOrEqual(2);
    // Time mode → strip data attr.
    expect(screen.getByTestId("debug-log-flow-trace")).toHaveAttribute("data-flow-mode", "time");
  });

  it("renders one clickable bar per visible entry inside its lane", () => {
    render(<DebugLogTab sessionId="sess-1" runId="run-1" host={HOST} />);
    fireEvent.click(screen.getByTestId("debug-log-view-flow"));

    expect(screen.getByTestId("debug-log-flow-bar-0")).toBeInTheDocument();
    expect(screen.getByTestId("debug-log-flow-bar-1")).toBeInTheDocument();
    expect(screen.getByTestId("debug-log-flow-bar-2")).toBeInTheDocument();
  });

  it("clicking a bar opens the inspector card with safe rows and the detail drawer", () => {
    render(<DebugLogTab sessionId="sess-1" runId="run-1" host={HOST} />);
    fireEvent.click(screen.getByTestId("debug-log-view-flow"));

    fireEvent.click(screen.getByTestId("debug-log-flow-bar-1"));

    const inspector = screen.getByTestId("debug-log-flow-inspector");
    expect(inspector).toBeInTheDocument();
    expect(within(inspector).getAllByText("bash").length).toBeGreaterThan(0);
    expect(screen.getByTestId("debug-log-flow-inspector-status")).toHaveTextContent("ok");
    expect(screen.getByTestId("debug-log-flow-inspector-row-tool")).toHaveTextContent("bash");
    expect(screen.getByTestId("debug-log-flow-inspector-row-duration")).toHaveTextContent(/1\.20s/);
    expect(screen.getByTestId("debug-log-flow-inspector-row-tokens")).toHaveTextContent("42");
    // Detail drawer still opens (parent selection contract preserved).
    expect(screen.getByRole("dialog", { name: /debug event detail/i })).toBeInTheDocument();
  });

  it("falls back to data-flow-mode='index' when most entries have null timestamps", () => {
    const noisyEntries: BrowseDebugEntry[] = [
      makeEntry({
        idx: 0,
        span_id: "ix0aaaaaaaaaaaaa",
        parent_span_id: null,
        kind: "tool_call",
        tool_name: "bash",
        message: "a",
      }),
      makeEntry({
        idx: 1,
        span_id: "ix1bbbbbbbbbbbbb",
        parent_span_id: "ix0aaaaaaaaaaaaa",
        kind: "tool_call",
        tool_name: "bash",
        message: "b",
      }),
      makeEntry({
        idx: 2,
        span_id: "ix2ccccccccccccc",
        parent_span_id: "ix0aaaaaaaaaaaaa",
        kind: "tool_call",
        tool_name: "bash",
        message: "c",
      }),
    ];
    // Strip timestamps after construction (helper uses `??` so null can't be passed in).
    noisyEntries.forEach((e) => {
      e.timestamp = null;
    });
    (useDebugLog as Mock).mockReturnValue({
      data: makeResponse(noisyEntries),
      error: null,
      isLoading: false,
    });
    render(<DebugLogTab sessionId="sess-1" runId="run-1" host={HOST} />);
    fireEvent.click(screen.getByTestId("debug-log-view-flow"));

    expect(screen.getByTestId("debug-log-flow-trace")).toHaveAttribute("data-flow-mode", "index");
    // Ruler ticks must use "#" prefix in index mode.
    const ruler = screen.getByTestId("debug-log-flow-time-ruler");
    expect(within(ruler).getAllByText(/^#/).length).toBeGreaterThanOrEqual(2);
  });

  it("inspector card renders an empty placeholder until first selection", () => {
    render(<DebugLogTab sessionId="sess-1" runId="run-1" host={HOST} />);
    fireEvent.click(screen.getByTestId("debug-log-view-flow"));

    expect(screen.getByTestId("debug-log-flow-inspector-empty")).toBeInTheDocument();
    expect(screen.queryByTestId("debug-log-flow-inspector")).not.toBeInTheDocument();
  });

  it("preserves the legacy node test IDs after the v2 strip is added", () => {
    render(<DebugLogTab sessionId="sess-1" runId="run-1" host={HOST} />);
    fireEvent.click(screen.getByTestId("debug-log-view-flow"));

    // v1 tree must still render.
    expect(screen.getByTestId("debug-log-flow-node-0")).toBeInTheDocument();
    expect(screen.getByTestId("debug-log-flow-node-1")).toBeInTheDocument();
    expect(screen.getByTestId("debug-log-flow-node-2")).toBeInTheDocument();
  });
});

// ── SubagentActivityPanel integration tests ───────────────────────────────────

function makeSubagentResponse(
  overrides?: Partial<SubagentActivityResponse>
): SubagentActivityResponse {
  return {
    schema_version: "1",
    session_id: "sess-1",
    total_subagents_seen: 2,
    returned: 2,
    cap: 1000,
    truncated: false,
    dropped_pending_starts: 0,
    entries: [
      {
        span_id: "span-a",
        agent_name: "code-review",
        agent_display_name: "Code Review Agent",
        model: "gpt-4o",
        status: "completed",
        started_at: "2024-01-01T10:00:00.000Z",
        ended_at: "2024-01-01T10:00:05.000Z",
        duration_ms: 5000,
        total_tool_calls: 3,
        total_tokens: 900,
        error_category: null,
        error_preview: null,
        start_idx: 10,
        end_idx: 20,
        redacted: false,
      },
      {
        span_id: "span-b",
        agent_name: "explore",
        agent_display_name: "Explore Agent",
        model: "claude-3-haiku",
        status: "failed",
        started_at: "2024-01-01T10:01:00.000Z",
        ended_at: "2024-01-01T10:01:02.000Z",
        duration_ms: 2000,
        total_tool_calls: 1,
        total_tokens: 200,
        error_category: "rate_limited",
        error_preview: null,
        start_idx: 50,
        end_idx: 55,
        redacted: false,
      },
    ],
    ...overrides,
  };
}

function makeSubagentInternalsResponse(
  overrides?: Partial<SubagentInternalsResponse>
): SubagentInternalsResponse {
  return {
    schema_version: "1",
    session_id: "sess-1",
    total_agents_seen: 1,
    returned: 1,
    cap: 1000,
    truncated: false,
    dropped_pending_starts: 0,
    skill_correlation_supported: false,
    uncorrelated_skill_invocations: 1,
    session_skill_names: ["code-reviewer"],
    entries: [
      {
        agent_key_hash: "0123456789abcdef",
        span_id: "span-a",
        agent_name: "code-review",
        agent_display_name: "Code Review Agent",
        model: "gpt-4o",
        status: "completed",
        started_at: "2024-01-01T10:00:00.000Z",
        ended_at: "2024-01-01T10:00:05.000Z",
        duration_ms: 5000,
        start_idx: 10,
        end_idx: 20,
        redacted: false,
        internals: {
          internal_event_count: 3,
          tool_call_count: 2,
          tool_success_count: 1,
          tool_failure_count: 0,
          llm_turn_count: 1,
          output_tokens_total: 700,
          tool_names: ["view", "rg"],
          tools: [
            {
              idx: 11,
              end_idx: 12,
              tool_name: "view",
              status: "completed",
              started_at: "2024-01-01T10:00:01.000Z",
              ended_at: "2024-01-01T10:00:02.000Z",
              duration_ms: 1000,
              input_bytes: 12,
              output_bytes: 2048,
            },
          ],
          tools_truncated: false,
          model_events: [
            {
              idx: 13,
              timestamp: "2024-01-01T10:00:03.000Z",
              output_tokens: 700,
              tool_request_count: 1,
            },
          ],
          model_events_truncated: false,
        },
      },
    ],
    ...overrides,
  };
}

describe("DebugLogTab — SubagentActivityPanel integration", () => {
  it("renders SubagentActivityPanel when subagent activity data is available (operator path)", () => {
    (useDebugLog as Mock).mockReturnValue({
      data: makeResponse([makeEntry()]),
      error: null,
      isLoading: false,
    });
    (useSubagentActivity as Mock).mockReturnValue({
      data: makeSubagentResponse(),
      error: null,
      isLoading: false,
    });
    render(<DebugLogTab sessionId="sess-1" runId="run-1" host={HOST} />);
    expect(screen.getByTestId("subagent-activity-panel")).toBeInTheDocument();
    expect(screen.getByTestId("subagent-summary-total")).toHaveTextContent("2 runs");
  });

  it("renders SubagentActivityPanel when subagent activity data is available (session path)", () => {
    (useSessionDebugLog as Mock).mockReturnValue({
      data: makeSessionResponse([makeEntry()]),
      error: null,
      isLoading: false,
    });
    (useSubagentActivity as Mock).mockReturnValue({
      data: makeSubagentResponse(),
      error: null,
      isLoading: false,
    });
    render(<DebugLogTab sessionId="sess-1" runId={null} host={HOST} />);
    expect(screen.getByTestId("subagent-activity-panel")).toBeInTheDocument();
    expect(screen.getByTestId("subagent-activity-row-span-a")).toBeInTheDocument();
    expect(screen.getByTestId("subagent-activity-row-span-b")).toBeInTheDocument();
  });

  it("shows completed row with data-status=ok", () => {
    (useDebugLog as Mock).mockReturnValue({
      data: makeResponse([makeEntry()]),
      error: null,
      isLoading: false,
    });
    (useSubagentActivity as Mock).mockReturnValue({
      data: makeSubagentResponse(),
      error: null,
      isLoading: false,
    });
    render(<DebugLogTab sessionId="sess-1" runId="run-1" host={HOST} />);
    expect(screen.getByTestId("subagent-activity-row-span-a")).toHaveAttribute("data-status", "ok");
  });

  it("shows failed row with data-status=error and correct outcome text", () => {
    (useDebugLog as Mock).mockReturnValue({
      data: makeResponse([makeEntry()]),
      error: null,
      isLoading: false,
    });
    (useSubagentActivity as Mock).mockReturnValue({
      data: makeSubagentResponse(),
      error: null,
      isLoading: false,
    });
    render(<DebugLogTab sessionId="sess-1" runId="run-1" host={HOST} />);
    expect(screen.getByTestId("subagent-activity-row-span-b")).toHaveAttribute(
      "data-status",
      "error"
    );
    expect(screen.getByTestId("subagent-activity-row-span-b-outcome")).toHaveTextContent(
      "Failed: rate_limited"
    );
  });

  it("SubagentActivityPanel shows loading state when subagent query is loading", () => {
    (useDebugLog as Mock).mockReturnValue({
      data: makeResponse([makeEntry()]),
      error: null,
      isLoading: false,
    });
    (useSubagentActivity as Mock).mockReturnValue({
      data: null,
      error: null,
      isLoading: true,
    });
    render(<DebugLogTab sessionId="sess-1" runId="run-1" host={HOST} />);
    expect(screen.getByTestId("subagent-activity-panel")).toBeInTheDocument();
    expect(screen.getByTestId("subagent-loading")).toBeInTheDocument();
  });

  it("SubagentActivityPanel shows empty state when no subagent data", () => {
    (useDebugLog as Mock).mockReturnValue({
      data: makeResponse([makeEntry()]),
      error: null,
      isLoading: false,
    });
    (useSubagentActivity as Mock).mockReturnValue({
      data: null,
      error: null,
      isLoading: false,
    });
    render(<DebugLogTab sessionId="sess-1" runId="run-1" host={HOST} />);
    expect(screen.getByTestId("subagent-activity-panel")).toBeInTheDocument();
    expect(screen.getByTestId("subagent-empty")).toBeInTheDocument();
  });

  it("MissionStrip subagent chip click triggers flash on SubagentActivityPanel", async () => {
    (useDebugLog as Mock).mockReturnValue({
      data: makeResponse([makeEntry()]),
      error: null,
      isLoading: false,
    });
    (useSubagentActivity as Mock).mockReturnValue({
      data: makeSubagentResponse(),
      error: null,
      isLoading: false,
    });
    render(<DebugLogTab sessionId="sess-1" runId="run-1" host={HOST} />);
    // Panel must be visible before chip click
    expect(screen.getByTestId("subagent-activity-panel")).toBeInTheDocument();
    // Click the sub-agents chip
    fireEvent.click(screen.getByTestId("mission-chip-subagents"));
    // Panel should now have data-flash=true after effects flush
    await waitFor(() => {
      expect(screen.getByTestId("subagent-activity-panel")).toHaveAttribute("data-flash", "true");
    });
  });

  it("MissionStrip subagent chip resets filter to all when activated", async () => {
    (useDebugLog as Mock).mockReturnValue({
      data: makeResponse([makeEntry()]),
      error: null,
      isLoading: false,
    });
    (useSubagentActivity as Mock).mockReturnValue({
      data: makeSubagentResponse(),
      error: null,
      isLoading: false,
    });
    render(<DebugLogTab sessionId="sess-1" runId="run-1" host={HOST} />);
    // Set filter to failed first
    fireEvent.click(screen.getByTestId("subagent-filter-failed"));
    expect(screen.getByTestId("subagent-filter-failed")).toHaveAttribute("aria-pressed", "true");
    // Now click the MissionStrip chip
    fireEvent.click(screen.getByTestId("mission-chip-subagents"));
    // Filter should reset to all after effects flush
    await waitFor(() => {
      expect(screen.getByTestId("subagent-filter-all")).toHaveAttribute("aria-pressed", "true");
    });
  });

  it("mission-chip-subagents shows correct count from dedicated activity route", () => {
    (useDebugLog as Mock).mockReturnValue({
      data: makeResponse([makeEntry()]),
      error: null,
      isLoading: false,
    });
    (useSubagentActivity as Mock).mockReturnValue({
      data: makeSubagentResponse(),
      error: null,
      isLoading: false,
    });
    render(<DebugLogTab sessionId="sess-1" runId="run-1" host={HOST} />);
    // MissionStrip chip should show count from dedicated route (2 entries),
    // not from the 100-event paginated debug log window.
    expect(screen.getByTestId("mission-chip-subagents")).toHaveTextContent("Sub-agents: 2");
  });

  it("expands a subagent row with correlated internals and skill attribution note", () => {
    (useDebugLog as Mock).mockReturnValue({
      data: makeResponse([makeEntry()]),
      error: null,
      isLoading: false,
    });
    (useSubagentActivity as Mock).mockReturnValue({
      data: makeSubagentResponse(),
      error: null,
      isLoading: false,
    });
    (useSubagentInternals as Mock).mockReturnValue({
      data: makeSubagentInternalsResponse(),
      error: null,
      isLoading: false,
    });
    render(<DebugLogTab sessionId="sess-1" runId="run-1" host={HOST} />);

    fireEvent.click(screen.getByTestId("subagent-activity-row-span-a"));

    expect(screen.getByTestId("subagent-activity-row-span-a-trace-summary")).toHaveTextContent(
      "2 tools"
    );
    expect(screen.getByTestId("subagent-activity-row-span-a-trace-tools")).toHaveTextContent(
      "view"
    );
    expect(screen.getByTestId("subagent-activity-row-span-a-trace-model-events")).toHaveTextContent(
      "assistant.message"
    );
    expect(screen.getByTestId("subagent-activity-row-span-a-trace-skills")).toHaveTextContent(
      "session-level skill load"
    );
  });

  it("does not block debug log table when subagent query fails", () => {
    (useDebugLog as Mock).mockReturnValue({
      data: makeResponse([makeEntry()]),
      error: null,
      isLoading: false,
    });
    (useSubagentActivity as Mock).mockReturnValue({
      data: null,
      error: new Error("API unavailable"),
      isLoading: false,
    });
    render(<DebugLogTab sessionId="sess-1" runId="run-1" host={HOST} />);
    // Debug log table must still render
    expect(screen.getByRole("grid", { name: /debug log events/i })).toBeInTheDocument();
    // Sub-agent panel shows error state
    expect(screen.getByTestId("subagent-error")).toBeInTheDocument();
  });

  it("useSubagentActivity is called with session ID regardless of run path", () => {
    render(<DebugLogTab sessionId="sess-xyz" runId="run-1" host={HOST} />);
    const calls = (useSubagentActivity as Mock).mock.calls;
    expect(calls.at(-1)?.[0]).toBe("sess-xyz");
  });

  it("useSubagentInternals is called with session ID regardless of run path", () => {
    render(<DebugLogTab sessionId="sess-xyz" runId="run-1" host={HOST} />);
    const calls = (useSubagentInternals as Mock).mock.calls;
    expect(calls.at(-1)?.[0]).toBe("sess-xyz");
  });

  it("useSessionMissionAtlas is called with enabled=false when viewMode is list", () => {
    render(<DebugLogTab sessionId="sess-atlas" runId="run-1" host={HOST} />);
    const calls = (useSessionMissionAtlas as Mock).mock.calls;
    // The second argument (enabled) should be false when viewMode defaults to list.
    expect(calls.at(-1)?.[1]).toBe(false);
  });

  it("useSessionMissionAtlas is called with enabled=true when viewMode is flow", async () => {
    (useDebugLog as Mock).mockReturnValue({
      data: makeResponse([makeEntry()]),
      error: null,
      isLoading: false,
    });
    render(<DebugLogTab sessionId="sess-atlas" runId="run-1" host={HOST} />);
    // Switch to flow mode using data-testid.
    fireEvent.click(screen.getByTestId("debug-log-view-flow"));
    const calls = (useSessionMissionAtlas as Mock).mock.calls;
    // After switching to flow, enabled should be true.
    expect(calls.at(-1)?.[1]).toBe(true);
  });

  it("Flow view renders full-session canvas when aggregate has totalEvents > PAGE_SIZE", async () => {
    const atlas: SessionMissionAtlasResponse = {
      schema_version: "1",
      session_id: "sess-atlas",
      total_events: 1000,
      event_file_bytes: 120000,
      first_event_at: "2024-01-01T00:00:00.000Z",
      last_event_at: "2024-01-01T00:10:00.000Z",
      duration_ms: 600000,
      bucket_count: 1,
      buckets: [
        {
          bucket_idx: 0,
          start_idx: 0,
          end_idx: 99,
          event_count: 100,
          start_rel_ms: 0,
          end_rel_ms: 60000,
          ts_start: "2024-01-01T00:00:00.000Z",
          ts_end: "2024-01-01T00:01:00.000Z",
          is_gap: false,
          error_count: 0,
          dominant_lane: "tool",
          lanes: { tool: 60, model: 40 },
        },
      ],
      lane_totals: {
        tool: 60,
        hook: 0,
        skill: 5,
        subagent: 3,
        model: 40,
        turn: 1,
        system: 0,
        error: 0,
        generic: 0,
      },
      top_tools: [{ name: "bash", count: 60 }],
      top_skills: [{ name: "session-knowledge", count: 5 }],
      top_agent_names: [{ name: "flow-agent", count: 3 }],
      milestones: [
        {
          idx: 42,
          timestamp: "2024-01-01T00:00:42.000Z",
          kind: "checkpoint",
          label: "Research checkpoint",
          bucket_idx: 0,
        },
      ],
      artifact_counts: {
        checkpoint_files: 1,
        rewind_snapshots: 0,
        todos_total: 0,
        todos_done: 0,
        todos_blocked: 0,
        todo_deps: 0,
        files: 0,
        compactions: 0,
      },
      error_count: 0,
      error_sample: [],
      caps: { top_n: 20, milestones: 200, error_sample: 5, buckets_min: 10, buckets_max: 200 },
      truncated: { tools: false, skills: false, agents: false, milestones: false },
    };

    (useDebugLog as Mock).mockReturnValue({
      data: makeResponse([makeEntry()]),
      error: null,
      isLoading: false,
    });
    (useSessionMissionAtlas as Mock).mockReturnValue({
      data: atlas,
      error: null,
      isLoading: false,
    });
    render(<DebugLogTab sessionId="sess-atlas" runId="run-1" host={HOST} />);
    fireEvent.click(screen.getByTestId("debug-log-view-flow"));
    await waitFor(() => {
      expect(screen.getByTestId("debug-log-flow-session-canvas")).toBeInTheDocument();
    });
    const canvas = screen.getByTestId("debug-log-flow-session-canvas");
    expect(canvas.getAttribute("data-flow-total-events")).toBe("1000");
    expect(screen.getByTestId("debug-log-flow-session-bucket-tool-0")).toBeInTheDocument();
    expect(screen.getByTestId("debug-log-flow-session-bucket-model-0")).toBeInTheDocument();
    expect(screen.getByTestId("debug-log-flow-session-milestone-0")).toBeInTheDocument();
    expect(screen.getByTestId("debug-log-flow-session-tool-chip-bash")).toHaveTextContent("bash");
    expect(
      screen.getByTestId("debug-log-flow-session-skill-chip-session-knowledge")
    ).toHaveTextContent("session-knowledge");
    expect(screen.getByTestId("debug-log-flow-session-agent-chip-flow-agent")).toHaveTextContent(
      "flow-agent"
    );
  });
});
