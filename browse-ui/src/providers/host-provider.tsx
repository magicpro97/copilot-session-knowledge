"use client";

/**
 * host-provider.tsx — browse-wide shared host state.
 *
 * Mounts at the root layout so all pages share a single source of truth for:
 *   - The active HostProfile (LOCAL_HOST when no remote host is selected).
 *   - Whether diagnostics/API calls are safe to fire on the current origin.
 *
 * Reacts to route changes, cross-tab `storage` events, and same-tab
 * `browse:host-change` events dispatched by the host-profiles helpers.
 */

import { createContext, useContext, useEffect, useState } from "react";
import { usePathname } from "next/navigation";

import type { HostProfile } from "@/lib/api/types";
import {
  BROWSE_HOST_CHANGE_EVENT,
  LOCAL_HOST,
  getEffectiveHost,
  isOperatorHostEnabled,
} from "@/lib/host-profiles";

// ── Types ────────────────────────────────────────────────────────────────────

export type HostState = {
  /** The currently active host profile. Defaults to LOCAL_HOST (SSR safe). */
  host: HostProfile;
  /**
   * Whether operator/diagnostics API calls are safe to fire.
   * False on a hosted static origin with no remote agent host configured.
   */
  diagnosticsEnabled: boolean;
};

// ── Context ───────────────────────────────────────────────────────────────────

const HostContext = createContext<HostState>({
  host: LOCAL_HOST,
  diagnosticsEnabled: false,
});

// ── Provider ──────────────────────────────────────────────────────────────────

export function HostProvider({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  // SSR-safe defaults — same as what every consumer was initialising locally.
  const [state, setState] = useState<HostState>({
    host: LOCAL_HOST,
    diagnosticsEnabled: false,
  });

  useEffect(() => {
    const update = () => {
      const h = getEffectiveHost();
      setState({
        host: h,
        diagnosticsEnabled: isOperatorHostEnabled(h, pathname ?? window.location.pathname),
      });
    };

    update(); // Hydrate from localStorage on first client render.
    window.addEventListener("storage", update); // Cross-tab changes.
    window.addEventListener(BROWSE_HOST_CHANGE_EVENT, update); // Same-tab changes.

    return () => {
      window.removeEventListener("storage", update);
      window.removeEventListener(BROWSE_HOST_CHANGE_EVENT, update);
    };
  }, [pathname]);

  return <HostContext.Provider value={state}>{children}</HostContext.Provider>;
}

// ── Hook ──────────────────────────────────────────────────────────────────────

/** Returns the browse-wide host state from the nearest HostProvider. */
export function useHostState(): HostState {
  return useContext(HostContext);
}
