"use client";

import { ExternalLink } from "lucide-react";

import type { GraphNode } from "@/lib/api/types";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

type NodeDetailCardProps = {
  node: GraphNode;
  onOpenSearch: (node: GraphNode) => void;
};

export function NodeDetailCard({ node, onOpenSearch }: NodeDetailCardProps) {
  return (
    <Card>
      <CardHeader className="space-y-1">
        <CardTitle className="text-sm">Node Detail</CardTitle>
        <p className="text-xs text-muted-foreground">{node.kind === "entry" ? "Entry" : "Entity"}</p>
      </CardHeader>
      <CardContent className="space-y-3 text-sm">
        <div>
          <p className="text-xs uppercase tracking-wide text-muted-foreground">Label</p>
          <p className="break-words">{node.label}</p>
        </div>

        {node.category ? (
          <div>
            <p className="text-xs uppercase tracking-wide text-muted-foreground">Category</p>
            <p className="break-words">{node.category}</p>
          </div>
        ) : null}

        {node.wing ? (
          <div>
            <p className="text-xs uppercase tracking-wide text-muted-foreground">Wing</p>
            <p>{node.wing}</p>
          </div>
        ) : null}

        {node.room ? (
          <div>
            <p className="text-xs uppercase tracking-wide text-muted-foreground">Room</p>
            <p>{node.room}</p>
          </div>
        ) : null}

        <Button type="button" variant="outline" size="sm" onClick={() => onOpenSearch(node)}>
          <ExternalLink className="size-3.5" />
          Open in Search
        </Button>
      </CardContent>
    </Card>
  );
}
