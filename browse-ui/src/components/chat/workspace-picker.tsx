"use client";

import { useEffect, useRef, useState } from "react";
import { usePathname } from "next/navigation";
import { Eye, EyeOff, FolderOpen } from "lucide-react";

import { cn } from "@/lib/utils";
import { useDebounce } from "@/hooks/use-debounce";
import { usePathSuggest } from "@/lib/api/hooks";
import { LOCAL_HOST, isOperatorHostEnabled } from "@/lib/host-profiles";
import type { HostProfile } from "@/lib/api/types";

type WorkspacePickerProps = {
  value: string;
  onChange: (value: string) => void;
  id?: string;
  className?: string;
  disabled?: boolean;
  /** Active agent host — passed to the path-suggest API so suggestions come from
   *  the right remote host. Defaults to the local same-origin host. */
  host?: HostProfile;
};

/**
 * Text input with debounced path suggestions from `/api/operator/suggest`.
 * Suggestions are shown in a dropdown below the input.
 * A toggle button in the input allows showing or hiding dotfile/hidden folders.
 */
export function WorkspacePicker({
  value,
  onChange,
  id,
  className,
  disabled,
  host = LOCAL_HOST,
}: WorkspacePickerProps) {
  const pathname = usePathname();
  const [inputValue, setInputValue] = useState(value);
  const [open, setOpen] = useState(false);
  const [highlightedIndex, setHighlightedIndex] = useState(-1);
  const [showHidden, setShowHidden] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const debouncedQuery = useDebounce(inputValue, 200);
  const suggestEnabled = isOperatorHostEnabled(host, pathname);
  const { data: suggestData } = usePathSuggest(debouncedQuery, showHidden, host, suggestEnabled);
  const suggestions = suggestEnabled ? (suggestData?.suggestions ?? []) : [];

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
            "border-input placeholder:text-muted-foreground focus-visible:border-ring focus-visible:ring-ring/50 w-full rounded-lg border bg-transparent py-2 pr-9 pl-9 text-sm transition-colors outline-none focus-visible:ring-2 disabled:opacity-50"
          )}
        />
        {/* Toggle hidden-folder visibility */}
        <button
          type="button"
          tabIndex={-1}
          title={showHidden ? "Hide hidden folders" : "Show hidden folders"}
          aria-label={showHidden ? "Hide hidden folders" : "Show hidden folders"}
          aria-pressed={showHidden}
          onClick={() => {
            const next = !showHidden;
            setShowHidden(next);
            setOpen(true);
            // Force a fresh suggestion fetch with the new showHidden flag by
            // momentarily clearing + restoring the input value through the
            // debounce cycle. This is a belt-and-suspenders guard for React
            // Query cache hits that might not re-render the list.
            setHighlightedIndex(-1);
            inputRef.current?.focus();
          }}
          disabled={disabled}
          className={cn(
            "absolute top-1/2 right-2 -translate-y-1/2 rounded p-1 transition-colors disabled:opacity-50",
            showHidden
              ? "bg-accent text-accent-foreground ring-accent ring-1"
              : "text-muted-foreground hover:text-foreground hover:bg-accent/40"
          )}
        >
          {showHidden ? <Eye className="size-3.5" /> : <EyeOff className="size-3.5" />}
        </button>
      </div>
      {open && suggestions.length > 0 ? (
        <ul
          role="listbox"
          className="border-border bg-popover text-popover-foreground absolute top-full z-[60] mt-1 max-h-48 w-full overflow-auto rounded-lg border shadow-md"
          style={{ opacity: 1 }}
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
                  : "hover:bg-accent/50"
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
