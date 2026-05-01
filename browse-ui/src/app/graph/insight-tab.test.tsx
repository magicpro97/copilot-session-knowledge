import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { InsightTab } from "@/app/graph/insight-tab";
import {
  useCommunities,
  useDashboard,
  useEvidenceGraph,
  useKnowledgeInsights,
} from "@/lib/api/hooks";

vi.mock("next/link", () => ({
  default: ({
    href,
    children,
    className,
  }: {
    href: string;
    children: React.ReactNode;
    className?: string;
  }) => (
    <a href={href} className={className}>
      {children}
    </a>
  ),
}));

vi.mock("@/lib/api/hooks", () => ({
  useCommunities: vi.fn(),
  useDashboard: vi.fn(),
  useEvidenceGraph: vi.fn(),
  useKnowledgeInsights: vi.fn(),
}));

const mockedUseEvidenceGraph = vi.mocked(useEvidenceGraph);
const mockedUseCommunities = vi.mocked(useCommunities);
const mockedUseKnowledgeInsights = vi.mocked(useKnowledgeInsights);
const mockedUseDashboard = vi.mocked(useDashboard);

const makeEvidenceData = (
  overrides: Partial<{
    nodes: Array<{ id: string; kind: string; label: string; color: string }>;
    edges: Array<{ source: string; target: string; relation_type: string; confidence: number }>;
    truncated: boolean;
    meta: { edge_source: string; relation_types: string[] };
  }> = {}
) => ({
  nodes: [
    { id: "n1", kind: "entry", label: "Alpha", color: "#111" },
    { id: "n2", kind: "entry", label: "Beta", color: "#222" },
    { id: "n3", kind: "entry", label: "Gamma", color: "#333" },
  ],
  edges: [
    { source: "n1", target: "n2", relation_type: "RESOLVED_BY", confidence: 0.8 },
    { source: "n2", target: "n3", relation_type: "TAG_OVERLAP", confidence: 0.6 },
  ],
  truncated: false,
  meta: { edge_source: "knowledge_relations", relation_types: ["RESOLVED_BY", "TAG_OVERLAP"] },
  ...overrides,
});

describe("InsightTab", () => {
  beforeEach(() => {
    mockedUseEvidenceGraph.mockReturnValue({
      data: makeEvidenceData(),
      error: null,
      isLoading: false,
      isSuccess: true,
      isError: false,
      refetch: vi.fn(),
    } as any);

    mockedUseCommunities.mockReturnValue({
      data: {
        communities: [
          {
            id: "c-1",
            entry_count: 3,
            top_categories: [{ name: "pattern", count: 2 }],
            wings: ["backend"],
            top_relation_types: [{ type: "RESOLVED_BY", count: 2 }],
            representative_entries: [{ id: 1, title: "Alpha", category: "pattern" }],
          },
        ],
      },
      error: null,
      isLoading: false,
      isSuccess: true,
      isError: false,
      refetch: vi.fn(),
    } as any);

    mockedUseKnowledgeInsights.mockReturnValue({
      data: {
        overview: {
          health_score: 75,
          total_entries: 10,
          sessions: 3,
          high_confidence_pct: 60,
          low_confidence_pct: 20,
          stale_pct: 10,
          relation_density: 0.4,
          embedding_pct: 90,
        },
        quality_alerts: [],
        recommended_actions: [],
        recurring_noise_titles: [],
        hot_files: [],
        entries: { mistakes: [], patterns: [], decisions: [], tools: [] },
        summary: "OK",
        generated_at: "2026-01-01T00:00:00Z",
      },
      error: null,
      isLoading: false,
      isSuccess: true,
      isError: false,
      refetch: vi.fn(),
    } as any);

    mockedUseDashboard.mockReturnValue({
      data: {
        totals: {
          sessions: 3,
          knowledge_entries: 10,
          relations: 5,
          embeddings: 9,
        },
        by_category: [],
        sessions_per_day: [],
        top_wings: [],
        red_flags: [],
        weekly_mistakes: [],
        top_modules: [],
      },
      error: null,
      isLoading: false,
      isSuccess: true,
      isError: false,
      refetch: vi.fn(),
    } as any);
  });

  it("renders graph metric tiles with loaded data", () => {
    render(<InsightTab active onNavigate={vi.fn()} />);

    expect(screen.getByText("Entries in graph")).toBeInTheDocument();
    expect(screen.getByText("Evidence edges")).toBeInTheDocument();
    expect(screen.getByText("Communities")).toBeInTheDocument();
    expect(screen.getByText("Embedding coverage")).toBeInTheDocument();
  });

  it("shows insight findings panel", () => {
    render(<InsightTab active onNavigate={vi.fn()} />);

    expect(screen.getByText("Graph findings")).toBeInTheDocument();
  });

  it("shows cross-session TAG_OVERLAP finding when present", () => {
    render(<InsightTab active onNavigate={vi.fn()} />);

    expect(screen.getByText(/Cross-session tag connections present/)).toBeInTheDocument();
  });

  it("shows community-found finding when multi-entry communities exist", () => {
    render(<InsightTab active onNavigate={vi.fn()} />);

    expect(screen.getByText(/1 connected community detected/)).toBeInTheDocument();
  });

  it("shows no-cross-session finding when only intra-session relations present", () => {
    mockedUseEvidenceGraph.mockReturnValue({
      data: makeEvidenceData({
        meta: {
          edge_source: "knowledge_relations",
          relation_types: ["RESOLVED_BY", "SAME_SESSION"],
        },
        edges: [{ source: "n1", target: "n2", relation_type: "RESOLVED_BY", confidence: 0.8 }],
      }),
      error: null,
      isLoading: false,
      isSuccess: true,
      isError: false,
      refetch: vi.fn(),
    } as any);

    render(<InsightTab active onNavigate={vi.fn()} />);

    expect(screen.getByText(/No cross-session connections in current view/)).toBeInTheDocument();
  });

  it("shows truncation finding when graph is truncated", () => {
    mockedUseEvidenceGraph.mockReturnValue({
      data: makeEvidenceData({ truncated: true }),
      error: null,
      isLoading: false,
      isSuccess: true,
      isError: false,
      refetch: vi.fn(),
    } as any);

    render(<InsightTab active onNavigate={vi.fn()} />);

    expect(screen.getByText(/Evidence graph is truncated/)).toBeInTheDocument();
  });

  it("shows low embedding coverage warning when pct < 50", () => {
    mockedUseKnowledgeInsights.mockReturnValue({
      data: {
        overview: {
          health_score: 60,
          total_entries: 10,
          sessions: 2,
          high_confidence_pct: 40,
          low_confidence_pct: 40,
          stale_pct: 20,
          relation_density: 0.2,
          embedding_pct: 30,
        },
        quality_alerts: [],
        recommended_actions: [],
        recurring_noise_titles: [],
        hot_files: [],
        entries: { mistakes: [], patterns: [], decisions: [], tools: [] },
        summary: "needs work",
        generated_at: "2026-01-01T00:00:00Z",
      },
      error: null,
      isLoading: false,
      isSuccess: true,
      isError: false,
      refetch: vi.fn(),
    } as any);

    render(<InsightTab active onNavigate={vi.fn()} />);

    expect(screen.getByText(/Only 30% of entries have embeddings/)).toBeInTheDocument();
    expect(screen.getByText("Suggested actions")).toBeInTheDocument();
  });

  it("shows no-graph-data warning when evidence graph is empty", () => {
    mockedUseEvidenceGraph.mockReturnValue({
      data: { nodes: [], edges: [], truncated: false, meta: undefined },
      error: null,
      isLoading: false,
      isSuccess: true,
      isError: false,
      refetch: vi.fn(),
    } as any);

    render(<InsightTab active onNavigate={vi.fn()} />);

    expect(screen.getByText(/No evidence graph data loaded/)).toBeInTheDocument();
  });

  it("shows loading state while evidence graph is loading", () => {
    mockedUseEvidenceGraph.mockReturnValue({
      data: undefined,
      error: null,
      isLoading: true,
      isSuccess: false,
      isError: false,
      refetch: vi.fn(),
    } as any);

    render(<InsightTab active onNavigate={vi.fn()} />);

    expect(screen.getByText("Loading graph data…")).toBeInTheDocument();
  });

  it("renders plain-language explainers for evidence, similarity, and communities", () => {
    render(<InsightTab active onNavigate={vi.fn()} />);

    expect(screen.getByText("What this graph shows")).toBeInTheDocument();
    expect(screen.getByText(/evidence graph surfaces typed connections/)).toBeInTheDocument();
    expect(screen.getByText(/Similarity uses semantic embeddings/)).toBeInTheDocument();
    expect(screen.getByText(/Communities are thematic clusters/)).toBeInTheDocument();
  });

  it("renders navigation CTAs to deeper graph tabs", () => {
    render(<InsightTab active onNavigate={vi.fn()} />);

    expect(screen.getByRole("button", { name: /Evidence graph →/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Similarity →/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Communities →/ })).toBeInTheDocument();
  });

  it("calls onNavigate with the correct tab when CTA is clicked", () => {
    const onNavigate = vi.fn();
    render(<InsightTab active onNavigate={onNavigate} />);

    fireEvent.click(screen.getByRole("button", { name: /Evidence graph →/ }));
    expect(onNavigate).toHaveBeenCalledWith("evidence");

    fireEvent.click(screen.getByRole("button", { name: /Similarity →/ }));
    expect(onNavigate).toHaveBeenCalledWith("similarity");

    fireEvent.click(screen.getByRole("button", { name: /Communities →/ }));
    expect(onNavigate).toHaveBeenCalledWith("communities");
  });

  it("renders a link to the full insights workspace", () => {
    render(<InsightTab active onNavigate={vi.fn()} />);

    const link = screen.getByRole("link", { name: /Full insights workspace/ });
    expect(link).toBeInTheDocument();
    expect(link).toHaveAttribute("href", "/insights");
  });

  it("renders drill-down link to /insights#knowledge for cross-session finding", () => {
    render(<InsightTab active onNavigate={vi.fn()} />);

    // cross-session-signal finding is present with TAG_OVERLAP in default mock
    const links = screen.getAllByRole("link");
    const knowledgeLinks = links.filter((l) =>
      l.getAttribute("href")?.includes("/insights#knowledge")
    );
    expect(knowledgeLinks.length).toBeGreaterThan(0);
  });

  it("renders drill-down link to /insights#overview for no-graph-data finding", () => {
    mockedUseEvidenceGraph.mockReturnValue({
      data: { nodes: [], edges: [], truncated: false, meta: undefined },
      error: null,
      isLoading: false,
      isSuccess: true,
      isError: false,
      refetch: vi.fn(),
    } as any);

    render(<InsightTab active onNavigate={vi.fn()} />);

    const links = screen.getAllByRole("link");
    const overviewLinks = links.filter((l) =>
      l.getAttribute("href")?.includes("/insights#overview")
    );
    expect(overviewLinks.length).toBeGreaterThan(0);
  });

  it("shows no community finding while communities are loading", () => {
    mockedUseCommunities.mockReturnValue({
      data: undefined,
      error: null,
      isLoading: true,
      isSuccess: false,
      isError: false,
      refetch: vi.fn(),
    } as any);

    render(<InsightTab active onNavigate={vi.fn()} />);

    expect(screen.queryByText(/No multi-entry communities detected yet/)).not.toBeInTheDocument();
    expect(screen.queryByText(/communities detected/)).not.toBeInTheDocument();
  });

  it("shows communities-load-error finding when communities fetch fails", () => {
    mockedUseCommunities.mockReturnValue({
      data: undefined,
      error: new Error("fetch failed"),
      isLoading: false,
      isSuccess: false,
      isError: true,
      refetch: vi.fn(),
    } as any);

    render(<InsightTab active onNavigate={vi.fn()} />);

    expect(screen.getByText(/Communities data failed to load/)).toBeInTheDocument();
    expect(screen.queryByText(/No multi-entry communities detected yet/)).not.toBeInTheDocument();
  });

  it("shows no-communities finding when only singletons are present", () => {
    mockedUseCommunities.mockReturnValue({
      data: {
        communities: [
          {
            id: "c-solo",
            entry_count: 1,
            top_categories: [],
            wings: [],
            top_relation_types: [],
            representative_entries: [],
          },
        ],
      },
      error: null,
      isLoading: false,
      isSuccess: true,
      isError: false,
      refetch: vi.fn(),
    } as any);

    render(<InsightTab active onNavigate={vi.fn()} />);

    expect(screen.getByText(/No multi-entry communities detected yet/)).toBeInTheDocument();
  });
});
