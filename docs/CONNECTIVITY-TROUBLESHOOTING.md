# Connectivity Troubleshooting

> Diagnosing why hosted browse UI connectivity works on some networks and fails on others.

This page focuses on the hosted UI / remote-host path when operators are on:

- **FPT and similar ISPs** with tunnel-specific DNS or DPI filtering
- **University networks** with aggressive egress policy
- **Corporate intercepting proxies** that terminate or rewrite TLS

If `python3 browse.py --hosted-bootstrap` works locally in Chromium/Edge but your tunnel never
connects, the problem is usually **network policy**, not the browse server itself.

---

## Common blocking mechanisms

| Mechanism | What gets blocked | One-liner to confirm |
|---|---|---|
| DNS blocklist | Tunnel hostnames such as `*.ngrok-free.app`, `*.ngrok.io`, `*.trycloudflare.com`, `*.lhr.life` | `nslookup abc123.ngrok-free.app` → `NXDOMAIN` |
| SNI-based DPI | TLS ClientHello for well-known tunnel SNIs | `curl -vk https://abc123.ngrok-free.app` → reset / handshake failure |
| IP / port reputation | QUIC / alternate tunnel ports (for example Cloudflare Tunnel UDP 7844) | `nc -zv 198.41.192.7 7844` → timeout |

**Interpretation:** when one of the checks above fails consistently, the network is blocking the
tunnel path before the browser ever reaches your local backend.

---

## Decision tree

```text
Can this network resolve and connect to your tunnel hostname?
  ├── No → DNS/SNI/port policy is blocking the tunnel
  │        → Use Outbound Control-Bus Mode (§5 in HOSTED-SHELL-ARCHITECTURE)
  │        → Or use Same-Origin Relay / Gateway (§2, issue #37)
  └── Yes → Can the local machine still reach the tunnel service on TCP 443?
           ├── No → outbound policy / proxy issue
           │        → prefer Outbound Control-Bus Mode or Same-Origin Relay
           └── Yes → tunnel should work
                    → check ngrok/cloudflared auth, ingress config, and token setup
```

---

## Known tunnel-hostile and broker-friendlier destinations

### Commonly blocked tunnel SNIs

- `*.ngrok-free.app`
- `*.ngrok.io`
- `*.trycloudflare.com`
- `*.lhr.life`

### Commonly allowed broker / control-plane destinations

- `api.telegram.org`
- `discord.com`
- `slack.com`
- `*.ably.io`
- `*.googleapis.com`, `*.firebaseio.com`, `*.firebasedatabase.app`

**Interpretation:** these are not guarantees, but they are often allowed where tunnel-specific
hostnames are blocked because they look like mainstream SaaS traffic instead of tunnel ingress.

---

## Minimal operator checklist

```bash
# 1. Local backend health
curl -s http://127.0.0.1:8765/.well-known/browse-host | python3 -m json.tool

# 2. DNS check for tunnel hostname
nslookup abc123.ngrok-free.app

# 3. TLS/SNI check
curl -vk https://abc123.ngrok-free.app

# 4. Alternate tunnel-port reachability (Cloudflare Tunnel example)
nc -zv 198.41.192.7 7844

# 5. Compare with a likely-allowed broker endpoint
curl -I https://api.telegram.org
```

---

## Chrome 138+ Local Network Access (LNA) permission model

**Background:** Chrome 138 introduced [Local Network Access (LNA)](https://developer.chrome.com/blog/local-network-access) as the successor to Private Network Access (PNA). LNA replaces preflight-header negotiation with an explicit **user permission prompt** ("Allow [site] to access devices on your local network?").

| Chrome version | LNA status | Impact |
|---|---|---|
| Chrome 138 | Opt-in flag only (`chrome://flags/#local-network-access-check`) | No user impact without flag |
| Chrome 139–141 | Opt-in flag, developer testing period | No user impact without flag |
| **Chrome 142** (Oct 2025) | **Stable, enforced by default** | All users: public→loopback fetches prompt or block |
| Chrome 145+ | Finer-grained split: `loopback-network` (localhost) vs `local-network` (LAN) | Stricter per-type enforcement |

**What changed in browse-ui:** As of the Chrome 138 LNA patch, `local-bootstrap.ts` sends
`targetAddressSpace: "loopback"` on every probe fetch. This explicit annotation tells Chrome
to apply the LNA permission flow cleanly instead of silently pending the request.
Chrome distinguishes `"loopback"` (127.0.0.0/8, ::1) from `"local"` (192.168.x, 10.x) —
using the wrong value causes a silent block with the error *"target IP address space of
local yet the resource is in address space loopback"*.

**What the user will see on Chrome 142+:** On the first auto-detect probe from the hosted UI, Chrome
shows a permission dialog. The user must click **Allow** to let the site reach
`http://127.0.0.1:8765`. After granting, the permission is remembered per origin (revocable in
_Settings → Privacy and security → Site settings → Local network devices_).

**Enterprise pre-grant (suppress the prompt for managed devices):** Configure the Chrome policy
[`LocalNetworkAccessAllowedByOrigins`](https://chromeenterprise.google/policies/#LocalNetworkAccessAllowedByOrigins)
to pre-allow the hosted UI origin:

```json
// chrome_policy.json (apply via Group Policy / macOS MDM / etc.)
{
  "LocalNetworkAccessAllowedByOrigins": [
    "https://agents.linhngo.dev",
    "https://agents-linhngo-dev.web.app"
  ]
}
```

This policy suppresses the permission dialog for the listed origins and is the recommended path
for teams deploying the hosted UI on managed devices.

**Testing with the LNA flag:**

```bash
# Open Chrome with LNA blocking enabled to reproduce user-prompt behavior:
# macOS
/Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome \
  --enable-features=LocalNetworkAccessChecks
# or set chrome://flags/#local-network-access-check → "Enabled (Blocking)"
```

**Diagnostic:** If loopback auto-detect stops working on Chrome 138+, check:
1. Chrome DevTools → Console for messages containing "Local Network Access".
2. `chrome://settings/content/localNetworkDevices` → ensure the hosted origin is listed as **Allowed**.
3. If the probe silently returns `unavailable`, the permission was denied. Re-grant at the URL above.

---

## Browser note: Edge / Chromium with hosted bootstrap

When using the hosted UI from **Edge 104+ / Chromium** against a local backend started with
`python3 browse.py --hosted-bootstrap`, you should **not** need `--disable-web-security`.

The expected path is:

1. hosted UI probes `http://127.0.0.1:8765/.well-known/browse-host`
2. browser runs the LNA permission flow (Chrome 142+) or PNA preflight flow (older Chrome/Edge)
3. browse server answers with the required allowlist + PNA headers

If that path fails, fix the origin / network problem instead of disabling browser security.

---

## Recommended remediations

1. **Chromium / Edge + local machine available:** use `python3 browse.py --hosted-bootstrap`
2. **Tunnel blocked by the network:** use **Outbound Control-Bus Mode** (Telegram broker, shipped):
   ```bash
   export BROWSE_BROKER_TELEGRAM_TOKEN="<bot-token>"
   export BROWSE_BROKER_AUTHORIZED_USER_ID="<your-user-id>"
   python browse.py --broker-mode telegram
   ```
   Full instructions: [docs/OPERATOR-PLAYBOOK.md §Broker Mode](OPERATOR-PLAYBOOK.md#broker-mode-outbound-only-control-bus-issue-71)
   Architecture: [docs/HOSTED-SHELL-ARCHITECTURE.md §5](HOSTED-SHELL-ARCHITECTURE.md#5-outbound-control-bus-mode-tunnel-hostile-networks)
3. **You can host a full gateway:** use **Same-Origin Relay / Gateway** in
   [docs/HOSTED-SHELL-ARCHITECTURE.md §2](HOSTED-SHELL-ARCHITECTURE.md#2-same-origin-relay--gateway-architecture-37)

Related operator guidance:

- [docs/HOSTED-SHELL-ARCHITECTURE.md §4.5](HOSTED-SHELL-ARCHITECTURE.md#45-browser-compatibility)
- [docs/OPERATOR-PLAYBOOK.md](OPERATOR-PLAYBOOK.md#loopback-bootstrap-for-hosted-ui-issue-49)
