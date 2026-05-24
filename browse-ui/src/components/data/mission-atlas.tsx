/**
 * Mission Atlas — full-session event aggregate visualizer (Flight Recorder v4).
 *
 * Renders a compact, visual summary of a session's 55k+ events using only the
 * safe aggregate fields from GET /api/session/{id}/mission-atlas. Never
 * displays raw paths, args, error messages, or entry content.
 *
 * Key surfaces:
 *   - Mission header (total events, duration, artifacts, error count)
 *   - Bucket heatmap strip (horizontal, lane-colored intensity bars)
 *   - Lane totals + top tools / skills / agents as chips
 *   - Milestone rail (checkpoint, skill, rewind, error, compaction, task_complete)
 *
 * data-testid selectors:
 *   - "mission-atlas"              — root
 *   - "mission-atlas-bucket"       — each heatmap bucket (multiple)
 *   - "mission-atlas-milestone"    — each milestone button (multiple)
 */

import { useMemo } from "react";

import type {
  MissionAtlasBucket,
  MissionAtlasMilestone,
  MissionAtlasNameCount,
  SessionMissionAtlasResponse,
} from "@/lib/api/types";

// ── Helpers ───────────────────────────────────────────────────────────────────

function formatMs(ms: number | null | undefined): string {
  if (ms == null) return "—";
  if (ms < 1000) return `${Math.round(ms)}ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`;
  const m = Math.floor(ms / 60_000);
  const s = Math.floor((ms % 60_000) / 1000);
  return `${m}m${s.toString().padStart(2, "0")}s`;
}

function formatBytes(b: number | null | undefined): string {
  if (b == null) return "—";
  if (b < 1024) return `${b}B`;
  if (b < 1024 * 1024) return `${(b / 1024).toFixed(1)}KB`;
  return `${(b / (1024 * 1024)).toFixed(1)}MB`;
}

/** Human-readable milestone kind label. */
function milestoneKindLabel(kind: string): string {
  switch (kind) {
    case "checkpoint":
      return "⚑";
    case "skill":
    case "skill_invoked":
      return "◈";
    case "subagent":
    case "subagent_start":
      return "⊕";
    case "subagent_fail":
      return "!";
    case "rewind":
    case "rewind_snapshot":
      return "↺";
    case "error":
      return "✕";
    case "compaction":
      return "◐";
    case "task_complete":
      return "✓";
    default:
      return "•";
  }
}

/** Colour class for a milestone kind. */
function milestoneKindClass(kind: string): string {
  switch (kind) {
    case "checkpoint":
      return "text-violet-400 border-violet-700 bg-violet-950/40 hover:bg-violet-900/60";
    case "skill":
    case "skill_invoked":
      return "text-amber-400 border-amber-700 bg-amber-950/40 hover:bg-amber-900/60";
    case "subagent":
    case "subagent_start":
      return "text-cyan-400 border-cyan-700 bg-cyan-950/40 hover:bg-cyan-900/60";
    case "subagent_fail":
      return "text-red-300 border-red-700 bg-red-950/40 hover:bg-red-900/60";
    case "rewind":
    case "rewind_snapshot":
      return "text-blue-400 border-blue-700 bg-blue-950/40 hover:bg-blue-900/60";
    case "error":
      return "text-red-400 border-red-700 bg-red-950/40 hover:bg-red-900/60";
    case "compaction":
      return "text-slate-400 border-slate-600 bg-slate-950/40 hover:bg-slate-800/60";
    case "task_complete":
      return "text-emerald-400 border-emerald-700 bg-emerald-950/40 hover:bg-emerald-900/60";
    default:
      return "text-zinc-400 border-zinc-600 bg-zinc-950/40 hover:bg-zinc-800/60";
  }
}

/** Lane → CSS colour class for the heatmap bars. */
const LANE_BAR_CLASS: Record<string, string> = {
  tool: "bg-amber-500",
  hook: "bg-orange-500",
  skill: "bg-amber-400",
  subagent: "bg-cyan-500",
  model: "bg-violet-500",
  turn: "bg-emerald-500",
  system: "bg-blue-500",
  error: "bg-red-500",
  generic: "bg-zinc-500",
};

function laneBarClass(lane: string | null): string {
  if (!lane) return "bg-zinc-600";
  return LANE_BAR_CLASS[lane] ?? "bg-zinc-600";
}

/** Compute a 0–1 intensity from event_count relative to max in set. */
function computeIntensities(buckets: MissionAtlasBucket[]): number[] {
  if (buckets.length === 0) return [];
  const max = Math.max(...buckets.map((b) => b.event_count), 1);
  return buckets.map((b) => b.event_count / max);
}

// ── Sub-components ────────────────────────────────────────────────────────────

type ChipProps = {
  label: string;
  count: number;
  colorClass?: string;
};

function CountChip({ label, count, colorClass = "bg-muted/30 border-border" }: ChipProps) {
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs ${colorClass}`}
    >
      <span className="text-muted-foreground">{label}</span>
      <span className="font-medium tabular-nums">{count.toLocaleString()}</span>
    </span>
  );
}

type NameCountChipProps = {
  item: MissionAtlasNameCount;
  variant?: "tool" | "skill" | "agent";
};

function NameCountChip({ item, variant = "tool" }: NameCountChipProps) {
  const colorMap = {
    tool: "border-amber-700/50 bg-amber-950/20 text-amber-300",
    skill: "border-amber-600/50 bg-amber-900/20 text-amber-200",
    agent: "border-cyan-700/50 bg-cyan-950/20 text-cyan-300",
  };
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[10px] ${colorMap[variant]}`}
      title={`${item.name}: ${item.count}`}
    >
      <span className="max-w-[12ch] truncate font-mono">{item.name}</span>
      <span className="text-muted-foreground">·</span>
      <span className="tabular-nums">{item.count}</span>
    </span>
  );
}

// ── Props ─────────────────────────────────────────────────────────────────────

export type MissionAtlasProps = {
  /** Resolved data from useSessionMissionAtlas. */
  data: SessionMissionAtlasResponse;
  /**
   * Called when the user clicks a bucket or milestone that has a raw entry
   * index. Parent should open the debug log and/or seek the TimelinePlayer.
   */
  onSelectEntryIdx?: (idx: number) => void;
};

// ── Component ─────────────────────────────────────────────────────────────────

export function MissionAtlas({ data, onSelectEntryIdx }: MissionAtlasProps) {
  const intensities = useMemo(() => computeIntensities(data.buckets), [data.buckets]);

  const { artifact_counts: ac, lane_totals: lt } = data;
  const hasTruncation = Object.values(data.truncated).some(Boolean);

  // Aggregate chip lane totals into logical groups for display.
  const laneRows: Array<{ label: string; count: number; colorClass: string }> = useMemo(
    () =>
      [
        { label: "Turns", count: lt.turn, colorClass: "border-emerald-700/40 bg-emerald-950/20" },
        { label: "Model", count: lt.model, colorClass: "border-violet-700/40 bg-violet-950/20" },
        { label: "Tools", count: lt.tool, colorClass: "border-amber-700/40 bg-amber-950/20" },
        { label: "Hooks", count: lt.hook, colorClass: "border-orange-700/40 bg-orange-950/20" },
        { label: "Skills", count: lt.skill, colorClass: "border-amber-600/40 bg-amber-900/20" },
        {
          label: "Sub-agents",
          count: lt.subagent,
          colorClass: "border-cyan-700/40 bg-cyan-950/20",
        },
        { label: "System", count: lt.system, colorClass: "border-blue-700/40 bg-blue-950/20" },
        {
          label: "Errors",
          count: lt.error,
          colorClass:
            lt.error > 0 ? "border-red-700/60 bg-red-950/30" : "border-border bg-muted/20",
        },
        { label: "Generic", count: lt.generic, colorClass: "border-zinc-600/40 bg-zinc-900/20" },
      ].filter((r) => r.count > 0),
    [lt]
  );

  return (
    <div data-testid="mission-atlas" className="border-border bg-card/60 rounded-xl border text-sm">
      {/* ── Header ─────────────────────────────────────────────────────────── */}
      <div className="border-border flex flex-wrap items-center gap-x-4 gap-y-1.5 border-b px-3 py-2">
        <span className="text-muted-foreground text-xs font-medium tracking-wide uppercase">
          Full-session aggregate
        </span>
        <span className="text-foreground font-medium tabular-nums">
          {data.total_events.toLocaleString()} events
        </span>
        <span className="text-muted-foreground">{formatMs(data.duration_ms)}</span>
        {data.event_file_bytes != null && (
          <span className="text-muted-foreground">
            {formatBytes(data.event_file_bytes)} on disk
          </span>
        )}
        {data.error_count > 0 && (
          <span className="font-medium text-red-400">
            {data.error_count.toLocaleString()} error{data.error_count !== 1 ? "s" : ""}
          </span>
        )}
        {hasTruncation && <span className="text-xs text-amber-400">(truncated)</span>}
      </div>

      {/* ── Artifact counts ─────────────────────────────────────────────────── */}
      {(ac.checkpoint_files > 0 ||
        ac.rewind_snapshots > 0 ||
        ac.todos_total > 0 ||
        ac.files > 0 ||
        ac.compactions > 0) && (
        <div className="border-border flex flex-wrap gap-2 border-b px-3 py-1.5">
          <span className="text-muted-foreground self-center text-[10px] tracking-wide uppercase">
            Artifacts
          </span>
          {ac.checkpoint_files > 0 && (
            <CountChip
              label="Checkpoints"
              count={ac.checkpoint_files}
              colorClass="border-violet-700/40 bg-violet-950/20"
            />
          )}
          {ac.rewind_snapshots > 0 && (
            <CountChip
              label="Rewinds"
              count={ac.rewind_snapshots}
              colorClass="border-blue-700/40 bg-blue-950/20"
            />
          )}
          {ac.todos_total > 0 && (
            <CountChip
              label={`Todos (${ac.todos_done}✓${ac.todos_blocked > 0 ? ` ${ac.todos_blocked}✕` : ""})`}
              count={ac.todos_total}
              colorClass="border-border bg-muted/20"
            />
          )}
          {ac.files > 0 && (
            <CountChip
              label="Files touched"
              count={ac.files}
              colorClass="border-border bg-muted/20"
            />
          )}
          {ac.compactions > 0 && (
            <CountChip
              label="Compactions"
              count={ac.compactions}
              colorClass="border-slate-600/40 bg-slate-950/20"
            />
          )}
        </div>
      )}

      {/* ── Bucket heatmap ─────────────────────────────────────────────────── */}
      {data.buckets.length > 0 && (
        <div className="border-border border-b px-3 py-2">
          <div className="text-muted-foreground mb-1.5 text-[10px] tracking-wide uppercase">
            Activity heatmap ({data.bucket_count} buckets)
          </div>
          <div
            className="flex h-6 w-full gap-px overflow-hidden rounded"
            aria-label="Session activity heatmap"
            role="img"
          >
            {data.buckets.map((bucket, i) => {
              const intensity = intensities[i] ?? 0;
              const colorCls = bucket.is_gap
                ? "bg-zinc-800/30"
                : laneBarClass(bucket.dominant_lane);
              const opacity = bucket.is_gap ? 0.2 : Math.max(0.15, intensity);
              const startIdx = bucket.start_idx;
              const hasIdx = startIdx != null;

              return (
                <button
                  key={bucket.bucket_idx}
                  type="button"
                  data-testid="mission-atlas-bucket"
                  aria-label={
                    bucket.is_gap
                      ? `Gap bucket ${bucket.bucket_idx}`
                      : `Bucket ${bucket.bucket_idx}: ${bucket.event_count} events${bucket.dominant_lane ? `, dominant ${bucket.dominant_lane}` : ""}${bucket.error_count > 0 ? `, ${bucket.error_count} errors` : ""}`
                  }
                  className={`h-full flex-1 rounded-sm transition-opacity ${colorCls} ${hasIdx ? "cursor-pointer hover:opacity-90" : "cursor-default"} ${bucket.error_count > 0 && !bucket.is_gap ? "ring-1 ring-red-500/60" : ""}`}
                  style={{ opacity }}
                  onClick={
                    hasIdx && onSelectEntryIdx ? () => onSelectEntryIdx(startIdx) : undefined
                  }
                />
              );
            })}
          </div>
          {/* Lane colour legend */}
          <div className="mt-1.5 flex flex-wrap gap-x-3 gap-y-0.5">
            {Object.entries(LANE_BAR_CLASS).map(([lane, cls]) => {
              const total = lt[lane as keyof typeof lt] ?? 0;
              if (total === 0) return null;
              return (
                <span key={lane} className="flex items-center gap-1 text-[10px]">
                  <span className={`inline-block h-2 w-2 rounded-sm ${cls}`} />
                  <span className="text-muted-foreground capitalize">{lane}</span>
                </span>
              );
            })}
          </div>
        </div>
      )}

      {/* ── Lane totals + top lists ─────────────────────────────────────────── */}
      <div className="border-border border-b px-3 py-2">
        <div className="flex flex-wrap gap-1.5">
          {laneRows.map((r) => (
            <CountChip key={r.label} label={r.label} count={r.count} colorClass={r.colorClass} />
          ))}
        </div>
        {(data.top_tools.length > 0 ||
          data.top_skills.length > 0 ||
          data.top_agent_names.length > 0) && (
          <div className="mt-2 flex flex-wrap gap-1.5">
            {data.top_tools.map((t) => (
              <NameCountChip key={`tool-${t.name}`} item={t} variant="tool" />
            ))}
            {data.top_skills.map((s) => (
              <NameCountChip key={`skill-${s.name}`} item={s} variant="skill" />
            ))}
            {data.top_agent_names.map((a) => (
              <NameCountChip key={`agent-${a.name}`} item={a} variant="agent" />
            ))}
          </div>
        )}
      </div>

      {/* ── Milestone rail ─────────────────────────────────────────────────── */}
      {data.milestones.length > 0 && (
        <div className="px-3 py-2">
          <div className="text-muted-foreground mb-1.5 text-[10px] tracking-wide uppercase">
            Milestones
          </div>
          <div className="flex flex-wrap gap-1">
            {data.milestones.map((ms, i) => (
              <MilestoneButton
                key={`ms-${i}-${ms.kind}-${ms.idx ?? ms.bucket_idx ?? i}`}
                milestone={ms}
                onSelectEntryIdx={onSelectEntryIdx}
              />
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

// ── Milestone button ──────────────────────────────────────────────────────────

type MilestoneButtonProps = {
  milestone: MissionAtlasMilestone;
  onSelectEntryIdx?: (idx: number) => void;
};

function MilestoneButton({ milestone, onSelectEntryIdx }: MilestoneButtonProps) {
  const icon = milestoneKindLabel(milestone.kind);
  const colorCls = milestoneKindClass(milestone.kind);
  const canSeek = milestone.idx != null && onSelectEntryIdx != null;

  return (
    <button
      type="button"
      data-testid="mission-atlas-milestone"
      aria-label={`${milestone.kind}: ${milestone.label}${milestone.idx != null ? ` (entry ${milestone.idx})` : ""}`}
      title={`${milestone.kind}: ${milestone.label}${milestone.idx != null ? ` (entry #${milestone.idx})` : ""}`}
      disabled={!canSeek}
      className={`inline-flex items-center gap-1 rounded border px-2 py-0.5 text-[11px] transition-colors ${colorCls} ${canSeek ? "cursor-pointer" : "cursor-default opacity-70"}`}
      onClick={canSeek ? () => onSelectEntryIdx!(milestone.idx!) : undefined}
    >
      <span aria-hidden="true">{icon}</span>
      <span className="max-w-[20ch] truncate">{milestone.label}</span>
    </button>
  );
}

// ── Loading / error / empty states ────────────────────────────────────────────

export function MissionAtlasLoading() {
  return (
    <div
      data-testid="mission-atlas-loading"
      className="border-border text-muted-foreground animate-pulse rounded-xl border px-3 py-2 text-xs"
    >
      Loading session atlas…
    </div>
  );
}

export function MissionAtlasError({ error }: { error: Error }) {
  return (
    <div
      data-testid="mission-atlas-error"
      className="border-border text-muted-foreground rounded-xl border px-3 py-2 text-xs"
    >
      Atlas unavailable — {error.message.slice(0, 80)}
    </div>
  );
}
