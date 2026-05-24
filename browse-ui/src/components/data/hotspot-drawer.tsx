/**
 * Flight Recorder v3 — Hotspot drawer (synthesis §4d/§4e).
 *
 * Collapsed-by-default drawer that surfaces errors / slowest spans /
 * long gaps from the already-loaded skeleton. Consumes only the safe
 * output of `deriveHotspots` from `@/lib/flight-recorder`.
 *
 * Selectors:
 *   - data-testid="hotspot-drawer"
 *   - data-testid="hotspot-drawer-toggle" (aria-expanded)
 *   - data-testid="hotspot-error-<idx>"   (per-row, idx = sorted index)
 *   - data-testid="hotspot-slow-<idx>"
 *   - data-testid="hotspot-gap-<idx>"
 */
import { useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";

import { Button } from "@/components/ui/button";
import type { Hotspots } from "@/lib/flight-recorder";
import { cn } from "@/lib/utils";

export type HotspotDrawerProps = {
  hotspots: Hotspots;
  /** Called when the user clicks a "Go to" affordance on a row. */
  onSeek?: (sortedIndex: number) => void;
  /** Default open state — defaults to closed for v3 contract. */
  defaultOpen?: boolean;
};

function formatMs(ms: number): string {
  if (!Number.isFinite(ms) || ms < 0) return "—";
  if (ms < 1000) return `${Math.round(ms)}ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`;
  const minutes = Math.floor(ms / 60_000);
  const seconds = Math.floor((ms % 60_000) / 1000);
  return `${minutes}m${seconds.toString().padStart(2, "0")}s`;
}

function formatTimestamp(ts: string | null): string {
  if (!ts) return "—";
  try {
    return new Date(ts).toISOString().replace("T", " ").replace("Z", "");
  } catch {
    return "—";
  }
}

export function HotspotDrawer({ hotspots, onSeek, defaultOpen = false }: HotspotDrawerProps) {
  const [open, setOpen] = useState<boolean>(defaultOpen);

  const totalRows = hotspots.errors.length + hotspots.slowest.length + hotspots.gaps.length;

  return (
    <div
      role="region"
      aria-label="Hotspots"
      data-testid="hotspot-drawer"
      data-open={open ? "true" : "false"}
      className="border-border bg-card overflow-hidden rounded-lg border text-xs"
    >
      <button
        type="button"
        data-testid="hotspot-drawer-toggle"
        aria-expanded={open}
        aria-controls="hotspot-drawer-body"
        aria-label={open ? "Collapse hotspots" : "Expand hotspots"}
        onClick={() => setOpen((v) => !v)}
        className="hover:bg-muted/50 flex w-full items-center gap-2 px-3 py-1.5 text-left"
      >
        {open ? (
          <ChevronDown className="size-3" aria-hidden />
        ) : (
          <ChevronRight className="size-3" aria-hidden />
        )}
        <span className="font-medium">Hotspots</span>
        <span className="text-muted-foreground ml-auto">
          {hotspots.errors.length} error · {hotspots.slowest.length} slow · {hotspots.gaps.length}{" "}
          gap
        </span>
      </button>

      {open && (
        <div id="hotspot-drawer-body" className="border-border space-y-3 border-t px-3 py-2">
          {totalRows === 0 && (
            <p className="text-muted-foreground py-2 text-center">No hotspots detected</p>
          )}

          {hotspots.errors.length > 0 && (
            <section aria-label="Errors">
              <h4 className="text-muted-foreground mb-1 text-[10px] tracking-wider uppercase">
                Errors
              </h4>
              <ul className="space-y-1">
                {hotspots.errors.map((row) => (
                  <li
                    key={`err-${row.sortedIndex}`}
                    data-testid={`hotspot-error-${row.sortedIndex}`}
                    className="flex items-center justify-between gap-2"
                  >
                    <span className="font-mono">
                      <span className="text-red-400">error</span> · idx {row.entryIdx} ·{" "}
                      <span className="text-muted-foreground">
                        {formatTimestamp(row.timestamp)}
                      </span>
                    </span>
                    <Button
                      type="button"
                      variant="ghost"
                      size="xs"
                      aria-label={`Go to error event ${row.entryIdx}`}
                      onClick={() => onSeek?.(row.sortedIndex)}
                    >
                      Go to
                    </Button>
                  </li>
                ))}
              </ul>
            </section>
          )}

          {hotspots.slowest.length > 0 && (
            <section aria-label="Slowest">
              <h4 className="text-muted-foreground mb-1 text-[10px] tracking-wider uppercase">
                Slowest
              </h4>
              <ul className="space-y-1">
                {hotspots.slowest.map((row) => (
                  <li
                    key={`slow-${row.sortedIndex}`}
                    data-testid={`hotspot-slow-${row.sortedIndex}`}
                    className="flex items-center justify-between gap-2"
                  >
                    <span className="font-mono">
                      idx {row.entryIdx} ·{" "}
                      <span className="text-amber-300">{formatMs(row.durationMs)}</span>
                    </span>
                    <Button
                      type="button"
                      variant="ghost"
                      size="xs"
                      aria-label={`Go to slow event ${row.entryIdx}`}
                      onClick={() => onSeek?.(row.sortedIndex)}
                    >
                      Go to
                    </Button>
                  </li>
                ))}
              </ul>
            </section>
          )}

          {hotspots.gaps.length > 0 && (
            <section aria-label="Long gaps">
              <h4 className="text-muted-foreground mb-1 text-[10px] tracking-wider uppercase">
                Long gaps
              </h4>
              <ul className="space-y-1">
                {hotspots.gaps.map((row) => (
                  <li
                    key={`gap-${row.startSortedIndex}-${row.endSortedIndex}`}
                    data-testid={`hotspot-gap-${row.startSortedIndex}`}
                    className="flex items-center justify-between gap-2"
                  >
                    <span
                      className={cn("font-mono", row.gapMs > 5 * 60_000 ? "text-amber-300" : "")}
                    >
                      {row.startSortedIndex} → {row.endSortedIndex} · {formatMs(row.gapMs)}
                    </span>
                    <Button
                      type="button"
                      variant="ghost"
                      size="xs"
                      aria-label={`Go to gap starting at event ${row.startSortedIndex}`}
                      onClick={() => onSeek?.(row.startSortedIndex)}
                    >
                      Go to
                    </Button>
                  </li>
                ))}
              </ul>
            </section>
          )}
        </div>
      )}
    </div>
  );
}
