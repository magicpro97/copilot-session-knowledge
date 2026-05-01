"use client";

import { Badge } from "@/components/ui/badge";
import { useEval } from "@/lib/api/hooks";
import { formatNumber } from "@/lib/formatters";
import { EvalBody } from "./eval-section";

export function SearchQualityTab() {
  const evalQuery = useEval();

  const rowCount = evalQuery.data?.aggregation?.length ?? 0;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <h2 className="text-sm font-medium">Search Quality</h2>
        {rowCount > 0 ? (
          <Badge variant="outline">{formatNumber(rowCount)} queries evaluated</Badge>
        ) : null}
      </div>

      <EvalBody evalQuery={evalQuery} />
    </div>
  );
}
