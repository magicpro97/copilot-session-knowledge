"use client";

import { formatDistanceToNow } from "date-fns";
import { MessageSquare, Trash2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";
import type { OperatorSession } from "@/lib/api/types";

type SessionListProps = {
  sessions: OperatorSession[];
  activeId: string | null;
  onSelect: (id: string) => void;
  onDelete?: (id: string) => void;
  loading?: boolean;
  isDeleting?: boolean;
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
                </div>
                <p className="text-muted-foreground truncate font-mono text-xs">
                  {session.workspace}
                </p>
                <div className="text-muted-foreground flex items-center gap-1.5 text-xs">
                  <span className="bg-muted rounded px-1">{session.model}</span>
                  <span className="bg-muted rounded px-1">{session.mode}</span>
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
