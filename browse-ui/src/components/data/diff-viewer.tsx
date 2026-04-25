import { useMemo, useState } from "react";

import type { DiffResult } from "@/lib/api/types";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { formatNumber } from "@/lib/formatters";

type DiffViewerProps = {
  result: DiffResult;
};

type DiffLine = {
  left: string;
  right: string;
};

function buildSplitDiff(unifiedDiff: string): DiffLine[] {
  return unifiedDiff.split("\n").map((line) => {
    if (line.startsWith("+") && !line.startsWith("+++")) {
      return { left: "", right: line };
    }
    if (line.startsWith("-") && !line.startsWith("---")) {
      return { left: line, right: "" };
    }
    return { left: line, right: line };
  });
}

export function DiffViewer({ result }: DiffViewerProps) {
  const [mode, setMode] = useState<"unified" | "split">("unified");
  const splitDiff = useMemo(() => buildSplitDiff(result.unified_diff), [result.unified_diff]);

  return (
    <Card>
      <CardHeader className="space-y-2">
        <CardTitle>
          Checkpoint diff ({result.from.seq} → {result.to.seq})
        </CardTitle>
        <p className="text-xs text-muted-foreground">
          +{formatNumber(result.stats.added)} added · -{formatNumber(result.stats.removed)} removed
        </p>
        <div className="flex items-center gap-2">
          <Button variant={mode === "unified" ? "default" : "outline"} onClick={() => setMode("unified")}>
            Unified
          </Button>
          <Button variant={mode === "split" ? "default" : "outline"} onClick={() => setMode("split")}>
            Side-by-side
          </Button>
        </div>
      </CardHeader>
      <CardContent>
        {mode === "unified" ? (
          <pre className="max-h-[520px] overflow-auto whitespace-pre-wrap rounded-lg bg-muted/40 p-3 text-xs">
            {result.unified_diff || "(no textual diff output)"}
          </pre>
        ) : (
          <div className="grid gap-2 md:grid-cols-2">
            <pre className="max-h-[520px] overflow-auto whitespace-pre-wrap rounded-lg bg-muted/40 p-3 text-xs">
              {splitDiff.map((line, index) => (
                <div key={`left-${index}`}>{line.left || " "}</div>
              ))}
            </pre>
            <pre className="max-h-[520px] overflow-auto whitespace-pre-wrap rounded-lg bg-muted/40 p-3 text-xs">
              {splitDiff.map((line, index) => (
                <div key={`right-${index}`}>{line.right || " "}</div>
              ))}
            </pre>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
