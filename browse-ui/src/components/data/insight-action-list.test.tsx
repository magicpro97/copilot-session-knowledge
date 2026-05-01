import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { InsightActionList } from "@/components/data/insight-action-list";
import type { InsightAction } from "@/lib/insight-models";

const actions: InsightAction[] = [
  {
    id: "a1",
    title: "Re-extract knowledge",
    detail: "Refreshes stale entries.",
    command: "python3 extract-knowledge.py",
  },
  {
    id: "a2",
    title: "Review low confidence entries",
    detail: "Manually inspect entries below 0.4 confidence.",
  },
  {
    id: "a3",
    title: "Enable embeddings",
  },
];

describe("InsightActionList", () => {
  it("renders nothing for an empty actions list", () => {
    const { container } = render(<InsightActionList actions={[]} />);
    expect(container.firstChild).toBeNull();
  });

  it("renders the default title when no title prop is provided", () => {
    render(<InsightActionList actions={actions} />);
    expect(screen.getByText("Recommended actions")).toBeInTheDocument();
  });

  it("renders a custom title when provided", () => {
    render(<InsightActionList actions={actions} title="Next steps" />);
    expect(screen.getByText("Next steps")).toBeInTheDocument();
  });

  it("renders all action titles", () => {
    render(<InsightActionList actions={actions} />);
    expect(screen.getByText("Re-extract knowledge")).toBeInTheDocument();
    expect(screen.getByText("Review low confidence entries")).toBeInTheDocument();
    expect(screen.getByText("Enable embeddings")).toBeInTheDocument();
  });

  it("renders detail text when present", () => {
    render(<InsightActionList actions={actions} />);
    expect(screen.getByText(/Refreshes stale entries./)).toBeInTheDocument();
    expect(screen.getByText(/Manually inspect entries below 0.4 confidence./)).toBeInTheDocument();
  });

  it("renders command code when action is actionable", () => {
    render(<InsightActionList actions={actions} />);
    expect(screen.getByText("python3 extract-knowledge.py")).toBeInTheDocument();
  });

  it("does not render command code for non-actionable actions", () => {
    render(<InsightActionList actions={[actions[1]!]} />);
    // Second action has no command — only title and detail should appear
    const codes = screen.queryAllByRole("code");
    // Detail is plain text, not code
    expect(codes).toHaveLength(0);
  });
});
