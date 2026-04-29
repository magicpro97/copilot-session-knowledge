"use client";

import { Filter, Loader2, Search, SearchX, ThumbsDown, ThumbsUp, X } from "lucide-react";
import { useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { Banner } from "@/components/data/banner";
import { EmptyState } from "@/components/data/empty-state";
import { SearchResultCard } from "@/components/data/search-result-card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { useDebounce } from "@/hooks/use-debounce";
import { useKeyboardShortcuts } from "@/hooks/use-keyboard-shortcuts";
import { useSearchHistory } from "@/hooks/use-search-history";
import { useSearch, useSubmitFeedback } from "@/lib/api/hooks";
import type { SearchResult } from "@/lib/api/types";
import { cn } from "@/lib/utils";

const ALL_SOURCES = ["sessions", "knowledge"] as const;
const ALL_COLS = ["user", "assistant", "tools", "title"] as const;
const ALL_KINDS = [
  "mistake",
  "pattern",
  "decision",
  "discovery",
  "tool",
  "feature",
  "refactor",
] as const;

type Verdict = -1 | 1;

function FeedbackRow({ result, query }: { result: SearchResult; query: string }) {
  const [submitted, setSubmitted] = useState<Verdict | null>(null);
  const feedback = useSubmitFeedback();

  const submit = useCallback(
    (verdict: Verdict) => {
      if (submitted !== null || feedback.isPending) return;
      feedback.mutate(
        {
          query,
          result_id: String(result.id),
          result_kind: result.type,
          verdict,
        },
        { onSuccess: () => setSubmitted(verdict) }
      );
    },
    [feedback, query, result.id, result.type, submitted]
  );

  return (
    <div
      className="flex items-center gap-1 px-1"
      onClick={(e) => e.stopPropagation()}
      onKeyDown={(e) => e.stopPropagation()}
    >
      {submitted !== null ? (
        <span className="text-muted-foreground text-xs">
          {submitted === 1 ? "👍 Thanks!" : "👎 Noted"}
        </span>
      ) : (
        <>
          <Button
            type="button"
            variant="ghost"
            size="icon-xs"
            aria-label="Helpful result"
            disabled={feedback.isPending}
            onClick={() => submit(1)}
          >
            <ThumbsUp className="size-3" />
          </Button>
          <Button
            type="button"
            variant="ghost"
            size="icon-xs"
            aria-label="Unhelpful result"
            disabled={feedback.isPending}
            onClick={() => submit(-1)}
          >
            <ThumbsDown className="size-3" />
          </Button>
        </>
      )}
    </div>
  );
}

function parseCsv<T extends readonly string[]>(value: string | null, valid: T): T[number][] {
  if (!value) return [];
  const validSet = new Set(valid);
  return value
    .split(",")
    .map((item) => item.trim())
    .filter((item): item is T[number] => validSet.has(item as T[number]));
}

function toggleValue<T extends string>(items: T[], value: T): T[] {
  return items.includes(value) ? items.filter((item) => item !== value) : [...items, value];
}

export default function SearchPage() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const inputRef = useRef<HTMLInputElement>(null);
  const resultRefs = useRef<Array<HTMLDivElement | null>>([]);

  const [query, setQuery] = useState(() => searchParams.get("q") ?? "");
  const [committedQuery, setCommittedQuery] = useState(() => searchParams.get("q")?.trim() ?? "");
  const [sources, setSources] = useState(() => parseCsv(searchParams.get("src"), ALL_SOURCES));
  const [columns, setColumns] = useState(() => parseCsv(searchParams.get("in"), ALL_COLS));
  const [kinds, setKinds] = useState(() => parseCsv(searchParams.get("kind"), ALL_KINDS));
  const [isFiltersOpen, setIsFiltersOpen] = useState(
    () =>
      parseCsv(searchParams.get("src"), ALL_SOURCES).length > 0 ||
      parseCsv(searchParams.get("in"), ALL_COLS).length > 0 ||
      parseCsv(searchParams.get("kind"), ALL_KINDS).length > 0
  );
  const [activeIndex, setActiveIndex] = useState(-1);

  const debouncedQuery = useDebounce(query.trim(), 300);
  const { recentSearches, addSearch, clearSearches } = useSearchHistory();

  useEffect(() => {
    setCommittedQuery(debouncedQuery);
  }, [debouncedQuery]);

  useEffect(() => {
    if (!committedQuery) return;
    addSearch(committedQuery);
  }, [addSearch, committedQuery]);

  useEffect(() => {
    const params = new URLSearchParams();
    const q = query.trim();
    if (q) params.set("q", q);
    if (sources.length > 0) params.set("src", sources.join(","));
    if (columns.length > 0) params.set("in", columns.join(","));
    if (kinds.length > 0) params.set("kind", kinds.join(","));
    const next = params.toString();
    const current = searchParams.toString();
    if (next !== current) {
      router.replace(next ? `/search?${next}` : "/search", { scroll: false });
    }
  }, [columns, kinds, query, router, searchParams, sources]);

  const search = useSearch(
    {
      query: committedQuery,
      sources,
      cols: columns,
      kinds,
    },
    committedQuery.length > 0
  );

  const results = useMemo(() => search.data?.results ?? [], [search.data?.results]);
  const isIdle = query.trim().length === 0;
  const showLoading = committedQuery.length > 0 && search.isFetching;
  const showError = committedQuery.length > 0 && search.isError;
  const showEmpty =
    committedQuery.length > 0 &&
    !showLoading &&
    !showError &&
    search.isSuccess &&
    results.length === 0;

  useEffect(() => {
    setActiveIndex(results.length > 0 ? 0 : -1);
  }, [results]);

  useEffect(() => {
    if (activeIndex < 0) return;
    resultRefs.current[activeIndex]?.scrollIntoView({
      block: "nearest",
      behavior: "smooth",
    });
  }, [activeIndex]);

  const openResult = useCallback(
    (index: number) => {
      const result = results[index];
      if (!result) return;
      if (result.type === "session" && typeof result.id === "string") {
        router.push(`/sessions/${encodeURIComponent(result.id)}`);
        return;
      }
      const nextKind = parseCsv(result.kind ?? null, ALL_KINDS);
      setQuery(result.title);
      setCommittedQuery(result.title.trim());
      setSources(["knowledge"]);
      setColumns([]);
      setKinds(nextKind);
    },
    [results, router]
  );

  useKeyboardShortcuts([
    {
      key: "j",
      preventDefault: true,
      handler: () => {
        if (!results.length) return false;
        setActiveIndex((prev) => (prev + 1) % results.length);
        return true;
      },
    },
    {
      key: "ArrowDown",
      preventDefault: true,
      handler: () => {
        if (!results.length) return false;
        setActiveIndex((prev) => (prev + 1) % results.length);
        return true;
      },
    },
    {
      key: "k",
      preventDefault: true,
      handler: () => {
        if (!results.length) return false;
        setActiveIndex((prev) => (prev <= 0 ? results.length - 1 : prev - 1));
        return true;
      },
    },
    {
      key: "ArrowUp",
      preventDefault: true,
      handler: () => {
        if (!results.length) return false;
        setActiveIndex((prev) => (prev <= 0 ? results.length - 1 : prev - 1));
        return true;
      },
    },
    {
      key: "Enter",
      preventDefault: true,
      handler: () => {
        if (activeIndex < 0) return false;
        openResult(activeIndex);
        return true;
      },
    },
  ]);

  return (
    <div className="space-y-6">
      <div className="space-y-2">
        <h1 className="text-2xl font-semibold">Search</h1>
        <p className="text-muted-foreground">Search across all sessions and knowledge entries.</p>
      </div>

      <div className="space-y-3">
        <div className="relative">
          <Search className="text-muted-foreground pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2" />
          <Input
            ref={inputRef}
            autoFocus
            type="search"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Escape") {
                event.preventDefault();
                setQuery("");
                setCommittedQuery("");
                inputRef.current?.focus();
              } else if (event.key === "Enter") {
                setCommittedQuery(query.trim());
              } else if (event.key === "ArrowDown" && results.length > 0) {
                event.preventDefault();
                setActiveIndex((prev) => (prev + 1) % results.length);
              } else if (event.key === "ArrowUp" && results.length > 0) {
                event.preventDefault();
                setActiveIndex((prev) => (prev <= 0 ? results.length - 1 : prev - 1));
              }
            }}
            placeholder="Search sessions + knowledge..."
            className="pr-16 pl-8"
            aria-label="Search sessions and knowledge"
          />
          <div className="absolute top-1/2 right-2 flex -translate-y-1/2 items-center gap-1">
            {showLoading ? <Loader2 className="text-muted-foreground size-4 animate-spin" /> : null}
            {query ? (
              <Button
                type="button"
                variant="ghost"
                size="icon-xs"
                aria-label="Clear search"
                onClick={() => {
                  setQuery("");
                  setCommittedQuery("");
                  inputRef.current?.focus();
                }}
              >
                <X className="size-3.5" />
              </Button>
            ) : null}
          </div>
        </div>

        {recentSearches.length > 0 ? (
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-muted-foreground text-xs">Recent:</span>
            {recentSearches.map((item) => (
              <Button
                key={item}
                type="button"
                variant="outline"
                size="xs"
                onClick={() => {
                  setQuery(item);
                  setCommittedQuery(item);
                  inputRef.current?.focus();
                }}
              >
                {item}
              </Button>
            ))}
            <Button type="button" variant="ghost" size="xs" onClick={clearSearches}>
              Clear
            </Button>
          </div>
        ) : null}

        <div className="border-border/70 bg-muted/20 space-y-3 rounded-xl border p-3">
          <Button
            type="button"
            variant="ghost"
            size="sm"
            className="w-full justify-start"
            onClick={() => setIsFiltersOpen((open) => !open)}
          >
            <Filter className="size-3.5" />
            Filters
          </Button>
          {isFiltersOpen ? (
            <div className="space-y-3">
              <div className="space-y-1.5">
                <p className="text-muted-foreground text-xs font-medium">Scope</p>
                <div className="flex flex-wrap gap-3">
                  {ALL_SOURCES.map((source) => (
                    <label key={source} className="flex cursor-pointer items-center gap-2 text-sm">
                      <Checkbox
                        checked={sources.includes(source)}
                        onCheckedChange={() => setSources((prev) => toggleValue(prev, source))}
                      />
                      <span className="capitalize">{source}</span>
                    </label>
                  ))}
                </div>
              </div>

              <div className="space-y-1.5">
                <p className="text-muted-foreground text-xs font-medium">In</p>
                <div className="flex flex-wrap gap-3">
                  {ALL_COLS.map((column) => (
                    <label key={column} className="flex cursor-pointer items-center gap-2 text-sm">
                      <Checkbox
                        checked={columns.includes(column)}
                        onCheckedChange={() => setColumns((prev) => toggleValue(prev, column))}
                      />
                      <span className="capitalize">{column}</span>
                    </label>
                  ))}
                </div>
              </div>

              <div className="space-y-1.5">
                <p className="text-muted-foreground text-xs font-medium">Kind</p>
                <div className="flex flex-wrap gap-3">
                  {ALL_KINDS.map((kind) => (
                    <label key={kind} className="flex cursor-pointer items-center gap-2 text-sm">
                      <Checkbox
                        checked={kinds.includes(kind)}
                        onCheckedChange={() => setKinds((prev) => toggleValue(prev, kind))}
                      />
                      <span className="capitalize">{kind}</span>
                    </label>
                  ))}
                </div>
              </div>
            </div>
          ) : null}
        </div>

        {showError ? (
          <Banner
            tone="danger"
            title="Search failed"
            description={
              search.error instanceof Error
                ? search.error.message
                : "Search failed — server may be restarting."
            }
          />
        ) : null}
      </div>

      <div className="space-y-3">
        {!isIdle && search.data ? (
          <p className="text-muted-foreground text-sm">
            {search.data.total} results · {search.data.took_ms}ms
          </p>
        ) : null}

        {isIdle ? (
          <EmptyState
            title="Start searching"
            description="Search across all sessions and knowledge entries."
          />
        ) : showLoading ? (
          <div className="grid gap-3">
            {Array.from({ length: 4 }).map((_, index) => (
              <div key={`search-skeleton-${index}`} className="rounded-xl border p-4">
                <Skeleton className="h-4 w-2/3" />
                <Skeleton className="mt-2 h-3 w-1/3" />
                <Skeleton className="mt-4 h-3 w-full" />
                <Skeleton className="mt-2 h-3 w-11/12" />
              </div>
            ))}
          </div>
        ) : showEmpty ? (
          <EmptyState
            icon={<SearchX className="size-5" />}
            title={`No results for "${committedQuery}"`}
            description="Try broader terms or check if sessions are indexed."
          />
        ) : (
          <div className="grid gap-3">
            {results.map((result, index) => (
              <div
                key={`${committedQuery}-${result.type}-${String(result.id)}-${index}`}
                ref={(node) => {
                  resultRefs.current[index] = node;
                }}
              >
                <SearchResultCard
                  result={result}
                  query={committedQuery}
                  className={cn(
                    activeIndex === index &&
                      "ring-primary/40 ring-offset-background ring-2 ring-offset-1"
                  )}
                  onSelect={() => openResult(index)}
                />
                <div className="mt-1 flex justify-end">
                  <FeedbackRow result={result} query={committedQuery} />
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      <div className="flex flex-wrap gap-2">
        <Badge variant="outline">J/K or ↑/↓ to move</Badge>
        <Badge variant="outline">Enter to open</Badge>
        <Badge variant="outline">Escape to clear</Badge>
      </div>
    </div>
  );
}
