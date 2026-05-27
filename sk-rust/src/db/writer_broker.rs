//! Writer broker for `sk learn` (#572).
//!
//! Opt-in via `SK_WRITER_BROKER=1`. When enabled, `sk learn` sends each
//! write request to a long-lived broker process over a local IPC
//! channel; the broker holds a single writable SQLite connection and
//! serializes every write. This collapses N concurrent `sk learn`
//! invocations into 1 SQLite writer, eliminating SQLITE_BUSY
//! contention observed under heavy multi-agent fan-out.
//!
//! ## Transport
//! * **Unix:** `~/.copilot/run/sk-writer.sock` — `chmod 0600`,
//!   owner-only Unix-domain socket.
//! * **Windows:** `\\.\pipe\sk-writer-<user>` — local named pipe with
//!   an explicit owner-only DACL (SDDL `O:<sid>D:P(A;;GA;;;<sid>)`
//!   where `<sid>` is the current process token user) plus
//!   `PIPE_REJECT_REMOTE_CLIENTS` so no network peer can connect and
//!   no other local user can open the pipe. Pinning the owner to the
//!   user SID (rather than relying on Windows' default-owner rule,
//!   which falls back to `BUILTIN\Administrators` for elevated
//!   tokens) keeps the `ensure_owned_by_current_user` invariant
//!   stable on Admin-elevated sessions such as CI runners. The first
//!   instance is created with `FILE_FLAG_FIRST_PIPE_INSTANCE` so the
//!   broker refuses to start if another process is squatting on the
//!   name.
//!
//! ## Security
//! * Local IPC only — no network listener.
//! * On Unix the socket file is owner-only (`0600`); same-user
//!   processes can connect.
//! * Every payload is authenticated by a per-broker random 32-byte
//!   session key stored in `~/.copilot/run/sk-writer.key` (also
//!   `0600`). Clients HMAC-SHA256 each request body with that key
//!   and the broker rejects any payload that fails verification. A
//!   hostile *other-user* process cannot read the key file and
//!   therefore cannot forge writes; a hostile *same-user* process
//!   that doesn't bother to read the key file cannot inject raw rows
//!   either.
//!
//! ## Wire protocol (request and response share the same framing)
//!   [4-byte big-endian length N (u32)] [32-byte HMAC-SHA256(body)] [N bytes JSON body]
//!
//! Request body:  `{"op":"write","entry":{...}}` or `{"op":"ping"}`.
//! Response body: `{"ok":true,"entry_id":<i64>,"latency_ms":<f64>}`
//!             or `{"ok":false,"error":"<msg>","busy":<bool>}`.
//!
//! ## Idle exit
//! The broker exits 60 seconds after the most recent client request,
//! removing its socket and PID file. The next `sk learn` invocation
//! re-spawns it.

use crate::config::resolve_home_dir;
use crate::db::write::{insert_or_update_entry, rebuild_fts, NewEntry};
use hmac::{Hmac, Mac};
use serde_json::{json, Value};
use sha2::Sha256;
use std::fs;
use std::io::{self, Read, Write};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

/// Monotonic per-process counter used to make `atomic_write_owner_only`
/// tmp paths unique even when two threads call it in the same nanosecond.
static TMP_WRITE_COUNTER: AtomicU64 = AtomicU64::new(0);

type HmacSha256 = Hmac<Sha256>;

const MAX_BODY_BYTES: u32 = 1_048_576; // 1 MiB
const HMAC_LEN: usize = 32;
const IDLE_EXIT_SECONDS: u64 = 60;
const SAMPLE_RING_CAPACITY: usize = 256;
const SAMPLES_STALENESS_SECS: u64 = 300;
const SPAWN_HANDSHAKE_MS: u64 = 2_000;

// ── Public client API ────────────────────────────────────────────────────

/// Errors a broker client surfaces to `sk learn`.
#[derive(Debug)]
pub enum BrokerError {
    /// Broker not supported on this platform (e.g. Windows today).
    #[allow(dead_code)]
    Unsupported,
    /// Could not spawn or connect to the broker. Caller should fall
    /// back to the direct-write path.
    Unavailable(String),
    /// SQLite reported BUSY/LOCKED via the broker. Caller should
    /// take the queue path (matches direct-write behavior).
    Busy(String),
    /// Anything else (protocol, IO, JSON, or server error).
    Other(String),
}

impl std::fmt::Display for BrokerError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            BrokerError::Unsupported => write!(f, "writer broker not supported on this platform"),
            BrokerError::Unavailable(s) => write!(f, "writer broker unavailable: {s}"),
            BrokerError::Busy(s) => write!(f, "writer broker reported busy: {s}"),
            BrokerError::Other(s) => write!(f, "writer broker error: {s}"),
        }
    }
}

/// Returns true when `SK_WRITER_BROKER=1` (or `true`). Any other value
/// (`0`, empty, unset) keeps the direct-write path.
pub fn broker_enabled() -> bool {
    std::env::var("SK_WRITER_BROKER")
        .map(|v| v == "1" || v.eq_ignore_ascii_case("true"))
        .unwrap_or(false)
}

#[cfg(unix)]
pub fn socket_path() -> PathBuf {
    if let Ok(p) = std::env::var("SK_WRITER_BROKER_SOCK") {
        return PathBuf::from(p);
    }
    run_dir().join("sk-writer.sock")
}

pub fn pid_path() -> PathBuf {
    if let Ok(p) = std::env::var("SK_WRITER_BROKER_PID") {
        return PathBuf::from(p);
    }
    run_dir().join("sk-writer.pid")
}

pub fn key_path() -> PathBuf {
    if let Ok(p) = std::env::var("SK_WRITER_BROKER_KEY") {
        return PathBuf::from(p);
    }
    run_dir().join("sk-writer.key")
}

pub fn samples_path() -> PathBuf {
    if let Ok(p) = std::env::var("SK_WRITER_BROKER_SAMPLES") {
        return PathBuf::from(p);
    }
    run_dir().join("sk-writer.samples.json")
}

fn run_dir() -> PathBuf {
    resolve_home_dir()
        .unwrap_or_else(|| PathBuf::from("."))
        .join(".copilot")
        .join("run")
}

// ── Latency sample reader (consumed by --writer-stats) ───────────────────

/// Read the latency-samples file and return (p50_ms, p95_ms) if a
/// recent broker has recorded samples. Stale or missing → `None`
/// so writer-stats keeps the contract `null` for p50/p95.
pub fn read_broker_latency_p50_p95() -> Option<(f64, f64)> {
    let raw = fs::read_to_string(samples_path()).ok()?;
    let val: Value = serde_json::from_str(&raw).ok()?;
    let updated_ms = val.get("updated_ms").and_then(|v| v.as_u64()).unwrap_or(0);
    let now_ms = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_millis() as u64)
        .unwrap_or(0);
    if now_ms.saturating_sub(updated_ms) > SAMPLES_STALENESS_SECS * 1000 {
        return None;
    }
    let arr = val.get("samples_ms").and_then(|v| v.as_array())?;
    let mut samples: Vec<f64> = arr.iter().filter_map(|v| v.as_f64()).collect();
    if samples.is_empty() {
        return None;
    }
    samples.sort_by(|a, b| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal));
    Some((percentile(&samples, 50.0), percentile(&samples, 95.0)))
}

fn percentile(sorted: &[f64], p: f64) -> f64 {
    if sorted.is_empty() {
        return 0.0;
    }
    let rank = (p / 100.0) * ((sorted.len() - 1) as f64);
    let lo = rank.floor() as usize;
    let hi = rank.ceil() as usize;
    if lo == hi {
        return sorted[lo];
    }
    let frac = rank - lo as f64;
    sorted[lo] * (1.0 - frac) + sorted[hi] * frac
}

// ── Client: write_via_broker ─────────────────────────────────────────────

/// Send a write request to the broker; auto-spawn if no broker is
/// running. Returns the inserted entry row id on success.
pub fn write_via_broker(entry: &NewEntry) -> Result<i64, BrokerError> {
    #[cfg(unix)]
    {
        unix_impl::client_write(entry)
    }
    #[cfg(windows)]
    {
        windows_impl::client_write(entry)
    }
    #[cfg(not(any(unix, windows)))]
    {
        let _ = entry;
        Err(BrokerError::Unsupported)
    }
}

// ── Server entry: run_broker_daemon (called from main.rs) ────────────────

pub fn run_broker_daemon() -> std::process::ExitCode {
    #[cfg(unix)]
    {
        match unix_impl::run_server() {
            Ok(()) => std::process::ExitCode::SUCCESS,
            Err(e) => {
                eprintln!("sk writer-broker: {e}");
                std::process::ExitCode::from(1)
            }
        }
    }
    #[cfg(windows)]
    {
        match windows_impl::run_server() {
            Ok(()) => std::process::ExitCode::SUCCESS,
            Err(e) => {
                eprintln!("sk writer-broker: {e}");
                std::process::ExitCode::from(1)
            }
        }
    }
    #[cfg(not(any(unix, windows)))]
    {
        eprintln!("sk writer-broker: not supported on this platform");
        std::process::ExitCode::from(2)
    }
}

// ── Shared framing helpers ───────────────────────────────────────────────

/// Load the broker session key from `path`, creating it on first use.
///
/// Security invariants enforced on every call (Unix):
///   1. `path` must NOT be a symlink. Symlinks are rejected outright so
///      a hostile same-user process cannot redirect the key file.
///   2. `path` must be a regular file.
///   3. `path` must be owned by the current effective uid.
///   4. `path` mode bits must have no group or world permissions
///      (`mode & 0o077 == 0`).
///   5. `path` must be exactly 32 bytes.
///
/// If the file does not exist we generate 32 bytes of platform-secure
/// randomness (`/dev/urandom` on Unix, `BCryptGenRandom` with
/// `BCRYPT_USE_SYSTEM_PREFERRED_RNG` on Windows) and create the file
/// with platform-correct owner-only semantics:
///
/// * Unix: `O_CREAT|O_EXCL` + mode `0o600` via
///   `OpenOptions::create_new` + `OpenOptionsExt::mode`.
/// * Windows: `CreateFileW(CREATE_NEW, ...)` with an explicit
///   owner-only DACL (`O:<sid>D:P(A;;GA;;;<sid>)`, where `<sid>` is
///   the current process token user) supplied via
///   `SECURITY_ATTRIBUTES`, so the new file is **never** born with the
///   default inheritable ACL of the parent directory and its owner is
///   pinned to the current user (not `BUILTIN\Administrators` under
///   elevation). Inherited ACEs on the user profile directory are
///   typically owner-only on a single-user box, but on shared/
///   domain-joined hosts they may grant `Authenticated Users` or
///   `Administrators` read access; the explicit Protected DACL forces
///   "creator-only" regardless.
///
/// The key is never world/group-readable, even momentarily, and a
/// racing client cannot inject a chosen key (the second creator hits
/// `AlreadyExists` and falls back to the read+validate path). The
/// whole sequence loops a small bounded number of times to absorb the
/// cold-start race.
fn load_or_create_key(path: &Path) -> Result<[u8; 32], String> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)
            .map_err(|e| format!("create key parent {}: {e}", parent.display()))?;
    }
    for _ in 0..8 {
        match read_validated_key(path) {
            Ok(k) => return Ok(k),
            Err(KeyReadError::NotFound) => match try_create_new_key(path) {
                Ok(k) => return Ok(k),
                Err(KeyCreateError::AlreadyExists) => continue,
                Err(KeyCreateError::Other(e)) => return Err(e),
            },
            Err(KeyReadError::Other(e)) => return Err(e),
        }
    }
    Err(format!(
        "writer-broker: key file race did not converge after retries: {}",
        path.display()
    ))
}

enum KeyReadError {
    NotFound,
    Other(String),
}

enum KeyCreateError {
    AlreadyExists,
    Other(String),
}

fn read_validated_key(path: &Path) -> Result<[u8; 32], KeyReadError> {
    let meta = match fs::symlink_metadata(path) {
        Ok(m) => m,
        Err(e) if e.kind() == io::ErrorKind::NotFound => return Err(KeyReadError::NotFound),
        Err(e) => {
            return Err(KeyReadError::Other(format!(
                "stat key {}: {e}",
                path.display()
            )))
        }
    };
    if meta.file_type().is_symlink() {
        return Err(KeyReadError::Other(format!(
            "writer-broker key {} is a symlink; refusing",
            path.display()
        )));
    }
    if !meta.file_type().is_file() {
        return Err(KeyReadError::Other(format!(
            "writer-broker key {} is not a regular file; refusing",
            path.display()
        )));
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::MetadataExt;
        let euid = current_euid();
        if meta.uid() != euid {
            return Err(KeyReadError::Other(format!(
                "writer-broker key {} owned by uid {}, expected {}; refusing",
                path.display(),
                meta.uid(),
                euid
            )));
        }
        let mode = meta.mode() & 0o777;
        if mode & 0o077 != 0 {
            return Err(KeyReadError::Other(format!(
                "writer-broker key {} has insecure mode 0o{:o}; expected owner-only \
                 (no group/world bits); refusing",
                path.display(),
                mode
            )));
        }
    }
    #[cfg(windows)]
    {
        // Equivalent of Unix uid check: the file's owner SID must
        // equal the current process token's user SID. NTFS inherited
        // ACLs from the user profile directory are already owner-only
        // by default, but we still refuse to read a key whose owner
        // does not match — defence in depth against an attacker who
        // somehow planted the file under a different account before
        // the broker first ran.
        windows_impl::ensure_owned_by_current_user(path).map_err(KeyReadError::Other)?;
    }
    let raw = fs::read(path)
        .map_err(|e| KeyReadError::Other(format!("read key {}: {e}", path.display())))?;
    if raw.len() != 32 {
        return Err(KeyReadError::Other(format!(
            "writer-broker key {} has wrong size ({} bytes; expected 32)",
            path.display(),
            raw.len()
        )));
    }
    let mut k = [0u8; 32];
    k.copy_from_slice(&raw);
    Ok(k)
}

fn try_create_new_key(path: &Path) -> Result<[u8; 32], KeyCreateError> {
    let mut buf = [0u8; 32];
    fill_secure_random(&mut buf).map_err(KeyCreateError::Other)?;
    let mut f = match open_create_new_owner_only(path) {
        Ok(f) => f,
        Err(OpenCreateNewError::AlreadyExists) => return Err(KeyCreateError::AlreadyExists),
        Err(OpenCreateNewError::Other(e)) => return Err(KeyCreateError::Other(e)),
    };
    f.write_all(&buf)
        .map_err(|e| KeyCreateError::Other(format!("write key: {e}")))?;
    let _ = f.sync_all();
    Ok(buf)
}

/// Outcome of `open_create_new_owner_only`. Modeled after
/// `KeyCreateError` so callers can distinguish the harmless
/// "someone else won the create-race" case from real errors.
enum OpenCreateNewError {
    AlreadyExists,
    Other(String),
}

/// Fill `buf` with cryptographically-secure random bytes from the
/// platform's preferred CSPRNG. Unix reads `/dev/urandom`; Windows
/// calls `BCryptGenRandom` with `BCRYPT_USE_SYSTEM_PREFERRED_RNG`
/// so there is no `/dev/urandom` open attempt on Windows (issue
/// #572 first-run regression).
#[cfg(unix)]
fn fill_secure_random(buf: &mut [u8]) -> Result<(), String> {
    let mut urand =
        fs::File::open("/dev/urandom").map_err(|e| format!("cannot open /dev/urandom: {e}"))?;
    urand
        .read_exact(buf)
        .map_err(|e| format!("cannot read /dev/urandom: {e}"))?;
    Ok(())
}

#[cfg(windows)]
fn fill_secure_random(buf: &mut [u8]) -> Result<(), String> {
    windows_impl::fill_random(buf)
}

#[cfg(not(any(unix, windows)))]
fn fill_secure_random(_buf: &mut [u8]) -> Result<(), String> {
    Err("no secure RNG available on this platform".to_string())
}

/// Create `path` with `O_CREAT|O_EXCL`-equivalent semantics and
/// strict owner-only permissions.
#[cfg(unix)]
fn open_create_new_owner_only(path: &Path) -> Result<fs::File, OpenCreateNewError> {
    use std::os::unix::fs::OpenOptionsExt;
    let mut opts = fs::OpenOptions::new();
    opts.write(true).create_new(true).mode(0o600);
    match opts.open(path) {
        Ok(f) => Ok(f),
        Err(e) if e.kind() == io::ErrorKind::AlreadyExists => {
            Err(OpenCreateNewError::AlreadyExists)
        }
        Err(e) => Err(OpenCreateNewError::Other(format!(
            "create_new {}: {e}",
            path.display()
        ))),
    }
}

#[cfg(windows)]
fn open_create_new_owner_only(path: &Path) -> Result<fs::File, OpenCreateNewError> {
    match windows_impl::create_new_owner_only_file(path) {
        Ok(f) => Ok(f),
        Err(windows_impl::WinCreateError::AlreadyExists) => Err(OpenCreateNewError::AlreadyExists),
        Err(windows_impl::WinCreateError::Other(e)) => Err(OpenCreateNewError::Other(e)),
    }
}

#[cfg(not(any(unix, windows)))]
fn open_create_new_owner_only(path: &Path) -> Result<fs::File, OpenCreateNewError> {
    let _ = path;
    Err(OpenCreateNewError::Other(
        "owner-only create not implemented on this platform".to_string(),
    ))
}

#[cfg(unix)]
fn current_euid() -> u32 {
    extern "C" {
        fn geteuid() -> u32;
    }
    // SAFETY: `geteuid` reads a single thread-safe kernel value and has
    // no preconditions.
    unsafe { geteuid() }
}

fn atomic_write_owner_only(path: &Path, bytes: &[u8]) -> Result<(), String> {
    let parent = path
        .parent()
        .ok_or_else(|| format!("invalid path: {}", path.display()))?;
    let fname = path
        .file_name()
        .map(|s| s.to_string_lossy().into_owned())
        .unwrap_or_else(|| "sk-writer.tmp".into());

    // Unique tmp name: pid + nanos + per-process counter ensures
    // concurrent calls from same or sibling processes cannot collide,
    // and an attacker cannot pre-create the path. `create_new` adds
    // O_EXCL so we fail loudly instead of clobbering an existing file
    // or following a symlink. Loop a small bounded number of times to
    // absorb the (very rare) timestamp collision.
    let pid = std::process::id();
    let nanos = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_nanos())
        .unwrap_or(0);

    let mut tmp = PathBuf::new();
    let mut f_opt = None;
    for _ in 0..8 {
        let counter = TMP_WRITE_COUNTER.fetch_add(1, Ordering::Relaxed);
        tmp = parent.join(format!(".{fname}.{pid}.{nanos:x}.{counter}.tmp"));
        match open_create_new_owner_only(&tmp) {
            Ok(f) => {
                f_opt = Some(f);
                break;
            }
            Err(OpenCreateNewError::AlreadyExists) => continue,
            Err(OpenCreateNewError::Other(e)) => {
                return Err(format!("create_new tmp {}: {e}", tmp.display()))
            }
        }
    }
    let mut f = f_opt.ok_or_else(|| {
        format!(
            "atomic_write_owner_only: could not create unique tmp under {}",
            parent.display()
        )
    })?;
    if let Err(e) = f.write_all(bytes) {
        let _ = fs::remove_file(&tmp);
        return Err(format!("write tmp {}: {e}", tmp.display()));
    }
    let _ = f.sync_all();
    drop(f);
    if let Err(e) = fs::rename(&tmp, path) {
        let _ = fs::remove_file(&tmp);
        return Err(format!(
            "rename {} -> {}: {e}",
            tmp.display(),
            path.display()
        ));
    }
    Ok(())
}

fn read_exact_bytes<R: Read>(r: &mut R, n: usize) -> std::io::Result<Vec<u8>> {
    let mut buf = vec![0u8; n];
    r.read_exact(&mut buf)?;
    Ok(buf)
}

fn recv_frame<R: Read>(r: &mut R, key: &[u8; 32]) -> Result<Vec<u8>, String> {
    let len_bytes = read_exact_bytes(r, 4).map_err(|e| format!("recv len: {e}"))?;
    let len = u32::from_be_bytes([len_bytes[0], len_bytes[1], len_bytes[2], len_bytes[3]]);
    if len == 0 || len > MAX_BODY_BYTES {
        return Err(format!("recv len out of range: {len}"));
    }
    let tag = read_exact_bytes(r, HMAC_LEN).map_err(|e| format!("recv hmac: {e}"))?;
    let body = read_exact_bytes(r, len as usize).map_err(|e| format!("recv body: {e}"))?;
    let mut mac = <HmacSha256 as Mac>::new_from_slice(key).expect("HMAC accepts any key length");
    mac.update(&body);
    mac.verify_slice(&tag)
        .map_err(|_| "HMAC verification failed".to_string())?;
    Ok(body)
}

fn send_frame<W: Write>(w: &mut W, key: &[u8; 32], body: &[u8]) -> std::io::Result<()> {
    let mut mac = <HmacSha256 as Mac>::new_from_slice(key).expect("HMAC accepts any key length");
    mac.update(body);
    let tag = mac.finalize().into_bytes();
    let len = body.len() as u32;
    w.write_all(&len.to_be_bytes())?;
    w.write_all(&tag)?;
    w.write_all(body)?;
    w.flush()?;
    Ok(())
}

fn write_samples_file(samples: &[f64]) {
    let now_ms = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_millis() as u64)
        .unwrap_or(0);
    let payload = json!({
        "updated_ms": now_ms,
        "samples_ms": samples,
    });
    if let Ok(raw) = serde_json::to_vec(&payload) {
        let _ = atomic_write_owner_only(&samples_path(), &raw);
    }
}

fn entry_from_json(v: &Value) -> NewEntry {
    NewEntry {
        category: get_str(v, "category"),
        title: get_str(v, "title"),
        content: get_str(v, "content"),
        tags: get_str(v, "tags"),
        wing: get_str(v, "wing"),
        room: get_str(v, "room"),
        confidence: v.get("confidence").and_then(|v| v.as_f64()).unwrap_or(0.7),
        facts_json: v
            .get("facts_json")
            .and_then(|v| v.as_str())
            .unwrap_or("[]")
            .to_string(),
    }
}

fn get_str(v: &Value, k: &str) -> String {
    v.get(k).and_then(|v| v.as_str()).unwrap_or("").to_string()
}

fn is_busy(err: &rusqlite::Error) -> bool {
    use rusqlite::ErrorCode;
    matches!(
        err,
        rusqlite::Error::SqliteFailure(e, _)
            if matches!(e.code, ErrorCode::DatabaseBusy | ErrorCode::DatabaseLocked)
    )
}

// Common per-request handler. Used by both the real broker server and
// the test-support harness so request/response semantics are identical.
fn handle_request(
    stream: &mut (impl Read + Write),
    key: &[u8; 32],
    conn: &Mutex<rusqlite::Connection>,
    samples: &Arc<Mutex<Vec<f64>>>,
) -> Result<(), String> {
    let body = recv_frame(stream, key)?;
    let msg: Value = serde_json::from_slice(&body).map_err(|e| format!("decode request: {e}"))?;
    let op = msg.get("op").and_then(|v| v.as_str()).unwrap_or("");
    match op {
        "ping" => {
            let reply = serde_json::to_vec(&json!({"ok": true, "pong": true}))
                .map_err(|e| e.to_string())?;
            send_frame(stream, key, &reply).map_err(|e| e.to_string())?;
            Ok(())
        }
        "write" => {
            let entry_val = msg
                .get("entry")
                .ok_or_else(|| "missing 'entry'".to_string())?;
            let entry = entry_from_json(entry_val);
            let start = Instant::now();
            let outcome: Result<i64, (bool, String)> = {
                let c = conn.lock().map_err(|e| e.to_string())?;
                match insert_or_update_entry(&c, &entry) {
                    Ok(id) => {
                        let _ = rebuild_fts(&c, id);
                        Ok(id)
                    }
                    Err(e) => Err((is_busy(&e), e.to_string())),
                }
            };
            let elapsed_ms = start.elapsed().as_secs_f64() * 1000.0;
            match outcome {
                Ok(id) => {
                    push_sample(samples, elapsed_ms);
                    let reply = serde_json::to_vec(&json!({
                        "ok": true,
                        "entry_id": id,
                        "latency_ms": elapsed_ms,
                    }))
                    .map_err(|e| e.to_string())?;
                    send_frame(stream, key, &reply).map_err(|e| e.to_string())?;
                    Ok(())
                }
                Err((busy, msg)) => {
                    let reply = serde_json::to_vec(&json!({
                        "ok": false,
                        "busy": busy,
                        "error": msg,
                    }))
                    .map_err(|e| e.to_string())?;
                    send_frame(stream, key, &reply).map_err(|e| e.to_string())?;
                    Ok(())
                }
            }
        }
        other => {
            let reply = serde_json::to_vec(&json!({
                "ok": false,
                "error": format!("unknown op: {other}"),
            }))
            .map_err(|e| e.to_string())?;
            send_frame(stream, key, &reply).map_err(|e| e.to_string())?;
            Ok(())
        }
    }
}

fn push_sample(samples: &Arc<Mutex<Vec<f64>>>, ms: f64) {
    if let Ok(mut s) = samples.lock() {
        if s.len() >= SAMPLE_RING_CAPACITY {
            s.remove(0);
        }
        s.push(ms);
    }
}

// ── Unix implementation ──────────────────────────────────────────────────

#[cfg(unix)]
mod unix_impl {
    use super::*;
    use crate::db::connection::knowledge_db_path;
    use crate::index::extract_schema::ensure_extract_tables;
    use rusqlite::{Connection, OpenFlags};
    use std::os::unix::fs::PermissionsExt;
    use std::os::unix::net::{UnixListener, UnixStream};

    /// Open knowledge.db read-write, creating the file and schema
    /// when missing. Mirrors `open_writable_with_busy_timeout` for
    /// pragma setup but uses `SQLITE_OPEN_CREATE` so a fresh broker
    /// can bootstrap an empty database.
    fn open_or_create_for_broker() -> Result<Connection, String> {
        let path = knowledge_db_path();
        if let Some(parent) = path.parent() {
            fs::create_dir_all(parent).map_err(|e| format!("mkdir {}: {e}", parent.display()))?;
        }
        let conn = Connection::open_with_flags(
            &path,
            OpenFlags::SQLITE_OPEN_READ_WRITE
                | OpenFlags::SQLITE_OPEN_CREATE
                | OpenFlags::SQLITE_OPEN_NO_MUTEX,
        )
        .map_err(|e| format!("open {}: {e}", path.display()))?;
        conn.busy_timeout(Duration::from_millis(30_000))
            .map_err(|e| format!("busy_timeout: {e}"))?;
        conn.execute_batch(
            "PRAGMA journal_mode=WAL;\
             PRAGMA synchronous=NORMAL;\
             PRAGMA wal_autocheckpoint=1000;",
        )
        .map_err(|e| format!("pragmas: {e}"))?;
        ensure_extract_tables(&conn).map_err(|e| format!("ensure schema: {e}"))?;
        Ok(conn)
    }

    pub(super) fn client_write(entry: &NewEntry) -> Result<i64, BrokerError> {
        match attempt_write(entry) {
            Ok(id) => return Ok(id),
            Err(BrokerError::Busy(s)) => return Err(BrokerError::Busy(s)),
            Err(BrokerError::Unavailable(_)) | Err(BrokerError::Other(_)) => {
                // Maybe stale/dead socket — try to spawn and retry once.
            }
            Err(other) => return Err(other),
        }

        clean_stale_socket();
        spawn_broker_daemon().map_err(BrokerError::Unavailable)?;
        if !wait_for_socket(SPAWN_HANDSHAKE_MS) {
            return Err(BrokerError::Unavailable(
                "broker did not come online within handshake window".into(),
            ));
        }
        attempt_write(entry)
    }

    fn attempt_write(entry: &NewEntry) -> Result<i64, BrokerError> {
        let sock = socket_path();
        if !sock.exists() {
            return Err(BrokerError::Unavailable(format!(
                "socket missing: {}",
                sock.display()
            )));
        }
        let key = load_or_create_key(&key_path())
            .map_err(|e| BrokerError::Unavailable(format!("key load: {e}")))?;

        let mut stream = UnixStream::connect(&sock)
            .map_err(|e| BrokerError::Unavailable(format!("connect {}: {e}", sock.display())))?;
        stream
            .set_read_timeout(Some(Duration::from_secs(30)))
            .map_err(|e| BrokerError::Other(format!("set_read_timeout: {e}")))?;
        stream
            .set_write_timeout(Some(Duration::from_secs(30)))
            .map_err(|e| BrokerError::Other(format!("set_write_timeout: {e}")))?;

        let body = json!({
            "op": "write",
            "entry": {
                "category":   entry.category,
                "title":      entry.title,
                "content":    entry.content,
                "tags":       entry.tags,
                "wing":       entry.wing,
                "room":       entry.room,
                "confidence": entry.confidence,
                "facts_json": entry.facts_json,
            }
        });
        let raw =
            serde_json::to_vec(&body).map_err(|e| BrokerError::Other(format!("encode: {e}")))?;
        send_frame(&mut stream, &key, &raw)
            .map_err(|e| BrokerError::Unavailable(format!("send: {e}")))?;

        let reply_body = recv_frame(&mut stream, &key)
            .map_err(|e| BrokerError::Unavailable(format!("recv: {e}")))?;
        let reply: Value = serde_json::from_slice(&reply_body)
            .map_err(|e| BrokerError::Other(format!("decode reply: {e}")))?;

        if reply.get("ok").and_then(|v| v.as_bool()).unwrap_or(false) {
            let id = reply
                .get("entry_id")
                .and_then(|v| v.as_i64())
                .ok_or_else(|| BrokerError::Other("reply missing entry_id".into()))?;
            Ok(id)
        } else {
            let msg = reply
                .get("error")
                .and_then(|v| v.as_str())
                .unwrap_or("unknown")
                .to_string();
            let busy = reply.get("busy").and_then(|v| v.as_bool()).unwrap_or(false);
            if busy {
                Err(BrokerError::Busy(msg))
            } else {
                Err(BrokerError::Other(msg))
            }
        }
    }

    fn clean_stale_socket() {
        let sock = socket_path();
        clean_stale_socket_at(&sock);
    }

    /// Symlink-safe stale-socket cleanup. Will NEVER remove a symlink:
    /// a hostile same-user process cannot trick us into unlinking
    /// arbitrary paths via the broker's socket-path env. Refuses to
    /// remove anything that is not a Unix-domain socket file. If the
    /// path connects, the broker is already live — leave it alone.
    fn clean_stale_socket_at(sock: &Path) {
        let meta = match fs::symlink_metadata(sock) {
            Ok(m) => m,
            Err(_) => return,
        };
        if meta.file_type().is_symlink() {
            eprintln!(
                "sk writer-broker: refusing to remove symlink at socket path {}",
                sock.display()
            );
            return;
        }
        // Only proceed if it's a socket (best-effort: on platforms where
        // FileTypeExt doesn't expose `is_socket`, fall back to "not a
        // regular file and not a directory" which matches Unix sockets).
        if !is_unix_socket(&meta) {
            eprintln!(
                "sk writer-broker: refusing to remove non-socket at socket path {}",
                sock.display()
            );
            return;
        }
        if UnixStream::connect(sock).is_ok() {
            return;
        }
        let _ = fs::remove_file(sock);
    }

    fn is_unix_socket(meta: &fs::Metadata) -> bool {
        use std::os::unix::fs::FileTypeExt;
        meta.file_type().is_socket()
    }

    fn wait_for_socket(timeout_ms: u64) -> bool {
        let sock = socket_path();
        let deadline = Instant::now() + Duration::from_millis(timeout_ms);
        while Instant::now() < deadline {
            if sock.exists() && UnixStream::connect(&sock).is_ok() {
                return true;
            }
            std::thread::sleep(Duration::from_millis(25));
        }
        false
    }

    fn spawn_broker_daemon() -> Result<(), String> {
        let exe = std::env::current_exe().map_err(|e| format!("current_exe: {e}"))?;
        let mut cmd = std::process::Command::new(exe);
        cmd.env("SK_WRITER_BROKER_DAEMON", "1");
        cmd.stdin(std::process::Stdio::null())
            .stdout(std::process::Stdio::null())
            .stderr(std::process::Stdio::null());

        use std::os::unix::process::CommandExt;
        unsafe {
            cmd.pre_exec(|| {
                libc_setsid();
                Ok(())
            });
        }
        cmd.spawn().map_err(|e| format!("spawn: {e}"))?;
        Ok(())
    }

    fn libc_setsid() {
        extern "C" {
            fn setsid() -> i32;
        }
        unsafe {
            setsid();
        }
    }

    /// Set the process file-creation mask to `mask` and return the
    /// previous mask. Used so the listener's socket file is created
    /// with mode 0600 atomically (umask 0o077 clears group+world bits
    /// from the default 0o666). `umask(2)` is async-signal-safe and
    /// cannot fail.
    pub(super) fn libc_umask(mask: u32) -> u32 {
        extern "C" {
            // POSIX type is mode_t which is u32 on every platform sk
            // currently builds for.
            fn umask(mask: u32) -> u32;
        }
        // SAFETY: `umask` is async-signal-safe and has no preconditions.
        unsafe { umask(mask) }
    }

    pub(super) fn run_server() -> Result<(), String> {
        let run_dir = run_dir();
        fs::create_dir_all(&run_dir).map_err(|e| format!("mkdir {}: {e}", run_dir.display()))?;

        let pid_path = pid_path();
        acquire_pid_file(&pid_path).map_err(|e| format!("acquire pid file: {e}"))?;

        // Load key + open DB FIRST so a failure here does not leave a
        // zombie socket file that clients would try to connect to.
        let key = load_or_create_key(&key_path())?;
        let conn = open_or_create_for_broker().map_err(|e| format!("open knowledge.db: {e}"))?;
        let conn = Mutex::new(conn);

        let sock_path = socket_path();
        // Symlink-safe pre-bind cleanup. If a hostile process planted a
        // symlink at sock_path, refuse to start rather than follow it.
        if let Ok(meta) = fs::symlink_metadata(&sock_path) {
            if meta.file_type().is_symlink() {
                return Err(format!(
                    "writer-broker: refusing to bind: {} is a symlink",
                    sock_path.display()
                ));
            }
            if is_unix_socket(&meta) {
                let _ = fs::remove_file(&sock_path);
            } else {
                return Err(format!(
                    "writer-broker: refusing to bind: {} exists and is not a socket",
                    sock_path.display()
                ));
            }
        }

        // Bind under a restrictive umask so the socket is created with
        // 0600 atomically — eliminates the bind-then-chmod window where
        // a same-user process could connect via a 0777 socket before we
        // tightened it. The chmod that follows is belt-and-suspenders.
        let listener = {
            let prev = libc_umask(0o077);
            let result = UnixListener::bind(&sock_path);
            let _ = libc_umask(prev);
            result.map_err(|e| format!("bind {}: {e}", sock_path.display()))?
        };
        let perm = fs::Permissions::from_mode(0o600);
        fs::set_permissions(&sock_path, perm).map_err(|e| format!("chmod socket: {e}"))?;

        listener
            .set_nonblocking(true)
            .map_err(|e| format!("set_nonblocking: {e}"))?;

        let samples: Arc<Mutex<Vec<f64>>> =
            Arc::new(Mutex::new(Vec::with_capacity(SAMPLE_RING_CAPACITY)));
        let last_activity_ms = Arc::new(AtomicU64::new(now_ms()));
        let stop = Arc::new(AtomicBool::new(false));

        {
            let stop = stop.clone();
            let _ = ctrlc::set_handler(move || {
                stop.store(true, Ordering::SeqCst);
            });
        }

        spawn_samples_flusher(samples.clone(), stop.clone());

        loop {
            if stop.load(Ordering::SeqCst) {
                break;
            }
            let since_ms = now_ms() - last_activity_ms.load(Ordering::Relaxed);
            if since_ms >= IDLE_EXIT_SECONDS * 1000 {
                break;
            }
            match listener.accept() {
                Ok((mut stream, _)) => {
                    last_activity_ms.store(now_ms(), Ordering::Relaxed);
                    let _ = stream.set_read_timeout(Some(Duration::from_secs(30)));
                    let _ = stream.set_write_timeout(Some(Duration::from_secs(30)));
                    if let Err(e) = handle_request(&mut stream, &key, &conn, &samples) {
                        eprintln!("sk writer-broker: connection error: {e}");
                    }
                    last_activity_ms.store(now_ms(), Ordering::Relaxed);
                }
                Err(ref e) if e.kind() == std::io::ErrorKind::WouldBlock => {
                    std::thread::sleep(Duration::from_millis(100));
                }
                Err(e) => {
                    eprintln!("sk writer-broker: accept error: {e}");
                    std::thread::sleep(Duration::from_millis(50));
                }
            }
        }

        if let Ok(s) = samples.lock() {
            write_samples_file(&s);
        }
        let _ = fs::remove_file(&sock_path);
        let _ = fs::remove_file(&pid_path);
        Ok(())
    }

    fn now_ms() -> u64 {
        SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map(|d| d.as_millis() as u64)
            .unwrap_or(0)
    }

    fn spawn_samples_flusher(samples: Arc<Mutex<Vec<f64>>>, stop: Arc<AtomicBool>) {
        std::thread::spawn(move || {
            while !stop.load(Ordering::SeqCst) {
                std::thread::sleep(Duration::from_secs(5));
                let snap = samples.lock().ok().map(|s| s.clone()).unwrap_or_default();
                if !snap.is_empty() {
                    write_samples_file(&snap);
                }
            }
        });
    }

    fn acquire_pid_file(path: &Path) -> Result<(), String> {
        if let Some(parent) = path.parent() {
            fs::create_dir_all(parent).map_err(|e| e.to_string())?;
        }
        if let Ok(raw) = fs::read_to_string(path) {
            if let Ok(pid) = raw.trim().parse::<i32>() {
                if pid > 0 && pid_is_alive(pid) {
                    return Err(format!("another broker is already running (pid {pid})"));
                }
            }
            let _ = fs::remove_file(path);
        }
        let pid = std::process::id();
        atomic_write_owner_only(path, format!("{pid}\n").as_bytes())?;
        Ok(())
    }

    fn pid_is_alive(pid: i32) -> bool {
        extern "C" {
            fn kill(pid: i32, sig: i32) -> i32;
        }
        unsafe { kill(pid, 0) == 0 }
    }
}

// ── Windows implementation ───────────────────────────────────────────────

#[cfg(windows)]
#[allow(clippy::upper_case_acronyms)]
mod windows_impl {
    //! Windows named-pipe transport equivalent of `unix_impl`.
    //!
    //! Security invariants (issue #572):
    //!
    //! * The pipe is **always local-only**: `CreateNamedPipeW` is called
    //!   with `PIPE_REJECT_REMOTE_CLIENTS`, so the kernel refuses any
    //!   SMB/network-mapped client and only `\\.\` (loopback) clients can
    //!   open the pipe.
    //! * The pipe carries an explicit **owner-only DACL** built from the
    //!   SDDL string `O:<sid>D:P(A;;GA;;;<sid>)` — explicit Owner =
    //!   current process token user, Protected DACL (no inherited
    //!   ACEs), single Allow ACE granting `GENERIC_ALL` to the same
    //!   SID. Pinning the owner directly (instead of relying on
    //!   `OWNER_RIGHTS` / default-owner) means "only the user who
    //!   started the broker may connect" remains true even when the
    //!   broker process runs under an Admin-elevated token (which
    //!   otherwise defaults the object owner to
    //!   `BUILTIN\Administrators`). No Everyone / Authenticated Users
    //!   / Network ACEs are granted.
    //! * The first instance is created with
    //!   `FILE_FLAG_FIRST_PIPE_INSTANCE`, so the broker fails loudly if
    //!   another process already squats on the name. The broker never
    //!   silently shares an existing pipe name with an attacker.
    //! * The session key file is opened with `OpenOptions::create_new`
    //!   so an attacker cannot pre-create a key file we then read; on
    //!   load we re-verify the file is not a symlink, is a regular
    //!   file, is exactly 32 bytes, **and** its owner SID equals the
    //!   current process token's user SID — the Windows analogue of the
    //!   Unix `uid` check.
    //!
    //! The wire protocol (length-prefixed HMAC-authenticated JSON
    //! frames) and the `handle_request` dispatcher are identical to
    //! Unix; only the IPC handle wrapper differs.

    use super::*;
    use crate::db::connection::knowledge_db_path;
    use crate::index::extract_schema::ensure_extract_tables;
    use rusqlite::{Connection, OpenFlags};
    use std::ffi::OsString;
    use std::os::raw::c_void;
    use std::os::windows::ffi::{OsStrExt, OsStringExt};

    // ── Win32 FFI ────────────────────────────────────────────────────

    type HANDLE = *mut c_void;
    type BOOL = i32;
    type DWORD = u32;
    type LPVOID = *mut c_void;
    type LPCVOID = *const c_void;

    const INVALID_HANDLE_VALUE: HANDLE = -1isize as HANDLE;
    const NULL_HANDLE: HANDLE = 0 as HANDLE;

    // CreateNamedPipeW open modes / pipe modes / flags
    const PIPE_ACCESS_DUPLEX: DWORD = 0x0000_0003;
    const FILE_FLAG_FIRST_PIPE_INSTANCE: DWORD = 0x0008_0000;
    const PIPE_TYPE_BYTE: DWORD = 0x0000_0000;
    const PIPE_READMODE_BYTE: DWORD = 0x0000_0000;
    const PIPE_WAIT: DWORD = 0x0000_0000;
    const PIPE_REJECT_REMOTE_CLIENTS: DWORD = 0x0000_0008;
    const PIPE_UNLIMITED_INSTANCES: DWORD = 255;

    // CreateFileW creation dispositions
    const CREATE_NEW_DISPOSITION: DWORD = 1;

    // ERROR_* values relevant to CreateFileW(CREATE_NEW)
    const ERROR_FILE_EXISTS: DWORD = 80;
    const ERROR_ALREADY_EXISTS: DWORD = 183;

    // BCryptGenRandom flags
    const BCRYPT_USE_SYSTEM_PREFERRED_RNG: DWORD = 0x0000_0002;

    // CreateFileW
    const GENERIC_READ: DWORD = 0x8000_0000;
    const GENERIC_WRITE: DWORD = 0x4000_0000;
    const OPEN_EXISTING: DWORD = 3;
    const FILE_ATTRIBUTE_NORMAL: DWORD = 0x0000_0080;
    const NMPWAIT_USE_DEFAULT_WAIT: DWORD = 0x0000_0000;

    // GetLastError values
    const ERROR_FILE_NOT_FOUND: DWORD = 2;
    const ERROR_PIPE_BUSY: DWORD = 231;
    const ERROR_SEM_TIMEOUT: DWORD = 121;
    const ERROR_PIPE_NOT_CONNECTED: DWORD = 233;
    const ERROR_NO_DATA: DWORD = 232;
    const ERROR_PIPE_CONNECTED: DWORD = 535;
    const ERROR_IO_PENDING: DWORD = 997;
    const ERROR_BROKEN_PIPE: DWORD = 109;
    const ERROR_MORE_DATA: DWORD = 234;

    // WaitForSingleObject
    const WAIT_OBJECT_0: DWORD = 0;
    const WAIT_TIMEOUT: DWORD = 258;
    const INFINITE: DWORD = 0xFFFF_FFFF;

    // Process creation flags (for std::os::windows::process::CommandExt)
    const CREATE_NEW_PROCESS_GROUP: u32 = 0x0000_0200;
    const DETACHED_PROCESS: u32 = 0x0000_0008;
    const CREATE_NO_WINDOW: u32 = 0x0800_0000;

    // ConvertStringSecurityDescriptorToSecurityDescriptorW
    const SDDL_REVISION_1: DWORD = 1;

    // GetNamedSecurityInfoW
    const SE_FILE_OBJECT: DWORD = 1;
    const OWNER_SECURITY_INFORMATION: DWORD = 0x0000_0001;

    // OpenProcessToken
    const TOKEN_QUERY: DWORD = 0x0008;
    const TOKEN_USER_INFO_CLASS: DWORD = 1; // TokenUser

    // OpenProcess
    const PROCESS_QUERY_LIMITED_INFORMATION: DWORD = 0x1000;
    const STILL_ACTIVE: DWORD = 259;

    #[repr(C)]
    struct SecurityAttributes {
        n_length: DWORD,
        lp_security_descriptor: *mut c_void,
        b_inherit_handle: BOOL,
    }

    #[repr(C)]
    struct Overlapped {
        internal: usize,
        internal_high: usize,
        offset: DWORD,
        offset_high: DWORD,
        h_event: HANDLE,
    }

    #[repr(C)]
    struct TokenUserStruct {
        // SID_AND_ATTRIBUTES { Sid: PSID, Attributes: DWORD }
        sid: *mut c_void,
        attributes: DWORD,
    }

    #[link(name = "kernel32")]
    extern "system" {
        fn CreateNamedPipeW(
            lpName: *const u16,
            dwOpenMode: DWORD,
            dwPipeMode: DWORD,
            nMaxInstances: DWORD,
            nOutBufferSize: DWORD,
            nInBufferSize: DWORD,
            nDefaultTimeOut: DWORD,
            lpSecurityAttributes: *const SecurityAttributes,
        ) -> HANDLE;
        fn ConnectNamedPipe(hNamedPipe: HANDLE, lpOverlapped: *mut Overlapped) -> BOOL;
        fn DisconnectNamedPipe(hNamedPipe: HANDLE) -> BOOL;
        fn CreateFileW(
            lpFileName: *const u16,
            dwDesiredAccess: DWORD,
            dwShareMode: DWORD,
            lpSecurityAttributes: *const SecurityAttributes,
            dwCreationDisposition: DWORD,
            dwFlagsAndAttributes: DWORD,
            hTemplateFile: HANDLE,
        ) -> HANDLE;
        fn ReadFile(
            hFile: HANDLE,
            lpBuffer: LPVOID,
            nNumberOfBytesToRead: DWORD,
            lpNumberOfBytesRead: *mut DWORD,
            lpOverlapped: *mut Overlapped,
        ) -> BOOL;
        fn WriteFile(
            hFile: HANDLE,
            lpBuffer: LPCVOID,
            nNumberOfBytesToWrite: DWORD,
            lpNumberOfBytesWritten: *mut DWORD,
            lpOverlapped: *mut Overlapped,
        ) -> BOOL;
        fn FlushFileBuffers(hFile: HANDLE) -> BOOL;
        fn CloseHandle(hObject: HANDLE) -> BOOL;
        fn GetLastError() -> DWORD;
        fn WaitNamedPipeW(lpNamedPipeName: *const u16, nTimeOut: DWORD) -> BOOL;
        fn LocalFree(hMem: HANDLE) -> HANDLE;
        fn CreateEventW(
            lpEventAttributes: *const SecurityAttributes,
            bManualReset: BOOL,
            bInitialState: BOOL,
            lpName: *const u16,
        ) -> HANDLE;
        fn SetEvent(hEvent: HANDLE) -> BOOL;
        fn ResetEvent(hEvent: HANDLE) -> BOOL;
        fn WaitForSingleObject(hHandle: HANDLE, dwMilliseconds: DWORD) -> DWORD;
        fn WaitForMultipleObjects(
            nCount: DWORD,
            lpHandles: *const HANDLE,
            bWaitAll: BOOL,
            dwMilliseconds: DWORD,
        ) -> DWORD;
        fn GetOverlappedResult(
            hFile: HANDLE,
            lpOverlapped: *mut Overlapped,
            lpNumberOfBytesTransferred: *mut DWORD,
            bWait: BOOL,
        ) -> BOOL;
        fn CancelIoEx(hFile: HANDLE, lpOverlapped: *mut Overlapped) -> BOOL;
        fn OpenProcess(dwDesiredAccess: DWORD, bInheritHandle: BOOL, dwProcessId: DWORD) -> HANDLE;
        fn GetExitCodeProcess(hProcess: HANDLE, lpExitCode: *mut DWORD) -> BOOL;
        fn GetCurrentProcess() -> HANDLE;
    }

    #[link(name = "advapi32")]
    extern "system" {
        fn ConvertStringSecurityDescriptorToSecurityDescriptorW(
            StringSecurityDescriptor: *const u16,
            StringSDRevision: DWORD,
            SecurityDescriptor: *mut *mut c_void,
            SecurityDescriptorSize: *mut DWORD,
        ) -> BOOL;
        fn ConvertSidToStringSidW(Sid: *mut c_void, StringSid: *mut *mut u16) -> BOOL;
        fn GetNamedSecurityInfoW(
            pObjectName: *const u16,
            ObjectType: DWORD,
            SecurityInfo: DWORD,
            ppsidOwner: *mut *mut c_void,
            ppsidGroup: *mut *mut c_void,
            ppDacl: *mut *mut c_void,
            ppSacl: *mut *mut c_void,
            ppSecurityDescriptor: *mut *mut c_void,
        ) -> DWORD;
        fn OpenProcessToken(
            ProcessHandle: HANDLE,
            DesiredAccess: DWORD,
            TokenHandle: *mut HANDLE,
        ) -> BOOL;
        fn GetTokenInformation(
            TokenHandle: HANDLE,
            TokenInformationClass: DWORD,
            TokenInformation: LPVOID,
            TokenInformationLength: DWORD,
            ReturnLength: *mut DWORD,
        ) -> BOOL;
        fn EqualSid(pSid1: *mut c_void, pSid2: *mut c_void) -> BOOL;
        fn IsValidSid(pSid: *mut c_void) -> BOOL;
    }

    #[link(name = "bcrypt")]
    extern "system" {
        // NTSTATUS is signed 32-bit; success is >= 0.
        fn BCryptGenRandom(
            hAlgorithm: HANDLE,
            pbBuffer: *mut u8,
            cbBuffer: DWORD,
            dwFlags: DWORD,
        ) -> i32;
    }

    // ── Secure RNG (BCryptGenRandom) ─────────────────────────────────

    /// Fill `buf` with cryptographically-secure random bytes using
    /// `BCryptGenRandom` with `BCRYPT_USE_SYSTEM_PREFERRED_RNG`. This
    /// replaces the previous (broken) `/dev/urandom` open on Windows
    /// in `try_create_new_key`. Documented as the recommended
    /// CSPRNG entry point in Microsoft's Cryptography Next Generation
    /// (CNG) API: callers pass a null algorithm handle and the flag,
    /// and Windows uses the kernel's preferred RNG (currently
    /// `AES_CTR_DRBG` seeded from the kernel entropy pool).
    pub(super) fn fill_random(buf: &mut [u8]) -> Result<(), String> {
        if buf.is_empty() {
            return Ok(());
        }
        if buf.len() > DWORD::MAX as usize {
            return Err(format!(
                "fill_random: buffer too large ({} bytes)",
                buf.len()
            ));
        }
        let status = unsafe {
            BCryptGenRandom(
                std::ptr::null_mut(),
                buf.as_mut_ptr(),
                buf.len() as DWORD,
                BCRYPT_USE_SYSTEM_PREFERRED_RNG,
            )
        };
        if status < 0 {
            return Err(format!(
                "BCryptGenRandom failed (NTSTATUS 0x{:08x})",
                status as u32
            ));
        }
        Ok(())
    }

    // ── Owner-only file creation ─────────────────────────────────────

    /// Outcome of `create_new_owner_only_file`; mirrors `KeyCreateError`
    /// so callers can map a lost create-race to `AlreadyExists`.
    #[derive(Debug)]
    pub(super) enum WinCreateError {
        AlreadyExists,
        Other(String),
    }

    /// Create `path` with `CREATE_NEW` disposition and an explicit
    /// Protected, owner-only DACL (`O:<sid>D:P(A;;GA;;;<sid>)` where
    /// `<sid>` is the current process token user). This is the
    /// Windows equivalent of Unix `O_CREAT|O_EXCL` + `0o600`: the
    /// file fails loudly if it already exists, and no inherited ACE
    /// from the parent directory can grant `Authenticated Users` /
    /// `Administrators` access to broker key material on shared or
    /// domain-joined hosts. Pinning the owner to the user SID also
    /// prevents Windows' default-owner rule from setting the owner
    /// to `BUILTIN\Administrators` when the broker runs under an
    /// elevated token.
    ///
    /// On success returns a `std::fs::File` wrapping the new handle
    /// (via `FromRawHandle`) so the rest of the broker code can
    /// `write_all`/`sync_all` through normal stdlib I/O.
    pub(super) fn create_new_owner_only_file(path: &Path) -> Result<std::fs::File, WinCreateError> {
        use std::os::windows::io::FromRawHandle;
        let sd = owner_only_security_descriptor().map_err(WinCreateError::Other)?;
        let sa = SecurityAttributes {
            n_length: std::mem::size_of::<SecurityAttributes>() as DWORD,
            lp_security_descriptor: sd.0,
            b_inherit_handle: 0,
        };
        let wpath = path_to_wide(path);
        let h = unsafe {
            CreateFileW(
                wpath.as_ptr(),
                GENERIC_READ | GENERIC_WRITE,
                0, // dwShareMode = no share
                &sa as *const SecurityAttributes,
                CREATE_NEW_DISPOSITION,
                FILE_ATTRIBUTE_NORMAL,
                NULL_HANDLE,
            )
        };
        if h == INVALID_HANDLE_VALUE {
            let err = unsafe { GetLastError() };
            if err == ERROR_FILE_EXISTS || err == ERROR_ALREADY_EXISTS {
                return Err(WinCreateError::AlreadyExists);
            }
            return Err(WinCreateError::Other(format!(
                "CreateFileW(CREATE_NEW) {} failed (err {err})",
                path.display()
            )));
        }
        // SAFETY: `h` is a valid Win32 file handle we just opened;
        // `from_raw_handle` takes ownership and closes it on drop.
        // `_sd` is dropped only after CreateFileW returns; the
        // descriptor bytes were copied into the new file's security
        // metadata at create time.
        let f = unsafe { std::fs::File::from_raw_handle(h as _) };
        Ok(f)
    }

    // ── Helpers ───────────────────────────────────────────────────────

    fn to_wide(s: &str) -> Vec<u16> {
        std::ffi::OsStr::new(s)
            .encode_wide()
            .chain(std::iter::once(0))
            .collect()
    }

    fn path_to_wide(p: &Path) -> Vec<u16> {
        p.as_os_str()
            .encode_wide()
            .chain(std::iter::once(0))
            .collect()
    }

    /// RAII wrapper for a Win32 HANDLE.
    struct WinHandle(HANDLE);
    impl WinHandle {
        fn raw(&self) -> HANDLE {
            self.0
        }
    }
    impl Drop for WinHandle {
        fn drop(&mut self) {
            if !self.0.is_null() && self.0 != INVALID_HANDLE_VALUE {
                unsafe {
                    CloseHandle(self.0);
                }
                self.0 = NULL_HANDLE;
            }
        }
    }
    // SAFETY: A Win32 pipe HANDLE is just an opaque kernel handle; we
    // only move it across threads, never share concurrently without
    // external synchronization.
    unsafe impl Send for WinHandle {}

    /// RAII wrapper for a buffer allocated by `LocalAlloc`-family APIs
    /// (`ConvertStringSecurityDescriptorToSecurityDescriptorW` returns
    /// memory that must be released with `LocalFree`).
    struct LocalAlloc(*mut c_void);
    impl Drop for LocalAlloc {
        fn drop(&mut self) {
            if !self.0.is_null() {
                unsafe {
                    LocalFree(self.0);
                }
                self.0 = std::ptr::null_mut();
            }
        }
    }

    /// Build an owner-only security descriptor whose **owner** and DACL
    /// are both the current process token's user SID:
    ///
    /// `O:<sid>D:P(A;;GA;;;<sid>)` — explicit Owner = current user,
    /// Protected DACL, single Allow ACE granting `GENERIC_ALL` to the
    /// same SID.
    ///
    /// Why explicit instead of `D:P(A;;GA;;;OW)` with default owner:
    /// when the broker runs under a token that is a member of the
    /// `Administrators` group (e.g. on GitHub Actions Windows runners
    /// or any UAC-elevated session), Windows' "default owner" rule
    /// assigns ownership of newly-created objects to
    /// `BUILTIN\Administrators` (a 16-byte SID) instead of the user
    /// (a 28-byte SID). That breaks the
    /// `ensure_owned_by_current_user` invariant downstream. Pinning
    /// the owner to the current user SID in the SD itself is always
    /// permitted (you can always own objects you create) and removes
    /// the ambiguity without weakening the DACL.
    fn owner_only_security_descriptor() -> Result<LocalAlloc, String> {
        let sid = current_user_sid_string()?;
        // SDDL accepts a SID string in place of a short alias. We
        // splice it into both the Owner field and the single Allow
        // ACE so the DACL is exactly `GENERIC_ALL` to the current
        // user, with no inherited ACEs (Protected).
        let sddl_str = format!("O:{sid}D:P(A;;GA;;;{sid})");
        let sddl = to_wide(&sddl_str);
        let mut sd: *mut c_void = std::ptr::null_mut();
        let ok = unsafe {
            ConvertStringSecurityDescriptorToSecurityDescriptorW(
                sddl.as_ptr(),
                SDDL_REVISION_1,
                &mut sd as *mut _,
                std::ptr::null_mut(),
            )
        };
        if ok == 0 || sd.is_null() {
            return Err(format!(
                "ConvertStringSecurityDescriptorToSecurityDescriptorW failed (err {})",
                unsafe { GetLastError() }
            ));
        }
        Ok(LocalAlloc(sd))
    }

    /// Return the current process token user's SID in SDDL string form
    /// (e.g. `S-1-5-21-...-...-...-1001`). Used to build an
    /// owner-only security descriptor that pins the owner to the
    /// current user instead of relying on Windows' default-owner
    /// fallback (which yields `BUILTIN\Administrators` for elevated
    /// tokens).
    fn current_user_sid_string() -> Result<String, String> {
        let mut sid_bytes = current_user_sid_bytes()?;
        let mut out_ptr: *mut u16 = std::ptr::null_mut();
        let ok = unsafe {
            ConvertSidToStringSidW(
                sid_bytes.as_mut_ptr() as *mut c_void,
                &mut out_ptr as *mut *mut u16,
            )
        };
        if ok == 0 || out_ptr.is_null() {
            return Err(format!("ConvertSidToStringSidW failed (err {})", unsafe {
                GetLastError()
            }));
        }
        // Count wide chars up to the NUL terminator, then convert.
        // ConvertSidToStringSidW allocates with LocalAlloc; free via
        // LocalAlloc-wrapped pointer on the way out.
        let _guard = LocalAlloc(out_ptr as *mut c_void);
        let mut len = 0usize;
        unsafe {
            while *out_ptr.add(len) != 0 {
                len += 1;
            }
        }
        let slice = unsafe { std::slice::from_raw_parts(out_ptr, len) };
        Ok(String::from_utf16_lossy(slice))
    }

    /// Read+Write helper that wraps a raw HANDLE so we can reuse the
    /// platform-agnostic `send_frame` / `recv_frame` / `handle_request`
    /// helpers from the parent module.
    struct PipeStream {
        handle: HANDLE,
        // When `owned` we close the handle on drop. The server's main
        // loop uses owned streams; clients also use owned streams.
        owned: bool,
    }
    impl PipeStream {
        fn from_owned(h: HANDLE) -> Self {
            Self {
                handle: h,
                owned: true,
            }
        }
    }
    impl Drop for PipeStream {
        fn drop(&mut self) {
            if self.owned && !self.handle.is_null() && self.handle != INVALID_HANDLE_VALUE {
                unsafe {
                    // Best-effort: flush so a pending response is not
                    // dropped, then close. Errors are not actionable on
                    // drop.
                    FlushFileBuffers(self.handle);
                    CloseHandle(self.handle);
                }
                self.handle = NULL_HANDLE;
            }
        }
    }

    impl io::Read for PipeStream {
        fn read(&mut self, buf: &mut [u8]) -> io::Result<usize> {
            if buf.is_empty() {
                return Ok(0);
            }
            let mut got: DWORD = 0;
            let ok = unsafe {
                ReadFile(
                    self.handle,
                    buf.as_mut_ptr() as LPVOID,
                    buf.len().min(DWORD::MAX as usize) as DWORD,
                    &mut got as *mut DWORD,
                    std::ptr::null_mut(),
                )
            };
            if ok == 0 {
                let err = unsafe { GetLastError() };
                if err == ERROR_BROKEN_PIPE || err == ERROR_PIPE_NOT_CONNECTED {
                    return Ok(0);
                }
                return Err(io::Error::other(format!("ReadFile failed (err {err})")));
            }
            Ok(got as usize)
        }
    }

    impl io::Write for PipeStream {
        fn write(&mut self, buf: &[u8]) -> io::Result<usize> {
            if buf.is_empty() {
                return Ok(0);
            }
            let mut written: DWORD = 0;
            let ok = unsafe {
                WriteFile(
                    self.handle,
                    buf.as_ptr() as LPCVOID,
                    buf.len().min(DWORD::MAX as usize) as DWORD,
                    &mut written as *mut DWORD,
                    std::ptr::null_mut(),
                )
            };
            if ok == 0 {
                let err = unsafe { GetLastError() };
                return Err(io::Error::other(format!("WriteFile failed (err {err})")));
            }
            Ok(written as usize)
        }
        fn flush(&mut self) -> io::Result<()> {
            let ok = unsafe { FlushFileBuffers(self.handle) };
            if ok == 0 {
                let err = unsafe { GetLastError() };
                // FlushFileBuffers on a pipe that the client has closed
                // returns ERROR_BROKEN_PIPE / ERROR_PIPE_NOT_CONNECTED;
                // that's not actionable here.
                if err == ERROR_BROKEN_PIPE || err == ERROR_PIPE_NOT_CONNECTED {
                    return Ok(());
                }
                return Err(io::Error::other(format!(
                    "FlushFileBuffers failed (err {err})"
                )));
            }
            Ok(())
        }
    }

    // ── Owner-SID check (analogue of Unix uid check) ─────────────────

    /// Return the current process token's user SID as a freshly-allocated
    /// buffer plus a `WinHandle` keeping the token alive (the SID points
    /// into the token-info buffer, but we copy it out to a Vec<u8> so the
    /// caller does not have to keep the token open).
    fn current_user_sid_bytes() -> Result<Vec<u8>, String> {
        unsafe {
            let mut token: HANDLE = NULL_HANDLE;
            if OpenProcessToken(GetCurrentProcess(), TOKEN_QUERY, &mut token as *mut HANDLE) == 0 {
                return Err(format!("OpenProcessToken failed (err {})", GetLastError()));
            }
            let _token_guard = WinHandle(token);

            // First call: discover required buffer size.
            let mut needed: DWORD = 0;
            GetTokenInformation(
                token,
                TOKEN_USER_INFO_CLASS,
                std::ptr::null_mut(),
                0,
                &mut needed as *mut DWORD,
            );
            if needed == 0 {
                return Err(format!(
                    "GetTokenInformation size probe returned 0 (err {})",
                    GetLastError()
                ));
            }
            let mut buf: Vec<u8> = vec![0u8; needed as usize];
            if GetTokenInformation(
                token,
                TOKEN_USER_INFO_CLASS,
                buf.as_mut_ptr() as LPVOID,
                needed,
                &mut needed as *mut DWORD,
            ) == 0
            {
                return Err(format!(
                    "GetTokenInformation failed (err {})",
                    GetLastError()
                ));
            }
            // buf now starts with a TOKEN_USER == SID_AND_ATTRIBUTES,
            // whose first pointer-sized field is `Sid: PSID`.
            let token_user = buf.as_ptr() as *const TokenUserStruct;
            let sid_ptr = (*token_user).sid;
            if sid_ptr.is_null() || IsValidSid(sid_ptr) == 0 {
                return Err("current process token has invalid user SID".to_string());
            }
            // Copy the SID bytes out. SID length = 8 + 4*SubAuthCount;
            // we read the SubAuthCount byte at offset 1.
            let sid_bytes = sid_ptr as *const u8;
            let sub_auth_count = *sid_bytes.add(1) as usize;
            let sid_len = 8 + 4 * sub_auth_count;
            let mut out = vec![0u8; sid_len];
            std::ptr::copy_nonoverlapping(sid_bytes, out.as_mut_ptr(), sid_len);
            Ok(out)
        }
    }

    /// Return the file's owner SID as a Vec<u8> (raw SID bytes copied
    /// out of the GetNamedSecurityInfo-allocated SD).
    fn file_owner_sid_bytes(path: &Path) -> Result<Vec<u8>, String> {
        let wpath = path_to_wide(path);
        let mut psid_owner: *mut c_void = std::ptr::null_mut();
        let mut psd: *mut c_void = std::ptr::null_mut();
        let err = unsafe {
            GetNamedSecurityInfoW(
                wpath.as_ptr(),
                SE_FILE_OBJECT,
                OWNER_SECURITY_INFORMATION,
                &mut psid_owner as *mut _,
                std::ptr::null_mut(),
                std::ptr::null_mut(),
                std::ptr::null_mut(),
                &mut psd as *mut _,
            )
        };
        if err != 0 {
            return Err(format!(
                "GetNamedSecurityInfoW {} failed (err {})",
                path.display(),
                err
            ));
        }
        // Wrap the SD so it is freed even on early return.
        let _sd_guard = LocalAlloc(psd);
        if psid_owner.is_null() || unsafe { IsValidSid(psid_owner) } == 0 {
            return Err(format!(
                "file {} has missing or invalid owner SID",
                path.display()
            ));
        }
        unsafe {
            let sid_bytes = psid_owner as *const u8;
            let sub_auth_count = *sid_bytes.add(1) as usize;
            let sid_len = 8 + 4 * sub_auth_count;
            let mut out = vec![0u8; sid_len];
            std::ptr::copy_nonoverlapping(sid_bytes, out.as_mut_ptr(), sid_len);
            Ok(out)
        }
    }

    /// Public from this module: assert the file at `path` is owned by
    /// the current process token's user. Called from
    /// `read_validated_key` on Windows.
    pub(super) fn ensure_owned_by_current_user(path: &Path) -> Result<(), String> {
        let mut me = current_user_sid_bytes()?;
        let mut them = file_owner_sid_bytes(path)?;
        if me.len() != them.len() {
            return Err(format!(
                "writer-broker key {} owned by SID of length {}, expected {}; refusing",
                path.display(),
                them.len(),
                me.len()
            ));
        }
        let equal = unsafe {
            EqualSid(
                me.as_mut_ptr() as *mut c_void,
                them.as_mut_ptr() as *mut c_void,
            )
        };
        if equal == 0 {
            return Err(format!(
                "writer-broker key {} owner SID does not match current user; refusing",
                path.display()
            ));
        }
        Ok(())
    }

    // ── Pipe-name resolution ─────────────────────────────────────────

    fn default_pipe_name() -> String {
        // Stable, user-scoped pipe name. We use the USERNAME env var
        // (which on Windows reflects the interactive session's user)
        // for human readability, but the security boundary is the
        // owner-only DACL, not the name. We also sanitize to alnum +
        // `-` to keep the name within the named-pipe legal character
        // set and avoid surprises.
        let user = std::env::var("USERNAME")
            .or_else(|_| std::env::var("USER"))
            .unwrap_or_else(|_| "default".to_string());
        let safe: String = user
            .chars()
            .map(|c| {
                if c.is_ascii_alphanumeric() || c == '-' || c == '_' {
                    c
                } else {
                    '_'
                }
            })
            .collect();
        let safe = if safe.is_empty() { "default" } else { &safe };
        format!("\\\\.\\pipe\\sk-writer-{safe}")
    }

    /// Resolve the named-pipe name from `SK_WRITER_BROKER_SOCK` (if set)
    /// or the default. Accepts either a full `\\.\pipe\...` name or a
    /// short name; if the override does not look like a UNC pipe path
    /// we coerce it into one so tests can use bare strings.
    fn pipe_name() -> String {
        if let Ok(p) = std::env::var("SK_WRITER_BROKER_SOCK") {
            let raw = p.trim();
            if raw.starts_with("\\\\.\\pipe\\") || raw.starts_with("\\\\?\\pipe\\") {
                return raw.to_string();
            }
            // Treat as a bare pipe basename.
            let base = raw.trim_start_matches(['\\', '/']);
            return format!("\\\\.\\pipe\\{base}");
        }
        default_pipe_name()
    }

    // ── Client ───────────────────────────────────────────────────────

    pub(super) fn client_write(entry: &NewEntry) -> Result<i64, BrokerError> {
        match attempt_write(entry) {
            Ok(id) => return Ok(id),
            Err(BrokerError::Busy(s)) => return Err(BrokerError::Busy(s)),
            Err(BrokerError::Unavailable(_)) | Err(BrokerError::Other(_)) => {
                // Pipe missing or dead — spawn broker and retry once.
            }
            Err(other) => return Err(other),
        }
        spawn_broker_daemon().map_err(BrokerError::Unavailable)?;
        if !wait_for_pipe(SPAWN_HANDSHAKE_MS) {
            return Err(BrokerError::Unavailable(
                "broker did not come online within handshake window".into(),
            ));
        }
        attempt_write(entry)
    }

    fn attempt_write(entry: &NewEntry) -> Result<i64, BrokerError> {
        let key = load_or_create_key(&key_path())
            .map_err(|e| BrokerError::Unavailable(format!("key load: {e}")))?;
        let mut stream = connect_with_retry(2_000)
            .map_err(|e| BrokerError::Unavailable(format!("connect: {e}")))?;

        let body = json!({
            "op": "write",
            "entry": {
                "category":   entry.category,
                "title":      entry.title,
                "content":    entry.content,
                "tags":       entry.tags,
                "wing":       entry.wing,
                "room":       entry.room,
                "confidence": entry.confidence,
                "facts_json": entry.facts_json,
            }
        });
        let raw =
            serde_json::to_vec(&body).map_err(|e| BrokerError::Other(format!("encode: {e}")))?;
        send_frame(&mut stream, &key, &raw)
            .map_err(|e| BrokerError::Unavailable(format!("send: {e}")))?;
        let reply_body = recv_frame(&mut stream, &key)
            .map_err(|e| BrokerError::Unavailable(format!("recv: {e}")))?;
        let reply: Value = serde_json::from_slice(&reply_body)
            .map_err(|e| BrokerError::Other(format!("decode reply: {e}")))?;
        if reply.get("ok").and_then(|v| v.as_bool()).unwrap_or(false) {
            let id = reply
                .get("entry_id")
                .and_then(|v| v.as_i64())
                .ok_or_else(|| BrokerError::Other("reply missing entry_id".into()))?;
            Ok(id)
        } else {
            let msg = reply
                .get("error")
                .and_then(|v| v.as_str())
                .unwrap_or("unknown")
                .to_string();
            let busy = reply.get("busy").and_then(|v| v.as_bool()).unwrap_or(false);
            if busy {
                Err(BrokerError::Busy(msg))
            } else {
                Err(BrokerError::Other(msg))
            }
        }
    }

    fn connect_with_retry(total_ms: u64) -> Result<PipeStream, String> {
        let name_w = to_wide(&pipe_name());
        let deadline = Instant::now() + Duration::from_millis(total_ms);
        loop {
            // Open with no SHARE (0) so we get exclusive client-side
            // access to this pipe instance.
            let h = unsafe {
                CreateFileW(
                    name_w.as_ptr(),
                    GENERIC_READ | GENERIC_WRITE,
                    0,
                    std::ptr::null(),
                    OPEN_EXISTING,
                    FILE_ATTRIBUTE_NORMAL,
                    NULL_HANDLE,
                )
            };
            if h != INVALID_HANDLE_VALUE {
                return Ok(PipeStream::from_owned(h));
            }
            let err = unsafe { GetLastError() };
            if err == ERROR_PIPE_BUSY {
                // All instances busy — wait briefly for a free one.
                let remaining = deadline.saturating_duration_since(Instant::now());
                if remaining.is_zero() {
                    return Err(format!("pipe {} busy", pipe_name()));
                }
                let wait_ms = remaining.as_millis().min(500) as DWORD;
                unsafe {
                    WaitNamedPipeW(name_w.as_ptr(), wait_ms);
                }
                continue;
            }
            if err == ERROR_FILE_NOT_FOUND {
                return Err(format!("pipe {} not found", pipe_name()));
            }
            return Err(format!("CreateFileW failed (err {err})"));
        }
    }

    fn wait_for_pipe(timeout_ms: u64) -> bool {
        let name_w = to_wide(&pipe_name());
        let deadline = Instant::now() + Duration::from_millis(timeout_ms);
        while Instant::now() < deadline {
            // WaitNamedPipeW returns TRUE when an instance is or
            // becomes available within the wait window. We poll on
            // short windows so we honour the overall deadline.
            let ok = unsafe { WaitNamedPipeW(name_w.as_ptr(), 100) };
            if ok != 0 {
                return true;
            }
            std::thread::sleep(Duration::from_millis(25));
        }
        false
    }

    fn spawn_broker_daemon() -> Result<(), String> {
        use std::os::windows::process::CommandExt;
        let exe = std::env::current_exe().map_err(|e| format!("current_exe: {e}"))?;
        let mut cmd = std::process::Command::new(exe);
        cmd.env("SK_WRITER_BROKER_DAEMON", "1");
        cmd.stdin(std::process::Stdio::null())
            .stdout(std::process::Stdio::null())
            .stderr(std::process::Stdio::null());
        cmd.creation_flags(DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW);
        cmd.spawn().map_err(|e| format!("spawn: {e}"))?;
        Ok(())
    }

    // ── Server ───────────────────────────────────────────────────────

    fn open_or_create_for_broker() -> Result<Connection, String> {
        let path = knowledge_db_path();
        if let Some(parent) = path.parent() {
            fs::create_dir_all(parent).map_err(|e| format!("mkdir {}: {e}", parent.display()))?;
        }
        let conn = Connection::open_with_flags(
            &path,
            OpenFlags::SQLITE_OPEN_READ_WRITE
                | OpenFlags::SQLITE_OPEN_CREATE
                | OpenFlags::SQLITE_OPEN_NO_MUTEX,
        )
        .map_err(|e| format!("open {}: {e}", path.display()))?;
        conn.busy_timeout(Duration::from_millis(30_000))
            .map_err(|e| format!("busy_timeout: {e}"))?;
        conn.execute_batch(
            "PRAGMA journal_mode=WAL;\
             PRAGMA synchronous=NORMAL;\
             PRAGMA wal_autocheckpoint=1000;",
        )
        .map_err(|e| format!("pragmas: {e}"))?;
        ensure_extract_tables(&conn).map_err(|e| format!("ensure schema: {e}"))?;
        Ok(conn)
    }

    pub(super) fn run_server() -> Result<(), String> {
        let run_dir = run_dir();
        fs::create_dir_all(&run_dir).map_err(|e| format!("mkdir {}: {e}", run_dir.display()))?;

        let pid_path = pid_path();
        acquire_pid_file(&pid_path).map_err(|e| format!("acquire pid file: {e}"))?;

        let key = load_or_create_key(&key_path())?;
        let conn = open_or_create_for_broker().map_err(|e| format!("open knowledge.db: {e}"))?;
        let conn = Mutex::new(conn);

        let samples: Arc<Mutex<Vec<f64>>> =
            Arc::new(Mutex::new(Vec::with_capacity(SAMPLE_RING_CAPACITY)));
        let last_activity_ms = Arc::new(AtomicU64::new(now_ms()));
        let stop = Arc::new(AtomicBool::new(false));

        {
            let stop = stop.clone();
            let _ = ctrlc::set_handler(move || {
                stop.store(true, Ordering::SeqCst);
            });
        }

        spawn_samples_flusher(samples.clone(), stop.clone());

        let sd = owner_only_security_descriptor()?;
        let sa = SecurityAttributes {
            n_length: std::mem::size_of::<SecurityAttributes>() as DWORD,
            lp_security_descriptor: sd.0,
            b_inherit_handle: 0,
        };

        let name_w = to_wide(&pipe_name());

        // Create the first instance with FILE_FLAG_FIRST_PIPE_INSTANCE
        // so we fail loudly if anything else is squatting on this
        // pipe name. All subsequent instances within this process
        // share the same DACL by re-using `sa`.
        let mut first = true;
        let mut serve_result: Result<(), String> = Ok(());

        loop {
            if stop.load(Ordering::SeqCst) {
                break;
            }
            let since_ms = now_ms().saturating_sub(last_activity_ms.load(Ordering::Relaxed));
            if since_ms >= IDLE_EXIT_SECONDS * 1000 {
                break;
            }
            let remaining_idle_ms =
                (IDLE_EXIT_SECONDS * 1000).saturating_sub(since_ms).max(50) as DWORD;

            let open_mode = PIPE_ACCESS_DUPLEX
                | 0x4000_0000 // FILE_FLAG_OVERLAPPED
                | if first { FILE_FLAG_FIRST_PIPE_INSTANCE } else { 0 };
            let pipe_mode =
                PIPE_TYPE_BYTE | PIPE_READMODE_BYTE | PIPE_WAIT | PIPE_REJECT_REMOTE_CLIENTS;

            let pipe = unsafe {
                CreateNamedPipeW(
                    name_w.as_ptr(),
                    open_mode,
                    pipe_mode,
                    PIPE_UNLIMITED_INSTANCES,
                    1 << 20, // 1 MiB out
                    1 << 20, // 1 MiB in
                    0,       // default timeout
                    &sa as *const SecurityAttributes,
                )
            };
            if pipe == INVALID_HANDLE_VALUE {
                let err = unsafe { GetLastError() };
                serve_result = Err(format!(
                    "CreateNamedPipeW {} failed (err {err})",
                    pipe_name()
                ));
                break;
            }
            let pipe = WinHandle(pipe);
            first = false;

            // Overlapped ConnectNamedPipe: create an event, attach to
            // an OVERLAPPED, call ConnectNamedPipe, then
            // WaitForMultipleObjects on [connect_evt, stop_event_proxy
            // — actually we only have connect_evt; we use a short
            // timeout loop that also checks `stop`].
            let evt = unsafe {
                CreateEventW(
                    std::ptr::null(),
                    1, /* manual reset */
                    0, /* initially non-signaled */
                    std::ptr::null(),
                )
            };
            if evt.is_null() {
                let err = unsafe { GetLastError() };
                serve_result = Err(format!("CreateEventW failed (err {err})"));
                break;
            }
            let evt_guard = WinHandle(evt);
            let mut ov = Overlapped {
                internal: 0,
                internal_high: 0,
                offset: 0,
                offset_high: 0,
                h_event: evt_guard.raw(),
            };

            let connected;
            let connect_ok = unsafe { ConnectNamedPipe(pipe.raw(), &mut ov as *mut Overlapped) };
            if connect_ok != 0 {
                // ConnectNamedPipe in overlapped mode never returns
                // nonzero on success — this is documented as a "should
                // not happen" path. Treat as connected.
                connected = true;
            } else {
                let err = unsafe { GetLastError() };
                if err == ERROR_PIPE_CONNECTED {
                    // Client connected between CreateNamedPipe and
                    // ConnectNamedPipe — already attached, proceed.
                    connected = true;
                    let _ = unsafe { SetEvent(evt_guard.raw()) };
                } else if err == ERROR_IO_PENDING {
                    // Wait, but in slices so we can honour `stop` and
                    // idle-exit.
                    let mut got = false;
                    loop {
                        if stop.load(Ordering::SeqCst) {
                            break;
                        }
                        let since =
                            now_ms().saturating_sub(last_activity_ms.load(Ordering::Relaxed));
                        if since >= IDLE_EXIT_SECONDS * 1000 {
                            break;
                        }
                        let slice_ms = remaining_idle_ms.min(200);
                        let w = unsafe { WaitForSingleObject(evt_guard.raw(), slice_ms) };
                        if w == WAIT_OBJECT_0 {
                            got = true;
                            break;
                        }
                        if w == WAIT_TIMEOUT {
                            continue;
                        }
                        // Treat any other return as a hard failure for
                        // this iteration — cancel and move on.
                        unsafe {
                            CancelIoEx(pipe.raw(), &mut ov as *mut Overlapped);
                        }
                        break;
                    }
                    if !got {
                        unsafe {
                            CancelIoEx(pipe.raw(), &mut ov as *mut Overlapped);
                        }
                        // Either stop, idle-exit, or failure: drop
                        // pipe instance, then the outer loop will
                        // re-check `stop`/idle and exit cleanly.
                        continue;
                    }
                    connected = true;
                } else {
                    // Real failure: drop this instance and continue.
                    eprintln!("sk writer-broker: ConnectNamedPipe failed (err {err})");
                    continue;
                }
            }

            if connected {
                last_activity_ms.store(now_ms(), Ordering::Relaxed);
                let mut stream = PipeStream {
                    handle: pipe.raw(),
                    owned: false, // pipe handle is owned by `pipe` WinHandle
                };
                if let Err(e) = handle_request(&mut stream, &key, &conn, &samples) {
                    eprintln!("sk writer-broker: connection error: {e}");
                }
                // Flush + disconnect so the next CreateNamedPipeW
                // instance is the active listener and the client side
                // sees the response before EOF.
                unsafe {
                    FlushFileBuffers(pipe.raw());
                    DisconnectNamedPipe(pipe.raw());
                }
                last_activity_ms.store(now_ms(), Ordering::Relaxed);
            }
            // `pipe` and `evt_guard` drop here → CloseHandle.
        }

        if let Ok(s) = samples.lock() {
            write_samples_file(&s);
        }
        let _ = fs::remove_file(&pid_path);
        serve_result
    }

    fn now_ms() -> u64 {
        SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map(|d| d.as_millis() as u64)
            .unwrap_or(0)
    }

    fn spawn_samples_flusher(samples: Arc<Mutex<Vec<f64>>>, stop: Arc<AtomicBool>) {
        std::thread::spawn(move || {
            while !stop.load(Ordering::SeqCst) {
                std::thread::sleep(Duration::from_secs(5));
                let snap = samples.lock().ok().map(|s| s.clone()).unwrap_or_default();
                if !snap.is_empty() {
                    write_samples_file(&snap);
                }
            }
        });
    }

    fn acquire_pid_file(path: &Path) -> Result<(), String> {
        if let Some(parent) = path.parent() {
            fs::create_dir_all(parent).map_err(|e| e.to_string())?;
        }
        if let Ok(raw) = fs::read_to_string(path) {
            if let Ok(pid) = raw.trim().parse::<u32>() {
                if pid > 0 && pid_is_alive(pid) {
                    return Err(format!("another broker is already running (pid {pid})"));
                }
            }
            let _ = fs::remove_file(path);
        }
        let pid = std::process::id();
        atomic_write_owner_only(path, format!("{pid}\n").as_bytes())?;
        Ok(())
    }

    fn pid_is_alive(pid: u32) -> bool {
        unsafe {
            let h = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, 0, pid);
            if h.is_null() {
                return false;
            }
            let _guard = WinHandle(h);
            let mut code: DWORD = 0;
            if GetExitCodeProcess(h, &mut code as *mut DWORD) == 0 {
                return false;
            }
            code == STILL_ACTIVE
        }
    }

    // ── Pipe-name + SDDL exposed to tests ────────────────────────────

    #[cfg(test)]
    pub(super) fn _test_pipe_name() -> String {
        pipe_name()
    }
    #[cfg(test)]
    pub(super) fn _test_default_pipe_name() -> String {
        default_pipe_name()
    }

    // Silence dead-code warnings for items kept for completeness/use
    // by future tests but not exercised on every code path.
    #[allow(dead_code)]
    fn _suppress_unused_warnings() {
        let _ = (
            ERROR_SEM_TIMEOUT,
            ERROR_NO_DATA,
            ERROR_MORE_DATA,
            INFINITE,
            NMPWAIT_USE_DEFAULT_WAIT,
            WaitForMultipleObjects,
            GetOverlappedResult,
            ResetEvent,
            OsString::new(),
            OsString::from_wide(&[0u16]),
        );
    }
}

// ── Unit tests ───────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn percentile_basic() {
        let v = vec![1.0, 2.0, 3.0, 4.0, 5.0];
        assert!((percentile(&v, 50.0) - 3.0).abs() < 1e-9);
        let p95 = percentile(&v, 95.0);
        assert!(p95 > 4.5 && p95 <= 5.0, "p95={p95}");
    }

    #[test]
    fn broker_enabled_reads_env() {
        let saved = std::env::var("SK_WRITER_BROKER").ok();
        std::env::remove_var("SK_WRITER_BROKER");
        assert!(!broker_enabled());
        std::env::set_var("SK_WRITER_BROKER", "1");
        assert!(broker_enabled());
        std::env::set_var("SK_WRITER_BROKER", "0");
        assert!(!broker_enabled());
        match saved {
            Some(v) => std::env::set_var("SK_WRITER_BROKER", v),
            None => std::env::remove_var("SK_WRITER_BROKER"),
        }
    }

    // ── Windows runtime-fix coverage (issue #572) ────────────────────
    //
    // These tests verify the platform-specific RNG and owner-only
    // file-creation paths that replaced the broken `/dev/urandom`
    // call on Windows. They run on `windows-latest` in CI; they are
    // `cfg(windows)`-gated so the rest of the platform matrix is
    // unaffected.

    #[cfg(windows)]
    #[test]
    fn windows_fill_random_returns_distinct_nonzero_samples() {
        let mut a = [0u8; 32];
        let mut b = [0u8; 32];
        super::windows_impl::fill_random(&mut a).expect("BCryptGenRandom #1");
        super::windows_impl::fill_random(&mut b).expect("BCryptGenRandom #2");
        // Two 32-byte CSPRNG samples colliding is astronomically
        // unlikely; if this ever fires the RNG is broken.
        assert_ne!(a, b, "two random samples must differ");
        assert!(a.iter().any(|&x| x != 0), "sample must not be all-zero");
        assert!(b.iter().any(|&x| x != 0), "sample must not be all-zero");
    }

    #[cfg(windows)]
    #[test]
    fn windows_fill_random_handles_empty_buffer() {
        let mut empty: [u8; 0] = [];
        super::windows_impl::fill_random(&mut empty).expect("empty buffer is a no-op");
    }

    #[cfg(windows)]
    #[test]
    fn windows_create_new_owner_only_creates_then_rejects_duplicate() {
        let dir = std::env::temp_dir().join(format!(
            "sk-broker-win-create-{}-{:x}",
            std::process::id(),
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .map(|d| d.as_nanos())
                .unwrap_or(0)
        ));
        std::fs::create_dir_all(&dir).expect("mkdir tmp");
        let p = dir.join("k.bin");
        {
            let f = super::windows_impl::create_new_owner_only_file(&p).expect("first create");
            // Drop closes the handle.
            drop(f);
        }
        assert!(p.exists(), "file should exist after first create");
        match super::windows_impl::create_new_owner_only_file(&p) {
            Err(super::windows_impl::WinCreateError::AlreadyExists) => {}
            Ok(_) => panic!("second create_new must fail with AlreadyExists"),
            Err(super::windows_impl::WinCreateError::Other(e)) => {
                panic!("expected AlreadyExists, got Other({e})")
            }
        }
        super::windows_impl::ensure_owned_by_current_user(&p)
            .expect("owner SID must equal current user");
        let _ = std::fs::remove_file(&p);
        let _ = std::fs::remove_dir(&dir);
    }

    #[cfg(windows)]
    #[test]
    fn windows_load_or_create_key_is_random_stable_and_owner_only() {
        // Exercise the *shared* key code path (the function that
        // previously force-opened /dev/urandom on Windows). After the
        // runtime-fix this must succeed end-to-end on Windows.
        let dir = std::env::temp_dir().join(format!(
            "sk-broker-win-key-{}-{:x}",
            std::process::id(),
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .map(|d| d.as_nanos())
                .unwrap_or(0)
        ));
        std::fs::create_dir_all(&dir).expect("mkdir tmp");
        let p = dir.join("sk-writer.key");
        let k1 = super::load_or_create_key(&p).expect("first load_or_create_key");
        let k2 = super::load_or_create_key(&p).expect("second load_or_create_key");
        assert_eq!(k1, k2, "key must be stable across reads");
        assert!(k1.iter().any(|&x| x != 0), "key must be non-zero");
        let meta = std::fs::metadata(&p).expect("stat key");
        assert!(meta.is_file());
        assert_eq!(meta.len(), 32, "key file must be exactly 32 bytes");
        super::windows_impl::ensure_owned_by_current_user(&p)
            .expect("key owner SID must equal current user");
        let _ = std::fs::remove_file(&p);
        let _ = std::fs::remove_dir(&dir);
    }

    #[cfg(windows)]
    #[test]
    fn windows_pipe_name_resolves_default_and_override() {
        let saved = std::env::var("SK_WRITER_BROKER_SOCK").ok();
        std::env::remove_var("SK_WRITER_BROKER_SOCK");
        let default_name = super::windows_impl::_test_default_pipe_name();
        assert!(
            default_name.starts_with("\\\\.\\pipe\\sk-writer-"),
            "default pipe name should be under \\\\.\\pipe\\sk-writer- but was {default_name}"
        );
        std::env::set_var("SK_WRITER_BROKER_SOCK", "sk-writer-test-override");
        let overridden = super::windows_impl::_test_pipe_name();
        assert_eq!(overridden, "\\\\.\\pipe\\sk-writer-test-override");
        match saved {
            Some(v) => std::env::set_var("SK_WRITER_BROKER_SOCK", v),
            None => std::env::remove_var("SK_WRITER_BROKER_SOCK"),
        }
    }
}
