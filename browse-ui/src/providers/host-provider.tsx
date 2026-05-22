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
 *
 * On hosted (non-local) origins with no explicitly selected remote host the
 * provider probes `http://127.0.0.1:8765/.well-known/browse-host` then
 * `http://localhost:8765/.well-known/browse-host` (issue #49). A detected
 * local backend is activated ephemerally without overriding any explicit
 * remote-host selection. A detected profile is held in memory so route changes
 * do not reset the app back to the same-origin placeholder. The probe runs at
 * most once per component lifecycle (backed by a 30-second negative cache in
 * local-bootstrap.ts).
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
import { probeLocalBootstrap } from "@/lib/hosts/local-bootstrap";
import type { LocalBootstrapResult } from "@/lib/hosts/local-bootstrap";

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
  /**
   * Latest result from the loopback bootstrap probe (issue #49 / #56).
   * Null when no probe has run yet or when running on a local origin.
   * Exposed so consumers (e.g. SessionCreateDialog) can pass it to DiagnosticPanel
   * without running a duplicate probe.
   */
  probeResult?: LocalBootstrapResult | null;
};

// ── Context ───────────────────────────────────────────────────────────────────

const HostContext = createContext<HostState>({
  host: LOCAL_HOST,
  diagnosticsEnabled: false,
  localDiagnosticsEnabled: false,
  probeResult: null,
});

// ── Provider ──────────────────────────────────────────────────────────────────

export function HostProvider({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const sameOriginDiagnosticsRef = useRef<boolean | null>(null);
  const mountedRef = useRef(false);
  const loopbackProfileRef = useRef<HostProfile | null>(null);
  /**
   * Tracks whether a loopback bootstrap probe has been initiated for this
   * component lifecycle. Prevents repeated probes on storage/route events.
   */
  const loopbackProbeStartedRef = useRef<boolean>(false);
  // SSR-safe defaults — same as what every consumer was initialising locally.
  const [state, setState] = useState<HostState>({
    host: LOCAL_HOST,
    diagnosticsEnabled: false,
    localDiagnosticsEnabled: false,
    probeResult: null,
  });

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

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

      const selectedId = window.localStorage?.getItem("browse_selected_host_id");
      if (!selectedId && loopbackProfileRef.current) {
        applyState(loopbackProfileRef.current, true);
        return;
      }

      if (sameOriginDiagnosticsRef.current !== null) {
        applyState(h, sameOriginDiagnosticsRef.current);
        return;
      }

      applyState(h, false);

      // Hosted static origins (e.g. Firebase, Vercel, GitHub Pages) have no
      // same-origin backend — skip the /healthz probe to avoid doomed 404s.
      // Instead, probe loopback candidates for a local backend (issue #49).
      if (!isLocalOrigin(window.location.origin)) {
        sameOriginDiagnosticsRef.current = false;
        // Probe loopback only once per component lifecycle, and only when no
        // explicit remote host is selected.
        if (!loopbackProbeStartedRef.current) {
          const selectedIdAtStart = window.localStorage?.getItem("browse_selected_host_id");
          if (!selectedIdAtStart) {
            loopbackProbeStartedRef.current = true;
            void probeLocalBootstrap().then((result) => {
              if (!mountedRef.current) return;
              // Guard: abort if the user has explicitly selected a host since
              // the probe started — never override an explicit selection.
              const selectedIdNow = window.localStorage?.getItem("browse_selected_host_id");
              if (selectedIdNow) return;

              if (result.status === "detected") {
                const detectedProfile: HostProfile = {
                  id: "local-bootstrap",
                  label: "Local backend (auto-detected)",
                  base_url: result.url,
                  token: "",
                  cli_kind: "copilot",
                  is_default: false,
                };
                loopbackProfileRef.current = detectedProfile;
                setState({
                  host: detectedProfile,
                  diagnosticsEnabled: true,
                  localDiagnosticsEnabled: false,
                  probeResult: result,
                });
              } else {
                // auth-required or unavailable: expose probe result for DiagnosticPanel.
                setState((prev) => ({ ...prev, probeResult: result }));
              }
            });
          }
        }
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
