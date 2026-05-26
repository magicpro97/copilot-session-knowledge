"use client";

import { formatDistanceToNow } from "date-fns";
import { Loader2, MessageSquare, Trash2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";
import type { OperatorSession } from "@/lib/api/types";
import { CliAdoptedBadge } from "./cli-session-picker";

type SessionListProps = {
  sessions: OperatorSession[];
  activeId: string | null;
  onSelect: (id: string) => void;
  onDelete?: (id: string) => void;
  loading?: boolean;
  isDeleting?: boolean;
  /**
   * Issue #564: set of session IDs that currently have a non-terminal
   * active run. When provided, each matching session row renders a small
   * indicator. When omitted (or empty) the list behaves exactly as before
   * for backward compatibility with hosts that lack `runs_workbench`.
   */
  activeRunSessionIds?: ReadonlySet<string>;
};

/**
 * Left-panel session list for the /chat route. Shows session name, workspace,
 * model, mode and last activity. Highlights the active session.
 */
export function SessionList({
  sessions,
  activeId,
  onSelect,
  onDelete,
  loading,
  isDeleting,
  activeRunSessionIds,
}: SessionListProps) {
  if (loading) {
    return (
      <div className="space-y-2 p-3">
        {Array.from({ length: 4 }).map((_, i) => (
          <Skeleton key={i} className="h-14 w-full rounded-md" />
        ))}
      </div>
    );
  }

  if (sessions.length === 0) {
    return (
      <div className="flex h-32 items-center justify-center p-4 text-center">
        <p className="text-muted-foreground text-sm">No sessions yet.</p>
      </div>
    );
  }

  return (
    <ul className="space-y-1 p-2">
      {sessions.map((session) => {
        const isActive = session.id === activeId;
        const updatedAt = formatDistanceToNow(new Date(session.updated_at), {
          addSuffix: true,
        });
        const isCliAdopted = session.source === "cli_adopt";
        const isConfirmed = Boolean(session.confirmed_at);
        const hasActiveRun = Boolean(activeRunSessionIds?.has(session.id));

        return (
          <li key={session.id}>
            <div
              className={cn(
                "group flex cursor-pointer items-start justify-between gap-2 rounded-lg px-3 py-2 transition-colors",
                isActive ? "bg-primary/10 text-primary" : "hover:bg-accent/50 text-foreground"
              )}
              onClick={() => onSelect(session.id)}
              role="button"
              tabIndex={0}
              aria-current={isActive ? "true" : undefined}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault();
                  onSelect(session.id);
                }
              }}
            >
              <div className="min-w-0 flex-1 space-y-0.5">
                <div className="flex items-center gap-1.5">
                  <MessageSquare className="size-3 shrink-0 opacity-60" />
                  <span className="truncate text-sm font-medium">{session.name}</span>
                  {hasActiveRun ? (
                    <span
                      data-testid="session-active-run-indicator"
                      data-session-id={session.id}
                      aria-label="Active run in progress"
                      title="Active run in progress"
                      className="bg-primary/15 text-primary ml-1 inline-flex shrink-0 items-center gap-0.5 rounded-full px-1.5 py-0.5 text-[10px] font-semibold uppercase"
                    >
                      <Loader2 className="size-2.5 animate-spin" />
                      <span>live</span>
                    </span>
                  ) : null}
                </div>
                <p className="text-muted-foreground truncate font-mono text-xs">
                  {session.workspace}
                </p>
                <div className="text-muted-foreground flex items-center gap-1.5 text-xs">
                  <span className="bg-muted rounded px-1">{session.model}</span>
                  <span className="bg-muted rounded px-1">{session.mode}</span>
                  {isCliAdopted ? <CliAdoptedBadge confirmed={isConfirmed} /> : null}
                  <span className="ml-auto opacity-60">{updatedAt}</span>
                </div>
              </div>
              {onDelete ? (
                <Button
                  type="button"
                  variant="ghost"
                  size="icon"
                  className="size-6 shrink-0 opacity-0 transition-opacity group-hover:opacity-100"
                  disabled={isDeleting}
                  aria-label={`Delete session ${session.name}`}
                  onClick={(e) => {
                    e.stopPropagation();
                    onDelete(session.id);
                  }}
                >
                  <Trash2 className="size-3" />
                </Button>
              ) : null}
            </div>
          </li>
        );
      })}
    </ul>
  );
}
