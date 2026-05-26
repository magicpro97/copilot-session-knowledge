import "@testing-library/jest-dom";
import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { WorkbenchPanel } from "@/components/chat/workbench-panel";
import { LOCAL_HOST } from "@/lib/host-profiles";

const useHostFeatureMock = vi.fn();
const useOperatorActiveRunsMock = vi.fn();

vi.mock("@/lib/hosts", async () => {
  const actual = await vi.importActual<typeof import("@/lib/hosts")>("@/lib/hosts");
  return {
    ...actual,
    useHostFeature: (...args: unknown[]) => useHostFeatureMock(...args),
  };
});

vi.mock("@/lib/api/hooks", () => ({
  useOperatorActiveRuns: (...args: unknown[]) => useOperatorActiveRunsMock(...args),
}));

function setHostFeature(supported: boolean, loading = false) {
  useHostFeatureMock.mockReturnValue({ supported, loading });
}

function setQuery(state: {
  data?: { runs: unknown[]; count: number };
  isLoading?: boolean;
  isError?: boolean;
}) {
  useOperatorActiveRunsMock.mockReturnValue({
    data: state.data,
    isLoading: state.isLoading ?? false,
    isError: state.isError ?? false,
    error: null,
  });
}

describe("WorkbenchPanel — issue #564", () => {
  beforeEach(() => {
    useHostFeatureMock.mockReset();
    useOperatorActiveRunsMock.mockReset();
  });

  it("renders nothing when capability is unsupported and not loading", () => {
    setHostFeature(false, false);
    setQuery({ data: undefined });
    const { container } = render(<WorkbenchPanel host={LOCAL_HOST} />);
    expect(container.firstChild).toBeNull();
    expect(screen.queryByTestId("workbench-panel")).toBeNull();
  });

  it("renders skeletons while the capabilities probe is loading", () => {
    setHostFeature(false, true);
    setQuery({ isLoading: true });
    const { container } = render(<WorkbenchPanel host={LOCAL_HOST} />);
    expect(screen.getByTestId("workbench-panel")).toBeInTheDocument();
    // Two skeleton placeholders rendered while loading.
    expect(container.querySelectorAll('[data-slot="skeleton"]').length).toBe(2);
  });

  it("renders empty state when supported but no runs are active", () => {
    setHostFeature(true, false);
    setQuery({ data: { runs: [], count: 0 } });
    render(<WorkbenchPanel host={LOCAL_HOST} />);
    expect(screen.getByTestId("workbench-panel")).toBeInTheDocument();
    expect(screen.getByText(/no active runs/i)).toBeInTheDocument();
  });

  it("renders an error message when the active-runs query fails", () => {
    setHostFeature(true, false);
    setQuery({ isError: true });
    render(<WorkbenchPanel host={LOCAL_HOST} />);
    expect(screen.getByText(/workbench unavailable/i)).toBeInTheDocument();
  });

  it("renders run rows with status and triggers onSelectRun on click", () => {
    setHostFeature(true, false);
    const run = {
      id: "run-1",
      session_id: "11111111-1111-1111-1111-111111111111",
      status: "running",
      started_at: "2025-01-01T00:00:00Z",
      finished_at: null,
      exit_code: null,
      resume_used: false,
      session_label: "Alpha Session",
    };
    setQuery({ data: { runs: [run], count: 1 } });
    const onSelect = vi.fn();

    render(<WorkbenchPanel host={LOCAL_HOST} onSelectRun={onSelect} />);

    const button = screen.getByRole("button", { name: /alpha session/i });
    expect(button).toHaveAttribute("data-run-id", "run-1");
    expect(button).toHaveAttribute("data-session-id", "11111111-1111-1111-1111-111111111111");
    expect(screen.getByTestId("workbench-run-status")).toHaveTextContent("running");

    fireEvent.click(button);
    expect(onSelect).toHaveBeenCalledTimes(1);
    expect(onSelect).toHaveBeenCalledWith(run);
  });

  it("highlights the run row whose session matches activeSessionId", () => {
    setHostFeature(true, false);
    const run = {
      id: "run-2",
      session_id: "22222222-2222-2222-2222-222222222222",
      status: "running",
      started_at: "2025-01-01T00:00:00Z",
      finished_at: null,
      exit_code: null,
      resume_used: false,
      session_label: "Beta",
    };
    setQuery({ data: { runs: [run], count: 1 } });

    render(
      <WorkbenchPanel host={LOCAL_HOST} activeSessionId="22222222-2222-2222-2222-222222222222" />
    );

    const button = screen.getByRole("button", { name: /beta/i });
    expect(button.className).toMatch(/text-primary/);
  });
});
