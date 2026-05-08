# browse-iroh-wasm

**Status: 🔴 NOT IMPLEMENTED — spike scaffold**

Iroh relay/QUIC Wasm transport for [browse-ui](../../browse-ui/), backing
`browse-ui/src/lib/iroh-transport.ts`. Created as part of the #69 spike.

## Spike verdict (2026-05-07)

| Finding | Value |
|---------|-------|
| `relay.iroh.network` reachable | ❌ DNS-unresolvable from spike env |
| `dumbpipe` binary available | ❌ not installed |
| Rust toolchain | ❌ not installed |
| Internet access | ✅ (iroh.computer / Vercel CDN confirmed reachable) |
| Relay-only latency advantage | ❌ relay-only = same hop count as Telegram |
| Direct QUIC in browsers | ❌ not yet available via iroh |

**Evidence:** `browse-ui/e2e/iroh-relay-rtt.spec.ts` + attached
`iroh-relay-probe.json` Playwright artifact.

The relay-only browser path provides **no meaningful latency advantage** over
the existing Telegram transport: both route data through a relay server,
adding equivalent latency. Direct QUIC peer-to-peer would require:

1. Browser WebTransport/QUIC support for iroh (not yet shipped)
2. `dumbpipe` sidecar running on the host machine
3. This Wasm crate compiled and served

**Recommendation for #69:** Defer. Relay-only path is not a compelling
improvement. Revisit when browser direct-QUIC is viable.

## Build (when environment allows)

```sh
# Prerequisites
rustup target add wasm32-unknown-unknown
cargo install wasm-pack

# Build
wasm-pack build --target web --out-dir ../../browse-ui/public/iroh-wasm

# Test
wasm-pack test --headless --firefox
```

## Structure

```
crates/browse-iroh-wasm/
├── Cargo.toml          # iroh dependency pinned to 0.35
├── README.md           # this file
└── src/
    └── lib.rs          # IrohNode Wasm-bindgen surface (stub)
```

## Integration

Once compiled:
1. Serve `browse-ui/public/iroh-wasm/` from the Next.js static dir
2. Update `browse-ui/src/lib/iroh-transport.ts` factory to load the Wasm module
3. Replace `IrohTransportStub` with a real `IrohNode` instance
4. Re-run `iroh-relay-rtt.spec.ts` to capture real RTT measurements
