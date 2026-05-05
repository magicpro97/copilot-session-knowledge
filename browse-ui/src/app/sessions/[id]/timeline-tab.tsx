import { useQuery } from "@tanstack/react-query";

import { Banner } from "@/components/data/banner";
import { EmptyState } from "@/components/data/empty-state";
import { TimelinePlayer } from "@/components/data/timeline-player";
import { hostFetch } from "@/lib/api/client";
import { timelineEventsResponseSchema } from "@/lib/api/schemas";
import type { HostProfile } from "@/lib/api/types";

type TimelineTabProps = {
  sessionId: string;
  active: boolean;
  host: HostProfile;
};

export function TimelineTab({ sessionId, active, host }: TimelineTabProps) {
  const query = useQuery({
    queryKey: ["session-timeline-events", host.id, sessionId],
    enabled: Boolean(sessionId),
    queryFn: async () => {
      const encoded = encodeURIComponent(sessionId);
      const data = await hostFetch(`/api/session/${encoded}/events?from=0&limit=200`, host);
      return timelineEventsResponseSchema.parse(data);
    },
  });

  if (query.isLoading) {
    return (
      <div className="border-border text-muted-foreground rounded-xl border p-4 text-sm">
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
