/**
 * broker-client.ts — browse-ui client stubs for broker-mode calls (#63 / #65).
 *
 * The broker runtime backend is being implemented separately (issue #71).
 * This module exists to:
 *
 *   1. Centralise all broker-originated fetch calls so they use `directFetch`,
 *      bypassing `HTTPS_PROXY` for broker relay traffic only.
 *   2. Provide helper utilities for building broker-mode URLs and checking
 *      reachability before a profile is saved.
 *   3. Define a stable import surface that broker-runtime code (issue #71) can
 *      extend without touching other modules.
 *
 * When the broker runtime is ready, add the real relay-connect / relay-send
 * calls here alongside `directFetch` so the proxy-bypass rule stays centralised.
 */

import { directFetch } from "@/lib/http/direct-fetch";

/** Maximum time to wait for a broker connectivity probe (ms). */
const BROKER_PROBE_TIMEOUT_MS = 5_000;

/**
 * Probes whether a broker endpoint URL is routable from the current environment.
 *
 * Uses `directFetch` so the probe bypasses `HTTPS_PROXY` — if we used the
 * system-proxy-aware `fetch`, a proxy could make a blocked endpoint appear
 * reachable, giving a false positive.
 *
 * Returns `true` when the endpoint responds with any HTTP status code (any
 * response means the network path is open). Returns `false` on network errors
 * or timeouts.
 */
export async function probeBrokerReachability(brokerUrl: string): Promise<boolean> {
  try {
    const res = await directFetch(brokerUrl, {
      method: "HEAD",
      signal: AbortSignal.timeout(BROKER_PROBE_TIMEOUT_MS),
    });
    return res.status < 600;
  } catch {
    return false;
  }
}

/**
 * Builds a Telegram deep-link URL for one-tap broker pairing on the user's phone.
 *
 * Format: `https://t.me/<botName>?start=<pairingToken>`
 *
 * The returned URL is also suitable as the `base_url` for a broker-mode
 * `HostProfile` — it is a valid HTTPS URL that uniquely identifies the bot.
 */
export function getTelegramBotUrl(botName: string, pairingToken: string): string {
  const safeName = encodeURIComponent(botName.replace(/^@/, ""));
  const safeToken = encodeURIComponent(pairingToken);
  return `https://t.me/${safeName}?start=${safeToken}`;
}

/**
 * Returns a human-readable summary of a broker base_url for display in the
 * host row. Extracts the bot name from a `t.me` URL if present.
 */
export function describeBrokerUrl(baseUrl: string): string {
  try {
    const url = new URL(baseUrl);
    if (url.hostname === "t.me") {
      const botName = url.pathname.replace(/^\//, "");
      return botName ? `@${botName} (Telegram)` : "Telegram broker";
    }
  } catch {
    // Ignore parse error — fall through to default.
  }
  return baseUrl;
}
