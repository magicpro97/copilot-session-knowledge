"use client";

import { useState } from "react";
import { FileText, Eye, GitCompare, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { useFilePreview } from "@/lib/api/hooks";
import { cn } from "@/lib/utils";
import type { FileEntry } from "@/components/chat/stream-derive";

type FileReviewPanelProps = {
  files: FileEntry[];
  className?: string;
};

function FilePreview({ path }: { path: string }) {
  const { data, isLoading, isError } = useFilePreview(path);

  if (isLoading) {
    return (
      <div className="space-y-2 p-3">
        <Skeleton className="h-4 w-3/4" />
        <Skeleton className="h-4 w-1/2" />
        <Skeleton className="h-4 w-2/3" />
      </div>
    );
  }

  if (isError || !data) {
    return <p className="text-destructive p-3 text-sm">Failed to load file preview.</p>;
  }

  return (
    <div className="space-y-2 p-3">
      <div className="text-muted-foreground flex items-center justify-between text-xs">
        <span className="font-mono">{data.path}</span>
        <span>
          {(data.size / 1024).toFixed(1)} KB · {data.mime}
        </span>
      </div>
      <pre className="bg-muted/40 max-h-96 overflow-auto rounded p-3 font-mono text-xs whitespace-pre-wrap">
        {data.content}
      </pre>
    </div>
  );
}

/** Renders a unified diff string captured from the tool payload (e.g. apply_patch detailedContent). */
function InlineDiff({ unifiedDiff }: { unifiedDiff: string }) {
  const lines = unifiedDiff.split("\n");
  const added = lines.filter((l) => l.startsWith("+") && !l.startsWith("+++")).length;
  const removed = lines.filter((l) => l.startsWith("-") && !l.startsWith("---")).length;

  return (
    <div className="p-3">
      <p className="text-muted-foreground mb-2 text-xs">
        +{added} added · -{removed} removed
      </p>
      <div className="bg-muted/40 max-h-96 overflow-auto rounded p-2 font-mono text-xs">
        {lines.map((line, i) => {
          const kind =
            line.startsWith("+") && !line.startsWith("+++")
              ? "add"
              : line.startsWith("-") && !line.startsWith("---")
                ? "remove"
                : line.startsWith("@@")
                  ? "hunk"
                  : "context";
          return (
            <div
              key={i}
              data-diff-kind={kind}
              className={cn(
                "rounded-sm px-1 py-0.5",
                kind === "add" &&
                  "border-l-2 border-emerald-500/40 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300",
                kind === "remove" &&
                  "border-l-2 border-rose-500/40 bg-rose-500/10 text-rose-700 dark:text-rose-300",
                kind === "hunk" &&
                  "border-l-2 border-sky-500/30 bg-sky-500/10 text-sky-700 dark:text-sky-300"
              )}
            >
              {line || " "}
            </div>
          );
        })}
      </div>
    </div>
  );
}

type ReviewMode = "preview" | "diff";
type FileSlot = FileEntry & { reviewMode: ReviewMode };

/**
 * Inline file review panel. Lists files extracted from tool events and
 * renders preview or inline diff (when diff payload is available) on demand.
 *
 * The diff button is only shown when `file.unifiedDiff` is present — this
 * ensures we never diff a file against itself (which produced empty diffs).
 */
export function FileReviewPanel({ files, className }: FileReviewPanelProps) {
  const [selected, setSelected] = useState<FileSlot | null>(null);

  if (files.length === 0) return null;

  return (
    <div className={cn("rounded-lg border", className)}>
      <div className="border-b px-3 py-2">
        <p className="text-xs font-medium">Files touched</p>
      </div>
      <ul className="divide-y text-xs">
        {files.map((file) => {
          const isActive = selected?.path === file.path;
          return (
            <li key={file.path} className="flex flex-col">
              <div
                className={cn(
                  "flex items-center justify-between gap-2 px-3 py-2",
                  isActive && "bg-accent/30"
                )}
              >
                <div className="flex min-w-0 items-center gap-1.5">
                  <FileText className="text-muted-foreground size-3 shrink-0" />
                  <span className="truncate font-mono" title={file.path}>
                    {file.path}
                  </span>
                  <span
                    className={cn(
                      "rounded px-1 py-0.5 text-[10px] font-medium",
                      file.created
                        ? "bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-400"
                        : "bg-sky-100 text-sky-700 dark:bg-sky-900/30 dark:text-sky-400"
                    )}
                  >
                    {file.created ? "new" : "changed"}
                  </span>
                </div>
                <div className="flex shrink-0 gap-1">
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon"
                    className="size-6"
                    title="Preview file"
                    onClick={() => {
                      if (isActive && selected?.reviewMode === "preview") {
                        setSelected(null);
                      } else {
                        setSelected({ ...file, reviewMode: "preview" });
                      }
                    }}
                  >
                    <Eye className="size-3" />
                  </Button>
                  {/* Diff button only shown when truthful diff content is available */}
                  {file.unifiedDiff ? (
                    <Button
                      type="button"
                      variant="ghost"
                      size="icon"
                      className="size-6"
                      title="Show applied diff"
                      onClick={() => {
                        if (isActive && selected?.reviewMode === "diff") {
                          setSelected(null);
                        } else {
                          setSelected({ ...file, reviewMode: "diff" });
                        }
                      }}
                    >
                      <GitCompare className="size-3" />
                    </Button>
                  ) : null}
                  {isActive ? (
                    <Button
                      type="button"
                      variant="ghost"
                      size="icon"
                      className="size-6"
                      title="Close panel"
                      onClick={() => setSelected(null)}
                    >
                      <X className="size-3" />
                    </Button>
                  ) : null}
                </div>
              </div>
              {isActive ? (
                <div className="border-t">
                  {selected.reviewMode === "preview" ? (
                    <FilePreview path={file.path} />
                  ) : selected.reviewMode === "diff" && selected.unifiedDiff ? (
                    <InlineDiff unifiedDiff={selected.unifiedDiff} />
                  ) : null}
                </div>
              ) : null}
            </li>
          );
        })}
      </ul>
    </div>
  );
}
