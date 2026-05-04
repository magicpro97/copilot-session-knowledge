"use client";

import {
  useEffect,
  useRef,
  useState,
  useCallback,
  type Dispatch,
  type MutableRefObject,
  type SetStateAction,
} from "react";
import type { LiveEvent } from "@/lib/api/types";
import { liveEventSchema } from "@/lib/api/schemas";

type SseTransport = "eventsource" | "fetch";

type UseSseOptions = {
  enabled?: boolean;
  transport?: SseTransport;
  authToken?: string;
};

function pushEvent(
  raw: unknown,
  pausedRef: MutableRefObject<boolean>,
  setEvents: Dispatch<SetStateAction<LiveEvent[]>>
) {
  if (pausedRef.current) return;
  const parsed = liveEventSchema.safeParse(raw);
  if (!parsed.success) return;
  setEvents((prev) => [parsed.data, ...prev].slice(0, 200));
}

export function useSSE(url: string, options?: UseSseOptions) {
  const [events, setEvents] = useState<LiveEvent[]>([]);
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

  useEffect(() => {
    if (previousUrlRef.current !== url) {
      setEvents([]);
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
          const res = await fetch(url, {
            headers,
            signal: controller.signal,
          });
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
              pushEvent(raw, pausedRef, setEvents);
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
      pushEvent(raw, pausedRef, setEvents);
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

  return { events, status, paused, toggle };
}
