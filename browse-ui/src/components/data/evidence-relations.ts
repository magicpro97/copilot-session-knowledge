import type {
  EvidenceEdge,
  EvidenceRelationType,
  EvidenceRelationTypeValue,
  GraphEdge,
} from "@/lib/api/types";

export type GraphRelationEdge = GraphEdge | EvidenceEdge;

export const EVIDENCE_RELATION_STYLES: Record<
  EvidenceRelationType,
  { label: string; color: string }
> = {
  SAME_SESSION: { label: "Same session", color: "#14b8a6" },
  RESOLVED_BY: { label: "Resolved by", color: "#8b5cf6" },
  TAG_OVERLAP: { label: "Tag overlap", color: "#f59e0b" },
  SAME_TOPIC: { label: "Same topic", color: "#6366f1" },
};

const UNKNOWN_RELATION_STYLE = {
  color: "#64748b",
};

export function isEvidenceEdge(edge: GraphRelationEdge): edge is EvidenceEdge {
  return "relation_type" in edge;
}

export function isKnownEvidenceRelationType(
  type: EvidenceRelationTypeValue
): type is EvidenceRelationType {
  return type in EVIDENCE_RELATION_STYLES;
}

export function relationTypeLabel(type: EvidenceRelationTypeValue): string {
  if (!isKnownEvidenceRelationType(type)) return type;
  return EVIDENCE_RELATION_STYLES[type].label;
}

export function relationTypeColor(type: EvidenceRelationTypeValue): string {
  if (!isKnownEvidenceRelationType(type)) return UNKNOWN_RELATION_STYLE.color;
  return EVIDENCE_RELATION_STYLES[type].color;
}

function withAlpha(hexColor: string, alpha: number): string {
  const normalized = hexColor.replace("#", "");
  if (normalized.length !== 6) return hexColor;
  const value = Math.max(0, Math.min(1, alpha));
  const r = Number.parseInt(normalized.slice(0, 2), 16);
  const g = Number.parseInt(normalized.slice(2, 4), 16);
  const b = Number.parseInt(normalized.slice(4, 6), 16);
  return `rgba(${r}, ${g}, ${b}, ${value})`;
}

export function edgeColor(
  edge: GraphRelationEdge,
  alpha: number,
  fallback = "rgba(148, 163, 184, 0.45)"
): string {
  if (!isEvidenceEdge(edge)) return fallback;
  return withAlpha(relationTypeColor(edge.relation_type), alpha);
}

export function formatConfidence(confidence: number): string {
  if (!Number.isFinite(confidence)) return "n/a";
  return `${Math.round(confidence * 100)}%`;
}
