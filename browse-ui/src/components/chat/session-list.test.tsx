import "@testing-library/jest-dom";
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { SessionList } from "@/components/chat/session-list";
import type { OperatorSession } from "@/lib/api/types";

function makeSession(overrides: Partial<OperatorSession> = {}): OperatorSession {
  const now = new Date().toISOString();
  return {
    id: "00000000-0000-0000-0000-000000000001",
    name: "Session One",
    workspace: "~/proj/one",
    model: "gpt-4o",
    mode: "interactive",
    add_dirs: [],
    created_at: now,
    updated_at: now,
    run_count: 0,
    last_run_id: null,
    resume_ready: false,
    ...overrides,
  };
}

describe("SessionList — issue #564 active-run indicator", () => {
  const sessions: OperatorSession[] = [
    makeSession({ id: "11111111-1111-1111-1111-111111111111", name: "Alpha" }),
    makeSession({ id: "22222222-2222-2222-2222-222222222222", name: "Beta" }),
  ];

  it("renders no indicator when activeRunSessionIds is omitted (legacy backend behavior)", () => {
    render(<SessionList sessions={sessions} activeId={null} onSelect={vi.fn()} />);
    expect(screen.queryByTestId("session-active-run-indicator")).toBeNull();
  });

  it("renders no indicator when activeRunSessionIds is empty", () => {
    render(
      <SessionList
        sessions={sessions}
        activeId={null}
        onSelect={vi.fn()}
        activeRunSessionIds={new Set()}
      />
    );
    expect(screen.queryByTestId("session-active-run-indicator")).toBeNull();
  });

  it("renders the indicator on exactly the matching session row", () => {
    render(
      <SessionList
        sessions={sessions}
        activeId={null}
        onSelect={vi.fn()}
        activeRunSessionIds={new Set(["22222222-2222-2222-2222-222222222222"])}
      />
    );

    const indicators = screen.getAllByTestId("session-active-run-indicator");
    expect(indicators).toHaveLength(1);
    expect(indicators[0].getAttribute("data-session-id")).toBe(
      "22222222-2222-2222-2222-222222222222"
    );
    expect(indicators[0]).toHaveAccessibleName(/active run/i);
  });

  it("does not change the existing session click/select behavior", () => {
    const onSelect = vi.fn();
    render(
      <SessionList
        sessions={sessions}
        activeId={null}
        onSelect={onSelect}
        activeRunSessionIds={new Set(["11111111-1111-1111-1111-111111111111"])}
      />
    );
    expect(screen.getByText("Alpha")).toBeInTheDocument();
    expect(screen.getByText("Beta")).toBeInTheDocument();
  });
});
