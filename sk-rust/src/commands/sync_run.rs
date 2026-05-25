//! Native `sk sync run` daemon for `sk sync run`.
//!
//! Provides a native daemon surface (atomic lock, Ctrl-C handler, adaptive
//! poll loop) that owns the sync cycle.  In the default build, `native-sync`
//! is enabled and the actual push/pull HTTP logic runs natively via
//! `crate::sync::engine`.  Builds compiled without `native-sync` fall back to
//! the Python `sync-daemon.py --once` subprocess for each sync cycle.
//!
//! Architecture (strangler-fig):
//!
//! ```text
//! sync_run.rs  ──owns──>  lock, loop, signal, markers, config-check
//!                  │
//!                  ├── [default]                crate::sync::engine::run_native_sync_cycle
//!                  └── [without native-sync]    sync-daemon.py --once  (Python fallback)
//! ```
//!
//! ## CLI flags (aligned with sync-daemon.py)
//!
//! ```text
//! sk sync run                Run sync loop (60 s adaptive interval)
//! sk sync run --once         Single push/pull cycle then exit
//! sk sync run --daemon       Background daemon (note: runs in foreground currently)
//! sk sync run --interval 30  Custom poll interval (seconds)
//! sk sync run --push-only    Only push local changes
//! sk sync run --pull-only    Only pull remote changes
//! sk sync run --limit N      Max transactions per batch (default 50)
//! ```

use std::path::Path;
use std::process::ExitCode;
use std::sync::atomic::AtomicBool;
use std::sync::Arc;

#[cfg(not(feature = "native-sync"))]
use crate::config::python_exe;
use crate::config::{resolve_copilot_dir, resolve_tools_dir};
use crate::daemon::{install_shutdown_handler, run_daemon_loop_with_wake, DaemonLock, LoopConfig};

const LOG_PREFIX: &str = "sync";
const DEFAULT_INTERVAL: u64 = 60;

// ── Public entry point ────────────────────────────────────────────────────────

/// Entry point for `sk sync run [args]`.
pub fn run_sync_run_command(args: &[String]) -> ExitCode {
    let opts = match parse_sync_args(args) {
        Ok(o) => o,
        Err(e) => {
            eprintln!("[sync] Error: {e}");
            eprintln!("{HELP_TEXT}");
            return ExitCode::from(1);
        }
    };

    if opts.help {
        println!("{HELP_TEXT}");
        return ExitCode::SUCCESS;
    }

    let copilot_dir = resolve_copilot_dir();
    let session_state = copilot_dir.join("session-state");

    // Ensure session-state exists (same guard as the Python daemon).
    if !session_state.exists() {
        eprintln!(
            "[sync] Error: session-state directory not found: {}",
            session_state.display()
        );
        return ExitCode::from(1);
    }

    let lock_file = session_state.join(".sync-daemon.lock");
    let state_file = session_state.join(".sync-daemon-state.json");

    let mut lock = match DaemonLock::acquire(&lock_file, LOG_PREFIX) {
        Ok(l) => l,
        Err(e) => {
            eprintln!("[sync] Error: {e}");
            return ExitCode::from(1);
        }
    };

    let running = Arc::new(AtomicBool::new(true));
    if let Err(e) = install_shutdown_handler(running.clone()) {
        eprintln!("[sync] Warning: {e}");
    }

    if opts.daemon {
        #[cfg(windows)]
        println!("[sync] Note: --daemon on Windows starts in foreground.");
        #[cfg(not(windows))]
        println!(
            "[sync] Note: --daemon runs in foreground in the native daemon \
             (native fork not yet implemented)."
        );
    }

    // Load sync config to check whether a remote is configured.
    let config = load_sync_config(&resolve_tools_dir());
    if config.connection_string.is_empty() {
        if opts.once {
            println!(
                "[sync] No remote configured (sync-config.json connection_string is empty). Running local cleanup only."
            );
            println!("[sync] Run: sk sync config --setup <url>");
        } else {
            println!(
                "[sync] No remote configured. Daemon will wait for configuration.\n\
             [sync] Run: sk sync config --setup <url>"
            );
        }
    }

    let markers_dir = copilot_dir.join("markers");
    let tools_dir = resolve_tools_dir();

    if opts.once {
        println!("[sync] Running single sync cycle\u{2026}");
        let code = run_sync_cycle(&opts, &config, &tools_dir);
        save_sync_state(&state_file, code.is_ok());
        lock.release();
        return match code {
            Ok(()) => ExitCode::SUCCESS,
            Err(_) => ExitCode::from(1),
        };
    }

    let adaptive = opts.interval == DEFAULT_INTERVAL;
    let loop_cfg = LoopConfig {
        interval_secs: opts.interval,
        adaptive,
        once: false,
    };

    println!(
        "[sync] Running sync loop | interval={}{}",
        if adaptive {
            "adaptive".to_string()
        } else {
            format!("{}s", opts.interval)
        },
        if opts.push_only {
            " | push-only"
        } else if opts.pull_only {
            " | pull-only"
        } else {
            ""
        }
    );
    println!("[sync] Ctrl+C to stop");

    // Pending flush flag: set when a sync-flush marker is detected; causes the
    // next cycle to do a full push+pull regardless of --push-only/--pull-only.
    let mut pending_flush = false;

    run_daemon_loop_with_wake(
        &running,
        &loop_cfg,
        || {
            // Check for nudge/flush markers before each cycle.
            let signals = consume_sync_markers(&markers_dir);
            pending_flush = pending_flush || signals.flush;
            let nudged = signals.nudge;

            if config.connection_string.is_empty() {
                // No remote configured: nothing to do this cycle.
                return if nudged { Some(0) } else { Some(u64::MAX) };
            }

            // Build per-cycle opts, honouring pending_flush.
            let cycle_opts = if pending_flush {
                // Flush overrides push/pull-only: do both.
                SyncOpts {
                    push_only: false,
                    pull_only: false,
                    ..opts.clone()
                }
            } else {
                opts.clone()
            };

            let ok = run_sync_cycle(&cycle_opts, &config, &tools_dir).is_ok();
            if pending_flush && ok {
                pending_flush = false;
            }
            save_sync_state(&state_file, ok);

            // Return idle age — sync daemon uses time-since-last-activity for
            // adaptive intervals, not file mtimes.  For now always use idle tier
            // unless there was recent activity (approximated by success).
            if ok || nudged {
                Some(0) // treat a successful cycle as "active"
            } else {
                None // idle tier on error
            }
        },
        || {
            markers_dir.join("sync-nudge.json").exists()
                || markers_dir.join("sync-flush.json").exists()
        },
    );

    println!("[sync] Stopped.");
    lock.release();
    ExitCode::SUCCESS
}

// ── Sync cycle ────────────────────────────────────────────────────────────────

/// Run one sync cycle.
///
/// In the default build, calls the native Rust HTTP engine directly.  Builds
/// compiled without `native-sync` shell out to `sync-daemon.py --once`.
fn run_sync_cycle(opts: &SyncOpts, config: &SyncConfig, tools_dir: &Path) -> Result<(), String> {
    run_sync_cycle_impl(opts, config, tools_dir)
}

/// Native Rust implementation — compiled only with `native-sync`.
#[cfg(feature = "native-sync")]
fn run_sync_cycle_impl(
    opts: &SyncOpts,
    config: &SyncConfig,
    _tools_dir: &Path,
) -> Result<(), String> {
    let db_path = crate::config::resolve_copilot_dir()
        .join("session-state")
        .join("knowledge.db");
    crate::sync::engine::run_native_sync_cycle(
        &db_path,
        &config.connection_string,
        opts.limit,
        /* do_push */ !opts.pull_only,
        /* do_pull */ !opts.push_only,
    )
}

/// Python fallback — compiled only when `native-sync` is NOT active.
///
/// Delegates to `sync-daemon.py --once` for the actual HTTP push/pull.
/// The native daemon owns the lock, loop, and signal handling; Python does
/// the network I/O.  The default binary no longer uses this path, but it
/// remains available for no-feature or compatibility builds.
#[cfg(not(feature = "native-sync"))]
fn run_sync_cycle_impl(
    opts: &SyncOpts,
    _config: &SyncConfig,
    tools_dir: &Path,
) -> Result<(), String> {
    let script = tools_dir.join("sync-daemon.py");
    if !script.exists() {
        return Err(format!("sync-daemon.py not found at {}", script.display()));
    }

    let mut cmd = std::process::Command::new(python_exe());
    cmd.arg(&script).arg("--once");

    if opts.push_only {
        cmd.arg("--push-only");
    } else if opts.pull_only {
        cmd.arg("--pull-only");
    }

    if opts.limit != 50 {
        cmd.args(["--limit", &opts.limit.to_string()]);
    }

    match cmd.output() {
        Ok(out) if out.status.success() => {
            for line in String::from_utf8_lossy(&out.stdout).lines() {
                println!("[sync] {}", line.trim());
            }
            Ok(())
        }
        Ok(out) => {
            let stderr = String::from_utf8_lossy(&out.stderr);
            let preview = &stderr[..stderr.len().min(300)];
            let err = format!("sync-daemon.py exited with error: {preview}");
            eprintln!("[sync] {err}");
            Err(err)
        }
        Err(e) => {
            let err = format!("failed to spawn sync-daemon.py: {e}");
            eprintln!("[sync] {err}");
            Err(err)
        }
    }
}

// ── Marker detection ──────────────────────────────────────────────────────────

struct MarkerSignals {
    nudge: bool,
    flush: bool,
}

/// Read and consume sync-nudge / sync-flush marker files.
///
/// Mirrors the Python `_consume_sync_markers` function: checks for the
/// marker files, records their presence, then removes them so they fire once.
fn consume_sync_markers(markers_dir: &Path) -> MarkerSignals {
    let nudge_path = markers_dir.join("sync-nudge.json");
    let flush_path = markers_dir.join("sync-flush.json");

    let nudge = nudge_path.exists();
    let flush = flush_path.exists();

    if nudge {
        let _ = std::fs::remove_file(&nudge_path);
    }
    if flush {
        let _ = std::fs::remove_file(&flush_path);
    }

    MarkerSignals { nudge, flush }
}

// ── Config ────────────────────────────────────────────────────────────────────

#[derive(Default)]
struct SyncConfig {
    connection_string: String,
}

fn load_sync_config(tools_dir: &Path) -> SyncConfig {
    let config_path = tools_dir.join("sync-config.json");
    if !config_path.exists() {
        return SyncConfig::default();
    }
    let Ok(text) = std::fs::read_to_string(&config_path) else {
        return SyncConfig::default();
    };
    let Ok(val): Result<serde_json::Value, _> = serde_json::from_str(&text) else {
        return SyncConfig::default();
    };
    let connection_string = val
        .get("connection_string")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .to_string();
    SyncConfig { connection_string }
}

// ── State persistence ─────────────────────────────────────────────────────────

fn save_sync_state(state_file: &Path, ok: bool) {
    let now = chrono::Utc::now().to_rfc3339();
    let state = if ok {
        serde_json::json!({ "last_activity": now, "last_error": "" })
    } else {
        serde_json::json!({ "last_activity": "", "last_error": format!("cycle failed at {now}") })
    };
    let Ok(json) = serde_json::to_string_pretty(&state) else {
        return;
    };
    let tmp = state_file.with_extension("tmp");
    if std::fs::write(&tmp, json.as_bytes()).is_ok() {
        let _ = std::fs::rename(&tmp, state_file);
    }
}

// ── CLI parsing ───────────────────────────────────────────────────────────────

#[derive(Clone)]
struct SyncOpts {
    once: bool,
    daemon: bool,
    interval: u64,
    push_only: bool,
    pull_only: bool,
    limit: usize,
    help: bool,
}

fn parse_sync_args(args: &[String]) -> Result<SyncOpts, String> {
    let mut opts = SyncOpts {
        once: false,
        daemon: false,
        interval: DEFAULT_INTERVAL,
        push_only: false,
        pull_only: false,
        limit: 50,
        help: false,
    };
    let mut i = 0;
    while i < args.len() {
        match args[i].as_str() {
            "--once" => {
                opts.once = true;
                i += 1;
            }
            "--daemon" | "--service" => {
                opts.daemon = true;
                i += 1;
            }
            "--interval" if i + 1 < args.len() => {
                opts.interval = args[i + 1]
                    .parse()
                    .map_err(|_| format!("invalid --interval value: {}", args[i + 1]))?;
                i += 2;
            }
            "--limit" if i + 1 < args.len() => {
                opts.limit = args[i + 1]
                    .parse::<usize>()
                    .map(|n| n.max(1))
                    .map_err(|_| format!("invalid --limit value: {}", args[i + 1]))?;
                i += 2;
            }
            "--push-only" => {
                opts.push_only = true;
                i += 1;
            }
            "--pull-only" => {
                opts.pull_only = true;
                i += 1;
            }
            "--help" | "-h" => {
                opts.help = true;
                i += 1;
            }
            unknown => {
                eprintln!("[sync] Unknown flag: {unknown} (ignored)");
                i += 1;
            }
        }
    }
    if opts.push_only && opts.pull_only {
        return Err("--push-only and --pull-only cannot be used together".to_string());
    }
    Ok(opts)
}

const HELP_TEXT: &str = "\
sk sync run \u{2014} Local-first background push/pull sync daemon

USAGE:
    sk sync run                Run sync loop (adaptive interval)
    sk sync run --once         Single push/pull cycle then exit
    sk sync run --daemon       Background daemon (note: foreground currently)
    sk sync run --interval 30  Custom poll interval (seconds)
    sk sync run --push-only    Only push local changes
    sk sync run --pull-only    Only pull remote changes
    sk sync run --limit N      Max transactions per batch (default: 50)

Configure the remote endpoint first:
    sk sync config --setup <url>";
