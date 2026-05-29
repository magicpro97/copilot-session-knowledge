//! Integration tests for `sk mcp` — MCP stdio server (MCP 2024-11-05).
//!
//! Uses pipe-based harness: spawn `sk mcp`, write framed JSON-RPC messages to
//! stdin, read responses from stdout, and assert correctness.

use std::io::{Read, Write};
use std::process::{Command, Stdio};

use assert_cmd::cargo::cargo_bin;
use serde_json::{json, Value};

// ── Helper: LSP framing ────────────────────────────────────────────────────

fn frame(msg: &Value) -> Vec<u8> {
    let body = msg.to_string();
    let header = format!("Content-Length: {}\r\n\r\n", body.len());
    let mut bytes = header.into_bytes();
    bytes.extend_from_slice(body.as_bytes());
    bytes
}

fn read_response(stdout: &mut dyn Read) -> Value {
    let mut header = String::new();
    let mut buf = [0u8; 1];
    loop {
        stdout.read_exact(&mut buf).expect("read header byte");
        header.push(buf[0] as char);
        if header.ends_with("\r\n\r\n") {
            break;
        }
    }
    let len: usize = header
        .lines()
        .find(|l| l.to_ascii_lowercase().starts_with("content-length:"))
        .and_then(|l| l.split(':').nth(1))
        .and_then(|v| v.trim().parse().ok())
        .expect("Content-Length header");

    let mut body = vec![0u8; len];
    stdout.read_exact(&mut body).expect("read body");
    serde_json::from_slice(&body).expect("valid JSON response")
}

// ── Spawn helper ──────────────────────────────────────────────────────────

fn spawn_mcp() -> std::process::Child {
    Command::new(cargo_bin("sk"))
        .arg("mcp")
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .spawn()
        .expect("spawn sk mcp")
}

fn send_and_recv(child: &mut std::process::Child, msg: &Value) -> Value {
    let bytes = frame(msg);
    child.stdin.as_mut().unwrap().write_all(&bytes).unwrap();
    read_response(child.stdout.as_mut().unwrap())
}

fn close_child(mut child: std::process::Child) {
    drop(child.stdin.take());
    let _ = child.wait();
}

// ── Tests ─────────────────────────────────────────────────────────────────

#[test]
fn test_initialize_response() {
    let mut child = spawn_mcp();
    let resp = send_and_recv(
        &mut child,
        &json!({
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2024-11-05", "clientInfo": {"name": "test", "version": "1"}}
        }),
    );
    close_child(child);

    assert_eq!(resp["jsonrpc"], "2.0");
    assert_eq!(resp["id"], 1);
    assert!(resp.get("error").is_none(), "no error: {resp}");
    assert_eq!(resp["result"]["protocolVersion"], "2024-11-05");
    assert_eq!(resp["result"]["serverInfo"]["name"], "sk");
}

#[test]
fn test_tools_list_returns_8_tools() {
    let mut child = spawn_mcp();
    // initialize first
    send_and_recv(
        &mut child,
        &json!({"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}),
    );
    let resp = send_and_recv(
        &mut child,
        &json!({"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}),
    );
    close_child(child);

    assert_eq!(resp["jsonrpc"], "2.0");
    assert_eq!(resp["id"], 2);
    assert!(resp.get("error").is_none(), "no error: {resp}");
    let tools = resp["result"]["tools"].as_array().expect("tools array");
    assert_eq!(tools.len(), 8, "expected 8 tools, got {}", tools.len());

    let names: Vec<&str> = tools.iter().filter_map(|t| t["name"].as_str()).collect();
    for expected in &[
        "sk_learn",
        "sk_query",
        "sk_briefing",
        "sk_index_status",
        "sk_index_health",
        "sk_audit_log",
        "sk_taxonomy_list",
        "sk_relate_supersedes",
    ] {
        assert!(names.contains(expected), "missing tool: {expected}");
    }
}

#[test]
fn test_ping() {
    let mut child = spawn_mcp();
    let resp = send_and_recv(
        &mut child,
        &json!({"jsonrpc":"2.0","id":42,"method":"ping","params":{}}),
    );
    close_child(child);

    assert_eq!(resp["id"], 42);
    assert!(resp.get("error").is_none());
    assert_eq!(resp["result"], json!({}));
}

#[test]
fn test_method_not_found() {
    let mut child = spawn_mcp();
    let resp = send_and_recv(
        &mut child,
        &json!({"jsonrpc":"2.0","id":3,"method":"nonexistent/method","params":{}}),
    );
    close_child(child);

    assert!(resp.get("error").is_some());
    assert_eq!(resp["error"]["code"], -32601);
}

#[test]
fn test_tools_call_missing_name() {
    let mut child = spawn_mcp();
    let resp = send_and_recv(
        &mut child,
        &json!({"jsonrpc":"2.0","id":4,"method":"tools/call","params":{"arguments":{}}}),
    );
    close_child(child);

    assert!(resp.get("error").is_some());
    assert_eq!(resp["error"]["code"], -32602);
}

#[test]
fn test_readonly_blocks_sk_learn() {
    let mut child = Command::new(cargo_bin("sk"))
        .arg("mcp")
        .env("SK_MCP_READONLY", "1")
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .spawn()
        .expect("spawn sk mcp");

    let resp = send_and_recv(
        &mut child,
        &json!({
            "jsonrpc":"2.0","id":5,"method":"tools/call",
            "params":{"name":"sk_learn","arguments":{"category":"mistake","title":"t","description":"d"}}
        }),
    );
    close_child(child);

    assert!(
        resp.get("error").is_some(),
        "readonly should block sk_learn"
    );
    assert_eq!(resp["error"]["code"], -32602);
}

#[test]
fn test_sk_tools_allowlist() {
    let mut child = Command::new(cargo_bin("sk"))
        .arg("mcp")
        .env("SK_MCP_TOOLS", "sk_query,sk_briefing")
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .spawn()
        .expect("spawn sk mcp");

    let resp = send_and_recv(
        &mut child,
        &json!({"jsonrpc":"2.0","id":6,"method":"tools/list","params":{}}),
    );
    close_child(child);

    let tools = resp["result"]["tools"].as_array().expect("tools array");
    assert_eq!(tools.len(), 2, "expected 2 tools with allowlist");
}

#[test]
fn test_list_tools_flag_exits_0() {
    let output = Command::new(cargo_bin("sk"))
        .args(["mcp", "--list-tools"])
        .output()
        .expect("run sk mcp --list-tools");

    assert!(output.status.success(), "expected exit 0");
    let stdout = String::from_utf8_lossy(&output.stdout);
    let parsed: Value = serde_json::from_str(&stdout).expect("valid JSON output");
    let tools = parsed["tools"].as_array().expect("tools array");
    assert_eq!(tools.len(), 8);
}

#[test]
fn test_initialized_notification_no_response() {
    // initialized notification has no id → server sends no response.
    // Send initialized, then ping — only ping should produce output.
    let mut child = spawn_mcp();

    // initialize
    send_and_recv(
        &mut child,
        &json!({"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}),
    );

    // initialized notification (no response expected)
    let notif = json!({"jsonrpc":"2.0","method":"initialized","params":{}});
    child
        .stdin
        .as_mut()
        .unwrap()
        .write_all(&frame(&notif))
        .unwrap();

    // ping still gets answered
    let resp = send_and_recv(
        &mut child,
        &json!({"jsonrpc":"2.0","id":99,"method":"ping","params":{}}),
    );
    close_child(child);

    assert_eq!(resp["id"], 99);
    assert!(resp.get("error").is_none());
}
