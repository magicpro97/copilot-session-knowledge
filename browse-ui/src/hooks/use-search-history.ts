"use client";

import { useCallback, useEffect, useState } from "react";

const STORAGE_KEY = "browse-ui-recent-searches";
const MAX_ITEMS = 8;

function sanitizeValue(value: string): string {
  return value.trim().slice(0, 200);
}

export function useSearchHistory() {
  const [recentSearches, setRecentSearches] = useState<string[]>([]);

  useEffect(() => {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      if (!raw) return;
      const parsed = JSON.parse(raw);
      if (Array.isArray(parsed)) {
        setRecentSearches(
          parsed
            .filter((item): item is string => typeof item === "string")
            .map(sanitizeValue)
            .filter(Boolean)
            .slice(0, MAX_ITEMS)
        );
      }
    } catch {
      setRecentSearches([]);
    }
  }, []);

  const addSearch = useCallback((query: string) => {
    const sanitized = sanitizeValue(query);
    if (!sanitized) return;
    setRecentSearches((previous) => {
      const next = [sanitized, ...previous.filter((item) => item !== sanitized)].slice(
        0,
        MAX_ITEMS
      );
      localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
      return next;
    });
  }, []);

  const clearSearches = useCallback(() => {
    setRecentSearches([]);
    localStorage.removeItem(STORAGE_KEY);
  }, []);

  return {
    recentSearches,
    addSearch,
    clearSearches,
  };
}
