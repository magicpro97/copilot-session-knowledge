"use client";

import { ExternalLink } from "lucide-react";

import type { EvidenceEdge, GraphNode } from "@/lib/api/types";
import {
  formatConfidence,
  relationTypeColor,
  relationTypeLabel,
} from "@/components/data/evidence-relations";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

export type ConnectedEvidenceItem = {
  edge: EvidenceEdge;
  node: GraphNode;
};

type NodeDetailCardProps = {
  node: GraphNode;
  connectedEvidence?: ConnectedEvidenceItem[];
  onSelectRelatedNode?: (node: GraphNode) => void;
  onOpenSearch: (node: GraphNode) => void;
};

export function NodeDetailCard({
  node,
  connectedEvidence = [],
  onSelectRelatedNode,
  onOpenSearch,
}: NodeDetailCardProps) {
  return (
    <Card>
      <CardHeader className="space-y-1">
        <CardTitle className="text-sm">Node Detail</CardTitle>
        <p className="text-muted-foreground text-xs">
          {node.kind === "entry" ? "Entry" : "Entity"}
        </p>
      </CardHeader>
      <CardContent className="space-y-3 text-sm">
        <div>
          <p className="text-muted-foreground text-xs tracking-wide uppercase">Label</p>
          <p className="break-words">{node.label}</p>
        </div>

        {node.category ? (
          <div>
            <p className="text-muted-foreground text-xs tracking-wide uppercase">Category</p>
            <p className="break-words">{node.category}</p>
          </div>
        ) : null}

        {node.wing ? (
          <div>
            <p className="text-muted-foreground text-xs tracking-wide uppercase">Wing</p>
            <p>{node.wing}</p>
          </div>
        ) : null}

        {node.room ? (
          <div>
            <p className="text-muted-foreground text-xs tracking-wide uppercase">Room</p>
            <p>{node.room}</p>
          </div>
        ) : null}

        <div className="space-y-2">
          <p className="text-muted-foreground text-xs tracking-wide uppercase">Evidence links</p>
          {connectedEvidence.length > 0 ? (
            <ul className="space-y-2">
              {connectedEvidence.map(({ edge, node: relatedNode }) => (
                <li
                  key={`${edge.source}:${edge.target}:${edge.relation_type}`}
                  className="rounded-md border p-2"
                >
                  <div className="flex items-start gap-2">
                    <span
                      className="mt-1 inline-block size-2 rounded-full"
                      style={{ backgroundColor: relationTypeColor(edge.relation_type) }}
                      aria-hidden
                    />
                    <div className="min-w-0 space-y-1">
                      <Button
                        type="button"
                        variant="link"
                        className="h-auto p-0 text-left"
                        onClick={() => onSelectRelatedNode?.(relatedNode)}
                      >
                        {relatedNode.label}
                      </Button>
                      <p className="text-muted-foreground text-xs">
                        {relationTypeLabel(edge.relation_type)} · confidence{" "}
                        {formatConfidence(edge.confidence)}
                      </p>
                    </div>
                  </div>
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-muted-foreground text-xs">
              No connected evidence edges for this node in the current view.
            </p>
          )}
        </div>

        <Button type="button" variant="outline" size="sm" onClick={() => onOpenSearch(node)}>
          <ExternalLink className="size-3.5" />
          Open in Search
        </Button>
      </CardContent>
    </Card>
  );
}
