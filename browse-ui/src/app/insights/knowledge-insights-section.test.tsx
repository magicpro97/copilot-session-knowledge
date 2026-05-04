import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { KnowledgeInsightsSection } from "@/app/insights/knowledge-insights-section";
import { InsightsTabContext } from "@/app/insights/insights-tab-context";
import { useKnowledgeInsights } from "@/lib/api/hooks";
import { LOCAL_HOST } from "@/lib/host-profiles";

vi.mock("@/lib/api/hooks", () => ({
  useKnowledgeInsights: vi.fn(),
}));

const mockedUseKnowledgeInsights = vi.mocked(useKnowledgeInsights);

/** Render inside a context that enables diagnostics (simulates local/remote-host mode). */
function renderEnabled(ui: React.ReactElement) {
  return render(
    <InsightsTabContext.Provider
      value={{ setActiveTab: vi.fn(), diagnosticsEnabled: true, host: LOCAL_HOST }}
    >
      {ui}
    </InsightsTabContext.Provider>
  );
}

const _baseInsightsData = {
  generated_at: "2026-01-01T00:00:00Z",
  summary: "Everything looks healthy.",
  overview: {
    health_score: 82.5,
    total_entries: 120,
    sessions: 15,
    high_confidence_pct: 70.0,
    low_confidence_pct: 8.0,
    stale_pct: 3.0,
    relation_density: 1.5,
    embedding_pct: 45.0,
  },
  quality_alerts: [
    {
      id: "low-conf",
      title: "Low confidence entries",
      severity: "warning" as const,
      detail: "8% of entries are low confidence.",
    },
  ],
  recommended_actions: [
    {
      id: "run-extract",
      title: "Re-extract knowledge",
      detail: "Run extraction to refresh entries.",
      command: "python3 extract-knowledge.py",
    },
  ],
  recurring_noise_titles: [
    { title: "Noisy pattern", category: "mistake", entry_count: 4, avg_confidence: 0.25 },
  ],
  hot_files: [{ path: "browse/api/__init__.py", references: 8 }],
  entries: {
    mistakes: [
      {
        id: 1,
        title: "Fix import ordering",
        confidence: 0.9,
        occurrence_count: 2,
        last_seen: "2026-01-01",
        summary: "Always sort imports",
        session_id: "abc",
      },
    ],
    patterns: [],
    decisions: [],
    tools: [],
  },
};

describe("KnowledgeInsightsSection", () => {
  it("renders loading skeletons while fetching", () => {
    mockedUseKnowledgeInsights.mockReturnValue({
      data: undefined,
      isLoading: true,
      isError: false,
      isSuccess: false,
      error: null,
      refetch: vi.fn(),
    } as any);

    renderEnabled(<KnowledgeInsightsSection />);
    // Section should be present
    expect(screen.getByText("Knowledge Insights")).toBeInTheDocument();
  });

  it("renders summary and overview tiles on success", () => {
    mockedUseKnowledgeInsights.mockReturnValue({
      data: _baseInsightsData,
      isLoading: false,
      isError: false,
      isSuccess: true,
      error: null,
      refetch: vi.fn(),
    } as any);

    renderEnabled(<KnowledgeInsightsSection />);
    expect(screen.getByText("Knowledge Insights")).toBeInTheDocument();
    expect(screen.getByText("Everything looks healthy.")).toBeInTheDocument();
    expect(screen.getByText("Health score")).toBeInTheDocument();
    expect(screen.getByText("82.5")).toBeInTheDocument();
    expect(screen.getByText("Entries")).toBeInTheDocument();
  });

  it("renders quality alerts with severity", () => {
    mockedUseKnowledgeInsights.mockReturnValue({
      data: _baseInsightsData,
      isLoading: false,
      isError: false,
      isSuccess: true,
      error: null,
      refetch: vi.fn(),
    } as any);

    renderEnabled(<KnowledgeInsightsSection />);
    expect(screen.getByText("Quality alerts")).toBeInTheDocument();
    expect(screen.getByText(/Low confidence entries/)).toBeInTheDocument();
  });

  it("renders recommended actions", () => {
    mockedUseKnowledgeInsights.mockReturnValue({
      data: _baseInsightsData,
      isLoading: false,
      isError: false,
      isSuccess: true,
      error: null,
      refetch: vi.fn(),
    } as any);

    renderEnabled(<KnowledgeInsightsSection />);
    expect(screen.getByText("Recommended actions")).toBeInTheDocument();
    expect(screen.getByText("Re-extract knowledge")).toBeInTheDocument();
  });

  it("renders hot files and noise titles", () => {
    mockedUseKnowledgeInsights.mockReturnValue({
      data: _baseInsightsData,
      isLoading: false,
      isError: false,
      isSuccess: true,
      error: null,
      refetch: vi.fn(),
    } as any);

    renderEnabled(<KnowledgeInsightsSection />);
    expect(screen.getByText("Hot files")).toBeInTheDocument();
    expect(screen.getByText("browse/api/__init__.py")).toBeInTheDocument();
    expect(screen.getByText("Recurring noise titles")).toBeInTheDocument();
    expect(screen.getByText("Noisy pattern")).toBeInTheDocument();
  });

  it("renders representative entries by category", () => {
    mockedUseKnowledgeInsights.mockReturnValue({
      data: _baseInsightsData,
      isLoading: false,
      isError: false,
      isSuccess: true,
      error: null,
      refetch: vi.fn(),
    } as any);

    renderEnabled(<KnowledgeInsightsSection />);
    expect(screen.getByText("Representative entries by category")).toBeInTheDocument();
    expect(screen.getByText("Fix import ordering")).toBeInTheDocument();
  });

  it("shows error banner when fetch fails", () => {
    mockedUseKnowledgeInsights.mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
      isSuccess: false,
      error: new Error("Network error"),
      refetch: vi.fn(),
    } as any);

    renderEnabled(<KnowledgeInsightsSection />);
    expect(screen.getByText("Knowledge insights unavailable")).toBeInTheDocument();
    expect(screen.getByText("Network error")).toBeInTheDocument();
  });

  it("shows critical badge when there are critical alerts", () => {
    mockedUseKnowledgeInsights.mockReturnValue({
      data: {
        ..._baseInsightsData,
        quality_alerts: [
          {
            id: "crit",
            title: "Critical issue",
            severity: "critical" as const,
            detail: "Something is broken.",
          },
        ],
      },
      isLoading: false,
      isError: false,
      isSuccess: true,
      error: null,
      refetch: vi.fn(),
    } as any);

    renderEnabled(<KnowledgeInsightsSection />);
    expect(screen.getByText("1 critical")).toBeInTheDocument();
  });

  it("returns null when data is undefined and query succeeded", () => {
    mockedUseKnowledgeInsights.mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: false,
      isSuccess: true,
      error: null,
      refetch: vi.fn(),
    } as any);

    const { container } = renderEnabled(<KnowledgeInsightsSection />);
    expect(container.firstChild).toBeNull();
  });
});

describe("Toward100Panel via KnowledgeInsightsSection", () => {
  it("renders toward_100 top gaps when present", () => {
    mockedUseKnowledgeInsights.mockReturnValue({
      data: {
        ..._baseInsightsData,
        toward_100: {
          total_gap: 33.5,
          dimensions: [],
          top_gaps: [
            {
              dimension: "confidence_quality",
              current: 0.2,
              max: 15.0,
              gap: 14.8,
              gap_pct: 98.7,
              pct_of_total_gap: 44.2,
            },
            {
              dimension: "stale_ratio",
              current: 0.7,
              max: 10.0,
              gap: 8.5,
              gap_pct: 85.0,
              pct_of_total_gap: 25.4,
            },
          ],
        },
      },
      isLoading: false,
      isError: false,
      isSuccess: true,
      error: null,
      refetch: vi.fn(),
    } as any);

    renderEnabled(<KnowledgeInsightsSection />);

    expect(screen.getByText(/🎯 Toward 100/)).toBeInTheDocument();
    expect(screen.getByText("confidence_quality")).toBeInTheDocument();
    expect(screen.getByText("stale_ratio")).toBeInTheDocument();
    expect(screen.getByText("44.2%")).toBeInTheDocument();
  });

  it("does not render Toward100Panel when toward_100 is absent", () => {
    mockedUseKnowledgeInsights.mockReturnValue({
      data: _baseInsightsData,
      isLoading: false,
      isError: false,
      isSuccess: true,
      error: null,
      refetch: vi.fn(),
    } as any);

    renderEnabled(<KnowledgeInsightsSection />);

    expect(screen.queryByText(/🎯 Toward 100/)).not.toBeInTheDocument();
  });

  it("does not render Toward100Panel when toward_100 top_gaps is empty", () => {
    mockedUseKnowledgeInsights.mockReturnValue({
      data: {
        ..._baseInsightsData,
        toward_100: { total_gap: 0, dimensions: [], top_gaps: [] },
      },
      isLoading: false,
      isError: false,
      isSuccess: true,
      error: null,
      refetch: vi.fn(),
    } as any);

    renderEnabled(<KnowledgeInsightsSection />);

    expect(screen.queryByText(/🎯 Toward 100/)).not.toBeInTheDocument();
  });
});

describe("KnowledgeInsightsSection hosted idle state", () => {
  it("renders null when diagnosticsEnabled is false (hosted static mode)", () => {
    mockedUseKnowledgeInsights.mockReturnValue({
      data: _baseInsightsData,
      isLoading: false,
      isError: false,
      isSuccess: true,
      error: null,
      refetch: vi.fn(),
    } as any);

    const { container } = render(
      <InsightsTabContext.Provider
        value={{ setActiveTab: vi.fn(), diagnosticsEnabled: false, host: LOCAL_HOST }}
      >
        <KnowledgeInsightsSection />
      </InsightsTabContext.Provider>
    );

    // Section should not render at all — no API call fired in hosted static mode
    expect(container.firstChild).toBeNull();
  });
});
