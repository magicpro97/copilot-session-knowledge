import type { OperatorRunInfo, RunFileMetadata } from "@/lib/api/types";

export type ActiveRun = { id: string; prompt: string; files?: RunFileMetadata[] };

const TERMINAL_RUN_STATUSES = new Set(["done", "failed", "timeout", "cancelled"]);

export function isTerminalRunStatus(status: string): boolean {
  return TERMINAL_RUN_STATUSES.has(status);
}

export function findRecoverableActiveRun(
  runs: OperatorRunInfo[],
  suppressedRunId?: string | null
): ActiveRun | null {
  const activeRuns = runs.filter(
    (run) => !isTerminalRunStatus(run.status) && run.id !== suppressedRunId
  );
  if (activeRuns.length === 0) return null;
  const latest = activeRuns.reduce((best, run) =>
    (run.started_at || "") >= (best.started_at || "") ? run : best
  );
  return {
    id: latest.id,
    prompt: latest.prompt,
    files: latest.files,
  };
}

export function visibleHistoricalRuns(
  runs: OperatorRunInfo[],
  activeRun: ActiveRun | null
): OperatorRunInfo[] {
  if (!activeRun) return runs;
  return runs.filter((run) => run.id !== activeRun.id);
}
