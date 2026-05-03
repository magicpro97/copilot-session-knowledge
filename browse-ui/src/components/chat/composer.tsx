"use client";

import { useRef, useState } from "react";
import { Send, Loader2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { cn } from "@/lib/utils";

type ComposerProps = {
  onSubmit: (prompt: string) => void;
  loading?: boolean;
  disabled?: boolean;
  className?: string;
  placeholder?: string;
};

/**
 * Prompt input area. Submits on Cmd/Ctrl+Enter or the send button.
 * Auto-resizes up to a max height.
 */
export function Composer({
  onSubmit,
  loading,
  disabled,
  className,
  placeholder = "Send a prompt… (⌘↩ to submit)",
}: ComposerProps) {
  const [value, setValue] = useState("");
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const canSubmit = Boolean(value.trim()) && !loading && !disabled;

  function handleSubmit(e?: React.FormEvent) {
    e?.preventDefault();
    if (!canSubmit) return;
    const prompt = value.trim();
    setValue("");
    onSubmit(prompt);
  }

  function handleKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
      e.preventDefault();
      handleSubmit();
    }
  }

  return (
    <form onSubmit={handleSubmit} className={cn("bg-card border-t px-4 py-3", className)}>
      <div className="relative flex items-end gap-2">
        <Textarea
          ref={textareaRef}
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={handleKeyDown}
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
