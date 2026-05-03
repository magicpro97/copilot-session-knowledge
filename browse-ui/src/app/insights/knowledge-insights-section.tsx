"use client";

import { Banner } from "@/components/data/banner";
import { InsightActionList } from "@/components/data/insight-action-list";
import { InsightExplainer } from "@/components/data/insight-explainer";
import { InsightFindingCard } from "@/components/data/insight-finding-card";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { useKnowledgeInsights } from "@/lib/api/hooks";
import type {
  KnowledgeInsightsAlert,
  KnowledgeInsightsEntry,
  KnowledgeInsightsResponse,
  KnowledgeInsightsToward100,
} from "@/lib/api/types";
import { deriveWhyFromDetail } from "@/lib/insight-derive";
import { formatNumber } from "@/lib/formatters";

const ENTRY_CATEGORY_LABELS: Record<string, string> = {
  mistakes: "Mistakes",
  patterns: "Patterns",
  decisions: "Decisions",
  tools: "Tools",
};

function OverviewGrid({ overview }: { overview: KnowledgeInsightsResponse["overview"] }) {
  const tiles = [
    { label: "Health score", value: `${formatNumber(overview.health_score)}` },
    { label: "Entries", value: formatNumber(overview.total_entries) },
    { label: "Sessions", value: formatNumber(overview.sessions) },
    { label: "High confidence", value: `${overview.high_confidence_pct.toFixed(1)}%` },
    { label: "Low confidence", value: `${overview.low_confidence_pct.toFixed(1)}%` },
    { label: "Stale", value: `${overview.stale_pct.toFixed(1)}%` },
    { label: "Relation density", value: overview.relation_density.toFixed(2) },
    { label: "Embeddings", value: `${overview.embedding_pct.toFixed(1)}%` },
  ];
  return (
    <div className="grid gap-2 sm:grid-cols-4">
      {tiles.map(({ label, value }) => (
        <div key={label} className="rounded-lg border px-3 py-2">
          <p className="text-muted-foreground text-xs">{label}</p>
          <p className="mt-0.5 text-base font-semibold">{value}</p>
        </div>
      ))}
    </div>
  );
}

function AlertsList({ alerts }: { alerts: KnowledgeInsightsAlert[] }) {
  if (alerts.length === 0) return null;
  return (
    <div className="space-y-2">
      <p className="text-xs font-medium">Quality alerts</p>
      <div role="list" className="space-y-1">
        {alerts.map((alert) => (
          <InsightFindingCard
            key={alert.id}
            finding={{
              id: alert.id,
              title: alert.title,
              detail: alert.detail,
              severity: alert.severity,
              why: deriveWhyFromDetail(alert.detail),
            }}
          />
        ))}
      </div>
    </div>
  );
}

function ActionsList({ actions }: { actions: KnowledgeInsightsResponse["recommended_actions"] }) {
  return (
    <InsightActionList
      actions={actions.map((a) => ({
        id: a.id,
        title: a.title,
        detail: a.detail,
        command: a.command,
      }))}
    />
  );
}

function HotFiles({ files }: { files: KnowledgeInsightsResponse["hot_files"] }) {
  if (files.length === 0) return null;
  return (
    <div>
      <p className="text-xs font-medium">Hot files</p>
      <ul className="mt-1 space-y-0.5">
        {files.map((f) => (
          <li key={f.path} className="flex items-center gap-2 text-xs">
            <code className="text-foreground min-w-0 flex-1 truncate font-mono text-[11px]">
              {f.path}
            </code>
            <Badge variant="outline" className="shrink-0 text-[10px]">
              {formatNumber(f.references)} refs
            </Badge>
          </li>
        ))}
      </ul>
    </div>
  );
}

function NoiseTitles({ titles }: { titles: KnowledgeInsightsResponse["recurring_noise_titles"] }) {
  if (titles.length === 0) return null;
  return (
    <div>
      <p className="text-xs font-medium">Recurring noise titles</p>
      <p className="text-muted-foreground mt-0.5 text-xs">
        These titles appear frequently and may explain why raw DB browsing feels noisy.
      </p>
      <ul className="mt-1 space-y-0.5">
        {titles.map((t) => (
          <li key={`${t.title}-${t.category}`} className="flex items-center gap-2 text-xs">
            <span className="text-foreground min-w-0 flex-1 truncate">{t.title}</span>
            <Badge variant="secondary" className="shrink-0 text-[10px]">
              {t.category}
            </Badge>
            <span className="text-muted-foreground shrink-0 text-[10px]">
              ×{t.entry_count} · conf {t.avg_confidence.toFixed(2)}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function EntriesByCategory({ entries }: { entries: KnowledgeInsightsResponse["entries"] }) {
  const categories = (["mistakes", "patterns", "decisions", "tools"] as const).filter(
    (cat) => entries[cat].length > 0
  );
  if (categories.length === 0) return null;

  return (
    <div className="space-y-3">
      <p className="text-xs font-medium">Representative entries by category</p>
      {categories.map((cat) => (
        <div key={cat}>
          <p className="text-muted-foreground mb-1 text-[11px] font-medium tracking-wide uppercase">
            {ENTRY_CATEGORY_LABELS[cat]} ({entries[cat].length})
          </p>
          <ul className="space-y-1">
            {entries[cat].slice(0, 5).map((entry: KnowledgeInsightsEntry) => (
              <li key={entry.id} className="rounded border px-2 py-1.5">
                <div className="flex items-start gap-2">
                  <span className="text-foreground min-w-0 flex-1 truncate text-xs font-medium">
                    {entry.title}
                  </span>
                  <Badge variant="outline" className="shrink-0 text-[10px]">
                    conf {entry.confidence.toFixed(2)}
                  </Badge>
                </div>
                {entry.summary ? (
                  <p className="text-muted-foreground mt-0.5 line-clamp-1 text-[11px]">
                    {entry.summary}
                  </p>
                ) : null}
              </li>
            ))}
          </ul>
        </div>
      ))}
    </div>
  );
}

function Toward100Panel({
  toward100,
}: {
  toward100: KnowledgeInsightsToward100 | null | undefined;
}) {
  if (!toward100) return null;
  const { total_gap, top_gaps } = toward100;
  if (!top_gaps || top_gaps.length === 0) return null;
  return (
    <div className="rounded-lg border px-3 py-2">
      <p className="mb-1 text-xs font-medium">
        🎯 Toward 100{" "}
        <span className="text-muted-foreground font-normal">
          (total gap: {total_gap.toFixed(1)})
        </span>
      </p>
      <ul className="space-y-1">
        {top_gaps.map((g) => (
          <li key={g.dimension} className="flex items-center gap-2 text-xs">
            <span className="text-foreground min-w-0 flex-1 truncate font-mono text-[11px]">
              {g.dimension}
            </span>
            <Badge variant="secondary" className="shrink-0 text-[10px]">
              {g.pct_of_total_gap.toFixed(1)}%
            </Badge>
            <span className="text-muted-foreground shrink-0 text-[10px]">
              cur {g.current.toFixed(2)} · gap {g.gap.toFixed(2)}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

/**
 * Reusable inner content for the Knowledge Insights panel.
 * Used by both KnowledgeInsightsSection (collapsible) and KnowledgeTab (full view).
 */
export function KnowledgeInsightsBody({
  insights,
}: {
  insights: ReturnType<typeof useKnowledgeInsights>;
}) {
  if (insights.isLoading) {
    return (
      <div className="space-y-2">
        <Skeleton className="h-6 w-48" />
        <Skeleton className="h-20 w-full" />
        <Skeleton className="h-20 w-full" />
      </div>
    );
  }
  if (insights.isError) {
    return (
      <Banner
        tone="warning"
        title="Knowledge insights unavailable"
        description={
          insights.error instanceof Error
            ? insights.error.message
            : "Could not load /api/knowledge/insights."
        }
      />
    );
  }
  if (!insights.data) return null;
  return (
    <div className="space-y-4">
      {insights.data.summary ? (
        <InsightExplainer text={insights.data.summary} generatedAt={insights.data.generated_at} />
      ) : (
        <p className="text-muted-foreground text-xs">generated {insights.data.generated_at}</p>
      )}
      <OverviewGrid overview={insights.data.overview} />
      <AlertsList alerts={insights.data.quality_alerts} />
      <ActionsList actions={insights.data.recommended_actions} />
      <Toward100Panel toward100={insights.data.toward_100} />
      <HotFiles files={insights.data.hot_files} />
      <NoiseTitles titles={insights.data.recurring_noise_titles} />
      <EntriesByCategory entries={insights.data.entries} />
    </div>
  );
}

export function KnowledgeInsightsSection() {
  const insights = useKnowledgeInsights();

  if (insights.isSuccess && !insights.data) {
    return null;
  }

  const alertCount = insights.data?.quality_alerts.length ?? 0;
  const criticalCount =
    insights.data?.quality_alerts.filter((a) => a.severity === "critical").length ?? 0;

  return (
    <details className="bg-card rounded-xl border p-4">
      <summary className="cursor-pointer list-none text-sm font-medium">
        <span className="inline-flex items-center gap-2">
          Knowledge Insights
          {insights.isSuccess && insights.data ? (
            <Badge variant="outline">
              score {formatNumber(insights.data.overview.health_score)}
            </Badge>
          ) : null}
          {criticalCount > 0 ? (
            <Badge variant="destructive">{criticalCount} critical</Badge>
          ) : alertCount > 0 ? (
            <Badge variant="secondary">{alertCount} alerts</Badge>
          ) : null}
        </span>
      </summary>

      <div className="mt-4">
        {insights.isLoading ? (
          <div className="space-y-2">
            <Skeleton className="h-6 w-48" />
            <Skeleton className="h-20 w-full" />
            <Skeleton className="h-20 w-full" />
          </div>
        ) : insights.isError ? (
          <Banner
            tone="warning"
            title="Knowledge insights unavailable"
            description={
              insights.error instanceof Error
                ? insights.error.message
                : "Could not load /api/knowledge/insights."
            }
          />
        ) : insights.data ? (
          <>
            {insights.data.summary ? (
              <p className="text-muted-foreground text-sm">{insights.data.summary}</p>
            ) : null}

            <div className="mt-4 space-y-4">
              <OverviewGrid overview={insights.data.overview} />
              <AlertsList alerts={insights.data.quality_alerts} />
              <ActionsList actions={insights.data.recommended_actions} />
              <Toward100Panel toward100={insights.data.toward_100} />
              <HotFiles files={insights.data.hot_files} />
              <NoiseTitles titles={insights.data.recurring_noise_titles} />
              <EntriesByCategory entries={insights.data.entries} />
            </div>

            <p className="text-muted-foreground mt-4 text-xs">
              generated {insights.data.generated_at}
            </p>
          </>
        ) : null}
      </div>
    </details>
  );
}
