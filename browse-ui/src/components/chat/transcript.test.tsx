import "@testing-library/jest-dom";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeAll, describe, expect, it, vi } from "vitest";

import { Transcript } from "@/components/chat/transcript";
import { LOCAL_HOST } from "@/lib/host-profiles";
import type { UseOperatorStreamResult } from "./use-operator-stream";

const REMOTE_HOST = {
  id: "tunnel-1",
  label: "My Tunnel",
  base_url: "https://xyz.ngrok.io",
  token: "secret",
  cli_kind: "copilot" as const,
  is_default: false,
};

beforeAll(() => {
  window.HTMLElement.prototype.scrollIntoView = vi.fn();
});

import type { HostProfile } from "@/lib/api/types";

const mockUseOperatorStream = vi.fn(
  (
    sessionId: string | null,
    runId: string | null,
    host?: HostProfile | null
  ): UseOperatorStreamResult => {
    void sessionId;
    void runId;
    void host;
    return {
      frames: [],
      status: "done" as const,
      exitCode: 0,
    };
  }
);

vi.mock("@/components/chat/use-operator-stream", () => ({
  useOperatorStream: (sessionId: string | null, runId: string | null, host?: HostProfile | null) =>
    mockUseOperatorStream(sessionId, runId, host),
}));

// Issue #563: mock the cancel-run mutation hook and the capability gate so
// Transcript can render <ActiveRun> in tests without a QueryClient provider.
const mockCancelMutate = vi.fn();
const mockCancelState = { isPending: false };
vi.mock("@/lib/api/hooks", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api/hooks")>("@/lib/api/hooks");
  return {
    ...actual,
    useCancelOperatorRun: (_sessionId: string, _host?: unknown) => ({
      mutate: mockCancelMutate,
      isPending: mockCancelState.isPending,
    }),
  };
});

const mockUseHostFeature = vi.fn((_host: HostProfile, _feature: string, _enabled?: boolean) => ({
  supported: true,
  loading: false,
}));
vi.mock("@/lib/hosts/use-host-feature", () => ({
  useHostFeature: (host: HostProfile, feature: string, enabled?: boolean) =>
    mockUseHostFeature(host, feature, enabled),
}));

describe("Transcript", () => {
  it("calls onRunDone only once when callback identity changes after completion", () => {
    const firstOnDone = vi.fn();
    const { rerender } = render(
      <Transcript
        runs={[]}
        activeRun={{ id: "run-1", prompt: "Ship it" }}
        sessionId="session-1"
        onRunDone={firstOnDone}
      />
    );

    expect(firstOnDone).toHaveBeenCalledTimes(1);

    const secondOnDone = vi.fn();
    rerender(
      <Transcript
        runs={[]}
        activeRun={{ id: "run-1", prompt: "Ship it" }}
        sessionId="session-1"
        onRunDone={secondOnDone}
      />
    );

    expect(firstOnDone).toHaveBeenCalledTimes(1);
    expect(secondOnDone).not.toHaveBeenCalled();
  });

  it("passes host to useOperatorStream when host prop is provided", () => {
    mockUseOperatorStream.mockClear();

    render(
      <Transcript
        runs={[]}
        activeRun={{ id: "run-remote", prompt: "Hello" }}
        sessionId="session-remote"
        host={REMOTE_HOST}
      />
    );

    expect(mockUseOperatorStream).toHaveBeenCalledWith("session-remote", "run-remote", REMOTE_HOST);
  });

  it("passes null host to useOperatorStream when no host prop is given (same-origin fallback)", () => {
    mockUseOperatorStream.mockClear();

    render(
      <Transcript
        runs={[]}
        activeRun={{ id: "run-local", prompt: "Hello" }}
        sessionId="session-local"
      />
    );

    // host is undefined → useOperatorStream third arg is undefined/null
    expect(mockUseOperatorStream).toHaveBeenCalledWith("session-local", "run-local", undefined);
  });

  it("passes LOCAL_HOST to useOperatorStream when local host is explicitly set", () => {
    mockUseOperatorStream.mockClear();

    render(
      <Transcript
        runs={[]}
        activeRun={{ id: "run-l2", prompt: "Local" }}
        sessionId="session-l2"
        host={LOCAL_HOST}
      />
    );

    expect(mockUseOperatorStream).toHaveBeenCalledWith("session-l2", "run-l2", LOCAL_HOST);
  });

  it("auto-scrolls while an active run is streaming", async () => {
    const scrollSpy = vi.fn();
    window.HTMLElement.prototype.scrollIntoView = scrollSpy;
    mockUseOperatorStream.mockReturnValueOnce({
      frames: [
        {
          type: "assistant.message_delta",
          idx: 0,
          event: { type: "assistant.message_delta", data: { deltaContent: "Hello" } },
          data: { deltaContent: "Hello" },
        },
      ],
      status: "streaming" as const,
      exitCode: null,
    });

    render(
      <Transcript
        runs={[]}
        activeRun={{ id: "run-streaming", prompt: "Hello" }}
        sessionId="session-streaming"
      />
    );

    await waitFor(() => expect(scrollSpy).toHaveBeenCalled());
    expect(screen.getAllByText("Hello")).toHaveLength(2);
  });

  // ── Issue #563: per-run cancel UI ─────────────────────────────────────

  it("shows the cancel button while streaming and run_cancel is supported", () => {
    mockUseOperatorStream.mockReturnValueOnce({
      frames: [],
      status: "streaming" as const,
      exitCode: null,
    });
    mockUseHostFeature.mockReturnValueOnce({ supported: true, loading: false });

    render(
      <Transcript
        runs={[]}
        activeRun={{ id: "run-cancel", prompt: "long task" }}
        sessionId="session-cancel"
      />
    );

    const btn = screen.getByTestId("cancel-run-button");
    expect(btn).toBeEnabled();
    expect(btn).toHaveAccessibleName(/cancel current run/i);
  });

  it("hides the cancel button when run_cancel capability is unsupported", () => {
    mockUseOperatorStream.mockReturnValueOnce({
      frames: [],
      status: "streaming" as const,
      exitCode: null,
    });
    mockUseHostFeature.mockReturnValueOnce({ supported: false, loading: false });

    render(
      <Transcript
        runs={[]}
        activeRun={{ id: "run-legacy", prompt: "legacy" }}
        sessionId="session-legacy"
      />
    );

    expect(screen.queryByTestId("cancel-run-button")).toBeNull();
  });

  it("hides the cancel button once the stream reaches a terminal status", () => {
    mockUseOperatorStream.mockReturnValueOnce({
      frames: [],
      status: "done" as const,
      exitCode: 0,
    });
    mockUseHostFeature.mockReturnValueOnce({ supported: true, loading: false });

    render(
      <Transcript
        runs={[]}
        activeRun={{ id: "run-done", prompt: "done" }}
        sessionId="session-done"
      />
    );

    expect(screen.queryByTestId("cancel-run-button")).toBeNull();
  });

  it("invokes the cancel mutation exactly once per click", async () => {
    mockCancelMutate.mockClear();
    mockUseOperatorStream.mockReturnValue({
      frames: [],
      status: "streaming" as const,
      exitCode: null,
    });
    mockUseHostFeature.mockReturnValue({ supported: true, loading: false });

    render(
      <Transcript
        runs={[]}
        activeRun={{ id: "run-click", prompt: "click" }}
        sessionId="session-click"
      />
    );

    const btn = screen.getByTestId("cancel-run-button");
    fireEvent.click(btn);

    expect(mockCancelMutate).toHaveBeenCalledTimes(1);
    expect(mockCancelMutate).toHaveBeenCalledWith("run-click");
  });

  it("renders the cancelled badge and maps cancelled→done for onRunDone", () => {
    const onDone = vi.fn();
    mockUseOperatorStream.mockReturnValueOnce({
      frames: [],
      status: "cancelled" as const,
      exitCode: null,
    });
    mockUseHostFeature.mockReturnValueOnce({ supported: true, loading: false });

    render(
      <Transcript
        runs={[]}
        activeRun={{ id: "run-cancelled", prompt: "x" }}
        sessionId="session-cancelled"
        onRunDone={onDone}
      />
    );

    expect(screen.getByTestId("cancelled-badge")).toBeInTheDocument();
    // contract preserved: parent sees "done", not "cancelled".
    expect(onDone).toHaveBeenCalledTimes(1);
    expect(onDone).toHaveBeenCalledWith("done", "run-cancelled");
  });
});

// ── Regression: HistoricalRun rendering ─────────────────────────────────────

import type { OperatorRunInfo } from "@/lib/api/types";

const makeRun = (overrides: Partial<OperatorRunInfo> = {}): OperatorRunInfo => ({
  id: "run-hist-1",
  session_id: "sess-1",
  prompt: "What model?",
  status: "done",
  exit_code: 0,
  started_at: "2026-05-05T17:12:57Z",
  finished_at: "2026-05-05T17:13:38Z",
  events: [],
  ...overrides,
});

describe("Transcript — historical runs", () => {
  it("renders prompt text for a historical run", () => {
    render(<Transcript runs={[makeRun()]} sessionId="sess-1" />);
    expect(screen.getByText("What model?")).toBeInTheDocument();
  });

  it("renders final answer text from task_complete tool result", () => {
    // Real Test-session: answer is inside task_complete tool, not assistant.message
    const run = makeRun({
      events: [
        {
          type: "assistant.message",
          idx: 0,
          event: { type: "assistant.message", data: { content: "" } },
          data: { content: "" },
        },
        {
          type: "tool.execution_start",
          idx: 1,
          event: {
            type: "tool.execution_start",
            data: { toolName: "task_complete", arguments: { summary: "I am GPT-5.4." } },
          },
          data: { toolName: "task_complete", arguments: { summary: "I am GPT-5.4." } },
        },
        {
          type: "tool.execution_complete",
          idx: 2,
          event: {
            type: "tool.execution_complete",
            data: { result: { content: "I am GPT-5.4." } },
          },
          data: { result: { content: "I am GPT-5.4." } },
        },
      ],
    });
    render(<Transcript runs={[run]} sessionId="sess-1" />);
    expect(screen.getByText("I am GPT-5.4.")).toBeInTheDocument();
  });

  it("does not render procedural task_complete summary as the assistant answer", () => {
    const run = makeRun({
      prompt: "hi",
      events: [
        {
          type: "tool.execution_start",
          idx: 0,
          event: {
            type: "tool.execution_start",
            data: {
              toolName: "task_complete",
              arguments: { summary: "Acknowledging the greeting and closing the turn." },
            },
          },
          data: {
            toolName: "task_complete",
            arguments: { summary: "Acknowledging the greeting and closing the turn." },
          },
        },
        {
          type: "tool.execution_complete",
          idx: 1,
          event: {
            type: "tool.execution_complete",
            data: { result: { content: "Acknowledging the greeting and closing the turn." } },
          },
          data: { result: { content: "Acknowledging the greeting and closing the turn." } },
        },
      ],
    });

    render(<Transcript runs={[run]} sessionId="sess-1" />);
    expect(screen.queryByText("Acknowledging the greeting and closing the turn.")).toBeNull();
    expect(screen.getByText("task_complete")).toBeInTheDocument();
  });

  it("renders elapsed duration for a completed historical run", () => {
    // started_at: 17:12:57, finished_at: 17:13:38 → 41 seconds
    render(<Transcript runs={[makeRun()]} sessionId="sess-1" />);
    expect(screen.getByText("41s")).toBeInTheDocument();
  });

  it("renders per-run resumed context state when resume_used is true", () => {
    render(<Transcript runs={[makeRun({ resume_used: true })]} sessionId="sess-1" />);
    expect(screen.getByText("resumed context")).toBeInTheDocument();
  });

  it("renders per-run new context state when resume_used is false", () => {
    render(<Transcript runs={[makeRun({ resume_used: false })]} sessionId="sess-1" />);
    expect(screen.getByText("new context")).toBeInTheDocument();
  });

  it("renders without errors for old runs without resume_used field", () => {
    // Backward-compatibility: resume_used is optional and must not cause crash
    const run = makeRun(); // no resume_used field
    expect(() => render(<Transcript runs={[run]} sessionId="sess-1" />)).not.toThrow();
  });

  // ── session.skills_loaded in historical runs ─────────────────────────────────

  it("renders skills loaded badge for a historical run with populated skills event", () => {
    const run = makeRun({
      events: [
        {
          type: "session.skills_loaded",
          idx: 0,
          event: {
            type: "session.skills_loaded",
            data: {
              skills: [
                {
                  name: "code-reviewer",
                  description: "...",
                  source: "global",
                  userInvocable: true,
                  enabled: true,
                  path: "/p",
                },
                {
                  name: "frontend-dev",
                  description: "...",
                  source: "global",
                  userInvocable: true,
                  enabled: true,
                  path: "/q",
                },
              ],
            },
          },
          data: {
            skills: [
              {
                name: "code-reviewer",
                description: "...",
                source: "global",
                userInvocable: true,
                enabled: true,
                path: "/p",
              },
              {
                name: "frontend-dev",
                description: "...",
                source: "global",
                userInvocable: true,
                enabled: true,
                path: "/q",
              },
            ],
          },
        },
      ],
    });
    render(<Transcript runs={[run]} sessionId="sess-1" />);
    expect(screen.getByText("2 skills loaded")).toBeInTheDocument();
  });

  it("does not render skills loaded badge when only the bootstrap empty event is present", () => {
    const run = makeRun({
      events: [
        {
          type: "session.skills_loaded",
          idx: 0,
          event: { type: "session.skills_loaded", data: { skills: [] } },
          data: { skills: [] },
        },
      ],
    });
    render(<Transcript runs={[run]} sessionId="sess-1" />);
    expect(screen.queryByText(/skills loaded/)).toBeNull();
  });

  it("renders skills loaded badge from populated event after bootstrap in historical run", () => {
    const run = makeRun({
      events: [
        {
          type: "session.skills_loaded",
          idx: 0,
          event: { type: "session.skills_loaded", data: { skills: [] } },
          data: { skills: [] },
        },
        {
          type: "session.skills_loaded",
          idx: 1,
          event: {
            type: "session.skills_loaded",
            data: {
              skills: [
                {
                  name: "karpathy-guidelines",
                  description: "...",
                  source: "global",
                  userInvocable: false,
                  enabled: true,
                  path: "/k",
                },
              ],
            },
          },
          data: {
            skills: [
              {
                name: "karpathy-guidelines",
                description: "...",
                source: "global",
                userInvocable: false,
                enabled: true,
                path: "/k",
              },
            ],
          },
        },
      ],
    });
    render(<Transcript runs={[run]} sessionId="sess-1" />);
    expect(screen.getByText("1 skills loaded")).toBeInTheDocument();
  });
});
