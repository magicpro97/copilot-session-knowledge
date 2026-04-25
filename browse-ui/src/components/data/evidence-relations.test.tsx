import { describe, expect, it } from "vitest";

import {
  edgeColor,
  formatConfidence,
  relationTypeColor,
  relationTypeLabel,
} from "@/components/data/evidence-relations";
import type { EvidenceEdge, GraphEdge } from "@/lib/api/types";

describe("evidence relation helpers", () => {
  it("returns deterministic labels and colors", () => {
    expect(relationTypeLabel("RESOLVED_BY")).toBe("Resolved by");
    expect(relationTypeColor("TAG_OVERLAP")).toBe("#f59e0b");
  });

  it("uses safe fallback label and color for unknown relation types", () => {
    expect(relationTypeLabel("CUSTOM_LINK")).toBe("CUSTOM_LINK");
    expect(relationTypeColor("CUSTOM_LINK")).toBe("#64748b");
  });

  it("colors evidence edges by relation type", () => {
    const edge: EvidenceEdge = {
      source: "n1",
      target: "n2",
      relation_type: "SAME_SESSION",
      confidence: 0.7,
    };

    expect(edgeColor(edge, 0.5)).toBe("rgba(20, 184, 166, 0.5)");
  });

  it("falls back for non-evidence edges", () => {
    const edge: GraphEdge = {
      source: "n1",
      target: "n2",
      relation: "legacy",
    };

    expect(edgeColor(edge, 0.5)).toBe("rgba(148, 163, 184, 0.45)");
  });

  it("formats confidence as percentage", () => {
    expect(formatConfidence(0.81)).toBe("81%");
    expect(formatConfidence(Number.NaN)).toBe("n/a");
  });
});
