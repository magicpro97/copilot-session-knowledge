import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";

import {
  normalizeSource,
  SourceBadge,
  TimeRelative,
} from "@/components/data/session-badges";

describe("session badges", () => {
  it("normalizes source values", () => {
    expect(normalizeSource("Claude")).toBe("claude");
    expect(normalizeSource("unknown-source")).toBe("unknown");
  });

  it("renders source badge label", () => {
    render(<SourceBadge source="copilot" />);
    expect(screen.getByText("Copilot")).toBeInTheDocument();
  });

  it("renders relative time with datetime attr", () => {
    render(<TimeRelative value="2025-01-01T00:00:00Z" />);
    const time = document.querySelector("time");
    expect(time).toBeInTheDocument();
    expect(time?.getAttribute("datetime")).toBe("2025-01-01T00:00:00.000Z");
    expect(time?.textContent?.trim().length).toBeGreaterThan(0);
  });
});
