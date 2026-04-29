"use client";

import { CircleDot } from "lucide-react";

import { LiveFeedItem } from "@/components/data/live-feed-item";
import { EmptyState } from "@/components/data/empty-state";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { useSSE } from "@/hooks/use-sse";

type LiveTabProps = {
  active?: boolean;
};

function connectionLabel(status: "connecting" | "open" | "closed"): string {
  if (status === "open") return "Connected";
  if (status === "connecting") return "Connecting";
  return "Disconnected";
}

function connectionTone(status: "connecting" | "open" | "closed"): string {
  if (status === "open") return "text-[hsl(142_72%_38%)]";
  if (status === "connecting") return "text-[hsl(215_90%_45%)]";
  return "text-[hsl(12_76%_46%)]";
}

export function LiveTab({ active = true }: LiveTabProps) {
  const { events, status, paused, toggle } = useSSE("/api/live", { enabled: active });

  return (
    <section className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <Badge variant="outline" className="gap-1.5">
          <CircleDot className={`size-3.5 ${connectionTone(status)}`} />
          {connectionLabel(status)}
        </Badge>
        <Button type="button" variant="outline" size="sm" onClick={toggle}>
          {paused ? "Resume" : "Pause"}
        </Button>
        <span className="text-muted-foreground text-xs">
          {paused ? "Paused: incoming events are intentionally dropped." : "Receiving live events."}
        </span>
      </div>

      {events.length === 0 ? (
        <EmptyState
          title="No live events yet"
          description="This stream updates when new knowledge entries are written."
        />
      ) : (
        <div className="space-y-2">
          {events.map((event) => (
            <LiveFeedItem key={event.id} event={event} />
          ))}
        </div>
      )}
    </section>
  );
}
