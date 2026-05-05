"use client";

import { useHostCapabilities } from "@/lib/api/hooks";
import type { HostProfile } from "@/lib/api/types";
import { LOCAL_HOST_ID } from "@/lib/host-profiles";

export type HostFeatureResult = {
  /** Whether the named feature is available on the given host. */
  supported: boolean;
  /** True while the capabilities response is in-flight (remote hosts only). */
  loading: boolean;
};

/**
 * Returns whether the given host supports the named feature.
 *
 * - LOCAL_HOST (same-origin): always supported — no remote capabilities check.
 * - Remote host: queries /api/operator/capabilities and checks supported_features.
 * - If capabilities haven't loaded yet: { supported: false, loading: true }.
 * - If the capabilities call errors: fails closed so pages show a clean unsupported state
 *   instead of issuing route fetches against unknown/stripped hosts.
 *
 * @param host     The active HostProfile from useHostState().
 * @param feature  Feature name to check (e.g. "search", "knowledge").
 * @param enabled  Whether diagnostics/API calls are currently safe to fire.
 *                 Pass `diagnosticsEnabled` from useHostState(). When false
 *                 the hook stays idle and returns { supported: false, loading: false }.
 */
export function useHostFeature(
  host: HostProfile,
  feature: string,
  enabled = true
): HostFeatureResult {
  const isLocal = host.id === LOCAL_HOST_ID || !host.base_url;
  // Only query capabilities for remote hosts when diagnostics are enabled.
  const capabilitiesQuery = useHostCapabilities(host, enabled && !isLocal);

  if (!enabled) {
    return { supported: false, loading: false };
  }

  if (isLocal) {
    // Local/same-origin hosts are assumed to support all features.
    return { supported: true, loading: false };
  }

  if (capabilitiesQuery.isLoading) {
    return { supported: false, loading: true };
  }

  if (capabilitiesQuery.isError || !capabilitiesQuery.data) {
    return { supported: false, loading: false };
  }

  return {
    supported: capabilitiesQuery.data.supported_features.includes(feature),
    loading: false,
  };
}
