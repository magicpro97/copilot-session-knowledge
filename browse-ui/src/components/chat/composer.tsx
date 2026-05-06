"use client";

import { useId, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Send, Loader2, Paperclip, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { POPUP_SURFACE_BASE } from "@/components/ui/popup-surface";
import { useKeyboardPlatform } from "@/hooks/use-keyboard-platform";
import { formatModShortcut } from "@/lib/shortcut-utils";
import { cn } from "@/lib/utils";
import type { QueuedFile } from "@/lib/api/types";
import {
  getCommandSuggestions,
  parseWebCommand,
  commandHasArgs,
  type SlashCommand,
} from "./slash-commands";

type ComposerProps = {
  onSubmit: (prompt: string, files: QueuedFile[]) => void;
  /**
   * Called when the user submits a web-supported slash command.
   * The composer clears its own draft before calling this.
   */
  onCommand?: (name: string, args: string) => void;
  loading?: boolean;
  disabled?: boolean;
  className?: string;
  placeholder?: string;
};

type SuggestionPanelPosition = {
  left: number;
  top: number;
  width: number;
};

async function readFileAsQueuedFile(file: File): Promise<QueuedFile> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const result = reader.result as string;
      const data = result.includes(",") ? (result.split(",")[1] ?? "") : result;
      resolve({
        name: file.name,
        type: file.type || "application/octet-stream",
        size: file.size,
        data,
      });
    };
    reader.onerror = () => reject(reader.error);
    reader.readAsDataURL(file);
  });
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes}B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)}KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)}MB`;
}

/**
 * Prompt input area. Submits on Cmd/Ctrl+Enter or the send button.
 * Auto-resizes up to a max height. Supports file attachment via button,
 * drag/drop, and clipboard paste. Queued files appear as removable chips.
 *
 * When the input is a web-supported slash command, `onCommand` is called
 * instead of `onSubmit`. Path-like inputs (/Users/…, /home/…, etc.) are
 * never intercepted and always submit normally.
 */
export function Composer({
  onSubmit,
  onCommand,
  loading,
  disabled,
  className,
  placeholder,
}: ComposerProps) {
  const platform = useKeyboardPlatform();
  const submitHint = formatModShortcut("Enter", platform, "↩");
  const resolvedPlaceholder = placeholder ?? `Send a prompt… (${submitHint} to submit)`;
  const [value, setValue] = useState("");
  const [queuedFiles, setQueuedFiles] = useState<QueuedFile[]>([]);
  const [fileError, setFileError] = useState<string | null>(null);
  const [dragOver, setDragOver] = useState(false);
  const [suggestions, setSuggestions] = useState<SlashCommand[]>([]);
  const [activeSuggestionIndex, setActiveSuggestionIndex] = useState(-1);
  const [suggestionPanelPosition, setSuggestionPanelPosition] = useState<SuggestionPanelPosition | null>(
    null
  );
  const suggestionListId = useId();
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const composerFieldRef = useRef<HTMLDivElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const dragCountRef = useRef(0);

  const canSubmit = Boolean(value.trim()) && !loading && !disabled;
  const activeSuggestion =
    activeSuggestionIndex >= 0 ? suggestions[activeSuggestionIndex] ?? null : null;

  useLayoutEffect(() => {
    if (typeof window === "undefined") {
      return;
    }
    if (suggestions.length === 0) {
      setSuggestionPanelPosition(null);
      return;
    }

    const updateSuggestionPanelPosition = () => {
      const rect = composerFieldRef.current?.getBoundingClientRect();
      if (!rect) {
        setSuggestionPanelPosition(null);
        return;
      }
      setSuggestionPanelPosition({
        left: rect.left,
        top: rect.top - 4,
        width: rect.width,
      });
    };

    updateSuggestionPanelPosition();
    const rafId = window.requestAnimationFrame(updateSuggestionPanelPosition);

    window.addEventListener("resize", updateSuggestionPanelPosition);
    window.addEventListener("scroll", updateSuggestionPanelPosition, true);
    return () => {
      window.cancelAnimationFrame(rafId);
      window.removeEventListener("resize", updateSuggestionPanelPosition);
      window.removeEventListener("scroll", updateSuggestionPanelPosition, true);
    };
  }, [suggestions.length, value]);

  async function addFiles(fileList: FileList | File[]) {
    const files = Array.from(fileList);
    const results = await Promise.allSettled(files.map(readFileAsQueuedFile));
    const queued = results.flatMap((result) =>
      result.status === "fulfilled" ? [result.value] : []
    );
    if (queued.length > 0) {
      setQueuedFiles((prev) => [...prev, ...queued]);
    }
    setFileError(
      results.some((result) => result.status === "rejected")
        ? "Failed to read one or more files."
        : null
    );
  }

  function clearDraft() {
    setValue("");
    setQueuedFiles([]);
    setFileError(null);
    setSuggestions([]);
    setActiveSuggestionIndex(-1);
  }

  function handleSubmit(e?: React.FormEvent) {
    e?.preventDefault();
    if (!canSubmit) return;
    const prompt = value.trim();

    // Check if this is a web-supported slash command.
    const webCmd = onCommand ? parseWebCommand(prompt) : null;
    if (webCmd && onCommand) {
      clearDraft();
      onCommand(webCmd.name, webCmd.args);
      return;
    }

    const files = queuedFiles;
    clearDraft();
    onSubmit(prompt, files);
  }

  /** Accept a suggestion into the textarea without executing it yet. */
  function handleSelectSuggestion(cmd: SlashCommand) {
    setSuggestions([]);
    setActiveSuggestionIndex(-1);
    setSuggestionPanelPosition(null);
    setValue(commandHasArgs(cmd.name) ? `/${cmd.name} ` : `/${cmd.name}`);
    textareaRef.current?.focus();
  }

  function handleChange(e: React.ChangeEvent<HTMLTextAreaElement>) {
    const newValue = e.target.value;
    setValue(newValue);
    const nextSuggestions = getCommandSuggestions(newValue);
    setSuggestions(nextSuggestions);
    setActiveSuggestionIndex(-1);
  }

  function handleKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    // Suggestion navigation takes priority when the list is open.
    if (suggestions.length > 0) {
      if (e.key === "Escape") {
        e.preventDefault();
        setSuggestions([]);
        setActiveSuggestionIndex(-1);
        return;
      }
      if (e.key === "ArrowUp") {
        e.preventDefault();
        setActiveSuggestionIndex((prev) => (prev <= 0 ? suggestions.length - 1 : prev - 1));
        return;
      }
      if (e.key === "ArrowDown") {
        e.preventDefault();
        setActiveSuggestionIndex((prev) => (prev >= suggestions.length - 1 ? 0 : prev + 1));
        return;
      }
      if (e.key === "Enter" && activeSuggestionIndex >= 0) {
        e.preventDefault();
        const cmd = suggestions[activeSuggestionIndex];
        if (cmd) handleSelectSuggestion(cmd);
        return;
      }
      if (e.key === "Tab" && !e.shiftKey) {
        const selectedIndex = activeSuggestionIndex >= 0 ? activeSuggestionIndex : 0;
        const cmd = suggestions[selectedIndex];
        if (cmd) {
          e.preventDefault();
          handleSelectSuggestion(cmd);
          return;
        }
      }
    }

    if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
      e.preventDefault();
      handleSubmit();
    }
  }

  function handleBlur() {
    setSuggestions([]);
    setActiveSuggestionIndex(-1);
  }

  function handleDragEnter(e: React.DragEvent) {
    e.preventDefault();
    dragCountRef.current += 1;
    setDragOver(true);
  }

  function handleDragOver(e: React.DragEvent) {
    e.preventDefault(); // Required to allow drop
  }

  function handleDragLeave() {
    dragCountRef.current = Math.max(0, dragCountRef.current - 1);
    if (dragCountRef.current === 0) setDragOver(false);
  }

  function handleDrop(e: React.DragEvent) {
    e.preventDefault();
    dragCountRef.current = 0;
    setDragOver(false);
    if (e.dataTransfer.files.length > 0) {
      void addFiles(e.dataTransfer.files);
    }
  }

  function handlePaste(e: React.ClipboardEvent<HTMLTextAreaElement>) {
    if (e.clipboardData.files.length > 0) {
      e.preventDefault();
      void addFiles(e.clipboardData.files);
    }
  }

  function handleFileInputChange(e: React.ChangeEvent<HTMLInputElement>) {
    if (e.target.files?.length) {
      void addFiles(e.target.files);
    }
    // Reset so the same file can be re-selected
    e.target.value = "";
  }

  function removeFile(index: number) {
    setQueuedFiles((prev) => prev.filter((_, fileIndex) => fileIndex !== index));
  }

  return (
    <form
      onSubmit={handleSubmit}
      onDragEnter={handleDragEnter}
      onDragOver={handleDragOver}
      onDragLeave={handleDragLeave}
      onDrop={handleDrop}
      className={cn(
        "bg-card border-t px-4 py-3 transition-colors",
        dragOver && "bg-accent/10 ring-primary/30 ring-1 ring-inset",
        className
      )}
    >
      {fileError ? <p className="text-destructive mb-2 text-xs">{fileError}</p> : null}

      {/* Queued file chips — shown before submit */}
      {queuedFiles.length > 0 ? (
        <div className="mb-2 flex flex-wrap gap-1.5" aria-label="Queued files">
          {queuedFiles.map((file, index) => (
            <span
              key={`${file.name}-${file.size}-${index}`}
              className="bg-muted text-foreground inline-flex max-w-[220px] items-center gap-1 rounded-full px-2.5 py-1 text-xs"
            >
              <Paperclip className="size-3 shrink-0 opacity-60" aria-hidden="true" />
              <span className="min-w-0 truncate font-medium">{file.name}</span>
              <span className="text-muted-foreground shrink-0">{formatBytes(file.size)}</span>
              <button
                type="button"
                onClick={() => removeFile(index)}
                aria-label={`Remove ${file.name}`}
                className="text-muted-foreground hover:text-foreground ml-0.5 shrink-0 rounded-full transition-colors"
              >
                <X className="size-3" />
              </button>
            </span>
          ))}
        </div>
      ) : null}

      <div className="relative flex items-end gap-2">
        {/* Hidden file input */}
        <input
          ref={fileInputRef}
          type="file"
          multiple
          className="sr-only"
          aria-hidden="true"
          tabIndex={-1}
          onChange={handleFileInputChange}
        />

        {/* File attachment button */}
        <Button
          type="button"
          variant="ghost"
          size="icon"
          disabled={loading || disabled}
          onClick={() => fileInputRef.current?.click()}
          aria-label="Attach files"
          className="mb-0.5 shrink-0"
        >
          <Paperclip className="size-4" />
        </Button>

        <div ref={composerFieldRef} className="relative flex-1">
          {/* Slash command suggestion panel. Render in a portal so parent overflow clipping
              cannot hide deeper entries like /skills. */}
          {typeof document !== "undefined" && suggestions.length > 0 && suggestionPanelPosition
            ? createPortal(
                <ul
                  id={suggestionListId}
                  role="listbox"
                  aria-label="Slash command suggestions"
                  className={cn(
                    POPUP_SURFACE_BASE,
                    "fixed z-50 max-h-72 overflow-y-auto rounded-md p-1"
                  )}
                  style={{
                    left: suggestionPanelPosition.left,
                    top: suggestionPanelPosition.top,
                    width: suggestionPanelPosition.width,
                    transform: "translateY(-100%)",
                  }}
                >
                  {suggestions.map((cmd, idx) => (
                    <li
                      key={cmd.name}
                      id={`${suggestionListId}-${cmd.name}`}
                      role="option"
                      aria-selected={idx === activeSuggestionIndex}
                    >
                      <button
                        type="button"
                        onMouseEnter={() => setActiveSuggestionIndex(idx)}
                        onMouseDown={(e) => {
                          // Prevent textarea blur before we handle selection.
                          e.preventDefault();
                          handleSelectSuggestion(cmd);
                        }}
                        className={cn(
                          "flex w-full items-baseline gap-2 rounded-md px-3 py-2 text-left text-sm transition-colors",
                          idx === activeSuggestionIndex
                            ? "bg-accent text-accent-foreground"
                            : "hover:bg-accent/50"
                        )}
                      >
                        <span className="text-primary shrink-0 font-mono font-medium">
                          /{cmd.name}
                        </span>
                        <span className="text-muted-foreground min-w-0 truncate text-xs">
                          {cmd.description}
                        </span>
                        {cmd.usage.includes("<") ? (
                          <span className="text-muted-foreground/60 ml-auto shrink-0 font-mono text-xs">
                            {cmd.usage}
                          </span>
                        ) : null}
                      </button>
                    </li>
                  ))}
                </ul>,
                document.body
              )
            : null}
          <Textarea
            ref={textareaRef}
            value={value}
            onChange={handleChange}
            onKeyDown={handleKeyDown}
            onBlur={handleBlur}
            onPaste={handlePaste}
            placeholder={resolvedPlaceholder}
            disabled={loading || disabled}
            rows={1}
            className="max-h-40 resize-none"
            aria-label="Prompt"
            aria-autocomplete="list"
            aria-expanded={suggestions.length > 0}
            aria-controls={suggestions.length > 0 ? suggestionListId : undefined}
            aria-activedescendant={
              activeSuggestion ? `${suggestionListId}-${activeSuggestion.name}` : undefined
            }
          />
        </div>
        <Button
          type="submit"
          size="icon"
          disabled={!canSubmit}
          aria-label="Send prompt"
          className="mb-0.5 shrink-0"
        >
          {loading ? <Loader2 className="size-4 animate-spin" /> : <Send className="size-4" />}
        </Button>
      </div>
    </form>
  );
}
