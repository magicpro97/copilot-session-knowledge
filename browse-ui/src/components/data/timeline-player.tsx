import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import {
  Bookmark,
  ChevronLeft,
  ChevronRight,
  Clock,
  HelpCircle,
  Layers,
  Pause,
  Play,
  SkipBack,
  SkipForward,
} from "lucide-react";

import {
  PLAYBACK_LANE_ORDER,
  binEntriesByCount,
  binEntriesByTime,
  deriveMarkersFromEntries,
  derivePlaybackFrame,
  derivePlaybackLane,
  filterByLane,
  nextMarkerIndex,
  normalizeAndSortEntries,
  prevMarkerIndex,
} from "@/lib/debug-event-playback";
import type {
  PlaybackBaseEntry,
  PlaybackBin,
  PlaybackLaneId,
  PlaybackMarkerKind,
} from "@/lib/debug-event-playback";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

// ── Constants ─────────────────────────────────────────────────────────────────

const SPEEDS = [0.5, 1, 2, 4, 8] as const;
type Speed = (typeof SPEEDS)[number];

/** Max bars rendered total (spread across lanes). Prevents 5 000-node DOM. */
const RENDER_LIMIT = 2000;

/** Overview strip bins target count. */
const OVERVIEW_BINS = 100;

/** Base interval ms per event advance at 1× speed. */
const BASE_INTERVAL_MS = 800;

// ── Lane styling ──────────────────────────────────────────────────────────────

type LaneStyle = {
  label: string;
  labelCls: string;
  rowBg: string;
  activeBar: string;
  inactiveBar: string;
};

const LANE_STYLES: Record<PlaybackLaneId, LaneStyle> = {
  Session: {
    label: "Session",
    labelCls: "text-blue-400",
    rowBg: "bg-blue-950/20",
    activeBar: "bg-blue-500",
    inactiveBar: "bg-blue-800/35",
  },
  Turn: {
    label: "Turn",
    labelCls: "text-emerald-400",
    rowBg: "bg-emerald-950/20",
    activeBar: "bg-emerald-500",
    inactiveBar: "bg-emerald-800/35",
  },
  Model: {
    label: "Model",
    labelCls: "text-violet-400",
    rowBg: "bg-violet-950/20",
    activeBar: "bg-violet-500",
    inactiveBar: "bg-violet-800/35",
  },
  "Tool/Hook/Skill": {
    label: "Tool / Hook / Skill",
    labelCls: "text-amber-400",
    rowBg: "bg-amber-950/20",
    activeBar: "bg-amber-500",
    inactiveBar: "bg-amber-800/35",
  },
  SubAgent: {
    label: "Sub-Agent",
    labelCls: "text-cyan-400",
    rowBg: "bg-cyan-950/20",
    activeBar: "bg-cyan-500",
    inactiveBar: "bg-cyan-800/35",
  },
  "Notification/Compaction": {
    label: "Notification",
    labelCls: "text-slate-400",
    rowBg: "bg-slate-950/20",
    activeBar: "bg-slate-400",
    inactiveBar: "bg-slate-700/35",
  },
  Error: {
    label: "Error",
    labelCls: "text-red-400",
    rowBg: "bg-red-950/20",
    activeBar: "bg-red-500",
    inactiveBar: "bg-red-800/35",
  },
  Generic: {
    label: "Generic",
    labelCls: "text-zinc-400",
    rowBg: "bg-zinc-950/20",
    activeBar: "bg-zinc-500",
    inactiveBar: "bg-zinc-700/35",
  },
};

const MARKER_COLORS: Record<PlaybackMarkerKind, string> = {
  turn_start: "bg-emerald-400",
  agent_response: "bg-violet-400",
  error: "bg-red-400",
  compaction: "bg-amber-400",
};

// ── Helpers ───────────────────────────────────────────────────────────────────

function isTypingTarget(target: EventTarget | null): boolean {
  const el = target as HTMLElement | null;
  if (!el) return false;
  const tag = el.tagName?.toLowerCase() ?? "";
  return Boolean(el.isContentEditable) || tag === "input" || tag === "textarea" || tag === "select";
}

function formatElapsed(ms: number): string {
  if (ms < 1000) return `${ms}ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`;
  const m = Math.floor(ms / 60_000);
  const s = Math.floor((ms % 60_000) / 1000);
  return `${m}m${s.toString().padStart(2, "0")}s`;
}

function formatTimestamp(ts: string | null): string {
  if (!ts) return "—";
  try {
    return new Date(ts).toISOString().replace("T", " ").replace("Z", "");
  } catch {
    return ts;
  }
}

// ── Props ─────────────────────────────────────────────────────────────────────

type TimelinePlayerProps = {
  entries: PlaybackBaseEntry[];
  total: number;
  hasMore: boolean;
  active?: boolean;
  /** Called when the user clicks "Open in Debug Log" for the current event. */
  onOpenDebugLog?: (entryIdx: number) => void;
};

// ── Component ─────────────────────────────────────────────────────────────────

export function TimelinePlayer({
  entries,
  total,
  hasMore,
  active = false,
  onOpenDebugLog,
}: TimelinePlayerProps) {
  // ── Reduce-motion ─────────────────────────────────────────────────────────
  const [reducedMotion, setReducedMotion] = useState(false);
  useEffect(() => {
    if (typeof window === "undefined") return;
    const mq = window.matchMedia("(prefers-reduced-motion: reduce)");
    setReducedMotion(mq.matches);
    const handler = (e: MediaQueryListEvent) => setReducedMotion(e.matches);
    mq.addEventListener("change", handler);
    return () => mq.removeEventListener("change", handler);
  }, []);

  // ── Playback state ────────────────────────────────────────────────────────
  const [playheadIndex, setPlayheadIndex] = useState(0);
  const [isPlaying, setIsPlaying] = useState(false);
  const [speed, setSpeed] = useState<Speed>(1);
  const [showKeyHelp, setShowKeyHelp] = useState(false);

  // ── Normalized + derived data (memoized) ──────────────────────────────────
  const { normalized, mode } = useMemo(() => normalizeAndSortEntries(entries), [entries]);

  const markers = useMemo(() => deriveMarkersFromEntries(normalized), [normalized]);

  // Clamp index on entry changes
  useEffect(() => {
    setPlayheadIndex((prev) => Math.min(prev, Math.max(normalized.length - 1, 0)));
  }, [normalized.length]);

  /** Derive cursor from current playheadIndex */
  const cursor = useMemo(() => {
    if (normalized.length === 0) return 0;
    const clamped = Math.min(Math.max(playheadIndex, 0), normalized.length - 1);
    if (mode === "time") {
      // Fall back to +Infinity so a null-timestamp playhead shows all events
      // rather than collapsing the frame to blank (a sorted index is not a
      // valid ms-since-epoch cursor and would match nothing in time mode).
      return normalized[clamped].timestampMs ?? Number.POSITIVE_INFINITY;
    }
    return clamped;
  }, [normalized, mode, playheadIndex]);

  /**
   * When in time mode and the playhead entry has a null timestamp, pass its
   * exact sorted-array index to derivePlaybackFrame. Without this, all null
   * entries share sortKey=MAX_SAFE_INTEGER (all ≤ Infinity), causing the scan
   * to overshoot to the last null entry rather than the targeted one.
   */
  const exactNullTsIndex = useMemo(() => {
    if (normalized.length === 0 || mode !== "time") return undefined;
    const clamped = Math.min(Math.max(playheadIndex, 0), normalized.length - 1);
    return normalized[clamped].timestampMs === null ? clamped : undefined;
  }, [normalized, mode, playheadIndex]);

  /** Full playback frame */
  const frame = useMemo(
    () => derivePlaybackFrame(entries, cursor, mode, exactNullTsIndex),
    [entries, cursor, mode, exactNullTsIndex]
  );

  /** Time-range for waterfall bar layout */
  const timeRange = useMemo(() => {
    if (normalized.length === 0) return { firstMs: 0, totalMs: 1, indexMode: true };
    if (mode === "time") {
      const firstMs = normalized[0].timestampMs ?? 0;
      const lastMs = normalized[normalized.length - 1].timestampMs ?? normalized.length - 1;
      const totalMs = Math.max(1, lastMs - firstMs);
      return { firstMs, totalMs, indexMode: false };
    }
    return { firstMs: 0, totalMs: Math.max(1, normalized.length - 1), indexMode: true };
  }, [normalized, mode]);

  /** Overview bins for the scrubber strip */
  const overviewBins: PlaybackBin[] = useMemo(() => {
    if (normalized.length === 0) return [];
    if (mode === "time" && normalized[0].timestampMs !== null) {
      const rangeMs = timeRange.totalMs;
      const binWidth = Math.max(1, Math.ceil(rangeMs / OVERVIEW_BINS));
      return binEntriesByTime(normalized, binWidth);
    }
    const binSize = Math.max(1, Math.ceil(normalized.length / OVERVIEW_BINS));
    return binEntriesByCount(normalized, binSize);
  }, [normalized, mode, timeRange]);

  /** Windowed entries per lane (render limit) */
  const visibleNormalized = useMemo(() => normalized.slice(0, RENDER_LIMIT), [normalized]);
  const isWindowed = normalized.length > RENDER_LIMIT;

  /** Entries grouped by lane */
  const laneEntries = useMemo(
    () =>
      Object.fromEntries(
        PLAYBACK_LANE_ORDER.map((lane) => [lane, filterByLane(visibleNormalized, lane)])
      ) as Record<PlaybackLaneId, ReturnType<typeof filterByLane>>,
    [visibleNormalized]
  );

  /** Active span entry idx set */
  const activeSpanIdxSet = useMemo(
    () => new Set(frame.activeSpans.map((s) => s.entryIdx)),
    [frame.activeSpans]
  );

  // ── Auto-play interval ────────────────────────────────────────────────────
  useEffect(() => {
    if (!isPlaying || normalized.length <= 1 || reducedMotion) return;
    const intervalMs = BASE_INTERVAL_MS / speed;
    const timer = window.setInterval(() => {
      setPlayheadIndex((prev) => {
        if (prev >= normalized.length - 1) {
          setIsPlaying(false);
          return prev;
        }
        return prev + 1;
      });
    }, intervalMs);
    return () => window.clearInterval(timer);
  }, [isPlaying, speed, normalized.length, reducedMotion]);

  // ── Seek helpers ──────────────────────────────────────────────────────────
  const seekToSortedIndex = useCallback(
    (idx: number) => {
      setPlayheadIndex(Math.min(Math.max(idx, 0), normalized.length - 1));
      setIsPlaying(false);
    },
    [normalized.length]
  );

  const seekToPrevMarker = useCallback(() => {
    if (markers.length === 0) return;
    const newIdx = prevMarkerIndex(markers, frame.currentMarkerIndex);
    seekToSortedIndex(markers[newIdx]?.sortedIndex ?? 0);
  }, [markers, frame.currentMarkerIndex, seekToSortedIndex]);

  const seekToNextMarker = useCallback(() => {
    if (markers.length === 0) return;
    const newIdx = nextMarkerIndex(markers, frame.currentMarkerIndex);
    seekToSortedIndex(markers[newIdx]?.sortedIndex ?? 0);
  }, [markers, frame.currentMarkerIndex, seekToSortedIndex]);

  const handlePlayPause = useCallback(() => {
    if (reducedMotion) {
      // Reduced motion: step one event forward per press
      setPlayheadIndex((prev) => Math.min(prev + 1, normalized.length - 1));
    } else {
      setIsPlaying((prev) => !prev);
    }
  }, [reducedMotion, normalized.length]);

  // ── Stable ref for keyboard handler ──────────────────────────────────────
  const stateRef = useRef({
    playheadIndex,
    normalized,
    markers,
    frame,
    reducedMotion,
  });
  useLayoutEffect(() => {
    stateRef.current = { playheadIndex, normalized, markers, frame, reducedMotion };
  });

  // Stable setters (these never change identity)
  const setPlayheadIndexRef = useRef(setPlayheadIndex);
  const setIsPlayingRef = useRef(setIsPlaying);
  const setShowKeyHelpRef = useRef(setShowKeyHelp);
  useLayoutEffect(() => {
    setPlayheadIndexRef.current = setPlayheadIndex;
    setIsPlayingRef.current = setIsPlaying;
    setShowKeyHelpRef.current = setShowKeyHelp;
  });

  useEffect(() => {
    if (!active) return;
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.defaultPrevented || isTypingTarget(e.target)) return;
      const { normalized: norm, markers: mkrs, frame: fr, reducedMotion: rm } = stateRef.current;
      const n = norm.length;
      if (n === 0) return;

      const setIdx = setPlayheadIndexRef.current;
      const setPlay = setIsPlayingRef.current;
      const setHelp = setShowKeyHelpRef.current;

      switch (e.key) {
        case " ":
          e.preventDefault();
          if (rm) {
            setIdx((prev) => Math.min(prev + 1, n - 1));
          } else {
            setPlay((prev) => !prev);
          }
          return;
        case ",":
          e.preventDefault();
          setIdx((prev) => Math.max(prev - 1, 0));
          setPlay(false);
          return;
        case ".":
          e.preventDefault();
          setIdx((prev) => Math.min(prev + 1, n - 1));
          setPlay(false);
          return;
        case "ArrowLeft":
          e.preventDefault();
          setPlay(false);
          if (e.shiftKey) {
            setIdx((prev) => Math.max(0, Math.round(prev - Math.max(1, Math.round(n * 0.1)))));
          } else {
            setIdx((prev) => Math.max(prev - 1, 0));
          }
          return;
        case "ArrowRight":
          e.preventDefault();
          setPlay(false);
          if (e.shiftKey) {
            setIdx((prev) => Math.min(n - 1, Math.round(prev + Math.max(1, Math.round(n * 0.1)))));
          } else {
            setIdx((prev) => Math.min(prev + 1, n - 1));
          }
          return;
        case "[":
          e.preventDefault();
          setPlay(false);
          if (mkrs.length > 0) {
            const mi = prevMarkerIndex(mkrs, fr.currentMarkerIndex);
            setIdx(mkrs[mi]?.sortedIndex ?? 0);
          }
          return;
        case "]":
          e.preventDefault();
          setPlay(false);
          if (mkrs.length > 0) {
            const mi = nextMarkerIndex(mkrs, fr.currentMarkerIndex);
            setIdx(mkrs[mi]?.sortedIndex ?? 0);
          }
          return;
        case "Home":
          e.preventDefault();
          setIdx(0);
          setPlay(false);
          return;
        case "End":
          e.preventDefault();
          setIdx(n - 1);
          setPlay(false);
          return;
        case "?":
          e.preventDefault();
          setHelp((prev) => !prev);
          return;
        default:
          if (e.key >= "0" && e.key <= "9") {
            e.preventDefault();
            const pct = parseInt(e.key, 10) / 10;
            setIdx(Math.round((n - 1) * pct));
            setPlay(false);
          }
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [active]);

  // ── Bar position helpers ──────────────────────────────────────────────────
  const getBarLeft = useCallback(
    (sortKey: number): string => {
      const pct = ((sortKey - timeRange.firstMs) / timeRange.totalMs) * 100;
      return `${Math.max(0, Math.min(99, pct)).toFixed(3)}%`;
    },
    [timeRange]
  );

  const getBarWidth = useCallback(
    (durationMs: number | null): string => {
      if (durationMs === null || durationMs <= 0) return "3px";
      if (timeRange.indexMode) return "3px";
      const pct = (durationMs / timeRange.totalMs) * 100;
      // Minimum 0.3% for small durations (or 3px as inline min-width)
      return `${Math.max(0.3, Math.min(100, pct)).toFixed(3)}%`;
    },
    [timeRange]
  );

  const playheadLeftPct = useMemo(() => {
    if (normalized.length === 0) return "0%";
    const clamped = Math.min(Math.max(playheadIndex, 0), normalized.length - 1);
    const key = normalized[clamped].sortKey;
    const pct = ((key - timeRange.firstMs) / timeRange.totalMs) * 100;
    return `${Math.max(0, Math.min(100, pct)).toFixed(3)}%`;
  }, [normalized, playheadIndex, timeRange]);

  // ── Render ────────────────────────────────────────────────────────────────
  const n = normalized.length;
  const currentEvent = frame.currentEvent;

  return (
    <div className="space-y-3 font-mono text-xs" data-testid="timeline-player">
      {/* ── Control bar ──────────────────────────────────────────────────── */}
      <div className="border-border bg-card flex flex-wrap items-center gap-2 rounded-lg border px-3 py-2">
        {/* Play/Pause */}
        <Button
          variant="outline"
          size="xs"
          onClick={handlePlayPause}
          aria-label={reducedMotion ? "Step forward" : isPlaying ? "Pause" : "Play"}
          data-testid="btn-play-pause"
        >
          {!reducedMotion && isPlaying ? (
            <Pause className="size-3" aria-hidden />
          ) : (
            <Play className="size-3" aria-hidden />
          )}
          <span className="sr-only">{reducedMotion ? "Step" : isPlaying ? "Pause" : "Play"}</span>
        </Button>

        {/* Speed */}
        {!reducedMotion && (
          <select
            aria-label="Playback speed"
            value={speed}
            onChange={(e) => setSpeed(Number(e.target.value) as Speed)}
            className="border-input bg-background h-6 rounded border px-1 text-xs"
            data-testid="speed-select"
          >
            {SPEEDS.map((s) => (
              <option key={s} value={s}>
                {s}×
              </option>
            ))}
          </select>
        )}

        {/* Step prev/next */}
        <Button
          variant="ghost"
          size="icon-xs"
          onClick={() => {
            setPlayheadIndex((prev) => Math.max(prev - 1, 0));
            setIsPlaying(false);
          }}
          aria-label="Step backward"
          data-testid="btn-step-prev"
        >
          <SkipBack className="size-3" aria-hidden />
        </Button>
        <Button
          variant="ghost"
          size="icon-xs"
          onClick={() => {
            setPlayheadIndex((prev) => Math.min(prev + 1, n - 1));
            setIsPlaying(false);
          }}
          aria-label="Step forward"
          data-testid="btn-step-next"
        >
          <SkipForward className="size-3" aria-hidden />
        </Button>

        {/* Marker prev/next */}
        {markers.length > 0 && (
          <>
            <Button
              variant="ghost"
              size="icon-xs"
              onClick={seekToPrevMarker}
              aria-label="Previous marker"
              data-testid="btn-marker-prev"
            >
              <ChevronLeft className="size-3" aria-hidden />
            </Button>
            <Button
              variant="ghost"
              size="icon-xs"
              onClick={seekToNextMarker}
              aria-label="Next marker"
              data-testid="btn-marker-next"
            >
              <ChevronRight className="size-3" aria-hidden />
            </Button>
            <span className="text-muted-foreground flex items-center gap-1">
              <Bookmark className="size-3" aria-hidden />
              <span>{markers.length}</span>
            </span>
          </>
        )}

        {/* Event count */}
        <span className="text-muted-foreground ml-1">
          {n > 0 ? `${playheadIndex + 1} / ${n}` : "0 events"}
        </span>

        {/* Elapsed / mode */}
        {frame.elapsedMs !== null && (
          <span className="text-muted-foreground flex items-center gap-1">
            <Clock className="size-3" aria-hidden />
            {formatElapsed(frame.elapsedMs)}
          </span>
        )}
        {mode === "index" && <span className="text-muted-foreground">[idx mode]</span>}

        {/* HasMore hint */}
        {hasMore && (
          <span className="text-amber-500/80" title={`${total} total events; showing first ${n}`}>
            +{total - n} more
          </span>
        )}
        {isWindowed && (
          <span className="text-muted-foreground/60" title="Rendering limit applied">
            <Layers className="inline size-3" aria-hidden /> window
          </span>
        )}

        {/* Key help toggle */}
        <Button
          variant="ghost"
          size="icon-xs"
          onClick={() => setShowKeyHelp((prev) => !prev)}
          aria-label="Keyboard shortcuts"
          aria-expanded={showKeyHelp}
          data-testid="btn-key-help"
          className="ml-auto"
        >
          <HelpCircle className="size-3" aria-hidden />
        </Button>
      </div>

      {/* ── Keyboard help ────────────────────────────────────────────────── */}
      {showKeyHelp && (
        <div
          className="border-border bg-muted/30 rounded-lg border p-3 text-xs"
          role="region"
          aria-label="Keyboard shortcuts"
          data-testid="key-help"
        >
          <div className="grid grid-cols-2 gap-x-6 gap-y-1 sm:grid-cols-3">
            {[
              ["Space", "Play / Pause"],
              [",  /  .", "Step ← →"],
              ["← →", "Step (Shift = ±10%)"],
              ["[  /  ]", "Prev / next marker"],
              ["Home / End", "Go to start / end"],
              ["0–9", "Seek to 0%–90%"],
              ["?", "Toggle this help"],
            ].map(([key, desc]) => (
              <span key={key} className="flex gap-2">
                <kbd className="text-foreground/70 font-sans">{key}</kbd>
                <span className="text-muted-foreground">{desc}</span>
              </span>
            ))}
          </div>
        </div>
      )}

      {/* ── Scrubber ─────────────────────────────────────────────────────── */}
      <div className="relative px-0.5" data-testid="scrubber-wrap">
        {/* Overview strip (bins) */}
        {overviewBins.length > 0 && (
          <div
            className="mb-1 flex h-4 w-full overflow-hidden rounded-sm"
            aria-hidden="true"
            data-testid="overview-strip"
          >
            {overviewBins.map((bin, i) => (
              <div
                key={i}
                className={cn(
                  "h-full flex-1",
                  bin.errorCount > 0 ? "bg-red-700/70" : "bg-border/60"
                )}
                title={`${bin.count} events${bin.errorCount > 0 ? `, ${bin.errorCount} errors` : ""}`}
              />
            ))}
          </div>
        )}

        {/* Marker ticks overlay */}
        {markers.length > 0 && (
          <div className="pointer-events-none absolute inset-x-0.5 top-0 h-4">
            {markers.map((m) => {
              const pct = n > 1 ? `${((m.sortedIndex / (n - 1)) * 100).toFixed(3)}%` : "0%";
              return (
                <button
                  key={m.entryIdx}
                  className={cn(
                    "pointer-events-auto absolute top-0 h-4 w-1 -translate-x-0.5 rounded-sm opacity-80 hover:opacity-100",
                    MARKER_COLORS[m.kind]
                  )}
                  style={{ left: pct }}
                  aria-label={`Marker: ${m.kind} at event ${m.entryIdx}`}
                  title={`${m.kind}${m.timestamp ? ` — ${formatTimestamp(m.timestamp)}` : ""}`}
                  onClick={() => {
                    setPlayheadIndex(m.sortedIndex);
                    setIsPlaying(false);
                  }}
                />
              );
            })}
          </div>
        )}

        {/* Range scrubber */}
        <input
          type="range"
          role="slider"
          aria-label="Timeline playhead"
          aria-valuemin={0}
          aria-valuemax={Math.max(n - 1, 0)}
          aria-valuenow={playheadIndex}
          aria-valuetext={`Event ${playheadIndex + 1} of ${n}`}
          min={0}
          max={Math.max(n - 1, 0)}
          value={playheadIndex}
          className="w-full cursor-pointer"
          data-testid="scrubber"
          onChange={(e) => {
            setPlayheadIndex(parseInt(e.target.value, 10));
            setIsPlaying(false);
          }}
        />
      </div>

      {/* ── Waterfall lanes ──────────────────────────────────────────────── */}
      <div
        className="border-border overflow-hidden rounded-lg border"
        data-testid="waterfall"
        aria-label="Timeline waterfall"
      >
        {/* Playhead label row */}
        <div className="border-border flex items-center justify-between border-b px-3 py-1.5 text-xs">
          <span className="text-muted-foreground">
            {mode === "time" ? "Time axis" : "Index axis"}
          </span>
          {isWindowed && (
            <span className="text-muted-foreground/60">
              Showing first {RENDER_LIMIT} of {n} events
            </span>
          )}
        </div>

        {PLAYBACK_LANE_ORDER.map((lane) => {
          const laneItems = laneEntries[lane];
          if (laneItems.length === 0) return null;
          const style = LANE_STYLES[lane];

          return (
            <div
              key={lane}
              className={cn("border-border border-b last:border-b-0", style.rowBg)}
              data-testid={`lane-row-${lane}`}
            >
              {/* Lane label */}
              <div className="flex items-center gap-1.5 px-3 py-1">
                <span
                  className={cn(
                    "text-[10px] font-semibold tracking-wider uppercase",
                    style.labelCls
                  )}
                >
                  {style.label}
                </span>
                <span className="text-muted-foreground/50">{laneItems.length}</span>
              </div>

              {/* Bar track */}
              <div className="relative mx-3 mb-2 h-5 overflow-visible">
                {/* Playhead vertical line */}
                <div
                  className="bg-foreground/50 pointer-events-none absolute top-0 z-10 h-full w-px"
                  style={{ left: playheadLeftPct }}
                />

                {laneItems.map((norm) => {
                  const entry = norm.entry;
                  const sortedIdx = normalized.indexOf(norm);
                  const isAfterPlayhead = sortedIdx > playheadIndex;
                  const isCurrent = entry.idx === currentEvent?.idx;
                  const isActiveSpan = activeSpanIdxSet.has(entry.idx);
                  const isError = entry.kind === "error" || entry.status === "error";

                  return (
                    <button
                      key={entry.idx}
                      className={cn(
                        "absolute top-0.5 h-4 rounded-sm transition-opacity",
                        isAfterPlayhead
                          ? cn(style.inactiveBar, "opacity-25")
                          : isActiveSpan
                            ? "bg-white/80 opacity-100"
                            : isError
                              ? "bg-red-500 opacity-90"
                              : cn(style.activeBar, "opacity-90"),
                        isCurrent && "ring-1 ring-white ring-offset-1 ring-offset-transparent",
                        "min-w-[3px]"
                      )}
                      style={{
                        left: getBarLeft(norm.sortKey),
                        width: getBarWidth(entry.duration_ms),
                      }}
                      title={`${entry.kind}${entry.status ? ` · ${entry.status}` : ""}${entry.duration_ms !== null ? ` · ${entry.duration_ms}ms` : ""}`}
                      aria-label={`Event ${entry.idx}: ${entry.kind}`}
                      onClick={() => {
                        // Find the sorted index for this entry
                        const targetSortedIdx = normalized.findIndex(
                          (ne) => ne.entry.idx === entry.idx
                        );
                        if (targetSortedIdx >= 0) {
                          setPlayheadIndex(targetSortedIdx);
                          setIsPlaying(false);
                        }
                      }}
                    />
                  );
                })}
              </div>
            </div>
          );
        })}
      </div>

      {/* ── Current event card ───────────────────────────────────────────── */}
      {currentEvent && (
        <div
          className="border-border bg-card rounded-lg border p-3"
          data-testid="event-card"
          aria-live="polite"
          aria-label="Current event details"
        >
          <div className="mb-2 flex items-center gap-2">
            <span className="text-foreground font-semibold">Event {currentEvent.idx}</span>
            <span
              className={cn(
                "rounded px-1.5 py-0.5 text-[10px] font-semibold tracking-wider uppercase",
                currentEvent.kind === "error" || currentEvent.status === "error"
                  ? "bg-red-500/20 text-red-400"
                  : "bg-muted text-muted-foreground"
              )}
            >
              {currentEvent.kind}
            </span>
            {currentEvent.status && (
              <span
                className={cn(
                  "rounded px-1.5 py-0.5 text-[10px]",
                  currentEvent.status === "error"
                    ? "bg-red-500/20 text-red-400"
                    : currentEvent.status === "ok"
                      ? "bg-emerald-500/20 text-emerald-400"
                      : "bg-muted text-muted-foreground"
                )}
              >
                {currentEvent.status}
              </span>
            )}
            <span
              className={cn(
                "text-[10px] tracking-wider uppercase",
                LANE_STYLES[derivePlaybackLane(currentEvent)].labelCls
              )}
            >
              {LANE_STYLES[derivePlaybackLane(currentEvent)].label}
            </span>
            {onOpenDebugLog && (
              <Button
                variant="outline"
                size="xs"
                className="ml-auto"
                data-testid="btn-open-debug-log"
                onClick={() => onOpenDebugLog(currentEvent.idx)}
                title={`Open event ${currentEvent.idx} in Debug Log`}
              >
                Open in Debug Log
              </Button>
            )}
          </div>

          <div className="text-muted-foreground grid grid-cols-2 gap-x-4 gap-y-0.5 sm:grid-cols-3">
            <span>
              <span className="text-foreground/50">timestamp</span>{" "}
              {formatTimestamp(currentEvent.timestamp)}
            </span>
            <span>
              <span className="text-foreground/50">duration</span>{" "}
              {currentEvent.duration_ms !== null ? `${currentEvent.duration_ms}ms` : "—"}
            </span>
            <span>
              <span className="text-foreground/50">span</span> {currentEvent.span_id ?? "—"}
            </span>
            <span>
              <span className="text-foreground/50">parent</span>{" "}
              {currentEvent.parent_span_id ?? "—"}
            </span>
            {frame.elapsedMs !== null && (
              <span>
                <span className="text-foreground/50">elapsed</span> {formatElapsed(frame.elapsedMs)}
              </span>
            )}
          </div>
        </div>
      )}

      {/* ── Active spans summary ─────────────────────────────────────────── */}
      {frame.activeSpans.length > 0 && (
        <div
          className="text-muted-foreground flex flex-wrap gap-2 text-[10px]"
          data-testid="active-spans"
        >
          {frame.activeSpans.map((span) => (
            <span
              key={`${span.spanId ?? span.entryIdx}`}
              className={cn(
                "inline-flex items-center gap-1 rounded px-1.5 py-0.5",
                "bg-muted/60",
                span.isZeroWidth && "opacity-60"
              )}
            >
              <span className={cn("size-1.5 rounded-full", LANE_STYLES[span.lane].activeBar)} />
              {span.kind}
              {span.durationMs !== null && ` ${span.durationMs}ms`}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}
