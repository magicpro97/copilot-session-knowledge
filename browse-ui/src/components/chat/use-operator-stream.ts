"use client";

import { useEffect, useRef, useState } from "react";
import type { CopilotStreamFrame, CopilotStatusFrame, HostProfile } from "@/lib/api/types";
import { createOperatorStreamPath, createOperatorStreamUrl } from "@/lib/api/hooks";

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

  useEffect(() => {
    if (!sessionId || !runId) {
      setFrames([]);
      setStatus("idle");
      setExitCode(null);
      return;
    }

    setFrames([]);
    setStatus("connecting");
    setExitCode(null);

    const isRemote = Boolean(host?.base_url);

    if (isRemote && host) {
      // Remote host: use fetch with Authorization header so the token is never
      // visible in browser URLs or server access logs (fixes #32).
      const url = createOperatorStreamUrl(sessionId, runId, host);
      const controller = new AbortController();
      const headers = host.token ? { Authorization: `Bearer ${host.token}` } : undefined;

      fetch(url, {
        headers,
        signal: controller.signal,
      })
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

          try {
            while (true) {
              const { done, value } = await reader.read();
              if (done) break;
              buffer += decoder.decode(value, { stream: true });
              const lines = buffer.split("\n");
              buffer = lines.pop() ?? "";
              for (const line of lines) {
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
                setFrames((prev) => [...prev, frame]);
              }
            }
            if (!sawTerminalStatus && !controller.signal.aborted) {
              setStatus("error");
            }
          } catch (err) {
            if ((err as Error).name !== "AbortError") setStatus("error");
          }
        })
        .catch((err: unknown) => {
          if ((err as Error).name !== "AbortError") setStatus("error");
        });

      return () => {
        controller.abort();
      };
    }

    // Local / same-origin: use EventSource (cookies handle auth, no token in URL).
    const url = host
      ? createOperatorStreamUrl(sessionId, runId, host)
      : createOperatorStreamPath(sessionId, runId);
    const es = new EventSource(url);
    esRef.current = es;

    es.onopen = () => setStatus("streaming");

    es.onmessage = (event) => {
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

      setFrames((prev) => [...prev, frame]);
    };

    es.onerror = () => {
      setStatus("error");
      es.close();
      esRef.current = null;
    };

    return () => {
      es.close();
      esRef.current = null;
    };
  }, [sessionId, runId, host]);

  return { frames, status, exitCode };
}
