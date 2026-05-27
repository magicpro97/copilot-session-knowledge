"use client";

import { useState } from "react";
import { Activity, AlertTriangle, CheckCircle2, Copy, XCircle } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { cn } from "@/lib/utils";
import type { TentacleStatusResponse, TentacleEntry } from "@/lib/api/types";

type StatusLevel = "healthy" | "degraded" | "error" | "idle";

function deriveStatusLevel(data: TentacleStatusResponse | undefined): StatusLevel {
  if (!data) return "idle";
  if (data.status === "error") return "error";
  if (data.active_count === 0) return "idle";
  const hasFailed = data.tentacles.some(
    (t) => t.status === "error" || t.terminal_status === "REGRESSED"
  );
  if (hasFailed) return "degraded";
  return "healthy";
}

const STATUS_CONFIG: Record<
  StatusLevel,
  { icon: typeof Activity; label: string; className: string }
> = {
  healthy: {
    icon: CheckCircle2,
    label: "Tentacles active",
    className: "text-emerald-600 dark:text-emerald-400",
  },
  degraded: {
    icon: AlertTriangle,
    label: "Tentacles degraded",
    className: "text-amber-600 dark:text-amber-400",
  },
  error: {
    icon: XCircle,
    label: "Tentacles error",
    className: "text-destructive",
  },
  idle: {
    icon: Activity,
    label: "No active tentacles",
    className: "text-muted-foreground",
  },
};

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);

  return (
    <Button
      type="button"
      variant="ghost"
      size="icon"
      className="size-5 shrink-0"
      aria-label={`Copy command: ${text}`}
      onClick={() => {
        void navigator.clipboard.writeText(text);
        setCopied(true);
        setTimeout(() => setCopied(false), 1500);
      }}
    >
      {copied ? <CheckCircle2 className="size-3 text-emerald-500" /> : <Copy className="size-3" />}
    </Button>
  );
}

// Security: entry.worktree.path and entry.scope are intentionally not rendered to avoid leaking filesystem layout.
function TentacleRow({ entry }: { entry: TentacleEntry }) {
  const isFailed = entry.status === "error" || entry.terminal_status === "REGRESSED";
  const isDone = entry.terminal_status === "DONE";

  return (
    <li className="rounded-md border px-2 py-1.5 text-xs" data-testid="tentacle-row">
      <div className="flex items-center justify-between gap-2">
        <span className="truncate font-medium" title={entry.name}>
          {entry.name}
        </span>
        <span
          className={cn(
            "shrink-0 rounded px-1 py-0.5 font-mono text-[10px]",
            isFailed && "bg-destructive/10 text-destructive",
            isDone &&
              "bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-400",
            !isFailed && !isDone && "bg-muted text-muted-foreground"
          )}
        >
          {entry.terminal_status ?? entry.status}
        </span>
      </div>
      {entry.description ? (
        <p className="text-muted-foreground mt-0.5 line-clamp-2">{entry.description}</p>
      ) : null}
      {entry.verification.total > 0 ? (
        <p className="text-muted-foreground mt-0.5">
          Tests: {entry.verification.passed}/{entry.verification.total} passed
          {entry.verification.failed > 0 ? (
            <span className="text-destructive ml-1">({entry.verification.failed} failed)</span>
          ) : null}
        </p>
      ) : null}
    </li>
  );
}

interface TentacleStatusChipProps {
  data: TentacleStatusResponse | undefined;
  isLoading: boolean;
  isError: boolean;
}

export function TentacleStatusChip({ data, isLoading, isError }: TentacleStatusChipProps) {
  const level = isError ? "error" : deriveStatusLevel(data);
  const config = STATUS_CONFIG[level];
  const Icon = config.icon;

  // Don't render if idle and no data
  if (level === "idle" && !data?.configured) return null;

  const activeEntries =
    data?.tentacles.filter((t) => t.status !== "idle" || t.terminal_status === "REGRESSED") ?? [];
  const degradedEntries = activeEntries.filter(
    (t) => t.status === "error" || t.terminal_status === "REGRESSED"
  );

  const chipLabel = isError
    ? "Tentacle status unavailable"
    : isLoading
      ? "Loading tentacle status…"
      : `${data?.active_count ?? 0} tentacle${(data?.active_count ?? 0) !== 1 ? "s" : ""} active`;

  return (
    <Popover>
      <PopoverTrigger
        className={cn(
          "hover:bg-muted inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-xs font-medium transition-colors",
          config.className
        )}
        aria-label={chipLabel}
        data-testid="tentacle-status-chip"
      >
        <Icon className="size-3 shrink-0" aria-hidden="true" />
        <span className="hidden sm:inline">{isLoading ? "…" : (data?.active_count ?? 0)}</span>
      </PopoverTrigger>

      <PopoverContent align="end" className="w-80" aria-label="Tentacle orchestration status">
        <div className="space-y-2">
          <div className="flex items-center justify-between">
            <h3 className="text-sm font-semibold">Orchestration Status</h3>
            {data ? (
              <span className="text-muted-foreground font-mono text-[10px]">
                {data.active_count}/{data.total_count}
              </span>
            ) : null}
          </div>

          {isError ? (
            <p className="text-destructive text-xs" role="alert">
              Failed to fetch tentacle status. Ensure the browse server is reachable.
            </p>
          ) : isLoading ? (
            <p className="text-muted-foreground animate-pulse text-xs">Loading…</p>
          ) : !data || activeEntries.length === 0 ? (
            <p className="text-muted-foreground text-xs">No active tentacles.</p>
          ) : (
            <>
              {degradedEntries.length > 0 ? (
                <div role="alert" className="text-xs">
                  <p className="font-medium text-amber-600 dark:text-amber-400">
                    {degradedEntries.length} tentacle{degradedEntries.length !== 1 ? "s" : ""} need
                    attention
                  </p>
                </div>
              ) : null}

              <ul
                className="max-h-48 space-y-1.5 overflow-y-auto"
                aria-label="Active tentacles"
                data-testid="tentacle-list"
              >
                {activeEntries.map((entry) => (
                  <TentacleRow key={entry.tentacle_id} entry={entry} />
                ))}
              </ul>
            </>
          )}

          {data?.operator_actions && data.operator_actions.length > 0 ? (
            <div className="border-t pt-2">
              <p className="text-muted-foreground mb-1 text-[10px] font-semibold tracking-wide uppercase">
                Quick Actions
              </p>
              <ul className="space-y-1" aria-label="Operator actions">
                {data.operator_actions.map((action) => (
                  <li key={action.id} className="flex items-center gap-1.5 text-xs">
                    <code className="bg-muted flex-1 truncate rounded px-1 py-0.5 font-mono text-[10px]">
                      {action.command}
                    </code>
                    <CopyButton text={action.command} />
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
        </div>
      </PopoverContent>
    </Popover>
  );
}
