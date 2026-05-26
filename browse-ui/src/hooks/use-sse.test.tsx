import "@testing-library/jest-dom";
import { act, render, screen, waitFor } from "@testing-library/react";
import { StrictMode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useSSE, LIVE_EVENT_BUFFER_LIMIT } from "@/hooks/use-sse";

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

// ── Buffer cap (#566) ─────────────────────────────────────────────────────────

function BufferProbe({ url }: { url: string }) {
  const { events, dropped, capped, bufferLimit, clear } = useSSE(url, {
    transport: "eventsource",
  });
  return (
    <div>
      <span data-testid="events-len">{events.length}</span>
      <span data-testid="dropped">{dropped}</span>
      <span data-testid="capped">{capped ? "yes" : "no"}</span>
      <span data-testid="limit">{bufferLimit}</span>
      <button type="button" data-testid="clear" onClick={clear}>
        clear
      </button>
    </div>
  );
}

describe("useSSE — buffer cap", () => {
  beforeEach(() => {
    MockEventSource.instances = [];
    vi.stubGlobal("EventSource", MockEventSource as unknown as typeof EventSource);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  function emitEvent(es: MockEventSource, id: number) {
    es.emitMessage({
      id,
      category: "patterns",
      title: `event-${id}`,
      wing: "alpha",
      room: "one",
      created_at: "2026-05-04T00:00:00Z",
    });
  }

  it("exposes the named buffer limit and starts uncapped", () => {
    render(<BufferProbe url="/api/live?stream=cap" />);
    expect(screen.getByTestId("limit")).toHaveTextContent(String(LIVE_EVENT_BUFFER_LIMIT));
    expect(screen.getByTestId("capped")).toHaveTextContent("no");
    expect(screen.getByTestId("dropped")).toHaveTextContent("0");
  });

  it("retains at most LIVE_EVENT_BUFFER_LIMIT events and counts overflow as dropped", async () => {
    render(<BufferProbe url="/api/live?stream=cap" />);
    const es = MockEventSource.instances[0];
    const total = LIVE_EVENT_BUFFER_LIMIT + 5;
    await act(async () => {
      for (let i = 0; i < total; i += 1) emitEvent(es, i);
    });
    expect(screen.getByTestId("events-len")).toHaveTextContent(String(LIVE_EVENT_BUFFER_LIMIT));
    expect(screen.getByTestId("dropped")).toHaveTextContent("5");
    expect(screen.getByTestId("capped")).toHaveTextContent("yes");
  });

  it("clear() resets events and dropped count without reconnecting", async () => {
    render(<BufferProbe url="/api/live?stream=cap" />);
    const es = MockEventSource.instances[0];
    await act(async () => {
      for (let i = 0; i < LIVE_EVENT_BUFFER_LIMIT + 3; i += 1) emitEvent(es, i);
    });
    expect(screen.getByTestId("dropped")).toHaveTextContent("3");

    await act(async () => {
      screen.getByTestId("clear").click();
    });

    expect(screen.getByTestId("events-len")).toHaveTextContent("0");
    expect(screen.getByTestId("dropped")).toHaveTextContent("0");
    expect(screen.getByTestId("capped")).toHaveTextContent("no");
    // No new EventSource constructed — clear is purely client-side
    expect(MockEventSource.instances).toHaveLength(1);
    expect(es.closed).toBe(false);
  });

  it("counts each overflow event exactly once under React.StrictMode", async () => {
    // Regression: pushEvent previously called setDropped inside setEvents'
    // updater. Under StrictMode (dev-only), React intentionally double-invokes
    // updater functions to surface impure logic, which double-counted dropped
    // events. The reducer-based buffer makes the update atomic.
    render(
      <StrictMode>
        <BufferProbe url="/api/live?stream=strict" />
      </StrictMode>
    );
    const es = MockEventSource.instances.find((i) => !i.closed)!;
    const overflow = 7;
    const total = LIVE_EVENT_BUFFER_LIMIT + overflow;
    await act(async () => {
      for (let i = 0; i < total; i += 1) emitEvent(es, i);
    });
    expect(screen.getByTestId("events-len")).toHaveTextContent(String(LIVE_EVENT_BUFFER_LIMIT));
    expect(screen.getByTestId("dropped")).toHaveTextContent(String(overflow));
  });
});
