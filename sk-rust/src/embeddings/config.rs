//! Embedding provider configuration.
//!
//! Loads and merges `embedding-config.json` with built-in defaults.
//! Mirrors Python's `load_config()`, `resolve_provider()`, and `get_api_key()`.

use crate::config::resolve_home_dir;
use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::path::PathBuf;

// ── Provider / config types ─────────────────────────────────────────────

/// Configuration for a single embedding provider.
#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct ProviderConfig {
    #[serde(default)]
    pub base_url: String,
    #[serde(default)]
    pub model: String,
    #[serde(default = "default_dimensions")]
    pub dimensions: u32,
    #[serde(default)]
    pub env_key: String,
    #[serde(default)]
    pub api_key: String,
}

fn default_dimensions() -> u32 {
    768
}

/// Top-level embedding configuration, matching Python's `DEFAULT_CONFIG`.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct EmbedConfig {
    #[serde(default = "default_active_provider")]
    pub active_provider: String,
    #[serde(default = "default_fallback")]
    pub fallback: String,
    #[serde(default = "default_batch_size")]
    pub batch_size: u32,
    #[serde(default)]
    pub providers: HashMap<String, ProviderConfig>,
}

fn default_active_provider() -> String {
    "auto".to_string()
}
fn default_fallback() -> String {
    "tfidf".to_string()
}
fn default_batch_size() -> u32 {
    100
}

impl Default for EmbedConfig {
    fn default() -> Self {
        let mut providers = HashMap::new();
        providers.insert(
            "openai".to_string(),
            ProviderConfig {
                base_url: "https://api.openai.com/v1".to_string(),
                model: "text-embedding-3-small".to_string(),
                dimensions: 1536,
                env_key: "OPENAI_API_KEY".to_string(),
                api_key: String::new(),
            },
        );
        providers.insert(
            "fireworks".to_string(),
            ProviderConfig {
                base_url: "https://api.fireworks.ai/inference/v1".to_string(),
                model: "nomic-ai/nomic-embed-text-v1.5".to_string(),
                dimensions: 768,
                env_key: "FIREWORKS_API_KEY".to_string(),
                api_key: String::new(),
            },
        );
        providers.insert(
            "openrouter".to_string(),
            ProviderConfig {
                base_url: "https://openrouter.ai/api/v1".to_string(),
                model: "openai/text-embedding-3-small".to_string(),
                dimensions: 1536,
                env_key: "OPENROUTER_API_KEY".to_string(),
                api_key: String::new(),
            },
        );
        providers.insert(
            "custom".to_string(),
            ProviderConfig {
                base_url: String::new(),
                model: String::new(),
                dimensions: 768,
                env_key: "EMBEDDING_API_KEY".to_string(),
                api_key: String::new(),
            },
        );
        Self {
            active_provider: "auto".to_string(),
            fallback: "tfidf".to_string(),
            batch_size: 100,
            providers,
        }
    }
}

// ── Provider resolution priority (matches Python's AUTO_PRIORITY) ───────

/// Provider names tried in order when `active_provider = "auto"`.
pub const AUTO_PRIORITY: &[&str] = &["fireworks", "openai", "openrouter", "custom"];

// ── Paths ───────────────────────────────────────────────────────────────

/// Path to `embedding-config.json`.
///
/// Matches Python: `TOOLS_DIR / "embedding-config.json"` where
/// `TOOLS_DIR = ~/.copilot/tools/`.
/// Override with `SK_EMBED_CONFIG` env var (for tests).
pub fn config_path() -> PathBuf {
    if let Ok(p) = std::env::var("SK_EMBED_CONFIG") {
        return PathBuf::from(p);
    }
    resolve_home_dir()
        .unwrap_or_else(|| PathBuf::from("."))
        .join(".copilot")
        .join("tools")
        .join("embedding-config.json")
}

// ── Config I/O ─────────────────────────────────────────────────────────

/// Load config from disk, merging user overrides onto built-in defaults.
///
/// Mirrors Python's `load_config()`: starts from `DEFAULT_CONFIG`,
/// then updates `providers` and top-level keys from the on-disk JSON.
/// If the file is missing or malformed, returns the built-in defaults.
pub fn load_config() -> EmbedConfig {
    let path = config_path();
    let mut base = EmbedConfig::default();

    let content = match std::fs::read_to_string(&path) {
        Ok(c) => c,
        Err(_) => return base,
    };

    // Deserialize as a loosely typed map to allow partial configs
    let raw: serde_json::Value = match serde_json::from_str(&content) {
        Ok(v) => v,
        Err(_) => return base,
    };

    if let Some(s) = raw.get("active_provider").and_then(|v| v.as_str()) {
        base.active_provider = s.to_string();
    }
    if let Some(s) = raw.get("fallback").and_then(|v| v.as_str()) {
        base.fallback = s.to_string();
    }
    if let Some(n) = raw.get("batch_size").and_then(|v| v.as_u64()) {
        base.batch_size = n as u32;
    }

    // Merge providers — only update fields that are present in the user file
    if let Some(providers_map) = raw.get("providers").and_then(|v| v.as_object()) {
        for (name, prov_val) in providers_map {
            let entry = base
                .providers
                .entry(name.clone())
                .or_insert_with(ProviderConfig::default);
            if let Some(s) = prov_val.get("base_url").and_then(|v| v.as_str()) {
                entry.base_url = s.to_string();
            }
            if let Some(s) = prov_val.get("model").and_then(|v| v.as_str()) {
                entry.model = s.to_string();
            }
            if let Some(n) = prov_val.get("dimensions").and_then(|v| v.as_u64()) {
                entry.dimensions = n as u32;
            }
            if let Some(s) = prov_val.get("env_key").and_then(|v| v.as_str()) {
                entry.env_key = s.to_string();
            }
            if let Some(s) = prov_val.get("api_key").and_then(|v| v.as_str()) {
                entry.api_key = s.to_string();
            }
        }
    }

    base
}

// ── Key / provider helpers ──────────────────────────────────────────────

/// Get API key: environment variable first, then `api_key` from config file.
///
/// Mirrors Python's `get_api_key()`.
pub fn get_api_key(prov: &ProviderConfig) -> String {
    if !prov.env_key.is_empty() {
        if let Ok(val) = std::env::var(&prov.env_key) {
            if !val.is_empty() {
                return val;
            }
        }
    }
    prov.api_key.clone()
}

/// Resolve the active provider. Returns `(name, config)` or `None`.
///
/// Mirrors Python's `resolve_provider()`:
/// - If `active_provider` is not `"auto"`, uses that provider (if key present).
/// - Otherwise iterates `AUTO_PRIORITY` and returns the first with a key.
pub fn resolve_provider(config: &EmbedConfig) -> Option<(String, ProviderConfig)> {
    let active = config.active_provider.as_str();

    if active != "auto" {
        let prov = config.providers.get(active)?;
        if !get_api_key(prov).is_empty() {
            return Some((active.to_string(), prov.clone()));
        }
        return None;
    }

    for name in AUTO_PRIORITY {
        if let Some(prov) = config.providers.get(*name) {
            if !get_api_key(prov).is_empty() && !prov.base_url.is_empty() {
                return Some((name.to_string(), prov.clone()));
            }
        }
    }
    None
}

// ── Config write ────────────────────────────────────────────────────────

/// Serialize `EmbedConfig` back to the JSON file on disk.
///
/// Mirrors Python's `save_config()`.  Providers are written in `AUTO_PRIORITY`
/// order (fireworks, openai, openrouter, custom) followed by any extras so
/// the file is stable and readable.
///
/// Returns the path written on success.
pub fn save_config(config: &EmbedConfig) -> std::io::Result<std::path::PathBuf> {
    let path = config_path();
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)?;
    }

    // Build providers object in stable order
    let mut providers_map = serde_json::Map::new();
    let mut ordered: Vec<&str> = AUTO_PRIORITY.to_vec();
    for name in config.providers.keys() {
        if !ordered.contains(&name.as_str()) {
            ordered.push(name.as_str());
        }
    }
    for name in ordered {
        if let Some(prov) = config.providers.get(name) {
            providers_map.insert(
                name.to_string(),
                serde_json::json!({
                    "base_url":   prov.base_url,
                    "model":      prov.model,
                    "dimensions": prov.dimensions,
                    "env_key":    prov.env_key,
                    "api_key":    prov.api_key,
                }),
            );
        }
    }

    let output = serde_json::json!({
        "active_provider": config.active_provider,
        "fallback":        config.fallback,
        "batch_size":      config.batch_size,
        "providers":       serde_json::Value::Object(providers_map),
    });

    let json_str = serde_json::to_string_pretty(&output)
        .map_err(|e| std::io::Error::new(std::io::ErrorKind::Other, e))?;
    std::fs::write(&path, json_str)?;
    Ok(path)
}

// ── Tests ───────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::{Mutex, OnceLock};
    use std::time::{SystemTime, UNIX_EPOCH};

    fn env_lock() -> std::sync::MutexGuard<'static, ()> {
        static LOCK: OnceLock<Mutex<()>> = OnceLock::new();
        LOCK.get_or_init(|| Mutex::new(())).lock().unwrap()
    }

    fn unique_test_path(prefix: &str) -> std::path::PathBuf {
        std::path::PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("target")
            .join(format!(
                "{prefix}-{}-{}.json",
                std::process::id(),
                SystemTime::now()
                    .duration_since(UNIX_EPOCH)
                    .unwrap()
                    .as_nanos()
            ))
    }

    #[test]
    fn default_config_has_all_providers() {
        let cfg = EmbedConfig::default();
        for name in ["openai", "fireworks", "openrouter", "custom"] {
            assert!(
                cfg.providers.contains_key(name),
                "missing default provider: {name}"
            );
        }
    }

    #[test]
    fn default_active_provider_is_auto() {
        let cfg = EmbedConfig::default();
        assert_eq!(cfg.active_provider, "auto");
    }

    #[test]
    fn default_batch_size_is_100() {
        let cfg = EmbedConfig::default();
        assert_eq!(cfg.batch_size, 100);
    }

    #[test]
    fn get_api_key_returns_config_file_key_when_no_env() {
        let prov = ProviderConfig {
            env_key: "SK_TEST_EMBED_KEY_NONEXISTENT_XYZ".to_string(),
            api_key: "file-key".to_string(),
            ..Default::default()
        };
        // env var not set → returns file key
        assert_eq!(get_api_key(&prov), "file-key");
    }

    #[test]
    fn get_api_key_empty_when_nothing_set() {
        let prov = ProviderConfig {
            env_key: "SK_TEST_EMBED_KEY_NONEXISTENT_XYZ".to_string(),
            api_key: String::new(),
            ..Default::default()
        };
        assert!(get_api_key(&prov).is_empty());
    }

    #[test]
    fn resolve_provider_explicit_with_key() {
        let mut cfg = EmbedConfig::default();
        cfg.active_provider = "openai".to_string();
        cfg.providers.get_mut("openai").unwrap().api_key = "test-key".to_string();

        let result = resolve_provider(&cfg);
        assert!(result.is_some(), "expected provider to be resolved");
        let (name, _) = result.unwrap();
        assert_eq!(name, "openai");
    }

    #[test]
    fn resolve_provider_explicit_without_key_returns_none() {
        let _guard = env_lock();
        let mut cfg = EmbedConfig::default();
        cfg.active_provider = "openai".to_string();
        // No api_key, no env var
        cfg.providers.get_mut("openai").unwrap().api_key = String::new();

        // Ensure env var is not set
        std::env::remove_var("OPENAI_API_KEY");
        let result = resolve_provider(&cfg);
        assert!(result.is_none());
    }

    #[test]
    fn resolve_provider_auto_picks_first_with_key() {
        let _guard = env_lock();
        let mut cfg = EmbedConfig::default();
        cfg.active_provider = "auto".to_string();

        // Set only openai key (not fireworks, which is higher priority)
        std::env::remove_var("FIREWORKS_API_KEY");
        std::env::remove_var("OPENROUTER_API_KEY");
        std::env::remove_var("EMBEDDING_API_KEY");
        cfg.providers.get_mut("openai").unwrap().api_key = "openai-key".to_string();
        // Fireworks has no key
        cfg.providers.get_mut("fireworks").unwrap().api_key = String::new();

        let result = resolve_provider(&cfg);
        assert!(result.is_some(), "expected openai to be resolved");
        let (name, _) = result.unwrap();
        assert_eq!(name, "openai");
    }

    #[test]
    fn load_config_returns_defaults_when_file_missing() {
        let _guard = env_lock();
        // Point SK_EMBED_CONFIG at a non-existent path
        std::env::set_var("SK_EMBED_CONFIG", "/nonexistent/path/embedding-config.json");
        let cfg = load_config();
        std::env::remove_var("SK_EMBED_CONFIG");

        assert_eq!(cfg.active_provider, "auto");
        assert_eq!(cfg.batch_size, 100);
    }

    #[test]
    fn load_config_merges_partial_json() {
        let _guard = env_lock();
        // Build a partial JSON string (no providers section, just top-level)
        let json = r#"{"active_provider":"fireworks","batch_size":50,"providers":{"fireworks":{"api_key":"fw_test"}}}"#;

        let tmp = unique_test_path("sk_test_embed_config");
        std::fs::write(&tmp, json).unwrap();
        std::env::set_var("SK_EMBED_CONFIG", tmp.to_str().unwrap());
        let cfg = load_config();
        std::env::set_var("SK_EMBED_CONFIG", "/nonexistent"); // unset
        std::fs::remove_file(&tmp).ok();

        assert_eq!(cfg.active_provider, "fireworks");
        assert_eq!(cfg.batch_size, 50);
        // fireworks api_key should be merged
        let fw = cfg.providers.get("fireworks").unwrap();
        assert_eq!(fw.api_key, "fw_test");
        // openai should still be present from defaults
        assert!(cfg.providers.contains_key("openai"));
    }

    #[test]
    fn auto_priority_order_matches_python() {
        // Python AUTO_PRIORITY = ["fireworks", "openai", "openrouter", "custom"]
        assert_eq!(
            AUTO_PRIORITY,
            &["fireworks", "openai", "openrouter", "custom"]
        );
    }

    // ── save_config ──

    #[test]
    fn save_config_roundtrip_top_level_fields() {
        let _guard = env_lock();
        let mut cfg = EmbedConfig::default();
        cfg.active_provider = "fireworks".to_string();
        cfg.fallback = "none".to_string();
        cfg.batch_size = 42;

        let tmp = unique_test_path("sk_test_save_cfg_roundtrip");
        // Set env var right before save (minimal race window)
        std::env::set_var("SK_EMBED_CONFIG", tmp.to_str().unwrap());
        save_config(&cfg).unwrap();
        std::env::remove_var("SK_EMBED_CONFIG");

        // Read and verify the file directly — avoids a second env-var lookup
        // which is susceptible to parallel-test races.
        let content = std::fs::read_to_string(&tmp).unwrap();
        std::fs::remove_file(&tmp).ok();

        let v: serde_json::Value = serde_json::from_str(&content).unwrap();
        assert_eq!(v["active_provider"].as_str().unwrap(), "fireworks");
        assert_eq!(v["fallback"].as_str().unwrap(), "none");
        assert_eq!(v["batch_size"].as_u64().unwrap(), 42);
    }

    #[test]
    fn save_config_roundtrip_api_key() {
        let _guard = env_lock();
        let mut cfg = EmbedConfig::default();
        cfg.providers.get_mut("openai").unwrap().api_key = "sk-test-key-xyz".to_string();

        let tmp = unique_test_path("sk_test_save_cfg_apikey");
        std::env::set_var("SK_EMBED_CONFIG", tmp.to_str().unwrap());
        save_config(&cfg).unwrap();
        std::env::remove_var("SK_EMBED_CONFIG");

        let content = std::fs::read_to_string(&tmp).unwrap();
        std::fs::remove_file(&tmp).ok();

        let v: serde_json::Value = serde_json::from_str(&content).unwrap();
        assert_eq!(
            v["providers"]["openai"]["api_key"].as_str().unwrap(),
            "sk-test-key-xyz"
        );
        // Other providers preserved
        assert!(v["providers"]["fireworks"].is_object());
        assert!(v["providers"]["openrouter"].is_object());
        assert!(v["providers"]["custom"].is_object());
    }

    #[test]
    fn save_config_roundtrip_custom_provider() {
        let _guard = env_lock();
        let mut cfg = EmbedConfig::default();
        if let Some(custom) = cfg.providers.get_mut("custom") {
            custom.base_url = "http://localhost:11434/v1".to_string();
            custom.model = "nomic-embed-text".to_string();
            custom.dimensions = 512;
        }
        cfg.active_provider = "custom".to_string();

        let tmp = unique_test_path("sk_test_save_cfg_custom");
        std::env::set_var("SK_EMBED_CONFIG", tmp.to_str().unwrap());
        save_config(&cfg).unwrap();
        std::env::remove_var("SK_EMBED_CONFIG");

        let content = std::fs::read_to_string(&tmp).unwrap();
        std::fs::remove_file(&tmp).ok();

        let v: serde_json::Value = serde_json::from_str(&content).unwrap();
        assert_eq!(v["active_provider"].as_str().unwrap(), "custom");
        assert_eq!(
            v["providers"]["custom"]["base_url"].as_str().unwrap(),
            "http://localhost:11434/v1"
        );
        assert_eq!(
            v["providers"]["custom"]["model"].as_str().unwrap(),
            "nomic-embed-text"
        );
        assert_eq!(
            v["providers"]["custom"]["dimensions"].as_u64().unwrap(),
            512
        );
    }
}
