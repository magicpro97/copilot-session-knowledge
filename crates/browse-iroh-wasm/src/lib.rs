//! browse-iroh-wasm — Iroh relay/QUIC transport for browse-ui (Wasm target)
//!
//! # Status: 🔴 NOT IMPLEMENTED — spike scaffold only
//!
//! This crate provides the Wasm-bindgen surface for the iroh transport used
//! by `browse-ui/src/lib/iroh-transport.ts`.
//!
//! ## Current state (2026-05-07 spike)
//!
//! The crate cannot be compiled because:
//! - No Rust toolchain is installed in the spike environment
//! - `relay.iroh.network` is DNS-unresolvable from the spike network
//! - The `iroh` crate's Wasm support is relay-only (no direct QUIC)
//!
//! ## Build instructions (for when environment allows)
//!
//! ```sh
//! rustup target add wasm32-unknown-unknown
//! cargo install wasm-pack
//! wasm-pack build --target web --out-dir ../../browse-ui/public/iroh-wasm
//! ```
//!
//! ## Architecture note
//!
//! Browser iroh Wasm connects to the iroh relay fleet and obtains a NodeId.
//! Peer discovery happens via out-of-band signalling (e.g. the existing
//! browse.py backend). Data is then routed relay-only until browsers support
//! direct QUIC (WebTransport over QUIC, not yet available for iroh).
//!
//! The relay-only path offers **no meaningful latency advantage** over the
//! existing Telegram transport; both add a relay hop. The value of iroh is
//! the potential for direct QUIC once browsers support it. Until then, the
//! practical improvement is limited to relay fleet latency variation.

use wasm_bindgen::prelude::*;

// ---------------------------------------------------------------------------
// Panic hook (debug builds only)
// ---------------------------------------------------------------------------

#[wasm_bindgen(start)]
pub fn init_panic_hook() {
    #[cfg(feature = "console_error_panic_hook")]
    console_error_panic_hook::set_once();
}

// ---------------------------------------------------------------------------
// IrohNode — Wasm-exposed transport handle
// ---------------------------------------------------------------------------

/// Wasm-exposed handle for an iroh relay connection.
///
/// This struct is a stub; the real implementation requires:
/// - `iroh::Endpoint` with Wasm support enabled
/// - relay URL configuration
/// - async wasm_bindgen bridge (via `wasm-bindgen-futures`)
///
/// See `browse-ui/src/lib/iroh-transport.ts` for the TypeScript interface.
#[wasm_bindgen]
pub struct IrohNode {
    /// NodeId as base32 string (set after connect).
    node_id: Option<String>,
}

#[wasm_bindgen]
impl IrohNode {
    /// Create a new IrohNode.
    ///
    /// Does not connect until `connect()` is called.
    #[wasm_bindgen(constructor)]
    pub fn new() -> IrohNode {
        IrohNode { node_id: None }
    }

    /// Return the local NodeId (base32), or null if not connected.
    #[wasm_bindgen(getter, js_name = nodeId)]
    pub fn node_id(&self) -> Option<String> {
        self.node_id.clone()
    }

    /// Probe the relay and return RTT in milliseconds.
    ///
    /// Returns 0 if relay is unreachable.
    ///
    /// NOTE: Stub — not implemented. Returns Err until the crate is wired
    /// to a real `iroh::Endpoint`.
    #[wasm_bindgen(js_name = probeRelayRtt)]
    pub fn probe_relay_rtt(&self) -> Result<f64, JsValue> {
        Err(JsValue::from_str(
            "IrohNode.probeRelayRtt() not implemented: \
             crate is a scaffold. \
             See crates/browse-iroh-wasm/src/lib.rs and issue #69.",
        ))
    }

    /// Close the transport.
    #[wasm_bindgen]
    pub fn close(&mut self) {
        self.node_id = None;
    }
}

impl Default for IrohNode {
    fn default() -> Self {
        Self::new()
    }
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn new_node_has_no_id() {
        let node = IrohNode::new();
        assert!(node.node_id().is_none());
    }

    #[test]
    fn probe_returns_err_when_not_connected() {
        let node = IrohNode::new();
        assert!(node.probe_relay_rtt().is_err());
    }
}
