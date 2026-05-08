# browse-ui Networking

> Last updated: 2025-07-14 (issues #63 / #65)

This document describes how `browse-ui` issues network requests and the rules
around proxy bypass, broker mode, and connection compatibility.

---

## 1. Default `fetch` behaviour

All API calls in `browse-ui` use the global `fetch` provided by the runtime:

- **Browser:** browser-managed fetch. System proxy is transparent — the browser
  handles proxy settings independently.
- **Node.js (Next.js API routes / SSR):** undici-backed fetch. Proxy tools such
  as `global-agent` work by calling `undici.setGlobalDispatcher(new EnvHttpProxyAgent())`,
  which causes all undici `fetch` calls to route through the proxy. **Node.js 24's
  native `fetch` does NOT automatically honour `HTTPS_PROXY` by itself**; the env
  var only takes effect when a proxy tool installs a global dispatcher. This means
  any outbound request from server-side code may be routed through a corporate proxy
  when such tools are present.

This is the correct default for most calls (Copilot SDK, MCP, npm registry,
operator API probes) and **must not be changed globally**.

---

## 2. Direct host probe

When validating a new remote host in `host-management.tsx`, the component calls
`probeRemoteHost()` (via `hostRequest`). This call uses the global `fetch` so
that standard CORS rules, browser policies, and any system proxy apply.

The probe target is `GET /api/operator/capabilities` — an authenticated route
that exercises the real CORS + auth path.

---

## 3. Proxy bypass rules (`directFetch`)

### 3.1 Why a bypass is needed

When an operator runs `next dev` or `next start` locally to test a hosted-shell
scenario, `HTTPS_PROXY` may route broker relay calls through a corporate proxy
that:

- blocks WebSocket / SSE protocol upgrades, or
- adds TLS interception that breaks E2EE broker connections.

### 3.2 Scope of the bypass

`lib/http/direct-fetch.ts` exports `directFetch`:

```ts
// Node.js: uses a dedicated undici Agent as per-request dispatcher — bypasses any
// global undici proxy dispatcher (e.g. EnvHttpProxyAgent set by corporate proxy tools).
// Browser: identical to the global fetch — no special behaviour needed.
export const directFetch: typeof fetch = (input, init) => ...;
```

**Import constraint:** `directFetch` must only be imported from
`lib/api/broker-client.ts`. All other `fetch` calls in `browse-ui` must use the
global `fetch`.

### 3.3 How the bypass works (Node.js)

```ts
import { Agent, fetch as undiciFetch } from "undici";
const directAgent = new Agent();
// Passed as `dispatcher` to undici — this per-request dispatcher overrides any
// global dispatcher (e.g. EnvHttpProxyAgent) for this specific request only.
// undici.fetch + undici.Agent from the same package avoids version-mismatch errors
// that arise when mixing Node.js 24's internal undici with an external undici Agent.
undiciFetch(url, { ...init, dispatcher: directAgent });
```

The `dispatcher` option is an undici extension to `RequestInit`. In browser builds
`buildDirectFetch()` is not called (falls back to `globalThis.fetch`), so no cast
is needed in production browser bundles.

### 3.4 CI validation

The proxy-bypass guarantee is proven by automated Vitest tests in
`src/lib/http/direct-fetch.test.ts` (`@vitest-environment node`):

1. **Behavioral proxy-bypass proof** — sets up a real local HTTP server and installs
   `EnvHttpProxyAgent` as the global undici dispatcher (simulating `HTTPS_PROXY`).
   Confirms that `directFetch` connects directly (HTTP 200) while plain `fetch`
   fails (`ECONNREFUSED`) because the global proxy dispatcher has no real proxy
   listener behind it.

2. **Browser isolation** — confirms that in a browser environment (`typeof window
   !== "undefined"`) `directFetch` falls back to `globalThis.fetch` with no
   undici dependency.

Run the proof locally:

```bash
cd browse-ui
pnpm test src/lib/http/direct-fetch.test.ts
# Expected: 4/4 pass — "proxy bypass" tests confirm direct connection survives
# an active global proxy dispatcher.
```

These tests run automatically in the `browse-ui` CI job (`pnpm test`) on every
push — no additional CI step or `HTTPS_PROXY` environment variable is needed.

---

## 4. Host compatibility (`HostCompatibility`)

`lib/host-profiles.ts` exports `checkHostCompatibility()` which returns a
discriminated union:

| Code | `compatible` | When |
|---|---|---|
| `"ok"` | `true` | Safe direct HTTPS or same-origin. |
| `"pna-required"` | `true` | HTTPS control plane → HTTP loopback (PNA gated). |
| `"mixed-content-http"` | `false` | HTTPS control plane → non-loopback HTTP. Hard block. |
| `"broker-required"` | `true` | `HostProfile.connectivity_mode === "broker"`. |

### `broker-required`

Returned when the `HostProfile` has `connectivity_mode: "broker"`. This signals
that no direct browser→backend connection is made; all traffic routes through
the broker relay. The UI should show relay-specific status (not a connectivity
error).

---

## 5. Broker mode (`connectivity_mode: "broker"`)

### 5.1 What it is

Broker mode enables operators on tunnel-hostile networks (FPT, corporate
firewalls that block ngrok/Cloudflare) to connect a hosted UI to a local CLI
backend through an outbound relay (Telegram bot, etc.).

Architecture: see `docs/HOSTED-SHELL-ARCHITECTURE.md §5`.

### 5.2 When to suggest it (§5.4 logic)

The UI recommends broker mode when:

1. The operator is on a hosted origin (`agents.linhngo.dev` etc.).
2. No explicit remote host is selected.
3. The local loopback probe failed (`probeLocalBootstrap` returned unavailable or
   browser blocked by PNA).
4. Troubleshooting confirms tunnel-hostile networking.

See `HOSTED-SHELL-ARCHITECTURE.md §5.4` for the full decision tree.

### 5.3 `HostProfile` schema

Broker profiles carry `connectivity_mode: "broker"` and use a Telegram deep-link
as `base_url` (valid HTTPS, uniquely identifies the bot):

```jsonc
{
  "id": "host-1234567890",
  "label": "@my_bot (Telegram broker)",
  "base_url": "https://t.me/my_bot?start=<pairingToken>",
  "token": "<pairingToken>",
  "cli_kind": "copilot",
  "is_default": false,
  "connectivity_mode": "broker"
}
```

### 5.4 localStorage migration

`connectivity_mode` is `optional` in `hostProfileSchema`. Profiles saved before
this field was introduced have `connectivity_mode: undefined`, which is treated
as `"direct"` throughout the codebase. No explicit migration step is needed.

### 5.5 Runtime status

The broker runtime itself (issue #71) is not yet implemented. `broker-client.ts`
provides:

- `probeBrokerReachability(url)` — HEAD probe using `directFetch`.
- `getTelegramBotUrl(botName, pairingToken)` — builds the deep-link URL.
- `describeBrokerUrl(baseUrl)` — human-readable summary for UI display.

---

## 6. Summary: which `fetch` for which call

| Call site | Function | Proxy behaviour |
|---|---|---|
| General API (`apiFetch`, `hostRequest`) | `fetch` | Respects `HTTPS_PROXY` |
| Remote host probe (`probeRemoteHost`) | `fetch` | Respects `HTTPS_PROXY` |
| Local bootstrap probe | `fetch` | Respects `HTTPS_PROXY` |
| Broker relay calls | `directFetch` | **Bypasses** `HTTPS_PROXY` |
