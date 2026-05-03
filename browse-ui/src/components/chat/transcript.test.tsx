import { render } from "@testing-library/react";
import { beforeAll, describe, expect, it, vi } from "vitest";

import { Transcript } from "@/components/chat/transcript";

beforeAll(() => {
  window.HTMLElement.prototype.scrollIntoView = vi.fn();
});

vi.mock("@/components/chat/use-operator-stream", () => ({
  useOperatorStream: vi.fn(() => ({
    frames: [],
    status: "done",
    exitCode: 0,
  })),
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
});
