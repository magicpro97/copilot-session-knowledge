"use client";

import {
  useEffect,
  useRef,
  useState,
  useCallback,
  useReducer,
  type Dispatch,
  type MutableRefObject,
} from "react";
import type { LiveEvent } from "@/lib/api/types";
import { liveEventSchema } from "@/lib/api/schemas";
import { withLoopbackHint } from "@/lib/http/loopback";

type SseTransport = "eventsource" | "fetch";

type UseSseOptions = {
  enabled?: boolean;
  transport?: SseTransport;
  authToken?: string;
};

/**
 * Maximum number of live events retained in the in-memory buffer.
 * Excess events are dropped (count-only, no payloads retained) and surfaced
 * via `dropped`/`capped` from {@link useSSE} so the UI can warn the operator
 * instead of silently truncating history.
 */
export const LIVE_EVENT_BUFFER_LIMIT = 200;

type BufferState = {
  events: LiveEvent[];
  dropped: number;
};

type BufferAction = { type: "push"; event: LiveEvent } | { type: "clear" };

const initialBufferState: BufferState = { events: [], dropped: 0 };

/**
 * Pure reducer for the live-event buffer. Both `events` and `dropped` live in
 * a single state so overflow accounting happens atomically without calling a
 * setter inside another setter's updater (which would double-count under
 * React StrictMode's intentional double-invocation of updaters in dev).
 */
function bufferReducer(state: BufferState, action: BufferAction): BufferState {
  switch (action.type) {
    case "push": {
      const next = [action.event, ...state.events];
      if (next.length > LIVE_EVENT_BUFFER_LIMIT) {
        // Drop accounting is O(1): only counts are retained — never payloads
        // or identifiers from the dropped events.
        const overflow = next.length - LIVE_EVENT_BUFFER_LIMIT;
        next.length = LIVE_EVENT_BUFFER_LIMIT;
        return { events: next, dropped: state.dropped + overflow };
      }
      return { events: next, dropped: state.dropped };
    }
    case "clear":
      return initialBufferState;
    default:
      return state;
  }
}

function pushEvent(
  raw: unknown,
  pausedRef: MutableRefObject<boolean>,
  dispatch: Dispatch<BufferAction>
) {
  if (pausedRef.current) return;
  const parsed = liveEventSchema.safeParse(raw);
  if (!parsed.success) return;
  dispatch({ type: "push", event: parsed.data });
}

export function useSSE(url: string, options?: UseSseOptions) {
  const [{ events, dropped }, dispatch] = useReducer(bufferReducer, initialBufferState);
  const [status, setStatus] = useState<"connecting" | "open" | "closed">("connecting");
  const [paused, setPaused] = useState(false);
  const esRef = useRef<EventSource | null>(null);
  const pausedRef = useRef(false);
  const previousUrlRef = useRef<string | null>(null);

  const toggle = useCallback(() => {
    setPaused((prev) => {
      const next = !prev;
      pausedRef.current = next;
      return next;
    });
  }, []);

  const clear = useCallback(() => {
    dispatch({ type: "clear" });
  }, []);

  useEffect(() => {
    if (previousUrlRef.current !== url) {
      dispatch({ type: "clear" });
      previousUrlRef.current = url;
    }
    if (options?.enabled === false) {
      setStatus("closed");
      return;
    }
    setStatus("connecting");
    if (options?.transport === "fetch") {
      const controller = new AbortController();

      void (async () => {
        try {
          const headers: Record<string, string> = {};
          if (options.authToken) {
            headers["Authorization"] = `Bearer ${options.authToken}`;
          }
          const res = await fetch(
            url,
            withLoopbackHint(url, {
              headers,
              signal: controller.signal,
            })
          );
          if (!res.ok || !res.body) {
            setStatus("closed");
            return;
          }

          setStatus("open");
          const reader = res.body.getReader();
          const decoder = new TextDecoder();
          let buffer = "";

          while (true) {
            const { done, value } = await reader.read();
            if (done) break;
            buffer += decoder.decode(value, { stream: true });
            const parts = buffer.split("\n\n");
            buffer = parts.pop() ?? "";

            for (const part of parts) {
              const dataLines = part
                .split("\n")
                .filter((line) => line.startsWith("data:"))
                .map((line) => line.slice(5).trimStart());
              if (dataLines.length === 0) continue;
              let raw: unknown;
              try {
                raw = JSON.parse(dataLines.join("\n"));
              } catch {
                continue;
              }
              pushEvent(raw, pausedRef, dispatch);
            }
          }

          if (!controller.signal.aborted) {
            setStatus("closed");
          }
        } catch (err) {
          if ((err as Error).name !== "AbortError") {
            setStatus("closed");
          }
        }
      })();

      return () => {
        controller.abort();
        setStatus("closed");
      };
    }

    const es = new EventSource(url);
    esRef.current = es;
    es.onopen = () => setStatus("open");
    es.onmessage = (e) => {
      let raw: unknown;
      try {
        raw = JSON.parse(e.data);
      } catch {
        return;
      }
      pushEvent(raw, pausedRef, dispatch);
    };
    es.onerror = () => {
      setStatus(es.readyState === EventSource.CLOSED ? "closed" : "connecting");
    };
    return () => {
      es.close();
      esRef.current = null;
      setStatus("closed");
    };
  }, [url, options?.authToken, options?.enabled, options?.transport]);

  return {
    events,
    status,
    paused,
    toggle,
    dropped,
    capped: dropped > 0,
    bufferLimit: LIVE_EVENT_BUFFER_LIMIT,
    clear,
  };
}
