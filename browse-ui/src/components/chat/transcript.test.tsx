import { render } from "@testing-library/react";
import { beforeAll, describe, expect, it, vi } from "vitest";

import { Transcript } from "@/components/chat/transcript";
import { LOCAL_HOST } from "@/lib/host-profiles";

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
  (sessionId: string | null, runId: string | null, host?: HostProfile | null) => {
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
});
