"use client";

import { useEffect, useState } from "react";

import { detectShortcutPlatform, type ShortcutPlatform } from "@/lib/shortcut-utils";

/**
 * Returns the detected platform for keyboard shortcut display labels.
 *
 * Initial value is "unknown" (SSR-safe); the value updates to the detected
 * platform after client-side hydration via a one-time useEffect.
 */
export function useKeyboardPlatform(): ShortcutPlatform {
  const [platform, setPlatform] = useState<ShortcutPlatform>("unknown");

  useEffect(() => {
    setPlatform(detectShortcutPlatform());
  }, []);

  return platform;
}
