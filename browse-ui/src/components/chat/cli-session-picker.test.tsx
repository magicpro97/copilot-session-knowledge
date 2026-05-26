import "@testing-library/jest-dom";
import { render, screen, fireEvent } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { CliSessionPicker, ConfirmAdoptionPanel } from "./cli-session-picker";
import type { CliSession } from "@/lib/api/types";

const baseSession: CliSession = {
  cli_session_id: "11111111-1111-4111-8111-111111111111",
  title: "Refactor auth flow",
  mtime: "2026-05-01T10:00:00Z",
  workspace_hint: "~/projects/app",
  branch: "main",
  repository: "owner/app",
};

const withPrior: CliSession = {
  ...baseSession,
  cli_session_id: "22222222-2222-4222-8222-222222222222",
  prior_context: {
    event_count: 7,
    first_event_at: "2026-05-01T09:00:00Z",
    last_event_at: "2026-05-01T09:55:00Z",
    last_status: "ok",
    truncated: false,
    redacted: false,
  },
};

describe("CliSessionPicker — base", () => {
  it("shows loading state", () => {
    render(<CliSessionPicker sessions={[]} isLoading onSelect={vi.fn()} />);
    expect(screen.getByText(/loading cli history/i)).toBeInTheDocument();
  });

  it("shows unavailable message", () => {
    render(<CliSessionPicker sessions={[]} unavailable onSelect={vi.fn()} />);
    expect(screen.getByText(/cli history is not available/i)).toBeInTheDocument();
  });

  it("shows empty state", () => {
    render(<CliSessionPicker sessions={[]} onSelect={vi.fn()} />);
    expect(screen.getByText(/no cli sessions found/i)).toBeInTheDocument();
  });

  it("renders title, workspace hint, and branch", () => {
    render(<CliSessionPicker sessions={[baseSession]} onSelect={vi.fn()} />);
    expect(screen.getByText("Refactor auth flow")).toBeInTheDocument();
    expect(screen.getByText("~/projects/app")).toBeInTheDocument();
    expect(screen.getByText("main")).toBeInTheDocument();
  });

  it("calls onSelect when an item is clicked", () => {
    const onSelect = vi.fn();
    render(<CliSessionPicker sessions={[baseSession]} onSelect={onSelect} />);
    fireEvent.click(screen.getByTestId("cli-session-item"));
    expect(onSelect).toHaveBeenCalledWith(baseSession);
  });
});

describe("CliSessionPicker — prior_context (#568)", () => {
  it("renders prior_context summary when event_count > 0", () => {
    render(<CliSessionPicker sessions={[withPrior]} onSelect={vi.fn()} />);
    const row = screen.getByTestId("cli-prior-context");
    expect(row).toBeInTheDocument();
    expect(row).toHaveTextContent(/7 prior events/i);
    expect(screen.getByTestId("cli-prior-status")).toHaveTextContent("ok");
    expect(screen.getByTestId("cli-prior-last-at")).toBeInTheDocument();
  });

  it("does not render prior_context block when absent", () => {
    render(<CliSessionPicker sessions={[baseSession]} onSelect={vi.fn()} />);
    expect(screen.queryByTestId("cli-prior-context")).not.toBeInTheDocument();
  });

  it("does not render prior_context block when event_count is zero", () => {
    const zero: CliSession = {
      ...baseSession,
      cli_session_id: "33333333-3333-4333-8333-333333333333",
      prior_context: {
        event_count: 0,
        first_event_at: null,
        last_event_at: null,
        last_status: null,
        truncated: false,
        redacted: false,
      },
    };
    render(<CliSessionPicker sessions={[zero]} onSelect={vi.fn()} />);
    expect(screen.queryByTestId("cli-prior-context")).not.toBeInTheDocument();
  });

  it("renders truncated indicator when truncated is true", () => {
    const truncated: CliSession = {
      ...withPrior,
      cli_session_id: "44444444-4444-4444-8444-444444444444",
      prior_context: { ...withPrior.prior_context!, truncated: true },
    };
    render(<CliSessionPicker sessions={[truncated]} onSelect={vi.fn()} />);
    expect(screen.getByTestId("cli-prior-truncated")).toHaveTextContent(/truncated/i);
  });

  it("does not surface absolute filesystem paths in the prior-context line", () => {
    render(<CliSessionPicker sessions={[withPrior]} onSelect={vi.fn()} />);
    const row = screen.getByTestId("cli-prior-context");
    expect(row.textContent).not.toMatch(/\/Users\//);
    expect(row.textContent).not.toMatch(/\/home\//);
    expect(row.textContent).not.toMatch(/[A-Za-z]:\\/);
  });
});

describe("ConfirmAdoptionPanel — prior_context", () => {
  it("renders prior activity summary when priorContext is supplied", () => {
    render(
      <ConfirmAdoptionPanel
        sessionName="Refactor auth flow"
        workspace="~/projects/app"
        priorContext={withPrior.prior_context!}
        onConfirm={vi.fn()}
      />
    );
    const node = screen.getByTestId("confirm-adoption-prior-context");
    expect(node).toHaveTextContent(/7 events/i);
    expect(node).toHaveTextContent(/last ok/i);
  });

  it("omits prior-activity line when priorContext is absent", () => {
    render(<ConfirmAdoptionPanel sessionName="x" workspace="~/projects/app" onConfirm={vi.fn()} />);
    expect(screen.queryByTestId("confirm-adoption-prior-context")).not.toBeInTheDocument();
  });

  it("omits prior-activity line when event_count is zero", () => {
    render(
      <ConfirmAdoptionPanel
        sessionName="x"
        workspace="~/projects/app"
        priorContext={{
          event_count: 0,
          first_event_at: null,
          last_event_at: null,
          last_status: null,
          truncated: false,
          redacted: false,
        }}
        onConfirm={vi.fn()}
      />
    );
    expect(screen.queryByTestId("confirm-adoption-prior-context")).not.toBeInTheDocument();
  });

  it("calls onConfirm when the confirm button is clicked", () => {
    const onConfirm = vi.fn();
    render(
      <ConfirmAdoptionPanel
        sessionName="x"
        workspace="~/projects/app"
        priorContext={withPrior.prior_context!}
        onConfirm={onConfirm}
      />
    );
    fireEvent.click(screen.getByTestId("confirm-adoption-btn"));
    expect(onConfirm).toHaveBeenCalled();
  });
});
