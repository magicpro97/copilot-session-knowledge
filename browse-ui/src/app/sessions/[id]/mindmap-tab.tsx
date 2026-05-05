import { useQuery } from "@tanstack/react-query";

import { Banner } from "@/components/data/banner";
import { EmptyState } from "@/components/data/empty-state";
import { MindmapViewer } from "@/components/data/mindmap-viewer";
import { hostFetch } from "@/lib/api/client";
import { mindmapResponseSchema } from "@/lib/api/schemas";
import type { HostProfile } from "@/lib/api/types";

type MindmapTabProps = {
  sessionId: string;
  active: boolean;
  host: HostProfile;
};

function headingCount(markdown: string): number {
  const matches = markdown.match(/^#{1,6}\s+\S+/gm);
  return matches ? matches.length : 0;
}

export function MindmapTab({ sessionId, active, host }: MindmapTabProps) {
  const query = useQuery({
    queryKey: ["session-mindmap", host.id, sessionId],
    enabled: Boolean(sessionId) && active,
    queryFn: async () => {
      const encoded = encodeURIComponent(sessionId);
      const data = await hostFetch(`/api/session/${encoded}/mindmap`, host);
      return mindmapResponseSchema.parse(data);
    },
  });

  if (!active) return null;

  if (query.isLoading) {
    return (
      <div className="border-border text-muted-foreground rounded-xl border p-4 text-sm">
        Loading mindmap...
      </div>
    );
  }

  if (query.error) {
    return (
      <Banner
        tone="danger"
        title="Failed to load mindmap"
        description={query.error instanceof Error ? query.error.message : "Unknown error"}
      />
    );
  }

  if (!query.data) {
    return (
      <EmptyState
        title="Mindmap unavailable"
        description="No mindmap data returned for this session."
      />
    );
  }

  if (headingCount(query.data.markdown) <= 1) {
    return (
      <EmptyState
        title="Session has no heading structure"
        description="This session does not have enough headings to render a meaningful mindmap."
      />
    );
  }

  return <MindmapViewer markdown={query.data.markdown} title={query.data.title} />;
}
