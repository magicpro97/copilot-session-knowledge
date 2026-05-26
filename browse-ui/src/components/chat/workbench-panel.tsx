"use client";

import { Activity } from "lucide-react";

import { Skeleton } from "@/components/ui/skeleton";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { useOperatorActiveRuns } from "@/lib/api/hooks";
import { useHostFeature } from "@/lib/hosts";
import type { HostProfile, OperatorActiveRunSummary } from "@/lib/api/types";

/**
 * Issue #564 — Chat Workbench panel.
 *
 * Renders the read-only list of non-terminal active runs returned by
 * `GET /api/operator/runs`. Gated by the `runs_workbench` capability:
 * when the active host does not advertise the feature the panel renders
 * nothing so legacy backends remain visually identical to before.
 *
 * The panel never displays prompt text, events, files, or any sensitive
 * run internals — only the public-summary allowlist returned by the
 * backend.
 */
export type WorkbenchPanelProps = {
  host: HostProfile;
  /**
   * Owning chat session id (if any). When provided, the panel highlights
   * runs that belong to the active session — useful for surfacing
   * background runs while the user is focused on a different chat.
   */
  activeSessionId?: string | null;
  /** Click handler invoked when an active run row is selected. */
  onSelectRun?: (run: OperatorActiveRunSummary) => void;
  className?: string;
};

export function WorkbenchPanel({
  host,
  activeSessionId,
  onSelectRun,
  className,
}: WorkbenchPanelProps) {
  const feature = useHostFeature(host, "runs_workbench", true);
  const query = useOperatorActiveRuns(feature.supported, host);

  // Capability-gated: legacy hosts (and local hosts during capabilities
  // resolution) keep their previous behavior — render nothing.
  if (!feature.supported && !feature.loading) {
    return null;
  }

  return (
    <section
      data-testid="workbench-panel"
      aria-label="Active runs workbench"
      className={cn("bg-card/40 flex flex-col gap-1 border-t p-3", className)}
    >
      <header className="text-muted-foreground flex items-center gap-1.5 text-xs font-semibold tracking-wide uppercase">
        <Activity className="size-3" />
        <span>Workbench</span>
        {query.isLoading || feature.loading ? (
          <span className="ml-auto text-[10px] font-normal normal-case opacity-70">Loading…</span>
        ) : query.data ? (
          <span className="ml-auto text-[10px] font-normal normal-case opacity-70">
            {query.data.count} active
          </span>
        ) : null}
      </header>

      {feature.loading || query.isLoading ? (
        <div className="space-y-1.5">
          <Skeleton className="h-9 w-full rounded-md" />
          <Skeleton className="h-9 w-full rounded-md" />
        </div>
      ) : query.isError ? (
        <p className="text-muted-foreground text-xs">Workbench unavailable on this host.</p>
      ) : !query.data || query.data.runs.length === 0 ? (
        <p className="text-muted-foreground text-xs">No active runs.</p>
      ) : (
        <ul className="space-y-1">
          {query.data.runs.map((run) => {
            const isActiveSession = activeSessionId && run.session_id === activeSessionId;
            const label = run.session_label?.trim() || run.session_id.slice(0, 8);
            const status = run.status || "running";
            return (
              <li key={run.id}>
                <Button
                  type="button"
                  variant="ghost"
                  className={cn(
                    "h-auto w-full justify-between gap-2 px-2 py-1.5 text-left",
                    isActiveSession ? "bg-primary/10 text-primary" : ""
                  )}
                  onClick={() => onSelectRun?.(run)}
                  aria-label={`Active run for session ${label}`}
                  data-run-id={run.id}
                  data-session-id={run.session_id}
                >
                  <span className="min-w-0 flex-1 truncate text-xs font-medium">{label}</span>
                  <span
                    data-testid="workbench-run-status"
                    className="bg-muted text-muted-foreground rounded px-1 py-0.5 font-mono text-[10px] uppercase"
                  >
                    {status}
                  </span>
                </Button>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
