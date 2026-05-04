import "@testing-library/jest-dom";
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { EvalBody, EvalSection } from "@/app/insights/eval-section";
import { InsightsTabContext } from "@/app/insights/insights-tab-context";
import { useEval, useKnowledgeInsights } from "@/lib/api/hooks";
import { LOCAL_HOST } from "@/lib/host-profiles";

vi.mock("@/lib/api/hooks", () => ({
  useEval: vi.fn(),
  useKnowledgeInsights: vi.fn(),
}));

type EvalQuery = ReturnType<typeof useEval>;
type InsightsQuery = ReturnType<typeof useKnowledgeInsights>;
const REMOTE_HOST = {
  id: "remote-h1",
  label: "Remote Host",
  base_url: "https://remote.example.com",
  token: "tok-remote",
  cli_kind: "copilot" as const,
  is_default: false,
};

function makeQuery(overrides: Partial<EvalQuery>): EvalQuery {
  return {
    data: undefined,
    isLoading: false,
    isError: false,
    isSuccess: false,
    error: null,
    ...overrides,
  } as EvalQuery;
}

function makeInsightsQuery(overrides: Partial<InsightsQuery> = {}): InsightsQuery {
  return {
    data: undefined,
    isLoading: false,
    isError: false,
    isSuccess: false,
    error: null,
    ...overrides,
  } as InsightsQuery;
}

/** Set up the useKnowledgeInsights mock before each test. */
function setupInsightsMock(embedPct?: number) {
  vi.mocked(useKnowledgeInsights).mockReturnValue(
    makeInsightsQuery(
      embedPct !== undefined
        ? {
            isSuccess: true,
            data: {
              generated_at: "",
              summary: "",
              overview: {
                health_score: 80,
                total_entries: 100,
                sessions: 10,
                high_confidence_pct: 70,
                low_confidence_pct: 10,
                stale_pct: 5,
                relation_density: 2,
                embedding_pct: embedPct,
              },
              quality_alerts: [],
              recommended_actions: [],
              recurring_noise_titles: [],
              hot_files: [],
              entries: { mistakes: [], patterns: [], decisions: [], tools: [] },
            },
          }
        : {}
    )
  );
}

describe("EvalBody", () => {
  it("shows skeleton rows while loading", () => {
    setupInsightsMock();
    const { container } = render(<EvalBody evalQuery={makeQuery({ isLoading: true })} />);
    expect(container.querySelectorAll('[data-slot="skeleton"]').length).toBeGreaterThan(0);
  });

  it("shows error banner when query fails", () => {
    setupInsightsMock();
    render(
      <EvalBody
        evalQuery={makeQuery({
          isError: true,
          error: new Error("Server unreachable"),
        })}
      />
    );
    expect(screen.getByText(/Search-quality stats unavailable/i)).toBeInTheDocument();
    expect(screen.getByText(/Server unreachable/i)).toBeInTheDocument();
  });

  it("shows empty state when data succeeds with zero aggregation rows", () => {
    setupInsightsMock();
    render(
      <EvalBody
        evalQuery={makeQuery({
          isSuccess: true,
          data: { aggregation: [], recent_comments: [] },
        })}
      />
    );
    expect(screen.getByText(/No search evaluations yet/i)).toBeInTheDocument();
    expect(screen.getByText(/thumbs-up/i)).toBeInTheDocument();
    // Table header must NOT be rendered
    expect(screen.queryByRole("columnheader", { name: /Query/i })).not.toBeInTheDocument();
  });

  it("renders aggregation table when rows are present", () => {
    setupInsightsMock();
    render(
      <EvalBody
        evalQuery={makeQuery({
          isSuccess: true,
          data: {
            aggregation: [{ query: "find bugs", up: 3, down: 1, neutral: 0, total: 4 }],
            recent_comments: [],
          },
        })}
      />
    );
    expect(screen.getByRole("columnheader", { name: /Query/i })).toBeInTheDocument();
    expect(screen.getByText("find bugs")).toBeInTheDocument();
    expect(screen.getByText("75%")).toBeInTheDocument();
  });

  it("renders recent comments when present alongside rows", () => {
    setupInsightsMock();
    render(
      <EvalBody
        evalQuery={makeQuery({
          isSuccess: true,
          data: {
            aggregation: [{ query: "test query", up: 1, down: 0, neutral: 0, total: 1 }],
            recent_comments: [
              {
                result_id: "r1",
                query: "test query",
                verdict: 1 as const,
                comment: "Very helpful!",
                created_at: "2026-01-01T00:00:00Z",
              },
            ],
          },
        })}
      />
    );
    expect(screen.getByText(/Recent feedback comments/i)).toBeInTheDocument();
    expect(screen.getByText("Very helpful!")).toBeInTheDocument();
  });
});

describe("ApprovalHistogram", () => {
  it("renders correct bucket counts based on approval rates", () => {
    setupInsightsMock();
    const { container } = render(
      <EvalBody
        evalQuery={makeQuery({
          isSuccess: true,
          data: {
            aggregation: [
              { query: "q1", up: 0, down: 5, neutral: 0, total: 5 }, // 0% → bucket 0
              { query: "q2", up: 2, down: 3, neutral: 0, total: 5 }, // 40% → bucket 2
              { query: "q3", up: 5, down: 0, neutral: 0, total: 5 }, // 100% → bucket 4
              { query: "q4", up: 4, down: 1, neutral: 0, total: 5 }, // 80% → bucket 4
            ],
            recent_comments: [],
          },
        })}
      />
    );
    expect(screen.getByText(/Approval rate distribution/i)).toBeInTheDocument();
    // Check the 5 bucket labels are rendered
    expect(screen.getByText("0–20%")).toBeInTheDocument();
    expect(screen.getByText("80–100%")).toBeInTheDocument();
    // 2 queries land in 80-100% bucket — verify the bar for that bucket has non-zero height
    const bars = container.querySelectorAll('[title*="80–100%"]');
    expect(bars.length).toBe(1);
    expect(bars[0]).toHaveAttribute("title", "80–100%: 2 queries");
  });

  it("does not render when aggregation data is empty", () => {
    setupInsightsMock();
    render(
      <EvalBody
        evalQuery={makeQuery({
          isSuccess: true,
          data: { aggregation: [], recent_comments: [] },
        })}
      />
    );
    expect(screen.queryByText(/Approval rate distribution/i)).not.toBeInTheDocument();
  });
});

describe("EvalSection", () => {
  it("passes the selected host into eval queries when diagnostics are enabled", () => {
    setupInsightsMock();
    vi.mocked(useEval).mockReturnValue(
      makeQuery({
        isSuccess: true,
        data: {
          aggregation: [{ query: "remote query", up: 1, down: 0, neutral: 0, total: 1 }],
          recent_comments: [],
        },
      })
    );

    render(
      <InsightsTabContext.Provider
        value={{ setActiveTab: vi.fn(), diagnosticsEnabled: true, host: REMOTE_HOST }}
      >
        <EvalSection />
      </InsightsTabContext.Provider>
    );

    expect(vi.mocked(useEval)).toHaveBeenCalledWith(REMOTE_HOST, true);
  });
});

describe("EmbeddingCoverageStat", () => {
  it("shows embedding coverage percentage", () => {
    setupInsightsMock(72);
    render(
      <EvalBody
        evalQuery={makeQuery({
          isSuccess: true,
          data: { aggregation: [], recent_comments: [] },
        })}
      />
    );
    expect(screen.getByText(/Embedding coverage/i)).toBeInTheDocument();
    expect(screen.getAllByText(/72%/).length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText(/72% of knowledge entries have embeddings/i)).toBeInTheDocument();
  });

  it("does not render when insights data is undefined", () => {
    vi.mocked(useKnowledgeInsights).mockReturnValue(makeInsightsQuery());
    render(
      <EvalBody
        evalQuery={makeQuery({
          isSuccess: true,
          data: { aggregation: [], recent_comments: [] },
        })}
      />
    );
    expect(screen.queryByText(/Embedding coverage/i)).not.toBeInTheDocument();
  });
});

describe("FeedbackTrend", () => {
  it("renders day bars for recent comments", () => {
    setupInsightsMock();
    const today = new Date();
    today.setHours(0, 0, 0, 0);
    const todayStr = today.toISOString().slice(0, 10);
    render(
      <EvalBody
        evalQuery={makeQuery({
          isSuccess: true,
          data: {
            aggregation: [{ query: "q1", up: 1, down: 0, neutral: 0, total: 1 }],
            recent_comments: [
              {
                result_id: "r1",
                query: "q1",
                verdict: 1 as const,
                comment: "great",
                created_at: `${todayStr}T10:00:00Z`,
              },
              {
                result_id: "r2",
                query: "q1",
                verdict: -1 as const,
                comment: "bad",
                created_at: `${todayStr}T11:00:00Z`,
              },
            ],
          },
        })}
      />
    );
    expect(screen.getByText(/Feedback activity/i)).toBeInTheDocument();
    // 14 day bars should be rendered
    const bars = document.querySelectorAll(`[title^="${todayStr}"]`);
    expect(bars.length).toBe(1);
    expect(bars[0]).toHaveAttribute("title", `${todayStr}: 2`);
  });

  it("does not render when comments array is empty", () => {
    setupInsightsMock();
    render(
      <EvalBody
        evalQuery={makeQuery({
          isSuccess: true,
          data: { aggregation: [], recent_comments: [] },
        })}
      />
    );
    expect(screen.queryByText(/Feedback activity/i)).not.toBeInTheDocument();
  });
});

describe("empty/undefined data handling", () => {
  it("all 3 visualizations render nothing when data is missing", () => {
    vi.mocked(useKnowledgeInsights).mockReturnValue(makeInsightsQuery());
    render(
      <EvalBody
        evalQuery={makeQuery({
          isSuccess: true,
          data: { aggregation: [], recent_comments: [] },
        })}
      />
    );
    expect(screen.queryByText(/Approval rate distribution/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/Embedding coverage/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/Feedback activity/i)).not.toBeInTheDocument();
  });
});

describe("EvalSection hosted idle state", () => {
  it("renders null when diagnosticsEnabled is false", () => {
    vi.mocked(useEval).mockReturnValue(
      makeQuery({ isSuccess: true, data: { aggregation: [], recent_comments: [] } })
    );
    vi.mocked(useKnowledgeInsights).mockReturnValue(makeInsightsQuery());

    const { container } = render(
      <InsightsTabContext.Provider
        value={{ setActiveTab: vi.fn(), diagnosticsEnabled: false, host: LOCAL_HOST }}
      >
        <EvalSection />
      </InsightsTabContext.Provider>
    );

    expect(container.firstChild).toBeNull();
  });

  it("renders section content when diagnosticsEnabled is true", () => {
    setupInsightsMock();
    vi.mocked(useEval).mockReturnValue(
      makeQuery({
        isSuccess: true,
        data: {
          aggregation: [{ query: "find bugs", up: 3, down: 1, neutral: 0, total: 4 }],
          recent_comments: [],
        },
      })
    );

    render(
      <InsightsTabContext.Provider
        value={{ setActiveTab: vi.fn(), diagnosticsEnabled: true, host: LOCAL_HOST }}
      >
        <EvalSection />
      </InsightsTabContext.Provider>
    );

    expect(screen.getByText(/Search quality/i)).toBeInTheDocument();
  });
});
