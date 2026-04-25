import { useEffect, useMemo, useState } from "react";
import { Pause, Play } from "lucide-react";

import type { TimelineEvent } from "@/lib/api/types";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

type TimelinePlayerProps = {
  events: TimelineEvent[];
  total: number;
  active?: boolean;
};

const SPEEDS = [1, 2, 4] as const;

function isTypingTarget(target: EventTarget | null) {
  const element = target as HTMLElement | null;
  if (!element) return false;
  const tag = element.tagName.toLowerCase();
  return (
    element.isContentEditable || tag === "input" || tag === "textarea" || tag === "select"
  );
}

export function TimelinePlayer({ events, total, active = false }: TimelinePlayerProps) {
  const [index, setIndex] = useState(0);
  const [isPlaying, setIsPlaying] = useState(false);
  const [speed, setSpeed] = useState<(typeof SPEEDS)[number]>(1);

  const current = events[index];
  const eventCount = events.length;
  const isTruncated = total > eventCount;

  useEffect(() => {
    setIndex((prev) => Math.min(prev, Math.max(eventCount - 1, 0)));
  }, [eventCount]);

  useEffect(() => {
    if (!isPlaying || eventCount <= 1) return;
    const intervalMs = 900 / speed;
    const timer = window.setInterval(() => {
      setIndex((prev) => {
        if (prev >= eventCount - 1) {
          setIsPlaying(false);
          return prev;
        }
        return prev + 1;
      });
    }, intervalMs);
    return () => window.clearInterval(timer);
  }, [eventCount, isPlaying, speed]);

  useEffect(() => {
    if (!active) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.defaultPrevented || isTypingTarget(event.target)) return;
      if (event.key === " ") {
        event.preventDefault();
        setIsPlaying((prev) => !prev);
        return;
      }
      if (event.key === "ArrowLeft") {
        event.preventDefault();
        setIndex((prev) => Math.max(prev - 1, 0));
        setIsPlaying(false);
        return;
      }
      if (event.key === "ArrowRight") {
        event.preventDefault();
        setIndex((prev) => Math.min(prev + 1, eventCount - 1));
        setIsPlaying(false);
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [active, eventCount]);

  const uniqueLegend = useMemo(() => {
    const map = new Map<string, string>();
    for (const event of events) {
      if (!map.has(event.color)) {
        map.set(event.color, event.kind || "unknown");
      }
    }
    return [...map.entries()];
  }, [events]);

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader className="space-y-2">
          <CardTitle>Timeline replay</CardTitle>
          <div className="flex flex-wrap items-center gap-2">
            <Button variant="outline" onClick={() => setIsPlaying((prev) => !prev)}>
              {isPlaying ? <Pause className="size-4" /> : <Play className="size-4" />}
              {isPlaying ? "Pause" : "Play"}
            </Button>

            <select
              aria-label="Playback speed"
              value={speed}
              onChange={(event) => setSpeed(Number(event.target.value) as (typeof SPEEDS)[number])}
              className="h-8 rounded-lg border border-input bg-background px-2 text-sm"
            >
              {SPEEDS.map((value) => (
                <option key={value} value={value}>
                  {value}x
                </option>
              ))}
            </select>

            <span className="text-sm text-muted-foreground">
              Event {index + 1} / {eventCount}
            </span>
            {isTruncated ? (
              <span className="text-sm text-muted-foreground">showing {eventCount} of {total}</span>
            ) : null}
          </div>
        </CardHeader>
        <CardContent className="space-y-3">
          {events.length >= 5 ? (
            <div className="flex gap-1">
              {events.map((event, eventIndex) => (
                <button
                  key={event.event_id}
                  className="h-3 min-w-0 flex-1 rounded-sm"
                  style={{
                    backgroundColor: event.color,
                    opacity: eventIndex === index ? 1 : 0.45,
                    outline: eventIndex === index ? "1px solid hsl(var(--foreground))" : undefined,
                  }}
                  onClick={() => setIndex(eventIndex)}
                  title={`Event ${eventIndex + 1}: ${event.kind}`}
                />
              ))}
            </div>
          ) : null}

          <input
            type="range"
            min={0}
            max={Math.max(eventCount - 1, 0)}
            value={index}
            className="w-full"
            onChange={(event) => setIndex(Number(event.target.value))}
          />
        </CardContent>
      </Card>

      {uniqueLegend.length > 1 ? (
        <div className="flex flex-wrap items-center gap-3 text-xs text-muted-foreground">
          {uniqueLegend.map(([color, label]) => (
            <span key={`${color}-${label}`} className="inline-flex items-center gap-1.5">
              <span className="size-2 rounded-full" style={{ backgroundColor: color }} />
              {label}
            </span>
          ))}
        </div>
      ) : null}

      <Card>
        <CardHeader>
          <CardTitle className="text-base">
            Event {index + 1}: {current?.kind || "unknown"}
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-2 text-sm">
          <p className="text-xs text-muted-foreground">
            byte_offset: {String(current?.byte_offset ?? "—")} · file_mtime:{" "}
            {current?.file_mtime || "—"}
          </p>
          <pre className="max-h-80 overflow-auto whitespace-pre-wrap rounded-lg bg-muted/40 p-3 text-xs">
            {current?.preview || "(no preview available)"}
          </pre>
        </CardContent>
      </Card>
    </div>
  );
}
