"use client";

import type { ColumnDef } from "@tanstack/react-table";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useMemo } from "react";

import { EvalSection } from "@/app/insights/eval-section";
import { KnowledgeInsightsSection } from "@/app/insights/knowledge-insights-section";
import { ResearchPackSection } from "@/app/insights/research-pack-section";
import { RetroSection } from "@/app/insights/retro-section";
import { AreaChart } from "@/components/charts/area-chart";
import { BarChart } from "@/components/charts/bar-chart";
import { DonutChart } from "@/components/charts/donut-chart";
import { Banner } from "@/components/data/banner";
import { DataTable } from "@/components/data/data-table";
import { EmptyState } from "@/components/data/empty-state";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useDashboard } from "@/lib/api/hooks";
import type { ModuleCount, RedFlag } from "@/lib/api/types";
import { formatNumber, formatSessionIdBadgeText } from "@/lib/formatters";

type KpiTileProps = {
  label: string;
  value: number;
  href: string;
};

function KpiTile({ label, value, href }: KpiTileProps) {
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

export function DashboardTab() {
  const router = useRouter();
  const dashboard = useDashboard();

  const redFlagColumns = useMemo<ColumnDef<RedFlag>[]>(
    () => [
      {
        id: "session",
        header: "Session",
        accessorKey: "session_id",
        cell: ({ row }) => (
          <span className="font-mono text-xs">
            {formatSessionIdBadgeText(row.original.session_id)}
          </span>
        ),
      },
      {
        id: "events",
        header: "Events",
        accessorKey: "events",
        cell: ({ row }) => formatNumber(row.original.events),
      },
      {
        id: "summary",
        header: "Summary",
        accessorKey: "summary",
        cell: ({ row }) => (
          <span className="text-muted-foreground line-clamp-1 max-w-[40rem] text-sm">
            {row.original.summary?.trim() || "No summary"}
          </span>
        ),
      },
    ],
    []
  );

  const topModulesColumns = useMemo<ColumnDef<ModuleCount>[]>(
    () => [
      {
        id: "module",
        header: "Module",
        accessorKey: "module",
        cell: ({ row }) => (
          <span className="text-foreground font-mono text-xs">{row.original.module}</span>
        ),
      },
      {
        id: "count",
        header: "References",
        accessorKey: "count",
        cell: ({ row }) => formatNumber(row.original.count),
      },
    ],
    []
  );

  if (dashboard.isLoading) {
    return (
      <div className="space-y-6">
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
          {Array.from({ length: 4 }).map((_, index) => (
            <div key={`insights-kpi-${index}`} className="bg-card rounded-xl border p-4">
              <Skeleton className="h-4 w-24" />
              <Skeleton className="mt-4 h-8 w-24" />
            </div>
          ))}
        </div>
        <div className="grid gap-4 xl:grid-cols-2">
          <Skeleton className="h-80 rounded-xl" />
          <Skeleton className="h-80 rounded-xl" />
        </div>
        <Skeleton className="h-64 rounded-xl" />
      </div>
    );
  }

  if (dashboard.isError || !dashboard.data) {
    return (
      <Banner
        tone="danger"
        title="Dashboard stats unavailable"
        description={
          dashboard.error instanceof Error
            ? dashboard.error.message
            : "Could not load /api/dashboard/stats."
        }
        actions={
          <Button type="button" variant="outline" size="sm" onClick={() => dashboard.refetch()}>
            Retry
          </Button>
        }
      />
    );
  }

  const stats = dashboard.data;
  const totalCategoryEntries = stats.by_category.reduce((sum, entry) => sum + entry.count, 0);
  const recentSessionTotal = stats.sessions_per_day.reduce((sum, entry) => sum + entry.count, 0);
  const sessionsPerDayChartData: Record<string, unknown>[] = stats.sessions_per_day.map(
    (entry) => ({
      date: entry.date,
      count: entry.count,
    })
  );
  const byCategoryChartData: Record<string, unknown>[] = stats.by_category.map((entry) => ({
    name: entry.name,
    count: entry.count,
  }));
  const weeklyMistakesChartData: Record<string, unknown>[] = stats.weekly_mistakes.map((entry) => ({
    week: entry.week,
    count: entry.count,
  }));

  return (
    <div className="space-y-6">
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <KpiTile label="Sessions" value={stats.totals.sessions} href="/sessions" />
        <KpiTile
          label="Knowledge entries"
          value={stats.totals.knowledge_entries}
          href="/search?src=knowledge"
        />
        <KpiTile label="Relations" value={stats.totals.relations} href="/graph" />
        <KpiTile label="Embeddings" value={stats.totals.embeddings} href="/graph" />
      </div>

      <div className="grid gap-4 xl:grid-cols-2">
        <div className="space-y-3">
          <Banner
            tone="info"
            title="Sessions/day shows total counts only"
            description="Current API shape is {date, count}. Per-source breakdown is not available yet."
          />
          {stats.sessions_per_day.length >= 3 ? (
            <AreaChart
              data={sessionsPerDayChartData}
              xKey="date"
              yKey="count"
              title="Sessions per day"
              description="Indexed sessions over the last 30 days."
            />
          ) : (
            <EmptyState
              title="Not enough daily history for a trend chart"
              description={`Last 30 days total: ${formatNumber(recentSessionTotal)} sessions.`}
            />
          )}
        </div>

        {totalCategoryEntries > 0 ? (
          <DonutChart
            data={byCategoryChartData}
            nameKey="name"
            valueKey="count"
            title="Knowledge by category"
            description="Distribution of captured knowledge entries."
          />
        ) : (
          <EmptyState
            title="No knowledge categories yet"
            description="Category distribution appears after entries are extracted into the knowledge table."
          />
        )}
      </div>

      <section className="space-y-3">
        <h2 className="text-sm font-medium">Red-flag sessions</h2>
        {stats.red_flags.length > 0 ? (
          <DataTable
            columns={redFlagColumns}
            data={stats.red_flags}
            onRowClick={(row) => router.push(`/sessions/${encodeURIComponent(row.session_id)}`)}
          />
        ) : (
          <Banner
            tone="success"
            title="No red flags"
            description="No high-event sessions are currently flagged as missing learnings."
          />
        )}
      </section>

      <div className="grid gap-4 xl:grid-cols-2">
        {stats.weekly_mistakes.length >= 2 ? (
          <BarChart
            data={weeklyMistakesChartData}
            xKey="week"
            yKey="count"
            title="Mistakes per week"
            description="Mistake entries grouped by week."
          />
        ) : (
          <EmptyState
            title="Not enough weekly points for a chart"
            description="Need at least two weekly buckets to show trend direction."
          />
        )}

        <section className="space-y-3">
          <h2 className="text-sm font-medium">Top referenced modules</h2>
          <DataTable
            columns={topModulesColumns}
            data={stats.top_modules}
            emptyTitle="No module references yet"
            emptyDescription="Module extraction appears once knowledge content includes parseable Python file paths."
          />
        </section>
      </div>

      <EvalSection />
      <RetroSection />
      <KnowledgeInsightsSection />
      <ResearchPackSection />
    </div>
  );
}
