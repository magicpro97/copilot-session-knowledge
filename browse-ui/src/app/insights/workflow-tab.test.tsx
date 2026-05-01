import "@testing-library/jest-dom";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { WorkflowTab } from "@/app/insights/workflow-tab";

vi.mock("@/lib/api/hooks", () => ({
  useWorkflowHealth: vi.fn(),
}));

vi.mock("lucide-react", async (importOriginal) => {
  const actual = await importOriginal<typeof import("lucide-react")>();
  return {
    ...actual,
    CheckCircle: () => <span data-testid="icon-check-circle" />,
  };
});

import { useWorkflowHealth } from "@/lib/api/hooks";

const mockedUseWorkflowHealth = vi.mocked(useWorkflowHealth);
type WorkflowQuery = ReturnType<typeof useWorkflowHealth>;

function makeWorkflowQuery(overrides: Partial<WorkflowQuery>): WorkflowQuery {
  return {
    data: undefined,
    error: null,
    isLoading: false,
    isFetching: false,
    isError: false,
    isSuccess: false,
    refetch: vi.fn(),
    ...overrides,
  } as unknown as WorkflowQuery;
}

describe("WorkflowTab", () => {
  it("shows loading skeletons while fetching", () => {
    mockedUseWorkflowHealth.mockReturnValue(
      makeWorkflowQuery({
        isLoading: true,
      })
    );

    render(<WorkflowTab />);
    expect(screen.getByTestId("workflow-loading")).toBeInTheDocument();
  });

  it("shows error banner when fetch fails", () => {
    mockedUseWorkflowHealth.mockReturnValue(
      makeWorkflowQuery({
        isError: true,
      })
    );

    render(<WorkflowTab />);
    expect(screen.getByText(/workflow health unavailable/i)).toBeInTheDocument();
  });

  it("shows reload button and refetches when unavailable", () => {
    const refetch = vi.fn();
    mockedUseWorkflowHealth.mockReturnValue(
      makeWorkflowQuery({
        isError: true,
        refetch,
      })
    );

    render(<WorkflowTab />);
    fireEvent.click(screen.getByRole("button", { name: /reload/i }));
    expect(refetch).toHaveBeenCalledTimes(1);
  });

  it("shows empty state when findings array is empty", () => {
    mockedUseWorkflowHealth.mockReturnValue(
      makeWorkflowQuery({
        isSuccess: true,
        data: { findings: [], health_grade: "A", generated_at: "2026-05-01T00:00:00Z" },
      })
    );

    render(<WorkflowTab />);
    expect(screen.getByText(/no workflow findings/i)).toBeInTheDocument();
  });

  it("renders findings via InsightFindingCard", () => {
    mockedUseWorkflowHealth.mockReturnValue(
      makeWorkflowQuery({
        isSuccess: true,
        data: {
          findings: [
            {
              id: "w1",
              title: "Missing test coverage",
              detail: "Several modules lack tests.",
              severity: "warning",
              impact: "Reduced reliability",
              action: "Add unit tests",
            },
            {
              id: "w2",
              title: "Outdated dependency",
              detail: "Package X is outdated.",
              severity: "info",
              impact: "Minor",
              action: "Run npm update",
            },
          ],
          health_grade: "B",
          generated_at: "2026-05-01T00:00:00Z",
        },
      })
    );

    render(<WorkflowTab />);
    expect(screen.getByText(/Missing test coverage/)).toBeInTheDocument();
    expect(screen.getByText(/Outdated dependency/)).toBeInTheDocument();
  });

  it("shows health grade badge when data is loaded", () => {
    mockedUseWorkflowHealth.mockReturnValue(
      makeWorkflowQuery({
        isSuccess: true,
        data: {
          findings: [
            {
              id: "w1",
              title: "Critical issue",
              detail: "Something is broken.",
              severity: "critical",
              impact: "High",
              action: "Fix immediately",
            },
          ],
          health_grade: "D",
          generated_at: "2026-05-01T00:00:00Z",
        },
      })
    );

    render(<WorkflowTab />);
    expect(screen.getByText("Grade: D")).toBeInTheDocument();
  });
});
