/**
 * local-bootstrap.ts — probe loopback candidates for a local CLI backend.
 *
 * Implements the `/.well-known/browse-host` discovery protocol (issue #49).
 * Probes `http://127.0.0.1:8765` then `http://localhost:8765` with a short
 * timeout. Uses an in-memory negative cache to avoid probe storms.
 */

import { browseHostBootstrapSchema } from "@/lib/api/schemas";
import type { BrowseHostBootstrapResponse } from "@/lib/api/types";

/** Loopback candidates probed in order — 127.0.0.1 first per issue #49. */
const LOOPBACK_CANDIDATES = ["http://127.0.0.1:8765", "http://localhost:8765"] as const;

const WELL_KNOWN_PATH = "/.well-known/browse-host";

/** Probe timeout in ms — fast enough to not block UI rendering. */
const PROBE_TIMEOUT_MS = 3000;

/** Negative cache duration in ms (5 minutes). Prevents probe storms. */
const NEGATIVE_CACHE_DURATION_MS = 5 * 60 * 1000;

/** In-memory negative cache expiry. 0 = no active negative cache. */
let negativeCacheUntil = 0;

export type LocalBootstrapResult =
  /** Probe succeeded; backend is open-auth or no token required yet. */
  | { status: "detected"; url: string; response: BrowseHostBootstrapResponse }
  /** Probe succeeded; backend is present but requires a manual token from the user. */
  | { status: "auth-required"; url: string; response: BrowseHostBootstrapResponse }
  /** All candidates failed; negative cache applied. */
  | { status: "unavailable" }
  /** Skipped due to active negative cache. */
  | { status: "cached-negative" };

/**
 * Probes loopback candidates for a local backend discovery response.
 *
 * Returns the first successful result, or `"unavailable"` / `"cached-negative"`.
 * On total failure a 5-minute negative cache is applied.
 *
 * Safe to call from a hosted (HTTPS) origin — requires the backend to be
 * started with `--hosted-bootstrap` so CORS/PNA headers are set correctly.
 * Browsers that do not support Private Network Access (PNA) will silently
 * reject the request; the result will be `"unavailable"`.
 */
export async function probeLocalBootstrap(): Promise<LocalBootstrapResult> {
  if (Date.now() < negativeCacheUntil) {
    return { status: "cached-negative" };
  }

  for (const baseUrl of LOOPBACK_CANDIDATES) {
    try {
      const url = `${baseUrl}${WELL_KNOWN_PATH}`;
      const response = await fetch(url, {
        method: "GET",
        signal: AbortSignal.timeout(PROBE_TIMEOUT_MS),
        cache: "no-store",
      });

      if (!response.ok) continue;

      const raw: unknown = await response.json();
      const parsed = browseHostBootstrapSchema.safeParse(raw);
      if (!parsed.success) continue;

      const data = parsed.data;

      // Manual-token case: backend present but requires user-supplied token.
      // Do NOT invent or store a blank token as authenticated.
      if (data.auth === "token" && data.manual_token_required) {
        return { status: "auth-required", url: baseUrl, response: data };
      }

      return { status: "detected", url: baseUrl, response: data };
    } catch {
      // Network error, AbortError (timeout), parse failure — try next candidate.
    }
  }

  negativeCacheUntil = Date.now() + NEGATIVE_CACHE_DURATION_MS;
  return { status: "unavailable" };
}

/**
 * Resets the in-memory negative cache so the next call to
 * `probeLocalBootstrap()` performs a fresh probe.
 *
 * Call this when the user explicitly triggers "Detect local backend"
 * so they get a live result rather than a cached failure.
 */
export function resetLocalBootstrapCache(): void {
  negativeCacheUntil = 0;
}
