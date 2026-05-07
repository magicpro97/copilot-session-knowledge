import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { usePalette } from "./use-palette";

const STORAGE_KEY = "browse-palette";
const HTML_CLASS = "palette-classic";

describe("usePalette", () => {
  beforeEach(() => {
    localStorage.clear();
    document.documentElement.classList.remove(HTML_CLASS);
  });

  afterEach(() => {
    localStorage.clear();
    document.documentElement.classList.remove(HTML_CLASS);
  });

  it("defaults to contrast and does not toggle the html class", async () => {
    const { result } = renderHook(() => usePalette());
    await act(async () => {});
    expect(result.current[0]).toBe("contrast");
    expect(document.documentElement.classList.contains(HTML_CLASS)).toBe(false);
  });

  it("hydrates from localStorage and applies palette-classic class", async () => {
    localStorage.setItem(STORAGE_KEY, "classic");
    const { result } = renderHook(() => usePalette());
    await act(async () => {});
    expect(result.current[0]).toBe("classic");
    expect(document.documentElement.classList.contains(HTML_CLASS)).toBe(true);
  });

  it("ignores unknown stored values", async () => {
    localStorage.setItem(STORAGE_KEY, "neon");
    const { result } = renderHook(() => usePalette());
    await act(async () => {});
    expect(result.current[0]).toBe("contrast");
    expect(document.documentElement.classList.contains(HTML_CLASS)).toBe(false);
  });

  it("setPalette persists and toggles the html class", async () => {
    const { result } = renderHook(() => usePalette());
    await act(async () => {});

    await act(async () => {
      result.current[1]("classic");
    });
    expect(result.current[0]).toBe("classic");
    expect(localStorage.getItem(STORAGE_KEY)).toBe("classic");
    expect(document.documentElement.classList.contains(HTML_CLASS)).toBe(true);

    await act(async () => {
      result.current[1]("contrast");
    });
    expect(result.current[0]).toBe("contrast");
    expect(localStorage.getItem(STORAGE_KEY)).toBe("contrast");
    expect(document.documentElement.classList.contains(HTML_CLASS)).toBe(false);
  });
});
