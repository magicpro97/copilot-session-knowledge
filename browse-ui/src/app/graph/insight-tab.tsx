"use client";

import { ExternalLink } from "lucide-react";
import Link from "next/link";
import { useMemo } from "react";

import { InsightActionList } from "@/components/data/insight-action-list";
import { InsightExplainer } from "@/components/data/insight-explainer";
import { InsightFindingCard } from "@/components/data/insight-finding-card";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  useCommunities,
  useDashboard,
  useEvidenceGraph,
  useKnowledgeInsights,
} from "@/lib/api/hooks";
import { sortFindingsBySeverity } from "@/lib/insight-derive";
import type { InsightAction, InsightFinding } from "@/lib/insight-models";

type GraphTab = "evidence" | "similarity" | "communities";

type InsightTabProps = {
  active: boolean;
  onNavigate: (tab: GraphTab) => void;
};

type MetricTileProps = {
  label: string;
  value: string;
  note?: string;
  loading?: boolean;
};

function MetricTile({ label, value, note, loading }: MetricTileProps) {
  return (
    <div className="bg-card rounded-lg border px-3 py-2">
      <p className="text-muted-foreground text-xs">{label}</p>
      <p className="text-foreground text-xl font-semibold tabular-nums">{loading ? "—" : value}</p>
      {note ? <p className="text-muted-foreground truncate text-xs">{note}</p> : null}
    </div>
  );
}

type DeriveParams = {
  nodeCount: number;
  edgeCount: number;
  truncated: boolean;
  relationTypes: string[];
  /** null = communities still loading or errored (skip community findings) */
  communityCount: number | null;
  communitiesError: boolean;
  embeddingPct: number | null;
  totalKnowledgeEntries: number | null;
};

function deriveGraphFindings({
  nodeCount,
  edgeCount,
  truncated,
  relationTypes,
  communityCount,
  communitiesError,
  embeddingPct,
  totalKnowledgeEntries,
}: DeriveParams): InsightFinding[] {
  const findings: InsightFinding[] = [];

  // 1. Edge coverage
  if (nodeCount === 0) {
    findings.push({
      id: "no-graph-data",
      title: "No evidence graph data loaded",
      detail:
        "The evidence endpoint returned no nodes. Run extract-knowledge to populate knowledge_relations, or check the Evidence tab for errors.",
      severity: "warning",
    });
  } else if (edgeCount === 0) {
    findings.push({
      id: "no-edges",
      title: "Entries are loaded but no edges found",
      detail: `${nodeCount} nodes loaded with no connections. knowledge_relations may be empty — run a full session to create relation records.`,
      severity: "warning",
    });
  } else {
    const edgeRatio = edgeCount / nodeCount;
    if (edgeRatio < 1) {
      findings.push({
        id: "sparse-graph",
        title: `Evidence graph is sparse (${edgeCount} edges, ${nodeCount} nodes)`,
        detail:
          "Many loaded entries have no connections. More sessions and diverse tagging will improve coverage.",
        severity: "info",
      });
    } else {
      findings.push({
        id: "graph-populated",
        title: `Evidence graph has ${edgeCount} connections across ${nodeCount} nodes`,
        detail:
          "Typed edges from knowledge_relations are loaded. Use the Evidence tab to filter by relation type or wing.",
        severity: "info",
      });
    }
  }

  // 2. Cross-session signal check (TAG_OVERLAP is the only cross-session evidence relation)
  if (nodeCount > 0 && edgeCount > 0) {
    const hasTagOverlap = relationTypes.includes("TAG_OVERLAP");
    const hasSameTopic = relationTypes.includes("SAME_TOPIC");
    if (!hasTagOverlap && !hasSameTopic) {
      findings.push({
        id: "no-cross-session",
        title: "No cross-session connections in current view",
        detail:
          "Only intra-session relations (SAME_SESSION, RESOLVED_BY) are visible. Tag your entries with shared tags to create TAG_OVERLAP connections across sessions.",
        severity: "info",
      });
    } else if (hasTagOverlap) {
      findings.push({
        id: "cross-session-signal",
        title: "Cross-session tag connections present",
        detail:
          "TAG_OVERLAP edges connect entries from different sessions that share tags — the primary cross-session signal in the evidence graph.",
        severity: "info",
      });
    }
  }

  // 3. Truncation
  if (truncated) {
    findings.push({
      id: "truncated",
      title: "Evidence graph is truncated to the backend limit",
      detail:
        "Not all edges are shown. Apply wing, category, or relation-type filters in the Evidence tab to see a narrower, complete subset.",
      severity: "info",
    });
  }

  // 4. Communities — skip while loading (null); surface error truthfully
  if (communityCount === null) {
    if (communitiesError) {
      findings.push({
        id: "communities-load-error",
        title: "Communities data failed to load",
        detail:
          "The communities endpoint returned an error. Community structure findings are unavailable.",
        severity: "warning",
      });
    }
    // While loading: skip silently — no false absence finding
  } else if (communityCount === 0 && nodeCount > 0) {
    findings.push({
      id: "no-communities",
      title: "No multi-entry communities detected yet",
      detail:
        "Community detection found only singletons. More evidence edges between distinct entries will produce meaningful thematic clusters.",
      severity: "info",
    });
  } else if (communityCount > 0) {
    findings.push({
      id: "communities-found",
      title: `${communityCount} connected ${communityCount === 1 ? "community" : "communities"} detected`,
      detail:
        "Thematic clusters exist in the evidence graph. See the Communities tab for category breakdowns and representative entries.",
      severity: "info",
    });
  }

  // 5. Embedding coverage (affects Similarity tab quality)
  if (embeddingPct !== null) {
    if (embeddingPct < 50) {
      findings.push({
        id: "low-embedding-coverage",
        title: `Only ${Math.round(embeddingPct)}% of entries have embeddings`,
        detail:
          "Low embedding coverage limits Similarity neighbor quality. Run embed.py to improve coverage.",
        severity: "warning",
      });
    } else if (embeddingPct < 80) {
      findings.push({
        id: "partial-embedding-coverage",
        title: `${Math.round(embeddingPct)}% embedding coverage`,
        detail:
          "Similarity neighbors work for covered entries. Run embed.py on remaining entries for full coverage.",
        severity: "info",
      });
    }
  }

  // 6. Entry coverage gap (graph vs. total entries)
  if (totalKnowledgeEntries !== null && nodeCount > 0 && totalKnowledgeEntries > 0) {
    const coveragePct = Math.round((nodeCount / totalKnowledgeEntries) * 100);
    if (coveragePct < 25) {
      findings.push({
        id: "low-graph-coverage",
        title: `Only ${coveragePct}% of entries appear in the evidence graph`,
        detail: `${totalKnowledgeEntries - nodeCount} entries have no evidence edges. Increase tagging diversity and run more sessions to improve graph coverage.`,
        severity: "warning",
      });
    }
  }

  return findings;
}

/** Graph-specific remediation actions linked to actionable shell commands. */
const GRAPH_REMEDIATION_ACTIONS: InsightAction[] = [
  {
    id: "run-embed",
    title: "Improve Similarity coverage",
    detail: "Generate embeddings for entries missing them.",
    command: "python3 embed.py",
  },
  {
    id: "run-extract",
    title: "Add more evidence edges",
    detail: "Re-run knowledge extraction to build new relation records.",
    command: "python3 extract-knowledge.py",
  },
];

export function InsightTab({ active, onNavigate }: InsightTabProps) {
  const evidenceQ = useEvidenceGraph({});
  const communitiesQ = useCommunities(active);
  const kiQ = useKnowledgeInsights();
  const dashQ = useDashboard();

  const nodeCount = evidenceQ.data?.nodes.length ?? 0;
  const edgeCount = evidenceQ.data?.edges.length ?? 0;
  const truncated = evidenceQ.data?.truncated ?? false;
  const relationTypes = useMemo(
    () => evidenceQ.data?.meta?.relation_types ?? [],
    [evidenceQ.data?.meta?.relation_types]
  );

  const communityCount = useMemo(() => {
    if (communitiesQ.isLoading || communitiesQ.isError) return null;
    return (communitiesQ.data?.communities ?? []).filter((community) => community.entry_count > 1)
      .length;
  }, [communitiesQ.data?.communities, communitiesQ.isLoading, communitiesQ.isError]);

  const embeddingPct = kiQ.data?.overview.embedding_pct ?? null;
  const totalKnowledgeEntries = dashQ.data?.totals.knowledge_entries ?? null;

  const findings = useMemo(
    () =>
      sortFindingsBySeverity(
        deriveGraphFindings({
          nodeCount,
          edgeCount,
          truncated,
          relationTypes,
          communityCount,
          communitiesError: communitiesQ.isError,
          embeddingPct,
          totalKnowledgeEntries,
        })
      ),
    [
      nodeCount,
      edgeCount,
      truncated,
      relationTypes,
      communityCount,
      communitiesQ.isError,
      embeddingPct,
      totalKnowledgeEntries,
    ]
  );

  const warningFindings = useMemo(
    () => findings.filter((f) => f.severity === "warning" || f.severity === "critical"),
    [findings]
  );
  const remediationActions = useMemo(
    () =>
      warningFindings.some((f) =>
        ["no-graph-data", "no-edges", "low-embedding-coverage", "low-graph-coverage"].includes(f.id)
      )
        ? GRAPH_REMEDIATION_ACTIONS
        : [],
    [warningFindings]
  );

  const isEvidenceLoading = evidenceQ.isLoading;
  const embeddingCoverageValue = embeddingPct !== null ? `${Math.round(embeddingPct)}%` : "—";
  const embeddingCoverageNote =
    embeddingPct !== null && embeddingPct < 70 ? "low — run embed.py" : undefined;

  return (
    <div className="space-y-4">
      {/* Key metric tiles */}
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <MetricTile
          label="Entries in graph"
          value={nodeCount.toString()}
          note={totalKnowledgeEntries ? `of ${totalKnowledgeEntries} total` : undefined}
          loading={isEvidenceLoading}
        />
        <MetricTile
          label="Evidence edges"
          value={edgeCount.toString()}
          note="from knowledge_relations"
          loading={isEvidenceLoading}
        />
        <MetricTile
          label="Communities"
          value={communityCount !== null ? communityCount.toString() : "—"}
          note="multi-entry only"
          loading={communitiesQ.isLoading}
        />
        <MetricTile
          label="Embedding coverage"
          value={embeddingCoverageValue}
          note={embeddingCoverageNote}
          loading={kiQ.isLoading}
        />
      </div>

      <div className="grid gap-4 lg:grid-cols-[1fr_20rem]">
        {/* Findings column */}
        <div className="space-y-3">
          <Card>
            <CardHeader>
              <CardTitle className="text-sm">Graph findings</CardTitle>
            </CardHeader>
            <CardContent className="space-y-2">
              {isEvidenceLoading ? (
                <p className="text-muted-foreground text-sm">Loading graph data…</p>
              ) : findings.length === 0 ? (
                <p className="text-muted-foreground text-sm">
                  No findings yet — data is still loading.
                </p>
              ) : (
                <div role="list" className="space-y-2">
                  {findings.map((finding) => (
                    <InsightFindingCard key={finding.id} finding={finding} />
                  ))}
                </div>
              )}
              {remediationActions.length > 0 ? (
                <div className="border-t pt-2">
                  <InsightActionList actions={remediationActions} title="Suggested actions" />
                </div>
              ) : null}
            </CardContent>
          </Card>
        </div>

        {/* Navigation + explainer column */}
        <div className="space-y-3">
          <Card>
            <CardHeader>
              <CardTitle className="text-sm">What this graph shows</CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              <InsightExplainer text="The evidence graph surfaces typed connections between knowledge entries — derived automatically from your sessions via knowledge_relations. Edges are heuristic signals, not hand-curated facts." />
              <InsightExplainer text="Similarity uses semantic embeddings to find entries discussing similar topics, even when no explicit evidence connection exists." />
              <InsightExplainer text="Communities are thematic clusters detected from connected components in the evidence graph. They surface recurring themes across your sessions." />
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="text-sm">Go deeper</CardTitle>
            </CardHeader>
            <CardContent className="space-y-2">
              <Button
                type="button"
                variant="outline"
                size="sm"
                className="w-full justify-start"
                onClick={() => onNavigate("evidence")}
              >
                Evidence graph →
              </Button>
              <Button
                type="button"
                variant="outline"
                size="sm"
                className="w-full justify-start"
                onClick={() => onNavigate("similarity")}
              >
                Similarity →
              </Button>
              <Button
                type="button"
                variant="outline"
                size="sm"
                className="w-full justify-start"
                onClick={() => onNavigate("communities")}
              >
                Communities →
              </Button>
              <div className="border-t pt-2">
                <Link
                  href="/insights"
                  className="text-muted-foreground hover:text-foreground flex items-center gap-1 text-xs"
                >
                  <ExternalLink className="size-3" />
                  Full insights workspace
                </Link>
              </div>
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  );
}
