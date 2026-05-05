# Hosted Shell Architecture

> Design specifications for the hosted browse-UI operating as a remote shell over Copilot CLI
> backends. Covers issues **#36** (localhost bootstrap), **#37** (same-origin relay), and **#41**
> (capability/version negotiation).
>
> **Facts** = verified, reproducible. **Interpretation** = qualified inference. **Actions** =
> executable next steps. **Verification evidence** = command or file ref that proves the claim.

---

## 1. Localhost Auto-Discovery / Bootstrap (#36)

### 1.1 Context

**Facts:**
- The hosted UI (`https://agents.linhngo.dev`) is served over HTTPS. Browsers
  (Chrome 94+, Firefox 96+, Safari 15.2+) block mixed-content requests to `http://127.x.x.x`
  from a secure origin unconditionally — this is not a CORS issue; the request is cancelled
  before it leaves the browser.
- `browse-ui/src/lib/host-profiles.ts · checkHostCompatibility()` already detects this case and
  returns `{ compatible: false, code: "mixed-content-loopback" }`.
  _Evidence: `grep -n "mixed-content-loopback" browse-ui/src/lib/host-profiles.ts`_
- `browse-ui/src/providers/host-provider.tsx` skips the `/healthz` probe when
  `isLocalOrigin(window.location.origin)` is false (i.e. on the hosted origin).
  _Evidence: `grep -n "isLocalOrigin" browse-ui/src/providers/host-provider.tsx`_
- `browse.py` currently has no HTTPS loopback mode.

**Interpretation:** Silent background probing of `http://localhost` from HTTPS is permanently
blocked by browsers; a trusted-cert path is the only reliable solution for direct
hosted→local connectivity.

### 1.2 Recommended Bootstrap Design

```
Hosted UI (HTTPS)
      │
      │  probe: GET https://127.0.0.1:8766/healthz
      │  (explicit "Detect local backend" button only — no silent background probe)
      │
      ▼
browse.py  ←  mkcert-issued cert  ←  installer provisions trust anchor
  :8765 (HTTP, same-origin default)
  :8766 (HTTPS, loopback companion, optional)
```

**Probe endpoint contract:**

| Endpoint | Method | Auth | Response |
|---|---|---|---|
| `https://127.0.0.1:8766/healthz` | GET | None (probe only) | `200 {"status":"ok","version":"<semver>","protocol_version":<int>}` |
| `https://127.0.0.1:8766/api/operator/capabilities` | GET | `Authorization: Bearer <token>` | See §3 |

**Port discovery/collision rules:**
1. Default probe port: `8766` (HTTPS companion), fallback `8765` (HTTP, same-origin only).
2. If `8766` is occupied, `browse.py --https-port=<n>` overrides; the UI lets the user enter a
   custom port in the "Detect local backend" sheet.
3. Ports below 1024 are forbidden. Ports `8765–8769` are reserved for browse companions.

**Origin-policy bootstrap:**
- The HTTPS loopback cert is provisioned by the installer via `mkcert` (or equivalent local CA).
- The browser must have the local CA in its trust store; `install.py` adds it via `mkcert
  -install` (macOS/Linux/Windows).
- A future `browse.py --install-cert` sub-command wraps this for post-install operators.

**Cross-platform install path:**

| OS | CA store command | Cert output path |
|---|---|---|
| macOS | `mkcert -install` | `~/.local/share/mkcert/` |
| Linux | `mkcert -install` (adds to `nssdb`) | `~/.local/share/mkcert/` |
| Windows | `mkcert -install` (user cert store) | `%APPDATA%\mkcert\` |

**Mixed-content rules for SSE/WebSocket:**
- `EventSource` and `fetch`-streaming from HTTPS → `https://127.x.x.x` are allowed when the
  cert is trusted.
- `ws://` from HTTPS is blocked; use `wss://` on the loopback companion port.
- `http://localhost` (not `127.x.x.x`) gets a temporary browser exception in some builds; do
  not rely on it — always use the HTTPS path.

**Negative-cache TTL and failure/manual-add UX:**
- If probe returns non-2xx or times out (3 s), cache the failure for 30 s before re-probing.
- After 2 failed probes, surface the "Add host manually" sheet automatically.
- The sheet pre-fills `https://127.0.0.1:8766` as the suggested URL.
- On persistent failure, show actionable copy: _"No local backend detected. Install browse.py
  and run `python3 ~/.copilot/tools/browse.py --https` to enable local access."_

**Liveness vs version probe:**
- `/healthz` = liveness only (no auth, <50 ms, no DB access). Returns `{"status":"ok","protocol_version":N}`.
- `/api/operator/capabilities` = version + feature flags (requires Bearer token). Used after
  the user enters their token in the Add Host sheet.

### 1.3 Actions

1. **Add `--https` / `--https-port` flags to `browse.py`** for loopback TLS.
   `gh issue view 36` → link implementation to this issue.
2. **Extend `install.py`** with `--install-cert` that invokes `mkcert -install`.
   Target file: `install.py · provision_local_cert()`.
3. **Update `browse/core/auth.py · check_origin()`** to accept `https://` when `X-Forwarded-Proto:
   https` is present (fixes Mode 1 remote CORS issue noted in ARCHITECTURE.md).
4. **Add explicit "Detect local backend" button** to the Settings hosts sheet.
   Calls `https://127.0.0.1:8766/healthz` with a 3 s timeout.
   File: `browse-ui/src/components/settings/HostManagement.tsx`.
5. **Wire `checkHostCompatibility()` result** into the Add Host form to show the
   `mixed-content-loopback` warning when the user enters an `http://localhost` URL.

---

## 2. Same-Origin Relay / Gateway Architecture (#37)

### 2.1 Context

**Facts:**
- Remote host mode today routes every API call cross-origin from the browser.
- `use-operator-stream.ts` uses `fetch`-streaming with `Authorization: Bearer` header for remote
  hosts; this avoids `?token=` in URLs but is still cross-origin.
  _Evidence: `grep -n "Authorization" browse-ui/src/components/chat/use-operator-stream.ts`_
- CORS preflight adds one round-trip per request type; streaming is unaffected after initial
  preflight but is sensitive to tunnel idle-timeout resets.
- Tokens in `host-profiles.ts` are stored in `localStorage` (see §#42 in HOSTED-SHELL-RESEARCH.md).

**Interpretation:** A same-origin relay/BFF would eliminate browser-side CORS and token-storage
concerns but introduces a server that must be reliably available and adds deployment complexity.
A local gateway companion is a lighter path that keeps the browser direct while removing
token-in-browser-memory risk.

### 2.2 Recommended Architecture

**Short-term (ship without relay):** keep browser-direct cross-origin calls but:
- Tokens move from `localStorage` to non-extractable `IndexedDB` + `sessionStorage` (see §#42
  in HOSTED-SHELL-RESEARCH.md).
- Reconnect cursor (Last-Event-ID) lives in browser memory (see §#43 in HOSTED-SHELL-RESEARCH.md).
- Capability skew handled by §3 protocol negotiation.

**Medium-term (local gateway companion):**
```
Hosted UI (HTTPS, Firebase)
      │  same-origin calls → /api/*
      ▼
Local Gateway (browse.py --gateway, 127.0.0.1:8767)
      │  tunnels through Cloudflare to remote host(s)
      ▼
Remote browse.py (on operator machine)
```
- The local gateway owns the remote token; the browser never sees it.
- All calls from hosted UI to local gateway are `https://127.0.0.1:8767` (loopback HTTPS from
  §1).
- Gateway registers remote hosts via `POST /api/gateway/register { label, tunnel_url, token }`.
- SSE/streaming passes through the gateway as chunked HTTP; the gateway rewrites
  `Last-Event-ID` headers and cursor position.

**Streaming pass-through cost/backpressure:**
- Gateway MUST NOT buffer full SSE stream; it must pipe bytes with ≤100 ms head-of-line
  latency.
- Backpressure: if browser read-side falls behind, gateway pauses the upstream read (standard
  Go/Python async socket flow); upstream backpressure signal propagates naturally.
- Maximum in-flight buffer: 64 KiB per connection before gateway emits a synthetic
  `event: flow-pause` heartbeat to signal the browser.

**Auth-token storage on relay:**
- The local gateway stores remote tokens in the OS keychain via `keyring` (Python stdlib
  replacement: `keyring` package is allowed as it is optional; fall back to `~/.copilot/
  hosts-credentials.json` with `chmod 600`).
- The gateway issues a short-lived (15 min) session cookie to the browser for same-origin
  requests; the browser never holds the remote token.

**End-to-end auth flow:**
1. User adds remote host URL in UI → POST to local gateway.
2. Gateway probes remote capabilities, stores token in keychain, returns session cookie.
3. Browser uses session cookie for all subsequent gateway calls.
4. Gateway forwards `Authorization: Bearer <remote-token>` upstream.

**Failure semantics:**
- Gateway unreachable: UI surfaces "Local gateway not running" banner with restart command.
- Remote host unreachable: gateway returns `502`; UI shows reconnect banner.
- Token expired/revoked: gateway returns `401`; UI opens re-auth sheet.

**Local gateway companion install/update/auto-launch:**
- `install.py --gateway` installs a launchd/systemd unit for `browse.py --gateway`.
- `auto-update-tools.py` restarts the gateway unit after updates (same pattern as watcher).
- Auto-launch: the hosted UI detects absence of gateway at `/api/gateway/health`; if not found,
  prompts to run `python3 ~/.copilot/tools/browse.py --gateway`.

**Interaction with stream reconnect cursor (see §#43 in HOSTED-SHELL-RESEARCH.md):**
- The browser sends `Last-Event-ID` on reconnect.
- The gateway forwards this header upstream; the remote host replays from that cursor.
- If replay is unavailable (cursor expired), gateway returns `410 Gone`; browser resets to zero.

### 2.3 Actions

1. **Short-term:** move token storage to `IndexedDB` (Issue #42). No relay needed yet.
2. **Medium-term:** add `--gateway` mode to `browse.py`.
   Target: `browse/core/gateway.py` (new file).
3. **Add `GET /api/gateway/health`** to the gateway for hosted-UI probe.
4. **Update `install.py`** with `--gateway` flag to register launchd/systemd unit.
5. **Document gateway topology** in `docs/OPERATOR-PLAYBOOK.md` (separate PR).
6. **Run `gh issue comment 37 --body "Relay spec committed to docs/HOSTED-SHELL-ARCHITECTURE.md"`**
   after merging this file.

---

## 3. Capability + Protocol Version Negotiation (#41)

### 3.1 Context

**Facts:**
- `GET /api/operator/capabilities` returns `{ cli_kind, version, supported_modes,
  supported_features }`. No `protocol_version` field exists today.
  _Evidence: `grep -n "hostCapabilitiesSchema" browse-ui/src/lib/api/schemas.ts`_
- `useHostCapabilities` in `hooks.ts` fetches this on mount; it does not guard against version
  skew.
- There is no `min_ui_version` or `min_backend_version` field.

### 3.2 Compatibility Policy

**Version semantics:**
- `protocol_version` is a monotonically increasing integer. Current value: `1`.
- `min_ui_build` (optional, semver): minimum browse-ui build the backend requires.
- `min_backend_build` (optional, semver): minimum backend the UI declares it requires.

**Skew rules:**

| Condition | UI behaviour |
|---|---|
| `protocol_version` absent | Treat as `1`; all features enabled. |
| UI knows protocol `N`, backend reports `M < N` | Disable features that require `>M`. Show "Update backend for full features" banner (dismissible). |
| Backend reports `M > N` (UI is older) | Accept unknown fields silently (forward-compat). Show "Update UI" banner if `min_ui_build` is present and current build is older. |
| `min_ui_build` present and current build older | Block deep navigation; show "Please reload — a newer app is available" modal. |

**Breaking vs additive changes:**
- Additive (new optional fields, new `supported_features` values): no `protocol_version` bump.
- Breaking (field renamed, required field removed, semantic change): bump `protocol_version`.
- First planned breaking change: `protocol_version: 2` when relay gateway is introduced.

**Where the version lives:** `GET /api/operator/capabilities` only — no `Server:` header, no
separate `/api/version` endpoint. One endpoint, one fetch.

**Schema change (draft):**

```typescript
// browse-ui/src/lib/api/schemas.ts — additions to hostCapabilitiesSchema
export const hostCapabilitiesSchema = z.object({
  cli_kind: cliKindSchema,
  version: z.string().nullable().optional(),
  supported_modes: z.array(z.string()),
  supported_features: z.array(z.string()),
  // NEW ↓
  protocol_version: z.number().int().positive().optional().default(1),
  min_ui_build: z.string().optional(),   // semver, e.g. "0.9.4"
  min_backend_build: z.string().optional(),
});

// browse-ui/src/lib/api/types.ts — additions to HostCapabilities
export interface HostCapabilities {
  cli_kind: CliKind;
  version?: string | null;
  supported_modes: string[];
  supported_features: string[];
  protocol_version: number;    // defaults to 1 when absent
  min_ui_build?: string;
  min_backend_build?: string;
}
```

**Python backend (`browse/api/operator.py`) — addition:**
```python
# GET /api/operator/capabilities response (additions)
{
  "protocol_version": 1,
  "min_ui_build": None,      # set when breaking change is deployed
  "min_backend_build": None,
}
```

**Auto-discovery integration (Issue #36):**
- `/healthz` returns `{ "status": "ok", "protocol_version": 1 }` (subset only, no features).
- Full capabilities require an authenticated call to `/api/operator/capabilities`.

### 3.3 Actions

1. **Add `protocol_version` to `hostCapabilitiesSchema` and `HostCapabilities`.**
   File: `browse-ui/src/lib/api/schemas.ts`, `browse-ui/src/lib/api/types.ts`.
2. **Add `protocol_version: 1` to `browse/api/operator.py` capabilities response.**
3. **Add version-skew banner component** in `browse-ui/src/components/` triggered by
   `useHostCapabilities` when `protocol_version < CURRENT_PROTOCOL_VERSION`.
4. **Add `protocol_version` to `/healthz` response** in `browse/core/server.py`.
5. **Update `hostCapabilitiesSchema.test.ts`** to cover the new optional fields and
   default-1 behaviour.
   ```bash
   cd browse-ui && pnpm test -- schemas
   ```
6. **Run `gh issue comment 41 --body "Spec committed; draft schema in docs/HOSTED-SHELL-ARCHITECTURE.md §3"`**

---

## Related Issues

| Issue | Status after this doc |
|---|---|
| #36 localhost bootstrap | Spec complete; actions listed in §1.3 |
| #37 same-origin relay | Spec complete; actions listed in §2.3 |
| #41 version negotiation | Spec complete + draft schema; actions listed in §3.3 |

**Cross-cutting issues closed by relay architecture (from #37 scope):**
- #27–#34 browser CORS/HTTPS failures: the short-term fix (HTTPS loopback cert) resolves
  loopback mixed-content. Relay eliminates remaining cross-origin issues medium-term.

## Changelog

| Date | Change |
|---|---|
| 2026-05-05 | Initial spec for #36, #37, #41 (hosted-research-specs tentacle) |
