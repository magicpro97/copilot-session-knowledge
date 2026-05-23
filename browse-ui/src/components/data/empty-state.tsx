import { Inbox } from "lucide-react";
import type { ReactNode } from "react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

type EmptyStateProps = {
  title: string;
  description?: string;
  icon?: ReactNode;
  actionLabel?: string;
  onAction?: () => void;
  /**
   * Custom action node rendered in place of the built-in `actionLabel`/`onAction` button.
   * Use when you need full button control (e.g. disabled state, spinner, data-testid).
   * Takes precedence over `actionLabel`/`onAction` when provided.
   */
  actionNode?: ReactNode;
  className?: string;
};

export function EmptyState({
  title,
  description,
  icon,
  actionLabel,
  onAction,
  actionNode,
  className,
}: EmptyStateProps) {
  return (
    <div
      className={cn(
        "border-border bg-muted/20 flex min-h-48 w-full flex-col items-center justify-center gap-3 rounded-xl border border-dashed px-6 py-10 text-center",
        className
      )}
    >
      <div className="bg-muted text-muted-foreground rounded-full p-2">
        {icon ?? <Inbox className="size-5" />}
      </div>
      <div className="space-y-1">
        <h3 className="text-foreground text-sm font-medium">{title}</h3>
        {description ? <p className="text-muted-foreground text-sm">{description}</p> : null}
      </div>
      {actionNode ??
        (actionLabel && onAction ? (
          <Button type="button" variant="outline" size="sm" onClick={onAction}>
            {actionLabel}
          </Button>
        ) : null)}
    </div>
  );
}
