//! Native session watcher for `sk watch`.
//!
//! Polls `~/.copilot/session-state/` and `~/.claude/projects/` for changed
//! `.md`, `.txt`, and `.jsonl` files and triggers indexing on changes.
//!
//! **Wave 3**: `.jsonl` Claude session files are now indexed natively by
//! `crate::index::claude`.
//!
//! **Wave 10**: `build-session-index.py --incremental` is no longer spawned for
//! existing-DB non-JSONL Copilot changes.  The three wave-4 Python-complement
//! gaps are now fully closed natively in `session.rs`:
//!
//! 1. `sessions_fts` population — wave-6 `write_copilot_sessions_fts()`
//! 2. Sessions-table column migrations — wave-5 `apply_sessions_column_migrations()`
//! 3. Sync-op enqueueing — wave-5 `enqueue_doc_sync_op_fail_open()`
//!
//! **Wave 17**: On a successful native extract pass, native residual helpers
//! (backfill_affected_files, infer_task_ids, confidence decay) run in Rust.
//! Python is only spawned for genuine DB open/create failures.
//!
//! **Wave 18**: First-run DB bootstrap is now native.  `session.rs`,
//! `claude.rs`, and `extract.rs` each call `open_or_create_index_db` /
//! `ensure_extract_tables` when the DB file is absent.  `build-session-index.py`
//! is no longer spawned for a missing DB.  `None` from the native indexers now
//! means *genuine creation failure* (not just "DB file absent"), so
//! `need_python_indexer` only triggers Python as a last-resort fallback for
//! real open/create errors.
//!
//! **Wave 19**: `SEMANTIC_PROXIMITY` (TF-IDF cosine ≥ 0.75) is now computed
//! natively by `extract.rs`.  The `extract-knowledge.py --semantic-only`
//! auto-spawn (wave 17, sklearn-gated) is removed.  On the successful native
//! watch path Python is **never** spawned — regardless of sklearn availability.
//! `extract-knowledge.py --semantic-only` remains available for manual or
//! fallback invocation but is no longer called automatically.
//!
//! **Wave 20**: The last-resort Python subprocess fallbacks on genuine DB
//! open/create errors are removed.  When native DB open/create fails, `watch`
//! emits a structured recovery hint naming the exact manual command to run.
//! `watch.rs` **never** spawns Python — not even on error paths.
//! `build-session-index.py` and `extract-knowledge.py` remain on disk for
//! manual operator use.
//!
//! ## CLI flags (aligned with watch-sessions.py)
//!
//! ```text
//! sk watch                   Run in foreground (Ctrl+C to stop)
//! sk watch --interval 30     Custom poll interval (seconds)
//! sk watch --once            Single check then exit
//! sk watch --daemon          Background process (Unix: note; Windows: foreground)
//! sk watch --changed-only    Print changed files before re-extracting
//! sk watch --install-hint    Print auto-start setup instructions
//! ```

use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::process::ExitCode;
use std::sync::atomic::AtomicBool;
use std::sync::Arc;
use std::time::{SystemTime, UNIX_EPOCH};

use crate::config::{resolve_copilot_dir, resolve_home_dir, resolve_tools_dir};
use crate::daemon::{install_shutdown_handler, run_daemon_loop, DaemonLock, LoopConfig};
use crate::index::claude as native_claude;
use crate::index::session as native_index;

const LOG_PREFIX: &str = "watch";
const DEFAULT_INTERVAL: u64 = 60;
const WATCH_EXTENSIONS: &[&str] = &["md", "txt", "jsonl"];

// ── Public entry point ────────────────────────────────────────────────────────

/// Entry point for `sk watch [args]`.
pub fn run_watch_command(args: &[String]) -> ExitCode {
    let opts = parse_watch_args(args);

    if opts.help {
        print_watch_help();
        return ExitCode::SUCCESS;
    }

    if opts.install_hint {
        print_install_hint();
        return ExitCode::SUCCESS;
    }

    let copilot_dir = resolve_copilot_dir();
    let session_state = copilot_dir.join("session-state");

    if !session_state.exists() {
        eprintln!(
            "[watch] Error: session-state directory not found: {}",
            session_state.display()
        );
        return ExitCode::from(1);
    }

    let lock_file = session_state.join(".watcher.lock");
    let state_file = session_state.join(".watch-state.json");
    let db_path = session_state.join("knowledge.db");

    let mut lock = match DaemonLock::acquire(&lock_file, LOG_PREFIX) {
        Ok(l) => l,
        Err(e) => {
            eprintln!("[watch] Error: {e}");
            return ExitCode::from(1);
        }
    };

    let running = Arc::new(AtomicBool::new(true));
    if let Err(e) = install_shutdown_handler(running.clone()) {
        eprintln!("[watch] Warning: {e}");
    }

    if opts.daemon {
        #[cfg(windows)]
        println!("[watch] Note: --daemon on Windows starts in foreground.");
        #[cfg(not(windows))]
        println!("[watch] Note: --daemon runs in foreground in the native watcher (native fork not yet implemented).");
    }

    let watch_dirs = build_watch_dirs(&copilot_dir);
    let dirs_str = watch_dirs
        .iter()
        .map(|p| p.display().to_string())
        .collect::<Vec<_>>()
        .join(", ");

    let interval_note = if opts.interval != DEFAULT_INTERVAL {
        format!("{}s", opts.interval)
    } else {
        "adaptive".to_string()
    };
    println!("[watch] Watching: {dirs_str}");
    println!("[watch] Poll interval: {interval_note} | Ctrl+C to stop");

    let mut state = load_watch_state(&state_file);
    let tools_dir = resolve_tools_dir();

    // adaptive = true when the user has NOT overridden --interval
    let adaptive = opts.interval == DEFAULT_INTERVAL;

    let loop_cfg = LoopConfig {
        interval_secs: opts.interval,
        adaptive,
        once: opts.once,
    };

    run_daemon_loop(&running, &loop_cfg, || {
        let age = check_and_index(
            &mut state,
            &watch_dirs,
            &tools_dir,
            &session_state,
            &db_path,
            opts.changed_only,
        );
        save_watch_state(&state_file, &state);
        age
    });

    println!("[watch] Stopped.");
    lock.release();
    ExitCode::SUCCESS
}

// ── File scanning and change detection ───────────────────────────────────────

/// (mtime_secs, size_bytes)
type Sig = (u64, u64);
type FileSigs = HashMap<String, Sig>;

#[derive(serde::Serialize, serde::Deserialize, Default)]
struct WatchState {
    signatures: FileSigs,
    last_index: Option<String>,
}

/// Directories to watch, matching the Python KNOWN_HOSTS list.
fn build_watch_dirs(copilot_dir: &Path) -> Vec<PathBuf> {
    let mut dirs = Vec::new();
    let ss = copilot_dir.join("session-state");
    if ss.exists() {
        dirs.push(ss);
    }
    if let Some(home) = resolve_home_dir() {
        let claude = home.join(".claude").join("projects");
        if claude.exists() {
            dirs.push(claude);
        }
    }
    dirs
}

fn scan_dir_recursive(dir: &Path, sigs: &mut FileSigs) {
    let Ok(entries) = std::fs::read_dir(dir) else {
        return;
    };
    for entry in entries.flatten() {
        let path = entry.path();
        if path.is_dir() {
            // Skip hidden directories (e.g. .git, .watch-state)
            let hidden = path
                .file_name()
                .map(|n| n.to_string_lossy().starts_with('.'))
                .unwrap_or(false);
            if !hidden {
                scan_dir_recursive(&path, sigs);
            }
        } else if is_watchable(&path) {
            if let Ok(meta) = path.metadata() {
                let mtime = meta
                    .modified()
                    .ok()
                    .and_then(|t| t.duration_since(UNIX_EPOCH).ok())
                    .map(|d| d.as_secs())
                    .unwrap_or(0);
                sigs.insert(path.to_string_lossy().into_owned(), (mtime, meta.len()));
            }
        }
    }
}

fn scan_files(dirs: &[PathBuf]) -> FileSigs {
    let mut sigs = FileSigs::new();
    for dir in dirs {
        scan_dir_recursive(dir, &mut sigs);
    }
    sigs
}

fn is_watchable(path: &Path) -> bool {
    path.extension()
        .and_then(|e| e.to_str())
        .map(|e| WATCH_EXTENSIONS.contains(&e))
        .unwrap_or(false)
}

/// Scan files, detect changes, and invoke the indexer on changes.
///
/// All indexing is now native Rust; `watch.rs` never spawns Python (wave 20).
///
/// Returns the age (seconds) of the most recently modified file — fed into
/// the adaptive interval calculation.
fn check_and_index(
    state: &mut WatchState,
    watch_dirs: &[PathBuf],
    tools_dir: &Path,
    session_state_dir: &Path,
    db_path: &Path,
    changed_only: bool,
) -> Option<u64> {
    let current = scan_files(watch_dirs);

    // Detect new or changed files (mtime or size changed).
    let changed: Vec<&str> = current
        .iter()
        .filter(|(path, &(mtime, size))| {
            state
                .signatures
                .get(*path)
                .map(|&(pm, ps)| pm != mtime || ps != size)
                .unwrap_or(true) // new file
        })
        .map(|(path, _)| path.as_str())
        .collect();

    if !changed.is_empty() {
        let now = chrono::Local::now().format("%H:%M:%S");
        println!(
            "[watch] {now} — {} file(s) changed, re-indexing\u{2026}",
            changed.len()
        );

        if changed_only {
            for path in changed.iter().take(20) {
                println!("[watch]   {path}");
            }
            if changed.len() > 20 {
                println!("[watch]   \u{2026} and {} more", changed.len() - 20);
            }
        }

        // --- Native Rust indexer (Copilot session-state docs) ---
        // Attempts to index changed Copilot session directories without
        // spawning a Python subprocess. Returns None only on a genuine
        // open/create failure; a missing DB is now created natively.
        let native_stats = native_index::index_changed_sessions(
            &changed,
            session_state_dir,
            db_path,
            true, // incremental
        );
        if let Some(stats) = &native_stats {
            if stats.any_indexed() {
                println!("[watch] Native indexed: {stats}");
            }
        }

        // --- Native Rust Claude indexer (Wave 3) ---
        // Handles .jsonl Claude session files natively — no Python subprocess.
        // Returns None only on a genuine open/create failure.
        let has_jsonl = changed.iter().any(|p| p.ends_with(".jsonl"));
        let has_non_jsonl = changed.iter().any(|p| !p.ends_with(".jsonl"));

        let claude_native_ok = if has_jsonl {
            match native_claude::index_changed_claude_sessions(&changed, db_path) {
                Some(stats) => {
                    if stats.events_indexed > 0 {
                        println!("[watch] Claude native: {stats}");
                    }
                    true
                }
                None => false, // DB open/create failure — recovery message below
            }
        } else {
            true // No JSONL changes — nothing to do here
        };

        // --- Wave-20: Recovery guidance on DB open/create failure ---
        // Wave-18: `index_changed_sessions` and `index_changed_claude_sessions` now
        // create the DB natively on first run.  They only return `None` on a genuine
        // creation failure (e.g. filesystem permission error), not merely for "DB absent".
        //
        // `native_stats == None` or `!claude_native_ok` means a real open/create
        // failure occurred.  Wave-20 replaces the Python subprocess with a structured
        // recovery message naming the exact manual command to run.  Python is never
        // spawned automatically — not even on error paths.
        //
        // See integration_test.rs `wave20_db_failure_emits_recovery_no_python_spawn`.
        let need_python_indexer = native_stats.is_none() || !claude_native_ok;
        if need_python_indexer {
            // Wave-20: emit recovery hint instead of spawning Python.
            eprintln!(
                "[watch] ERROR: Native DB open/create failed (filesystem permission error or disk full).\n\
                 [watch] Recovery hint: python {} --incremental\n\
                 [watch] Automatic Python fallback suppressed (wave-20).",
                tools_dir.join("build-session-index.py").display()
            );
        }

        // --- Wave-14/16/17/19: Native extract hot path (feature-gated) ---
        // Runs classification/title/tag/topic_key/content_hash/knowledge_entries
        // writes, deterministic relation extraction (SAME_SESSION, SAME_TOPIC,
        // TAG_OVERLAP, RESOLVED_BY), and SEMANTIC_PROXIMITY (wave 19) natively.
        //
        // Wave 17/19 routing (successful native pass):
        //   1. Run native residual helpers: backfill_affected_files, infer_task_ids,
        //      confidence decay.  All three are Rust-native (no sklearn needed).
        //   2. SEMANTIC_PROXIMITY is Rust-native (wave 19) — no Python spawn.
        //   → Successful native path: Python is NEVER spawned.
        //
        // Failure path (genuine DB open/create failure or native extract error):
        // emit recovery guidance only (wave-20, no Python spawn).
        //
        let mut native_extract_ok = false;
        #[cfg(feature = "native-extract")]
        let mut native_extract_needs_recovery = false;
        #[cfg(feature = "native-extract")]
        if has_non_jsonl {
            match crate::index::extract::extract_from_changed_sessions(
                &changed,
                session_state_dir,
                db_path,
            ) {
                Some(Ok(s)) => {
                    if s.extracted > 0 || s.relations_extracted > 0 {
                        println!(
                            "[watch] Native extract: {} new, {} deduped, {} relations",
                            s.extracted, s.deduped, s.relations_extracted
                        );
                    }
                    native_extract_ok = true;
                }
                Some(Err(e)) => eprintln!("[watch] Native extract error (continuing): {e}"),
                None => {
                    native_extract_needs_recovery = true;
                } // Genuine DB creation failure — recovery hint emitted below
            }
        }

        if native_extract_ok {
            // Wave 17/19: run residual helpers and SEMANTIC_PROXIMITY natively.
            // No Python spawn — the successful native path is fully Rust.
            #[cfg(feature = "native-extract")]
            crate::index::extract::run_native_residual_helpers_for_changed(
                &changed,
                session_state_dir,
                db_path,
            );
        }
        #[cfg(feature = "native-extract")]
        if !native_extract_ok && has_non_jsonl && native_extract_needs_recovery {
            // Wave-20: emit recovery hint instead of spawning Python.
            eprintln!(
                "[watch] ERROR: Native extract did not complete (DB unavailable or error).\n\
                 [watch] Recovery hint: python {}\n\
                 [watch] Automatic Python fallback suppressed (wave-20).",
                tools_dir.join("extract-knowledge.py").display()
            );
        }

        state.last_index = Some(chrono::Utc::now().to_rfc3339());
    }

    state.signatures = current;

    // Return age of most recently modified file for adaptive intervals.
    let now_secs = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0);
    state
        .signatures
        .values()
        .map(|&(mtime, _)| now_secs.saturating_sub(mtime))
        .min()
}

// ── State persistence ─────────────────────────────────────────────────────────

fn load_watch_state(state_file: &Path) -> WatchState {
    if !state_file.exists() {
        return WatchState::default();
    }
    std::fs::read_to_string(state_file)
        .ok()
        .and_then(|s| serde_json::from_str(&s).ok())
        .unwrap_or_default()
}

fn save_watch_state(state_file: &Path, state: &WatchState) {
    let Ok(json) = serde_json::to_string_pretty(state) else {
        return;
    };
    let tmp = state_file.with_extension("tmp");
    if std::fs::write(&tmp, json.as_bytes()).is_ok() {
        let _ = std::fs::rename(&tmp, state_file);
    }
}

// ── CLI parsing ───────────────────────────────────────────────────────────────

struct WatchOpts {
    interval: u64,
    once: bool,
    daemon: bool,
    changed_only: bool,
    install_hint: bool,
    help: bool,
}

fn parse_watch_args(args: &[String]) -> WatchOpts {
    let mut opts = WatchOpts {
        interval: DEFAULT_INTERVAL,
        once: false,
        daemon: false,
        changed_only: false,
        install_hint: false,
        help: false,
    };
    let mut i = 0;
    while i < args.len() {
        match args[i].as_str() {
            "--interval" if i + 1 < args.len() => {
                opts.interval = args[i + 1].parse().unwrap_or(DEFAULT_INTERVAL);
                i += 2;
            }
            "--once" => {
                opts.once = true;
                i += 1;
            }
            "--daemon" | "--service" => {
                opts.daemon = true;
                i += 1;
            }
            "--changed-only" => {
                opts.changed_only = true;
                i += 1;
            }
            "--install-hint" => {
                opts.install_hint = true;
                i += 1;
            }
            "--help" | "-h" => {
                opts.help = true;
                i += 1;
            }
            _ => i += 1,
        }
    }
    opts
}

fn print_watch_help() {
    println!(
        "sk watch \u{2014} Auto-index Copilot session-state on changes\n\
         \n\
         USAGE:\n\
         \x20   sk watch                   Run in foreground (Ctrl+C to stop)\n\
         \x20   sk watch --interval 30     Custom poll interval (seconds)\n\
         \x20   sk watch --once            Single check then exit\n\
         \x20   sk watch --daemon          Background process\n\
         \x20   sk watch --changed-only    Print changed files before re-extracting\n\
         \x20   sk watch --install-hint    Print auto-start setup instructions"
    );
}

fn print_install_hint() {
    let exe = std::env::current_exe().unwrap_or_else(|_| PathBuf::from("sk"));
    println!(
        "{}",
        render_install_hint(std::env::consts::OS, &exe.display().to_string())
    );
}

fn render_install_hint(os_name: &str, exe: &str) -> String {
    match os_name {
        "windows" => format!(
            "# Windows \u{2014} Task Scheduler (run at logon):\n\
             schtasks /create /tn \"CopilotSessionWatcher\" /tr '\"{exe}\" watch' /sc onlogon /f\n\
             \n\
             # Start now:\n\
             schtasks /run /tn \"CopilotSessionWatcher\"\n\
             \n\
             # Remove:\n\
             schtasks /end /tn \"CopilotSessionWatcher\"\n\
             schtasks /delete /tn \"CopilotSessionWatcher\" /f"
        ),
        "macos" => format!(
            "# macOS \u{2014} launchd user agent (~/Library/LaunchAgents/dev.linhngo.sk-watcher.plist):\n\
             mkdir -p ~/Library/LaunchAgents\n\
             cat > ~/Library/LaunchAgents/dev.linhngo.sk-watcher.plist <<'PLIST'\n\
             <?xml version=\"1.0\" encoding=\"UTF-8\"?>\n\
             <!DOCTYPE plist PUBLIC \"-//Apple//DTD PLIST 1.0//EN\" \"http://www.apple.com/DTDs/PropertyList-1.0.dtd\">\n\
             <plist version=\"1.0\">\n\
             <dict>\n\
               <key>Label</key>\n\
               <string>dev.linhngo.sk-watcher</string>\n\
               <key>ProgramArguments</key>\n\
               <array>\n\
                 <string>{exe}</string>\n\
                 <string>watch</string>\n\
               </array>\n\
               <key>RunAtLoad</key>\n\
               <true/>\n\
               <key>KeepAlive</key>\n\
               <true/>\n\
             </dict>\n\
             </plist>\n\
             PLIST\n\
             \n\
             # Load now:\n\
             launchctl unload ~/Library/LaunchAgents/dev.linhngo.sk-watcher.plist 2>/dev/null || true\n\
             launchctl load ~/Library/LaunchAgents/dev.linhngo.sk-watcher.plist\n\
             \n\
             # Remove:\n\
             launchctl unload ~/Library/LaunchAgents/dev.linhngo.sk-watcher.plist\n\
             rm ~/Library/LaunchAgents/dev.linhngo.sk-watcher.plist"
        ),
        _ => format!(
            "# Linux \u{2014} crontab (`crontab -e`):\n\
             @reboot {exe} watch\n\
             \n\
             # Or systemd user service (~/.config/systemd/user/sk-watcher.service):\n\
             [Unit]\n\
             Description=sk session watcher\n\
             [Service]\n\
             ExecStart={exe} watch\n\
             Restart=on-failure\n\
             [Install]\n\
             WantedBy=default.target"
        ),
    }
}

#[cfg(test)]
mod tests {
    use super::render_install_hint;

    #[test]
    fn install_hint_windows_mentions_task_scheduler() {
        let hint = render_install_hint("windows", "sk.exe");
        assert!(hint.contains("Task Scheduler"));
        assert!(hint.contains("schtasks /create"));
    }

    #[test]
    fn install_hint_linux_mentions_systemd() {
        let hint = render_install_hint("linux", "sk");
        assert!(hint.contains("systemd user service"));
        assert!(hint.contains("@reboot sk watch"));
    }

    #[test]
    fn install_hint_macos_mentions_launchd_not_systemd() {
        let hint = render_install_hint("macos", "sk");
        assert!(hint.contains("launchd user agent"));
        assert!(hint.contains("launchctl load"));
        assert!(!hint.contains("systemd"));
    }
}
