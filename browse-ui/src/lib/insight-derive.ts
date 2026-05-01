/**
 * Pure derivation helpers for the shared insight layer.
 *
 * All functions are free of side-effects and do not import from API hooks or
 * the API types module. They operate only on the fetch-free models in
 * insight-models.ts.
 */

import type {
  InsightAction,
  InsightConfidenceLevel,
  InsightFinding,
  InsightFindingsSummary,
  InsightSeverity,
} from "@/lib/insight-models";

const SEVERITY_RANK: Record<InsightSeverity, number> = {
  critical: 2,
  warning: 1,
  info: 0,
};

/** Returns a numeric rank so findings can be sorted highest-severity first. */
export function severityRank(severity: InsightSeverity): number {
  return SEVERITY_RANK[severity] ?? 0;
}

/**
 * Derive a severity level from a 0–100 health score.
 *
 * Thresholds are intentionally conservative:
 *   >= 70  → info (healthy enough; surface informational findings)
 *   >= 40  → warning (noticeable degradation)
 *   <  40  → critical (requires attention)
 */
export function deriveSeverityFromScore(score: number): InsightSeverity {
  if (!Number.isFinite(score)) return "warning";
  if (score >= 70) return "info";
  if (score >= 40) return "warning";
  return "critical";
}

/**
 * Map a 0–1 confidence fraction to a human-readable level.
 *
 * Thresholds match the knowledge-insights backend convention:
 *   >= 0.7  → high
 *   >= 0.4  → medium
 *   <  0.4  → low
 */
export function deriveConfidenceLevel(confidence: number): InsightConfidenceLevel {
  if (!Number.isFinite(confidence)) return "low";
  if (confidence >= 0.7) return "high";
  if (confidence >= 0.4) return "medium";
  return "low";
}

/**
 * Format a 0–1 confidence value as a percentage string.
 * Returns "n/a" for non-finite inputs (matches evidence-relations.ts convention).
 */
export function formatInsightConfidence(confidence: number): string {
  if (!Number.isFinite(confidence)) return "n/a";
  return `${Math.round(confidence * 100)}%`;
}

/**
 * Format a 0–100 health score for display.
 * Returns "—" for non-finite inputs.
 */
export function formatHealthScore(score: number): string {
  if (!Number.isFinite(score)) return "—";
  return String(Math.round(score));
}

/** Returns true when the action carries a shell command the user can run. */
export function isActionable(action: InsightAction): boolean {
  return typeof action.command === "string" && action.command.trim().length > 0;
}

/**
 * Sort findings so critical entries come first, then warning, then info.
 * Stable relative order within each severity bucket is preserved.
 */
export function sortFindingsBySeverity(findings: InsightFinding[]): InsightFinding[] {
  return [...findings].sort((a, b) => severityRank(b.severity) - severityRank(a.severity));
}

/**
 * Attempt to extract a consequence/impact clause from an alert detail string.
 *
 * Recognizes two patterns (checked in order):
 *   1. Em-dash separator: "Metric is high — this affects retrieval quality." → "this affects retrieval quality."
 *   2. Multi-sentence:    "Metric is high. This degrades results." → "This degrades results."
 *
 * Returns undefined when no extractable consequence clause is found.
 */
export function deriveWhyFromDetail(detail: string): string | undefined {
  const emDash = detail.indexOf(" — ");
  if (emDash !== -1) {
    const remainder = detail.slice(emDash + 3).trim();
    if (remainder.length > 0) return remainder;
  }
  // Match sentence-ending punctuation only after a word of 3+ lowercase letters
  // to avoid splitting on abbreviations like "e.g.", "approx.", "vs."
  const sentenceRe = /(?<=[a-z]{3})[.!?]\s+/;
  const sentenceMatch = sentenceRe.exec(detail);
  if (sentenceMatch && sentenceMatch.index !== undefined) {
    const remainder = detail.slice(sentenceMatch.index + 1).trimStart();
    if (remainder.length > 0) return remainder;
  }
  return undefined;
}

/**
 * Convert an array of natural-language improvement action strings into InsightAction objects.
 *
 * Derivation rules per item:
 *   - id:     `retro-action-{index}`
 *   - title:  first sentence (up to first sentence-ending punctuation), truncated to 80 chars
 *   - detail: remainder after the first sentence (omitted when absent)
 */
export function deriveActionsFromStrings(actions: string[]): InsightAction[] {
  return actions.map((text, i) => {
    const match = text.match(/[.!?]\s+/);
    if (match && match.index !== undefined) {
      const splitAt = match.index + 1;
      const rawTitle = text.slice(0, splitAt).trim();
      const detail = text.slice(splitAt).trim();
      return {
        id: `retro-action-${i}`,
        title: rawTitle.length > 80 ? `${rawTitle.slice(0, 79)}…` : rawTitle,
        detail: detail.length > 0 ? detail : undefined,
      };
    }
    return {
      id: `retro-action-${i}`,
      title: text.length > 80 ? `${text.slice(0, 79)}…` : text,
    };
  });
}

/**
 * Compute per-severity counts from a list of findings.
 * Useful for rendering summary badges without iterating multiple times.
 */
export function summarizeFindingsCount(findings: InsightFinding[]): InsightFindingsSummary {
  const result: InsightFindingsSummary = {
    total: findings.length,
    critical: 0,
    warning: 0,
    info: 0,
  };
  for (const f of findings) {
    if (f.severity === "critical") result.critical += 1;
    else if (f.severity === "warning") result.warning += 1;
    else result.info += 1;
  }
  return result;
}
