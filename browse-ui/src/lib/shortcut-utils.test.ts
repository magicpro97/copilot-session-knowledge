import { afterEach, describe, expect, it, vi } from "vitest";

import {
  detectShortcutPlatform,
  formatModShortcut,
  getModLabel,
  resolveModShortcut,
  type ShortcutPlatform,
} from "./shortcut-utils";

describe("detectShortcutPlatform", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("returns unknown when navigator is undefined (SSR)", () => {
    vi.stubGlobal("navigator", undefined);
    expect(detectShortcutPlatform()).toBe("unknown");
  });

  it("returns mac for Macintosh UA", () => {
    vi.stubGlobal("navigator", {
      userAgent: "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120",
    });
    expect(detectShortcutPlatform()).toBe("mac");
  });

  it("returns mac for iPhone UA", () => {
    vi.stubGlobal("navigator", {
      userAgent: "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605",
    });
    expect(detectShortcutPlatform()).toBe("mac");
  });

  it("returns mac for iPad UA", () => {
    vi.stubGlobal("navigator", {
      userAgent: "Mozilla/5.0 (iPad; CPU OS 17_0 like Mac OS X) AppleWebKit/605",
    });
    expect(detectShortcutPlatform()).toBe("mac");
  });

  it("returns win for Windows UA", () => {
    vi.stubGlobal("navigator", {
      userAgent: "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120",
    });
    expect(detectShortcutPlatform()).toBe("win");
  });

  it("returns win for Linux UA", () => {
    vi.stubGlobal("navigator", {
      userAgent: "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120",
    });
    expect(detectShortcutPlatform()).toBe("win");
  });

  it("returns win for Android UA", () => {
    vi.stubGlobal("navigator", {
      userAgent: "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 Chrome/120",
    });
    expect(detectShortcutPlatform()).toBe("win");
  });

  it("returns unknown for unrecognized UA", () => {
    vi.stubGlobal("navigator", { userAgent: "SomeUnknownBrowser/1.0" });
    expect(detectShortcutPlatform()).toBe("unknown");
  });
});

describe("getModLabel", () => {
  it.each([
    ["mac" as ShortcutPlatform, "⌘"],
    ["win" as ShortcutPlatform, "Ctrl"],
    ["unknown" as ShortcutPlatform, "⌘/Ctrl"],
  ])("returns correct label for %s", (platform, expected) => {
    expect(getModLabel(platform)).toBe(expected);
  });
});

describe("formatModShortcut", () => {
  it("formats K on mac as ⌘K", () => {
    expect(formatModShortcut("K", "mac")).toBe("⌘K");
  });

  it("formats K on win as Ctrl+K", () => {
    expect(formatModShortcut("K", "win")).toBe("Ctrl+K");
  });

  it("formats K on unknown as ⌘/Ctrl+K", () => {
    expect(formatModShortcut("K", "unknown")).toBe("⌘/Ctrl+K");
  });

  it("formats B on mac as ⌘B", () => {
    expect(formatModShortcut("B", "mac")).toBe("⌘B");
  });

  it("formats B on win as Ctrl+B", () => {
    expect(formatModShortcut("B", "win")).toBe("Ctrl+B");
  });

  it("formats Enter on mac as ⌘↩ when macKey is provided", () => {
    expect(formatModShortcut("Enter", "mac", "↩")).toBe("⌘↩");
  });

  it("formats Enter on win as Ctrl+Enter (ignores macKey)", () => {
    expect(formatModShortcut("Enter", "win", "↩")).toBe("Ctrl+Enter");
  });

  it("formats Enter on unknown as ⌘/Ctrl+Enter (ignores macKey)", () => {
    expect(formatModShortcut("Enter", "unknown", "↩")).toBe("⌘/Ctrl+Enter");
  });

  it("falls back to key on mac when no macKey provided", () => {
    expect(formatModShortcut("Enter", "mac")).toBe("⌘Enter");
  });
});

describe("resolveModShortcut", () => {
  it("resolves ⌘/Ctrl+K to ⌘K on mac", () => {
    expect(resolveModShortcut("⌘/Ctrl+K", "mac")).toBe("⌘K");
  });

  it("resolves ⌘/Ctrl+K to Ctrl+K on win", () => {
    expect(resolveModShortcut("⌘/Ctrl+K", "win")).toBe("Ctrl+K");
  });

  it("returns ⌘/Ctrl+K unchanged on unknown", () => {
    expect(resolveModShortcut("⌘/Ctrl+K", "unknown")).toBe("⌘/Ctrl+K");
  });

  it("resolves ⌘/Ctrl+B to ⌘B on mac", () => {
    expect(resolveModShortcut("⌘/Ctrl+B", "mac")).toBe("⌘B");
  });

  it("returns non-mod shortcut strings unchanged", () => {
    expect(resolveModShortcut("G then S", "mac")).toBe("G then S");
    expect(resolveModShortcut("J / ↓", "win")).toBe("J / ↓");
    expect(resolveModShortcut("Esc", "unknown")).toBe("Esc");
  });
});
