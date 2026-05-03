"use client";

import { useEffect, useRef, useState } from "react";
import { FolderOpen } from "lucide-react";

import { cn } from "@/lib/utils";
import { useDebounce } from "@/hooks/use-debounce";
import { usePathSuggest } from "@/lib/api/hooks";

type WorkspacePickerProps = {
  value: string;
  onChange: (value: string) => void;
  id?: string;
  className?: string;
  disabled?: boolean;
};

/**
 * Text input with debounced path suggestions from `/api/operator/suggest`.
 * Suggestions are shown in a dropdown below the input.
 */
export function WorkspacePicker({
  value,
  onChange,
  id,
  className,
  disabled,
}: WorkspacePickerProps) {
  const [inputValue, setInputValue] = useState(value);
  const [open, setOpen] = useState(false);
  const [highlightedIndex, setHighlightedIndex] = useState(-1);
  const containerRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const debouncedQuery = useDebounce(inputValue, 200);
  const { data: suggestData } = usePathSuggest(debouncedQuery, true);
  const suggestions = suggestData?.suggestions ?? [];

  // Sync external value changes
  useEffect(() => {
    setInputValue(value);
  }, [value]);

  // Close dropdown on outside click
  useEffect(() => {
    function handleClickOutside(event: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(event.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  function handleInputChange(e: React.ChangeEvent<HTMLInputElement>) {
    const next = e.target.value;
    setInputValue(next);
    onChange(next);
    setOpen(true);
    setHighlightedIndex(-1);
  }

  function handleSelect(suggestion: string) {
    setInputValue(suggestion);
    onChange(suggestion);
    setOpen(false);
    inputRef.current?.focus();
  }

  function handleKeyDown(e: React.KeyboardEvent<HTMLInputElement>) {
    if (!open || suggestions.length === 0) {
      if (e.key === "ArrowDown") {
        setOpen(true);
      }
      return;
    }

    if (e.key === "ArrowDown") {
      e.preventDefault();
      setHighlightedIndex((prev) => (prev < suggestions.length - 1 ? prev + 1 : 0));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setHighlightedIndex((prev) => (prev > 0 ? prev - 1 : suggestions.length - 1));
    } else if (e.key === "Enter") {
      if (highlightedIndex >= 0 && suggestions[highlightedIndex]) {
        e.preventDefault();
        handleSelect(suggestions[highlightedIndex]);
      }
    } else if (e.key === "Escape") {
      setOpen(false);
      setHighlightedIndex(-1);
    }
  }

  return (
    <div ref={containerRef} className={cn("relative", className)}>
      <div className="relative">
        <FolderOpen className="text-muted-foreground absolute top-1/2 left-3 size-4 -translate-y-1/2" />
        <input
          ref={inputRef}
          id={id}
          type="text"
          value={inputValue}
          onChange={handleInputChange}
          onFocus={() => setOpen(true)}
          onKeyDown={handleKeyDown}
          disabled={disabled}
          placeholder="~/projects/my-app"
          autoComplete="off"
          spellCheck={false}
          className={cn(
            "border-input placeholder:text-muted-foreground focus-visible:border-ring focus-visible:ring-ring/50 w-full rounded-lg border bg-transparent py-2 pr-3 pl-9 text-sm transition-colors outline-none focus-visible:ring-2 disabled:opacity-50"
          )}
        />
      </div>
      {open && suggestions.length > 0 ? (
        <ul
          role="listbox"
          className="bg-popover border-border absolute top-full z-50 mt-1 max-h-48 w-full overflow-auto rounded-lg border shadow-md"
        >
          {suggestions.map((suggestion, index) => (
            <li
              key={suggestion}
              role="option"
              aria-selected={index === highlightedIndex}
              onMouseDown={(e) => {
                e.preventDefault();
                handleSelect(suggestion);
              }}
              className={cn(
                "flex cursor-pointer items-center gap-2 px-3 py-2 font-mono text-xs",
                index === highlightedIndex
                  ? "bg-accent text-accent-foreground"
                  : "text-foreground hover:bg-accent/50"
              )}
            >
              <FolderOpen className="size-3 shrink-0 opacity-50" />
              {suggestion}
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
