import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ClustersTab } from "@/app/graph/clusters-tab";
import { useEmbeddings, useSimilarity } from "@/lib/api/hooks";

vi.mock("next/navigation", () => ({
  useRouter: () => ({
    push: vi.fn(),
  }),
}));

vi.mock("@/lib/api/hooks", () => ({
  useEmbeddings: vi.fn(),
  useSimilarity: vi.fn(),
}));

vi.mock("@/components/data/scatter-canvas", () => ({
  CATEGORY_COLORS: { pattern: "#51cf66", decision: "#339af0", mistake: "#ff6b6b" },
  ScatterCanvas: ({
    points,
    onPointSelect,
  }: {
    points: Array<{ id: number }>;
    onPointSelect?: (point: { id: number } | null) => void;
  }) => (
    <button type="button" onClick={() => onPointSelect?.(points[0] ?? null)}>
      Select from map
    </button>
  ),
}));

const mockedUseEmbeddings = vi.mocked(useEmbeddings);
const mockedUseSimilarity = vi.mocked(useSimilarity);

const embeddingsData = {
  points: [
    { id: 1, title: "Alpha entry", category: "pattern", x: 0.1, y: 0.2 },
    { id: 2, title: "Beta entry", category: "decision", x: 0.3, y: 0.4 },
    { id: 3, title: "Gamma entry", category: "mistake", x: 0.5, y: 0.6 },
  ],
  count: 3,
  cached: true,
};

describe("ClustersTab similarity mode", () => {
  beforeEach(() => {
    mockedUseEmbeddings.mockReturnValue({
      data: embeddingsData,
      error: null,
      isLoading: false,
      isSuccess: true,
      isError: false,
      refetch: vi.fn(),
    } as any);

    mockedUseSimilarity.mockImplementation((params: any, enabled?: boolean) => {
      if (!enabled) {
        return {
          data: undefined,
          error: null,
          isLoading: false,
          isSuccess: false,
          isError: false,
          refetch: vi.fn(),
        } as any;
      }

      const entryId = Number(Array.isArray(params?.entry_id) ? params.entry_id[0] : params?.entry_id);
      if (entryId === 1) {
        return {
          data: {
            results: [
              {
                entry_id: 1,
                neighbors: [
                  { id: 2, title: "Beta entry", category: "decision", score: 0.983 },
                  { id: 3, title: "Gamma entry", category: "mistake", score: 0.812 },
                ],
              },
            ],
            meta: { embedding_count: 3, cached: true },
          },
          error: null,
          isLoading: false,
          isSuccess: true,
          isError: false,
          refetch: vi.fn(),
        } as any;
      }

      return {
        data: { results: [{ entry_id: entryId, neighbors: [] }], meta: {} },
        error: null,
        isLoading: false,
        isSuccess: true,
        isError: false,
        refetch: vi.fn(),
      } as any;
    });
  });

  it("supports cold-start selection flow before neighbors load", () => {
    render(<ClustersTab active />);

    expect(screen.getByText("Select an entry to explore neighbors")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /Alpha entry/i }));

    expect(screen.getByText(/Nearest neighbors/)).toBeInTheDocument();
    expect(screen.getAllByText("Beta entry").length).toBeGreaterThan(0);
    expect(screen.getByText("score 0.983")).toBeInTheDocument();
  });

  it("renders neighbor list as the primary similarity surface", () => {
    render(<ClustersTab active />);

    fireEvent.click(screen.getByRole("button", { name: /Alpha entry/i }));

    expect(screen.getByText("Nearest neighbors")).toBeInTheDocument();
    expect(screen.getAllByText("Gamma entry").length).toBeGreaterThan(0);
    expect(screen.getByText("score 0.812")).toBeInTheDocument();
  });

  it("shows explicit degraded-state and skipped entry copy", () => {
    mockedUseSimilarity.mockReturnValue({
      data: {
        results: [{ entry_id: 1, neighbors: [] }],
        meta: { degraded: true, skipped_entry_ids: [1] },
      },
      error: null,
      isLoading: false,
      isSuccess: true,
      isError: false,
      refetch: vi.fn(),
    } as any);

    render(<ClustersTab active />);

    fireEvent.click(screen.getByRole("button", { name: /Alpha entry/i }));

    expect(screen.getByText("Similarity results are partially degraded")).toBeInTheDocument();
    expect(screen.getByText(/Skipped entry IDs: 1\./)).toBeInTheDocument();
    expect(screen.getByText("Selected entry was skipped")).toBeInTheDocument();
  });

  it("shows degraded fallback copy when skipped IDs are not provided", () => {
    mockedUseSimilarity.mockReturnValue({
      data: {
        results: [{ entry_id: 1, neighbors: [] }],
        meta: { degraded: true },
      },
      error: null,
      isLoading: false,
      isSuccess: true,
      isError: false,
      refetch: vi.fn(),
    } as any);

    render(<ClustersTab active />);

    fireEvent.click(screen.getByRole("button", { name: /Alpha entry/i }));

    expect(screen.getByText("Similarity results are partially degraded")).toBeInTheDocument();
    expect(
      screen.getByText(
        "The backend marked this response as degraded. Neighbor coverage may be incomplete."
      )
    ).toBeInTheDocument();
  });

  it("supports selecting a source entry from the orientation map", () => {
    render(<ClustersTab active />);

    fireEvent.click(screen.getByRole("button", { name: "Select from map" }));

    expect(screen.getByText(/Source:/)).toBeInTheDocument();
    expect(screen.getAllByText("Alpha entry").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Beta entry").length).toBeGreaterThan(0);
  });

  it("surfaces similarity errors with retry affordance", () => {
    const refetch = vi.fn();
    mockedUseSimilarity.mockImplementation((params: any, enabled?: boolean) => {
      if (!enabled) {
        return {
          data: undefined,
          error: null,
          isLoading: false,
          isSuccess: false,
          isError: false,
          refetch: vi.fn(),
        } as any;
      }
      return {
        data: undefined,
        error: new Error("API 500: similarity unavailable"),
        isLoading: false,
        isSuccess: false,
        isError: true,
        refetch,
      } as any;
    });

    render(<ClustersTab active />);
    fireEvent.click(screen.getByRole("button", { name: /Alpha entry/i }));

    expect(screen.getByText("Failed to load similarity neighbors")).toBeInTheDocument();
    expect(screen.getByText("API 500: similarity unavailable")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(refetch).toHaveBeenCalled();
  });

  it("labels the projection map as a truthful secondary orientation surface", () => {
    render(<ClustersTab active />);

    expect(screen.getByText("Orientation map (secondary)")).toBeInTheDocument();
    expect(
      screen.getByText(/Projection map from \/api\/embeddings\/points for orientation only\./)
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Select from map" })).toBeInTheDocument();
  });
});
