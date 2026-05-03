"use client";

import { useEffect, useRef } from "react";

import { formatDistanceToNow } from "date-fns";

import { Skeleton } from "@/components/ui/skeleton";
import { UserBubble, AssistantBubble } from "./chat-bubbles";
import { FileReviewPanel } from "./file-review-panel";
import { deriveChunks, extractFilePaths } from "./stream-derive";
import { useOperatorStream } from "./use-operator-stream";
import type { OperatorRunInfo } from "@/lib/api/types";

type HistoricalRunProps = {
  run: OperatorRunInfo;
};

function HistoricalRun({ run }: HistoricalRunProps) {
  const chunks = deriveChunks(run.events);
  const files = extractFilePaths(chunks);
  const ts = run.started_at
    ? formatDistanceToNow(new Date(run.started_at), { addSuffix: true })
    : undefined;

  return (
    <div className="space-y-3">
      <UserBubble prompt={run.prompt} timestamp={ts} />
      <AssistantBubble
        chunks={chunks}
        exitCode={run.exit_code}
        timestamp={
          run.finished_at
            ? formatDistanceToNow(new Date(run.finished_at), { addSuffix: true })
            : undefined
        }
      />
      {files.length > 0 ? <FileReviewPanel files={files} /> : null}
    </div>
  );
}

type ActiveRunProps = {
  sessionId: string;
  runId: string;
  prompt: string;
  onDone?: () => void;
};

function ActiveRun({ sessionId, runId, prompt, onDone }: ActiveRunProps) {
  const { frames, status, exitCode } = useOperatorStream(sessionId, runId);
  const onDoneRef = useRef(onDone);

  useEffect(() => {
    onDoneRef.current = onDone;
  }, [onDone]);

  useEffect(() => {
    if (status === "done" || status === "error") {
      onDoneRef.current?.();
    }
  }, [status]);

  const chunks = deriveChunks(frames);
  const files = extractFilePaths(chunks);
  const streaming = status === "connecting" || status === "streaming";

  return (
    <div className="space-y-3">
      <UserBubble prompt={prompt} />
      <AssistantBubble chunks={chunks} streaming={streaming} exitCode={exitCode} />
      {files.length > 0 ? <FileReviewPanel files={files} /> : null}
    </div>
  );
}

type TranscriptProps = {
  /** All historical runs for this session (newest last). */
  runs: OperatorRunInfo[];
  /** The active run, if any. */
  activeRun?: { id: string; prompt: string } | null;
  sessionId: string;
  loading?: boolean;
  onRunDone?: () => void;
};

/**
 * Renders all historical runs and optionally a live-streaming active run.
 * Auto-scrolls to the bottom when new content arrives.
 */
export function Transcript({ runs, activeRun, sessionId, loading, onRunDone }: TranscriptProps) {
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [runs.length, activeRun?.id]);

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
        <HistoricalRun key={run.id} run={run} />
      ))}
      {activeRun ? (
        <ActiveRun
          key={activeRun.id}
          sessionId={sessionId}
          runId={activeRun.id}
          prompt={activeRun.prompt}
          onDone={onRunDone}
        />
      ) : null}
      <div ref={bottomRef} />
    </div>
  );
}
