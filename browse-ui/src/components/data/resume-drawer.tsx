/**
 * Flight Recorder v3 — Resume drawer (synthesis §4d/§4e).
 *
 * Collapsed-by-default drawer that lists redaction-safe rewind snapshots
 * with their associated `task_complete` boundary (computed by
 * `deriveResumeAnchors`). NEVER renders raw `userMessage` text, only the
 * presence flag and byte size.
 *
 * Selectors:
 *   - data-testid="resume-drawer"
 *   - data-testid="resume-drawer-toggle" (aria-expanded)
 *   - data-testid="resume-anchor-<snapshotId>"
 *       data-git-commit, data-event-span-id
 */
import { useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";

import { Button } from "@/components/ui/button";
import type { ResumeAnchor } from "@/lib/flight-recorder";

export type ResumeDrawerProps = {
  anchors: ResumeAnchor[];
  /** Called when the user clicks a "Jump" affordance for an anchor. */
  onSeek?: (anchor: ResumeAnchor) => void;
  defaultOpen?: boolean;
};

function formatTimestamp(ts: string | null): string {
  if (!ts) return "—";
  try {
    return new Date(ts).toISOString().replace("T", " ").replace("Z", "");
  } catch {
    return "—";
  }
}

function shortCommit(sha: string | null): string {
  if (!sha) return "—";
  return sha.length >= 7 ? sha.slice(0, 7) : sha;
}

function formatAssociation(anchor: ResumeAnchor): { label: string; tone: string } {
  switch (anchor.association) {
    case "exact":
      return { label: "exact match", tone: "text-emerald-300" };
    case "nearest": {
      const dms = anchor.associationDistanceMs;
      const human =
        dms === null || !Number.isFinite(dms)
          ? "nearest"
          : dms < 1000
            ? `nearest ±${Math.round(dms)}ms`
            : dms < 60_000
              ? `nearest ±${(dms / 1000).toFixed(1)}s`
              : `nearest ±${Math.round(dms / 60_000)}m`;
      return { label: human, tone: "text-amber-300" };
    }
    case "unavailable":
    default:
      return { label: "unavailable", tone: "text-muted-foreground" };
  }
}

export function ResumeDrawer({ anchors, onSeek, defaultOpen = false }: ResumeDrawerProps) {
  const [open, setOpen] = useState<boolean>(defaultOpen);

  return (
    <div
      role="region"
      aria-label="Resume points"
      data-testid="resume-drawer"
      data-open={open ? "true" : "false"}
      className="border-border bg-card overflow-hidden rounded-lg border text-xs"
    >
      <button
        type="button"
        data-testid="resume-drawer-toggle"
        aria-expanded={open}
        aria-controls="resume-drawer-body"
        aria-label={open ? "Collapse resume points" : "Expand resume points"}
        onClick={() => setOpen((v) => !v)}
        className="hover:bg-muted/50 flex w-full items-center gap-2 px-3 py-1.5 text-left"
      >
        {open ? (
          <ChevronDown className="size-3" aria-hidden />
        ) : (
          <ChevronRight className="size-3" aria-hidden />
        )}
        <span className="font-medium">Resume points</span>
        <span className="text-muted-foreground ml-auto">
          {anchors.length} snapshot{anchors.length === 1 ? "" : "s"}
        </span>
      </button>

      {open && (
        <div id="resume-drawer-body" className="border-border border-t px-3 py-2">
          {anchors.length === 0 ? (
            <p className="text-muted-foreground py-2 text-center">No resume points</p>
          ) : (
            <ul className="space-y-1">
              {anchors.map((anchor) => {
                const assoc = formatAssociation(anchor);
                const disabled = anchor.association === "unavailable";
                return (
                  <li
                    key={anchor.snapshotId}
                    data-testid={`resume-anchor-${anchor.snapshotId}`}
                    data-git-commit={anchor.gitCommit ?? ""}
                    data-event-span-id={anchor.eventSpanId ?? ""}
                    data-association={anchor.association}
                    data-association-distance-ms={
                      anchor.associationDistanceMs === null
                        ? ""
                        : String(anchor.associationDistanceMs)
                    }
                    className="flex items-center justify-between gap-2"
                  >
                    <span className="font-mono">
                      <span className="text-muted-foreground">
                        {formatTimestamp(anchor.timestamp)}
                      </span>
                      {" · "}
                      <span className="text-cyan-300">{shortCommit(anchor.gitCommit)}</span>
                      {anchor.gitBranch ? (
                        <span className="text-muted-foreground"> · {anchor.gitBranch}</span>
                      ) : null}
                      {anchor.userMessagePresent ? (
                        <span className="text-muted-foreground">
                          {" "}
                          · prompt {anchor.userMessageByteSize}B
                        </span>
                      ) : null}
                      <span
                        className={`${assoc.tone} ml-1`}
                        aria-label={`association: ${assoc.label}`}
                      >
                        {" "}
                        · {assoc.label}
                      </span>
                    </span>
                    <Button
                      type="button"
                      variant="ghost"
                      size="xs"
                      aria-label={
                        disabled
                          ? `Jump unavailable for resume point ${anchor.snapshotId}`
                          : `Jump to resume point ${anchor.snapshotId} (${assoc.label})`
                      }
                      onClick={() => onSeek?.(anchor)}
                      disabled={disabled}
                    >
                      Jump
                    </Button>
                  </li>
                );
              })}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}
