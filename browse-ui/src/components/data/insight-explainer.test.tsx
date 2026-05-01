import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { InsightExplainer } from "@/components/data/insight-explainer";

describe("InsightExplainer", () => {
  it("renders the summary text", () => {
    render(<InsightExplainer text="Knowledge health looks good overall." />);
    expect(screen.getByText("Knowledge health looks good overall.")).toBeInTheDocument();
  });

  it("renders the generatedAt footer when provided", () => {
    render(<InsightExplainer text="All systems nominal." generatedAt="2026-01-01T00:00:00Z" />);
    expect(screen.getByText(/generated 2026-01-01T00:00:00Z/)).toBeInTheDocument();
  });

  it("omits the generatedAt footer when not provided", () => {
    render(<InsightExplainer text="Short summary." />);
    expect(screen.queryByText(/generated/)).toBeNull();
  });

  it("accepts an additional className on the wrapper", () => {
    const { container } = render(<InsightExplainer text="Text." className="extra-class" />);
    expect(container.firstChild).toHaveClass("extra-class");
  });
});
