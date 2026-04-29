"use client";

import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Loader2, Search } from "lucide-react";

import { Banner } from "@/components/data/banner";
import { SourceBadge, TimeRelative } from "@/components/data/session-badges";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { ScrollArea } from "@/components/ui/scroll-area";
import { apiFetch } from "@/lib/api/client";
import { sessionListResponseSchema } from "@/lib/api/schemas";
import type { SessionRow } from "@/lib/api/types";
import { formatSessionIdBadgeText } from "@/lib/formatters";
import { cn } from "@/lib/utils";

const COMPARE_RECENT_KEY = "browse-ui-recent-compare-session-ids";
const SEARCH_DEBOUNCE_MS = 250;
const PAGE_SIZE = 20;
const RECENT_LIMIT = 8;

function readRecentSessionIds(storage: Storage | null): string[] {
  if (!storage) return [];
  try {
    const raw = storage.getItem(COMPARE_RECENT_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed
      .filter((value): value is string => typeof value === "string" && value.trim().length > 0)
      .slice(0, RECENT_LIMIT);
  } catch {
    return [];
  }
}

function writeRecentSessionIds(storage: Storage | null, ids: string[]) {
  if (!storage) return;
  storage.setItem(COMPARE_RECENT_KEY, JSON.stringify(ids.slice(0, RECENT_LIMIT)));
}

function toSessionsUrl(query = ""): string {
  const params = new URLSearchParams({
    page: "1",
    page_size: String(PAGE_SIZE),
  });
  const trimmed = query.trim();
  if (trimmed) params.set("q", trimmed);
  return `/api/sessions?${params.toString()}`;
}

function makePlaceholderSession(id: string): SessionRow {
  return {
    id,
    path: null,
    summary: null,
    source: null,
    event_count_estimate: null,
    fts_indexed_at: null,
    indexed_at_r: null,
  };
}

type SessionPickerProps = {
  currentSessionId: string;
  open: boolean;
  value: string;
  onValueChange: (sessionId: string) => void;
};

export function SessionPicker({
  currentSessionId,
  open,
  value,
  onValueChange,
}: SessionPickerProps) {
  const [query, setQuery] = useState("");
  const [debouncedQuery, setDebouncedQuery] = useState("");
  const [recentSessionIds, setRecentSessionIds] = useState<string[]>([]);

  useEffect(() => {
    const timeout = window.setTimeout(() => setDebouncedQuery(query.trim()), SEARCH_DEBOUNCE_MS);
    return () => window.clearTimeout(timeout);
  }, [query]);

  useEffect(() => {
    if (!open || typeof window === "undefined") return;
    setRecentSessionIds(readRecentSessionIds(window.localStorage));
  }, [open]);

  const recentQuery = useQuery({
    queryKey: ["session-picker", "recent", currentSessionId],
    enabled: open,
    queryFn: async () => {
      const data = await apiFetch(toSessionsUrl());
      return sessionListResponseSchema.parse(data);
    },
  });

  const searchQuery = useQuery({
    queryKey: ["session-picker", "search", currentSessionId, debouncedQuery],
    enabled: open && debouncedQuery.length > 0,
    queryFn: async () => {
      const data = await apiFetch(toSessionsUrl(debouncedQuery));
      return sessionListResponseSchema.parse(data);
    },
  });

  const activeQuery = debouncedQuery ? searchQuery : recentQuery;

  const allKnownCandidates = useMemo(() => {
    const map = new Map<string, SessionRow>();
    const recentPool = recentQuery.data?.items ?? [];
    const searchPool = searchQuery.data?.items ?? [];
    for (const candidate of [...recentPool, ...searchPool]) {
      if (candidate.id === currentSessionId) continue;
      map.set(candidate.id, candidate);
    }
    return map;
  }, [currentSessionId, recentQuery.data?.items, searchQuery.data?.items]);

  const listedCandidates = useMemo(
    () => (activeQuery.data?.items ?? []).filter((candidate) => candidate.id !== currentSessionId),
    [activeQuery.data?.items, currentSessionId]
  );

  const recentCandidates = useMemo(
    () =>
      recentSessionIds
        .filter((id) => id !== currentSessionId)
        .map((id) => allKnownCandidates.get(id) ?? makePlaceholderSession(id)),
    [allKnownCandidates, currentSessionId, recentSessionIds]
  );

  useEffect(() => {
    if (!open || value) return;
    const next = recentCandidates[0]?.id ?? listedCandidates[0]?.id;
    if (next) onValueChange(next);
  }, [listedCandidates, onValueChange, open, recentCandidates, value]);

  const rememberSessionId = (sessionId: string) => {
    if (sessionId === currentSessionId || typeof window === "undefined") return;
    const next = [sessionId, ...recentSessionIds.filter((id) => id !== sessionId)].slice(
      0,
      RECENT_LIMIT
    );
    setRecentSessionIds(next);
    writeRecentSessionIds(window.localStorage, next);
  };

  const handleSelect = (sessionId: string) => {
    onValueChange(sessionId);
    rememberSessionId(sessionId);
  };

  const renderSessionButton = (candidate: SessionRow, context: "recent" | "result") => {
    const isActive = candidate.id === value;
    return (
      <Button
        key={`${context}-${candidate.id}`}
        type="button"
        variant="ghost"
        onClick={() => handleSelect(candidate.id)}
        className={cn(
          "h-auto w-full items-start justify-start gap-2 rounded-md border px-3 py-2 text-left",
          isActive ? "border-primary/50 bg-primary/10 text-foreground" : "border-border/50"
        )}
      >
        <div className="min-w-0 flex-1 space-y-1">
          <p className="truncate text-sm font-medium">
            {formatSessionIdBadgeText(candidate.id)} — {candidate.summary?.trim() || "(no summary)"}
          </p>
          <div className="text-muted-foreground flex flex-wrap items-center gap-2 text-xs">
            <SourceBadge source={candidate.source} />
            <TimeRelative value={candidate.fts_indexed_at} />
          </div>
        </div>
      </Button>
    );
  };

  return (
    <div className="space-y-3">
      <label className="text-sm font-medium" htmlFor="compare-session-search">
        Compare with
      </label>
      <div className="relative">
        <Search className="text-muted-foreground pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2" />
        <Input
          id="compare-session-search"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          className="pl-8"
          placeholder="Search session id or summary..."
          autoComplete="off"
        />
      </div>

      {recentCandidates.length > 0 ? (
        <div className="space-y-2">
          <p className="text-muted-foreground text-xs font-medium tracking-wide uppercase">
            Recent
          </p>
          <div className="space-y-2">
            {recentCandidates.map((candidate) => renderSessionButton(candidate, "recent"))}
          </div>
        </div>
      ) : null}

      <div className="space-y-2">
        <p className="text-muted-foreground text-xs font-medium tracking-wide uppercase">
          {debouncedQuery ? "Search results" : "Latest sessions"}
        </p>
        {activeQuery.isLoading || activeQuery.isFetching ? (
          <div className="text-muted-foreground flex items-center gap-2 text-sm">
            <Loader2 className="size-4 animate-spin" />
            Loading sessions...
          </div>
        ) : null}
        {activeQuery.error ? (
          <Banner
            tone="danger"
            title="Failed to load sessions"
            description={
              activeQuery.error instanceof Error ? activeQuery.error.message : "Unknown error"
            }
          />
        ) : null}
        {!activeQuery.error ? (
          <ScrollArea className="border-border/50 max-h-72 rounded-lg border p-1">
            <div className="space-y-1">
              {listedCandidates.length > 0 ? (
                listedCandidates.map((candidate) => renderSessionButton(candidate, "result"))
              ) : (
                <p className="text-muted-foreground px-2 py-3 text-sm">
                  {debouncedQuery
                    ? "No matching sessions found."
                    : "No sessions available to compare."}
                </p>
              )}
            </div>
          </ScrollArea>
        ) : null}
      </div>
    </div>
  );
}
