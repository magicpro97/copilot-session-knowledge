import { renderHook, act, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { useOperatorStream } from "@/components/chat/use-operator-stream";

// ---------------------------------------------------------------------------
// Minimal mock for the URL-builder helpers from hooks.ts
// ---------------------------------------------------------------------------
vi.mock("@/lib/api/hooks", () => ({
  createOperatorStreamPath: (sid: string, rid: string) =>
    `/api/operator/sessions/${sid}/stream?run=${rid}`,
  createOperatorStreamUrl: (sid: string, rid: string, host: { base_url: string }) =>
    host.base_url
      ? `${host.base_url}/api/operator/sessions/${sid}/stream?run=${rid}`
      : `/api/operator/sessions/${sid}/stream?run=${rid}`,
}));

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------
const LOCAL_HOST = {
  id: "local",
  label: "Local",
  base_url: "",
  token: "",
  cli_kind: "copilot" as const,
  is_default: true,
};

const REMOTE_HOST = {
  id: "remote-1",
  label: "Remote",
  base_url: "https://xyz.ngrok.io",
  token: "remote-tok",
  cli_kind: "copilot" as const,
  is_default: false,
};

const REMOTE_HOST_NO_TOKEN = {
  ...REMOTE_HOST,
  id: "remote-no-token",
  token: "",
};

// ---------------------------------------------------------------------------
// Minimal EventSource mock
// ---------------------------------------------------------------------------
class MockEventSource {
  static instances: MockEventSource[] = [];

  url: string;
  onopen: ((e: Event) => void) | null = null;
  onmessage: ((e: MessageEvent<string>) => void) | null = null;
  onerror: ((e: Event) => void) | null = null;
  closed = false;

  constructor(url: string | URL) {
    this.url = String(url);
    MockEventSource.instances.push(this);
  }

  close() {
    this.closed = true;
  }

  emitMessage(data: unknown) {
    this.onmessage?.({ data: JSON.stringify(data) } as MessageEvent<string>);
  }

  emitOpen() {
    this.onopen?.(new Event("open"));
  }

  emitError() {
    this.onerror?.(new Event("error"));
  }
}

// ---------------------------------------------------------------------------
// Helpers to build an SSE readable stream
// ---------------------------------------------------------------------------
function makeSseStream(frames: unknown[]): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder();
  return new ReadableStream({
    start(controller) {
      for (const frame of frames) {
        controller.enqueue(encoder.encode(`data: ${JSON.stringify(frame)}\n\n`));
      }
      controller.close();
    },
  });
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------
describe("useOperatorStream", () => {
  beforeEach(() => {
    MockEventSource.instances = [];
    vi.stubGlobal("EventSource", MockEventSource as unknown as typeof EventSource);
  });

  it("starts idle when sessionId or runId is null", () => {
    const { result } = renderHook(() => useOperatorStream(null, null));
    expect(result.current.status).toBe("idle");
    expect(result.current.frames).toHaveLength(0);
    expect(result.current.exitCode).toBeNull();
  });

  it("resets to idle when sessionId becomes null after streaming starts", async () => {
    const { result, rerender } = renderHook(
      ({ sid, rid }: { sid: string | null; rid: string | null }) =>
        useOperatorStream(sid, rid, LOCAL_HOST),
      { initialProps: { sid: "sess-1" as string | null, rid: "run-1" as string | null } }
    );

    rerender({ sid: null, rid: null });
    expect(result.current.status).toBe("idle");
  });

  // ── Local / same-origin path (EventSource) ──────────────────────────────

  it("uses EventSource for local host (not fetch)", () => {
    const fetchSpy = vi.fn();
    vi.stubGlobal("fetch", fetchSpy);

    renderHook(() => useOperatorStream("sess-1", "run-1", LOCAL_HOST));

    expect(fetchSpy).not.toHaveBeenCalled();
    expect(MockEventSource.instances).toHaveLength(1);
    expect(MockEventSource.instances[0].url).toContain("/api/operator/sessions/sess-1/stream");
  });

  it("uses EventSource for null host (same-origin fallback)", () => {
    const fetchSpy = vi.fn();
    vi.stubGlobal("fetch", fetchSpy);

    renderHook(() => useOperatorStream("sess-1", "run-1"));

    expect(fetchSpy).not.toHaveBeenCalled();
    expect(MockEventSource.instances).toHaveLength(1);
  });

  it("transitions to streaming when EventSource opens", async () => {
    renderHook(() => useOperatorStream("sess-1", "run-1", LOCAL_HOST));

    await act(async () => {
      MockEventSource.instances[0].emitOpen();
    });

    // Status update happens in onopen handler
    expect(MockEventSource.instances[0]).toBeDefined();
  });

  it("collects frames and closes on terminal status frame via EventSource", async () => {
    const { result } = renderHook(() => useOperatorStream("sess-1", "run-1", LOCAL_HOST));

    await act(async () => {
      MockEventSource.instances[0].emitMessage({ type: "text", content: "hello" });
    });

    expect(result.current.frames).toHaveLength(1);

    await act(async () => {
      MockEventSource.instances[0].emitMessage({ type: "status", status: "done", exit_code: 0 });
    });

    expect(result.current.status).toBe("done");
    expect(result.current.exitCode).toBe(0);
    expect(MockEventSource.instances[0].closed).toBe(true);
  });

  it("sets status to error on EventSource onerror", async () => {
    const { result } = renderHook(() => useOperatorStream("sess-1", "run-1", LOCAL_HOST));

    await act(async () => {
      MockEventSource.instances[0].emitError();
    });

    expect(result.current.status).toBe("error");
    expect(MockEventSource.instances[0].closed).toBe(true);
  });

  // ── Remote host path (fetch + Authorization header) ─────────────────────

  it("uses fetch (not EventSource) for remote host (issue #32)", async () => {
    const mockFetch = vi.fn().mockResolvedValue({
      ok: true,
      body: makeSseStream([]),
    });
    vi.stubGlobal("fetch", mockFetch);

    renderHook(() => useOperatorStream("sess-1", "run-1", REMOTE_HOST));

    await waitFor(() => expect(mockFetch).toHaveBeenCalledOnce());
    expect(MockEventSource.instances).toHaveLength(0);
  });

  it("sends Authorization header for remote host, token NOT in URL (issue #32)", async () => {
    const mockFetch = vi.fn().mockResolvedValue({
      ok: true,
      body: makeSseStream([]),
    });
    vi.stubGlobal("fetch", mockFetch);

    renderHook(() => useOperatorStream("sess-1", "run-1", REMOTE_HOST));

    await waitFor(() => expect(mockFetch).toHaveBeenCalledOnce());
    const [calledUrl, calledInit] = mockFetch.mock.calls[0] as [string, RequestInit];
    expect(calledUrl).toContain("xyz.ngrok.io");
    expect(calledUrl).not.toContain("token=");
    expect(calledUrl).not.toContain("remote-tok");
    const headers = calledInit.headers as Record<string, string>;
    expect(headers["Authorization"]).toBe("Bearer remote-tok");
  });

  it("omits Authorization header for remote host when token is empty", async () => {
    const mockFetch = vi.fn().mockResolvedValue({
      ok: true,
      body: makeSseStream([]),
    });
    vi.stubGlobal("fetch", mockFetch);

    renderHook(() => useOperatorStream("sess-1", "run-1", REMOTE_HOST_NO_TOKEN));

    await waitFor(() => expect(mockFetch).toHaveBeenCalledOnce());
    const [, calledInit] = mockFetch.mock.calls[0] as [string, RequestInit];
    expect(calledInit.headers).toBeUndefined();
  });

  it("parses SSE frames from fetch response body and updates state", async () => {
    const stream = makeSseStream([
      { type: "text", content: "line 1" },
      { type: "text", content: "line 2" },
      { type: "status", status: "done", exit_code: 0 },
    ]);

    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, body: stream }));

    const { result } = renderHook(() => useOperatorStream("sess-1", "run-1", REMOTE_HOST));

    await waitFor(() => expect(result.current.status).toBe("done"), { timeout: 2000 });

    expect(result.current.frames).toHaveLength(2);
    expect(result.current.exitCode).toBe(0);
  });

  it("sets status to error when remote stream closes without terminal status frame", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        body: makeSseStream([{ type: "text", content: "partial output" }]),
      })
    );

    const { result } = renderHook(() => useOperatorStream("sess-1", "run-1", REMOTE_HOST));

    await waitFor(() => expect(result.current.status).toBe("error"), { timeout: 2000 });
    expect(result.current.frames).toHaveLength(1);
    expect(result.current.exitCode).toBeNull();
  });

  it("sets status to error when fetch response is not ok", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: false, status: 401, body: null }));

    const { result } = renderHook(() => useOperatorStream("sess-1", "run-1", REMOTE_HOST));

    await waitFor(() => expect(result.current.status).toBe("error"), { timeout: 2000 });
  });

  it("sets status to error when fetch rejects", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("Network error")));

    const { result } = renderHook(() => useOperatorStream("sess-1", "run-1", REMOTE_HOST));

    await waitFor(() => expect(result.current.status).toBe("error"), { timeout: 2000 });
  });

  it("AbortError from cancelled fetch does not set error status", async () => {
    // Simulate an abort
    const abortErr = new DOMException("Aborted", "AbortError");
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(abortErr));

    const { result, unmount } = renderHook(() => useOperatorStream("sess-1", "run-1", REMOTE_HOST));

    // Unmounting triggers abort, which causes the AbortError — status should stay "connecting"
    unmount();

    // Status should NOT flip to "error" on abort
    expect(result.current.status).not.toBe("error");
  });

  it("sets status to error on terminal status frame with non-done status", async () => {
    const stream = makeSseStream([{ type: "status", status: "error", exit_code: 1 }]);

    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, body: stream }));

    const { result } = renderHook(() => useOperatorStream("sess-1", "run-1", REMOTE_HOST));

    await waitFor(() => expect(result.current.status).toBe("error"), { timeout: 2000 });
    expect(result.current.exitCode).toBe(1);
  });
});
