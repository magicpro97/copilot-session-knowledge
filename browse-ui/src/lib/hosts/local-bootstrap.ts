/**
 * local-bootstrap.ts — probe loopback candidates for a local CLI backend.
 *
 * Implements the `/.well-known/browse-host` discovery protocol (issue #49).
 * Probes `http://127.0.0.1:8765` then `http://localhost:8765` with a short
 * timeout. Uses an in-memory negative cache to avoid probe storms.
 */

import { browseHostBootstrapSchema } from "@/lib/api/schemas";
import type { BrowseHostBootstrapResponse } from "@/lib/api/types";

/** Returns true when the page is served from an HTTPS origin (e.g. the hosted
 *  shell at agents.linhngo.dev). From hosted HTTPS we must NOT probe `http://`
 *  loopback URLs:
 *  - Chrome LNA/PNA blocks HTTP→loopback from HTTPS pages, so HTTP probes
 *    cannot succeed — they only produce console/network spam (#517).
 *  - Even if a future policy/browser permitted it, downgrading from hosted
 *    HTTPS to plaintext HTTP would create a trust downgrade and local
 *    impersonation risk. */
function isSecureOrigin(): boolean {
  return typeof window !== "undefined" && window.location.protocol === "https:";
}

/** Loopback candidates ordered by origin protocol:
 *  - From HTTPS origins (hosted shell): HTTPS-only. No HTTP fallback (#517).
 *  - From HTTP/file origins (local dev): HTTP first — Chrome Enterprise Policy
 *    `InsecurePrivateNetworkRequestsAllowedForUrls` exempts HTTP→loopback. */
function getLoopbackCandidates(): readonly string[] {
  return isSecureOrigin()
    ? ["https://127.0.0.1:8765", "https://localhost:8765"]
    : [
        "http://127.0.0.1:8765",
        "http://localhost:8765",
        "https://127.0.0.1:8765",
        "https://localhost:8765",
      ];
}

const WELL_KNOWN_PATH = "/.well-known/browse-host";

/** Probe timeout in ms — fast enough to not block UI rendering. */
const PROBE_TIMEOUT_MS = 3000;

/** Negative cache duration in ms (30 seconds). Short enough to retry quickly
 * when the backend starts after page load, long enough to avoid probe storms. */
const NEGATIVE_CACHE_DURATION_MS = 30 * 1000;

/** In-memory negative cache expiry. 0 = no active negative cache. */
let negativeCacheUntil = 0;

/**
 * Per-candidate failure reason recorded when a loopback probe attempt fails.
 *
 * - `"timeout"` — request was aborted by AbortSignal.timeout (backend not responding).
 * - `"network-error"` — fetch threw a TypeError/network error; common causes are CORS
 *   preflight rejection or Private Network Access (PNA) block by the browser.
 * - `"bad-status"` — backend responded but with a non-ok HTTP status code.
 * - `"parse-error"` — response body did not match the expected browse-host/1 schema.
 */
export type ProbeFailureReason = "timeout" | "network-error" | "bad-status" | "parse-error";

/**
 * Inferred daemon running state used to distinguish failure sub-types.
 *
 * After a `network-error` on the PNA-gated probe, a secondary no-cors probe is
 * attempted against the base URL. The result indicates:
 * - `"not-running"` — secondary probe also failed (connection refused; daemon is down).
 * - `"running-no-pna"` — secondary probe succeeded (server is up, but PNA/CORS headers
 *   are missing — daemon needs to be restarted with `--hosted-bootstrap`).
 * - `"unknown"` — secondary probe was inconclusive (e.g. another network error).
 *
 * Note: in Chrome 126+ / LNA, the secondary no-cors probe may also be blocked for
 * loopback addresses from a public HTTPS origin, making "unknown" the likely result
 * on modern Chrome without LNA permission. Even so, the guidance surfaced to the user
 * covers both "start daemon" and "restart with --hosted-bootstrap".
 */
export type DaemonState = "not-running" | "running-no-pna" | "unknown";

/** Records the failure details for a single loopback candidate probe attempt. */
export interface ProbeAttempt {
  url: string;
  reason: ProbeFailureReason;
  /**
   * Inferred daemon running state. Only populated when reason === "network-error"
   * on an HTTPS-to-loopback probe where a secondary no-cors check was attempted.
   */
  daemonState?: DaemonState;
}

export type LocalBootstrapResult =
  /** Probe succeeded; backend is open-auth or no token required yet. */
  | { status: "detected"; url: string; response: BrowseHostBootstrapResponse }
  /** Probe succeeded; backend is present but requires a manual token from the user. */
  | { status: "auth-required"; url: string; response: BrowseHostBootstrapResponse }
  /**
   * All candidates failed; negative cache applied.
   * `reasons` records per-candidate failure details for diagnostic surfaces.
   * Callers that only care about `status` can ignore `reasons` without breakage.
   */
  | { status: "unavailable"; reasons?: ProbeAttempt[] }
  /** Skipped due to active negative cache. */
  | { status: "cached-negative" };

/**
 * Probes loopback candidates for a local backend discovery response.
 *
 * Returns the first successful result, or `"unavailable"` / `"cached-negative"`.
 * On total failure a 5-minute negative cache is applied.
 *
 * Safe to call from a hosted (HTTPS) origin — the backend automatically
 * enables CORS/PNA headers when bound to loopback (no flags required).
 * Browsers that do not support Private Network Access (PNA) will silently
 * reject the request; the result will be `"unavailable"`.
 */
export async function probeLocalBootstrap(): Promise<LocalBootstrapResult> {
  if (Date.now() < negativeCacheUntil) {
    return { status: "cached-negative" };
  }

  const reasons: ProbeAttempt[] = [];

  for (const baseUrl of getLoopbackCandidates()) {
    try {
      const url = `${baseUrl}${WELL_KNOWN_PATH}`;
      const response = await fetch(url, {
        method: "GET",
        signal: AbortSignal.timeout(PROBE_TIMEOUT_MS),
        cache: "no-store",
        // Chrome 138+ Local Network Access (LNA): explicitly annotate this request as
        // targeting the loopback address space (127.0.0.1 / localhost) so Chrome
        // triggers the LNA permission flow instead of blocking with a mismatch error.
        // "loopback" is required — "local" (private-network, e.g. 192.168.x) is a
        // different address space and Chrome rejects the mismatch.
        // TypeScript lib.dom.d.ts does not yet include this option.
        // @ts-expect-error — targetAddressSpace is a Chrome LNA extension (Chrome 138+, stable Chrome 142+)
        targetAddressSpace: "loopback",
      });

      if (!response.ok) {
        reasons.push({ url: baseUrl, reason: "bad-status" });
        continue;
      }

      const raw: unknown = await response.json();
      const parsed = browseHostBootstrapSchema.safeParse(raw);
      if (!parsed.success) {
        reasons.push({ url: baseUrl, reason: "parse-error" });
        continue;
      }

      const data = parsed.data;

      // Manual-token case: backend present but requires user-supplied token.
      // Do NOT invent or store a blank token as authenticated.
      if (data.auth === "token" && data.manual_token_required) {
        return { status: "auth-required", url: baseUrl, response: data };
      }

      return { status: "detected", url: baseUrl, response: data };
    } catch (err) {
      // Distinguish timeout (AbortError) from network errors (CORS/PNA block, connection refused).
      const reason: ProbeFailureReason =
        err instanceof Error && err.name === "AbortError" ? "timeout" : "network-error";

      if (reason === "network-error") {
        // Secondary no-cors probe to distinguish "daemon not running" (connection refused)
        // from "daemon running but CORS/PNA preflight rejected" (e.g. old version without
        // auto-CORS). In Chrome 126+ / LNA the secondary may also be blocked, yielding "unknown".
        //
        // From hosted HTTPS origins we skip the secondary probe entirely (#517):
        //   - It produces a second visible LNA/PNA console error per candidate.
        //   - Chrome LNA blocks the no-cors probe from HTTPS too, so the result is
        //     always "unknown" → providing no diagnostic value, only noise.
        // The DiagnosticPanel renders the PNA-blocked panel when daemonState is
        // "unknown" on hosted origin, which is the correct guidance.
        let daemonState: DaemonState = "unknown";
        if (!isSecureOrigin()) {
          try {
            await fetch(`${baseUrl}/`, {
              mode: "no-cors",
              signal: AbortSignal.timeout(1_000),
              cache: "no-store",
            });
            // Any response (even opaque) means something is listening → CORS/PNA is the blocker.
            daemonState = "running-no-pna";
          } catch {
            // Connection refused or also blocked by LNA.
            daemonState = "not-running";
          }
        }
        reasons.push({ url: baseUrl, reason, daemonState });
      } else {
        reasons.push({ url: baseUrl, reason });
      }
    }
  }

  negativeCacheUntil = Date.now() + NEGATIVE_CACHE_DURATION_MS;
  return { status: "unavailable", reasons };
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
