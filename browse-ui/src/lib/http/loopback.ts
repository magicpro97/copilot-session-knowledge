/**
 * browse-ui/src/lib/http/loopback.ts
 *
 * Chrome Local Network Access (LNA) / Private Network Access (PNA) helpers
 * for HTTPS → loopback fetches (issue #517 follow-up).
 *
 * Chrome 138+ blocks fetches from an HTTPS page to a loopback target unless
 * the request explicitly opts in via the `targetAddressSpace: "loopback"`
 * RequestInit field. The local-bootstrap probe already does this, but the
 * normal API/SSE/pairing fetches did not — causing the hosted shell at
 * https://agents.linhngo.dev to fail with `ERR_PRIVATE_NETWORK_ACCESS_*`
 * even when the backend preflight is correct.
 *
 * This module centralizes:
 *   1. Safe detection of loopback URLs (no string-prefix spoofing).
 *   2. The TypeScript `@ts-expect-error` cast for the LNA RequestInit field,
 *      so call sites stay clean.
 *
 * The helper is a no-op in three cases:
 *   - SSR / non-browser contexts (no `window`).
 *   - HTTP page origins (LNA only gates HTTPS → loopback).
 *   - Non-loopback target URLs.
 */

/**
 * IPv4 loopback test: 127.0.0.0/8 — any address whose first octet is 127.
 * Accepts canonical dotted-quad. Rejects partial / non-quad forms because
 * those are not the addresses our backend binds to.
 */
function isLoopbackIPv4(hostname: string): boolean {
  const parts = hostname.split(".");
  if (parts.length !== 4) return false;
  for (const part of parts) {
    if (!/^\d+$/.test(part)) return false;
    const n = Number(part);
    if (!Number.isFinite(n) || n < 0 || n > 255) return false;
  }
  return Number(parts[0]) === 127;
}

/**
 * Returns true when the given URL targets the loopback address space
 * (localhost, IPv6 ::1, or IPv4 127.0.0.0/8).
 *
 * Uses URL parsing — string-prefix checks like `url.startsWith("http://127.0.0.1")`
 * are unsafe because `http://127.0.0.1.evil.com/` would pass them.
 *
 * Returns false for invalid URLs and for spoofed hostnames such as
 * `127.0.0.1.evil.com` (the IP check requires exactly four octets) or
 * `localhost.evil.com` (the hostname check requires an exact match).
 */
export function isLoopbackUrl(url: string | URL): boolean {
  let parsed: URL;
  try {
    parsed = typeof url === "string" ? new URL(url) : url;
  } catch {
    return false;
  }

  // URL exposes IPv6 hosts wrapped in brackets, e.g. "[::1]".
  let hostname = parsed.hostname;
  if (hostname.startsWith("[") && hostname.endsWith("]")) {
    hostname = hostname.slice(1, -1);
  }

  if (hostname === "localhost") return true;
  if (hostname === "::1") return true;
  return isLoopbackIPv4(hostname);
}

/** Returns true when running in a browser context with a window object. */
function inBrowser(): boolean {
  return typeof window !== "undefined" && typeof window.location !== "undefined";
}

/** Returns true when the current page is served over HTTPS. */
function pageIsHttps(): boolean {
  return inBrowser() && window.location.protocol === "https:";
}

/**
 * RequestInit extension used by Chrome's Local Network Access (LNA) feature.
 * Not yet present in lib.dom.d.ts; we declare a narrow shape so call sites
 * don't need their own `@ts-expect-error` annotations.
 */
type LoopbackRequestInit = RequestInit & {
  targetAddressSpace?: "loopback" | "local" | "private" | "public" | "unknown";
};

/**
 * Returns a RequestInit that preserves every field from `init` and, when the
 * runtime conditions match, adds Chrome's LNA `targetAddressSpace: "loopback"`
 * hint. The hint is only added when ALL of the following are true:
 *
 *   1. We are running in a browser (no SSR / no Node test runner).
 *   2. The current page protocol is `https:` (LNA only gates HTTPS pages).
 *   3. The target URL points at the loopback address space.
 *
 * For any other case the function returns `init` unchanged (or `{}` if init
 * was undefined) so callers can use it unconditionally.
 *
 * The Chrome RequestInit field is intentionally declared via a typed
 * extension so individual call sites do not need their own `@ts-expect-error`
 * annotations — this is the single point where the TypeScript workaround
 * lives.
 */
export function withLoopbackHint(url: string | URL, init?: RequestInit): RequestInit {
  const base: RequestInit = init ? { ...init } : {};

  if (!inBrowser()) return base;
  if (!pageIsHttps()) return base;
  if (!isLoopbackUrl(url)) return base;

  const hinted: LoopbackRequestInit = { ...base, targetAddressSpace: "loopback" };
  return hinted;
}
