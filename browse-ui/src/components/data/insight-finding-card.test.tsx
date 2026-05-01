import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { InsightFindingCard } from "@/components/data/insight-finding-card";
import type { InsightFinding } from "@/lib/insight-models";

const infoFinding: InsightFinding = {
  id: "f1",
  title: "Embedding coverage low",
  detail: "Only 20% of entries have embeddings.",
  severity: "info",
};

const warningFinding: InsightFinding = {
  id: "f2",
  title: "Stale entries detected",
  detail: "12% of entries have not been seen recently.",
  severity: "warning",
};

const criticalFinding: InsightFinding = {
  id: "f3",
  title: "Health score critical",
  detail: "Score has dropped below 40.",
  severity: "critical",
};

describe("InsightFindingCard", () => {
  it("renders the finding title and detail", () => {
    render(<InsightFindingCard finding={infoFinding} />);
    expect(screen.getByText(/Embedding coverage low/)).toBeInTheDocument();
    expect(screen.getByText(/Only 20% of entries have embeddings./)).toBeInTheDocument();
  });

  it("renders the info severity emoji", () => {
    render(<InsightFindingCard finding={infoFinding} />);
    expect(screen.getByText(/ℹ️/)).toBeInTheDocument();
  });

  it("renders the warning severity emoji", () => {
    render(<InsightFindingCard finding={warningFinding} />);
    expect(screen.getByText(/⚠️/)).toBeInTheDocument();
  });

  it("renders the critical severity emoji", () => {
    render(<InsightFindingCard finding={criticalFinding} />);
    expect(screen.getByText(/🚨/)).toBeInTheDocument();
  });

  it("omits the detail paragraph when detail is empty", () => {
    const minimal: InsightFinding = { id: "f4", title: "No detail", detail: "", severity: "info" };
    const { container } = render(<InsightFindingCard finding={minimal} />);
    // Only the title paragraph should be present
    expect(container.querySelectorAll("p")).toHaveLength(1);
  });

  it("accepts an additional className", () => {
    const { container } = render(
      <InsightFindingCard finding={infoFinding} className="custom-class" />
    );
    expect(container.firstChild).toHaveClass("custom-class");
  });

  it("has role listitem for accessibility", () => {
    render(<InsightFindingCard finding={infoFinding} />);
    expect(screen.getByRole("listitem")).toBeInTheDocument();
  });
});
