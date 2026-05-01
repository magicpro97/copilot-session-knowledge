import type { InsightAction } from "@/lib/insight-models";
import { isActionable } from "@/lib/insight-derive";
import { cn } from "@/lib/utils";

type InsightActionListProps = {
  actions: InsightAction[];
  title?: string;
  className?: string;
};

/** Presentational list of recommended insight actions. Fetch-free; driven by props only. */
export function InsightActionList({
  actions,
  title = "Recommended actions",
  className,
}: InsightActionListProps) {
  if (actions.length === 0) return null;

  return (
    <div className={cn("space-y-1", className)}>
      <p className="text-xs font-medium">{title}</p>
      <ul className="list-disc space-y-1 pl-4">
        {actions.map((action) => (
          <li key={action.id} className="text-xs">
            <span className="font-medium">{action.title}</span>
            {action.detail ? (
              <span className="text-muted-foreground ml-1">— {action.detail}</span>
            ) : null}
            {isActionable(action) ? (
              <code className="text-muted-foreground ml-1 text-[10px]">{action.command}</code>
            ) : null}
          </li>
        ))}
      </ul>
    </div>
  );
}
