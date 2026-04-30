"use client";

import { Clock3, Copy } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { SOURCE_BADGE_CLASSNAMES, SOURCE_LABELS } from "@/lib/constants";
import { formatRelativeTime, formatSessionIdBadgeText } from "@/lib/formatters";
import { cn } from "@/lib/utils";

export function normalizeSource(source: string | null | undefined): string {
  if (!source) return "unknown";
  const normalized = source.trim().toLowerCase();
  if (normalized in SOURCE_LABELS) return normalized;
  return "unknown";
}

type SourceBadgeProps = {
  source: string | null | undefined;
  className?: string;
};

export function SourceBadge({ source, className }: SourceBadgeProps) {
  const normalized = normalizeSource(source);
  const label =
    normalized === "unknown" ? "Unknown" : SOURCE_LABELS[normalized as keyof typeof SOURCE_LABELS];

  return (
    <Badge
      variant={normalized === "unknown" ? "secondary" : "outline"}
      className={cn(SOURCE_BADGE_CLASSNAMES[normalized], className)}
    >
      {label}
    </Badge>
  );
}

type IDBadgeProps = {
  id: string | null | undefined;
  className?: string;
  onCopy?: (id: string) => void;
};

export function IDBadge({ id, className, onCopy }: IDBadgeProps) {
  const text = formatSessionIdBadgeText(id);

  const handleCopy = async () => {
    if (!id || typeof navigator === "undefined" || !navigator.clipboard) return;
    await navigator.clipboard.writeText(id);
    onCopy?.(id);
  };

  return (
    <button
      type="button"
      onClick={handleCopy}
      className={cn(
        "border-border bg-muted text-muted-foreground hover:bg-muted/80 hover:text-foreground inline-flex items-center gap-1 rounded-md border px-1.5 py-0.5 font-mono text-xs transition-colors",
        className
      )}
      title={id ?? undefined}
      aria-label={id ? `Copy session id ${id}` : "Session id unavailable"}
    >
      <span>{text}</span>
      {id ? <Copy className="size-3" aria-hidden /> : null}
    </button>
  );
}

type TimeRelativeProps = {
  value: string | Date | null | undefined;
  className?: string;
};

export function TimeRelative({ value, className }: TimeRelativeProps) {
  const [mounted, setMounted] = useState(false);
  const date = useMemo(() => (value ? new Date(value) : null), [value]);
  const iso = date && !Number.isNaN(date.getTime()) ? date.toISOString() : undefined;
  const stableLabel = iso ? iso.slice(0, 10) : "—";

  useEffect(() => {
    setMounted(true);
  }, []);

  return (
    <time
      className={cn("text-muted-foreground inline-flex items-center gap-1 text-xs", className)}
      dateTime={iso}
      title={iso}
    >
      <Clock3 className="size-3" aria-hidden />
      {mounted ? formatRelativeTime(value) : stableLabel}
    </time>
  );
}
