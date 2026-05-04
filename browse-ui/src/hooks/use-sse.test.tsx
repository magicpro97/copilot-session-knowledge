import "@testing-library/jest-dom";
import { act, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { useSSE } from "@/hooks/use-sse";

class MockEventSource {
  static instances: MockEventSource[] = [];

  url: string;
  readyState = 1;
  onopen: ((event: Event) => void) | null = null;
  onmessage: ((event: MessageEvent<string>) => void) | null = null;
  onerror: ((event: Event) => void) | null = null;
  closed = false;

  constructor(url: string | URL) {
    this.url = String(url);
    MockEventSource.instances.push(this);
  }

  close() {
    this.closed = true;
    this.readyState = 2;
  }

  emitMessage(data: unknown) {
    this.onmessage?.({ data: JSON.stringify(data) } as MessageEvent<string>);
  }
}

vi.stubGlobal("EventSource", MockEventSource as unknown as typeof EventSource);

function Probe({ url, enabled = true }: { url: string; enabled?: boolean }) {
  const { events, status } = useSSE(url, { enabled });
  return (
    <div>
      <span data-testid="status">{status}</span>
      <ul>
        {events.map((event) => (
          <li key={event.id}>{event.title}</li>
        ))}
      </ul>
    </div>
  );
}

describe("useSSE", () => {
  beforeEach(() => {
    MockEventSource.instances = [];
  });

  it("clears prior events when the stream URL changes", async () => {
    const { rerender } = render(<Probe url="/api/live?stream=one" />);

    await act(async () => {
      MockEventSource.instances[0].emitMessage({
        id: 1,
        category: "mistakes",
        title: "Old host event",
        wing: "alpha",
        room: "one",
        created_at: "2026-05-04T00:00:00Z",
      });
    });

    expect(screen.getByText("Old host event")).toBeInTheDocument();

    rerender(<Probe url="https://remote.example.com/api/live?token=tok" />);

    expect(screen.queryByText("Old host event")).not.toBeInTheDocument();
    expect(MockEventSource.instances[0].closed).toBe(true);
    expect(MockEventSource.instances[1].url).toBe("https://remote.example.com/api/live?token=tok");
  });
});
