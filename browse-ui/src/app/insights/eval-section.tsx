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
import { useEval } from "@/lib/api/hooks";
import { formatNumber } from "@/lib/formatters";

function formatVerdict(verdict: -1 | 0 | 1): string {
  if (verdict === 1) return "👍";
  if (verdict === -1) return "👎";
  return "😐";
}

export function EvalSection() {
  const evalQuery = useEval();

  const rows = useMemo(() => evalQuery.data?.aggregation ?? [], [evalQuery.data?.aggregation]);
  const comments = useMemo(
    () => evalQuery.data?.recent_comments ?? [],
    [evalQuery.data?.recent_comments]
  );

  if (evalQuery.isSuccess && rows.length === 0) {
    return null;
  }

  return (
    <details className="rounded-xl border bg-card p-4">
      <summary className="cursor-pointer list-none text-sm font-medium">
        <span className="inline-flex items-center gap-2">
          Search quality
          {rows.length > 0 ? <Badge variant="outline">{formatNumber(rows.length)} queries</Badge> : null}
        </span>
      </summary>

      <div className="mt-4 space-y-4">
        {evalQuery.isLoading ? (
          <div className="space-y-2">
            <Skeleton className="h-8 w-full" />
            <Skeleton className="h-8 w-full" />
            <Skeleton className="h-8 w-full" />
          </div>
        ) : evalQuery.isError ? (
          <Banner
            tone="warning"
            title="Search-quality stats unavailable"
            description={
              evalQuery.error instanceof Error
                ? evalQuery.error.message
                : "Could not load /api/eval/stats."
            }
          />
        ) : (
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
                    <div key={`${comment.result_id}-${index}`} className="rounded-lg border px-3 py-2">
                      <p className="text-sm font-medium">
                        {formatVerdict(comment.verdict)} {comment.query}
                      </p>
                      <p className="text-sm text-muted-foreground">{comment.comment}</p>
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
        )}
      </div>
    </details>
  );
}
