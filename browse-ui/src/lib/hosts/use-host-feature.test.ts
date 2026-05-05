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
    mockCapabilities.mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: false,
    } as ReturnType<typeof hooks.useHostCapabilities>);

    const { result } = renderHook(() => useHostFeature(LOCAL_HOST, "search", true));

    expect(result.current).toEqual({ supported: true, loading: false });
    // capabilities should not be fetched for local host
    expect(mockCapabilities).toHaveBeenCalledWith(LOCAL_HOST, false);
  });

  it("returns supported=false, loading=false when enabled=false", () => {
    mockCapabilities.mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: false,
    } as ReturnType<typeof hooks.useHostCapabilities>);

    const { result } = renderHook(() => useHostFeature(REMOTE_HOST, "search", false));
    expect(result.current).toEqual({ supported: false, loading: false });
  });

  it("returns loading=true while capabilities are fetching for remote host", () => {
    mockCapabilities.mockReturnValue({
      data: undefined,
      isLoading: true,
      isError: false,
    } as ReturnType<typeof hooks.useHostCapabilities>);

    const { result } = renderHook(() => useHostFeature(REMOTE_HOST, "search", true));
    expect(result.current).toEqual({ supported: false, loading: true });
  });

  it("returns supported=true if feature is in supported_features", () => {
    mockCapabilities.mockReturnValue({
      data: {
        cli_kind: "full",
        supported_modes: ["chat"],
        supported_features: ["search", "knowledge"],
      },
      isLoading: false,
      isError: false,
    } as ReturnType<typeof hooks.useHostCapabilities>);

    const { result } = renderHook(() => useHostFeature(REMOTE_HOST, "search", true));
    expect(result.current).toEqual({ supported: true, loading: false });
  });

  it("returns supported=false if feature is NOT in supported_features", () => {
    mockCapabilities.mockReturnValue({
      data: {
        cli_kind: "lite",
        supported_modes: ["chat"],
        supported_features: ["chat"],
      },
      isLoading: false,
      isError: false,
    } as ReturnType<typeof hooks.useHostCapabilities>);

    const { result } = renderHook(() => useHostFeature(REMOTE_HOST, "search", true));
    expect(result.current).toEqual({ supported: false, loading: false });
  });

  it("fails closed when capabilities call errors", () => {
    mockCapabilities.mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
    } as ReturnType<typeof hooks.useHostCapabilities>);

    const { result } = renderHook(() => useHostFeature(REMOTE_HOST, "search", true));
    expect(result.current).toEqual({ supported: false, loading: false });
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
    mockCapabilities.mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: false,
    } as ReturnType<typeof hooks.useHostCapabilities>);

    const { result } = renderHook(() => useHostFeature(noUrlHost, "knowledge", true));
    expect(result.current).toEqual({ supported: true, loading: false });
  });
});
