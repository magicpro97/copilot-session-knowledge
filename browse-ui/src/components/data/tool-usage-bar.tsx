import type { TimelineEntry } from "@/lib/api/types";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { formatNumber } from "@/lib/formatters";

const TOOL_REGEX = /\b(edit|view|bash|grep|glob|write_bash|task|create)\s*\(/g;

type ToolUsageBarProps = {
  timeline: TimelineEntry[];
};

export function ToolUsageBar({ timeline }: ToolUsageBarProps) {
  const counts = new Map<string, number>();

  for (const row of timeline) {
    const content = row.content || "";
    TOOL_REGEX.lastIndex = 0;
    let match = TOOL_REGEX.exec(content);
    while (match) {
      const tool = match[1];
      counts.set(tool, (counts.get(tool) || 0) + 1);
      match = TOOL_REGEX.exec(content);
    }
  }

  const entries = [...counts.entries()].sort((a, b) => b[1] - a[1]);
  const total = entries.reduce((sum, item) => sum + item[1], 0);
  if (total === 0) return null;

  return (
    <Card>
      <CardHeader>
        <CardTitle>Tool usage ({formatNumber(total)} invocations)</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <div
          className="bg-muted flex h-2 overflow-hidden rounded-full"
          role="img"
          aria-label="Tool usage distribution"
        >
          {entries.map(([tool, count]) => (
            <span
              key={tool}
              className="bg-primary/70 h-full"
              style={{ width: `${(count / total) * 100}%` }}
              title={`${tool}: ${count}`}
            />
          ))}
        </div>

        <div className="flex flex-wrap gap-2">
          {entries.map(([tool, count]) => (
            <span
              key={tool}
              className="border-border bg-muted rounded-full border px-2 py-0.5 text-xs"
            >
              {tool} × {formatNumber(count)}
            </span>
          ))}
        </div>
      </CardContent>
    </Card>
  );
}
