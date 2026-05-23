"use client";

import { useState } from "react";
import { ChevronDown, ChevronRight, Copy, Check, Filter } from "lucide-react";

import { Banner } from "@/components/data/banner";
import { EmptyState } from "@/components/data/empty-state";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { useDebugLog } from "@/lib/api/hooks";
import type { BrowseDebugEntry, DebugLogParams, HostProfile } from "@/lib/api/types";
import { deriveSpanTree, type SpanTreeNode } from "@/lib/debug-span-tree";

// ── Constants ────────────────────────────────────────────────────────────────

/** All event kinds from the taxonomy. */
const DEBUG_KINDS = [
  "session_start",
  "turn_start",
  "llm_request",
  "tool_call",
  "hook",
  "subagent",
  "agent_response",
  "error",
  "generic",
  "raw",
] as const;

const DEBUG_LEVELS = ["debug", "info", "warn", "error"] as const;
const DEBUG_STATUSES = ["ok", "error", "cancelled"] as const;

/** Number of events per page for server-side pagination. */
const PAGE_SIZE = 100;

// ── Types ────────────────────────────────────────────────────────────────────

export type DebugLogTabProps = {
  sessionId: string;
  /** Run ID to show debug log for. null/empty = show unavailable state. */
  runId: string | null;
  /** True while the parent is resolving the latest operator run for this session. */
  runsLoading?: boolean;
  /**
   * True when the backend signals an operator session exists for this id.
   * When false (default), the tab shows a knowledge-only empty state without
   * issuing any network request to /api/operator/sessions/{id}/runs.
   */
  hasOperatorRuns?: boolean;
  host: HostProfile;
};

type FilterState = {
  text: string;
  kind: string;
  level: string;
  status: string;
};

// ── Helpers ──────────────────────────────────────────────────────────────────

function formatDuration(ms: number | null): string {
  if (ms === null) return "";
  if (ms < 1000) return `${ms}ms`;
  return `${(ms / 1000).toFixed(2)}s`;
}

function formatTimestamp(ts: string | null): string {
  if (!ts) return "—";
  try {
    return new Date(ts).toLocaleTimeString(undefined, {
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      // fractionalSecondDigits is a valid option but TS lib may not include it
      // in all targets; cast to avoid lint noise.
    });
  } catch {
    return ts;
  }
}

/** Returns a colour class for the level badge. */
function levelClass(level: string | null): string {
  switch (level) {
    case "error":
      return "text-red-600 dark:text-red-400";
    case "warn":
      return "text-yellow-600 dark:text-yellow-400";
    case "info":
      return "text-blue-600 dark:text-blue-400";
    case "debug":
      return "text-gray-500 dark:text-gray-400";
    default:
      return "text-muted-foreground";
  }
}

/** Returns a colour class for the status badge. */
function statusClass(status: string | null): string {
  switch (status) {
    case "ok":
      return "text-green-600 dark:text-green-400";
    case "error":
      return "text-red-600 dark:text-red-400";
    case "cancelled":
      return "text-yellow-600 dark:text-yellow-400";
    default:
      return "text-muted-foreground";
  }
}

/**
 * Case-insensitive literal substring match — no regex, safe for arbitrary user input.
 * Uses toLowerCase().includes() rather than compiling a regex from user text.
 */
function textMatches(entry: BrowseDebugEntry, needle: string): boolean {
  if (!needle) return true;
  const lower = needle.toLowerCase();
  return (
    entry.message.toLowerCase().includes(lower) ||
    entry.source.toLowerCase().includes(lower) ||
    (entry.tool_name?.toLowerCase().includes(lower) ?? false)
  );
}

/** AND-composition of all active filters. */
function applyFilters(events: BrowseDebugEntry[], filters: FilterState): BrowseDebugEntry[] {
  return events.filter((entry) => {
    if (!textMatches(entry, filters.text)) return false;
    if (filters.kind && entry.kind !== filters.kind) return false;
    if (filters.level && entry.level !== filters.level) return false;
    if (filters.status && entry.status !== filters.status) return false;
    return true;
  });
}

// ── Detail Drawer ────────────────────────────────────────────────────────────

type DetailDrawerProps = {
  entry: BrowseDebugEntry;
  onClose: () => void;
};

function DetailDrawer({ entry, onClose }: DetailDrawerProps) {
  const [copied, setCopied] = useState(false);

  /**
   * Build a copy payload from the (server-redacted) entry.
   * Only content that has already been processed by the server's WBS-102
   * redaction pass is included — the `redacted` boolean signals whether any
   * field was modified by that pass.
   */
  const copyPayload = JSON.stringify(entry, null, 2);

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(copyPayload);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      // Clipboard API may be blocked; fail silently.
    }
  };

  const attrsJson = entry.attrs ? JSON.stringify(entry.attrs, null, 2) : null;

  return (
    <div
      id={`detail-${entry.idx}`}
      className="border-border bg-card fixed inset-y-0 right-0 z-30 flex w-full max-w-md flex-col overflow-y-auto border-l shadow-xl sm:w-[420px]"
      role="dialog"
      aria-label="Debug event detail"
    >
      {/* Header */}
      <div className="border-border flex items-center justify-between border-b px-4 py-3">
        <h3 className="text-sm font-semibold">Event Detail</h3>
        <div className="flex items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            onClick={() => void handleCopy()}
            title="Copy redacted event JSON to clipboard"
          >
            {copied ? (
              <>
                <Check className="size-3.5" />
                Copied
              </>
            ) : (
              <>
                <Copy className="size-3.5" />
                Copy
              </>
            )}
          </Button>
          <Button variant="outline" size="sm" onClick={onClose} aria-label="Close detail panel">
            ✕
          </Button>
        </div>
      </div>

      {/* Fields */}
      <div className="space-y-3 p-4 text-sm">
        <DetailField label="idx" value={String(entry.idx)} />
        <DetailField label="timestamp" value={entry.timestamp ?? "—"} />
        <DetailField label="kind" value={entry.kind} />
        <DetailField label="level" value={entry.level ?? "—"} className={levelClass(entry.level)} />
        <DetailField label="source" value={entry.source} />
        <DetailField
          label="status"
          value={entry.status ?? "—"}
          className={statusClass(entry.status)}
        />
        {entry.tool_name ? <DetailField label="tool_name" value={entry.tool_name} /> : null}
        {entry.duration_ms !== null ? (
          <DetailField label="duration_ms" value={String(entry.duration_ms)} />
        ) : null}
        {entry.span_id ? <DetailField label="span_id" value={entry.span_id} mono /> : null}
        {entry.parent_span_id ? (
          <DetailField label="parent_span_id" value={entry.parent_span_id} mono />
        ) : null}
        <DetailField label="redacted" value={entry.redacted ? "yes" : "no"} />

        {/* Message — full text, not truncated, rendered as text (no dangerouslySetInnerHTML) */}
        <div>
          <p className="text-muted-foreground mb-1 text-xs font-medium tracking-wide uppercase">
            message
          </p>
          <p className="break-words whitespace-pre-wrap">{entry.message}</p>
        </div>

        {/* Attrs — pretty-printed, rendered as text inside a <pre> */}
        {attrsJson ? (
          <div>
            <p className="text-muted-foreground mb-1 text-xs font-medium tracking-wide uppercase">
              attrs
            </p>
            <pre className="bg-muted text-muted-foreground overflow-x-auto rounded-md p-2 text-xs break-words whitespace-pre-wrap">
              {attrsJson}
            </pre>
          </div>
        ) : null}
      </div>
    </div>
  );
}

type DetailFieldProps = {
  label: string;
  value: string;
  className?: string;
  mono?: boolean;
};

function DetailField({ label, value, className, mono }: DetailFieldProps) {
  return (
    <div className="flex flex-col gap-0.5">
      <p className="text-muted-foreground text-xs font-medium tracking-wide uppercase">{label}</p>
      <p className={`break-all ${mono ? "font-mono text-xs" : ""} ${className ?? ""}`}>{value}</p>
    </div>
  );
}

// ── Filter Toolbar ────────────────────────────────────────────────────────────

type FilterToolbarProps = {
  filters: FilterState;
  onChange: (next: FilterState) => void;
  resultCount: number;
  totalCount: number;
};

function FilterToolbar({ filters, onChange, resultCount, totalCount }: FilterToolbarProps) {
  return (
    <div className="border-border flex flex-wrap items-center gap-2 rounded-xl border p-3">
      <Filter className="text-muted-foreground size-4 shrink-0" aria-hidden />

      {/* Text search — uses substring match, never compiled as regex */}
      <Input
        className="h-8 max-w-xs min-w-[160px] flex-1"
        placeholder="Search message, source, tool…"
        value={filters.text}
        onChange={(e) => onChange({ ...filters, text: e.target.value })}
        aria-label="Filter by text"
      />

      {/* Kind filter */}
      <Select
        value={filters.kind || "_all"}
        onValueChange={(v) => onChange({ ...filters, kind: !v || v === "_all" ? "" : v })}
      >
        <SelectTrigger className="h-8 w-[130px]" aria-label="Filter by kind">
          <SelectValue placeholder="Kind" />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="_all">All kinds</SelectItem>
          {DEBUG_KINDS.map((k) => (
            <SelectItem key={k} value={k}>
              {k}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>

      {/* Level filter */}
      <Select
        value={filters.level || "_all"}
        onValueChange={(v) => onChange({ ...filters, level: !v || v === "_all" ? "" : v })}
      >
        <SelectTrigger className="h-8 w-[110px]" aria-label="Filter by level">
          <SelectValue placeholder="Level" />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="_all">All levels</SelectItem>
          {DEBUG_LEVELS.map((l) => (
            <SelectItem key={l} value={l}>
              {l}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>

      {/* Status filter */}
      <Select
        value={filters.status || "_all"}
        onValueChange={(v) => onChange({ ...filters, status: !v || v === "_all" ? "" : v })}
      >
        <SelectTrigger className="h-8 w-[120px]" aria-label="Filter by status">
          <SelectValue placeholder="Status" />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="_all">All statuses</SelectItem>
          {DEBUG_STATUSES.map((s) => (
            <SelectItem key={s} value={s}>
              {s}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>

      {/* Result count */}
      <p className="text-muted-foreground ml-auto shrink-0 text-xs">
        {resultCount} of {totalCount}
      </p>
    </div>
  );
}

// ── Event Row ─────────────────────────────────────────────────────────────────

type EventRowProps = {
  entry: BrowseDebugEntry;
  isSelected: boolean;
  onSelect: (entry: BrowseDebugEntry) => void;
};

function EventRow({ entry, isSelected, onSelect }: EventRowProps) {
  const handleKeyDown = (e: React.KeyboardEvent<HTMLTableRowElement>) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      onSelect(entry);
    }
  };

  return (
    <tr
      className={`hover:bg-muted/50 cursor-pointer border-b text-xs transition-colors ${
        isSelected ? "bg-muted/70" : ""
      }`}
      onClick={() => onSelect(entry)}
      onKeyDown={handleKeyDown}
      role="row"
      tabIndex={0}
      aria-expanded={isSelected}
      aria-controls={`detail-${entry.idx}`}
      aria-label={`Debug event ${entry.idx}: ${entry.kind} from ${entry.source}`}
    >
      {/* Expand indicator */}
      <td className="text-muted-foreground w-4 px-1 py-2">
        {isSelected ? (
          <ChevronDown className="size-3" aria-hidden />
        ) : (
          <ChevronRight className="size-3" aria-hidden />
        )}
      </td>

      {/* Timestamp */}
      <td className="text-muted-foreground px-2 py-2 font-mono whitespace-nowrap">
        {formatTimestamp(entry.timestamp)}
      </td>

      {/* Kind */}
      <td className="px-2 py-2">
        <span className="border-border rounded border px-1 py-0.5 font-mono text-[10px]">
          {entry.kind}
        </span>
      </td>

      {/* Source */}
      <td className="text-muted-foreground max-w-[120px] truncate px-2 py-2">{entry.source}</td>

      {/* Tool name */}
      <td className="max-w-[100px] truncate px-2 py-2">{entry.tool_name ?? ""}</td>

      {/* Duration */}
      <td className="text-muted-foreground px-2 py-2 text-right font-mono whitespace-nowrap">
        {formatDuration(entry.duration_ms)}
      </td>

      {/* Status */}
      <td className={`px-2 py-2 whitespace-nowrap ${statusClass(entry.status)}`}>
        {entry.status ?? ""}
      </td>

      {/* Message preview — rendered as text, never as HTML */}
      <td className="max-w-[280px] truncate px-2 py-2">
        <span className={entry.redacted ? "italic" : ""}>{entry.message}</span>
        {entry.redacted ? (
          <span className="text-muted-foreground ml-1 text-[10px]">[redacted]</span>
        ) : null}
      </td>
    </tr>
  );
}

// ── Span Tree View ─────────────────────────────────────────────────────────────

type SpanTreeViewProps = {
  entries: BrowseDebugEntry[];
  selectedEntry: BrowseDebugEntry | null;
  onSelect: (entry: BrowseDebugEntry) => void;
};

/**
 * Collapsible span tree derived from `entries`.
 * - Click row or press Enter/Space to expand/collapse children and open detail.
 * - ArrowRight expands; ArrowLeft collapses.
 * - Error nodes get a red/orange background; orphan nodes get a dashed border.
 * - Duration rollup badge shown when parent has no own duration but children do.
 */
function SpanTreeView({ entries, selectedEntry, onSelect }: SpanTreeViewProps) {
  const [expandedKeys, setExpandedKeys] = useState<Set<string>>(() => new Set());

  const roots = deriveSpanTree(entries);

  function toggleKey(key: string): void {
    setExpandedKeys((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }

  function expandKey(key: string): void {
    setExpandedKeys((prev) => new Set([...prev, key]));
  }

  function collapseKey(key: string): void {
    setExpandedKeys((prev) => {
      const next = new Set(prev);
      next.delete(key);
      return next;
    });
  }

  function renderNode(node: SpanTreeNode) {
    const key = node.entry.idx === -1 ? "synthetic-orphan" : String(node.entry.idx);
    const hasChildren = node.children.length > 0;
    const isExpanded = expandedKeys.has(key);
    const isSelected = selectedEntry?.idx === node.entry.idx;
    const isSynthetic = node.entry.idx === -1;
    const isError = node.entry.status === "error";
    const childrenId = `span-children-${key}`;

    const rowClasses = [
      "flex items-center gap-2 rounded px-2 py-1.5 text-xs cursor-pointer transition-colors outline-none focus-visible:ring-2 focus-visible:ring-ring",
      isError
        ? "bg-red-50 dark:bg-red-950/40 hover:bg-red-100 dark:hover:bg-red-900/60"
        : isSelected
          ? "bg-muted/70"
          : "hover:bg-muted/50",
      node.isOrphan ? "border border-dashed border-border my-0.5" : "",
      isSynthetic ? "text-muted-foreground italic" : "",
    ]
      .filter(Boolean)
      .join(" ");

    return (
      <div key={key}>
        <div
          role="treeitem"
          aria-expanded={hasChildren ? isExpanded : undefined}
          aria-controls={hasChildren && isExpanded ? childrenId : undefined}
          aria-selected={isSelected}
          aria-label={
            isSynthetic
              ? "orphans group"
              : `Span event ${node.entry.idx}: ${node.entry.kind} from ${node.entry.source}`
          }
          tabIndex={0}
          className={rowClasses}
          style={{ paddingLeft: `${node.depth * 16 + 8}px` }}
          onClick={() => {
            if (hasChildren) toggleKey(key);
            if (!isSynthetic) onSelect(node.entry);
          }}
          onKeyDown={(e) => {
            if (e.key === "Enter" || e.key === " ") {
              e.preventDefault();
              if (hasChildren) toggleKey(key);
              if (!isSynthetic) onSelect(node.entry);
            } else if (e.key === "ArrowRight") {
              e.preventDefault();
              if (hasChildren && !isExpanded) expandKey(key);
            } else if (e.key === "ArrowLeft") {
              e.preventDefault();
              if (isExpanded) collapseKey(key);
            }
          }}
        >
          {/* Expand indicator */}
          <span className="w-3 shrink-0">
            {hasChildren ? (
              isExpanded ? (
                <ChevronDown className="size-3" aria-hidden />
              ) : (
                <ChevronRight className="size-3" aria-hidden />
              )
            ) : null}
          </span>

          {/* Kind badge */}
          <span className="border-border shrink-0 rounded border px-1 py-0.5 font-mono text-[10px]">
            {node.entry.kind}
          </span>

          {/* Source */}
          <span className="text-muted-foreground max-w-[100px] shrink-0 truncate">
            {node.entry.source}
          </span>

          {/* Tool name */}
          {node.entry.tool_name ? (
            <span className="shrink-0 font-mono text-[10px]">{node.entry.tool_name}</span>
          ) : null}

          {/* Message */}
          <span className="min-w-0 flex-1 truncate">
            <span className={node.entry.redacted ? "italic" : ""}>{node.entry.message}</span>
            {node.entry.redacted ? (
              <span className="text-muted-foreground ml-1 text-[10px]">[redacted]</span>
            ) : null}
          </span>

          {/* Duration: rollup badge or own duration */}
          {node.rollupDurationMs !== null ? (
            <span className="bg-muted text-muted-foreground shrink-0 rounded px-1 py-0.5 font-mono text-[10px]">
              {formatDuration(node.rollupDurationMs)} (∑)
            </span>
          ) : node.entry.duration_ms !== null ? (
            <span className="text-muted-foreground shrink-0 font-mono text-[10px]">
              {formatDuration(node.entry.duration_ms)}
            </span>
          ) : null}

          {/* Status */}
          {node.entry.status ? (
            <span className={`shrink-0 ${statusClass(node.entry.status)}`}>
              {node.entry.status}
            </span>
          ) : null}
        </div>

        {hasChildren && isExpanded ? (
          <div id={childrenId} role="group">
            {node.children.map((child) => renderNode(child))}
          </div>
        ) : null}
      </div>
    );
  }

  if (roots.length === 0) {
    return (
      <div className="text-muted-foreground py-8 text-center text-sm">
        No events match the current filters.
      </div>
    );
  }

  return (
    <div className="border-border rounded-xl border p-2" role="tree" aria-label="Debug span tree">
      {roots.map((root) => renderNode(root))}
    </div>
  );
}

// ── Main Component ────────────────────────────────────────────────────────────

/**
 * Debug Log tab — shows debug log entries for an operator session run.
 *
 * Follows the same pattern as timeline-tab.tsx:
 * 1. useQuery → loading / error / empty / success states
 * 2. Filter toolbar (text, kind, level, status — AND composition, no regex)
 * 3. Event list table with expand-on-click row detail
 * 4. Detail drawer rendered as text (no dangerouslySetInnerHTML)
 *
 * Server-side pagination: fetches PAGE_SIZE events at a time.
 * For 1000+ events, the user pages through via Previous / Next controls.
 *
 * When runId is null or empty the tab shows an informational empty state.
 */
export function DebugLogTab({ sessionId, runId, runsLoading = false, hasOperatorRuns = false, host }: DebugLogTabProps) {
  const [filters, setFilters] = useState<FilterState>({
    text: "",
    kind: "",
    level: "",
    status: "",
  });
  const [page, setPage] = useState(0);
  const [selectedEntry, setSelectedEntry] = useState<BrowseDebugEntry | null>(null);
  const [viewMode, setViewMode] = useState<"list" | "tree">("list");

  const isEnabled = Boolean(sessionId) && Boolean(runId);

  // Server-side pagination: fetch PAGE_SIZE events at a time.
  const fetchParams: DebugLogParams = {
    from: page * PAGE_SIZE,
    limit: PAGE_SIZE,
  };

  const query = useDebugLog(sessionId, runId ?? "", fetchParams, isEnabled, host);

  // Apply client-side filters (text, kind, level, status) — AND composition.
  const filteredEvents = query.data?.events ? applyFilters(query.data.events, filters) : [];

  // Show tree toggle only when any raw event has a span_id.
  const hasSpanIds = Boolean(query.data?.events.some((e) => e.span_id !== null));

  const handleSelect = (entry: BrowseDebugEntry) => {
    setSelectedEntry((prev) => (prev?.idx === entry.idx ? null : entry));
  };

  const handleFilterChange = (next: FilterState) => {
    setFilters(next);
    // Reset detail drawer when filters change to avoid stale context.
    setSelectedEntry(null);
  };

  // ── Knowledge-only session (no operator backing store) ────────────────────
  if (!hasOperatorRuns) {
    return (
      <EmptyState
        title="No operator runs"
        description="This session has no operator runs. Debug Log is only available for sessions launched through the operator console."
      />
    );
  }

  // ── Operator run lookup loading ────────────────────────────────────────────
  if (runsLoading) {
    return (
      <div className="border-border text-muted-foreground rounded-xl border p-4 text-sm">
        Loading debug log…
      </div>
    );
  }

  // ── No run available ───────────────────────────────────────────────────────
  if (!runId) {
    return (
      <EmptyState
        title="No run available"
        description="No operator run was found for this session. Debug log entries are recorded per run."
      />
    );
  }

  // ── Loading state ──────────────────────────────────────────────────────────
  if (query.isLoading) {
    return (
      <div className="border-border text-muted-foreground rounded-xl border p-4 text-sm">
        Loading debug log…
      </div>
    );
  }

  // ── Error state ────────────────────────────────────────────────────────────
  if (query.error) {
    return (
      <Banner
        tone="danger"
        title="Failed to load debug log"
        description={query.error instanceof Error ? query.error.message : "Unknown error"}
      />
    );
  }

  // ── Empty state ────────────────────────────────────────────────────────────
  if (!query.data || query.data.events.length === 0) {
    return (
      <EmptyState
        title="No debug log entries"
        description="No debug log events were recorded for this session run."
      />
    );
  }

  const { total, has_more } = query.data;

  return (
    <div className="space-y-3">
      {/* Filter toolbar */}
      <FilterToolbar
        filters={filters}
        onChange={handleFilterChange}
        resultCount={filteredEvents.length}
        totalCount={query.data.events.length}
      />

      {/* View mode toggle — only shown when the dataset has span_ids */}
      {hasSpanIds && (
        <div className="flex items-center gap-1">
          <button
            type="button"
            className={`rounded px-2 py-1 text-xs transition-colors ${
              viewMode === "list"
                ? "bg-primary text-primary-foreground"
                : "text-muted-foreground hover:text-foreground"
            }`}
            aria-pressed={viewMode === "list"}
            onClick={() => setViewMode("list")}
          >
            List
          </button>
          <button
            type="button"
            className={`rounded px-2 py-1 text-xs transition-colors ${
              viewMode === "tree"
                ? "bg-primary text-primary-foreground"
                : "text-muted-foreground hover:text-foreground"
            }`}
            aria-pressed={viewMode === "tree"}
            onClick={() => setViewMode("tree")}
          >
            Tree
          </button>
        </div>
      )}

      {/* Tree view */}
      {viewMode === "tree" ? (
        <SpanTreeView
          entries={filteredEvents}
          selectedEntry={selectedEntry}
          onSelect={handleSelect}
        />
      ) : (
        /* Event table */
        <div className="border-border overflow-x-auto rounded-xl border">
          <table className="w-full text-sm" role="grid" aria-label="Debug log events">
            <thead>
              <tr className="border-border bg-muted/40 border-b">
                <th className="w-4 px-1 py-2" aria-label="Expand" />
                <th className="text-muted-foreground px-2 py-2 text-left text-xs font-medium whitespace-nowrap">
                  Time
                </th>
                <th className="text-muted-foreground px-2 py-2 text-left text-xs font-medium whitespace-nowrap">
                  Kind
                </th>
                <th className="text-muted-foreground px-2 py-2 text-left text-xs font-medium whitespace-nowrap">
                  Source
                </th>
                <th className="text-muted-foreground px-2 py-2 text-left text-xs font-medium whitespace-nowrap">
                  Tool
                </th>
                <th className="text-muted-foreground px-2 py-2 text-right text-xs font-medium whitespace-nowrap">
                  Duration
                </th>
                <th className="text-muted-foreground px-2 py-2 text-left text-xs font-medium whitespace-nowrap">
                  Status
                </th>
                <th className="text-muted-foreground px-2 py-2 text-left text-xs font-medium">
                  Message
                </th>
              </tr>
            </thead>
            <tbody>
              {filteredEvents.length === 0 ? (
                <tr>
                  <td colSpan={8} className="text-muted-foreground py-8 text-center text-sm">
                    No events match the current filters.
                  </td>
                </tr>
              ) : (
                filteredEvents.map((entry) => (
                  <EventRow
                    key={entry.idx}
                    entry={entry}
                    isSelected={selectedEntry?.idx === entry.idx}
                    onSelect={handleSelect}
                  />
                ))
              )}
            </tbody>
          </table>
        </div>
      )}

      {/* Server-side pagination controls */}
      {(page > 0 || has_more) && (
        <div className="flex items-center justify-between">
          <p className="text-muted-foreground text-xs">
            Showing {page * PAGE_SIZE + 1}–{page * PAGE_SIZE + query.data.events.length} of {total}{" "}
            total events
          </p>
          <div className="flex gap-2">
            {page > 0 && (
              <Button
                variant="outline"
                size="sm"
                onClick={() => {
                  setPage((p) => Math.max(0, p - 1));
                  setSelectedEntry(null);
                }}
              >
                Previous
              </Button>
            )}
            {has_more && (
              <Button
                variant="outline"
                size="sm"
                onClick={() => {
                  setPage((p) => p + 1);
                  setSelectedEntry(null);
                }}
              >
                Next
              </Button>
            )}
          </div>
        </div>
      )}

      {/* Detail drawer — slides in from the right, rendered as text */}
      {selectedEntry ? (
        <DetailDrawer entry={selectedEntry} onClose={() => setSelectedEntry(null)} />
      ) : null}
    </div>
  );
}
