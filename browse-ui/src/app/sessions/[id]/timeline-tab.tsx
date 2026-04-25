import { useQuery } from "@tanstack/react-query";

import { Banner } from "@/components/data/banner";
import { EmptyState } from "@/components/data/empty-state";
import { TimelinePlayer } from "@/components/data/timeline-player";
import { apiFetch } from "@/lib/api/client";
import { timelineEventsResponseSchema } from "@/lib/api/schemas";

type TimelineTabProps = {
  sessionId: string;
  active: boolean;
};

export function TimelineTab({ sessionId, active }: TimelineTabProps) {
  const query = useQuery({
    queryKey: ["session-timeline-events", sessionId],
    enabled: Boolean(sessionId),
    queryFn: async () => {
      const encoded = encodeURIComponent(sessionId);
      const data = await apiFetch(`/api/session/${encoded}/events?from=0&limit=200`);
      return timelineEventsResponseSchema.parse(data);
    },
  });

  if (query.isLoading) {
    return (
      <div className="rounded-xl border border-border p-4 text-sm text-muted-foreground">
        Loading timeline events...
      </div>
    );
  }

  if (query.error) {
    return (
      <Banner
        tone="danger"
        title="Failed to load timeline events"
        description={query.error instanceof Error ? query.error.message : "Unknown error"}
      />
    );
  }

  if (!query.data || query.data.events.length === 0) {
    return (
      <EmptyState
        title="No timeline events"
        description="No event offsets were available for this session."
      />
    );
  }

  return <TimelinePlayer events={query.data.events} total={query.data.total} active={active} />;
}
