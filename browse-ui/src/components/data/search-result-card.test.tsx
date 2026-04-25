import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { SearchResultCard } from "@/components/data/search-result-card";
import type { SearchResult } from "@/lib/api/types";

describe("SearchResultCard", () => {
  it("strips backend <mark> tags from snippet text", () => {
    const result: SearchResult = {
      type: "knowledge",
      id: "k-1",
      title: "Result",
      snippet: "found <mark>matching</mark> text in session",
      score: 0.9,
      wing: "copilot",
      kind: "pattern",
    };

    const { container } = render(<SearchResultCard result={result} query="matching" />);

    const mark = screen.getByText("matching");
    expect(mark.tagName).toBe("MARK");
    expect(container.textContent).not.toContain("<mark>");
    expect(container.textContent).not.toContain("</mark>");
  });
});
