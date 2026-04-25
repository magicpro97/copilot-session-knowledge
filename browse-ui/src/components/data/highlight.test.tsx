import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";

import { Highlight, splitHighlightParts } from "@/components/data/highlight";

describe("Highlight", () => {
  it("splits highlighted terms safely", () => {
    const parts = splitHighlightParts("hello world", "world");
    expect(parts).toEqual([
      { text: "hello ", highlighted: false },
      { text: "world", highlighted: true },
    ]);
  });

  it("renders highlighted fragments", () => {
    render(<Highlight text="hello world" query="hello" />);
    const mark = screen.getByText("hello");
    expect(mark.tagName).toBe("MARK");
    expect(screen.getByText("world")).toBeInTheDocument();
  });

  it("highlights tokens containing regex special characters", () => {
    const parts = splitHighlightParts("hello.world a+b foo?", "hello.world foo?");
    expect(parts).toEqual([
      { text: "hello.world", highlighted: true },
      { text: " a+b ", highlighted: false },
      { text: "foo?", highlighted: true },
    ]);
  });
});
