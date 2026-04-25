"use client";

import { useEffect, useRef, useState, useCallback } from "react";
import type { LiveEvent } from "@/lib/api/types";
import { liveEventSchema } from "@/lib/api/schemas";

export function useSSE(url: string, options?: { enabled?: boolean }) {
  const [events, setEvents] = useState<LiveEvent[]>([]);
  const [status, setStatus] = useState<"connecting" | "open" | "closed">(
    "connecting"
  );
  const [paused, setPaused] = useState(false);
  const esRef = useRef<EventSource | null>(null);
  const pausedRef = useRef(false);

  const toggle = useCallback(() => {
    setPaused((prev) => {
      const next = !prev;
      pausedRef.current = next;
      return next;
    });
  }, []);

  useEffect(() => {
    if (options?.enabled === false) {
      setStatus("closed");
      return;
    }
    setStatus("connecting");
    const es = new EventSource(url);
    esRef.current = es;
    es.onopen = () => setStatus("open");
    es.onmessage = (e) => {
      if (pausedRef.current) return;
      let raw: unknown;
      try {
        raw = JSON.parse(e.data);
      } catch {
        return;
      }
      const parsed = liveEventSchema.safeParse(raw);
      if (!parsed.success) return;
      setEvents((prev) => [parsed.data, ...prev].slice(0, 200));
    };
    es.onerror = () => {
      setStatus(
        es.readyState === EventSource.CLOSED ? "closed" : "connecting"
      );
    };
    return () => {
      es.close();
      esRef.current = null;
      setStatus("closed");
    };
  }, [url, options?.enabled]);

  return { events, status, paused, toggle };
}
