/**
 * direct-fetch.ts — narrow fetch wrapper for Next.js server-side broker calls.
 *
 * ## Why this exists
 *
 * When an operator configures a corporate proxy tool (e.g. global-agent,
 * undici EnvHttpProxyAgent, or a custom setGlobalDispatcher call) the undici
 * global dispatcher is replaced with a proxy-routing dispatcher.  Node.js
 * native `fetch` (backed by undici) then routes ALL outbound requests through
 * that proxy — including broker relay calls that must reach the broker directly.
 *
 * This module exports `directFetch` — a fetch wrapper that attaches a dedicated
 * `undici.Agent` as the per-request `dispatcher`, overriding any proxy-aware
 * global dispatcher for broker calls only. All other `fetch` calls in browse-ui
 * continue to honour whatever global dispatcher is configured.
 *
 * ## Bypass mechanism (proven in direct-fetch.test.ts)
 *
 * When undici's `fetch()` receives a `dispatcher` option it uses that
 * dispatcher for the request instead of `getGlobalDispatcher()`.  By supplying
 * a plain `undici.Agent` we guarantee the broker call goes direct regardless of
 * whether `EnvHttpProxyAgent` or any other proxy agent is the global default.
 *
 * ```
 * // proxy-aware global dispatcher active (HTTPS_PROXY=http://127.0.0.1:9999)
 * await fetch(brokerUrl)        // → ECONNREFUSED at proxy — blocked
 * await directFetch(brokerUrl)  // → direct connection — bypassed ✓
 * ```
 *
 * ## Bypass rules
 *
 * - **Node.js (server):** attaches a fresh `undici.Agent` as `dispatcher`
 *   so the global dispatcher (proxy) is bypassed. Only used by broker-client.ts.
 * - **Browser:** returns the global `fetch` unchanged. Browsers have no
 *   `HTTPS_PROXY` concept — system proxies are transparent to browser web APIs.
 * - **Edge runtime / no undici:** falls back to the global `fetch` (try/catch).
 *
 * ## ⚠️ Usage constraint
 *
 * Import `directFetch` **ONLY** from `lib/api/broker-client.ts`.
 * Default `fetch` everywhere else in browse-ui must continue to honour system
 * proxy settings (Copilot SDK calls, MCP, npm — all rely on the proxy).
 *
 * See also: docs/BROWSE-UI-NETWORKING.md §3 (Proxy bypass rules).
 */

/** Lazily-built fetch wrapper so the module is safe in all environments. */
let _cachedFetch: typeof fetch | undefined;

function buildDirectFetch(): typeof fetch {
  if (_cachedFetch) return _cachedFetch;

  // Browser context — no proxy bypass needed.
  if (typeof window !== "undefined") {
    _cachedFetch = fetch;
    return _cachedFetch;
  }

  // Node.js server context — attach an undici Agent as per-request dispatcher.
  //
  // undici's fetch() uses the per-request `dispatcher` option in preference to
  // getGlobalDispatcher().  A plain Agent routes direct, bypassing any
  // proxy-aware global dispatcher (e.g. EnvHttpProxyAgent set by global-agent).
  //
  // We import both `fetch` and `Agent` from the same undici package to avoid
  // the version-mismatch error ("invalid onRequestStart method") that occurs
  // when passing an undici@8 Agent to Node.js 24's internal undici fetch.
  // Using undici's own fetch guarantees the handler interface is consistent.
  try {
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    const { Agent, fetch: undiciFetch } = require("undici") as typeof import("undici");
    const directAgent = new Agent();
    // Cast via `unknown` to bridge the undici.Response / DOM Response type gap.
    // The actual runtime objects are structurally compatible — this is purely a
    // TypeScript declaration mismatch between undici@8 and the tslib DOM types.
    _cachedFetch = ((input: RequestInfo | URL, init?: RequestInit) =>
      undiciFetch(
        input as Parameters<typeof undiciFetch>[0],
        {
          ...init,
          dispatcher: directAgent,
        } as Parameters<typeof undiciFetch>[1]
      )) as unknown as typeof fetch;
  } catch {
    // Edge runtime or environment without the undici module —
    // fall back to the global fetch.
    _cachedFetch = fetch;
  }

  return _cachedFetch!;
}

/**
 * A fetch wrapper that bypasses `HTTPS_PROXY` for broker relay calls only.
 *
 * In Node.js (Next.js API routes / SSR): uses a dedicated `https.Agent` as the
 * undici dispatcher so broker endpoints are reached directly, independent of
 * any proxy configuration in the environment.
 *
 * In browsers: identical to the global `fetch` — no special behaviour needed.
 *
 * **Use ONLY from `lib/api/broker-client.ts`.** Do not use this for general API
 * calls — those must honour system proxy settings.
 */
export const directFetch: typeof fetch = (input, init) => buildDirectFetch()(input, init);
