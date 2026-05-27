use super::*;
use serde_json::json;
use std::sync::{Mutex, OnceLock};

fn env_lock() -> std::sync::MutexGuard<'static, ()> {
    static LOCK: OnceLock<Mutex<()>> = OnceLock::new();
    LOCK.get_or_init(|| Mutex::new(()))
        .lock()
        .unwrap_or_else(|e| e.into_inner())
}

mod all_rules;
mod auto_flush_learn;
mod edit_track;
mod guard;
mod learn;
mod session;
mod tentacle;
mod verification;
