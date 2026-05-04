import { describe, expect, it } from "vitest";

import { matchesAppPath, normalizeAppPathname } from "./pathname";

describe("pathname helpers", () => {
  it("normalizes root-served paths by removing trailing slashes", () => {
    expect(normalizeAppPathname("/search/")).toBe("/search");
    expect(normalizeAppPathname("/insights/")).toBe("/insights");
    expect(normalizeAppPathname("/sessions/abc/")).toBe("/sessions/abc");
    expect(normalizeAppPathname("/sessions/abc")).toBe("/sessions/abc");
  });

  it("keeps root-like values stable", () => {
    expect(normalizeAppPathname("/")).toBe("/");
    expect(normalizeAppPathname("")).toBe("/");
    expect(normalizeAppPathname(null)).toBe("/");
  });

  it("matches nested routes after normalization", () => {
    expect(matchesAppPath("/search", "/search")).toBe(true);
    expect(matchesAppPath("/search/", "/search")).toBe(true);
    expect(matchesAppPath("/sessions/abc", "/sessions")).toBe(true);
    expect(matchesAppPath("/graph", "/sessions")).toBe(false);
  });
});
