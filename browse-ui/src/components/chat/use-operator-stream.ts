"use client";

import { useEffect, useRef, useState } from "react";
import type { CopilotStreamFrame, CopilotStatusFrame, HostProfile } from "@/lib/api/types";
import { createOperatorStreamPath, createOperatorStreamUrl } from "@/lib/api/hooks";
import { withLoopbackHint } from "@/lib/http/loopback";

export type StreamStatus = "idle" | "connecting" | "streaming" | "done" | "error";

export type UseOperatorStreamResult = {
  frames: CopilotStreamFrame[];
  status: StreamStatus;
  exitCode: number | null;
};

function parseSseData(raw: string): CopilotStreamFrame | null {
  try {
    return JSON.parse(raw) as CopilotStreamFrame;
  } catch {
    return null;
  }
}

/**
 * Consume the SSE stream from `/api/operator/sessions/{id}/stream?run=<id>`.
 *
 * Transport strategy:
 * - **Remote hosts** (`host.base_url` non-empty): uses `fetch` with an
 *   `Authorization: Bearer` header so the token never appears in the URL
 *   (fixes #32).  Parses SSE frames from the response body stream manually.
 * - **Local / same-origin hosts**: uses `EventSource` (cookies handle auth,
 *   no token in URL needed).
 *
 * Fast-resume (issue #60):
 * - Checkpoint events carry an SSE `id:` field containing an opaque single-use
 *   resume token (UUID4) issued by the server.
 * - On disconnect, the client retries once using that token via the
 *   `Last-Event-ID` header so the server can resume from the last checkpoint.
 * - If the token is absent, expired, or invalid the server gracefully restarts
 *   from index 0; the client deduplicates frames it already received using the
 *   `idx` field on each event frame.
 * - The token is NEVER placed in the URL — it travels only via HTTP headers.
 *
 * When `host` is provided the stream URL is resolved via
 * `createOperatorStreamUrl(host)` so that remote agents stream from their
 * own base URL rather than the same-origin `/api/operator/...` path.
 */
export function useOperatorStream(
  sessionId: string | null,
  runId: string | null,
  host?: HostProfile | null
): UseOperatorStreamResult {
  const [frames, setFrames] = useState<CopilotStreamFrame[]>([]);
  const [status, setStatus] = useState<StreamStatus>("idle");
  const [exitCode, setExitCode] = useState<number | null>(null);
  const esRef = useRef<EventSource | null>(null);

  // Fast-resume refs (issue #60)
  const lastEventIdRef = useRef<string>("");
  const maxSeenIdxRef = useRef<number>(-1);
  const retryCountRef = useRef<number>(0);

  useEffect(() => {
    if (!sessionId || !runId) {
      setFrames([]);
      setStatus("idle");
      setExitCode(null);
      lastEventIdRef.current = "";
      maxSeenIdxRef.current = -1;
      retryCountRef.current = 0;
      return;
    }

    setFrames([]);
    setStatus("connecting");
    setExitCode(null);
    lastEventIdRef.current = "";
    maxSeenIdxRef.current = -1;
    retryCountRef.current = 0;

    const isRemote = Boolean(host?.base_url);
    // At this point sessionId and runId are non-null (guarded above).
    const sid = sessionId;
    const rid = runId;

    /** Append a frame, deduplicating by idx when retrying. */
    function addFrame(frame: CopilotStreamFrame): void {
      if ("idx" in frame && frame.idx <= maxSeenIdxRef.current) return; // duplicate
      if ("idx" in frame) maxSeenIdxRef.current = frame.idx;
      setFrames((prev) => [...prev, frame]);
    }

    /**
     * Attempt a reconnect using the resume token (fetch-based for both paths).
     * Called at most once per effect lifecycle.
     */
    function attemptReconnect(): boolean {
      if (retryCountRef.current >= 1 || !lastEventIdRef.current) return false;
      retryCountRef.current++;
      setStatus("connecting");
      startFetchStream(lastEventIdRef.current);
      return true;
    }

    /** Start a fetch-based SSE connection, optionally with a resume token. */
    function startFetchStream(resumeToken?: string): AbortController {
      const url = host
        ? createOperatorStreamUrl(sid, rid, host)
        : createOperatorStreamPath(sid, rid);
      const controller = new AbortController();

      const authHeaders: Record<string, string> = {};
      if (isRemote && host?.token) {
        authHeaders["Authorization"] = `Bearer ${host.token}`;
      }
      // Resume token goes in Last-Event-ID header, never in the URL.
      if (resumeToken) {
        authHeaders["Last-Event-ID"] = resumeToken;
      }
      const headers = Object.keys(authHeaders).length > 0 ? authHeaders : undefined;

      fetch(url, withLoopbackHint(url, { headers, signal: controller.signal }))
        .then(async (res) => {
          if (!res.ok || !res.body) {
            setStatus("error");
            return;
          }
          setStatus("streaming");

          const reader = res.body.getReader();
          const decoder = new TextDecoder();
          let buffer = "";
          let sawTerminalStatus = false;
          let currentEventId = "";

          try {
            while (true) {
              const { done, value } = await reader.read();
              if (done) break;
              buffer += decoder.decode(value, { stream: true });
              const lines = buffer.split("\n");
              buffer = lines.pop() ?? "";
              for (const line of lines) {
                if (line.startsWith("id: ")) {
                  // Capture SSE id: field as the resume token for next reconnect.
                  currentEventId = line.slice(4).trim();
                  if (currentEventId) lastEventIdRef.current = currentEventId;
                  continue;
                }
                if (!line.startsWith("data: ")) continue;
                const data = line.slice(6).trim();
                if (!data || data === "[DONE]") continue;
                const frame = parseSseData(data);
                if (!frame) continue;
                if (frame.type === "status") {
                  sawTerminalStatus = true;
                  const statusFrame = frame as CopilotStatusFrame;
                  setExitCode(statusFrame.exit_code);
                  setStatus(statusFrame.status === "done" ? "done" : "error");
                  return;
                }
                addFrame(frame);
              }
            }
            if (!sawTerminalStatus && !controller.signal.aborted) {
              if (!attemptReconnect()) setStatus("error");
            }
          } catch (err) {
            if ((err as Error).name !== "AbortError") {
              if (!attemptReconnect()) setStatus("error");
            }
          }
        })
        .catch((err: unknown) => {
          if ((err as Error).name !== "AbortError") {
            if (!attemptReconnect()) setStatus("error");
          }
        });

      return controller;
    }

    if (isRemote && host) {
      // Remote host: use fetch with Authorization header so the token is never
      // visible in browser URLs or server access logs (fixes #32).
      const controller = startFetchStream();
      return () => {
        controller.abort();
      };
    }

    // Local / same-origin: use EventSource (cookies handle auth, no token in URL).
    const url = host ? createOperatorStreamUrl(sid, rid, host) : createOperatorStreamPath(sid, rid);
    const es = new EventSource(url);
    esRef.current = es;
    let fetchCleanup: (() => void) | null = null;

    es.onopen = () => setStatus("streaming");

    es.onmessage = (event) => {
      // EventSource tracks id: fields automatically; event.lastEventId reflects the
      // last seen SSE id: value — store it for potential reconnect.
      if (event.lastEventId) lastEventIdRef.current = event.lastEventId;

      let raw: unknown;
      try {
        raw = JSON.parse(event.data as string);
      } catch {
        return;
      }

      const frame = raw as CopilotStreamFrame;

      if (frame.type === "status") {
        const statusFrame = frame as CopilotStatusFrame;
        setExitCode(statusFrame.exit_code);
        setStatus(statusFrame.status === "done" ? "done" : "error");
        es.close();
        esRef.current = null;
        return;
      }

      addFrame(frame);
    };

    es.onerror = () => {
      es.close();
      esRef.current = null;
      // Attempt one reconnect via fetch if we have a resume token; otherwise error.
      if (lastEventIdRef.current && retryCountRef.current < 1) {
        retryCountRef.current++;
        setStatus("connecting");
        const ctrl = startFetchStream(lastEventIdRef.current);
        fetchCleanup = () => ctrl.abort();
      } else {
        setStatus("error");
      }
    };

    return () => {
      es.close();
      esRef.current = null;
      fetchCleanup?.();
    };
  }, [sessionId, runId, host]);

  return { frames, status, exitCode };
}
