/**
 * Tests for the /graph page shell:
 * - Default tab is "insight" (new behaviour)
 * - Hash aliases: #relationships -> evidence, #clusters -> similarity, #insight -> insight
 * - Keyboard routing 1/2/3/4 maps to insight/evidence/similarity/communities
 */
import { render, screen, fireEvent } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

// Stub all child tab components so page-shell tests focus on routing logic only
vi.mock("@/app/graph/insight-tab", () => ({
  InsightTab: ({ active }: { active: boolean }) => (
    <div data-testid="insight-tab" data-active={String(active)}>
      Insight tab
    </div>
  ),
}));
vi.mock("@/app/graph/relationships-tab", () => ({
  RelationshipsTab: ({ active }: { active: boolean }) => (
    <div data-testid="evidence-tab" data-active={String(active)}>
      Evidence tab
    </div>
  ),
}));
vi.mock("@/app/graph/clusters-tab", () => ({
  ClustersTab: ({ active }: { active: boolean }) => (
    <div data-testid="similarity-tab" data-active={String(active)}>
      Similarity tab
    </div>
  ),
}));
vi.mock("@/app/graph/communities-tab", () => ({
  CommunitiesTab: ({ active }: { active: boolean }) => (
    <div data-testid="communities-tab" data-active={String(active)}>
      Communities tab
    </div>
  ),
}));

// Import AFTER mocking
import GraphPage from "@/app/graph/page";

describe("GraphPage", () => {
  beforeEach(() => {
    // Reset hash and history stub before each test
    window.location.hash = "";
    vi.spyOn(window.history, "replaceState").mockImplementation(() => undefined);
  });

  it("renders Insight tab as the default (first) tab", () => {
    render(<GraphPage />);
    expect(screen.getByText("Insight tab")).toBeInTheDocument();
    expect(screen.getByTestId("insight-tab")).toHaveAttribute("data-active", "true");
  });

  it("renders all four tab triggers", () => {
    render(<GraphPage />);
    expect(screen.getByRole("tab", { name: "Insight" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Evidence" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Similarity" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Communities" })).toBeInTheDocument();
  });

  it("uses vertical tab orientation for the menu list", () => {
    render(<GraphPage />);
    expect(screen.getByRole("tablist").closest("[data-orientation='vertical']")).not.toBeNull();
  });

  it("activates Evidence tab via #evidence hash on mount", () => {
    window.location.hash = "#evidence";
    render(<GraphPage />);
    expect(screen.getByTestId("evidence-tab")).toHaveAttribute("data-active", "true");
  });

  it("activates Evidence tab via legacy #relationships hash alias", () => {
    window.location.hash = "#relationships";
    render(<GraphPage />);
    expect(screen.getByTestId("evidence-tab")).toHaveAttribute("data-active", "true");
  });

  it("activates Similarity tab via #similarity hash on mount", () => {
    window.location.hash = "#similarity";
    render(<GraphPage />);
    expect(screen.getByTestId("similarity-tab")).toHaveAttribute("data-active", "true");
  });

  it("activates Similarity tab via legacy #clusters hash alias", () => {
    window.location.hash = "#clusters";
    render(<GraphPage />);
    expect(screen.getByTestId("similarity-tab")).toHaveAttribute("data-active", "true");
  });

  it("activates Communities tab via #communities hash on mount", () => {
    window.location.hash = "#communities";
    render(<GraphPage />);
    expect(screen.getByTestId("communities-tab")).toHaveAttribute("data-active", "true");
  });

  it("activates Insight tab via #insight hash on mount", () => {
    window.location.hash = "#insight";
    render(<GraphPage />);
    expect(screen.getByTestId("insight-tab")).toHaveAttribute("data-active", "true");
  });

  it("switches to Evidence tab on key 2", () => {
    render(<GraphPage />);
    fireEvent.keyDown(window, { key: "2" });
    expect(screen.getByTestId("evidence-tab")).toHaveAttribute("data-active", "true");
  });

  it("switches to Similarity tab on key 3", () => {
    render(<GraphPage />);
    fireEvent.keyDown(window, { key: "3" });
    expect(screen.getByTestId("similarity-tab")).toHaveAttribute("data-active", "true");
  });

  it("switches to Communities tab on key 4", () => {
    render(<GraphPage />);
    fireEvent.keyDown(window, { key: "4" });
    expect(screen.getByTestId("communities-tab")).toHaveAttribute("data-active", "true");
  });

  it("switches back to Insight tab on key 1", () => {
    render(<GraphPage />);
    // Navigate away first
    fireEvent.keyDown(window, { key: "2" });
    expect(screen.getByTestId("evidence-tab")).toHaveAttribute("data-active", "true");
    // Then back to insight
    fireEvent.keyDown(window, { key: "1" });
    expect(screen.getByTestId("insight-tab")).toHaveAttribute("data-active", "true");
  });

  it("does not switch tabs when modifier key is held", () => {
    render(<GraphPage />);
    fireEvent.keyDown(window, { key: "2", metaKey: true });
    // Should still be on Insight
    expect(screen.getByTestId("insight-tab")).toHaveAttribute("data-active", "true");
  });

  it("does not switch tabs when focus is on an input element", () => {
    render(
      <>
        <GraphPage />
        <input data-testid="text-input" />
      </>
    );
    const input = screen.getByTestId("text-input");
    fireEvent.keyDown(input, { key: "2", target: input });
    // target.tagName = INPUT → should not switch
    expect(screen.getByTestId("insight-tab")).toHaveAttribute("data-active", "true");
  });
});
