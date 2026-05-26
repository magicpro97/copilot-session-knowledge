"use client";

import { formatDistanceToNow } from "date-fns";
import { Terminal, GitBranch, FolderOpen, AlertCircle, Loader2, Activity } from "lucide-react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import type { CliSession, CliSessionPriorContext } from "@/lib/api/types";

type CliSessionPickerProps = {
  sessions: CliSession[];
  isLoading?: boolean;
  isError?: boolean;
  unavailable?: boolean;
  onSelect: (session: CliSession) => void;
  isAdopting?: boolean;
};

/**
 * Picker for CLI history sessions during the adopt flow.
 *
 * SECURITY: `session.cli_session_id` is opaque state that must never
 * flow into router URLs or storage. The parent is responsible for
 * calling `useAdoptCliSession` with it in a POST JSON body only.
 */
export function CliSessionPicker({
  sessions,
  isLoading,
  isError,
  unavailable,
  onSelect,
  isAdopting,
}: CliSessionPickerProps) {
  if (isLoading) {
    return (
      <div className="text-muted-foreground flex items-center justify-center gap-2 py-6 text-sm">
        <Loader2 className="size-4 animate-spin" />
        <span>Loading CLI history…</span>
      </div>
    );
  }

  if (unavailable) {
    return (
      <div className="text-muted-foreground flex items-center gap-2 px-2 py-4 text-sm">
        <AlertCircle className="size-4 shrink-0" />
        <span>
          CLI history is not available on this host. Update the browse backend to enable it.
        </span>
      </div>
    );
  }

  if (isError) {
    return (
      <div className="text-destructive flex items-center gap-2 px-2 py-4 text-sm">
        <AlertCircle className="size-4 shrink-0" />
        <span>Failed to load CLI history. Check that the browse server is running.</span>
      </div>
    );
  }

  if (sessions.length === 0) {
    return (
      <div className="text-muted-foreground flex items-center justify-center py-6 text-sm">
        No CLI sessions found.
      </div>
    );
  }

  return (
    <ul className="max-h-64 space-y-1 overflow-y-auto" data-testid="cli-session-list">
      {sessions.map((session) => {
        const updatedAt = (() => {
          try {
            return formatDistanceToNow(new Date(session.mtime), { addSuffix: true });
          } catch {
            return session.mtime;
          }
        })();

        return (
          <li key={session.cli_session_id}>
            <button
              type="button"
              className={cn(
                "hover:bg-accent/50 w-full rounded-lg px-3 py-2 text-left transition-colors",
                "focus-visible:ring-ring focus-visible:ring-2 focus-visible:outline-none",
                isAdopting && "pointer-events-none opacity-60"
              )}
              onClick={() => onSelect(session)}
              disabled={isAdopting}
              data-testid="cli-session-item"
            >
              <div className="flex min-w-0 items-center gap-1.5">
                <Terminal className="size-3 shrink-0 opacity-60" />
                <span className="truncate text-sm font-medium">{session.title}</span>
              </div>
              <div className="text-muted-foreground mt-0.5 flex items-center gap-2 text-xs">
                {session.workspace_hint ? (
                  <span className="flex items-center gap-1 truncate">
                    <FolderOpen className="size-3 shrink-0" />
                    {/* Render server-provided value as-is; do not expand or resolve paths */}
                    <span className="truncate font-mono">{session.workspace_hint}</span>
                  </span>
                ) : null}
                {session.branch ? (
                  <span className="flex shrink-0 items-center gap-1">
                    <GitBranch className="size-3 shrink-0" />
                    <span className="font-mono">{session.branch}</span>
                  </span>
                ) : null}
                <span className="ml-auto shrink-0 opacity-60">{updatedAt}</span>
              </div>
              {session.prior_context && session.prior_context.event_count > 0 ? (
                <div
                  className="text-muted-foreground mt-0.5 flex items-center gap-2 text-xs"
                  data-testid="cli-prior-context"
                >
                  <Activity className="size-3 shrink-0" />
                  <span>
                    {session.prior_context.event_count} prior event
                    {session.prior_context.event_count !== 1 ? "s" : ""}
                  </span>
                  {session.prior_context.last_status ? (
                    <span
                      className="rounded bg-slate-100 px-1.5 py-0.5 font-mono text-[10px] dark:bg-slate-800"
                      data-testid="cli-prior-status"
                    >
                      {session.prior_context.last_status}
                    </span>
                  ) : null}
                  {session.prior_context.last_event_at ? (
                    <span className="opacity-60" data-testid="cli-prior-last-at">
                      {(() => {
                        try {
                          return formatDistanceToNow(
                            new Date(session.prior_context.last_event_at),
                            { addSuffix: true }
                          );
                        } catch {
                          return session.prior_context.last_event_at;
                        }
                      })()}
                    </span>
                  ) : null}
                  {session.prior_context.truncated ? (
                    <span className="opacity-60" data-testid="cli-prior-truncated">
                      (truncated)
                    </span>
                  ) : null}
                </div>
              ) : null}
            </button>
          </li>
        );
      })}
    </ul>
  );
}

/**
 * Compact badge shown in session list/metadata to indicate a CLI-adopted session.
 */
export function CliAdoptedBadge({ confirmed }: { confirmed: boolean }) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded px-1.5 py-0.5 font-mono text-xs",
        confirmed
          ? "bg-sky-100 text-sky-700 dark:bg-sky-900/30 dark:text-sky-400"
          : "bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400"
      )}
      title={
        confirmed
          ? "Adopted from CLI history (confirmed)"
          : "Adopted from CLI history — pending confirmation"
      }
    >
      <Terminal className="size-3" />
      {confirmed ? "CLI" : "CLI (unconfirmed)"}
    </span>
  );
}

/**
 * Confirmation panel shown when a session is in the unconfirmed adoption state
 * (`source === 'cli_adopt' && !confirmed_at`).
 *
 * The composer is disabled until the user clicks Confirm.
 *
 * NOTE: `resume_target` (the internal CLI UUID) must NOT be passed or rendered here.
 */
type ConfirmAdoptionPanelProps = {
  sessionName: string;
  /** Resolved workspace path from the OperatorSession, shown for user confirmation. */
  workspace?: string;
  /** Additional directories included in the adopted session. */
  addDirs?: string[];
  /** Optional bounded prior-context envelope from the CLI session events.jsonl (#568). */
  priorContext?: CliSessionPriorContext | null;
  onConfirm: () => void;
  isConfirming?: boolean;
};

export function ConfirmAdoptionPanel({
  sessionName,
  workspace,
  addDirs,
  priorContext,
  onConfirm,
  isConfirming,
}: ConfirmAdoptionPanelProps) {
  return (
    <div
      className="flex items-center gap-3 border-b bg-amber-50 px-4 py-2.5 text-sm dark:bg-amber-900/20"
      data-testid="confirm-adoption-panel"
      role="status"
    >
      <Terminal className="size-4 shrink-0 text-amber-700 dark:text-amber-400" />
      <div className="min-w-0 flex-1">
        <span className="font-medium text-amber-900 dark:text-amber-300">Resume CLI session?</span>
        <span className="ml-2 truncate text-amber-700 dark:text-amber-400">
          Confirm to resume &ldquo;{sessionName}&rdquo; and enable the composer.
        </span>
        {workspace ? (
          <span
            className="ml-2 font-mono text-xs text-amber-700 dark:text-amber-400"
            data-testid="confirm-adoption-workspace"
          >
            {workspace}
          </span>
        ) : null}
        {addDirs && addDirs.length > 0 ? (
          <span
            className="ml-2 text-xs text-amber-700 dark:text-amber-400"
            data-testid="confirm-adoption-add-dirs"
          >
            +{addDirs.join(", ")}
          </span>
        ) : null}
        {priorContext && priorContext.event_count > 0 ? (
          <span
            className="ml-2 text-xs text-amber-700 dark:text-amber-400"
            data-testid="confirm-adoption-prior-context"
          >
            prior activity: {priorContext.event_count} event
            {priorContext.event_count !== 1 ? "s" : ""}
            {priorContext.last_status ? `, last ${priorContext.last_status}` : ""}
            {priorContext.truncated ? " (truncated)" : ""}
          </span>
        ) : null}
      </div>
      <Button
        type="button"
        size="sm"
        className="h-7 shrink-0 px-3 text-xs"
        onClick={onConfirm}
        disabled={isConfirming}
        data-testid="confirm-adoption-btn"
      >
        {isConfirming ? (
          <>
            <Loader2 className="mr-1.5 size-3 animate-spin" />
            Confirming…
          </>
        ) : (
          "Confirm & Resume"
        )}
      </Button>
    </div>
  );
}
