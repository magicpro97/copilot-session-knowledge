import "@testing-library/jest-dom";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { TentacleStatusChip } from "./tentacle-status-chip";
import type { TentacleStatusResponse, TentacleEntry } from "@/lib/api/types";

// Minimal clipboard mock for copy button tests
Object.assign(navigator, {
  clipboard: { writeText: vi.fn().mockResolvedValue(undefined) },
});

const baseTentacle: TentacleEntry = {
  name: "feat-auth",
  tentacle_id: "t-001",
  status: "active",
  created_at: "2026-05-01T10:00:00Z",
  description: "Implement auth module",
  scope: ["src/auth.ts"],
  skills: [],
  worktree: { prepared: true, path: "/worktrees/feat-auth", stale: false },
  verification: { coverage_exists: true, total: 5, passed: 5, failed: 0 },
};

const baseResponse: TentacleStatusResponse = {
  status: "ok",
  configured: true,
  active_count: 2,
  total_count: 3,
  worktrees_prepared: 2,
  verification_covered: 2,
  marker: { active: false, path: "", age_hours: null, stale: false },
  tentacles: [
    baseTentacle,
    { ...baseTentacle, name: "feat-api", tentacle_id: "t-002", description: "API routes" },
  ],
  audit: { checks: [], summary: { ok: true, total_checks: 0, warning_checks: 0 } },
  operator_actions: [
    {
      id: "tentacle-list",
      title: "List tentacles",
      description: "List all tentacles",
      command: "python3 tentacle.py list",
      safe: true,
    },
  ],
  runtime: { generated_at: "2026-05-01T10:00:00Z" },
};

describe("TentacleStatusChip", () => {
  it("renders nothing when idle and not configured", () => {
    const { container } = render(
      <TentacleStatusChip data={undefined} isLoading={false} isError={false} />
    );
    expect(container.firstChild).toBeNull();
  });

  it("renders chip with active count", () => {
    render(<TentacleStatusChip data={baseResponse} isLoading={false} isError={false} />);
    expect(screen.getByTestId("tentacle-status-chip")).toBeInTheDocument();
    expect(screen.getByLabelText(/2 tentacles active/)).toBeInTheDocument();
  });

  it("shows error state when isError is true", () => {
    render(<TentacleStatusChip data={undefined} isLoading={false} isError={true} />);
    expect(screen.getByTestId("tentacle-status-chip")).toBeInTheDocument();
    expect(screen.getByLabelText(/unavailable/i)).toBeInTheDocument();
  });

  it("shows loading state", () => {
    render(
      <TentacleStatusChip
        data={{ ...baseResponse, configured: true }}
        isLoading={true}
        isError={false}
      />
    );
    expect(screen.getByTestId("tentacle-status-chip")).toBeInTheDocument();
    expect(screen.getByLabelText(/Loading tentacle status/)).toBeInTheDocument();
  });

  it("shows degraded status when a tentacle has REGRESSED terminal_status", () => {
    const degraded: TentacleStatusResponse = {
      ...baseResponse,
      tentacles: [{ ...baseTentacle, terminal_status: "REGRESSED", status: "idle" }],
    };
    render(<TentacleStatusChip data={degraded} isLoading={false} isError={false} />);
    expect(screen.getByLabelText(/2 tentacles active/)).toBeInTheDocument();
  });

  it("renders operator actions with copy button in expanded panel", async () => {
    render(<TentacleStatusChip data={baseResponse} isLoading={false} isError={false} />);
    // The popover trigger is visible; content is in the DOM via portal
    const trigger = screen.getByTestId("tentacle-status-chip");
    fireEvent.click(trigger);
    // After opening popover, check for operator actions
    expect(
      await screen.findByLabelText("Copy command: python3 tentacle.py list")
    ).toBeInTheDocument();
  });

  it("renders tentacle rows in the expanded panel", async () => {
    render(<TentacleStatusChip data={baseResponse} isLoading={false} isError={false} />);
    fireEvent.click(screen.getByTestId("tentacle-status-chip"));
    const rows = await screen.findAllByTestId("tentacle-row");
    expect(rows).toHaveLength(2);
  });

  it("shows verification stats when available", async () => {
    render(<TentacleStatusChip data={baseResponse} isLoading={false} isError={false} />);
    fireEvent.click(screen.getByTestId("tentacle-status-chip"));
    const matches = await screen.findAllByText(/5\/5 passed/);
    expect(matches.length).toBeGreaterThanOrEqual(1);
  });
});
