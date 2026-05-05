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
 * Core features that legacy/older remote backends are assumed to support even
 * when `supported_features` does not enumerate them (or is absent).  These
 * correspond to routes that existed before the v2 protocol marker was
 * introduced.  Non-core / newer features (e.g. "suggest", "preview", "diff")
 * are NOT included here and remain fail-closed against legacy backends.
 */
const LEGACY_CORE_FEATURES = new Set([
  "chat",
  "sessions",
  "search",
  "graph",
  "insights",
  "diagnostics",
]);

/**
 * Returns whether the given host supports the named feature.
 *
 * - LOCAL_HOST (same-origin): always supported — no remote capabilities check.
 * - Remote host: queries /api/operator/capabilities and checks supported_features.
 * - If capabilities haven't loaded yet: { supported: false, loading: true }.
 * - Backward-compatibility rule for remote hosts:
 *   - Modern backends include `protocol: "v2"` in the response; the UI trusts
 *     `supported_features` exactly (fail-closed for missing features).
 *   - Legacy backends (no `protocol` field) may not enumerate all working
 *     routes; the UI falls back to allowing LEGACY_CORE_FEATURES regardless of
 *     what `supported_features` contains.
 *   - Transient errors (network, parse failure) also use the legacy fallback
 *     for LEGACY_CORE_FEATURES so core pages stay accessible during disruptions.
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
    // Transient failure: allow core legacy features so disruptions don't hide
    // working pages.  Non-core features remain unsupported.
    return {
      supported: LEGACY_CORE_FEATURES.has(feature),
      loading: false,
    };
  }

  const { data } = capabilitiesQuery;

  if (data.protocol) {
    // Modern backend with explicit protocol marker: trust supported_features exactly.
    return {
      supported: data.supported_features.includes(feature),
      loading: false,
    };
  }

  // Legacy backend (no protocol marker): assume all core legacy features are
  // available even if not explicitly listed in supported_features.
  return {
    supported: data.supported_features.includes(feature) || LEGACY_CORE_FEATURES.has(feature),
    loading: false,
  };
}
