import "@testing-library/jest-dom";
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { EvalBody } from "@/app/insights/eval-section";
import { useEval } from "@/lib/api/hooks";

vi.mock("@/lib/api/hooks", () => ({
  useEval: vi.fn(),
}));

type EvalQuery = ReturnType<typeof useEval>;

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

describe("EvalBody", () => {
  it("shows skeleton rows while loading", () => {
    const { container } = render(<EvalBody evalQuery={makeQuery({ isLoading: true })} />);
    expect(container.querySelectorAll('[data-slot="skeleton"]').length).toBeGreaterThan(0);
  });

  it("shows error banner when query fails", () => {
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
