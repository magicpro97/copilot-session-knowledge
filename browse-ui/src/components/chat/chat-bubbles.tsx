"use client";

import { useState } from "react";
import { ChevronDown, ChevronUp, Terminal, Wrench } from "lucide-react";

import { cn } from "@/lib/utils";
import type { AssistantChunk } from "./stream-derive";

// ── Text chunk ──────────────────────────────────────────────────────────────

function TextChunk({ text }: { text: string }) {
  return <p className="text-sm leading-relaxed whitespace-pre-wrap">{text}</p>;
}

// ── Raw chunk ───────────────────────────────────────────────────────────────

function RawChunk({ text }: { text: string }) {
  return (
    <pre className="bg-muted/40 overflow-x-auto rounded p-2 font-mono text-xs whitespace-pre-wrap">
      {text}
    </pre>
  );
}

// ── Tool chunk ──────────────────────────────────────────────────────────────

function ToolChunk({ chunk }: { chunk: AssistantChunk & { kind: "tool" } }) {
  const [open, setOpen] = useState(false);

  const inputStr = Object.entries(chunk.input)
    .map(([k, v]) => `${k}: ${typeof v === "string" ? v : JSON.stringify(v)}`)
    .join("\n");

  return (
    <div className="rounded-md border text-xs">
      <button
        type="button"
        onClick={() => setOpen((p) => !p)}
        className="text-muted-foreground hover:text-foreground flex w-full items-center gap-2 px-3 py-2 text-left transition-colors"
      >
        <Wrench className="size-3 shrink-0" />
        <span className="font-mono font-medium">{chunk.name}</span>
        {chunk.output !== undefined ? (
          <span className="ml-auto shrink-0 text-emerald-600 dark:text-emerald-400">✓</span>
        ) : (
          <span className="ml-auto shrink-0 animate-pulse text-amber-500">…</span>
        )}
        {open ? (
          <ChevronUp className="size-3 shrink-0" />
        ) : (
          <ChevronDown className="size-3 shrink-0" />
        )}
      </button>
      {open ? (
        <div className="border-t px-3 pt-2 pb-3">
          {inputStr ? (
            <pre className="bg-muted/40 rounded p-2 font-mono text-xs whitespace-pre-wrap">
              {inputStr}
            </pre>
          ) : null}
          {chunk.output !== undefined ? (
            <div className="mt-2">
              <p className="text-muted-foreground mb-1 text-xs font-medium tracking-wide uppercase">
                Output
              </p>
              <pre className="bg-muted/40 rounded p-2 font-mono text-xs whitespace-pre-wrap">
                {chunk.output || "(empty)"}
              </pre>
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

// ── User bubble ─────────────────────────────────────────────────────────────

type UserBubbleProps = { prompt: string; timestamp?: string };

export function UserBubble({ prompt, timestamp }: UserBubbleProps) {
  return (
    <div className="flex justify-end">
      <div className="max-w-[80%] space-y-1">
        <div className="bg-primary text-primary-foreground rounded-2xl rounded-tr-sm px-4 py-2.5 text-sm">
          <p className="whitespace-pre-wrap">{prompt}</p>
        </div>
        {timestamp ? <p className="text-muted-foreground text-right text-xs">{timestamp}</p> : null}
      </div>
    </div>
  );
}

// ── Assistant bubble ─────────────────────────────────────────────────────────

type AssistantBubbleProps = {
  chunks: AssistantChunk[];
  streaming?: boolean;
  exitCode?: number | null;
  timestamp?: string;
};

export function AssistantBubble({
  chunks,
  streaming = false,
  exitCode,
  timestamp,
}: AssistantBubbleProps) {
  const isEmpty = chunks.length === 0;

  return (
    <div className="flex justify-start">
      <div className="max-w-[90%] space-y-1">
        <div className="bg-card rounded-2xl rounded-tl-sm border px-4 py-2.5">
          {isEmpty && streaming ? (
            <div className="flex items-center gap-2 py-1">
              <span className="text-muted-foreground animate-pulse text-xs">Thinking…</span>
            </div>
          ) : null}
          {isEmpty && !streaming ? (
            <p className="text-muted-foreground text-sm">(no output)</p>
          ) : null}
          <div className="space-y-2">
            {chunks.map((chunk, index) => {
              if (chunk.kind === "text") return <TextChunk key={index} text={chunk.text} />;
              if (chunk.kind === "raw") return <RawChunk key={index} text={chunk.text} />;
              if (chunk.kind === "tool") return <ToolChunk key={index} chunk={chunk} />;
              return null;
            })}
          </div>
          {streaming ? (
            <div className="mt-2 flex items-center gap-1">
              <span className="bg-primary size-1.5 animate-bounce rounded-full" />
              <span
                className="bg-primary size-1.5 animate-bounce rounded-full"
                style={{ animationDelay: "0.15s" }}
              />
              <span
                className="bg-primary size-1.5 animate-bounce rounded-full"
                style={{ animationDelay: "0.3s" }}
              />
            </div>
          ) : null}
        </div>
        <div className="flex items-center gap-2">
          {timestamp ? <p className="text-muted-foreground text-xs">{timestamp}</p> : null}
          {exitCode !== null && exitCode !== undefined && !streaming ? (
            <span
              className={cn(
                "inline-flex items-center gap-1 rounded px-1.5 py-0.5 font-mono text-xs",
                exitCode === 0
                  ? "bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-400"
                  : "bg-rose-100 text-rose-700 dark:bg-rose-900/30 dark:text-rose-400"
              )}
            >
              <Terminal className="size-3" />
              exit {exitCode}
            </span>
          ) : null}
        </div>
      </div>
    </div>
  );
}
