"use client";

import { useEffect, useId, useMemo, useRef, useState } from "react";
import { usePathname } from "next/navigation";
import { Plus, Terminal } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { useOperatorModelCatalog, useCliSessions } from "@/lib/api/hooks";
import {
  isOperatorHostEnabled,
  LOCAL_HOST_ID,
  checkHostCompatibility,
  isLocalOrigin,
} from "@/lib/host-profiles";
import type { HostProfile, CreateOperatorSessionRequest, CliSession } from "@/lib/api/types";
import { useHostState } from "@/providers/host-provider";
import { useHostFeature } from "@/lib/hosts";
import { DiagnosticPanel } from "@/components/DiagnosticPanel";
import { WorkspacePicker } from "./workspace-picker";
import { HostPicker } from "./host-picker";
import { CliSessionPicker } from "./cli-session-picker";

export const COPILOT_MODES = [
  { value: "interactive", label: "Interactive" },
  { value: "plan", label: "Plan" },
  { value: "autopilot", label: "Autopilot" },
];

/**
 * Extends the base create-session request with the selected `HostProfile` so the
 * shell can pass the right host to all downstream API hooks.
 * The `host` field is stripped before the actual POST to the operator API.
 */
export type CreateSessionPayload = CreateOperatorSessionRequest & {
  host: HostProfile;
};

/**
 * Payload for adopting a CLI history session.
 * SECURITY: cli_session_id is carried here only for POST JSON body dispatch.
 * It must not be pushed to router URLs or stored in localStorage/sessionStorage.
 */
export type AdoptSessionPayload = {
  /** Internal CLI UUID — for POST JSON body only, never for URLs/storage */
  cli_session_id: string;
  host: HostProfile;
  name?: string;
};

type SessionCreateDialogProps = {
  onSubmit: (payload: CreateSessionPayload) => void;
  onAdopt?: (payload: AdoptSessionPayload) => void;
  initialHost: HostProfile;
  loading?: boolean;
  /**
   * Increment this counter to programmatically open the dialog.
   * ChatShell increments it when a slash command triggers dialog open.
   */
  openSignal?: number;
  /**
   * Which tab to land on when the dialog is opened via `openSignal`.
   * Defaults to "new" when omitted.
   */
  initialTab?: DialogTab;
};

type DialogTab = "new" | "cli";

export function SessionCreateDialog({
  onSubmit,
  onAdopt,
  initialHost,
  loading,
  openSignal,
  initialTab,
}: SessionCreateDialogProps) {
  const pathname = usePathname();
  const { diagnosticsEnabled, localDiagnosticsEnabled, probeResult } = useHostState();
  const nameId = useId();
  const workspaceId = useId();
  const modelId = useId();
  const modelListId = useId();
  const [open, setOpen] = useState(false);
  const [tab, setTab] = useState<DialogTab>("new");
  const [name, setName] = useState("");
  const [workspace, setWorkspace] = useState("");
  const [model, setModel] = useState("");
  const [mode, setMode] = useState(COPILOT_MODES[0].value);

  // Seed from the route-resolved active host so the form and create mutation
  // always target the same operator host. The user can still override per-session.
  const [host, setHost] = useState<HostProfile>(initialHost);
  const latestInitialHostRef = useRef(initialHost);
  /**
   * When the dialog is opened via `openSignal`, store the desired tab here so
   * the `[open]` effect below can pick it up instead of defaulting to "new".
   */
  const pendingTabRef = useRef<DialogTab | null>(null);
  const prevOpenSignalRef = useRef(openSignal ?? 0);

  useEffect(() => {
    latestInitialHostRef.current = initialHost;
  }, [initialHost]);

  // Programmatic open: when the parent increments openSignal, open the dialog
  // on the requested tab. We store the desired tab in a ref so the [open]
  // effect below can read it synchronously when React flushes the state change.
  useEffect(() => {
    if (openSignal !== undefined && openSignal !== prevOpenSignalRef.current) {
      prevOpenSignalRef.current = openSignal;
      pendingTabRef.current = initialTab ?? "new";
      setOpen(true);
    }
  }, [openSignal, initialTab]);

  useEffect(() => {
    if (open) {
      setHost(latestInitialHostRef.current);
      // Use pendingTab when the dialog was opened via openSignal; otherwise
      // reset to "new" so that manually triggered opens always start fresh.
      setTab(pendingTabRef.current ?? "new");
      pendingTabRef.current = null;
    }
  }, [open]);

  const hostReady =
    isOperatorHostEnabled(host, pathname) ||
    (host.id === LOCAL_HOST_ID && (localDiagnosticsEnabled ?? diagnosticsEnabled));
  const hostCompat = useMemo(
    () =>
      typeof window !== "undefined" ? checkHostCompatibility(window.location.origin, host) : null,
    [host]
  );
  const isHosted = useMemo(
    () => typeof window !== "undefined" && !isLocalOrigin(window.location.origin),
    []
  );

  // Gate CLI tab on `cli_adopt` capability. Local hosts are always supported.
  // Do not fetch CLI sessions at all until the feature is confirmed.
  const { supported: cliAdoptSupported, loading: cliAdoptLoading } = useHostFeature(
    host,
    "cli_adopt",
    hostReady
  );
  const showCliTab = Boolean(onAdopt) && cliAdoptSupported;

  const modelCatalogQuery = useOperatorModelCatalog(host, open && hostReady && tab === "new");
  const modelSuggestions = modelCatalogQuery.data?.models ?? [];
  const defaultModel = modelCatalogQuery.data?.default_model ?? "";

  // CLI sessions — only fetched when the CLI tab is open, host is ready, AND
  // the backend advertises the cli_adopt feature. This prevents polling
  // /api/operator/cli-sessions against unsupported backends.
  const cliSessionsQuery = useCliSessions(
    host,
    cliAdoptSupported && open && hostReady && tab === "cli"
  );
  const cliSessions = cliSessionsQuery.data?.sessions ?? [];
  // Treat 404 as "unavailable" (older backend) rather than "error"
  const cliUnavailable =
    cliSessionsQuery.isError &&
    cliSessionsQuery.error instanceof Error &&
    cliSessionsQuery.error.message.includes("404");
  const cliError = cliSessionsQuery.isError && !cliUnavailable;

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!workspace.trim()) return;
    onSubmit({
      name: name.trim() || `Chat ${new Date().toLocaleString()}`,
      workspace: workspace.trim(),
      model: model.trim() || undefined,
      mode,
      host,
    });
    setOpen(false);
    setName("");
    setWorkspace("");
    setModel("");
    setMode(COPILOT_MODES[0].value);
    setHost(latestInitialHostRef.current);
  }

  function handleCliSelect(session: CliSession) {
    if (!onAdopt) return;
    // SECURITY: cli_session_id travels only in this payload to become a POST JSON body.
    // It must never be pushed to router.push() or stored.
    onAdopt({
      cli_session_id: session.cli_session_id,
      host,
      name: session.title,
    });
    setOpen(false);
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger
        render={
          <Button size="sm" className="w-full" aria-label="New chat session">
            <Plus className="mr-2 size-4" />
            New Chat
          </Button>
        }
      />
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Start a new chat session</DialogTitle>
          <DialogDescription>
            Create a fresh session or resume one from CLI history.
          </DialogDescription>
        </DialogHeader>

        {/* Tab switcher */}
        <div className="bg-muted flex gap-1 rounded-lg p-1 text-xs font-medium" role="tablist">
          <button
            type="button"
            role="tab"
            aria-selected={tab === "new"}
            className={`flex-1 rounded-md px-3 py-1.5 transition-colors ${
              tab === "new"
                ? "bg-background text-foreground shadow-sm"
                : "text-muted-foreground hover:text-foreground"
            }`}
            onClick={() => setTab("new")}
          >
            New Session
          </button>
          {showCliTab ? (
            <button
              type="button"
              role="tab"
              aria-selected={tab === "cli"}
              className={`flex flex-1 items-center justify-center gap-1.5 rounded-md px-3 py-1.5 transition-colors ${
                tab === "cli"
                  ? "bg-background text-foreground shadow-sm"
                  : "text-muted-foreground hover:text-foreground"
              }`}
              onClick={() => setTab("cli")}
              disabled={cliAdoptLoading}
              data-testid="cli-history-tab"
            >
              <Terminal className="size-3" />
              From CLI History
            </button>
          ) : null}
        </div>

        {tab === "new" ? (
          <form onSubmit={handleSubmit} className="space-y-4">
            <div className="space-y-1.5">
              <label className="text-sm font-medium">Agent Host</label>
              <HostPicker value={host} onChange={setHost} disabled={loading} />
              <p className="text-muted-foreground text-xs">
                {isHosted
                  ? "Open the local browse app directly, or add a public HTTPS tunnel URL below."
                  : "Local (same origin) or a saved public tunnel URL."}
              </p>
              {!hostReady ? (
                <DiagnosticPanel
                  compat={hostCompat}
                  probeResult={probeResult}
                  isHosted={isHosted}
                  hostUrl={host.base_url}
                />
              ) : null}
            </div>
            <div className="space-y-1.5">
              <label htmlFor={workspaceId} className="text-sm font-medium">
                Workspace <span className="text-destructive">*</span>
              </label>
              <WorkspacePicker
                id={workspaceId}
                value={workspace}
                onChange={setWorkspace}
                disabled={loading}
                host={host}
              />
              <p className="text-muted-foreground text-xs">
                Must be a path under <code>~/</code> on the host.
              </p>
            </div>
            <div className="space-y-1.5">
              <label htmlFor={nameId} className="text-sm font-medium">
                Name
              </label>
              <input
                id={nameId}
                type="text"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="My session"
                disabled={loading}
                className="border-input placeholder:text-muted-foreground focus-visible:border-ring focus-visible:ring-ring/50 w-full rounded-lg border bg-transparent px-3 py-2 text-sm transition-colors outline-none focus-visible:ring-2 disabled:opacity-50"
              />
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-1.5">
                <label htmlFor={modelId} className="text-sm font-medium">
                  Model
                </label>
                <input
                  id={modelId}
                  type="text"
                  list={modelListId}
                  value={model}
                  onChange={(e) => setModel(e.target.value)}
                  placeholder={defaultModel || "Leave blank to use the CLI default"}
                  disabled={loading}
                  className="border-input placeholder:text-muted-foreground focus-visible:border-ring focus-visible:ring-ring/50 w-full rounded-lg border bg-transparent px-3 py-2 text-sm transition-colors outline-none focus-visible:ring-2 disabled:opacity-50"
                />
                <datalist id={modelListId}>
                  {modelSuggestions.map((m) => (
                    <option key={m.id} value={m.id} label={m.display_name} />
                  ))}
                </datalist>
                <p className="text-muted-foreground text-xs">Leave blank to use the CLI default.</p>
              </div>
              <div className="space-y-1.5">
                <label className="text-sm font-medium">Mode</label>
                <Select
                  value={mode}
                  onValueChange={(value) => {
                    if (value) setMode(value);
                  }}
                  disabled={loading}
                >
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {COPILOT_MODES.map((m) => (
                      <SelectItem key={m.value} value={m.value}>
                        {m.label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            </div>
            <DialogFooter>
              <DialogClose
                render={
                  <Button type="button" variant="outline" disabled={loading}>
                    Cancel
                  </Button>
                }
              />
              <Button type="submit" disabled={loading || !workspace.trim() || !hostReady}>
                {loading ? "Creating…" : "Start Chat"}
              </Button>
            </DialogFooter>
          </form>
        ) : (
          /* CLI History tab */
          <div className="space-y-3">
            <div className="space-y-1.5">
              <label className="text-sm font-medium">Agent Host</label>
              <HostPicker value={host} onChange={setHost} disabled={loading} />
              {!hostReady ? (
                <DiagnosticPanel
                  compat={hostCompat}
                  probeResult={probeResult}
                  isHosted={isHosted}
                  hostUrl={host.base_url}
                />
              ) : null}
            </div>
            <CliSessionPicker
              sessions={cliSessions}
              isLoading={cliSessionsQuery.isLoading}
              isError={cliError}
              unavailable={cliUnavailable || !hostReady}
              onSelect={handleCliSelect}
              isAdopting={loading}
            />
            <DialogFooter>
              <DialogClose
                render={
                  <Button type="button" variant="outline" disabled={loading}>
                    Cancel
                  </Button>
                }
              />
            </DialogFooter>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
