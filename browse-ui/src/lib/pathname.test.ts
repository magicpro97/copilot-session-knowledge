import { describe, expect, it } from "vitest";

import { matchesAppPath, normalizeAppPathname } from "./pathname";

describe("pathname helpers", () => {
  it("strips the /v2 basePath before route matching", () => {
    expect(normalizeAppPathname("/v2/search")).toBe("/search");
    expect(normalizeAppPathname("/v2/search/")).toBe("/search");
    expect(normalizeAppPathname("/v2/insights")).toBe("/insights");
    expect(normalizeAppPathname("/v2/sessions/abc")).toBe("/sessions/abc");
    expect(normalizeAppPathname("/v2/sessions/abc/")).toBe("/sessions/abc");
  });

  it("keeps root-like values stable", () => {
    expect(normalizeAppPathname("/")).toBe("/");
    expect(normalizeAppPathname("/v2")).toBe("/");
    expect(normalizeAppPathname("")).toBe("/");
    expect(normalizeAppPathname(null)).toBe("/");
  });

  it("matches nested routes after basePath normalization", () => {
    expect(matchesAppPath("/v2/search", "/search")).toBe(true);
    expect(matchesAppPath("/v2/sessions/abc", "/sessions")).toBe(true);
    expect(matchesAppPath("/v2/graph", "/sessions")).toBe(false);
  });
});
