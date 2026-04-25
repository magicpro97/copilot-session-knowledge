"use client";

import { useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";

import { useKeyboardShortcuts } from "@/hooks/use-keyboard-shortcuts";
import { GLOBAL_CHORD_TIMEOUT_MS } from "@/lib/constants";

export function GlobalShortcuts() {
  const router = useRouter();
  const [isGChordActive, setIsGChordActive] = useState(false);

  useEffect(() => {
    if (!isGChordActive) return;
    const timer = window.setTimeout(() => setIsGChordActive(false), GLOBAL_CHORD_TIMEOUT_MS);
    return () => window.clearTimeout(timer);
  }, [isGChordActive]);

  const shortcuts = useMemo(
    () => [
      {
        key: "g",
        preventDefault: true,
        handler: () => {
          if (isGChordActive) {
            setIsGChordActive(false);
            router.push("/graph");
            return;
          }
          setIsGChordActive(true);
        },
      },
      {
        key: "s",
        handler: () => {
          if (!isGChordActive) return false;
          setIsGChordActive(false);
          router.push("/sessions");
          return true;
        },
        preventDefault: true,
      },
      {
        key: "/",
        handler: () => {
          if (!isGChordActive) return false;
          setIsGChordActive(false);
          router.push("/search");
          return true;
        },
        preventDefault: true,
      },
      {
        key: "i",
        handler: () => {
          if (!isGChordActive) return false;
          setIsGChordActive(false);
          router.push("/insights");
          return true;
        },
        preventDefault: true,
      },
      {
        key: ",",
        handler: () => {
          if (!isGChordActive) return false;
          setIsGChordActive(false);
          router.push("/settings");
          return true;
        },
        preventDefault: true,
      },
      {
        key: "?",
        preventDefault: true,
        handler: () => {
          setIsGChordActive(false);
          router.push("/settings#shortcuts");
        },
      },
      {
        key: "Escape",
        handler: () => {
          setIsGChordActive(false);
          return false;
        },
      },
    ],
    [isGChordActive, router]
  );

  useKeyboardShortcuts(shortcuts, { capture: true });

  return null;
}
