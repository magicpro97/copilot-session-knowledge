import "@testing-library/jest-dom";
import { act, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

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

function makeSseStream(events: unknown[]): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder();
  return new ReadableStream({
    start(controller) {
      for (const event of events) {
        controller.enqueue(encoder.encode(`data: ${JSON.stringify(event)}\n\n`));
      }
      controller.close();
    },
  });
}

function Probe({
  url,
  enabled = true,
  transport,
  authToken,
}: {
  url: string;
  enabled?: boolean;
  transport?: "eventsource" | "fetch";
  authToken?: string;
}) {
  const { events, status } = useSSE(url, { enabled, transport, authToken });
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
    vi.stubGlobal("EventSource", MockEventSource as unknown as typeof EventSource);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("clears prior events when the EventSource URL changes", async () => {
    const { rerender } = render(<Probe url="/api/live?stream=one" transport="eventsource" />);

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

    rerender(<Probe url="/api/live?stream=two" transport="eventsource" />);

    expect(screen.queryByText("Old host event")).not.toBeInTheDocument();
    expect(MockEventSource.instances[0].closed).toBe(true);
    expect(MockEventSource.instances[1].url).toBe("/api/live?stream=two");
  });

  it("uses fetch with Authorization for remote streams without exposing token in the URL", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        body: makeSseStream([
          {
            id: 2,
            category: "patterns",
            title: "Remote host event",
            wing: "beta",
            room: "two",
            created_at: "2026-05-04T00:00:00Z",
          },
        ]),
      })
    );

    render(<Probe url="https://remote.example.com/api/live" transport="fetch" authToken="tok" />);

    await waitFor(() => expect(screen.getByText("Remote host event")).toBeInTheDocument());
    expect(vi.mocked(fetch)).toHaveBeenCalledWith(
      "https://remote.example.com/api/live",
      expect.objectContaining({
        headers: { Authorization: "Bearer tok" },
      })
    );
    expect(MockEventSource.instances).toHaveLength(0);
  });

  it("omits Authorization for remote fetch streams when no token is set", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        body: makeSseStream([]),
      })
    );

    render(<Probe url="https://remote.example.com/api/live" transport="fetch" />);

    await waitFor(() => expect(vi.mocked(fetch)).toHaveBeenCalled());
    expect(vi.mocked(fetch)).toHaveBeenCalledWith(
      "https://remote.example.com/api/live",
      expect.objectContaining({
        headers: {},
      })
    );
  });
});
