"use client";

import { useState, useEffect, useRef } from "react";
import {
  FolderOpen,
  Cpu,
  Settings2,
  Hash,
  RefreshCw,
  Pencil,
  Terminal,
  GitBranch,
  Clock,
  Wrench,
  Gauge,
  ChevronDown,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import {
  Popover,
  PopoverContent,
  PopoverHeader,
  PopoverTitle,
  PopoverTrigger,
} from "@/components/ui/popover";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { COPILOT_MODES } from "./session-create-dialog";
import type {
  OperatorSession,
  UpdateOperatorSessionRequest,
  MutableOperatorSessionMode,
  UsageResponse,
} from "@/lib/api/types";

/**
 * #556: Minimal compatible shape for the `usage` prop.
 *
 * The legacy callers passed only `{remaining_hour, hourly_limit}`. The full
 * `UsageResponse` is a strict superset, so accept either to remain backward
 * compatible with existing tests and screens.
 */
export type MetadataBarUsage =
  | UsageResponse
  | {
      remaining_hour: number;
      hourly_limit: number;
    };

type MetadataBarProps = {
  session: OperatorSession;
  /** True while a run is active — edits are disabled. */
  isRunning?: boolean;
  /** True while the PATCH mutation is in-flight. */
  isUpdating?: boolean;
  /** Provide this callback to enable the edit affordance. */
  onUpdate?: (payload: UpdateOperatorSessionRequest) => void;
  /**
   * When `true`, programmatically opens the session editor (e.g. from `/session`
   * slash command). Ignored while a run is active. Reset this to `false` when
   * `onEditorClose` is called.
   */
  openEditor?: boolean;
  /** Called when the editor closes after an external open request. */
  onEditorClose?: () => void;
  /**
   * #556: Optional usage data to display quota info. Accepts either the
   * legacy narrow shape or the full `UsageResponse` from `/api/operator/usage`.
   * When the full shape is supplied, the bar shows a soft-warn styled gauge
   * and an expandable details panel grouped by session/host/model/day.
   */
  usage?: MetadataBarUsage | null;
  className?: string;
};

/** Type-guard for the full `UsageResponse` shape. */
function isFullUsage(u: MetadataBarUsage): u is UsageResponse {
  return (
    typeof (u as UsageResponse).prompts_this_hour === "number" &&
    Array.isArray((u as UsageResponse).by_session)
  );
}

/** Resolve a session mode string to a valid COPILOT_MODES value, or fall back to first. */
function resolveFormMode(sessionMode: string): string {
  return COPILOT_MODES.find((m) => m.value === sessionMode)?.value ?? COPILOT_MODES[0].value;
}

/**
 * Compact metadata bar showing workspace, model, mode, and run count for the
 * active operator session. When `onUpdate` is supplied, an edit affordance
 * appears that lets the user change the session name, model, and mode via a
 * small popover form. Edits are disabled while a run is active.
 *
 * Pass `openEditor={true}` to programmatically open the editor (e.g. from the
 * `/session` slash command). Reset it to `false` via `onEditorClose`.
 */
export function MetadataBar({
  session,
  isRunning,
  isUpdating,
  onUpdate,
  openEditor,
  onEditorClose,
  usage,
  className,
}: MetadataBarProps) {
  const editDisabled = isRunning || isUpdating;

  const [open, setOpen] = useState(false);
  const [name, setName] = useState(session.name);
  const [model, setModel] = useState(session.model ?? "");
  const [mode, setMode] = useState(() => resolveFormMode(session.mode));
  // Track whether the user explicitly changed the mode this session so we
  // don't silently migrate legacy mode values on a no-op save.
  const [modeChanged, setModeChanged] = useState(false);
  // #556: usage panel popover open state.
  const [usagePanelOpen, setUsagePanelOpen] = useState(false);

  // Track previous openEditor value so we only trigger on rising edge.
  const prevOpenEditorRef = useRef(false);

  function syncFormToSession() {
    setName(session.name);
    setModel(session.model ?? "");
    setMode(resolveFormMode(session.mode));
    setModeChanged(false);
  }

  // Respond to external open requests (e.g. from the /session slash command).
  useEffect(() => {
    const prev = prevOpenEditorRef.current;
    prevOpenEditorRef.current = !!openEditor;
    if (openEditor && !prev && !editDisabled) {
      syncFormToSession();
      setOpen(true);
    }
    // syncFormToSession is stable (reads from session via closure); editDisabled
    // is a derived boolean — intentionally omitted to fire only on openEditor change.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [openEditor]);

  useEffect(() => {
    syncFormToSession();
    if (open) {
      setOpen(false);
      onEditorClose?.();
    }
    // Re-sync and close stale editor state when the active session changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [session.id]);

  function handleOpenChange(next: boolean) {
    if (next && editDisabled) return;
    if (next) syncFormToSession();
    setOpen(next);
    if (!next) onEditorClose?.();
  }

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!onUpdate) return;

    const payload: UpdateOperatorSessionRequest = {};
    const trimmedName = name.trim();
    if (trimmedName && trimmedName !== session.name) payload.name = trimmedName;
    const trimmedModel = model.trim();
    if (trimmedModel !== (session.model ?? "")) payload.model = trimmedModel;
    if (modeChanged && mode !== session.mode && COPILOT_MODES.some((m) => m.value === mode)) {
      payload.mode = mode as MutableOperatorSessionMode;
    }

    if (Object.keys(payload).length > 0) {
      onUpdate(payload);
    }
    handleOpenChange(false);
  }

  const cliMeta = session.cli_metadata ?? null;
  const fullUsage = usage && isFullUsage(usage) ? usage : null;

  // #556: Soft-warn threshold detection. Triggered when prompts_this_hour
  // crosses the configured threshold (default 80%) — render with the warn
  // colour palette so the operator notices before hitting the hard cap.
  const overHourlyWarn =
    fullUsage !== null &&
    fullUsage.hourly_limit > 0 &&
    fullUsage.prompts_this_hour >= fullUsage.soft_warn_threshold_hour;
  const overSessionWarn =
    fullUsage !== null &&
    typeof fullUsage.session_hour === "number" &&
    typeof fullUsage.soft_warn_threshold_session === "number" &&
    fullUsage.soft_warn_threshold_session > 0 &&
    fullUsage.session_hour >= fullUsage.soft_warn_threshold_session;

  return (
    <>
      <div
        className={cn(
          "bg-muted/30 flex flex-wrap items-center gap-x-4 gap-y-1 border-b px-4 py-1.5 text-xs",
          className
        )}
      >
        <span className="text-muted-foreground flex items-center gap-1">
          <FolderOpen className="size-3" />
          <span className="font-mono">{session.workspace}</span>
        </span>
        <span className="text-muted-foreground flex items-center gap-1">
          <Cpu className="size-3" />
          <span>{session.model}</span>
        </span>
        <span className="text-muted-foreground flex items-center gap-1">
          <Settings2 className="size-3" />
          <span>{session.mode}</span>
        </span>
        <span className="text-muted-foreground flex items-center gap-1">
          <Hash className="size-3" />
          <span>
            {session.run_count} run{session.run_count !== 1 ? "s" : ""}
          </span>
        </span>
        <span
          className={cn(
            "flex items-center gap-1 rounded px-1.5 py-0.5",
            session.resume_ready
              ? "bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-400"
              : "bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-300"
          )}
        >
          {session.resume_ready ? (
            <>
              <RefreshCw className="size-3" />
              <span>context ready</span>
            </>
          ) : (
            <>
              <RefreshCw className="size-3" />
              <span>new context</span>
            </>
          )}
        </span>

        {usage && fullUsage ? (
          <Popover open={usagePanelOpen} onOpenChange={setUsagePanelOpen}>
            <PopoverTrigger
              render={
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  className={cn(
                    "h-5 gap-1 px-1.5 text-xs",
                    overHourlyWarn || overSessionWarn
                      ? "text-amber-700 dark:text-amber-400"
                      : "text-muted-foreground"
                  )}
                  data-testid="usage-gauge"
                  aria-label="Open usage details"
                >
                  <Gauge className="size-3" />
                  <span>
                    {fullUsage.remaining_hour}/{fullUsage.hourly_limit} left
                  </span>
                  <ChevronDown className="size-3 opacity-60" />
                </Button>
              }
            />
            <PopoverContent align="start" className="w-96" data-testid="usage-panel">
              <PopoverHeader>
                <PopoverTitle>Usage details</PopoverTitle>
              </PopoverHeader>
              <div className="mt-2 space-y-3 text-xs">
                <div className="grid grid-cols-2 gap-2">
                  <div className="bg-muted/30 rounded p-2">
                    <p className="text-muted-foreground">This hour</p>
                    <p
                      className={cn(
                        "font-mono text-sm",
                        overHourlyWarn ? "text-amber-700 dark:text-amber-400" : ""
                      )}
                    >
                      {fullUsage.prompts_this_hour}/{fullUsage.hourly_limit}
                    </p>
                    <p className="text-muted-foreground">{fullUsage.remaining_hour} remaining</p>
                  </div>
                  <div className="bg-muted/30 rounded p-2">
                    <p className="text-muted-foreground">Today</p>
                    <p className="font-mono text-sm">
                      {fullUsage.prompts_today}/{fullUsage.daily_limit}
                    </p>
                    <p className="text-muted-foreground">{fullUsage.remaining_day} remaining</p>
                  </div>
                </div>
                {typeof fullUsage.session_hour === "number" &&
                typeof fullUsage.session_cap_hour === "number" ? (
                  <div className="bg-muted/30 rounded p-2">
                    <p className="text-muted-foreground">This session (hour)</p>
                    <p
                      className={cn(
                        "font-mono text-sm",
                        overSessionWarn ? "text-amber-700 dark:text-amber-400" : ""
                      )}
                    >
                      {fullUsage.session_hour}/{fullUsage.session_cap_hour}
                    </p>
                  </div>
                ) : null}
                <UsageGroupList
                  title="By session"
                  testId="usage-group-by-session"
                  entries={fullUsage.by_session}
                />
                <UsageGroupList
                  title="By host"
                  testId="usage-group-by-host"
                  entries={fullUsage.by_host}
                />
                <UsageGroupList
                  title="By model"
                  testId="usage-group-by-model"
                  entries={fullUsage.by_model}
                />
                <UsageGroupList
                  title="By day"
                  testId="usage-group-by-day"
                  entries={fullUsage.by_day}
                />
                <p className="text-muted-foreground">
                  Soft-warn at {Math.round(fullUsage.soft_warn_fraction * 100)}% · override policy:{" "}
                  <span className="font-mono">{fullUsage.override_policy}</span>
                </p>
              </div>
            </PopoverContent>
          </Popover>
        ) : usage ? (
          <span className="text-muted-foreground flex items-center gap-1" data-testid="usage-gauge">
            <Gauge className="size-3" />
            <span>
              {usage.remaining_hour}/{usage.hourly_limit} left
            </span>
          </span>
        ) : null}

        {onUpdate ? (
          <Popover open={open} onOpenChange={handleOpenChange}>
            <PopoverTrigger
              render={
                <Button
                  type="button"
                  variant="ghost"
                  size="icon"
                  className="size-5 shrink-0"
                  disabled={editDisabled}
                  aria-label={
                    isRunning
                      ? "Editing disabled during active run"
                      : isUpdating
                        ? "Saving…"
                        : "Edit session"
                  }
                  data-testid="edit-session-btn"
                >
                  <Pencil className="size-3" />
                </Button>
              }
            />
            <PopoverContent align="start" className="w-80">
              <PopoverHeader>
                <PopoverTitle>Edit Session</PopoverTitle>
              </PopoverHeader>
              <form onSubmit={handleSubmit} className="mt-2 space-y-3">
                <div className="space-y-1">
                  <label className="text-xs font-medium">Name</label>
                  <input
                    type="text"
                    value={name}
                    onChange={(e) => setName(e.target.value)}
                    placeholder="Session name"
                    disabled={editDisabled}
                    className="border-input placeholder:text-muted-foreground focus-visible:border-ring focus-visible:ring-ring/50 w-full rounded border bg-transparent px-2 py-1 text-xs transition-colors outline-none focus-visible:ring-1 disabled:opacity-50"
                  />
                </div>
                <div className="space-y-1">
                  <label className="text-xs font-medium">Model</label>
                  <input
                    type="text"
                    value={model}
                    onChange={(e) => setModel(e.target.value)}
                    placeholder="CLI default"
                    disabled={editDisabled}
                    className="border-input placeholder:text-muted-foreground focus-visible:border-ring focus-visible:ring-ring/50 w-full rounded border bg-transparent px-2 py-1 text-xs transition-colors outline-none focus-visible:ring-1 disabled:opacity-50"
                  />
                </div>
                <div className="space-y-1">
                  <label className="text-xs font-medium">Mode</label>
                  <Select
                    value={mode}
                    onValueChange={(v) => {
                      if (v) {
                        setMode(v);
                        setModeChanged(true);
                      }
                    }}
                    disabled={editDisabled}
                  >
                    <SelectTrigger className="h-7 text-xs">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {COPILOT_MODES.map((m) => (
                        <SelectItem key={m.value} value={m.value} className="text-xs">
                          {m.label}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
                {/* UX copy: next-run semantics and operator-approval notice */}
                <p className="text-muted-foreground text-xs" data-testid="next-run-note">
                  Changes apply to the next run. Hosted and operator prompts remain
                  operator-approved — no additional browser permission is required.
                </p>
                <div className="flex justify-end gap-2">
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    className="h-6 px-2 text-xs"
                    onClick={() => handleOpenChange(false)}
                  >
                    Cancel
                  </Button>
                  <Button
                    type="submit"
                    size="sm"
                    className="h-6 px-2 text-xs"
                    disabled={editDisabled}
                  >
                    {isUpdating ? "Saving…" : "Save"}
                  </Button>
                </div>
              </form>
            </PopoverContent>
          </Popover>
        ) : null}
      </div>
      {cliMeta ? (
        <div
          className={cn(
            "bg-muted/20 flex flex-wrap items-center gap-x-4 gap-y-1 border-b px-4 py-1 text-xs",
            className
          )}
          data-testid="cli-metadata-row"
          aria-label="Adopted CLI session metadata"
        >
          {cliMeta.cli_kind ? (
            <span className="text-muted-foreground flex items-center gap-1" data-testid="cli-kind">
              <Terminal className="size-3" />
              <span>
                {cliMeta.cli_kind}
                {cliMeta.cli_version ? ` ${cliMeta.cli_version}` : ""}
              </span>
            </span>
          ) : null}
          {cliMeta.model_version ? (
            <span
              className="text-muted-foreground flex items-center gap-1"
              data-testid="cli-model-version"
            >
              <Cpu className="size-3" />
              <span>{cliMeta.model_version}</span>
            </span>
          ) : null}
          {cliMeta.branch ? (
            <span
              className="text-muted-foreground flex items-center gap-1"
              data-testid="cli-branch"
            >
              <GitBranch className="size-3" />
              <span>{cliMeta.branch}</span>
            </span>
          ) : null}
          {cliMeta.started_at ? (
            <span
              className="text-muted-foreground flex items-center gap-1"
              data-testid="cli-started-at"
            >
              <Clock className="size-3" />
              <span title="Started">started {cliMeta.started_at}</span>
            </span>
          ) : null}
          {cliMeta.last_activity ? (
            <span
              className="text-muted-foreground flex items-center gap-1"
              data-testid="cli-last-activity"
            >
              <Clock className="size-3" />
              <span title="Last activity">active {cliMeta.last_activity}</span>
            </span>
          ) : null}
          {cliMeta.tool_inventory_summary ? (
            <span className="text-muted-foreground flex items-center gap-1" data-testid="cli-tools">
              <Wrench className="size-3" />
              <span>{cliMeta.tool_inventory_summary}</span>
            </span>
          ) : null}
          {cliMeta.host_profile_id ? (
            <span
              className="text-muted-foreground flex items-center gap-1"
              data-testid="cli-host-profile"
            >
              <Settings2 className="size-3" />
              <span>{cliMeta.host_profile_id}</span>
            </span>
          ) : null}
        </div>
      ) : null}
    </>
  );
}

/** Render the top-N aggregation rows from a usage-by-X array. */
function UsageGroupList({
  title,
  testId,
  entries,
}: {
  title: string;
  testId: string;
  entries: Array<{ key: string; count: number }>;
}) {
  if (!entries || entries.length === 0) {
    return null;
  }
  const top = entries.slice(0, 5);
  return (
    <div data-testid={testId}>
      <p className="text-muted-foreground mb-0.5">{title}</p>
      <ul className="space-y-0.5">
        {top.map((entry) => (
          <li key={entry.key} className="flex items-center justify-between gap-2">
            <span className="min-w-0 truncate font-mono">{entry.key || "—"}</span>
            <span className="text-muted-foreground tabular-nums">{entry.count}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}
