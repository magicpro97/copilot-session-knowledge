import { useMemo, useState } from "react";

import type { DiffResult } from "@/lib/api/types";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { formatNumber } from "@/lib/formatters";
import { cn } from "@/lib/utils";

type DiffViewerProps = {
  result: DiffResult;
};

type DiffLine = {
  left: string;
  right: string;
  leftKind: DiffLineKind;
  rightKind: DiffLineKind;
};

type DiffLineKind = "add" | "remove" | "hunk" | "meta" | "context";

function getDiffLineKind(line: string): DiffLineKind {
  if (line.startsWith("+") && !line.startsWith("+++")) return "add";
  if (line.startsWith("-") && !line.startsWith("---")) return "remove";
  if (line.startsWith("@@")) return "hunk";
  if (
    line.startsWith("diff ") ||
    line.startsWith("index ") ||
    line.startsWith("---") ||
    line.startsWith("+++")
  ) {
    return "meta";
  }
  return "context";
}

function buildSplitDiff(unifiedDiff: string): DiffLine[] {
  return unifiedDiff.split("\n").map((line) => {
    const kind = getDiffLineKind(line);
    if (kind === "add") {
      return { left: "", right: line, leftKind: "context", rightKind: kind };
    }
    if (kind === "remove") {
      return { left: line, right: "", leftKind: kind, rightKind: "context" };
    }
    return { left: line, right: line, leftKind: kind, rightKind: kind };
  });
}

function diffLineClass(kind: DiffLineKind): string {
  if (kind === "add") {
    return "border-l-2 border-emerald-500/40 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300";
  }
  if (kind === "remove") {
    return "border-l-2 border-rose-500/40 bg-rose-500/10 text-rose-700 dark:text-rose-300";
  }
  if (kind === "hunk") {
    return "border-l-2 border-sky-500/30 bg-sky-500/10 text-sky-700 dark:text-sky-300";
  }
  if (kind === "meta") {
    return "text-muted-foreground bg-muted/30";
  }
  return "";
}

export function DiffViewer({ result }: DiffViewerProps) {
  const [mode, setMode] = useState<"unified" | "split">("unified");
  const unifiedLines = useMemo(() => result.unified_diff.split("\n"), [result.unified_diff]);
  const splitDiff = useMemo(() => buildSplitDiff(result.unified_diff), [result.unified_diff]);

  return (
    <Card>
      <CardHeader className="space-y-2">
        <CardTitle>
          Checkpoint diff ({result.from.seq} → {result.to.seq})
        </CardTitle>
        <p className="text-muted-foreground text-xs">
          +{formatNumber(result.stats.added)} added · -{formatNumber(result.stats.removed)} removed
        </p>
        <div className="flex items-center gap-2">
          <Button
            variant={mode === "unified" ? "default" : "outline"}
            onClick={() => setMode("unified")}
          >
            Unified
          </Button>
          <Button
            variant={mode === "split" ? "default" : "outline"}
            onClick={() => setMode("split")}
          >
            Side-by-side
          </Button>
        </div>
      </CardHeader>
      <CardContent>
        {mode === "unified" ? (
          <div className="bg-muted/40 max-h-[520px] overflow-auto rounded-lg p-3 font-mono text-xs whitespace-pre-wrap">
            {unifiedLines.length > 0
              ? unifiedLines.map((line, index) => {
                  const kind = getDiffLineKind(line);
                  return (
                    <div
                      key={`unified-${index}`}
                      data-diff-kind={kind}
                      className={cn("rounded-sm px-2 py-0.5", diffLineClass(kind))}
                    >
                      {line || " "}
                    </div>
                  );
                })
              : "(no textual diff output)"}
          </div>
        ) : (
          <div className="grid gap-2 md:grid-cols-2">
            <div className="bg-muted/40 max-h-[520px] overflow-auto rounded-lg p-3 font-mono text-xs whitespace-pre-wrap">
              {splitDiff.map((line, index) => (
                <div
                  key={`left-${index}`}
                  data-diff-kind={line.leftKind}
                  data-diff-side="left"
                  className={cn("rounded-sm px-2 py-0.5", diffLineClass(line.leftKind))}
                >
                  {line.left || " "}
                </div>
              ))}
            </div>
            <div className="bg-muted/40 max-h-[520px] overflow-auto rounded-lg p-3 font-mono text-xs whitespace-pre-wrap">
              {splitDiff.map((line, index) => (
                <div
                  key={`right-${index}`}
                  data-diff-kind={line.rightKind}
                  data-diff-side="right"
                  className={cn("rounded-sm px-2 py-0.5", diffLineClass(line.rightKind))}
                >
                  {line.right || " "}
                </div>
              ))}
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
