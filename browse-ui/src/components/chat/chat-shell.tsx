"use client";

import { useCallback, useMemo, useState } from "react";
import { useRouter, useSearchParams, usePathname } from "next/navigation";
import { Bot, Globe, Menu, PanelLeftClose, PanelLeftOpen, ServerCog } from "lucide-react";

import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/data/empty-state";
import { Banner } from "@/components/data/banner";
import { Sheet, SheetContent, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import {
  useOperatorSessions,
  useOperatorSession,
  useOperatorRuns,
  useCreateOperatorSession,
  useDeleteOperatorSession,
  useSubmitPrompt,
} from "@/lib/api/hooks";
import {
  getAllHostProfiles,
  getEffectiveHost,
  setSelectedHostId,
  clearSelectedHostId,
  LOCAL_HOST,
  LOCAL_HOST_ID,
} from "@/lib/host-profiles";
import { cn } from "@/lib/utils";
import { SessionList } from "./session-list";
import { SessionCreateDialog } from "./session-create-dialog";
import { MetadataBar } from "./metadata-bar";
import { Transcript } from "./transcript";
import { Composer } from "./composer";
import type {
  OperatorRunInfo,
  OperatorSession,
  QueuedFile,
  RunFileMetadata,
} from "@/lib/api/types";
import type { CreateSessionPayload } from "./session-create-dialog";

const SESSION_PARAM = "s";
/** Stores the host profile id for the active session's agent host. */
const HOST_PARAM = "h";

type ActiveRun = { id: string; prompt: string; files?: RunFileMetadata[] };

export function ChatShell() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const pathname = usePathname();

  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [mobileSidebarOpen, setMobileSidebarOpen] = useState(false);
  const [activeRun, setActiveRun] = useState<ActiveRun | null>(null);
  const [submitError, setSubmitError] = useState<string | null>(null);

  const activeSessionId = searchParams.get(SESSION_PARAM) ?? null;
  const hParam = searchParams.get(HOST_PARAM);

  // Resolve the active HostProfile.
  // Priority: h= URL param (preserved for direct links) → persisted selection → LOCAL_HOST.
  const activeHost = useMemo(() => {
    if (hParam) {
      if (hParam === LOCAL_HOST_ID) return LOCAL_HOST;
      const saved = getAllHostProfiles();
      return saved.find((p) => p.id === hParam) ?? LOCAL_HOST;
    }
    return getEffectiveHost();
  }, [hParam]);

  // Load all sessions from the active host
  const sessionsQuery = useOperatorSessions(activeHost);
  const sessions = useMemo(() => sessionsQuery.data?.sessions ?? [], [sessionsQuery.data]);

  // Load active session detail
  const sessionQuery = useOperatorSession(
    activeSessionId ?? "",
    Boolean(activeSessionId),
    activeHost
  );
  const session = sessionQuery.data ?? null;

  // Load full persisted run history for the active session.
  const runsQuery = useOperatorRuns(activeSessionId ?? "", Boolean(activeSessionId), activeHost);

  const createMutation = useCreateOperatorSession(activeHost);
  const deleteMutation = useDeleteOperatorSession(activeHost);
  const promptMutation = useSubmitPrompt(activeSessionId ?? "", activeHost);

  // Select a session → update URL (preserve host param)
  const handleSelectSession = useCallback(
    (id: string) => {
      const params = new URLSearchParams(searchParams.toString());
      params.set(SESSION_PARAM, id);
      router.push(`${pathname}?${params.toString()}`);
      setActiveRun(null);
      setSubmitError(null);
      setMobileSidebarOpen(false);
    },
    [router, pathname, searchParams]
  );

  // Create a new session — strip the non-API `host` field before sending to the backend
  const handleCreateSession = useCallback(
    (payload: CreateSessionPayload) => {
      const { host, ...apiPayload } = payload;
      createMutation.mutate(apiPayload, {
        onSuccess: (newSession: OperatorSession) => {
          const params = new URLSearchParams(searchParams.toString());
          params.set(SESSION_PARAM, newSession.id);
          if (host.id !== LOCAL_HOST_ID) {
            params.set(HOST_PARAM, host.id);
            setSelectedHostId(host.id);
          } else {
            params.delete(HOST_PARAM);
            clearSelectedHostId();
          }
          router.push(`${pathname}?${params.toString()}`);
          setActiveRun(null);
          setSubmitError(null);
          setMobileSidebarOpen(false);
        },
      });
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
  const handleRunDone = useCallback(() => {
    if (activeSessionId) {
      void Promise.allSettled([sessionQuery.refetch(), runsQuery.refetch()]).finally(() => {
        setActiveRun(null);
      });
      return;
    }
    setActiveRun(null);
  }, [activeSessionId, runsQuery, sessionQuery]);

  // While an active run is still streaming, hide its persisted copy if it has
  // already landed in history so the transcript shows it exactly once.
  const runs: OperatorRunInfo[] = useMemo(() => {
    const allRuns = runsQuery.data?.runs ?? [];
    if (!activeRun) return allRuns;
    return allRuns.filter((run) => run.id !== activeRun.id);
  }, [activeRun, runsQuery.data]);

  const isRunning = promptMutation.isPending || activeRun !== null;

  return (
    <div className="flex h-full overflow-hidden" data-testid="chat-shell">
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
          <SessionCreateDialog onSubmit={handleCreateSession} loading={createMutation.isPending} />
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
              ) : (
                <span className="text-muted-foreground/60 flex items-center gap-0.5 font-mono text-xs">
                  <ServerCog className="size-3 shrink-0" />
                  local
                </span>
              )}
            </p>
          )}
        </div>

        {/* Metadata bar */}
        {session ? <MetadataBar session={session} /> : null}

        {/* Error banners */}
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
              description="Select an existing session from the list or create a new one to start chatting."
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
              loading={isRunning}
              disabled={!activeSessionId}
            />
          </>
        )}
      </div>
    </div>
  );
}
