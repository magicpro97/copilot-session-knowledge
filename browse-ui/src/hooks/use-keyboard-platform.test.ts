import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { useKeyboardPlatform } from "./use-keyboard-platform";

describe("useKeyboardPlatform", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("detects mac platform after mount on Mac UA", async () => {
    vi.stubGlobal("navigator", {
      userAgent: "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
    });
    const { result } = renderHook(() => useKeyboardPlatform());
    await act(async () => {});
    expect(result.current).toBe("mac");
  });

  it("detects win platform on Windows UA after mount", async () => {
    vi.stubGlobal("navigator", {
      userAgent: "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    });
    const { result } = renderHook(() => useKeyboardPlatform());
    await act(async () => {});
    expect(result.current).toBe("win");
  });

  it("detects win platform on Linux UA after mount", async () => {
    vi.stubGlobal("navigator", {
      userAgent: "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
    });
    const { result } = renderHook(() => useKeyboardPlatform());
    await act(async () => {});
    expect(result.current).toBe("win");
  });

  it("returns unknown for unrecognized UA after mount", async () => {
    vi.stubGlobal("navigator", { userAgent: "SomeBot/1.0" });
    const { result } = renderHook(() => useKeyboardPlatform());
    await act(async () => {});
    expect(result.current).toBe("unknown");
  });

  it("returns unknown when navigator is undefined after mount (SSR guard)", async () => {
    vi.stubGlobal("navigator", undefined);
    const { result } = renderHook(() => useKeyboardPlatform());
    await act(async () => {});
    expect(result.current).toBe("unknown");
  });
});
