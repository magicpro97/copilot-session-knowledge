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
  | { compatible: false; code: "mixed-content-loopback"; reason: string };

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
 * Detects the mixed-content scenario where a secure HTTPS control plane tries
 * to reach an insecure HTTP local URL (`http://localhost`, `http://127.0.0.1`,
 * `http://0.0.0.0`, `http://[::1]`). Browsers unconditionally block such requests.
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

  // HTTPS control plane → insecure HTTP loopback: browsers block this outright.
  if (
    controlScheme === "https:" &&
    hostUrl.protocol === "http:" &&
    isLoopbackHostname(hostUrl.hostname)
  ) {
    return {
      compatible: false,
      code: "mixed-content-loopback",
      reason:
        `Cannot reach ${host.base_url} from a secure (HTTPS) origin — ` +
        "browsers block insecure loopback requests from HTTPS pages. " +
        "Expose your local server via an HTTPS tunnel (e.g. ngrok) and update the host URL.",
    };
  }

  return { compatible: true, code: "ok", reason: null };
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
 * - Remote host with an explicit base_url → safe, unless the browser would block the
 *   request due to a mixed-content loopback incompatibility detected by
 *   `checkHostCompatibility`. On SSR (no `window`) the check is skipped (fail-open).
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
