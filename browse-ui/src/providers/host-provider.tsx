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

import { createContext, useContext, useEffect, useRef, useState } from "react";
import { usePathname } from "next/navigation";

import type { HostProfile } from "@/lib/api/types";
import {
  BROWSE_HOST_CHANGE_EVENT,
  LOCAL_HOST,
  getEffectiveHost,
  isLocalOrigin,
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
  /** Whether same-origin LOCAL_HOST operator routes are reachable on this origin. */
  localDiagnosticsEnabled?: boolean;
};

// ── Context ───────────────────────────────────────────────────────────────────

const HostContext = createContext<HostState>({
  host: LOCAL_HOST,
  diagnosticsEnabled: false,
  localDiagnosticsEnabled: false,
});

// ── Provider ──────────────────────────────────────────────────────────────────

export function HostProvider({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const sameOriginDiagnosticsRef = useRef<boolean | null>(null);
  // SSR-safe defaults — same as what every consumer was initialising locally.
  const [state, setState] = useState<HostState>({
    host: LOCAL_HOST,
    diagnosticsEnabled: false,
    localDiagnosticsEnabled: false,
  });

  useEffect(() => {
    let active = true;

    const update = () => {
      const localDiagnosticsEnabled = sameOriginDiagnosticsRef.current ?? false;
      const applyState = (host: HostProfile, diagnosticsEnabled: boolean) => {
        setState({ host, diagnosticsEnabled, localDiagnosticsEnabled });
      };
      const h = getEffectiveHost();
      const diagnosticsEnabled = isOperatorHostEnabled(h, pathname ?? window.location.pathname);
      if (diagnosticsEnabled) {
        applyState(h, true);
        return;
      }

      if (h.id !== LOCAL_HOST.id) {
        applyState(h, false);
        return;
      }

      if (sameOriginDiagnosticsRef.current !== null) {
        applyState(h, sameOriginDiagnosticsRef.current);
        return;
      }

      applyState(h, false);

      // Only probe same-origin /healthz on real local/loopback origins.
      // Hosted static origins (e.g. Firebase, Vercel, GitHub Pages) have no
      // backend process — issuing the probe there produces a doomed 404 and
      // briefly misleads the provider about local availability.
      if (!isLocalOrigin(window.location.origin)) {
        sameOriginDiagnosticsRef.current = false;
        return;
      }

      void fetch("/healthz", {
        cache: "no-store",
        credentials: "same-origin",
      })
        .then(
          (response) => response.ok,
          () => false
        )
        .then((enabled) => {
          sameOriginDiagnosticsRef.current = enabled;
          if (!active) {
            return;
          }
          const currentHost = getEffectiveHost();
          if (currentHost.id !== LOCAL_HOST.id) {
            setState({
              host: currentHost,
              diagnosticsEnabled: isOperatorHostEnabled(
                currentHost,
                pathname ?? window.location.pathname
              ),
              localDiagnosticsEnabled: enabled,
            });
            return;
          }
          setState({
            host: currentHost,
            diagnosticsEnabled: enabled,
            localDiagnosticsEnabled: enabled,
          });
        });
    };

    update(); // Hydrate from localStorage on first client render.
    window.addEventListener("storage", update); // Cross-tab changes.
    window.addEventListener(BROWSE_HOST_CHANGE_EVENT, update); // Same-tab changes.

    return () => {
      active = false;
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
