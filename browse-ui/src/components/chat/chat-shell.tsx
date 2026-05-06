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
  useCreateOperatorSession,
  useDeleteOperatorSession,
  useSubmitPrompt,
  useUpdateOperatorSession,
  useSkillCatalog,
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
import { COPILOT_MODES } from "./session-create-dialog";
import { SLASH_COMMANDS } from "./slash-commands";
import { findRecoverableActiveRun, visibleHistoricalRuns, type ActiveRun } from "./run-state";
import type {
  OperatorRunInfo,
  OperatorSession,
  QueuedFile,
  RunFileMetadata,
  UpdateOperatorSessionRequest,
  MutableOperatorSessionMode,
} from "@/lib/api/types";
import type { CreateSessionPayload } from "./session-create-dialog";

const SESSION_PARAM = "s";
/** Stores the host profile id for the active session's agent host. */
const HOST_PARAM = "h";

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
  const runsQuery = useOperatorRuns(
    activeSessionId ?? "",
    Boolean(activeSessionId) && operatorEnabled,
    activeHost
  );

  const createMutation = useCreateOperatorSession(activeHost);
  const deleteMutation = useDeleteOperatorSession(activeHost);
  const promptMutation = useSubmitPrompt(activeSessionId ?? "", activeHost);
  const updateMutation = useUpdateOperatorSession(activeSessionId ?? "", activeHost);

  // Lazy-loaded skill catalog — only fetched when the /skills overlay is open.
  const skillCatalogQuery = useSkillCatalog(activeHost, skillsOpen && operatorEnabled);

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

  // Create a new session — strip the non-API `host` field before sending to the backend
  const handleCreateSession = useCallback(
    (payload: CreateSessionPayload) => {
      const { host, ...apiPayload } = payload;
      createMutation.mutate(
        { payload: apiPayload, host },
        {
          onSuccess: (newSession: OperatorSession) => {
            const params = new URLSearchParams(searchParams.toString());
            params.set(SESSION_PARAM, newSession.id);
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
        }
      );
    },
    [createMutation, router, pathname, searchParams]
  );

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
    (prompt: string, files: QueuedFile[] = []) => {
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
        { prompt, files: files.length > 0 ? files : undefined },
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
    [activeSessionId, promptMutation]
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
          // Reuse the existing "New chat session" button already rendered in the sidebar.
          const btn = document.querySelector<HTMLButtonElement>('[aria-label="New chat session"]');
          btn?.click();
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

  const composerDisabled = !activeSessionId || updateMutation.isPending;

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
            ) : skillCatalogQuery.isError ? (
              <p className="text-destructive py-4 text-center text-sm">
                Failed to load skills. Check that the browse server is running.
              </p>
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
            initialHost={activeHost}
            loading={createMutation.isPending}
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
          />
        </div>
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
              initialHost={activeHost}
              loading={createMutation.isPending}
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
            />
          </div>
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
            />
          </>
        )}
      </div>
    </div>
  );
}
