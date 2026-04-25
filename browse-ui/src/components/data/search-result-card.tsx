import { Brain, ScrollText } from "lucide-react";

import { Highlight } from "@/components/data/highlight";
import { IDBadge, SourceBadge } from "@/components/data/session-badges";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type { SearchResult } from "@/lib/api/types";
import { formatNumber } from "@/lib/formatters";
import { cn } from "@/lib/utils";

type SearchResultCardProps = {
  result: SearchResult;
  query?: string;
  className?: string;
  onSelect?: (result: SearchResult) => void;
};

export function SearchResultCard({
  result,
  query = "",
  className,
  onSelect,
}: SearchResultCardProps) {
  const Icon = result.type === "knowledge" ? Brain : ScrollText;
  const titleText = result.title || String(result.id);
  const isSessionId = result.type === "session" && typeof result.id === "string";
  const snippet = result.snippet?.replace(/<\/?mark>/gi, "") ?? "";

  return (
    <Card
      className={cn(
        "cursor-pointer border border-border/80 transition-colors hover:bg-muted/30",
        className
      )}
      onClick={() => onSelect?.(result)}
      role="button"
      tabIndex={0}
      onKeyDown={(event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          onSelect?.(result);
        }
      }}
    >
      <CardHeader className="gap-2">
        <div className="flex items-center justify-between gap-3">
          <CardTitle className="flex min-w-0 items-center gap-2 text-sm">
            <Icon className="size-4 shrink-0 text-muted-foreground" />
            <span className="truncate">{titleText}</span>
          </CardTitle>
          <span className="shrink-0 text-xs text-muted-foreground">
            Score {formatNumber(result.score)}
          </span>
        </div>
        <div className="flex items-center gap-2">
          {isSessionId && typeof result.id === "string" ? (
            <IDBadge id={result.id} />
          ) : null}
          {result.wing ? <SourceBadge source={result.wing} /> : null}
          {result.kind ? (
            <span className="rounded-md border border-border bg-muted px-1.5 py-0.5 text-xs text-muted-foreground">
              {result.kind}
            </span>
          ) : null}
        </div>
      </CardHeader>
      {snippet ? (
        <CardContent>
          <p className="line-clamp-4 text-sm text-muted-foreground">
            <Highlight text={snippet} query={query} />
          </p>
        </CardContent>
      ) : null}
    </Card>
  );
}
