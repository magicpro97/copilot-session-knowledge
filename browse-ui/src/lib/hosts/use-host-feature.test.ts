"use client";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { renderHook } from "@testing-library/react";

import { useHostFeature } from "./use-host-feature";
import * as hooks from "@/lib/api/hooks";
import { LOCAL_HOST } from "@/lib/host-profiles";
import type { HostProfile } from "@/lib/api/types";

vi.mock("@/lib/api/hooks", async (importOriginal) => {
  const original = await importOriginal<typeof hooks>();
  return {
    ...original,
    useHostCapabilities: vi.fn(),
  };
});

const mockCapabilities = vi.mocked(hooks.useHostCapabilities);

function asCapabilitiesQuery(
  value: Partial<ReturnType<typeof hooks.useHostCapabilities>>
): ReturnType<typeof hooks.useHostCapabilities> {
  return value as unknown as ReturnType<typeof hooks.useHostCapabilities>;
}

const REMOTE_HOST: HostProfile = {
  id: "remote-1",
  label: "Remote Agent",
  base_url: "http://remote.example.com",
  token: "",
  cli_kind: "copilot",
  is_default: false,
};

describe("useHostFeature", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("returns supported=true for LOCAL_HOST without calling capabilities", () => {
    mockCapabilities.mockReturnValue(
      asCapabilitiesQuery({
        data: undefined,
        isLoading: false,
        isError: false,
      })
    );

    const { result } = renderHook(() => useHostFeature(LOCAL_HOST, "search", true));

    expect(result.current).toEqual({ supported: true, loading: false });
    // capabilities should not be fetched for local host
    expect(mockCapabilities).toHaveBeenCalledWith(LOCAL_HOST, false);
  });

  it("returns supported=false, loading=false when enabled=false", () => {
    mockCapabilities.mockReturnValue(
      asCapabilitiesQuery({
        data: undefined,
        isLoading: false,
        isError: false,
      })
    );

    const { result } = renderHook(() => useHostFeature(REMOTE_HOST, "search", false));
    expect(result.current).toEqual({ supported: false, loading: false });
  });

  it("returns loading=true while capabilities are fetching for remote host", () => {
    mockCapabilities.mockReturnValue(
      asCapabilitiesQuery({
        data: undefined,
        isLoading: true,
        isError: false,
      })
    );

    const { result } = renderHook(() => useHostFeature(REMOTE_HOST, "search", true));
    expect(result.current).toEqual({ supported: false, loading: true });
  });

  it("returns supported=true if feature is in supported_features (modern payload)", () => {
    mockCapabilities.mockReturnValue(
      asCapabilitiesQuery({
        data: {
          cli_kind: "full",
          supported_modes: ["chat"],
          supported_features: ["search", "knowledge"],
          protocol: "v2",
        },
        isLoading: false,
        isError: false,
      })
    );

    const { result } = renderHook(() => useHostFeature(REMOTE_HOST, "search", true));
    expect(result.current).toEqual({ supported: true, loading: false });
  });

  it("returns supported=false if feature is NOT in supported_features (modern payload)", () => {
    mockCapabilities.mockReturnValue(
      asCapabilitiesQuery({
        data: {
          cli_kind: "lite",
          supported_modes: ["chat"],
          supported_features: ["chat"],
          protocol: "v2",
        },
        isLoading: false,
        isError: false,
      })
    );

    const { result } = renderHook(() => useHostFeature(REMOTE_HOST, "search", true));
    expect(result.current).toEqual({ supported: false, loading: false });
  });

  // ── Backward-compatibility: legacy payloads (no protocol marker) ──────────

  it("legacy payload: core feature not in supported_features is still supported", () => {
    // Older backend returns empty supported_features without a protocol marker.
    mockCapabilities.mockReturnValue(
      asCapabilitiesQuery({
        data: {
          cli_kind: "copilot",
          supported_modes: ["ask"],
          supported_features: [],
          // no protocol field
        },
        isLoading: false,
        isError: false,
      })
    );

    for (const coreFeature of ["chat", "sessions", "search", "graph", "insights", "diagnostics"]) {
      const { result } = renderHook(() => useHostFeature(REMOTE_HOST, coreFeature, true));
      expect(
        result.current,
        `Expected core feature "${coreFeature}" to be supported on legacy payload`
      ).toEqual({ supported: true, loading: false });
    }
  });

  it("legacy payload: non-core feature not in supported_features is unsupported", () => {
    mockCapabilities.mockReturnValue(
      asCapabilitiesQuery({
        data: {
          cli_kind: "copilot",
          supported_modes: ["ask"],
          supported_features: [],
          // no protocol field
        },
        isLoading: false,
        isError: false,
      })
    );

    for (const newFeature of ["suggest", "preview", "diff", "knowledge"]) {
      const { result } = renderHook(() => useHostFeature(REMOTE_HOST, newFeature, true));
      expect(
        result.current,
        `Expected non-core feature "${newFeature}" to be unsupported on legacy payload`
      ).toEqual({ supported: false, loading: false });
    }
  });

  it("legacy payload: non-core feature explicitly in supported_features IS supported", () => {
    mockCapabilities.mockReturnValue(
      asCapabilitiesQuery({
        data: {
          cli_kind: "copilot",
          supported_modes: ["ask"],
          supported_features: ["suggest"],
          // no protocol field
        },
        isLoading: false,
        isError: false,
      })
    );

    const { result } = renderHook(() => useHostFeature(REMOTE_HOST, "suggest", true));
    expect(result.current).toEqual({ supported: true, loading: false });
  });

  it("modern payload (protocol set): core feature NOT in supported_features is unsupported", () => {
    // Modern backend explicitly omits a core feature — should be fail-closed.
    mockCapabilities.mockReturnValue(
      asCapabilitiesQuery({
        data: {
          cli_kind: "lite",
          supported_modes: ["chat"],
          supported_features: ["chat"],
          protocol: "v2",
        },
        isLoading: false,
        isError: false,
      })
    );

    const { result } = renderHook(() => useHostFeature(REMOTE_HOST, "search", true));
    expect(result.current).toEqual({ supported: false, loading: false });
  });

  // ── Transient capability failures ─────────────────────────────────────────

  it("transient error: core feature is supported via legacy fallback", () => {
    mockCapabilities.mockReturnValue(
      asCapabilitiesQuery({
        data: undefined,
        isLoading: false,
        isError: true,
      })
    );

    for (const coreFeature of ["chat", "sessions", "search", "graph", "insights", "diagnostics"]) {
      const { result } = renderHook(() => useHostFeature(REMOTE_HOST, coreFeature, true));
      expect(
        result.current,
        `Expected core feature "${coreFeature}" to be supported after transient error`
      ).toEqual({ supported: true, loading: false });
    }
  });

  it("transient error: non-core feature remains unsupported", () => {
    mockCapabilities.mockReturnValue(
      asCapabilitiesQuery({
        data: undefined,
        isLoading: false,
        isError: true,
      })
    );

    for (const newFeature of ["suggest", "preview", "diff", "knowledge"]) {
      const { result } = renderHook(() => useHostFeature(REMOTE_HOST, newFeature, true));
      expect(
        result.current,
        `Expected non-core feature "${newFeature}" to be unsupported after transient error`
      ).toEqual({ supported: false, loading: false });
    }
  });

  it("treats host with no base_url as local (full support)", () => {
    const noUrlHost: HostProfile = {
      id: "custom",
      label: "Custom",
      base_url: "",
      token: "",
      cli_kind: "copilot",
      is_default: false,
    };
    mockCapabilities.mockReturnValue(
      asCapabilitiesQuery({
        data: undefined,
        isLoading: false,
        isError: false,
      })
    );

    const { result } = renderHook(() => useHostFeature(noUrlHost, "knowledge", true));
    expect(result.current).toEqual({ supported: true, loading: false });
  });
});
