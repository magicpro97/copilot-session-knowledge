import "@testing-library/jest-dom";
import { render, screen } from "@testing-library/react";
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

describe("WorkflowTab", () => {
  it("shows loading skeletons while fetching", () => {
    mockedUseWorkflowHealth.mockReturnValue({
      isLoading: true,
      isError: false,
      isSuccess: false,
      data: undefined,
    } as unknown as ReturnType<typeof useWorkflowHealth>);

    render(<WorkflowTab />);
    expect(screen.getByTestId("workflow-loading")).toBeInTheDocument();
  });

  it("shows error banner when fetch fails", () => {
    mockedUseWorkflowHealth.mockReturnValue({
      isLoading: false,
      isError: true,
      isSuccess: false,
      data: undefined,
    } as unknown as ReturnType<typeof useWorkflowHealth>);

    render(<WorkflowTab />);
    expect(screen.getByText(/workflow health unavailable/i)).toBeInTheDocument();
  });

  it("shows empty state when findings array is empty", () => {
    mockedUseWorkflowHealth.mockReturnValue({
      isLoading: false,
      isError: false,
      isSuccess: true,
      data: { findings: [], health_grade: "A", generated_at: "2026-05-01T00:00:00Z" },
    } as unknown as ReturnType<typeof useWorkflowHealth>);

    render(<WorkflowTab />);
    expect(screen.getByText(/no workflow findings/i)).toBeInTheDocument();
  });

  it("renders findings via InsightFindingCard", () => {
    mockedUseWorkflowHealth.mockReturnValue({
      isLoading: false,
      isError: false,
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
    } as unknown as ReturnType<typeof useWorkflowHealth>);

    render(<WorkflowTab />);
    expect(screen.getByText(/Missing test coverage/)).toBeInTheDocument();
    expect(screen.getByText(/Outdated dependency/)).toBeInTheDocument();
  });

  it("shows health grade badge when data is loaded", () => {
    mockedUseWorkflowHealth.mockReturnValue({
      isLoading: false,
      isError: false,
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
    } as unknown as ReturnType<typeof useWorkflowHealth>);

    render(<WorkflowTab />);
    expect(screen.getByText("Grade: D")).toBeInTheDocument();
  });
});
