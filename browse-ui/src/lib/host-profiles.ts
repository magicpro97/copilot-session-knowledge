/**
 * host-profiles.ts — client-side persistence for operator host profiles.
 *
 * Profiles are stored in localStorage. The LOCAL_HOST sentinel ("local") is
 * the built-in same-origin default; it cannot be saved or deleted via these helpers.
 *
 * Same-tab change notifications: call sites that mutate selection dispatch
 * BROWSE_HOST_CHANGE_EVENT on window so in-tab listeners (e.g. HostProvider)
 * update immediately without waiting for the next storage event (which only
 * fires cross-tab).
 */

import { hostProfileSchema } from "@/lib/api/schemas";
import type { HostProfile } from "@/lib/api/types";

// ── Compatibility ─────────────────────────────────────────────────────────────

/**
 * Structured result of a host compatibility check.
 * Prefer this over bare booleans so UI surfaces can display the actionable reason.
 */
export type HostCompatibility =
  | { compatible: true; code: "ok"; reason: null }
  /**
   * HTTPS control plane → HTTP loopback address.
   * Whether this works depends on browser Private Network Access (PNA) support:
   * - Chromium/Edge: supported when the backend is started with `--hosted-bootstrap`.
   * - Safari/Firefox: behaviour depends on their local-network/CORS policy; use
   *   an HTTPS tunnel if direct loopback is blocked.
   * `compatible: true` so the request is attempted; failures surface as API errors.
   */
  | { compatible: true; code: "pna-required"; reason: string }
  /** HTTPS control plane → non-loopback HTTP host. This is unsafe mixed content. */
  | { compatible: false; code: "mixed-content-http"; reason: string }
  /**
   * Broker relay mode: the operator has configured this host to be reached via
   * an outbound control-bus relay (e.g. Telegram bot, #65).
   *
   * `compatible: true` because broker-mode connections succeed — they are routed
   * through the relay service, not as direct browser→backend network requests.
   * The UI should surface relay-specific status and setup guidance rather than
   * standard connectivity error messages.
   *
   * Triggers when `host.connectivity_mode === "broker"`. Also returned by
   * `suggestBrokerFromProbeFail()` when a live network probe fails in a way that
   * indicates the network is tunnel-hostile (see §5.4 of
   * docs/HOSTED-SHELL-ARCHITECTURE.md).
   *
   * `recommendedBroker` names the relay integration the UI should guide the
   * operator through. Currently always `"telegram"` (the only shipped integration);
   * future values: `"discord"`, `"ably"`.
   */
  | {
      compatible: true;
      code: "broker-required";
      reason: string;
      recommendedBroker: "telegram" | "discord" | "ably";
    };

/** Local hostnames/addresses that browsers block from HTTPS origins when served over HTTP. */
const LOOPBACK_HOSTNAMES = new Set(["localhost", "127.0.0.1", "0.0.0.0", "[::1]"]);

/** Returns true when `hostname` is a loopback address (covers 127.x.x.x range too). */
function isLoopbackHostname(hostname: string): boolean {
  if (LOOPBACK_HOSTNAMES.has(hostname)) return true;
  return /^127\.\d{1,3}\.\d{1,3}\.\d{1,3}$/.test(hostname);
}

/**
 * Returns true when `origin` is a real local/loopback origin (e.g. `http://localhost:3000`,
 * `http://127.0.0.1:8080`). These origins serve a live backend so the same-origin
 * `/healthz` probe makes sense.
 *
 * Returns false for hosted static origins (e.g. `https://agents.example.web.app`) where
 * there is no backend process — those origins must NOT issue a doomed `/healthz` probe.
 */
export function isLocalOrigin(origin: string): boolean {
  try {
    const { hostname } = new URL(origin);
    return isLoopbackHostname(hostname);
  } catch {
    return false;
  }
}

/**
 * Determines whether the browser can safely reach `host` from `controlPlaneOrigin`.
 *
 * Detects insecure HTTP targets from a secure HTTPS control plane. HTTP
 * loopback can be attempted through browser Private Network Access (PNA) when
 * the local backend opts in with CORS/PNA headers. Non-loopback HTTP remains a
 * hard mixed-content failure and should be exposed through HTTPS instead.
 *
 * Compatible paths that are preserved:
 * - LOCAL_HOST (empty base_url) — always safe, same-origin request.
 * - Remote HTTPS tunnel hosts — secure on any control plane.
 * - HTTP loopback hosts from an HTTP control plane — local dev setup.
 * - Explicit API-base environments handled upstream (NEXT_PUBLIC_API_BASE).
 *
 * @param controlPlaneOrigin  The origin of the page hosting the browse UI
 *                            (e.g. `window.location.origin`).
 * @param host                The HostProfile to evaluate.
 */
export function checkHostCompatibility(
  controlPlaneOrigin: string,
  host: HostProfile
): HostCompatibility {
  // LOCAL_HOST sentinel (empty base_url) — same-origin, always compatible.
  if (!host.base_url) return { compatible: true, code: "ok", reason: null };

  // Broker-mode: operator has explicitly configured relay routing (#65).
  // Return broker-required so the UI shows relay status, not a connectivity error.
  // Direct browser→backend requests are not attempted for broker profiles.
  if (host.connectivity_mode === "broker") {
    return {
      compatible: true,
      code: "broker-required",
      reason:
        `${host.label || host.base_url} is configured for broker relay mode. ` +
        "Browser requests are routed through the relay — no direct browser→backend " +
        "connection is made. Ensure the broker agent is running on the host machine " +
        "and that the relay service is reachable. " +
        "See docs/HOSTED-SHELL-ARCHITECTURE.md §5.4 for setup guidance.",
      recommendedBroker: "telegram",
    };
  }

  let controlScheme: string;
  try {
    controlScheme = new URL(controlPlaneOrigin).protocol; // "https:" | "http:"
  } catch {
    // Unparseable origin — fail open; let the request attempt and surface its own error.
    return { compatible: true, code: "ok", reason: null };
  }

  let hostUrl: URL;
  try {
    hostUrl = new URL(host.base_url);
  } catch {
    // Malformed base_url — not a compatibility concern; validation handles this elsewhere.
    return { compatible: true, code: "ok", reason: null };
  }

  // HTTPS control plane → insecure HTTP loopback.
  // Chromium and Edge support Private Network Access (PNA), which allows HTTPS
  // pages to reach HTTP loopback addresses when the backend sets the required
  // CORS/PNA response headers (e.g. via --hosted-bootstrap).
  // Other engines may follow a different local-network/CORS policy; use an
  // HTTPS tunnel if direct loopback is blocked.
  // Return compatible:true so the request is attempted; browsers that block PNA
  // will surface a network error that the UI can handle gracefully.
  if (controlScheme === "https:" && hostUrl.protocol === "http:") {
    if (!isLoopbackHostname(hostUrl.hostname)) {
      return {
        compatible: false,
        code: "mixed-content-http",
        reason:
          `A secure (HTTPS) control plane cannot connect to insecure HTTP host ${host.base_url}. ` +
          "Expose the backend through HTTPS, or use loopback with --hosted-bootstrap.",
      };
    }
    return {
      compatible: true,
      code: "pna-required",
      reason:
        `Connecting from a secure (HTTPS) origin to ${host.base_url} requires ` +
        "browser Private Network Access (PNA) support. " +
        "This works in Chromium/Edge when the local backend is started with --hosted-bootstrap. " +
        "Other browsers may block direct loopback — use an HTTPS tunnel (e.g. ngrok) as a fallback.",
    };
  }

  return { compatible: true, code: "ok", reason: null };
}

/**
 * Returns a `broker-required` HostCompatibility suggestion when a live network
 * probe has failed in a way that suggests the network is tunnel-hostile.
 *
 * This is a companion to the synchronous `checkHostCompatibility()` — it
 * incorporates the result of an async probe (which that function cannot perform
 * itself) to decide whether broker relay mode should be recommended.
 *
 * Called by the Add-Host UI after `probeRemoteHost()` fails. The Broker Mode
 * tab and recommendation banner are shown only when this function returns a
 * non-null result, making the display diagnosis-driven rather than unconditional.
 *
 * @param targetUrl   - The URL that was probed (used in the human-readable reason).
 * @param isAuthError - True when the failure was a 401/403 (auth/CORS issue,
 *                      not a tunnel-hostile network block). Auth errors are not
 *                      tunnel-hostile, so broker mode is not suggested.
 * @returns A `broker-required` HostCompatibility or null when broker mode
 *          should not be recommended.
 */
export function suggestBrokerFromProbeFail(
  targetUrl: string,
  isAuthError: boolean
): HostCompatibility | null {
  // Auth errors (401/403) are token/CORS misconfigurations, not network blocks.
  // The operator should fix the auth token or CORS allowlist, not switch to broker.
  if (isAuthError) return null;

  const label = targetUrl || "the remote host";
  return {
    compatible: true,
    code: "broker-required",
    reason:
      `Direct connection to ${label} failed. ` +
      "Your network may block direct browser→backend connections (tunnel-hostile network). " +
      "Broker relay mode routes traffic through a relay service instead of a direct browser connection. " +
      "Run `python browse.py --broker-mode telegram` on the host machine, then configure it below. " +
      "See docs/HOSTED-SHELL-ARCHITECTURE.md §5.4 for full setup guidance.",
    recommendedBroker: "telegram",
  };
}

export const LOCAL_HOST_ID = "local";

/** Custom event dispatched in the same tab whenever the active host changes. */
export const BROWSE_HOST_CHANGE_EVENT = "browse:host-change";

/**
 * Built-in same-origin host profile. Used when no remote profile is selected.
 * Immutable sentinel — cannot be overwritten or deleted via the storage helpers.
 */
export const LOCAL_HOST: HostProfile = {
  id: LOCAL_HOST_ID,
  label: "Local (same-origin)",
  base_url: "",
  token: "",
  cli_kind: "copilot",
  is_default: true,
};

const PROFILES_STORAGE_KEY = "browse_host_profiles";
const SELECTED_ID_STORAGE_KEY = "browse_selected_host_id";

function notifyHostChange(): void {
  if (typeof window === "undefined") return;
  window.dispatchEvent(new Event(BROWSE_HOST_CHANGE_EVENT));
}

function writeProfiles(profiles: HostProfile[], notify = true): void {
  if (typeof window === "undefined") return;
  const validatedProfiles = profiles
    .filter((profile) => profile.id !== LOCAL_HOST_ID)
    .map((profile) => hostProfileSchema.parse(profile));
  localStorage.setItem(PROFILES_STORAGE_KEY, JSON.stringify(validatedProfiles));
  if (notify) notifyHostChange();
}

function loadRawProfiles(): unknown[] {
  if (typeof window === "undefined") return [];
  try {
    const raw = localStorage.getItem(PROFILES_STORAGE_KEY);
    if (!raw) return [];
    const parsed: unknown = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

/** Returns all saved remote host profiles (excludes the LOCAL_HOST sentinel). */
export function getHostProfiles(): HostProfile[] {
  return loadRawProfiles()
    .map((item) => {
      const result = hostProfileSchema.safeParse(item);
      return result.success ? result.data : null;
    })
    .filter((p): p is HostProfile => p !== null);
}

/** Returns all profiles including the LOCAL_HOST sentinel at index 0. */
export function getAllHostProfiles(): HostProfile[] {
  return [LOCAL_HOST, ...getHostProfiles()];
}

/**
 * Saves or updates a host profile.
 * The LOCAL_HOST sentinel (id === "local") is silently ignored.
 * Dispatches a same-tab change event so HostProvider refreshes immediately.
 */
export function saveHostProfile(profile: HostProfile): void {
  if (typeof window === "undefined") return;
  if (profile.id === LOCAL_HOST_ID) return;
  const validated = hostProfileSchema.parse(profile);
  const existing = getHostProfiles().filter((p) => p.id !== validated.id);
  writeProfiles([...existing, validated]);
}

/**
 * Deletes a host profile by id.
 * The LOCAL_HOST sentinel is silently ignored.
 * If the deleted profile was selected, the selection is cleared.
 * Dispatches a same-tab change event so HostProvider refreshes immediately.
 */
export function deleteHostProfile(id: string): void {
  if (typeof window === "undefined") return;
  if (id === LOCAL_HOST_ID) return;
  const remaining = getHostProfiles().filter((p) => p.id !== id);
  writeProfiles(remaining, false);
  if (getSelectedHostId() === id) {
    localStorage.removeItem(SELECTED_ID_STORAGE_KEY);
  }
  notifyHostChange();
}

/** Replaces all saved remote profiles in one storage write and same-tab event. */
export function replaceHostProfiles(profiles: HostProfile[]): void {
  writeProfiles(profiles);
}

/** Returns the currently selected host id, or null when none is explicitly set. */
export function getSelectedHostId(): string | null {
  if (typeof window === "undefined") return null;
  return localStorage.getItem(SELECTED_ID_STORAGE_KEY);
}

/** Persists the selected host id and notifies in-tab listeners. */
export function setSelectedHostId(id: string): void {
  if (typeof window === "undefined") return;
  localStorage.setItem(SELECTED_ID_STORAGE_KEY, id);
  notifyHostChange();
}

/** Clears the selected host id so getEffectiveHost() falls back to the default host. */
export function clearSelectedHostId(): void {
  if (typeof window === "undefined") return;
  localStorage.removeItem(SELECTED_ID_STORAGE_KEY);
  notifyHostChange();
}

/**
 * Returns the default host profile.
 *
 * - The first saved remote profile marked `is_default === true`, if any.
 * - Otherwise LOCAL_HOST.
 */
export function getDefaultHost(): HostProfile {
  return getHostProfiles().find((p) => p.is_default) ?? LOCAL_HOST;
}

/**
 * Returns the active HostProfile.
 *
 * Resolution order:
 * 1. Explicit selection stored in localStorage (if profile still exists).
 * 2. First remote profile with `is_default === true`.
 * 3. LOCAL_HOST sentinel.
 */
export function getEffectiveHost(): HostProfile {
  const selectedId = getSelectedHostId();
  if (!selectedId) return getDefaultHost();
  if (selectedId === LOCAL_HOST_ID) return LOCAL_HOST;
  const profile = getHostProfiles().find((p) => p.id === selectedId);
  return profile ?? getDefaultHost();
}

/**
 * Returns whether operator API calls are safe to issue for the current host.
 *
 * - Explicit API base configured at build time (NEXT_PUBLIC_API_BASE) → always safe.
 * - LOCAL_HOST (empty base_url) without NEXT_PUBLIC_API_BASE → not safe by default;
 *   configure a remote host or set NEXT_PUBLIC_API_BASE.
 * - Remote host with an explicit base_url → safe, unless `checkHostCompatibility`
 *   identifies a hard browser incompatibility. PNA-required loopback remains enabled so
 *   the browser can attempt the standards-based preflight.
 */
export function isOperatorHostEnabled(host: HostProfile, _pathname: string): boolean {
  void _pathname;
  // Build-time explicit API base always takes precedence.
  if (Boolean(process.env.NEXT_PUBLIC_API_BASE)) return true;
  // LOCAL_HOST (same-origin) has no remote base_url — not enabled without API base.
  if (!host.base_url) return false;
  // Remote host: verify the browser can actually reach it from this origin.
  if (typeof window !== "undefined") {
    const compat = checkHostCompatibility(window.location.origin, host);
    if (!compat.compatible) return false;
  }
  return true;
}
