"use client";

import type { ColumnDef } from "@tanstack/react-table";
import { ArrowLeft, ArrowRight, Search, ScrollText, Slash } from "lucide-react";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";

import { Banner } from "@/components/data/banner";
import { DataTable } from "@/components/data/data-table";
import { EmptyState } from "@/components/data/empty-state";
import {
  IDBadge,
  normalizeSource,
  SourceBadge,
  TimeRelative,
} from "@/components/data/session-badges";
import { FilterSidebar } from "@/components/layout/filter-sidebar";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { PAGE_SIZES, SEARCH_DEBOUNCE_MS, SOURCE_LABELS } from "@/lib/constants";
import { useSessions } from "@/lib/api/hooks";
import type { SessionRow } from "@/lib/api/types";
import { formatNumber } from "@/lib/formatters";
import { useKeyboardShortcuts } from "@/hooks/use-keyboard-shortcuts";

type TimeRange = "all" | "today" | "7d" | "30d";
type SummaryFilter = "all" | "yes" | "no";
type SortMode = "recent" | "oldest" | "events_desc" | "events_asc" | "summary_asc" | "summary_desc";

const SOURCE_ORDER = [...Object.keys(SOURCE_LABELS), "unknown"];

function getSessionTimestamp(row: SessionRow): number | null {
  const raw = row.fts_indexed_at ?? row.indexed_at_r;
  if (!raw) return null;
  const timestamp = new Date(raw).getTime();
  return Number.isNaN(timestamp) ? null : timestamp;
}

function inTimeRange(timestamp: number | null, timeRange: TimeRange, nowMs: number): boolean {
  if (timeRange === "all") return true;
  if (!timestamp) return false;

  const dayMs = 24 * 60 * 60 * 1000;
  if (timeRange === "today") return nowMs - timestamp <= dayMs;
  if (timeRange === "7d") return nowMs - timestamp <= 7 * dayMs;
  return nowMs - timestamp <= 30 * dayMs;
}

export default function SessionsPage() {
  const router = useRouter();
  const searchInputId = "sessions-sidebar-search";

  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState<number>(PAGE_SIZES[0]);
  const [queryInput, setQueryInput] = useState("");
  const [query, setQuery] = useState("");
  const [selectedSources, setSelectedSources] = useState<string[]>([]);
  const [timeRange, setTimeRange] = useState<TimeRange>("all");
  const [summaryFilter, setSummaryFilter] = useState<SummaryFilter>("all");
  const [sortMode, setSortMode] = useState<SortMode>("recent");
  const [focusedIndex, setFocusedIndex] = useState(0);

  const sessionsQuery = useSessions({
    page,
    pageSize,
    query,
  });

  const items = useMemo(() => sessionsQuery.data?.items ?? [], [sessionsQuery.data]);
  const nowMsForTimeFilter = useMemo(() => {
    switch (timeRange) {
      case "all":
      case "today":
      case "7d":
      case "30d":
        return Date.now();
    }
  }, [timeRange]);

  const sourceCounts = useMemo(() => {
    const counts: Record<string, number> = {};
    for (const session of items) {
      const source = normalizeSource(session.source);
      counts[source] = (counts[source] ?? 0) + 1;
    }
    return counts;
  }, [items]);

  const sourceOptions = useMemo(() => {
    const available = new Set<string>([...Object.keys(sourceCounts), ...selectedSources]);
    const ordered = SOURCE_ORDER.filter((source) => available.has(source));
    const extras = [...available].filter((source) => !SOURCE_ORDER.includes(source)).sort();
    return [...ordered, ...extras];
  }, [sourceCounts, selectedSources]);

  const filteredAndSortedItems = useMemo(() => {
    const next = items
      .filter((session) => {
        const source = normalizeSource(session.source);
        if (selectedSources.length > 0 && !selectedSources.includes(source)) return false;

        const hasSummary = Boolean(session.summary && session.summary.trim());
        if (summaryFilter === "yes" && !hasSummary) return false;
        if (summaryFilter === "no" && hasSummary) return false;

        return inTimeRange(getSessionTimestamp(session), timeRange, nowMsForTimeFilter);
      })
      .slice();

    next.sort((a, b) => {
      if (sortMode === "recent") {
        return (getSessionTimestamp(b) ?? 0) - (getSessionTimestamp(a) ?? 0);
      }
      if (sortMode === "oldest") {
        return (getSessionTimestamp(a) ?? 0) - (getSessionTimestamp(b) ?? 0);
      }
      if (sortMode === "events_desc") {
        return (b.event_count_estimate ?? -1) - (a.event_count_estimate ?? -1);
      }
      if (sortMode === "events_asc") {
        return (a.event_count_estimate ?? -1) - (b.event_count_estimate ?? -1);
      }
      if (sortMode === "summary_asc") {
        return (a.summary ?? "").localeCompare(b.summary ?? "");
      }
      return (b.summary ?? "").localeCompare(a.summary ?? "");
    });

    return next;
  }, [items, nowMsForTimeFilter, selectedSources, summaryFilter, timeRange, sortMode]);

  const focusedRow = filteredAndSortedItems[focusedIndex] ?? null;
  const focusedRowId = focusedRow?.id ?? null;

  useEffect(() => {
    const timer = window.setTimeout(() => {
      setQuery(queryInput.trim());
      setPage(1);
      setFocusedIndex(0);
    }, SEARCH_DEBOUNCE_MS);

    return () => window.clearTimeout(timer);
  }, [queryInput]);

  useEffect(() => {
    if (focusedIndex >= filteredAndSortedItems.length) {
      setFocusedIndex(Math.max(0, filteredAndSortedItems.length - 1));
    }
  }, [filteredAndSortedItems.length, focusedIndex]);

  const total = sessionsQuery.data?.total ?? 0;
  const totalPages = Math.max(1, Math.ceil(total / pageSize));

  useEffect(() => {
    if (page > totalPages) {
      setPage(totalPages);
    }
  }, [page, totalPages]);

  const openSession = useCallback(
    (session: SessionRow) => {
      router.push(`/sessions/${encodeURIComponent(session.id)}`);
    },
    [router]
  );

  const keyboardShortcuts = useMemo(
    () => [
      {
        key: "/",
        preventDefault: true,
        handler: () => {
          const searchInput = document.getElementById(searchInputId) as HTMLInputElement | null;
          searchInput?.focus();
          searchInput?.select();
        },
      },
      {
        key: "j",
        preventDefault: true,
        handler: () => {
          setFocusedIndex((prev) =>
            Math.min(prev + 1, Math.max(0, filteredAndSortedItems.length - 1))
          );
        },
      },
      {
        key: "ArrowDown",
        preventDefault: true,
        handler: () => {
          setFocusedIndex((prev) =>
            Math.min(prev + 1, Math.max(0, filteredAndSortedItems.length - 1))
          );
        },
      },
      {
        key: "k",
        preventDefault: true,
        handler: () => {
          setFocusedIndex((prev) => Math.max(prev - 1, 0));
        },
      },
      {
        key: "ArrowUp",
        preventDefault: true,
        handler: () => {
          setFocusedIndex((prev) => Math.max(prev - 1, 0));
        },
      },
      {
        key: "Enter",
        preventDefault: true,
        handler: () => {
          if (!focusedRow) return false;
          openSession(focusedRow);
          return true;
        },
      },
    ],
    [filteredAndSortedItems.length, focusedRow, openSession, searchInputId]
  );

  useKeyboardShortcuts(keyboardShortcuts, { enabled: true });

  const clearAllFilters = () => {
    setQueryInput("");
    setQuery("");
    setSelectedSources([]);
    setTimeRange("all");
    setSummaryFilter("all");
    setSortMode("recent");
    setPage(1);
    setFocusedIndex(0);
  };

  const columns = useMemo<ColumnDef<SessionRow>[]>(
    () => [
      {
        id: "id",
        header: "ID",
        accessorKey: "id",
        cell: ({ row }) => {
          const isFocused = row.original.id === focusedRowId;
          return (
            <div className="flex items-center gap-2">
              <span aria-hidden className={isFocused ? "text-primary" : "text-transparent"}>
                ▸
              </span>
              <IDBadge id={row.original.id} />
            </div>
          );
        },
      },
      {
        id: "summary",
        header: "Summary",
        accessorFn: (row) => row.summary ?? "",
        cell: ({ row }) => (
          <span className="text-foreground/95 line-clamp-1 text-sm">
            {row.original.summary?.trim() || "No summary"}
          </span>
        ),
      },
      {
        id: "source",
        header: "Source",
        accessorFn: (row) => normalizeSource(row.source),
        cell: ({ row }) => <SourceBadge source={row.original.source} />,
      },
      {
        id: "events",
        header: "Events",
        accessorFn: (row) => row.event_count_estimate ?? -1,
        cell: ({ row }) => formatNumber(row.original.event_count_estimate),
      },
      {
        id: "time",
        header: "Time",
        accessorFn: (row) => getSessionTimestamp(row) ?? 0,
        cell: ({ row }) => (
          <TimeRelative value={row.original.fts_indexed_at ?? row.original.indexed_at_r} />
        ),
      },
    ],
    [focusedRowId]
  );

  const canGoPrev = page > 1;
  const canGoNext = Boolean(sessionsQuery.data?.has_more);
  const showPagination = total > pageSize;
  const loadedCount = items.length;
  const loadedStart = loadedCount === 0 ? 0 : (page - 1) * pageSize + 1;
  const loadedEnd = loadedCount === 0 ? 0 : loadedStart + loadedCount - 1;
  const hasClientSideFilters =
    selectedSources.length > 0 || timeRange !== "all" || summaryFilter !== "all";

  const pageButtons = useMemo(() => {
    const pages = new Set<number>([1, totalPages, page - 1, page, page + 1]);
    return [...pages].filter((value) => value >= 1 && value <= totalPages).sort((a, b) => a - b);
  }, [page, totalPages]);

  return (
    <div className="space-y-4">
      <div className="space-y-1">
        <h1 className="text-2xl font-semibold tracking-tight">Sessions</h1>
        <p className="text-muted-foreground text-sm">
          Scan recent sessions, refine by filters, and open details quickly.
        </p>
      </div>

      <div className="flex gap-4">
        <FilterSidebar
          className="hidden lg:block"
          sections={[
            {
              id: "search",
              title: "Search",
              content: (
                <div className="space-y-2">
                  <div className="relative">
                    <Search className="text-muted-foreground pointer-events-none absolute top-1/2 left-2 size-3.5 -translate-y-1/2" />
                    <Input
                      id={searchInputId}
                      value={queryInput}
                      onChange={(event) => setQueryInput(event.target.value)}
                      placeholder="Filter by summary or ID"
                      className="pl-7"
                    />
                  </div>
                  <p className="text-muted-foreground text-xs">
                    <Slash className="mr-1 inline size-3" />
                    Press <kbd className="bg-muted rounded px-1 py-0.5 text-[10px]">/</kbd> to focus
                  </p>
                </div>
              ),
            },
            {
              id: "source",
              title: "Source",
              content: (
                <div className="space-y-2">
                  {sourceOptions.length === 0 ? (
                    <p className="text-muted-foreground text-xs">No source values on this page.</p>
                  ) : (
                    sourceOptions.map((source) => {
                      const checked = selectedSources.includes(source);
                      const count = sourceCounts[source] ?? 0;
                      const label =
                        source === "unknown"
                          ? "Unknown"
                          : SOURCE_LABELS[source as keyof typeof SOURCE_LABELS];
                      return (
                        <label key={source} className="flex items-center gap-2 text-sm">
                          <Checkbox
                            checked={checked}
                            onCheckedChange={(value) => {
                              const nextChecked = value === true;
                              setSelectedSources((prev) => {
                                if (nextChecked) {
                                  return prev.includes(source) ? prev : [...prev, source];
                                }
                                return prev.filter((entry) => entry !== source);
                              });
                              setFocusedIndex(0);
                            }}
                          />
                          <span>{label}</span>
                          <span className="text-muted-foreground text-xs">
                            ({formatNumber(count)})
                          </span>
                        </label>
                      );
                    })
                  )}
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    className="px-0 text-xs"
                    onClick={() => {
                      setSelectedSources([]);
                      setFocusedIndex(0);
                    }}
                  >
                    Show all sources
                  </Button>
                </div>
              ),
            },
            {
              id: "time",
              title: "Time range",
              content: (
                <Select
                  value={timeRange}
                  onValueChange={(value) => {
                    setTimeRange(value as TimeRange);
                    setFocusedIndex(0);
                  }}
                >
                  <SelectTrigger className="w-full">
                    <SelectValue placeholder="All" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="all">All</SelectItem>
                    <SelectItem value="today">Today</SelectItem>
                    <SelectItem value="7d">Last 7 days</SelectItem>
                    <SelectItem value="30d">Last 30 days</SelectItem>
                  </SelectContent>
                </Select>
              ),
            },
            {
              id: "summary",
              title: "Has summary",
              content: (
                <div className="grid grid-cols-3 gap-1">
                  {(
                    [
                      ["all", "All"],
                      ["yes", "Yes"],
                      ["no", "No"],
                    ] as const
                  ).map(([value, label]) => (
                    <Button
                      key={value}
                      type="button"
                      variant={summaryFilter === value ? "secondary" : "outline"}
                      size="sm"
                      onClick={() => {
                        setSummaryFilter(value);
                        setFocusedIndex(0);
                      }}
                    >
                      {label}
                    </Button>
                  ))}
                </div>
              ),
            },
            {
              id: "sort",
              title: "Sort",
              content: (
                <Select
                  value={sortMode}
                  onValueChange={(value) => {
                    setSortMode(value as SortMode);
                    setFocusedIndex(0);
                  }}
                >
                  <SelectTrigger className="w-full">
                    <SelectValue placeholder="Most recent" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="recent">Most recent</SelectItem>
                    <SelectItem value="oldest">Oldest first</SelectItem>
                    <SelectItem value="events_desc">Most events</SelectItem>
                    <SelectItem value="events_asc">Fewest events</SelectItem>
                    <SelectItem value="summary_asc">Summary A-Z</SelectItem>
                    <SelectItem value="summary_desc">Summary Z-A</SelectItem>
                  </SelectContent>
                </Select>
              ),
            },
          ]}
        />

        <div className="min-w-0 flex-1 space-y-3">
          {sessionsQuery.isError ? (
            <Banner
              tone="danger"
              title="Failed to load sessions"
              description="Check that the browse server is running, then retry."
              actions={
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  onClick={() => sessionsQuery.refetch()}
                >
                  Retry
                </Button>
              }
            />
          ) : null}

          {!sessionsQuery.isError && sessionsQuery.isFetching && !sessionsQuery.isLoading ? (
            <Banner
              tone="info"
              title="Refreshing sessions"
              description="Updating this page with the latest indexed data."
            />
          ) : null}

          {!sessionsQuery.isError &&
          !sessionsQuery.isLoading &&
          filteredAndSortedItems.length === 0 ? (
            <EmptyState
              icon={<ScrollText className="size-5" />}
              title="No sessions found"
              description="Try adjusting filters or run build-session-index.py to add session data."
              actionLabel="Clear filters"
              onAction={clearAllFilters}
            />
          ) : (
            <DataTable
              columns={columns}
              data={filteredAndSortedItems}
              isLoading={sessionsQuery.isLoading}
              emptyTitle="No sessions found"
              emptyDescription="Try adjusting your filters or run build-session-index.py."
              onRowClick={openSession}
            />
          )}

          <div className="bg-card flex flex-col gap-3 rounded-xl border px-3 py-2 sm:flex-row sm:items-center sm:justify-between">
            <p className="text-muted-foreground text-sm">
              {hasClientSideFilters ? (
                <>
                  Showing {formatNumber(filteredAndSortedItems.length)} of{" "}
                  {formatNumber(loadedCount)} loaded on this page ({formatNumber(total)} total
                  sessions)
                </>
              ) : null}
              {!hasClientSideFilters ? (
                <>
                  Showing {loadedStart}-{loadedEnd} of {formatNumber(total)} sessions
                </>
              ) : null}
              {hasClientSideFilters ? (
                <span className="ml-1">(client-side filters apply to this loaded page)</span>
              ) : null}
            </p>

            <div className="flex flex-wrap items-center gap-2">
              <Select
                value={String(pageSize)}
                onValueChange={(value) => {
                  setPageSize(Number(value));
                  setPage(1);
                }}
              >
                <SelectTrigger className="w-[90px]">
                  <SelectValue placeholder={String(PAGE_SIZES[0])} />
                </SelectTrigger>
                <SelectContent>
                  {PAGE_SIZES.map((size) => (
                    <SelectItem key={size} value={String(size)}>
                      {size}/page
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>

              {showPagination ? (
                <div className="flex items-center gap-1">
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    disabled={!canGoPrev}
                    onClick={() => setPage((prev) => Math.max(1, prev - 1))}
                  >
                    <ArrowLeft className="size-3.5" />
                    Prev
                  </Button>

                  {pageButtons.map((pageNumber) => (
                    <Button
                      key={pageNumber}
                      type="button"
                      size="sm"
                      variant={pageNumber === page ? "secondary" : "outline"}
                      onClick={() => setPage(pageNumber)}
                    >
                      {pageNumber}
                    </Button>
                  ))}

                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    disabled={!canGoNext}
                    onClick={() => setPage((prev) => prev + 1)}
                  >
                    Next
                    <ArrowRight className="size-3.5" />
                  </Button>
                </div>
              ) : null}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
