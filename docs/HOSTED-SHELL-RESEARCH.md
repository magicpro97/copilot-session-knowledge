# Hosted Shell Research

> Design decisions and UX specifications for the hosted browse-UI. Covers issues **#42** (browser
> secret storage), **#43** (stream reconnect/replay), and **#44** (first-run / no-backend UX).
>
> **Facts** = verified, reproducible. **Interpretation** = qualified inference. **Actions** =
> executable next steps. **Verification evidence** = command or file ref that proves the claim.

---

## 1. Browser Secret Storage Model for Remote Host Tokens (#42)

### 1.1 Context

**Facts:**
- `browse-ui/src/lib/host-profiles.ts` stores host tokens in `localStorage` under
  `browse_host_profiles` (a JSON array). Token is a plain string in `profile.token`.
  _Evidence: `grep -n "token" browse-ui/src/lib/host-profiles.ts | head -10`_
- `localStorage` is synchronously readable by any JavaScript on the same origin, including XSS
  payloads and browser extensions with `host_permissions: ["https://agents.linhngo.dev/*"]`.
- Tokens have no expiry field; they persist until the user removes the host.
- The app is a static export (`output: "export"` in `next.config.ts`); there is no server-side
  session that could hold tokens instead.
  _Evidence: `grep -n "output" browse-ui/next.config.ts`_

### 1.2 Threat Model

| Threat | Likelihood | Impact | Mitigated by |
|---|---|---|---|
| XSS on hosted origin reads `localStorage` | Medium (any CDN script supply-chain) | High — full remote access to operator machine | Session-only storage, CSP, short TTL; true secrecy requires relay/gateway |
| Browser extension reads browser storage | Medium | High | Short TTL + fingerprint display; true secrecy requires relay/gateway |
| Shoulder-surfing / DevTools inspection | Low | Medium | Token display as fingerprint only |
| CSRF via stored token | Low (Bearer-only, no cookies) | Low | N/A |
| Token persists across browsers / devices | N/A (localStorage is local) | Low | N/A |

### 1.3 Recommended Storage Model

**Threat model conclusion (interpretation):** `localStorage` is acceptable for a low-value
short-term token (e.g. a read-only diagnostics key), but not for operator tokens that grant
full command execution over a machine. The product's goal of "add URL and go" implies
indefinite token persistence — this multiplies exposure.

**Recommended storage:**

```
Token lifecycle:
  1. User enters token in Add Host form.
  2. UI stores the token only for the current browser session.
  3. UI displays only a token fingerprint.
  4. On each API call, UI reads the token into memory and sends Authorization: Bearer.
```

**Important limitation (fact + interpretation):** a browser client that sends
`Authorization: Bearer <token>` must have access to the plaintext token at request time. WebCrypto
non-extractable keys can protect key material from direct export, but they do not make a Bearer
token XSS-proof once app JavaScript is allowed to decrypt/read/send it. IndexedDB encryption can
reduce casual disk/DevTools exposure, but it is not a substitute for keeping operator credentials
out of the browser. True non-browser token storage requires the relay/gateway model in §1.4.

**Practical short-term migration (two-phase):**

| Phase | What changes | Files |
|---|---|---|
| Phase 1 | Store token in `sessionStorage` instead of `localStorage`; display only a fingerprint (last-6 chars) to the user | `host-profiles.ts` |
| Phase 2 | Add a relay/gateway session so the browser never stores the operator token | gateway service + `host-profiles.ts` |

**Phase 1 is shippable in one PR** and immediately removes cross-session persistence of the
raw token. Phase 2 is a follow-up PR.

**Migration from `localStorage`:**
```typescript
// host-profiles.ts — migration on first load
const legacy = localStorage.getItem("browse_host_profiles");
if (legacy) {
  const profiles = JSON.parse(legacy);
  // move to sessionStorage, clear plaintext from localStorage
  sessionStorage.setItem("browse_host_profiles", legacy);
  // zero-out tokens before removing from localStorage
  const redacted = profiles.map((p: HostProfile) => ({ ...p, token: "" }));
  localStorage.setItem("browse_host_profiles", JSON.stringify(redacted));
}
```

**Token rotation/revocation:**
- Host removal (`deleteHostProfile()`) MUST clear any session-scoped token material for that
  profile.
- Rotation: user re-enters token in Settings → host edit sheet → old session token replaced.
- No server-initiated revocation path today (out of scope); a rotation endpoint on `browse.py`
  can be added later.

**CSP posture:**
The Firebase Hosting `firebase.json` should add a `Content-Security-Policy` header:
```
Content-Security-Policy:
  default-src 'self';
  script-src 'self' 'unsafe-eval';   ← Next.js requires unsafe-eval in dev; remove in prod
  connect-src 'self' https: wss:;
  object-src 'none';
  base-uri 'self';
```
`'unsafe-inline'` for scripts must be absent to make XSS exfiltration meaningfully harder.

**Raw token display policy:**
- Never render the raw token in the UI; show `••••••<last-6-chars>` only.
- Provide a one-click "Regenerate token" that calls `POST /api/operator/token/rotate`.

### 1.4 Relay interaction (#37)

If the local gateway model from HOSTED-SHELL-ARCHITECTURE.md §2 is adopted:
- Browser never holds the remote token at all; it holds only a short-lived (15 min) session
  cookie for the local gateway.
- This eliminates the entire `localStorage`/`IndexedDB` token exposure concern.
- **Phase 1 migration above is still worth shipping** before the relay lands, as a near-term
  risk reduction.

### 1.5 Actions

1. **Phase 1 PR — sessionStorage migration.**
   File: `browse-ui/src/lib/host-profiles.ts`.
   ```bash
   cd browse-ui && pnpm test -- host-profiles
   ```
2. **Phase 2 PR — relay/gateway session.**
   Add a gateway service that stores the operator token server-side and issues short-lived browser
   sessions.
3. **Add `Content-Security-Policy` header to Firebase Hosting.**
   File: `browse-ui/firebase.json` (or `public/_headers`).
4. **Display only token fingerprint** in `HostManagement` form.
   File: `browse-ui/src/components/hosts/host-management.tsx`.
5. **Run `gh issue comment 42 --body "Threat model + storage spec in docs/HOSTED-SHELL-RESEARCH.md §1"`**

---

## 2. Stream Reconnect / Replay Semantics (#43)

### 2.1 Context

**Facts:**
- `use-operator-stream.ts` uses `EventSource` for local hosts and `fetch`-streaming (chunked
  `ReadableStream`) for remote hosts.
  _Evidence: `grep -n "EventSource\|fetch" browse-ui/src/components/chat/use-operator-stream.ts`_
- `EventSource` has built-in automatic reconnect with `Last-Event-ID` forwarding (browser
  handles it). No heartbeat is sent; reconnect delay is browser-controlled (default 3 s).
- The `fetch`-streaming path has no reconnect logic today; a dropped TCP connection is silent.
- `browse/api/operator.py` SSE route does not emit `id:` event fields or `retry:` headers.
  _Evidence: `grep -n "id:\|retry:" browse/api/operator.py` (should return nothing)_
- Cloudflare Tunnel / free tunnel providers impose an idle-write timeout of ~100 s.

**Interpretation:** The `fetch` path is silently vulnerable to half-open TCP and tunnel idle
timeouts. Users see "frozen" chat. The `EventSource` path reconnects but loses position without
`id:` fields on the server side.

### 2.2 Reconnect Protocol

**Target reconnect latency:** ≤ 5 s from disconnect detection to first new byte.

**Heartbeat contract:**
- Server MUST emit a comment heartbeat every 25 s while a run is active:
  ```
  : heartbeat\n\n
  ```
  (SSE comment; clients ignore comments but the byte resets the idle-timeout clock.)
- Client MUST treat 30 s of silence as a dead connection and reconnect.

**`Last-Event-ID` / replay / run-id behaviour:**
- Server MUST assign an `id:` field to every SSE event:
  ```
  id: <run-id>:<sequence-number>\n
  event: tool_output\n
  data: {...}\n\n
  ```
- `<run-id>` is the stable run identifier from `POST /api/operator/sessions/{id}/prompt`.
- `<sequence-number>` is a monotonic integer starting at `1`.
- On reconnect, client sends `Last-Event-ID: <run-id>:<seq>` header.
- Server replays events from `<seq>+1` up to the current tail (max replay window: 500 events
  or 60 s of history, whichever is smaller).
- If the cursor is outside the replay window, server returns `410 Gone`; client resets to zero
  and shows "Some output may have been lost" inline notice.

**`EventSource` path (local hosts):**
- No code change needed for reconnect triggering (browser handles it).
- Server-side: add `id:` and `retry: 5000` fields to every event.

**`fetch`-streaming path (remote hosts):**
- Add a 30 s read timeout in `use-operator-stream.ts`; if no bytes received, tear down the
  fetch and open a new one with `Last-Event-ID` header.
- Use `AbortController` with a per-chunk timer reset:
  ```typescript
  const HEARTBEAT_TIMEOUT_MS = 30_000;
  let timer = setTimeout(() => abort(), HEARTBEAT_TIMEOUT_MS);
  reader.read().then(({ value }) => { clearTimeout(timer); timer = setTimeout(...); ... });
  ```

**WebSocket vs SSE decision:**
- Do **not** migrate operator streams to WebSocket at this time.
  Rationale: SSE over HTTP/2 multiplexes cleanly through existing tunnel infrastructure;
  WebSocket requires a separate handshake path that Cloudflare Tunnel supports but adds
  complexity. Revisit if bidirectional cancellation becomes a hard requirement.

**Relay cursor location (interaction with #37):**
- Browser holds the cursor in React state (`lastEventIdRef`).
- Relay gateway forwards `Last-Event-ID` header upstream on reconnect; remote backend handles
  replay.
- If the relay itself restarts, the browser's cursor survives (it is in React state, not relay
  state).

### 2.3 UX States

| State | UI indicator | User action |
|---|---|---|
| Connected, streaming | Green dot in status bar | None |
| Heartbeat timeout detected (reconnecting) | Amber spinner: "Reconnecting…" banner (non-blocking) | Dismiss or wait |
| Reconnected, replaying | "Resuming — <N> events replayed" toast (auto-dismiss 3 s) | None |
| `410 Gone` (cursor expired) | "Connection reset — some output may be missing" inline notice | Scroll to see what was missed |
| Max retries exceeded (5 attempts) | Error banner: "Lost connection. Reload or check host." | Reload button |
| Run completed before disconnect | No banner; normal completion | None |

### 2.4 Actions

1. **Add `id:` and `retry: 5000` fields to SSE events in `browse/api/operator.py`.**
2. **Add 25 s heartbeat comment emission in `browse/core/operator_console.py`.**
3. **Add replay-from-cursor support** (`?since=<seq>`) to
   `GET /api/operator/sessions/{id}/stream`.
4. **Add 30 s read-timeout + reconnect loop** to the `fetch`-streaming path in
   `browse-ui/src/components/chat/use-operator-stream.ts`.
5. **Add reconnect UX states** (banner + toast) in `Transcript.tsx`.
6. **Test reconnect path:**
   ```bash
   cd browse-ui && pnpm test -- use-operator-stream
   ```
7. **Run `gh issue comment 43 --body "Reconnect protocol spec in docs/HOSTED-SHELL-RESEARCH.md §2"`**

---

## 3. First-Run / No-Backend UX (#44)

### 3.1 Context

**Facts:**
- On a clean machine, `https://agents.linhngo.dev` opens with `LOCAL_HOST` active and
  `diagnosticsEnabled=false`.
- `HostProvider` skips the `/healthz` probe on non-local origins (added in a prior fix wave).
  _Evidence: `grep -n "isLocalOrigin" browse-ui/src/providers/host-provider.tsx`_
- Routes (`/sessions`, `/search`, `/graph`) render their shells, fire API calls, and fail
  silently or show empty states.
- Settings shows a generic healthz failure diagnostic rather than install instructions.
- There is no first-run gate, no install guidance, and no explicit "add a host" prompt.

**Interpretation:** The experience is misleading — pages appear functional but return empty or
error states. New users have no actionable path forward.

### 3.2 First-Run States

```
App opens
    │
    ├─ Has ≥1 reachable host?  ──YES──▶  Normal app flow
    │
    NO
    │
    ├─ Has saved host profiles (unreachable)?
    │      YES ──▶  State B: "Your backend is offline"
    │
    ├─ No profiles → State A: "Welcome — connect a backend"
    │
    └─ (After user clicks "Detect local backend") → Probe flow (§1 of ARCHITECTURE.md)
           ├─ Found  ──▶  State C: "Local backend connected" → proceed
           └─ Not found ──▶ State D: "Install or add remote URL"
```

**State A — No host configured (first-run):**
- Full-screen welcome card (replaces page content, not a modal):
  ```
  ┌──────────────────────────────────────────────┐
  │  🔌 Connect a backend                        │
  │                                              │
  │  Open-source Copilot CLI management          │
  │  needs a running backend to work.            │
  │                                              │
  │  [Detect local backend]   [Add remote URL]  │
  └──────────────────────────────────────────────┘
  ```
- Navigation links in sidebar are visible but clicking any deep route redirects to this card
  until a host is reachable.
- Settings → Hosts is always reachable (escape hatch).

**State B — Saved host(s) but none reachable:**
- Inline banner at top of every page (does not block navigation):
  ```
  ⚠️  No reachable backend.  [Retry]  [Manage hosts →]
  ```
- Individual route pages show their shells with empty states (no fake errors).

**State C — Local backend just detected:**
- Toast: "Local backend connected at `https://127.0.0.1:8766`" (auto-dismiss 5 s).
- Redirect to last visited route (or `/sessions` default).

**State D — Probe failed, install guidance:**
- Card extends with collapsible install instructions:
  ```
  Local backend not detected. To use local mode:

  macOS / Linux:
    git clone https://github.com/magicpro97/copilot-session-knowledge ~/.copilot/tools
    cd ~/.copilot/tools && python3 install.py
    python3 browse.py --https

  Windows (WSL):
    <same commands>

  Or, add a remote backend URL:
  [Add remote URL →]
  ```
- Copy-to-clipboard button on each command block.

**Navigation rules:**
- State A: all routes other than `/settings` redirect to `/` (first-run card). Deep links
  are preserved in `sessionStorage`; restored after host is connected.
- State B: navigation is unrestricted; banner persists.
- After host connection: remove first-run gate; redirect to deep link if preserved.

### 3.3 Settings Diagnostics in No-Backend State

Replace the current generic `/healthz` failure message with:

```
Backend status: Not connected
  No local backend detected at https://127.0.0.1:8766
  No remote hosts configured

  → [Add host] [Detect local backend]

  Install a local backend:
    sk browse --https
```

Links to GitHub releases for download when `browse.py` is not installed.

### 3.4 Handoff to Implementation

**Files to create/modify:**

| File | Change |
|---|---|
| `browse-ui/src/components/first-run/FirstRunGate.tsx` | New — wraps app layout; renders state A when no reachable host |
| `browse-ui/src/app/layout.tsx` | Wrap children in `<FirstRunGate>` |
| `browse-ui/src/providers/host-provider.tsx` | Expose `hasReachableHost: boolean` from context |
| `browse-ui/src/components/settings/HostDiagnostics.tsx` | Replace generic error with install guidance |
| `browse-ui/src/app/page.tsx` or `/app/(root)/page.tsx` | State A welcome card content |

**Acceptance criteria:**
1. Clean-browser visit to `https://agents.linhngo.dev` shows state A welcome card.
2. "Detect local backend" button probes `https://127.0.0.1:8766/healthz` and transitions
   to state C on success, state D on failure.
3. "Add remote URL" opens the Add Host sheet.
4. After adding a reachable host, welcome card dismisses and deep link is restored.
5. Settings → Hosts is reachable from all states.
6. Playwright smoke test covers state A → add host → state C transition.

### 3.5 Actions

1. **Create `FirstRunGate.tsx`** and integrate into root layout.
2. **Add `hasReachableHost` to `HostProvider` context**.
3. **Update `HostDiagnostics.tsx`** with install instructions.
4. **Add Playwright test for first-run flow:**
   ```bash
   cd browse-ui && pnpm test:e2e --project behavioral -- --grep "first-run"
   ```
5. **Run `gh issue comment 44 --body "First-run UX spec in docs/HOSTED-SHELL-RESEARCH.md §3"`**

---

## Related Issues

| Issue | Status after this doc |
|---|---|
| #42 browser token storage | Threat model + migration spec complete; actions listed in §1.5 |
| #43 stream reconnect/replay | Protocol + heartbeat + UX states spec complete; actions listed in §2.4 |
| #44 first-run UX | States, copy, navigation rules, implementation handoff complete; actions listed in §3.5 |

## Changelog

| Date | Change |
|---|---|
| 2026-05-05 | Initial spec for #42, #43, #44 (hosted-research-specs tentacle) |
