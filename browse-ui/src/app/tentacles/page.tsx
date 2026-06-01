"use client";

import { useEffect, useState } from "react";
import { ChevronDown, ChevronRight, GitBranch, RefreshCw } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { useTentacleStatus } from "@/lib/api/hooks";
import { useHostState } from "@/providers/host-provider";
import { cn } from "@/lib/utils";
import type { TentacleEntry } from "@/lib/api/types";

// ── Status helpers ────────────────────────────────────────────────────────────

type FilterValue = "all" | "active" | "completed" | "failed";

function getDisplayStatus(entry: TentacleEntry): string {
  if (entry.terminal_status) return entry.terminal_status;
  return entry.status;
}

function isActive(entry: TentacleEntry): boolean {
  return ["active", "dispatched", "running"].includes(entry.status) && !entry.terminal_status;
}

function isCompleted(entry: TentacleEntry): boolean {
  return entry.terminal_status === "DONE";
}

function isFailed(entry: TentacleEntry): boolean {
  return (
    entry.status === "error" ||
    ["BLOCKED", "REGRESSED", "TOO_BIG", "AMBIGUOUS"].includes(entry.terminal_status ?? "")
  );
}

function matchesFilter(entry: TentacleEntry, filter: FilterValue): boolean {
  if (filter === "all") return true;
  if (filter === "active") return isActive(entry);
  if (filter === "completed") return isCompleted(entry);
  if (filter === "failed") return isFailed(entry);
  return true;
}

// ── Progress ──────────────────────────────────────────────────────────────────

function progressPercent(entry: TentacleEntry): number | null {
  const { total, passed } = entry.verification;
  if (total === 0) return null;
  return Math.round((passed / total) * 100);
}

// ── Status badge ─────────────────────────────────────────────────────────────

function StatusBadge({ entry }: { entry: TentacleEntry }) {
  const display = getDisplayStatus(entry);
  const active = isActive(entry);
  const done = isCompleted(entry);
  const failed = isFailed(entry);

  return (
    <Badge
      variant="outline"
      className={cn(
        "font-mono text-[10px]",
        active && "border-blue-500/50 bg-blue-500/10 text-blue-700 dark:text-blue-400",
        done && "border-emerald-500/50 bg-emerald-500/10 text-emerald-700 dark:text-emerald-400",
        failed && "border-destructive/50 bg-destructive/10 text-destructive",
        !active && !done && !failed && "text-muted-foreground"
      )}
    >
      {display || "idle"}
    </Badge>
  );
}

// ── Detail panel ─────────────────────────────────────────────────────────────

function TentacleDetail({ entry }: { entry: TentacleEntry }) {
  return (
    <div
      className="bg-muted/30 border-border space-y-3 rounded-b-lg border-t px-4 py-3 text-sm"
      data-testid="tentacle-detail"
    >
      {/* Assigned files (scope) */}
      <div>
        <p className="text-muted-foreground mb-1 text-[11px] font-semibold tracking-wide uppercase">
          Assigned Files
        </p>
        {entry.scope.length > 0 ? (
          <ul className="space-y-0.5">
            {entry.scope.map((f) => (
              <li key={f}>
                <code className="bg-muted rounded px-1 py-0.5 font-mono text-xs">{f}</code>
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-muted-foreground text-xs">No files in scope.</p>
        )}
      </div>

      {/* Handoff summary */}
      <div>
        <p className="text-muted-foreground mb-1 text-[11px] font-semibold tracking-wide uppercase">
          Handoff Summary
        </p>
        {entry.description ? (
          <p className="text-xs">{entry.description}</p>
        ) : (
          <p className="text-muted-foreground text-xs">No description.</p>
        )}
        {entry.has_handoff && (
          <p className="text-muted-foreground mt-1 text-xs">
            Terminal status: <span className="font-medium">{entry.terminal_status ?? "—"}</span>
          </p>
        )}
      </div>

      {/* Changed files — reported via scope (handoff detail not surfaced by API) */}
      <div>
        <p className="text-muted-foreground mb-1 text-[11px] font-semibold tracking-wide uppercase">
          Changed Files
        </p>
        {entry.scope.length > 0 ? (
          <ul className="space-y-0.5">
            {entry.scope.map((f) => (
              <li key={f}>
                <code className="bg-muted rounded px-1 py-0.5 font-mono text-xs">{f}</code>
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-muted-foreground text-xs">No scope data available.</p>
        )}
      </div>

      {/* Learnings (skills) */}
      <div>
        <p className="text-muted-foreground mb-1 text-[11px] font-semibold tracking-wide uppercase">
          Skills / Learnings
        </p>
        {entry.skills.length > 0 ? (
          <div className="flex flex-wrap gap-1">
            {entry.skills.map((s) => (
              <Badge key={s} variant="secondary" className="text-[10px]">
                {s}
              </Badge>
            ))}
          </div>
        ) : (
          <p className="text-muted-foreground text-xs">No skills recorded.</p>
        )}
      </div>

      {/* Verification */}
      {entry.verification.total > 0 && (
        <div>
          <p className="text-muted-foreground mb-1 text-[11px] font-semibold tracking-wide uppercase">
            Verification
          </p>
          <p className="text-xs">
            {entry.verification.passed}/{entry.verification.total} checks passed
            {entry.verification.failed > 0 && (
              <span className="text-destructive ml-1">({entry.verification.failed} failed)</span>
            )}
          </p>
        </div>
      )}

      {/* Goal info */}
      {entry.goal_name && (
        <div>
          <p className="text-muted-foreground mb-1 text-[11px] font-semibold tracking-wide uppercase">
            Goal
          </p>
          <p className="text-xs">
            {entry.goal_name}
            {entry.goal_iteration !== undefined && (
              <span className="text-muted-foreground ml-1">(iteration {entry.goal_iteration})</span>
            )}
          </p>
        </div>
      )}
    </div>
  );
}

// ── Row ───────────────────────────────────────────────────────────────────────

function TentacleRow({ entry }: { entry: TentacleEntry }) {
  const [expanded, setExpanded] = useState(false);
  const progress = progressPercent(entry);

  return (
    <div
      className="border-border rounded-lg border"
      data-testid="tentacle-row"
      aria-label={`Tentacle: ${entry.name}`}
    >
      <button
        type="button"
        className="hover:bg-muted/40 flex w-full items-center gap-3 rounded-t-lg px-4 py-3 text-left transition-colors"
        onClick={() => setExpanded((v) => !v)}
        aria-expanded={expanded}
        data-testid="tentacle-row-toggle"
      >
        {expanded ? (
          <ChevronDown className="text-muted-foreground size-4 shrink-0" aria-hidden />
        ) : (
          <ChevronRight className="text-muted-foreground size-4 shrink-0" aria-hidden />
        )}

        {/* Name */}
        <span className="min-w-0 flex-1 truncate text-sm font-medium" title={entry.name}>
          {entry.name}
        </span>

        {/* Status */}
        <StatusBadge entry={entry} />

        {/* Progress */}
        {progress !== null && (
          <span
            className="text-muted-foreground shrink-0 font-mono text-xs"
            aria-label={`${progress}% verification passing`}
          >
            {progress}%
          </span>
        )}
      </button>

      {expanded && <TentacleDetail entry={entry} />}
    </div>
  );
}

// ── Filter bar ────────────────────────────────────────────────────────────────

const FILTER_OPTIONS: { value: FilterValue; label: string }[] = [
  { value: "all", label: "All" },
  { value: "active", label: "Active" },
  { value: "completed", label: "Completed" },
  { value: "failed", label: "Failed" },
];

// ── Page ──────────────────────────────────────────────────────────────────────

export default function TentaclesPage() {
  const { host, diagnosticsEnabled } = useHostState();
  const { data, isLoading, isError, refetch, isFetching } = useTentacleStatus(
    host,
    diagnosticsEnabled
  );

  const [filter, setFilter] = useState<FilterValue>("all");

  // Real-time polling every 5 s while diagnostics are enabled
  useEffect(() => {
    if (!diagnosticsEnabled) return;
    const id = setInterval(() => void refetch(), 5_000);
    return () => clearInterval(id);
  }, [diagnosticsEnabled, refetch]);

  const tentacles = data?.tentacles ?? [];
  const filtered = tentacles.filter((t) => matchesFilter(t, filter));

  return (
    <div className="space-y-6" data-testid="tentacles-page">
      {/* Header */}
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="space-y-1">
          <h1 className="text-2xl font-semibold tracking-tight">Tentacle Management</h1>
          <p className="text-muted-foreground text-sm">
            Real-time orchestration status for all active and completed tentacles.
          </p>
        </div>
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={() => void refetch()}
          disabled={isFetching || !diagnosticsEnabled}
          aria-label="Refresh tentacle status"
          data-testid="refresh-btn"
        >
          <RefreshCw className={cn("size-3.5", isFetching && "animate-spin")} aria-hidden />
          Refresh
        </Button>
      </div>

      {/* No host state */}
      {!diagnosticsEnabled && (
        <Card>
          <CardContent className="py-8 text-center">
            <GitBranch className="text-muted-foreground mx-auto mb-3 size-8" aria-hidden />
            <p className="font-medium">No agent host selected</p>
            <p className="text-muted-foreground mt-1 text-sm">
              Run the browse server locally or select a remote agent host in the header to load
              tentacle data.
            </p>
          </CardContent>
        </Card>
      )}

      {/* Stats row */}
      {diagnosticsEnabled && data && (
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          {[
            { label: "Total", value: data.total_count },
            { label: "Active", value: data.active_count },
            { label: "Worktrees", value: data.worktrees_prepared },
            { label: "Verified", value: data.verification_covered },
          ].map(({ label, value }) => (
            <Card key={label} size="sm">
              <CardHeader>
                <CardTitle className="text-muted-foreground text-xs font-medium">{label}</CardTitle>
              </CardHeader>
              <CardContent>
                <p className="text-2xl font-semibold tabular-nums">{value}</p>
              </CardContent>
            </Card>
          ))}
        </div>
      )}

      {/* Filter bar */}
      {diagnosticsEnabled && (
        <div
          className="flex flex-wrap gap-2"
          role="group"
          aria-label="Filter tentacles by status"
          data-testid="filter-bar"
        >
          {FILTER_OPTIONS.map(({ value, label }) => {
            const count =
              value === "all"
                ? tentacles.length
                : tentacles.filter((t) => matchesFilter(t, value)).length;
            return (
              <Button
                key={value}
                type="button"
                variant={filter === value ? "secondary" : "outline"}
                size="sm"
                onClick={() => setFilter(value)}
                aria-pressed={filter === value}
                data-testid={`filter-${value}`}
              >
                {label}
                <span className="text-muted-foreground ml-1.5 font-mono text-[10px]">{count}</span>
              </Button>
            );
          })}
        </div>
      )}

      {/* Content */}
      {diagnosticsEnabled && (
        <>
          {isLoading && (
            <div className="space-y-2" data-testid="loading-state">
              {[...Array(3)].map((_, i) => (
                <div key={i} className="bg-muted animate-pulse rounded-lg p-4" aria-hidden />
              ))}
            </div>
          )}

          {isError && !isLoading && (
            <Card>
              <CardContent className="py-6">
                <p className="text-destructive text-sm" role="alert">
                  Failed to load tentacle status. Ensure the browse server is reachable.
                </p>
              </CardContent>
            </Card>
          )}

          {!isLoading && !isError && filtered.length === 0 && (
            <Card>
              <CardContent className="py-8 text-center">
                <p className="text-muted-foreground text-sm" data-testid="empty-state">
                  {tentacles.length === 0
                    ? "No tentacles found. Start an orchestration to see tentacles here."
                    : `No tentacles match the "${filter}" filter.`}
                </p>
              </CardContent>
            </Card>
          )}

          {!isLoading && !isError && filtered.length > 0 && (
            <ul className="space-y-2" aria-label="Tentacle list" data-testid="tentacle-list">
              {filtered.map((entry) => (
                <li key={entry.tentacle_id || entry.name}>
                  <TentacleRow entry={entry} />
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </div>
  );
}
