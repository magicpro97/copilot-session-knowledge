"use client";

import { Rows2, Rows3 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { useDensity } from "@/hooks/use-density";
import { cn } from "@/lib/utils";

export function DensityToggle() {
  const [density, setDensity] = useDensity();

  return (
    <div className="inline-flex items-center gap-1 rounded-lg border border-border bg-card p-1">
      <Button
        type="button"
        size="sm"
        variant={density === "compact" ? "secondary" : "ghost"}
        className={cn("h-7")}
        onClick={() => setDensity("compact")}
        aria-pressed={density === "compact"}
      >
        <Rows2 className="size-3.5" />
        Compact
      </Button>
      <Button
        type="button"
        size="sm"
        variant={density === "comfortable" ? "secondary" : "ghost"}
        className={cn("h-7")}
        onClick={() => setDensity("comfortable")}
        aria-pressed={density === "comfortable"}
      >
        <Rows3 className="size-3.5" />
        Comfortable
      </Button>
    </div>
  );
}
