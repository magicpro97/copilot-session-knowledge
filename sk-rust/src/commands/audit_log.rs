//! `sk audit-log` query surface (issue #574).
//!
//! Read-only operator commands over `~/.copilot/markers/audit.jsonl`:
//!   sk audit-log --since <duration> --event <prefix> [--json]
//!   sk audit-log latency --pair learn,briefing [--since <duration>]
//!
//! All commands are data-only and never modify the audit file.
//! Missing file → empty result, exit 0. Malformed lines are skipped.

use std::fs;
use std::path::PathBuf;
use std::process::ExitCode;
use std::time::{SystemTime, UNIX_EPOCH};

use serde_json::Value;

use crate::config::resolve_home_dir;

fn audit_path() -> PathBuf {
    resolve_home_dir()
        .unwrap_or_else(|| PathBuf::from("."))
        .join(".copilot")
        .join("markers")
        .join("audit.jsonl")
}

fn now_secs() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs()
}

/// Parse a duration like `1h`, `15m`, `90s`, `2d` into seconds.
/// A bare integer is treated as seconds. Returns `None` on invalid
/// input so the caller can surface a friendly error.
pub fn parse_duration_secs(s: &str) -> Option<u64> {
    let s = s.trim();
    if s.is_empty() {
        return None;
    }
    let (num_part, unit) = match s.chars().last()? {
        c if c.is_ascii_alphabetic() => (&s[..s.len() - 1], c.to_ascii_lowercase()),
        _ => (s, 's'),
    };
    let n: u64 = num_part.parse().ok()?;
    let mult = match unit {
        's' => 1,
        'm' => 60,
        'h' => 3_600,
        'd' => 86_400,
        _ => return None,
    };
    Some(n.saturating_mul(mult))
}

/// Read the audit log into memory as parsed JSON values.
/// Missing file → empty Vec.
fn read_audit_lines() -> Vec<Value> {
    let path = audit_path();
    let Ok(body) = fs::read_to_string(&path) else {
        return Vec::new();
    };
    body.lines()
        .filter_map(|l| {
            let l = l.trim();
            if l.is_empty() {
                None
            } else {
                serde_json::from_str::<Value>(l).ok()
            }
        })
        .collect()
}

/// Parse the optional `detail` field as JSON (audit details are stored
/// as JSON strings inside the JSONL envelope).
fn detail_object(v: &Value) -> Option<Value> {
    let s = v.get("detail")?.as_str()?;
    serde_json::from_str(s).ok()
}

/// Args parser for `sk audit-log [latency] ...`.
struct AuditArgs {
    is_latency: bool,
    since: Option<u64>,
    event_prefix: Option<String>,
    pair: Option<(String, String)>,
    json: bool,
}

fn parse_args(args: &[String]) -> Result<AuditArgs, String> {
    let mut is_latency = false;
    let mut since: Option<u64> = None;
    let mut event_prefix: Option<String> = None;
    let mut pair: Option<(String, String)> = None;
    let mut json = false;

    let mut i = 0;
    if args.first().map(|s| s.as_str()) == Some("latency") {
        is_latency = true;
        i = 1;
    }
    while i < args.len() {
        match args[i].as_str() {
            "--since" => {
                let v = args
                    .get(i + 1)
                    .ok_or_else(|| "--since requires a value".to_string())?;
                since =
                    Some(parse_duration_secs(v).ok_or_else(|| format!("invalid duration: {v}"))?);
                i += 2;
            }
            "--event" => {
                event_prefix = Some(
                    args.get(i + 1)
                        .cloned()
                        .ok_or_else(|| "--event requires a value".to_string())?,
                );
                i += 2;
            }
            "--pair" => {
                let v = args
                    .get(i + 1)
                    .ok_or_else(|| "--pair requires <a>,<b>".to_string())?;
                let parts: Vec<&str> = v.split(',').collect();
                if parts.len() != 2 {
                    return Err(format!(
                        "--pair expected two comma-separated event names; got {v}"
                    ));
                }
                pair = Some((parts[0].trim().to_string(), parts[1].trim().to_string()));
                i += 2;
            }
            "--json" => {
                json = true;
                i += 1;
            }
            "-h" | "--help" => {
                println!("Usage: sk audit-log [--since <dur>] [--event <prefix>] [--json]");
                println!("       sk audit-log latency --pair <a>,<b> [--since <dur>]");
                std::process::exit(0);
            }
            other => {
                return Err(format!("unknown flag: {other}"));
            }
        }
    }
    Ok(AuditArgs {
        is_latency,
        since,
        event_prefix,
        pair,
        json,
    })
}

pub fn run_audit_log_command(args: &[String]) -> ExitCode {
    let parsed = match parse_args(args) {
        Ok(p) => p,
        Err(e) => {
            eprintln!("sk audit-log: {e}");
            return ExitCode::from(2);
        }
    };

    let records = read_audit_lines();
    let cutoff = parsed.since.map(|d| now_secs().saturating_sub(d));

    if parsed.is_latency {
        return run_latency(&records, cutoff, parsed.pair.as_ref());
    }

    // Filter by --since and --event prefix; emit JSON lines (default
    // --json since the issue only specifies a JSON output schema).
    let _ = parsed.json; // currently always JSONL; flag accepted for forward-compat
    for rec in records.iter() {
        if let Some(c) = cutoff {
            let ts = rec.get("ts").and_then(|v| v.as_u64()).unwrap_or(0);
            if ts < c {
                continue;
            }
        }
        if let Some(prefix) = &parsed.event_prefix {
            let ev = rec.get("event").and_then(|v| v.as_str()).unwrap_or("");
            if !ev.starts_with(prefix) {
                continue;
            }
        }
        println!("{rec}");
    }
    ExitCode::SUCCESS
}

fn run_latency(
    records: &[Value],
    cutoff: Option<u64>,
    pair: Option<&(String, String)>,
) -> ExitCode {
    let (a_prefix, b_prefix) = match pair {
        Some(p) => (p.0.as_str(), p.1.as_str()),
        None => {
            eprintln!("sk audit-log: latency requires --pair <a>,<b>");
            return ExitCode::from(2);
        }
    };

    // Collect timestamps keyed by stable_id for each side.
    use std::collections::HashMap;
    let mut left: HashMap<String, u64> = HashMap::new();
    let mut right: HashMap<String, Vec<u64>> = HashMap::new();
    let mut window_min: u64 = u64::MAX;
    let mut window_max: u64 = 0;
    let mut samples_seen = false;

    for rec in records.iter() {
        let ev = rec.get("event").and_then(|v| v.as_str()).unwrap_or("");
        let ts = rec.get("ts").and_then(|v| v.as_u64()).unwrap_or(0);
        if let Some(c) = cutoff {
            if ts < c {
                continue;
            }
        }
        if ev.starts_with(a_prefix) {
            if let Some(det) = detail_object(rec) {
                if let Some(sid) = det.get("stable_id").and_then(|v| v.as_str()) {
                    // First write wins (earliest learn).
                    left.entry(sid.to_string()).or_insert(ts);
                    samples_seen = true;
                    window_min = window_min.min(ts);
                    window_max = window_max.max(ts);
                }
            }
        } else if ev.starts_with(b_prefix) {
            if let Some(det) = detail_object(rec) {
                if let Some(arr) = det.get("stable_ids").and_then(|v| v.as_array()) {
                    for sid_v in arr {
                        if let Some(sid) = sid_v.as_str() {
                            right.entry(sid.to_string()).or_default().push(ts);
                            samples_seen = true;
                            window_min = window_min.min(ts);
                            window_max = window_max.max(ts);
                        }
                    }
                } else if let Some(sid) = det.get("stable_id").and_then(|v| v.as_str()) {
                    right.entry(sid.to_string()).or_default().push(ts);
                    samples_seen = true;
                    window_min = window_min.min(ts);
                    window_max = window_max.max(ts);
                }
            }
        }
    }

    // Compute per-stable_id latency: smallest (b - a) where b >= a.
    let mut deltas_ms: Vec<u64> = Vec::new();
    for (sid, a_ts) in left.iter() {
        if let Some(bs) = right.get(sid) {
            if let Some(min_b) = bs.iter().filter(|t| **t >= *a_ts).min() {
                deltas_ms.push((min_b - a_ts) * 1000);
            }
        }
    }

    deltas_ms.sort_unstable();
    let n = deltas_ms.len();
    let percentile = |p: f64| -> u64 {
        if n == 0 {
            return 0;
        }
        // Nearest-rank method: idx = ceil(p * n) - 1, clamped.
        let raw = (p * n as f64).ceil() as i64 - 1;
        let idx = raw.clamp(0, (n - 1) as i64) as usize;
        deltas_ms[idx]
    };
    let p50 = percentile(0.50);
    let p95 = percentile(0.95);
    let window_s = if samples_seen && window_max >= window_min {
        window_max - window_min
    } else {
        0
    };

    let out = serde_json::json!({
        "p50_ms": p50,
        "p95_ms": p95,
        "n": n,
        "window_s": window_s,
    });
    println!("{out}");
    ExitCode::SUCCESS
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parse_duration_handles_units() {
        assert_eq!(parse_duration_secs("30s"), Some(30));
        assert_eq!(parse_duration_secs("5m"), Some(300));
        assert_eq!(parse_duration_secs("2h"), Some(7_200));
        assert_eq!(parse_duration_secs("1d"), Some(86_400));
        assert_eq!(parse_duration_secs("90"), Some(90));
        assert_eq!(parse_duration_secs(""), None);
        assert_eq!(parse_duration_secs("bad"), None);
    }
}
