import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ReactNode } from "react";
import { fireEvent } from "@testing-library/react";

import { RelationshipsTab } from "@/app/graph/relationships-tab";
import { useEvidenceGraph } from "@/lib/api/hooks";
import type { EvidenceGraphResponse } from "@/lib/api/types";

vi.mock("next/navigation", () => ({
  useRouter: () => ({
    push: vi.fn(),
  }),
}));

vi.mock("@/lib/api/hooks", () => ({
  useEvidenceGraph: vi.fn(),
}));

vi.mock("@/components/layout/filter-sidebar", () => ({
  FilterSidebar: ({
    sections,
  }: {
    sections: Array<{ id: string; title: string; content: ReactNode }>;
  }) => (
    <aside>
      {sections.map((section) => (
        <section key={section.id}>
          <h3>{section.title}</h3>
          {section.content}
        </section>
      ))}
    </aside>
  ),
}));

vi.mock("@/components/data/graph-canvas", () => ({
  GraphCanvas: ({
    nodes,
    onNodeSelect,
  }: {
    nodes: Array<{ id: string }>;
    onNodeSelect?: (node: { id: string }) => void;
  }) => (
    <button type="button" onClick={() => onNodeSelect?.(nodes[0])}>
      Select first node
    </button>
  ),
}));

const mockedUseEvidenceGraph = vi.mocked(useEvidenceGraph);

describe("RelationshipsTab evidence mode", () => {
  const evidenceData: EvidenceGraphResponse = {
    nodes: [
      {
        id: "n1",
        kind: "entry",
        label: "First entry",
        category: "pattern",
        wing: "copilot",
        room: "graph",
        color: "#111",
      },
      {
        id: "n2",
        kind: "entry",
        label: "Second entry",
        category: "decision",
        wing: "copilot",
        room: "graph",
        color: "#222",
      },
    ],
    edges: [
      {
        source: "n1",
        target: "n2",
        relation_type: "RESOLVED_BY",
        confidence: 0.8,
      },
    ],
    truncated: false,
    meta: {
      edge_source: "knowledge_relations",
      relation_types: ["RESOLVED_BY", "TAG_OVERLAP"],
    },
  };

  beforeEach(() => {
    mockedUseEvidenceGraph.mockReturnValue({
      data: evidenceData,
      error: null,
      isLoading: false,
      isSuccess: true,
      refetch: vi.fn(),
    } as any);
  });

  it("derives relation controls from runtime metadata and hides SAME_TOPIC", () => {
    render(<RelationshipsTab active />);

    expect(screen.getByText("Relation type")).toBeInTheDocument();
    expect(screen.getAllByText("Resolved by").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Tag overlap").length).toBeGreaterThan(0);
    expect(screen.queryByText("Same topic")).not.toBeInTheDocument();
    expect(
      screen.getByText(
        /Showing 2 nodes and 1 edges from knowledge_relations\. Edges are heuristically derived/
      )
    ).toBeInTheDocument();
  });

  it("shows typed evidence details for selected nodes", async () => {
    render(<RelationshipsTab active />);

    fireEvent.click(screen.getByRole("button", { name: "Select first node" }));

    expect(screen.getByText("Evidence links")).toBeInTheDocument();
    expect(screen.getByText("Resolved by · confidence 80%")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Second entry" })).toBeInTheDocument();
  });

  it("shows truthful truncation warning for evidence backend limits", () => {
    mockedUseEvidenceGraph.mockReturnValue({
      data: { ...evidenceData, truncated: true },
      error: null,
      isLoading: false,
      isSuccess: true,
      refetch: vi.fn(),
    } as any);

    render(<RelationshipsTab active />);

    expect(
      screen.getByText("Evidence graph results are truncated by the backend limit.")
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        /This view is from \/api\/graph\/evidence \(knowledge_relations\) with limit=500\./
      )
    ).toBeInTheDocument();
  });

  it("shows evidence graph error banner with backend message", () => {
    mockedUseEvidenceGraph.mockReturnValue({
      data: undefined,
      error: new Error("API 500: evidence unavailable"),
      isLoading: false,
      isSuccess: false,
      refetch: vi.fn(),
    } as any);

    render(<RelationshipsTab active />);

    expect(screen.getByText("Failed to load evidence graph")).toBeInTheDocument();
    expect(screen.getByText("API 500: evidence unavailable")).toBeInTheDocument();
  });

  it("renders unknown relation metadata with fallback label and disables filtering", () => {
    mockedUseEvidenceGraph.mockReturnValue({
      data: {
        ...evidenceData,
        edges: [
          {
            source: "n1",
            target: "n2",
            relation_type: "CITED_WITH",
            confidence: 0.6,
          },
        ],
        meta: {
          edge_source: "knowledge_relations",
          relation_types: ["RESOLVED_BY", "CITED_WITH"],
        },
      },
      error: null,
      isLoading: false,
      isSuccess: true,
      refetch: vi.fn(),
    } as any);

    render(<RelationshipsTab active />);

    expect(screen.getAllByText("CITED_WITH").length).toBeGreaterThan(0);
    expect(screen.getByRole("checkbox", { name: /CITED_WITH/ })).toHaveAttribute(
      "aria-disabled",
      "true"
    );
  });
});
