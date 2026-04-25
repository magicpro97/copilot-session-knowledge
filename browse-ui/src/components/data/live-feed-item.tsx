"use client";

import type { LiveEvent } from "@/lib/api/types";
import { Badge } from "@/components/ui/badge";
import { TimeRelative } from "@/components/data/session-badges";

type LiveFeedItemProps = {
  event: LiveEvent;
};

export function LiveFeedItem({ event }: LiveFeedItemProps) {
  return (
    <article className="space-y-2 rounded-xl border bg-card p-3">
      <header className="flex flex-wrap items-center gap-2 text-xs">
        <TimeRelative value={event.created_at} />
        <Badge variant="secondary">{event.category || "unknown"}</Badge>
        <Badge variant="outline" className="font-mono">
          {event.wing || "—"} · {event.room || "—"}
        </Badge>
        <span className="ml-auto font-mono text-muted-foreground">#{event.id}</span>
      </header>
      <p className="text-sm leading-5 text-foreground">{event.title || "Untitled event"}</p>
    </article>
  );
}
