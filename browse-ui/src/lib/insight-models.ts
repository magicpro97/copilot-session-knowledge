/**
 * Fetch-free insight models shared by /graph and /insights surfaces.
 *
 * These types are presentational adapters — they do NOT extend API types.
 * Consumer code maps API responses onto these shapes before passing them
 * to insight components or derivation helpers.
 */

export type InsightSeverity = "info" | "warning" | "critical";

export type InsightConfidenceLevel = "low" | "medium" | "high";

/** A single finding displayed in an InsightFindingCard. */
export interface InsightFinding {
  id: string;
  title: string;
  detail: string;
  severity: InsightSeverity;
  /** Optional explanation of why this finding matters — rendered below detail as muted text. */
  why?: string;
}

/** A recommended remediation action. */
export interface InsightAction {
  id: string;
  title: string;
  detail?: string;
  /** Shell command the user can run to act on this recommendation. */
  command?: string;
}

/** A single stat tile shown in overview grids. */
export interface InsightMetricTile {
  label: string;
  value: string;
  /** Optional contextual note rendered below the value. */
  note?: string;
}

/** Summary counts derived from a set of findings — used for summary badges. */
export interface InsightFindingsSummary {
  total: number;
  critical: number;
  warning: number;
  info: number;
}
