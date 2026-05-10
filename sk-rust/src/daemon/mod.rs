//! Shared daemon harness: atomic lock file, PID verification, signal-driven
//! shutdown, and adaptive poll loop.
//!
//! Used by both `sk watch` and `sk sync run` so that lock / signal / loop
//! behaviour is implemented once and tested in one place.

use std::fs;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

// ── Lock file ─────────────────────────────────────────────────────────────────

/// Atomic, PID-bearing lock file.
///
/// Acquiring the lock writes the current process ID into `lock_file` using
/// `OpenOptions::create_new` (equivalent to `O_CREAT | O_EXCL`).  If the
/// file already exists the holder's PID is checked; stale locks (holder not
/// running) are silently replaced.  The lock is released on `Drop`.
pub struct DaemonLock {
    path: PathBuf,
    owned: bool,
}

impl DaemonLock {
    /// Attempt to acquire the lock. Returns `Ok(lock)` on success.
    pub fn acquire(lock_file: &Path, log_prefix: &str) -> Result<Self, String> {
        // Fast path: atomic create succeeds immediately.
        if Self::try_create(lock_file).is_ok() {
            return Ok(Self {
                path: lock_file.to_owned(),
                owned: true,
            });
        }

        // Lock file already exists — inspect holder.
        match read_lock_pid(lock_file) {
            Some(pid) if is_pid_running(pid) => {
                return Err(format!(
                    "another {log_prefix} is already running (PID {pid}). \
                     Remove {} if stale.",
                    lock_file.display()
                ));
            }
            Some(pid) => {
                eprintln!("[{log_prefix}] Removing stale lock (PID {pid} no longer running)");
            }
            None => {
                eprintln!("[{log_prefix}] Removing unreadable lock file");
            }
        }

        // Remove stale lock and retry once.
        let _ = fs::remove_file(lock_file);
        Self::try_create(lock_file)
            .map(|()| Self {
                path: lock_file.to_owned(),
                owned: true,
            })
            .map_err(|e| format!("could not acquire lock (race?): {e}"))
    }

    fn try_create(lock_file: &Path) -> std::io::Result<()> {
        use std::io::Write;
        let mut f = fs::OpenOptions::new()
            .write(true)
            .create_new(true) // O_CREAT | O_EXCL equivalent
            .open(lock_file)?;
        write!(f, "{}", std::process::id())?;
        Ok(())
    }

    /// Explicitly release the lock.  Calling this is optional; `Drop` handles it.
    pub fn release(&mut self) {
        if self.owned {
            // Only delete if our PID is still recorded (guards against accidental
            // double-release or lock theft by another process).
            if read_lock_pid(&self.path) == Some(std::process::id()) {
                let _ = fs::remove_file(&self.path);
            }
            self.owned = false;
        }
    }
}

impl Drop for DaemonLock {
    fn drop(&mut self) {
        self.release();
    }
}

fn read_lock_pid(path: &Path) -> Option<u32> {
    fs::read_to_string(path).ok()?.trim().parse().ok()
}

// ── PID check ─────────────────────────────────────────────────────────────────

/// Returns `true` if the process with `pid` is currently running.
pub fn is_pid_running(pid: u32) -> bool {
    is_pid_running_impl(pid)
}

#[cfg(target_os = "windows")]
fn is_pid_running_impl(pid: u32) -> bool {
    // Use OpenProcess with PROCESS_QUERY_LIMITED_INFORMATION.
    // No winapi crate needed — declare the symbols inline.
    extern "system" {
        fn OpenProcess(
            desired_access: u32,
            inherit_handle: i32,
            pid: u32,
        ) -> *mut core::ffi::c_void;
        fn CloseHandle(handle: *mut core::ffi::c_void) -> i32;
    }
    const PROCESS_QUERY_LIMITED_INFORMATION: u32 = 0x1000;
    unsafe {
        let handle = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, 0, pid);
        if handle.is_null() {
            return false;
        }
        CloseHandle(handle);
        true
    }
}

#[cfg(target_os = "linux")]
fn is_pid_running_impl(pid: u32) -> bool {
    // /proc/{pid} exists iff the process is alive on Linux.
    Path::new(&format!("/proc/{pid}")).exists()
}

#[cfg(not(any(target_os = "windows", target_os = "linux")))]
fn is_pid_running_impl(pid: u32) -> bool {
    // macOS / other POSIX: send signal 0 via the shell.
    std::process::Command::new("kill")
        .args(["-0", &pid.to_string()])
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::null())
        .status()
        .map(|s| s.success())
        .unwrap_or(false)
}

// ── Signal / Ctrl-C ───────────────────────────────────────────────────────────

/// Install a Ctrl-C / SIGTERM handler that clears `running`.
///
/// Uses the `ctrlc` crate for cross-platform signal interception.  When
/// Ctrl-C is received the `running` flag is set to `false`; the poll loop
/// exits on the next 1-second tick, then `Drop` releases the lock cleanly.
pub fn install_shutdown_handler(running: Arc<AtomicBool>) -> Result<(), String> {
    ctrlc::set_handler(move || {
        println!();
        running.store(false, Ordering::SeqCst);
    })
    .map_err(|e| format!("failed to install Ctrl-C handler: {e}"))
}

// ── Adaptive poll interval ────────────────────────────────────────────────────

/// Tiered adaptive poll interval matching the Python watch-sessions.py tiers:
///
/// | Tier   | Condition                  | Interval |
/// |--------|----------------------------|----------|
/// | Active | most-recent age ≤ 2 min    | 5 s      |
/// | Recent | most-recent age ≤ 1 hour   | 30 s     |
/// | Idle   | older or no files          | 300 s    |
pub fn adaptive_interval(most_recent_age_secs: Option<u64>) -> Duration {
    match most_recent_age_secs {
        Some(age) if age <= 120 => Duration::from_secs(5),
        Some(age) if age <= 3600 => Duration::from_secs(30),
        _ => Duration::from_secs(300),
    }
}

/// Return the age in seconds of `mtime` relative to now, or `None` on error.
pub fn mtime_age_secs(mtime: SystemTime) -> Option<u64> {
    let now = SystemTime::now().duration_since(UNIX_EPOCH).ok()?.as_secs();
    let ts = mtime.duration_since(UNIX_EPOCH).ok()?.as_secs();
    Some(now.saturating_sub(ts))
}

// ── Poll loop configuration ───────────────────────────────────────────────────

/// Configuration passed to [`run_daemon_loop`].
pub struct LoopConfig {
    /// Fixed poll interval (seconds).  Ignored when `adaptive` is `true`.
    pub interval_secs: u64,
    /// Use adaptive tiered intervals instead of the fixed `interval_secs`.
    pub adaptive: bool,
    /// Run exactly one tick then exit.
    pub once: bool,
}

/// Run a signal-aware daemon poll loop until `running` becomes `false`.
///
/// `tick` is called once per cycle.  It should perform the unit of work
/// (file scan, sync cycle, …) and return the age in seconds of the most
/// recently modified relevant file — used for adaptive interval calculation.
/// Returning `None` means "no relevant files seen"; the idle tier (300 s) is
/// used.
///
/// The loop sleeps in 1-second increments so that a Ctrl-C signal wakes it
/// within at most one second.
pub fn run_daemon_loop<F>(running: &Arc<AtomicBool>, config: &LoopConfig, mut tick: F)
where
    F: FnMut() -> Option<u64>,
{
    run_daemon_loop_with_wake(running, config, &mut tick, || false);
}

/// Run a signal-aware daemon poll loop with an optional early-wake hook.
///
/// `should_wake` is checked once per 1-second sleep tick. When it returns
/// `true`, the loop starts the next work cycle immediately instead of waiting
/// for the full adaptive interval to elapse.
pub fn run_daemon_loop_with_wake<F, W>(
    running: &Arc<AtomicBool>,
    config: &LoopConfig,
    mut tick: F,
    mut should_wake: W,
) where
    F: FnMut() -> Option<u64>,
    W: FnMut() -> bool,
{
    loop {
        let age = tick();

        if config.once || !running.load(Ordering::SeqCst) {
            break;
        }

        let sleep_for = if config.adaptive {
            adaptive_interval(age)
        } else {
            Duration::from_secs(config.interval_secs.max(1))
        };

        // Sleep in 1-second increments for responsive shutdown.
        let ticks = sleep_for.as_secs().max(1);
        let mut woke_early = false;
        for _ in 0..ticks {
            if !running.load(Ordering::SeqCst) {
                return;
            }
            if should_wake() {
                woke_early = true;
                break;
            }
            std::thread::sleep(Duration::from_secs(1));
        }

        if !running.load(Ordering::SeqCst) {
            break;
        }
        if woke_early {
            continue;
        }
    }
}
