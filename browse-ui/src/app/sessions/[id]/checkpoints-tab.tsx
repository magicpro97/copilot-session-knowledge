"use client";

import { useState } from "react";
import { useMutation } from "@tanstack/react-query";

import { Banner } from "@/components/data/banner";
import { DiffViewer } from "@/components/data/diff-viewer";
import { EmptyState } from "@/components/data/empty-state";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { apiFetch } from "@/lib/api/client";
import { diffResultSchema } from "@/lib/api/schemas";

type CheckpointsTabProps = {
  sessionId: string;
};

export function CheckpointsTab({ sessionId }: CheckpointsTabProps) {
  const [fromSelector, setFromSelector] = useState("first");
  const [toSelector, setToSelector] = useState("latest");

  const diffMutation = useMutation({
    mutationFn: async (payload: { from: string; to: string }) => {
      const query = new URLSearchParams({
        session: sessionId,
        from: payload.from,
        to: payload.to,
      });
      const data = await apiFetch(`/api/diff?${query.toString()}`);
      return diffResultSchema.parse(data);
    },
  });

  return (
    <div className="space-y-4">
      <Banner
        tone="warning"
        title="Checkpoint list endpoint is unavailable"
        description="This API supports diff execution but does not expose checkpoint options. Enter selectors manually (e.g., first, latest, or a sequence number)."
      />

      <div className="grid gap-3 rounded-xl border border-border p-3 md:grid-cols-[1fr_1fr_auto]">
        <div className="space-y-1">
          <label className="text-xs font-medium text-muted-foreground" htmlFor="diff-from">
            From
          </label>
          <Input
            id="diff-from"
            value={fromSelector}
            onChange={(event) => setFromSelector(event.target.value)}
            placeholder="first"
          />
        </div>
        <div className="space-y-1">
          <label className="text-xs font-medium text-muted-foreground" htmlFor="diff-to">
            To
          </label>
          <Input
            id="diff-to"
            value={toSelector}
            onChange={(event) => setToSelector(event.target.value)}
            placeholder="latest"
          />
        </div>
        <div className="flex items-end">
          <Button
            onClick={() => diffMutation.mutate({ from: fromSelector.trim(), to: toSelector.trim() })}
            disabled={!fromSelector.trim() || !toSelector.trim() || diffMutation.isPending}
          >
            {diffMutation.isPending ? "Loading diff..." : "Compute diff"}
          </Button>
        </div>
      </div>

      {diffMutation.error ? (
        <Banner
          tone="danger"
          title="Diff request failed"
          description={
            diffMutation.error instanceof Error ? diffMutation.error.message : "Unknown diff error"
          }
        />
      ) : null}

      {diffMutation.data ? (
        <DiffViewer result={diffMutation.data} />
      ) : (
        <EmptyState
          title="No diff loaded"
          description="Select checkpoints and run diff to inspect changes."
          className="min-h-36"
        />
      )}
    </div>
  );
}
