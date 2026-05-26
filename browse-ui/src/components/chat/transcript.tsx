"use client";

import { useCallback, useEffect, useRef } from "react";

import { formatDistanceToNow } from "date-fns";

import { Skeleton } from "@/components/ui/skeleton";
import { Button } from "@/components/ui/button";
import { UserBubble, AssistantBubble } from "./chat-bubbles";
import { FileReviewPanel } from "./file-review-panel";
import { deriveChunks, extractFilePaths } from "./stream-derive";
import { useOperatorStream } from "./use-operator-stream";
import { useCancelOperatorRun } from "@/lib/api/hooks";
import { useHostFeature } from "@/lib/hosts/use-host-feature";
import { LOCAL_HOST } from "@/lib/host-profiles";
import type { OperatorRunInfo, HostProfile, RunFileMetadata } from "@/lib/api/types";

type HistoricalRunProps = {
  run: OperatorRunInfo;
  host?: HostProfile | null;
};

function HistoricalRun({ run, host }: HistoricalRunProps) {
  const chunks = deriveChunks(run.events);
  const files = extractFilePaths(chunks);
  const ts = run.started_at
    ? formatDistanceToNow(new Date(run.started_at), { addSuffix: true })
    : undefined;

  const elapsedMs =
    run.started_at && run.finished_at
      ? new Date(run.finished_at).getTime() - new Date(run.started_at).getTime()
      : undefined;

  return (
    <div className="space-y-3">
      <UserBubble prompt={run.prompt} timestamp={ts} files={run.files} />
      <AssistantBubble
        chunks={chunks}
        exitCode={run.exit_code}
        elapsedMs={elapsedMs}
        resumeUsed={run.resume_used}
        timestamp={
          run.finished_at
            ? formatDistanceToNow(new Date(run.finished_at), { addSuffix: true })
            : undefined
        }
      />
      {files.length > 0 ? <FileReviewPanel files={files} host={host} /> : null}
    </div>
  );
}

type ActiveRunProps = {
  sessionId: string;
  runId: string;
  prompt: string;
  files?: RunFileMetadata[];
  host?: HostProfile | null;
  onDone?: (status: "done" | "error", runId: string) => void;
  onProgress?: () => void;
};

function ActiveRun({ sessionId, runId, prompt, files, host, onDone, onProgress }: ActiveRunProps) {
  const { frames, status, exitCode } = useOperatorStream(sessionId, runId, host);
  const onDoneRef = useRef(onDone);
  const onProgressRef = useRef(onProgress);

  // Issue #563: gate the Cancel button on the backend advertising
  // ``run_cancel`` in /api/operator/capabilities.  Legacy backends without
  // this feature do not expose the POST cancel endpoint, so hiding the
  // button fails closed (no broken 404 if a user clicked through).
  const hostProfile = host ?? LOCAL_HOST;
  const { supported: cancelSupported } = useHostFeature(hostProfile, "run_cancel", true);
  const cancelMutation = useCancelOperatorRun(sessionId, hostProfile);

  useEffect(() => {
    onDoneRef.current = onDone;
  }, [onDone]);

  useEffect(() => {
    onProgressRef.current = onProgress;
  }, [onProgress]);

  useEffect(() => {
    if (status === "done" || status === "error" || status === "cancelled") {
      // Issue #563: ``cancelled`` is a successful operator-initiated
      // terminal state.  We collapse it to ``"done"`` when notifying the
      // parent so existing onRunDone consumers (chat-shell) continue to
      // work without an API change.  The badge below still renders the
      // distinct cancelled state for the user.
      const parentStatus: "done" | "error" = status === "error" ? "error" : "done";
      onDoneRef.current?.(parentStatus, runId);
    }
  }, [runId, status]);

  useEffect(() => {
    if (status !== "idle") {
      onProgressRef.current?.();
    }
  }, [frames.length, status]);

  const chunks = deriveChunks(frames);
  const streamFiles = extractFilePaths(chunks);
  const streaming = status === "connecting" || status === "streaming";
  const cancelDisabled = cancelMutation.isPending || !streaming;

  const handleCancel = useCallback(() => {
    if (cancelDisabled) return;
    cancelMutation.mutate(runId);
  }, [cancelDisabled, cancelMutation, runId]);

  return (
    <div className="space-y-3">
      <UserBubble prompt={prompt} files={files} />
      <AssistantBubble chunks={chunks} streaming={streaming} exitCode={exitCode} />
      {streaming && cancelSupported ? (
        <div className="flex justify-end">
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={handleCancel}
            disabled={cancelDisabled}
            aria-label="Cancel current run"
            data-testid="cancel-run-button"
          >
            {cancelMutation.isPending ? "Cancelling…" : "Cancel run"}
          </Button>
        </div>
      ) : null}
      {status === "cancelled" ? (
        <div
          className="text-muted-foreground text-xs italic"
          role="status"
          data-testid="cancelled-badge"
        >
          Run cancelled by operator
        </div>
      ) : null}
      {streamFiles.length > 0 ? <FileReviewPanel files={streamFiles} host={host} /> : null}
    </div>
  );
}

type TranscriptProps = {
  /** All historical runs for this session (newest last). */
  runs: OperatorRunInfo[];
  /** The active run, if any. */
  activeRun?: { id: string; prompt: string; files?: RunFileMetadata[] } | null;
  sessionId: string;
  /** Host profile for the active session — used to route the live SSE stream. */
  host?: HostProfile | null;
  loading?: boolean;
  onRunDone?: (status: "done" | "error", runId: string) => void;
};

/**
 * Renders all historical runs and optionally a live-streaming active run.
 * Auto-scrolls to the bottom when new content arrives.
 */
export function Transcript({
  runs,
  activeRun,
  sessionId,
  host,
  loading,
  onRunDone,
}: TranscriptProps) {
  const bottomRef = useRef<HTMLDivElement>(null);
  const scrollToBottom = useCallback(() => {
    requestAnimationFrame(() => {
      bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
    });
  }, []);

  useEffect(() => {
    scrollToBottom();
  }, [runs.length, activeRun?.id, loading, scrollToBottom]);

  if (loading) {
    return (
      <div className="flex-1 space-y-4 overflow-y-auto p-4">
        <Skeleton className="ml-auto h-12 w-3/4" />
        <Skeleton className="h-20 w-4/5" />
        <Skeleton className="ml-auto h-12 w-2/3" />
        <Skeleton className="h-16 w-4/5" />
      </div>
    );
  }

  if (runs.length === 0 && !activeRun) {
    return (
      <div className="flex flex-1 items-center justify-center p-8">
        <div className="text-center">
          <p className="text-muted-foreground text-sm">
            Send a prompt below to start this session.
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="flex-1 space-y-6 overflow-y-auto p-4">
      {runs.map((run) => (
        <HistoricalRun key={run.id} run={run} host={host} />
      ))}
      {activeRun ? (
        <ActiveRun
          key={activeRun.id}
          sessionId={sessionId}
          runId={activeRun.id}
          prompt={activeRun.prompt}
          files={activeRun.files}
          host={host}
          onDone={onRunDone}
          onProgress={scrollToBottom}
        />
      ) : null}
      <div ref={bottomRef} />
    </div>
  );
}
