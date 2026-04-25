"use client";

import { useEffect, useRef, useState, useCallback } from "react";
import type { LiveEvent } from "@/lib/api/types";

export function useSSE(url: string, options?: { enabled?: boolean }) {
  const [events, setEvents] = useState<LiveEvent[]>([]);
  const [status, setStatus] = useState<"connecting" | "open" | "closed">(
    "connecting"
  );
  const esRef = useRef<EventSource | null>(null);
  const pausedRef = useRef(false);

  const toggle = useCallback(() => {
    pausedRef.current = !pausedRef.current;
  }, []);

  useEffect(() => {
    if (options?.enabled === false) return;
    const es = new EventSource(url);
    esRef.current = es;
    es.onopen = () => setStatus("open");
    es.onmessage = (e) => {
      if (pausedRef.current) return;
      const data: LiveEvent = JSON.parse(e.data) as LiveEvent;
      setEvents((prev) => [data, ...prev].slice(0, 200));
    };
    es.onerror = () => setStatus("closed");
    return () => {
      es.close();
      setStatus("closed");
    };
  }, [url, options?.enabled]);

  return { events, status, toggle };
}
