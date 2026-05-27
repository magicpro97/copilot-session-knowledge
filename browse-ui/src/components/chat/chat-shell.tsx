"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { useRouter, useSearchParams, usePathname } from "next/navigation";
import {
  Bot,
  Globe,
  Menu,
  PanelLeftClose,
  PanelLeftOpen,
  ServerCog,
  BookOpen,
  Zap,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/data/empty-state";
import { Banner } from "@/components/data/banner";
import { Sheet, SheetContent, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import {
  useOperatorSessions,
  useOperatorSession,
  useOperatorRuns,
  useOperatorActiveRuns,
  useCreateOperatorSession,
  useDeleteOperatorSession,
  useSubmitPrompt,
  useUpdateOperatorSession,
  useSkillCatalog,
  useAdoptCliSession,
  useConfirmAdoptedSession,
  useTentacleStatus,
  useOperatorUsage,
  useGenericPromptPreflight,
} from "@/lib/api/hooks";
import {
  getAllHostProfiles,
  setSelectedHostId,
  LOCAL_HOST,
  LOCAL_HOST_ID,
  isOperatorHostEnabled,
} from "@/lib/host-profiles";
import { useHostState } from "@/providers/host-provider";
import { cn } from "@/lib/utils";
import { SessionList } from "./session-list";
import { SessionCreateDialog } from "./session-create-dialog";
import { MetadataBar } from "./metadata-bar";
import { Transcript } from "./transcript";
import { Composer } from "./composer";
import { ConfirmAdoptionPanel } from "./cli-session-picker";
import { WorkbenchPanel } from "./workbench-panel";
import { TentacleStatusChip } from "./tentacle-status-chip";
import { COPILOT_MODES } from "./session-create-dialog";
import { SLASH_COMMANDS } from "./slash-commands";
import { findRecoverableActiveRun, visibleHistoricalRuns, type ActiveRun } from "./run-state";
import type {
  HostProfile,
  OperatorRunInfo,
  OperatorSession,
  QueuedFile,
  RunFileMetadata,
  UpdateOperatorSessionRequest,
  MutableOperatorSessionMode,
} from "@/lib/api/types";
import type { CreateSessionPayload, AdoptSessionPayload } from "./session-create-dialog";

const SESSION_PARAM = "s";
/** Stores the host profile id for the active session's agent host. */
const HOST_PARAM = "h";

function getSkillCatalogErrorMessage(host: HostProfile, error: unknown): string {
  const message = error instanceof Error ? error.message : "";

  if (/API 404\b/i.test(message)) {
    return `This host does not support the installed skills catalog yet. Update the browse backend running on ${host.label}.`;
  }
  if (/Unauthorized/i.test(message)) {
    return `This host rejected the installed skills request. Reconnect ${host.label} with a valid token and try again.`;
  }
  return `Failed to load skills from ${host.label}. Check that the browse server is running and reachable.`;
}

function getSkillCatalogUnavailableMessage(host: HostProfile): string {
  if (!host.base_url) {
    return "Connect a compatible agent host before opening installed skills from this hosted control plane.";
  }
  return `This host is not reachable from the current page. Reconnect ${host.label} with a compatible HTTPS host.`;
}

export function ChatShell() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const pathname = usePathname();
  const { host: globalHost, diagnosticsEnabled, localDiagnosticsEnabled } = useHostState();

  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [mobileSidebarOpen, setMobileSidebarOpen] = useState(false);
  const [activeRun, setActiveRun] = useState<ActiveRun | null>(null);
  const [suppressedRecoveryRunId, setSuppressedRecoveryRunId] = useState<string | null>(null);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [commandBanner, setCommandBanner] = useState<{
    title: string;
    description?: string;
  } | null>(null);

  // Slash command overlay state
  const [helpOpen, setHelpOpen] = useState(false);
  const [skillsOpen, setSkillsOpen] = useState(false);
  const [metadataEditorOpen, setMetadataEditorOpen] = useState(false);
  /**
   * Controlled open signal for SessionCreateDialog.
   * Incrementing this value (and setting dialogInitialTab) causes the dialog to
   * open on the requested tab without any DOM querying or timers.
   */
  const [dialogOpenSignal, setDialogOpenSignal] = useState(0);
  const [dialogInitialTab, setDialogInitialTab] = useState<"new" | "cli">("new");

  const activeSessionId = searchParams.get(SESSION_PARAM) ?? null;
  const hParam = searchParams.get(HOST_PARAM);

  useEffect(() => {
    setMetadataEditorOpen(false);
    setCommandBanner(null);
  }, [activeSessionId]);

  // Resolve the active HostProfile.
  // Priority: h= URL param (preserved for direct links) → shared browse-wide host selection.
  const activeHost = useMemo(() => {
    if (hParam) {
      if (hParam === LOCAL_HOST_ID) return LOCAL_HOST;
      const saved = getAllHostProfiles();
      return saved.find((p) => p.id === hParam) ?? LOCAL_HOST;
    }
    return globalHost;
  }, [globalHost, hParam]);
  const operatorEnabled = useMemo(
    () =>
      isOperatorHostEnabled(activeHost, pathname) ||
      (activeHost.id === LOCAL_HOST_ID && (localDiagnosticsEnabled ?? diagnosticsEnabled)),
    [activeHost, diagnosticsEnabled, localDiagnosticsEnabled, pathname]
  );

  // Load all sessions from the active host
  const sessionsQuery = useOperatorSessions(activeHost, operatorEnabled);
  const sessions = useMemo(() => sessionsQuery.data?.sessions ?? [], [sessionsQuery.data]);

  // Load active session detail
  const sessionQuery = useOperatorSession(
    activeSessionId ?? "",
    Boolean(activeSessionId) && operatorEnabled,
    activeHost
  );
  const session = sessionQuery.data ?? null;

  // Load full persisted run history for the active session.
  // Gate on sessionQuery.isSuccess so a stale/missing session fires only one 404.
  const runsQuery = useOperatorRuns(
    activeSessionId ?? "",
    Boolean(activeSessionId) && operatorEnabled && sessionQuery.isSuccess,
    activeHost
  );

  // Issue #564: Chat Workbench feed. The hook itself is also gated by the
  // `runs_workbench` capability inside the WorkbenchPanel component; here we
  // additionally read it to derive the per-session active-run indicator set
  // for the SessionList. When the host does not advertise the capability the
  // hook stays disabled and the indicator set is empty — preserving the
  // legacy SessionList appearance.
  const activeRunsQuery = useOperatorActiveRuns(operatorEnabled, activeHost);
  const activeRunSessionIds = useMemo(() => {
    const set = new Set<string>();
    for (const run of activeRunsQuery.data?.runs ?? []) {
      if (run?.session_id) set.add(run.session_id);
    }
    return set;
  }, [activeRunsQuery.data]);

  const createMutation = useCreateOperatorSession(activeHost);
  const deleteMutation = useDeleteOperatorSession(activeHost);
  const promptMutation = useSubmitPrompt(activeSessionId ?? "", activeHost);
  const updateMutation = useUpdateOperatorSession(activeSessionId ?? "", activeHost);
  const adoptMutation = useAdoptCliSession(activeHost);
  const confirmMutation = useConfirmAdoptedSession(activeSessionId ?? "", activeHost);

  // #556/#557: Usage gauge data + generic preflight + soft-cap override.
  // Bound to the active session so the gauge shows session-scoped fields too.
  const usageQuery = useOperatorUsage(
    activeSessionId ?? undefined,
    Boolean(activeSessionId) && operatorEnabled,
    activeHost
  );
  const preflightMutation = useGenericPromptPreflight(activeHost);

  const runPreflight = useCallback(
    async (req: { prompt: string; files: Array<{ name: string; type: string; size: number }> }) => {
      return preflightMutation.mutateAsync({
        prompt: req.prompt,
        model: session?.model || undefined,
        host_id: activeHost.id,
        session_id: activeSessionId ?? undefined,
        files: req.files,
      });
    },
    [preflightMutation, session?.model, activeHost.id, activeSessionId]
  );

  // #556 follow-up: soft-cap override audit is recorded server-side by
  // `handle_run_prompt` when the resubmit carries `override_acknowledged: true`.
  // The standalone `/api/operator/usage/override` endpoint remains available
  // (via `useUsageOverride`) for explicit non-prompt audit flows, but the
  // chat composer no longer calls it — that would double-record the audit
  // entry and halve effective retention.

  // Lazy-loaded skill catalog — only fetched when the /skills overlay is open.
  const skillCatalogQuery = useSkillCatalog(activeHost, skillsOpen && operatorEnabled);
  const skillCatalogMessage = useMemo(() => {
    if (!operatorEnabled) {
      return getSkillCatalogUnavailableMessage(activeHost);
    }
    if (skillCatalogQuery.isError) {
      return getSkillCatalogErrorMessage(activeHost, skillCatalogQuery.error);
    }
    return null;
  }, [activeHost, operatorEnabled, skillCatalogQuery.error, skillCatalogQuery.isError]);

  // Tentacle orchestration status — reuses the existing useTentacleStatus hook
  // with no additional polling loop; relies on React Query stale/gc intervals.
  const tentacleStatusQuery = useTentacleStatus(activeHost, operatorEnabled);

  // Select a session → update URL (preserve host param)
  const handleSelectSession = useCallback(
    (id: string) => {
      const params = new URLSearchParams(searchParams.toString());
      params.set(SESSION_PARAM, id);
      router.push(`${pathname}?${params.toString()}`);
      setActiveRun(null);
      setSuppressedRecoveryRunId(null);
      setMetadataEditorOpen(false);
      setCommandBanner(null);
      setSubmitError(null);
      setMobileSidebarOpen(false);
    },
    [router, pathname, searchParams]
  );

  // Navigate to an adopted session — uses operator session id only.
  // SECURITY: CLI UUID must not appear in this function or in the URL.
  const navigateToOperatorSession = useCallback(
    (operatorId: string, host: HostProfile) => {
      const params = new URLSearchParams(searchParams.toString());
      params.set(SESSION_PARAM, operatorId);
      if (host.id !== LOCAL_HOST_ID) {
        params.set(HOST_PARAM, host.id);
        setSelectedHostId(host.id);
      } else {
        params.delete(HOST_PARAM);
        setSelectedHostId(LOCAL_HOST_ID);
      }
      router.push(`${pathname}?${params.toString()}`);
      setActiveRun(null);
      setSuppressedRecoveryRunId(null);
      setMetadataEditorOpen(false);
      setCommandBanner(null);
      setSubmitError(null);
      setMobileSidebarOpen(false);
    },
    [router, pathname, searchParams]
  );

  // Create a new session — strip the non-API `host` field before sending to the backend
  const handleCreateSession = useCallback(
    (payload: CreateSessionPayload) => {
      const { host, ...apiPayload } = payload;
      createMutation.mutate(
        { payload: apiPayload, host },
        {
          onSuccess: (newSession: OperatorSession) => {
            navigateToOperatorSession(newSession.id, host);
          },
        }
      );
    },
    [createMutation, navigateToOperatorSession]
  );

  // Adopt a CLI session.
  // SECURITY: cli_session_id goes only in the POST JSON body via adoptMutation.
  // The returned operator session id is used for navigation — never the CLI UUID.
  const handleAdoptSession = useCallback(
    (payload: AdoptSessionPayload) => {
      const { host, ...adoptPayload } = payload;
      adoptMutation.mutate(
        { payload: adoptPayload, host },
        {
          onSuccess: (newSession: OperatorSession) => {
            // Navigate using operator id only — CLI UUID is gone from here.
            navigateToOperatorSession(newSession.id, host);
          },
        }
      );
    },
    [adoptMutation, navigateToOperatorSession]
  );

  // Confirm an adopted CLI session (POST /confirm with empty JSON body).
  const handleConfirmAdoption = useCallback(() => {
    if (!activeSessionId) return;
    confirmMutation.mutate(undefined, {
      onSuccess: () => {
        // Session cache is invalidated by the hook; session detail refetch picks up confirmed_at.
        void sessionQuery.refetch();
      },
    });
  }, [activeSessionId, confirmMutation, sessionQuery]);

  // Delete a session
  const handleDeleteSession = useCallback(
    (id: string) => {
      deleteMutation.mutate(id, {
        onSuccess: () => {
          if (activeSessionId === id) {
            const params = new URLSearchParams(searchParams.toString());
            params.delete(SESSION_PARAM);
            router.push(`${pathname}?${params.toString()}`);
            setActiveRun(null);
            setSuppressedRecoveryRunId(null);
            setMetadataEditorOpen(false);
            setCommandBanner(null);
          }
        },
      });
    },
    [deleteMutation, activeSessionId, router, pathname, searchParams]
  );

  // Submit a prompt with optional file attachments
  const handleSubmitPrompt = useCallback(
    (prompt: string, files: QueuedFile[] = [], options?: { overrideAcknowledged?: boolean }) => {
      if (!activeSessionId) return;
      setCommandBanner(null);
      setSubmitError(null);

      // Build user-visible file metadata (strip base64 content after reading)
      const runFiles: RunFileMetadata[] = files.map(({ name, type, size }) => ({
        name,
        type,
        size,
      }));

      promptMutation.mutate(
        {
          prompt,
          files: files.length > 0 ? files : undefined,
          override_acknowledged: options?.overrideAcknowledged || undefined,
          // #556: Forward active host id so server-side usage ledger groups
          // submissions under the correct host (not the implicit "local").
          host_id: activeHost.id,
        },
        {
          onSuccess: (result) => {
            setSuppressedRecoveryRunId(null);
            setActiveRun({
              id: result.run_id,
              prompt,
              files: runFiles.length > 0 ? runFiles : undefined,
            });
          },
          onError: (error: unknown) => {
            const message = error instanceof Error ? error.message : "Failed to submit prompt";
            setSubmitError(message);
          },
        }
      );
    },
    [activeSessionId, activeHost.id, promptMutation]
  );

  // Keep the active run rendered until persisted history refresh completes to
  // avoid a brief "disappearing reply" window after the stream closes.
  const handleRunDone = useCallback(
    (status: "done" | "error", runId: string) => {
      setSuppressedRecoveryRunId(status === "error" ? runId : null);
      if (activeSessionId) {
        void Promise.allSettled([sessionQuery.refetch(), runsQuery.refetch()]).finally(() => {
          setActiveRun(null);
        });
        return;
      }
      setActiveRun(null);
    },
    [activeSessionId, runsQuery, sessionQuery]
  );

  const allRuns = useMemo(() => runsQuery.data?.runs ?? [], [runsQuery.data]);

  useEffect(() => {
    if (activeRun || !activeSessionId || runsQuery.isLoading || !runsQuery.isFetchedAfterMount) {
      return;
    }
    const recovered = findRecoverableActiveRun(allRuns, suppressedRecoveryRunId);
    if (recovered) {
      setActiveRun(recovered);
    }
  }, [
    activeRun,
    activeSessionId,
    allRuns,
    runsQuery.isFetchedAfterMount,
    runsQuery.isLoading,
    suppressedRecoveryRunId,
  ]);

  // While an active run is still streaming, hide its persisted copy if it has
  // already landed in history so the transcript shows it exactly once.
  const runs: OperatorRunInfo[] = useMemo(
    () => visibleHistoricalRuns(allRuns, activeRun),
    [activeRun, allRuns]
  );

  const isRunning = promptMutation.isPending || activeRun !== null;

  // Update session name/model/mode via the verified PATCH mutation.
  const handleUpdateSession = useCallback(
    (payload: UpdateOperatorSessionRequest) => {
      if (!activeSessionId) return;
      setCommandBanner(null);
      updateMutation.mutate({ payload });
    },
    [updateMutation, activeSessionId]
  );

  // Handle slash command dispatch from the Composer.
  const handleCommand = useCallback(
    (name: string, args: string) => {
      setCommandBanner(null);
      switch (name) {
        case "help":
          setHelpOpen(true);
          break;
        case "skills":
          setSkillsOpen(true);
          break;
        case "history": {
          // Open the New Chat dialog on the CLI History tab.
          // Use the openSignal/initialTab controlled props instead of DOM
          // queries + timers, which are racy with the dialog's own tab reset.
          setDialogInitialTab("cli");
          setDialogOpenSignal((s) => s + 1);
          break;
        }
        case "session":
          if (!activeSessionId) {
            break;
          }
          if (updateMutation.isPending) {
            setCommandBanner({
              title: "Session settings unavailable",
              description:
                "Wait for the current settings save to finish before reopening the session editor.",
            });
            break;
          }
          if (isRunning) {
            setCommandBanner({
              title: "Session settings unavailable",
              description: "Wait for the active run to finish before editing this session.",
            });
            break;
          }
          if (activeSessionId) {
            setMetadataEditorOpen(true);
          }
          break;
        case "new": {
          // Open the New Chat dialog on the New Session tab via the controlled
          // signal. Avoids DOM querying.
          setDialogInitialTab("new");
          setDialogOpenSignal((s) => s + 1);
          break;
        }
        case "clear":
          // The Composer has already cleared its draft and queued attachments.
          // Persisted server-side run history is not affected.
          break;
        case "mode": {
          const modeArg = args.toLowerCase();
          const validModes = COPILOT_MODES.map((m) => m.value);
          if (!activeSessionId) {
            break;
          }
          if (updateMutation.isPending) {
            setCommandBanner({
              title: "Mode change pending",
              description:
                "Wait for the current session update to finish before changing modes again.",
            });
            break;
          }
          if (!modeArg || !validModes.includes(modeArg)) {
            setCommandBanner({
              title: "Invalid /mode command",
              description:
                "Use /mode interactive, /mode plan, or /mode autopilot. Changes apply to the next run.",
            });
            break;
          }
          updateMutation.mutate({
            payload: { mode: modeArg as MutableOperatorSessionMode },
          });
          break;
        }
        default:
          break;
      }
    },
    [activeSessionId, isRunning, updateMutation]
  );

  // Determine if the active session is an unconfirmed CLI adoption.
  const isUnconfirmedAdoption = session?.source === "cli_adopt" && !session?.confirmed_at;

  // Composer disabled: no session, settings updating, or unconfirmed CLI adoption.
  const composerDisabled = !activeSessionId || updateMutation.isPending || isUnconfirmedAdoption;

  // Whether adopt/create operations are pending (for dialog loading state).
  const sessionCreating = createMutation.isPending || adoptMutation.isPending;

  return (
    <div className="flex h-full overflow-hidden" data-testid="chat-shell">
      {/* ── Help dialog ──────────────────────────────────────────────────────── */}
      <Dialog open={helpOpen} onOpenChange={setHelpOpen}>
        <DialogContent className="max-w-lg" aria-label="Slash command help">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <BookOpen className="size-4" />
              Slash Commands
            </DialogTitle>
          </DialogHeader>
          <div className="space-y-4 overflow-y-auto" style={{ maxHeight: "60vh" }}>
            <section>
              <p className="text-muted-foreground mb-2 text-xs font-semibold tracking-wide uppercase">
                Supported in Browse Chat
              </p>
              <ul className="space-y-1.5">
                {SLASH_COMMANDS.filter((c) => c.scope === "web").map((cmd) => (
                  <li key={cmd.name} className="flex items-baseline gap-2">
                    <span className="text-primary w-36 shrink-0 font-mono text-xs">
                      {cmd.usage}
                    </span>
                    <span className="text-muted-foreground text-xs">{cmd.description}</span>
                  </li>
                ))}
              </ul>
            </section>
            <section>
              <p className="text-muted-foreground mb-2 text-xs font-semibold tracking-wide uppercase">
                CLI Reference (not available in web)
              </p>
              <ul className="space-y-1.5">
                {SLASH_COMMANDS.filter((c) => c.scope === "cli-reference").map((cmd) => (
                  <li key={cmd.name} className="flex items-baseline gap-2 opacity-60">
                    <span className="w-36 shrink-0 font-mono text-xs">{cmd.usage}</span>
                    <span className="text-muted-foreground text-xs">{cmd.description}</span>
                  </li>
                ))}
              </ul>
              <p className="text-muted-foreground mt-3 text-xs">
                These commands are only available in the Copilot CLI terminal, not in this web
                interface.
              </p>
            </section>
          </div>
        </DialogContent>
      </Dialog>

      {/* ── Skills dialog ────────────────────────────────────────────────────── */}
      <Dialog open={skillsOpen} onOpenChange={setSkillsOpen}>
        <DialogContent className="max-w-lg" aria-label="Installed skills">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <Zap className="size-4" />
              Installed Skills
            </DialogTitle>
          </DialogHeader>
          <div className="overflow-y-auto" style={{ maxHeight: "60vh" }}>
            {skillCatalogQuery.isLoading ? (
              <p className="text-muted-foreground animate-pulse py-4 text-center text-sm">
                Loading skills…
              </p>
            ) : skillCatalogMessage ? (
              <p className="text-destructive py-4 text-center text-sm">{skillCatalogMessage}</p>
            ) : !skillCatalogQuery.data?.skills.length ? (
              <p className="text-muted-foreground py-4 text-center text-sm">
                No skills installed. Add skills to your global or project skills directory.
              </p>
            ) : (
              <ul className="space-y-2" data-testid="skills-list">
                {skillCatalogQuery.data.skills.map((skill) => (
                  <li
                    key={skill.id}
                    className="bg-muted/40 rounded-md px-3 py-2"
                    data-testid="skill-entry"
                  >
                    <div className="flex items-center gap-2">
                      <span className="text-sm font-medium">{skill.name}</span>
                      <span
                        className={cn(
                          "rounded px-1.5 py-0.5 font-mono text-xs",
                          skill.status === "installed"
                            ? "bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-400"
                            : "bg-slate-100 text-slate-500 dark:bg-slate-800"
                        )}
                      >
                        {skill.source_kind}
                      </span>
                      {skill.status !== "installed" ? (
                        <span className="text-destructive ml-auto text-xs">unavailable</span>
                      ) : null}
                    </div>
                    {skill.description ? (
                      <p className="text-muted-foreground mt-0.5 text-xs">{skill.description}</p>
                    ) : null}
                  </li>
                ))}
              </ul>
            )}
            {skillCatalogQuery.data ? (
              <p className="text-muted-foreground mt-3 text-xs">
                {skillCatalogQuery.data.total} skill
                {skillCatalogQuery.data.total !== 1 ? "s" : ""} installed
              </p>
            ) : null}
          </div>
        </DialogContent>
      </Dialog>

      {/* Session list sidebar — desktop split-pane (hidden on mobile) */}
      <aside
        className={cn(
          "bg-card hidden shrink-0 flex-col border-r transition-[width] duration-200 motion-reduce:transition-none md:flex",
          sidebarOpen ? "md:w-72" : "md:w-0 md:overflow-hidden"
        )}
      >
        <div className="flex items-center justify-between border-b px-3 py-2">
          <p className="text-sm font-semibold">Chat Sessions</p>
          <Button
            type="button"
            variant="ghost"
            size="icon"
            className="size-7"
            onClick={() => setSidebarOpen(false)}
            aria-label="Collapse session list"
          >
            <PanelLeftClose className="size-4" />
          </Button>
        </div>
        <div className="border-b p-2">
          <SessionCreateDialog
            onSubmit={handleCreateSession}
            onAdopt={handleAdoptSession}
            initialHost={activeHost}
            loading={sessionCreating}
            openSignal={dialogOpenSignal}
            initialTab={dialogInitialTab}
          />
        </div>
        <div className="flex-1 overflow-y-auto">
          <SessionList
            sessions={sessions}
            activeId={activeSessionId}
            onSelect={handleSelectSession}
            onDelete={handleDeleteSession}
            loading={sessionsQuery.isLoading}
            isDeleting={deleteMutation.isPending}
            activeRunSessionIds={activeRunSessionIds}
          />
        </div>
        <WorkbenchPanel
          host={activeHost}
          activeSessionId={activeSessionId}
          onSelectRun={(run) => handleSelectSession(run.session_id)}
        />
      </aside>

      {/* Mobile session sidebar — Sheet overlay (visible only on small screens) */}
      <Sheet open={mobileSidebarOpen} onOpenChange={setMobileSidebarOpen}>
        <SheetContent side="left" className="flex flex-col gap-0 p-0" showCloseButton={false}>
          <SheetHeader className="sr-only">
            <SheetTitle>Chat Sessions</SheetTitle>
          </SheetHeader>
          <div className="flex items-center justify-between border-b px-3 py-2">
            <p className="text-sm font-semibold">Chat Sessions</p>
          </div>
          <div className="border-b p-2">
            <SessionCreateDialog
              onSubmit={handleCreateSession}
              onAdopt={handleAdoptSession}
              initialHost={activeHost}
              loading={sessionCreating}
              openSignal={dialogOpenSignal}
              initialTab={dialogInitialTab}
            />
          </div>
          <div className="flex-1 overflow-y-auto">
            <SessionList
              sessions={sessions}
              activeId={activeSessionId}
              onSelect={handleSelectSession}
              onDelete={handleDeleteSession}
              loading={sessionsQuery.isLoading}
              isDeleting={deleteMutation.isPending}
              activeRunSessionIds={activeRunSessionIds}
            />
          </div>
          <WorkbenchPanel
            host={activeHost}
            activeSessionId={activeSessionId}
            onSelectRun={(run) => {
              handleSelectSession(run.session_id);
              setMobileSidebarOpen(false);
            }}
          />
        </SheetContent>
      </Sheet>

      {/* Main chat area */}
      <div className="flex min-w-0 flex-1 flex-col overflow-hidden">
        {/* Top bar */}
        <div className="bg-card flex h-10 items-center gap-2 border-b px-3">
          {/* Mobile: hamburger to open the session Sheet */}
          <Button
            type="button"
            variant="ghost"
            size="icon"
            className="size-7 shrink-0 md:hidden"
            onClick={() => setMobileSidebarOpen(true)}
            aria-label="Open session list"
          >
            <Menu className="size-4" />
          </Button>
          {/* Desktop: expand-sidebar button when sidebar is collapsed */}
          {!sidebarOpen ? (
            <Button
              type="button"
              variant="ghost"
              size="icon"
              className="hidden size-7 shrink-0 md:flex"
              onClick={() => setSidebarOpen(true)}
              aria-label="Expand session list"
            >
              <PanelLeftOpen className="size-4" />
            </Button>
          ) : null}
          {session ? (
            <p className="truncate text-sm font-medium">{session.name}</p>
          ) : (
            <p className="text-muted-foreground flex items-center gap-1.5 text-sm">
              CLI Chat
              {activeHost.id !== LOCAL_HOST_ID ? (
                <span
                  className="bg-muted inline-flex max-w-[180px] items-center gap-1 truncate rounded px-1.5 py-0.5 font-mono text-xs"
                  title={activeHost.base_url}
                >
                  <Globe className="size-3 shrink-0" />
                  {activeHost.base_url.replace(/^https?:\/\//, "")}
                </span>
              ) : operatorEnabled ? (
                <span
                  className="text-muted-foreground/60 flex items-center gap-0.5 font-mono text-xs"
                  data-testid="local-host-chip"
                >
                  <ServerCog className="size-3 shrink-0" />
                  local
                </span>
              ) : null}
            </p>
          )}
          <div className="ml-auto">
            <TentacleStatusChip
              data={tentacleStatusQuery.data}
              isLoading={tentacleStatusQuery.isLoading}
              isError={tentacleStatusQuery.isError}
            />
          </div>
        </div>

        {/* Metadata bar */}
        {session ? (
          <MetadataBar
            session={session}
            isRunning={isRunning}
            isUpdating={updateMutation.isPending}
            onUpdate={handleUpdateSession}
            openEditor={metadataEditorOpen}
            onEditorClose={() => setMetadataEditorOpen(false)}
            usage={usageQuery.data ?? null}
          />
        ) : null}

        {/* CLI adoption confirmation panel */}
        {isUnconfirmedAdoption && session ? (
          <ConfirmAdoptionPanel
            sessionName={session.name}
            workspace={session.workspace}
            addDirs={session.add_dirs}
            onConfirm={handleConfirmAdoption}
            isConfirming={confirmMutation.isPending}
          />
        ) : null}

        {/* Error banners */}
        {commandBanner ? (
          <Banner
            tone="warning"
            title={commandBanner.title}
            description={commandBanner.description}
            className="mx-4 mt-3"
          />
        ) : null}
        {submitError ? (
          <Banner
            tone="danger"
            title="Submit failed"
            description={submitError}
            className="mx-4 mt-3"
          />
        ) : null}
        {sessionsQuery.isError ? (
          <Banner
            tone="danger"
            title="Failed to load sessions"
            description="Check that the browse server is running."
            className="mx-4 mt-3"
          />
        ) : null}
        {runsQuery.isError && activeSessionId ? (
          <Banner
            tone="danger"
            title="Failed to load chat history"
            description="The session loaded, but its persisted runs could not be retrieved."
            className="mx-4 mt-3"
          />
        ) : null}

        {/* Content area */}
        {!activeSessionId ? (
          <div className="flex flex-1 items-center justify-center p-8">
            <EmptyState
              title="No session selected"
              description={
                operatorEnabled
                  ? "Select an existing session from the list or create a new one to start chatting."
                  : "No compatible host is configured. Open the local browse app directly, or add a public HTTPS tunnel host (e.g. ngrok) in New Chat."
              }
              icon={<Bot className="size-5" />}
              actionLabel="New Chat"
              onAction={() => {
                const btn = document.querySelector<HTMLButtonElement>(
                  '[aria-label="New chat session"]'
                );
                btn?.click();
              }}
            />
          </div>
        ) : sessionQuery.isLoading ? (
          <div className="flex flex-1 items-center justify-center">
            <p className="text-muted-foreground animate-pulse text-sm">Loading session…</p>
          </div>
        ) : sessionQuery.isError ? (
          <div className="flex flex-1 items-center justify-center p-8">
            <EmptyState
              title="Session not found"
              description="This session may have been deleted."
              icon={<Bot className="size-5" />}
            />
          </div>
        ) : (
          <>
            <Transcript
              runs={runs}
              activeRun={activeRun}
              sessionId={activeSessionId}
              host={activeHost}
              loading={runsQuery.isLoading}
              onRunDone={handleRunDone}
            />
            <Composer
              onSubmit={handleSubmitPrompt}
              onCommand={handleCommand}
              loading={isRunning}
              disabled={composerDisabled}
              runPreflight={runPreflight}
            />
          </>
        )}
      </div>
    </div>
  );
}
