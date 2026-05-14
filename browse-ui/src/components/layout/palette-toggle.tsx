"use client";

import { Palette as PaletteIcon, Sparkles } from "lucide-react";

import { Button } from "@/components/ui/button";
import { usePalette, type Palette } from "@/hooks/use-palette";
import { cn } from "@/lib/utils";

const OPTIONS: ReadonlyArray<{ value: Palette; label: string; icon: typeof PaletteIcon }> = [
  { value: "contrast", label: "Warm", icon: Sparkles },
  { value: "classic", label: "Classic", icon: PaletteIcon },
];

export function PaletteToggle() {
  const [palette, setPalette] = usePalette();

  return (
    <div
      role="radiogroup"
      aria-label="Color palette"
      className="border-border bg-card inline-flex items-center gap-1 rounded-lg border p-1"
    >
      {OPTIONS.map(({ value, label, icon: Icon }) => (
        <Button
          key={value}
          type="button"
          size="sm"
          variant={palette === value ? "secondary" : "ghost"}
          className={cn("h-7")}
          onClick={() => setPalette(value)}
          role="radio"
          aria-checked={palette === value}
        >
          <Icon className="size-3.5" />
          {label}
        </Button>
      ))}
    </div>
  );
}
