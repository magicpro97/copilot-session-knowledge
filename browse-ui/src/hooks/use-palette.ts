"use client";

import { useEffect, useState } from "react";

/**
 * Palette variants:
 * - "contrast": current default (post-2b54b65 APCA tuning) — softer surfaces, less glare.
 * - "classic":  pre-2b54b65 palette restored as opt-in. Activates via .palette-classic on <html>.
 *
 * Both palettes funnel through hsl(var(--…)) in globals.css @theme inline (commit 35a5210),
 * so popover/card surfaces stay opaque under either choice.
 */
export type Palette = "contrast" | "classic";

const STORAGE_KEY = "browse-palette";
const HTML_CLASS = "palette-classic";

function isPalette(value: unknown): value is Palette {
  return value === "contrast" || value === "classic";
}

export function usePalette(): [Palette, (next: Palette) => void] {
  const [palette, setPaletteState] = useState<Palette>("contrast");

  useEffect(() => {
    try {
      const stored = localStorage.getItem(STORAGE_KEY);
      if (isPalette(stored)) setPaletteState(stored);
    } catch {
      // localStorage may throw in private mode / SSR — keep default.
    }
  }, []);

  useEffect(() => {
    const html = document.documentElement;
    html.classList.toggle(HTML_CLASS, palette === "classic");
  }, [palette]);

  const setPalette = (next: Palette) => {
    try {
      localStorage.setItem(STORAGE_KEY, next);
    } catch {
      // Ignore storage failures; in-memory state still updates.
    }
    setPaletteState(next);
  };

  return [palette, setPalette];
}
