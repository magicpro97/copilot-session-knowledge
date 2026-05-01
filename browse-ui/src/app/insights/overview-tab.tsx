"use client";

import { BarChart2, BookOpen, RotateCcw, Star } from "lucide-react";
import Link from "next/link";

import { InsightExplainer } from "@/components/data/insight-explainer";
import { InsightFindingCard } from "@/components/data/insight-finding-card";
import { Banner } from "@/components/data/banner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useDashboard, useEval, useKnowledgeInsights, useRetro } from "@/lib/api/hooks";
import { formatHealthScore } from "@/lib/insight-derive";
import type { InsightFinding } from "@/lib/insight-models";
import { formatNumber } from "@/lib/formatters";
import { ResearchPackSection } from "./research-pack-section";

export type InsightsTabKey = "overview" | "knowledge" | "retro" | "search-quality" | "live";

type OverviewTabProps = {
  /** Called when the user clicks a "Go to tab" CTA inside the overview. */
  onNavigate: (tab: InsightsTabKey) => void;
};

function KpiTile({ label, value, href }: { label: string; value: number; href: string }) {
  return (
    <Link
      href={href}
      className="ring-ring block rounded-xl transition outline-none focus-visible:ring-2"
    >
      <Card className="ring-foreground/10 hover:bg-muted/30 h-full ring-1 transition">
        <CardContent className="space-y-2">
          <p className="text-muted-foreground text-sm">{label}</p>
          <p className="text-2xl font-semibold tracking-tight">{formatNumber(value)}</p>
        </CardContent>
      </Card>
    </Link>
  );
}

export function OverviewTab({ onNavigate }: OverviewTabProps) {
  const dashboard = useDashboard();
  const insights = useKnowledgeInsights();
  const retro = useRetro("repo");
  const evalQuery = useEval();

  const criticalFindings: InsightFinding[] =
    insights.data?.quality_alerts
      .filter((a) => a.severity === "critical" || a.severity === "warning")
      .slice(0, 3)
      .map((a) => ({
        id: a.id,
        title: a.title,
        detail: a.detail,
        severity: a.severity,
      })) ?? [];

  return (
    <div className="space-y-6">
      {/* KPI row */}
      {dashboard.isLoading ? (
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
          {Array.from({ length: 4 }).map((_, i) => (
            <div key={`kpi-sk-${i}`} className="bg-card rounded-xl border p-4">
              <Skeleton className="h-4 w-24" />
              <Skeleton className="mt-4 h-8 w-24" />
            </div>
          ))}
        </div>
      ) : dashboard.isError ? (
        <Banner
          tone="danger"
          title="Stats unavailable"
          description="Could not load dashboard stats."
        />
      ) : dashboard.data ? (
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
          <KpiTile label="Sessions" value={dashboard.data.totals.sessions} href="/sessions" />
          <KpiTile
            label="Knowledge entries"
            value={dashboard.data.totals.knowledge_entries}
            href="/search?src=knowledge"
          />
          <KpiTile label="Relations" value={dashboard.data.totals.relations} href="/graph" />
          <KpiTile label="Embeddings" value={dashboard.data.totals.embeddings} href="/graph" />
        </div>
      ) : null}

      {/* Brief panels grid */}
      <div className="grid gap-4 xl:grid-cols-3">
        {/* Knowledge brief */}
        <div className="space-y-3 rounded-xl border p-4">
          <div className="flex items-center justify-between">
            <h2 className="flex items-center gap-2 text-sm font-medium">
              <BookOpen className="size-4" />
              Knowledge
            </h2>
            {insights.data ? (
              <Badge variant="outline">
                health {formatHealthScore(insights.data.overview.health_score)}
              </Badge>
            ) : null}
          </div>

          {insights.isLoading ? (
            <Skeleton className="h-16 w-full" />
          ) : insights.data ? (
            <div className="space-y-2">
              {insights.data.summary ? <InsightExplainer text={insights.data.summary} /> : null}
              {criticalFindings.length > 0 ? (
                <div role="list" className="space-y-1">
                  {criticalFindings.map((f) => (
                    <InsightFindingCard key={f.id} finding={f} />
                  ))}
                </div>
              ) : (
                <p className="text-muted-foreground text-xs">No active alerts.</p>
              )}
            </div>
          ) : insights.isError ? (
            <p className="text-muted-foreground text-xs">Knowledge insights unavailable.</p>
          ) : null}

          <Button
            type="button"
            variant="ghost"
            size="sm"
            className="w-full justify-between"
            onClick={() => onNavigate("knowledge")}
          >
            Full Knowledge insights →
          </Button>
        </div>

        {/* Retro brief */}
        <div className="space-y-3 rounded-xl border p-4">
          <div className="flex items-center justify-between">
            <h2 className="flex items-center gap-2 text-sm font-medium">
              <RotateCcw className="size-4" />
              Retrospective
            </h2>
            {retro.data ? (
              <Badge variant="outline">
                {retro.data.grade_emoji} {retro.data.grade}
              </Badge>
            ) : null}
          </div>

          {retro.isLoading ? (
            <Skeleton className="h-16 w-full" />
          ) : retro.data ? (
            <div className="space-y-2">
              {retro.data.summary ? <InsightExplainer text={retro.data.summary} /> : null}
              <div className="grid grid-cols-2 gap-2">
                {retro.data.available_sections.slice(0, 4).map((section) => {
                  const score = retro.data!.subscores[section];
                  return (
                    <div key={section} className="rounded border px-2 py-1.5">
                      <p className="text-muted-foreground text-[10px] tracking-wide uppercase">
                        {section}
                      </p>
                      <p className="text-sm font-semibold">
                        {score != null ? formatNumber(score) : "–"}
                      </p>
                    </div>
                  );
                })}
              </div>
            </div>
          ) : retro.isError ? (
            <p className="text-muted-foreground text-xs">Retrospective unavailable.</p>
          ) : null}

          <Button
            type="button"
            variant="ghost"
            size="sm"
            className="w-full justify-between"
            onClick={() => onNavigate("retro")}
          >
            Full Retrospective →
          </Button>
        </div>

        {/* Search quality brief */}
        <div className="space-y-3 rounded-xl border p-4">
          <div className="flex items-center justify-between">
            <h2 className="flex items-center gap-2 text-sm font-medium">
              <Star className="size-4" />
              Search Quality
            </h2>
            {evalQuery.data?.aggregation?.length ? (
              <Badge variant="outline">
                {formatNumber(evalQuery.data.aggregation.length)} queries
              </Badge>
            ) : null}
          </div>

          {evalQuery.isLoading ? (
            <Skeleton className="h-16 w-full" />
          ) : evalQuery.data?.aggregation?.length ? (
            <ul className="space-y-1">
              {evalQuery.data.aggregation.slice(0, 3).map((row) => {
                const approval = row.total > 0 ? Math.round((row.up / row.total) * 100) : 0;
                return (
                  <li key={row.query} className="flex items-center gap-2 text-xs">
                    <span className="min-w-0 flex-1 truncate">{row.query}</span>
                    <Badge variant={approval >= 70 ? "outline" : "secondary"}>{approval}%</Badge>
                  </li>
                );
              })}
            </ul>
          ) : evalQuery.isError ? (
            <p className="text-muted-foreground text-xs">Search quality unavailable.</p>
          ) : (
            <p className="text-muted-foreground text-xs">No search quality data yet.</p>
          )}

          <Button
            type="button"
            variant="ghost"
            size="sm"
            className="w-full justify-between"
            onClick={() => onNavigate("search-quality")}
          >
            Full Search Quality →
          </Button>
        </div>
      </div>

      {/* Graph insight link */}
      <div className="flex items-center gap-3 rounded-xl border border-dashed px-4 py-3">
        <BarChart2 className="text-muted-foreground size-4 shrink-0" />
        <div className="min-w-0 flex-1">
          <p className="text-sm font-medium">Graph Insight lens</p>
          <p className="text-muted-foreground text-xs">
            Visual graph analysis, evidence paths, and community clusters live in the Graph view.
          </p>
        </div>
        <Link
          href="/graph#insight"
          className="border-border bg-background hover:bg-muted focus-visible:ring-ring/50 inline-flex h-7 shrink-0 items-center justify-center gap-1 rounded-[min(var(--radius-md),12px)] border px-2.5 text-[0.8rem] font-medium transition-all focus-visible:ring-2"
        >
          View graph →
        </Link>
      </div>

      {/* Research pack */}
      <ResearchPackSection />
    </div>
  );
}
