/**
 * host-profiles.ts — client-side persistence for operator host profiles.
 *
 * Profiles are stored in localStorage. The LOCAL_HOST sentinel ("local") is
 * the built-in same-origin default; it cannot be saved or deleted via these helpers.
 */

import { hostProfileSchema } from "@/lib/api/schemas";
import type { HostProfile } from "@/lib/api/types";

export const LOCAL_HOST_ID = "local";

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
 */
export function saveHostProfile(profile: HostProfile): void {
  if (typeof window === "undefined") return;
  if (profile.id === LOCAL_HOST_ID) return;
  const validated = hostProfileSchema.parse(profile);
  const existing = getHostProfiles().filter((p) => p.id !== validated.id);
  localStorage.setItem(PROFILES_STORAGE_KEY, JSON.stringify([...existing, validated]));
}

/**
 * Deletes a host profile by id.
 * The LOCAL_HOST sentinel is silently ignored.
 * If the deleted profile was selected, the selection is cleared.
 */
export function deleteHostProfile(id: string): void {
  if (typeof window === "undefined") return;
  if (id === LOCAL_HOST_ID) return;
  const remaining = getHostProfiles().filter((p) => p.id !== id);
  localStorage.setItem(PROFILES_STORAGE_KEY, JSON.stringify(remaining));
  if (getSelectedHostId() === id) {
    clearSelectedHostId();
  }
}

/** Returns the currently selected host id, or null when none is explicitly set. */
export function getSelectedHostId(): string | null {
  if (typeof window === "undefined") return null;
  return localStorage.getItem(SELECTED_ID_STORAGE_KEY);
}

/** Persists the selected host id. */
export function setSelectedHostId(id: string): void {
  if (typeof window === "undefined") return;
  localStorage.setItem(SELECTED_ID_STORAGE_KEY, id);
}

/** Clears the selected host id so getEffectiveHost() falls back to LOCAL_HOST. */
export function clearSelectedHostId(): void {
  if (typeof window === "undefined") return;
  localStorage.removeItem(SELECTED_ID_STORAGE_KEY);
}

/**
 * Returns the active HostProfile.
 *
 * - If a non-local host id is stored and found in saved profiles, returns it.
 * - Otherwise returns LOCAL_HOST.
 */
export function getEffectiveHost(): HostProfile {
  const selectedId = getSelectedHostId();
  if (!selectedId || selectedId === LOCAL_HOST_ID) return LOCAL_HOST;
  const profile = getHostProfiles().find((p) => p.id === selectedId);
  return profile ?? LOCAL_HOST;
}
