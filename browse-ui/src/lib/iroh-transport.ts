/**
 * iroh-transport.ts — Iroh relay transport scaffold (#69)
 *
 * STATUS: 🔴 NOT IMPLEMENTED — scaffold only (spike evidence)
 *
 * This file provides the TypeScript interface surface for a future
 * iroh-Wasm + dumbpipe relay transport. It is intentionally not wired
 * to any runtime implementation because:
 *
 *   1. The iroh Wasm module (`browse-iroh-wasm`) does not exist yet.
 *      The Rust crate scaffold is at `crates/browse-iroh-wasm/` but has
 *      not been compiled; no Rust toolchain is available in this environment.
 *
 *   2. `relay.iroh.network` is DNS-unresolvable from the current network.
 *      See spike evidence: `browse-ui/e2e/iroh-relay-rtt.spec.ts` and
 *      the attached `iroh-relay-probe.json` artifact.
 *
 *   3. The relay-only path (browser iroh without direct QUIC) routes all
 *      traffic through a relay server, which gives no meaningful latency
 *      advantage over the existing Telegram transport. Direct QUIC
 *      peer-to-peer would require a QUIC-capable Wasm runtime and
 *      dumbpipe sidecar — both are outside the current scope.
 *
 * How to advance past this scaffold:
 *   a. Install Rust + wasm-pack: `curl https://sh.rustup.rs -sSf | sh`
 *   b. Build the crate: `cd crates/browse-iroh-wasm && wasm-pack build --target web`
 *   c. Confirm relay.iroh.network is reachable (DNS/firewall)
 *   d. Replace `IrohTransportStub` below with a real `IrohNode` instance
 *   e. Re-run the Playwright spec to capture real RTT evidence
 *
 * Spike decision for #69 (2026-05-07):
 *   relay.iroh.network is unreachable → relay-only path cannot be proven.
 *   Relay path offers no latency advantage even if reachable.
 *   Full QUIC path requires Wasm compilation + sidecar deployment.
 *   Recommend: close #69 as "deferred — relay-only gives no advantage;
 *   revisit when direct QUIC + Wasm sidecar are feasible."
 */

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

/** Connection state of an iroh relay channel. */
export type IrohConnectionState =
  | "disconnected"
  | "connecting"
  | "relay-connected" // relay path only — latency equivalent to Telegram
  | "direct-connected" // direct QUIC — NOT achievable in current browser env
  | "error";

/** RTT measurement from a relay probe. */
export interface IrohRelayRtt {
  relayUrl: string;
  rttMs: number;
  timestamp: string;
  path: "relay" | "direct";
}

/** Message envelope passed over an iroh channel. */
export interface IrohMessage {
  from: string; // iroh NodeId (base32)
  to: string; // iroh NodeId (base32)
  payload: Uint8Array;
  timestamp: number;
}

/** Options for opening an iroh relay transport. */
export interface IrohTransportOptions {
  /** Relay server URL. Defaults to the iroh global relay fleet. */
  relayUrl?: string;
  /** Connection timeout in ms. */
  connectTimeoutMs?: number;
  /** Called when connection state changes. */
  onStateChange?: (state: IrohConnectionState) => void;
  /** Called when an incoming message arrives. */
  onMessage?: (msg: IrohMessage) => void;
}

// ---------------------------------------------------------------------------
// Interface
// ---------------------------------------------------------------------------

/**
 * IrohTransport — abstract interface for an iroh relay/direct transport.
 *
 * Implementations must be provided by the `browse-iroh-wasm` Wasm module
 * (see `crates/browse-iroh-wasm/`). This interface exists so the rest of
 * the codebase can reference the surface without a Wasm dependency.
 */
export interface IrohTransport {
  readonly state: IrohConnectionState;
  /** Iroh NodeId of this peer (base32 string). Available after connect(). */
  readonly nodeId: string | null;
  /** Connect to the relay and obtain a NodeId. */
  connect(options?: IrohTransportOptions): Promise<void>;
  /** Send a message to a remote NodeId. */
  send(to: string, payload: Uint8Array): Promise<void>;
  /** Measure RTT to the relay. Returns null if relay unreachable. */
  probeRelayRtt(): Promise<IrohRelayRtt | null>;
  /** Gracefully close the transport. */
  close(): Promise<void>;
}

// ---------------------------------------------------------------------------
// Stub (spike / placeholder)
// ---------------------------------------------------------------------------

/**
 * IrohTransportStub — non-functional placeholder that throws on every
 * operation. Exists so imports resolve while the real Wasm module is absent.
 *
 * Replace with `createIrohWasmTransport()` once the Wasm crate is compiled.
 */
export class IrohTransportStub implements IrohTransport {
  readonly state: IrohConnectionState = "disconnected";
  readonly nodeId: string | null = null;

  private static notReady(): never {
    throw new Error(
      [
        "IrohTransport is not implemented.",
        "The browse-iroh-wasm Wasm module has not been compiled.",
        "See crates/browse-iroh-wasm/README.md for build instructions.",
        "Issue: #69 — iroh relay transport is deferred pending Wasm sidecar.",
      ].join(" ")
    );
  }

  connect(_options?: IrohTransportOptions): Promise<void> {
    void _options;
    return IrohTransportStub.notReady();
  }
  send(_to: string, _payload: Uint8Array): Promise<void> {
    void _to;
    void _payload;
    return IrohTransportStub.notReady();
  }
  probeRelayRtt(): Promise<IrohRelayRtt | null> {
    return IrohTransportStub.notReady();
  }
  close(): Promise<void> {
    return IrohTransportStub.notReady();
  }
}

// ---------------------------------------------------------------------------
// Factory (to be replaced when Wasm module is compiled)
// ---------------------------------------------------------------------------

/**
 * Returns the iroh transport implementation.
 *
 * Currently returns the stub. Once `browse-iroh-wasm` is compiled and
 * deployed, replace the body with the real Wasm-backed factory, e.g.:
 *
 * ```ts
 * import init, { IrohNode } from "../../crates/browse-iroh-wasm/pkg";
 * await init();
 * return new IrohNode(options);
 * ```
 */
export function createIrohTransport(_options?: IrohTransportOptions): IrohTransport {
  void _options;
  return new IrohTransportStub();
}
