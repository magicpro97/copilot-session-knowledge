"use client";

import { useEffect } from "react";

export type KeyboardShortcut = {
  key: string;
  handler: (event: KeyboardEvent) => boolean | void;
  metaKey?: boolean;
  ctrlKey?: boolean;
  altKey?: boolean;
  shiftKey?: boolean;
  allowInInput?: boolean;
  preventDefault?: boolean;
  stopPropagation?: boolean;
};

type UseKeyboardShortcutsOptions = {
  enabled?: boolean;
  capture?: boolean;
  target?: Window | Document | null;
};

export function isTypingContext(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  return Boolean(
    target.closest(
      "input, textarea, select, [contenteditable=''], [contenteditable='true'], [role='combobox']"
    )
  );
}

function keyMatches(expected: string, actual: string): boolean {
  return expected.toLowerCase() === actual.toLowerCase();
}

function modifiersMatch(event: KeyboardEvent, shortcut: KeyboardShortcut): boolean {
  const expectedMeta = shortcut.metaKey ?? false;
  const expectedCtrl = shortcut.ctrlKey ?? false;
  const expectedAlt = shortcut.altKey ?? false;
  const expectedShift = shortcut.shiftKey;

  return (
    event.metaKey === expectedMeta &&
    event.ctrlKey === expectedCtrl &&
    event.altKey === expectedAlt &&
    (expectedShift === undefined || event.shiftKey === expectedShift)
  );
}

export function useKeyboardShortcuts(
  shortcuts: KeyboardShortcut[],
  { enabled = true, capture = false, target }: UseKeyboardShortcutsOptions = {}
) {
  useEffect(() => {
    if (!enabled || shortcuts.length === 0 || typeof window === "undefined") return;

    const targetNode = target ?? window;
    const listener = (event: KeyboardEvent) => {
      if (event.defaultPrevented) return;

      for (const shortcut of shortcuts) {
        if (!shortcut.allowInInput && isTypingContext(event.target)) continue;
        if (!modifiersMatch(event, shortcut)) continue;
        if (!keyMatches(shortcut.key, event.key)) continue;

        const handled = shortcut.handler(event);
        if (handled === false) continue;
        if (shortcut.preventDefault) event.preventDefault();
        if (shortcut.stopPropagation) event.stopPropagation();
        break;
      }
    };

    targetNode.addEventListener("keydown", listener as EventListener, { capture });
    return () =>
      targetNode.removeEventListener("keydown", listener as EventListener, {
        capture,
      });
  }, [capture, enabled, shortcuts, target]);
}
