import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { CommunitiesTab } from "@/app/graph/communities-tab";
import { useCommunities } from "@/lib/api/hooks";

vi.mock("@/lib/api/hooks", () => ({
  useCommunities: vi.fn(),
}));

const mockedUseCommunities = vi.mocked(useCommunities);

describe("CommunitiesTab", () => {
  beforeEach(() => {
    mockedUseCommunities.mockReturnValue({
      data: {
        communities: [
          {
            id: "c-1",
            entry_count: 4,
            top_categories: [
              { name: "pattern", count: 2 },
              { name: "decision", count: 1 },
            ],
            wings: ["backend", "frontend"],
            top_relation_types: [
              { type: "RESOLVED_BY", count: 2 },
              { type: "CITED_WITH", count: 1 },
            ],
            representative_entries: [
              { id: 2, title: "Use parameterized SQL", category: "pattern" },
              { id: 4, title: "FTS5 supports NEAR queries", category: "discovery" },
            ],
          },
        ],
      },
      error: null,
      isLoading: false,
      isError: false,
      isSuccess: true,
      refetch: vi.fn(),
    } as any);
  });

  it("renders deterministic summary cards with grounded stats", () => {
    render(<CommunitiesTab active />);

    expect(screen.getByText("Community c-1 · 4 entries")).toBeInTheDocument();
    expect(screen.getByText(/Top categories:/)).toBeInTheDocument();
    expect(screen.getByText(/pattern \(2\), decision \(1\)/)).toBeInTheDocument();
    expect(screen.getByText(/Top wings:/)).toBeInTheDocument();
    expect(screen.getByText(/backend, frontend/)).toBeInTheDocument();
    expect(screen.getByText(/Top relation types:/)).toBeInTheDocument();
    expect(screen.getByText("Resolved by (2)")).toBeInTheDocument();
    expect(screen.getByText("CITED_WITH (1)")).toBeInTheDocument();
    expect(screen.getByText("Use parameterized SQL (pattern) #2")).toBeInTheDocument();
  });

  it("suppresses singleton-only communities with an honest empty state", () => {
    mockedUseCommunities.mockReturnValue({
      data: {
        communities: [
          {
            id: "c-9",
            entry_count: 1,
            top_categories: [{ name: "pattern", count: 1 }],
            wings: ["backend"],
            top_relation_types: [],
            representative_entries: [{ id: 9, title: "Singleton", category: "pattern" }],
          },
        ],
      },
      error: null,
      isLoading: false,
      isError: false,
      isSuccess: true,
      refetch: vi.fn(),
    } as any);

    render(<CommunitiesTab active />);

    expect(screen.getByText("No useful communities yet")).toBeInTheDocument();
    expect(screen.queryByText(/Community c-9/)).not.toBeInTheDocument();
  });

  it("supports drill-in actions to evidence and similarity tabs", () => {
    const onDrillIn = vi.fn();
    render(<CommunitiesTab active onDrillIn={onDrillIn} />);

    fireEvent.click(screen.getByRole("button", { name: "Open Evidence tab" }));
    fireEvent.click(screen.getByRole("button", { name: "Open Similarity tab" }));

    expect(onDrillIn).toHaveBeenNthCalledWith(1, "evidence");
    expect(onDrillIn).toHaveBeenNthCalledWith(2, "similarity");
  });
});
