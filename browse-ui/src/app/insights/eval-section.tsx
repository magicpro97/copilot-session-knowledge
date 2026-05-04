"use client";

import { useMemo } from "react";

import { Banner } from "@/components/data/banner";
import { EmptyState } from "@/components/data/empty-state";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { useEval, useKnowledgeInsights } from "@/lib/api/hooks";
import type { EvalAggRow, EvalComment } from "@/lib/api/types";
import { formatNumber } from "@/lib/formatters";
import { useInsightsTab } from "./insights-tab-context";

function formatVerdict(verdict: -1 | 0 | 1): string {
  if (verdict === 1) return "👍";
  if (verdict === -1) return "👎";
  return "😐";
}

const APPROVAL_LABELS = ["0–20%", "20–40%", "40–60%", "60–80%", "80–100%"];

/** Bar chart showing distribution of per-query approval rates across 5 buckets. */
function ApprovalHistogram({ rows }: { rows: EvalAggRow[] }) {
  if (!rows.length) return null;

  const counts = [0, 0, 0, 0, 0];
  for (const row of rows) {
    const rate = row.total > 0 ? (row.up / row.total) * 100 : 0;
    counts[Math.min(Math.floor(rate / 20), 4)]++;
  }
  const maxCount = Math.max(...counts, 1);

  return (
    <div className="space-y-1">
      <h3 className="text-sm font-medium">Approval rate distribution</h3>
      <div className="flex h-12 items-end gap-1">
        {counts.map((count, i) => (
          <div
            key={APPROVAL_LABELS[i]}
            className="bg-primary min-h-0 flex-1 rounded-sm"
            style={{ height: `${(count / maxCount) * 100}%` }}
            title={`${APPROVAL_LABELS[i]}: ${count} quer${count === 1 ? "y" : "ies"}`}
          />
        ))}
      </div>
      <div className="flex gap-1">
        {APPROVAL_LABELS.map((label) => (
          <span key={label} className="text-muted-foreground flex-1 text-center text-xs">
            {label}
          </span>
        ))}
      </div>
    </div>
  );
}

/** Progress bar showing what fraction of knowledge entries have embeddings. */
function EmbeddingCoverageStat() {
  const { host, diagnosticsEnabled } = useInsightsTab();
  const insightsQuery = useKnowledgeInsights(host, diagnosticsEnabled);
  const pct = insightsQuery.data?.overview?.embedding_pct;
  if (pct === undefined || pct === null) return null;

  const rounded = Math.round(pct);
  return (
    <div className="space-y-1">
      <h3 className="text-sm font-medium">Embedding coverage</h3>
      <div className="flex items-center gap-2">
        <div className="bg-muted h-2 flex-1 overflow-hidden rounded-full">
          <div className="bg-primary h-full rounded-full" style={{ width: `${pct}%` }} />
        </div>
        <span className="text-muted-foreground w-10 text-right text-xs">{rounded}%</span>
      </div>
      <p className="text-muted-foreground text-xs">
        {rounded}% of knowledge entries have embeddings
      </p>
    </div>
  );
}

/** Daily feedback volume sparkline for the last 14 days. */
function FeedbackTrend({ comments }: { comments: EvalComment[] }) {
  const days = useMemo(() => {
    if (!comments.length) return null;

    const today = new Date();
    today.setHours(0, 0, 0, 0);
    const slots: { date: string; count: number }[] = [];
    for (let i = 13; i >= 0; i--) {
      const d = new Date(today);
      d.setDate(d.getDate() - i);
      slots.push({ date: d.toISOString().slice(0, 10), count: 0 });
    }
    for (const c of comments) {
      const date = c.created_at.slice(0, 10);
      const slot = slots.find((s) => s.date === date);
      if (slot) slot.count++;
    }
    return slots;
  }, [comments]);

  if (!days) return null;

  const maxCount = Math.max(...days.map((d) => d.count), 1);

  return (
    <div className="space-y-1">
      <h3 className="text-sm font-medium">Feedback activity (14 days)</h3>
      <div className="flex h-12 items-end gap-px">
        {days.map((day) => (
          <div
            key={day.date}
            className="bg-primary min-h-0 flex-1 rounded-sm"
            style={{ height: `${(day.count / maxCount) * 100}%` }}
            title={`${day.date}: ${day.count}`}
          />
        ))}
      </div>
    </div>
  );
}

/** Inner content shared by EvalSection (collapsible) and SearchQualityTab (full view). */
export function EvalBody({ evalQuery }: { evalQuery: ReturnType<typeof useEval> }) {
  const rows = useMemo(() => evalQuery.data?.aggregation ?? [], [evalQuery.data?.aggregation]);
  const comments = useMemo(
    () => evalQuery.data?.recent_comments ?? [],
    [evalQuery.data?.recent_comments]
  );

  if (evalQuery.isLoading) {
    return (
      <div className="space-y-2">
        <Skeleton className="h-8 w-full" />
        <Skeleton className="h-8 w-full" />
        <Skeleton className="h-8 w-full" />
      </div>
    );
  }
  if (evalQuery.isError) {
    return (
      <Banner
        tone="warning"
        title="Search-quality stats unavailable"
        description={
          evalQuery.error instanceof Error
            ? evalQuery.error.message
            : "Could not load /api/eval/stats."
        }
      />
    );
  }

  return (
    <div className="space-y-4">
      {/* ApprovalHistogram only when aggregation data exists */}
      <ApprovalHistogram rows={rows} />

      {/* Table section — empty state applies only to the table */}
      {rows.length > 0 ? (
        <>
          <div className="rounded-xl border">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Query</TableHead>
                  <TableHead className="text-right">👍</TableHead>
                  <TableHead className="text-right">👎</TableHead>
                  <TableHead className="text-right">😐</TableHead>
                  <TableHead className="text-right">Total</TableHead>
                  <TableHead className="text-right">Approval</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {rows.slice(0, 12).map((row) => {
                  const approval = row.total > 0 ? Math.round((row.up / row.total) * 100) : 0;
                  return (
                    <TableRow key={row.query}>
                      <TableCell className="max-w-[26rem] truncate" title={row.query}>
                        {row.query}
                      </TableCell>
                      <TableCell className="text-right">{formatNumber(row.up)}</TableCell>
                      <TableCell className="text-right">{formatNumber(row.down)}</TableCell>
                      <TableCell className="text-right">{formatNumber(row.neutral)}</TableCell>
                      <TableCell className="text-right">{formatNumber(row.total)}</TableCell>
                      <TableCell className="text-right">{approval}%</TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          </div>

          {comments.length > 0 ? (
            <div className="space-y-2">
              <h3 className="text-sm font-medium">Recent feedback comments</h3>
              <div className="space-y-2">
                {comments.slice(0, 8).map((comment, index) => (
                  <div
                    key={`${comment.result_id}-${index}`}
                    className="rounded-lg border px-3 py-2"
                  >
                    <p className="text-sm font-medium">
                      {formatVerdict(comment.verdict)} {comment.query}
                    </p>
                    <p className="text-muted-foreground text-sm">{comment.comment}</p>
                  </div>
                ))}
              </div>
            </div>
          ) : (
            <EmptyState
              title="No search comments yet"
              description="Recent free-text feedback will appear here when users add comments."
            />
          )}
        </>
      ) : (
        <EmptyState
          title="No search evaluations yet"
          description="Search quality data will appear here once users rate search results. Try a search and use the thumbs-up / thumbs-down buttons."
        />
      )}

      {/* EmbeddingCoverageStat and FeedbackTrend always render — independent data sources */}
      <div className="grid gap-3 sm:grid-cols-2">
        <EmbeddingCoverageStat />
        <FeedbackTrend comments={comments} />
      </div>
    </div>
  );
}

function EvalSectionContent() {
  const { host, diagnosticsEnabled } = useInsightsTab();
  const evalQuery = useEval(host, diagnosticsEnabled);

  const rows = useMemo(() => evalQuery.data?.aggregation ?? [], [evalQuery.data?.aggregation]);

  if (evalQuery.isSuccess && rows.length === 0) {
    return null;
  }

  return (
    <details className="bg-card rounded-xl border p-4">
      <summary className="cursor-pointer list-none text-sm font-medium">
        <span className="inline-flex items-center gap-2">
          Search quality
          {rows.length > 0 ? (
            <Badge variant="outline">{formatNumber(rows.length)} queries</Badge>
          ) : null}
        </span>
      </summary>

      <div className="mt-4">
        <EvalBody evalQuery={evalQuery} />
      </div>
    </details>
  );
}

export function EvalSection() {
  const { diagnosticsEnabled } = useInsightsTab();
  if (!diagnosticsEnabled) return null;
  return <EvalSectionContent />;
}
