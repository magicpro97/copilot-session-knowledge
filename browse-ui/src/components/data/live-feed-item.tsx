"use client";

import type { LiveEvent } from "@/lib/api/types";
import { Badge } from "@/components/ui/badge";
import { TimeRelative } from "@/components/data/session-badges";

type LiveFeedItemProps = {
  event: LiveEvent;
};

export function LiveFeedItem({ event }: LiveFeedItemProps) {
  return (
    <article className="bg-card space-y-2 rounded-xl border p-3">
      <header className="flex flex-wrap items-center gap-2 text-xs">
        <TimeRelative value={event.created_at} />
        <Badge variant="secondary">{event.category || "unknown"}</Badge>
        <Badge variant="outline" className="font-mono">
          {event.wing || "—"} · {event.room || "—"}
        </Badge>
        <span className="text-muted-foreground ml-auto font-mono">#{event.id}</span>
      </header>
      <p className="text-foreground text-sm leading-5">{event.title || "Untitled event"}</p>
    </article>
  );
}
