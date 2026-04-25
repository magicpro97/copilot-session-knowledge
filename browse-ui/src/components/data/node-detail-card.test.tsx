import { render, screen } from "@testing-library/react";
import { fireEvent } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { NodeDetailCard } from "@/components/data/node-detail-card";
import type { GraphNode } from "@/lib/api/types";

describe("NodeDetailCard", () => {
  const node: GraphNode = {
    id: "n1",
    kind: "entry",
    label: "Root node",
    category: "pattern",
    wing: "copilot",
    room: "graph",
    color: "#fff",
  };

  it("renders connected evidence and supports navigation callbacks", async () => {
    const relatedNode: GraphNode = {
      id: "n2",
      kind: "entry",
      label: "Related node",
      category: "decision",
      wing: "copilot",
      room: "graph",
      color: "#000",
    };
    const onSelectRelatedNode = vi.fn();
    const onOpenSearch = vi.fn();

    render(
      <NodeDetailCard
        node={node}
        connectedEvidence={[
          {
            edge: {
              source: "n1",
              target: "n2",
              relation_type: "RESOLVED_BY",
              confidence: 0.8,
            },
            node: relatedNode,
          },
        ]}
        onSelectRelatedNode={onSelectRelatedNode}
        onOpenSearch={onOpenSearch}
      />
    );

    expect(screen.getByText("Evidence links")).toBeInTheDocument();
    expect(screen.getByText("Resolved by · confidence 80%")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Related node" }));
    expect(onSelectRelatedNode).toHaveBeenCalledWith(relatedNode);

    fireEvent.click(screen.getByRole("button", { name: /open in search/i }));
    expect(onOpenSearch).toHaveBeenCalledWith(node);
  });

  it("shows empty evidence copy when there are no connected edges", () => {
    render(<NodeDetailCard node={node} onOpenSearch={vi.fn()} />);

    expect(
      screen.getByText("No connected evidence edges for this node in the current view.")
    ).toBeInTheDocument();
  });

  it("renders unknown evidence relation types without crashing", () => {
    const relatedNode: GraphNode = {
      id: "n3",
      kind: "entry",
      label: "Custom linked node",
      category: "pattern",
      wing: "copilot",
      room: "graph",
      color: "#123456",
    };

    render(
      <NodeDetailCard
        node={node}
        connectedEvidence={[
          {
            edge: {
              source: "n1",
              target: "n3",
              relation_type: "CITED_WITH",
              confidence: 0.4,
            },
            node: relatedNode,
          },
        ]}
        onOpenSearch={vi.fn()}
      />
    );

    expect(screen.getByText("CITED_WITH · confidence 40%")).toBeInTheDocument();
  });
});
