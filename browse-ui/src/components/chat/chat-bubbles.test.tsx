import "@testing-library/jest-dom";
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { AssistantBubble, UserBubble } from "./chat-bubbles";

describe("UserBubble", () => {
  it("renders the prompt text", () => {
    render(<UserBubble prompt="Hello world" />);
    expect(screen.getByText("Hello world")).toBeInTheDocument();
  });

  it("renders timestamp when provided", () => {
    render(<UserBubble prompt="Hi" timestamp="2 days ago" />);
    expect(screen.getByText("2 days ago")).toBeInTheDocument();
  });
});

describe("AssistantBubble", () => {
  it("renders (no output) when chunks are empty and not streaming", () => {
    render(<AssistantBubble chunks={[]} />);
    expect(screen.getByText("(no output)")).toBeInTheDocument();
  });

  it("renders Thinking… when streaming with empty chunks", () => {
    render(<AssistantBubble chunks={[]} streaming />);
    expect(screen.getByText("Thinking…")).toBeInTheDocument();
  });

  it("renders text chunk content", () => {
    render(<AssistantBubble chunks={[{ kind: "text", text: "Final answer here" }]} />);
    expect(screen.getByText("Final answer here")).toBeInTheDocument();
  });

  // ── Regression: elapsed duration display ────────────────────────────────────

  it("renders elapsed duration when elapsedMs is provided", () => {
    // elapsedMs should appear in the footer metadata
    render(<AssistantBubble chunks={[{ kind: "text", text: "done" }]} elapsedMs={41000} />);
    expect(screen.getByText("41s")).toBeInTheDocument();
  });

  it("renders elapsed in minutes+seconds for longer runs", () => {
    render(<AssistantBubble chunks={[{ kind: "text", text: "done" }]} elapsedMs={125000} />);
    expect(screen.getByText("2m 5s")).toBeInTheDocument();
  });

  it("renders elapsed in whole minutes when no remainder seconds", () => {
    render(<AssistantBubble chunks={[{ kind: "text", text: "done" }]} elapsedMs={120000} />);
    expect(screen.getByText("2m")).toBeInTheDocument();
  });

  it("does not render elapsed when elapsedMs is undefined", () => {
    const { container } = render(<AssistantBubble chunks={[{ kind: "text", text: "done" }]} />);
    // Should not find any elapsed duration badge
    expect(container.querySelector("[data-testid='elapsed']")).toBeNull();
  });

  it("renders exit code badge alongside elapsed when both provided", () => {
    render(
      <AssistantBubble chunks={[{ kind: "text", text: "done" }]} exitCode={0} elapsedMs={30000} />
    );
    expect(screen.getByText("30s")).toBeInTheDocument();
    expect(screen.getByText(/exit 0/)).toBeInTheDocument();
  });

  it("renders resumed context badge when resumeUsed is true", () => {
    render(<AssistantBubble chunks={[{ kind: "text", text: "done" }]} resumeUsed />);
    expect(screen.getByText("resumed context")).toBeInTheDocument();
  });

  it("renders new context badge when resumeUsed is false", () => {
    render(<AssistantBubble chunks={[{ kind: "text", text: "done" }]} resumeUsed={false} />);
    expect(screen.getByText("new context")).toBeInTheDocument();
  });

  // ── Skills chunk rendering ───────────────────────────────────────────────────

  it("renders skills loaded summary from a skills chunk", () => {
    render(
      <AssistantBubble
        chunks={[
          {
            kind: "skills",
            count: 3,
            names: ["code-reviewer", "frontend-dev", "session-knowledge"],
          },
        ]}
      />
    );
    expect(screen.getByText("3 skills loaded")).toBeInTheDocument();
  });

  it("does not render skill names until expanded", () => {
    render(
      <AssistantBubble chunks={[{ kind: "skills", count: 2, names: ["skill-a", "skill-b"] }]} />
    );
    expect(screen.queryByText("skill-a")).toBeNull();
    expect(screen.queryByText("skill-b")).toBeNull();
  });
});
