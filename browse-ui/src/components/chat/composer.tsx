"use client";

import { useRef, useState } from "react";
import { Send, Loader2, Paperclip, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { cn } from "@/lib/utils";
import type { QueuedFile } from "@/lib/api/types";

type ComposerProps = {
  onSubmit: (prompt: string, files: QueuedFile[]) => void;
  loading?: boolean;
  disabled?: boolean;
  className?: string;
  placeholder?: string;
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
 */
export function Composer({
  onSubmit,
  loading,
  disabled,
  className,
  placeholder = "Send a prompt… (⌘↩ to submit)",
}: ComposerProps) {
  const [value, setValue] = useState("");
  const [queuedFiles, setQueuedFiles] = useState<QueuedFile[]>([]);
  const [fileError, setFileError] = useState<string | null>(null);
  const [dragOver, setDragOver] = useState(false);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const dragCountRef = useRef(0);

  const canSubmit = Boolean(value.trim()) && !loading && !disabled;

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

  function handleSubmit(e?: React.FormEvent) {
    e?.preventDefault();
    if (!canSubmit) return;
    const prompt = value.trim();
    const files = queuedFiles;
    setValue("");
    setQueuedFiles([]);
    setFileError(null);
    onSubmit(prompt, files);
  }

  function handleKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
      e.preventDefault();
      handleSubmit();
    }
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

        <Textarea
          ref={textareaRef}
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={handleKeyDown}
          onPaste={handlePaste}
          placeholder={placeholder}
          disabled={loading || disabled}
          rows={1}
          className="max-h-40 resize-none pr-12"
          aria-label="Prompt"
        />
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
