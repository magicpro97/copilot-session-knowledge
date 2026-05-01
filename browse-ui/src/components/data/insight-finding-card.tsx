import type { InsightFinding } from "@/lib/insight-models";
import { cn } from "@/lib/utils";

const SEVERITY_STYLES: Record<InsightFinding["severity"], string> = {
  info: "border-blue-500/30 bg-blue-500/5 text-blue-700 dark:text-blue-400",
  warning: "border-yellow-500/30 bg-yellow-500/5 text-yellow-700 dark:text-yellow-400",
  critical: "border-red-500/30 bg-red-500/5 text-red-700 dark:text-red-400",
};

const SEVERITY_EMOJI: Record<InsightFinding["severity"], string> = {
  info: "ℹ️",
  warning: "⚠️",
  critical: "🚨",
};

type InsightFindingCardProps = {
  finding: InsightFinding;
  className?: string;
};

/** Presentational card for a single insight finding. Fetch-free; driven by props only. */
export function InsightFindingCard({ finding, className }: InsightFindingCardProps) {
  return (
    <div
      role="listitem"
      className={cn("rounded-lg border px-3 py-2", SEVERITY_STYLES[finding.severity], className)}
    >
      <p className="text-xs font-medium">
        {SEVERITY_EMOJI[finding.severity]} {finding.title}
      </p>
      {finding.detail ? <p className="mt-0.5 text-xs opacity-80">{finding.detail}</p> : null}
      {finding.why ? (
        <p className="text-muted-foreground mt-0.5 text-xs italic">{finding.why}</p>
      ) : null}
    </div>
  );
}
