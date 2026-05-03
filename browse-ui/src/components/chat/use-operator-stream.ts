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

/**
 * Consume the SSE stream from `/api/operator/sessions/{id}/stream?run=<id>`.
 * Opens an EventSource when both sessionId and runId are non-null, parses
 * typed CopilotStreamFrame JSON from each message, and closes on terminal
 * `{ type: "status" }` frame.
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
