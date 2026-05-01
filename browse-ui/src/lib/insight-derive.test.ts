import { describe, expect, it } from "vitest";

import {
  deriveSeverityFromScore,
  deriveConfidenceLevel,
  formatInsightConfidence,
  formatHealthScore,
  isActionable,
  severityRank,
  sortFindingsBySeverity,
  summarizeFindingsCount,
} from "@/lib/insight-derive";
import type { InsightAction, InsightFinding } from "@/lib/insight-models";

describe("severityRank", () => {
  it("assigns highest rank to critical", () => {
    expect(severityRank("critical")).toBeGreaterThan(severityRank("warning"));
    expect(severityRank("warning")).toBeGreaterThan(severityRank("info"));
  });
});

describe("deriveSeverityFromScore", () => {
  it("returns info for scores >= 70", () => {
    expect(deriveSeverityFromScore(100)).toBe("info");
    expect(deriveSeverityFromScore(70)).toBe("info");
  });

  it("returns warning for scores in [40, 70)", () => {
    expect(deriveSeverityFromScore(69)).toBe("warning");
    expect(deriveSeverityFromScore(40)).toBe("warning");
  });

  it("returns critical for scores < 40", () => {
    expect(deriveSeverityFromScore(39)).toBe("critical");
    expect(deriveSeverityFromScore(0)).toBe("critical");
  });

  it("returns warning for non-finite input", () => {
    expect(deriveSeverityFromScore(Number.NaN)).toBe("warning");
    // Infinity is not finite — the guard treats it as warning too
    expect(deriveSeverityFromScore(Infinity)).toBe("warning");
  });
});

describe("deriveConfidenceLevel", () => {
  it("maps >= 0.7 to high", () => {
    expect(deriveConfidenceLevel(1)).toBe("high");
    expect(deriveConfidenceLevel(0.7)).toBe("high");
  });

  it("maps [0.4, 0.7) to medium", () => {
    expect(deriveConfidenceLevel(0.69)).toBe("medium");
    expect(deriveConfidenceLevel(0.4)).toBe("medium");
  });

  it("maps < 0.4 to low", () => {
    expect(deriveConfidenceLevel(0.39)).toBe("low");
    expect(deriveConfidenceLevel(0)).toBe("low");
  });

  it("returns low for non-finite input", () => {
    expect(deriveConfidenceLevel(Number.NaN)).toBe("low");
  });
});

describe("formatInsightConfidence", () => {
  it("formats a 0–1 fraction as a percentage", () => {
    expect(formatInsightConfidence(0.81)).toBe("81%");
    expect(formatInsightConfidence(0)).toBe("0%");
    expect(formatInsightConfidence(1)).toBe("100%");
  });

  it("returns n/a for non-finite input", () => {
    expect(formatInsightConfidence(Number.NaN)).toBe("n/a");
  });
});

describe("formatHealthScore", () => {
  it("rounds to nearest integer", () => {
    expect(formatHealthScore(82.5)).toBe("83");
    expect(formatHealthScore(82.4)).toBe("82");
  });

  it("returns — for non-finite input", () => {
    expect(formatHealthScore(Number.NaN)).toBe("—");
  });
});

describe("isActionable", () => {
  it("returns true when action has a non-empty command", () => {
    const action: InsightAction = { id: "a1", title: "Fix it", command: "python3 fix.py" };
    expect(isActionable(action)).toBe(true);
  });

  it("returns false when command is absent", () => {
    const action: InsightAction = { id: "a2", title: "No command" };
    expect(isActionable(action)).toBe(false);
  });

  it("returns false when command is whitespace-only", () => {
    const action: InsightAction = { id: "a3", title: "Empty command", command: "   " };
    expect(isActionable(action)).toBe(false);
  });
});

describe("sortFindingsBySeverity", () => {
  it("places critical before warning before info", () => {
    const findings: InsightFinding[] = [
      { id: "f1", title: "Info", detail: "", severity: "info" },
      { id: "f2", title: "Critical", detail: "", severity: "critical" },
      { id: "f3", title: "Warning", detail: "", severity: "warning" },
    ];
    const sorted = sortFindingsBySeverity(findings);
    expect(sorted.map((f) => f.severity)).toEqual(["critical", "warning", "info"]);
  });

  it("does not mutate the original array", () => {
    const findings: InsightFinding[] = [
      { id: "f1", title: "A", detail: "", severity: "warning" },
      { id: "f2", title: "B", detail: "", severity: "critical" },
    ];
    const original = [...findings];
    sortFindingsBySeverity(findings);
    expect(findings).toEqual(original);
  });
});

describe("summarizeFindingsCount", () => {
  it("counts findings per severity correctly", () => {
    const findings: InsightFinding[] = [
      { id: "f1", title: "A", detail: "", severity: "critical" },
      { id: "f2", title: "B", detail: "", severity: "critical" },
      { id: "f3", title: "C", detail: "", severity: "warning" },
      { id: "f4", title: "D", detail: "", severity: "info" },
    ];
    expect(summarizeFindingsCount(findings)).toEqual({
      total: 4,
      critical: 2,
      warning: 1,
      info: 1,
    });
  });

  it("returns zeros for an empty list", () => {
    expect(summarizeFindingsCount([])).toEqual({ total: 0, critical: 0, warning: 0, info: 0 });
  });
});
