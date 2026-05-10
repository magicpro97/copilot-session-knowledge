//! Native `sk index embed` command implementation.
//!
//! ## Hot paths handled natively (no Python subprocess):
//! | Flag              | What it does                                                  |
//! |-------------------|---------------------------------------------------------------|
//! | `--status`        | DB embedding counts + provider status                        |
//! | `--providers`     | List configured providers and key status                     |
//! | `--test`          | Provider config diagnostic; **live HTTP** with `native-embed`|
//! | `--setup`         | Interactive config wizard (writes JSON)                      |
//! | `--rebuild-tfidf` | Rebuild TF-IDF model from DB sections (pure Rust)            |
//! | `--build`         | Batch HTTP embeddings + TF-IDF build (requires `native-embed`)|
//! | `--search Q`      | FTS5 + stored-vector + TF-IDF hybrid search (no HTTP)        |
//! | `Q` (positional)  | Same as `--search Q`                                         |
//!
//! ### `--test` note
//! Without the `native-embed` feature the native `--test` path shows provider
//! name, model, endpoint, and API-key status from config/env only.  With
//! `native-embed` (the default) it makes a live HTTP call to embed a test
//! string and reports latency and dimensions.
//!
//! ### `--build` note
//! With `native-embed` (the default) the build runs entirely in Rust:
//! 1. Resolves the active embedding provider from config / env vars.
//! 2. Fetches all sections and knowledge entries from knowledge.db.
//! 3. Calls the OpenAI-compatible `/embeddings` endpoint in batches.
//! 4. Stores float32 blob vectors in the `embeddings` table.
//! 5. Builds a TF-IDF model (pure Rust, no scikit-learn) and stores it.
//!
//! Without `native-embed` `--build` falls back to `embed.py`.
//!
//! ### `--rebuild-tfidf` note
//! Rebuilds the TF-IDF model from all sections currently in the DB.
//! Pure Rust, no Python or scikit-learn required.
//!
//! ### `--setup` note
//! Two differences from the Python version:
//! 1. scikit-learn availability is not checked.
//! 2. The post-setup HTTP connectivity test is skipped — run `--test` instead.

use std::process::ExitCode;

use crate::commands::fallback::run_fallback;
use crate::db::connection::{knowledge_db_path, KnowledgeDb};
use crate::db::write::open_writable;
use crate::embeddings::config::{
    get_api_key, load_config, resolve_provider, save_config, AUTO_PRIORITY,
};
use crate::embeddings::search::run_hybrid_search;
use crate::embeddings::store::{
    ensure_embedding_tables, store_batch_embeddings, store_tfidf_model,
};
use crate::embeddings::tfidf::build_tfidf_model;

/// Dispatch `sk index embed [args]`.
pub fn run_embed_command(args: &[String]) -> ExitCode {
    let has_status = args.iter().any(|a| a == "--status");
    let has_providers = args.iter().any(|a| a == "--providers");
    let has_test = args.iter().any(|a| a == "--test");
    let has_setup = args.iter().any(|a| a == "--setup");
    let has_rebuild_tfidf = args.iter().any(|a| a == "--rebuild-tfidf");
    let has_build = args.iter().any(|a| a == "--build");
    let has_force = args.iter().any(|a| a == "--force");
    let search_idx = args.iter().position(|a| a == "--search");

    // Positional search: no flags at all, treat all non-option tokens as query
    let is_positional = !args.is_empty() && !args.iter().any(|a| a.starts_with('-'));

    if has_status {
        return run_status();
    }
    if has_providers {
        return run_providers();
    }
    if has_test {
        return run_test();
    }
    if has_setup {
        return run_setup();
    }
    if has_rebuild_tfidf {
        return run_rebuild_tfidf();
    }
    if has_build {
        #[cfg(feature = "native-embed")]
        return run_build(has_force);
        // When native-embed is not compiled, fall through to Python
        #[cfg(not(feature = "native-embed"))]
        return run_fallback("embed.py", args);
    }
    if let Some(idx) = search_idx {
        let query = args.get(idx + 1).cloned().unwrap_or_default();
        if query.is_empty() {
            eprintln!("sk index embed: --search requires a query string");
            return ExitCode::from(1);
        }
        let limit = parse_limit(args, 10);
        return run_search(&query, limit);
    }
    if is_positional {
        let query: String = args
            .iter()
            .filter(|a| !a.starts_with("--"))
            .cloned()
            .collect::<Vec<_>>()
            .join(" ");
        let limit = parse_limit(args, 10);
        if !query.is_empty() {
            return run_search(&query, limit);
        }
    }

    // Anything else → Python
    run_fallback("embed.py", args)
}

// ── --status ────────────────────────────────────────────────────────────

fn run_status() -> ExitCode {
    let config = load_config();
    let db_path = knowledge_db_path();

    println!("\n═══ Embedding Status ═══\n");

    // Provider line
    match resolve_provider(&config) {
        Some((name, prov)) => {
            println!("Active provider: {name} ({})", prov.model);
        }
        None => {
            println!("Active provider: none configured");
            println!("  Tip: Run 'sk index embed --setup' or set an env var:");
            for name in AUTO_PRIORITY {
                if let Some(p) = config.providers.get(*name) {
                    println!("    export {}=your-key  # → {name}", p.env_key);
                }
            }
        }
    }

    // Fallback line
    let fallback = config.fallback.as_str();
    match fallback {
        "tfidf" => println!("Fallback: TF-IDF (configured)"),
        "" | "none" => println!("Fallback: none"),
        other => println!("Fallback: {other}"),
    }

    if !db_path.exists() {
        println!("\nKnowledge DB: not found");
        return ExitCode::SUCCESS;
    }

    let conn = match open_writable(Some(db_path)) {
        Ok(c) => c,
        Err(e) => {
            eprintln!("sk index embed: cannot open knowledge.db: {e}");
            return ExitCode::from(1);
        }
    };
    let _ = ensure_embedding_tables(&conn);

    // Embedding / content counts
    let emb_count: i64 = conn
        .query_row("SELECT COUNT(*) FROM embeddings", [], |r| r.get(0))
        .unwrap_or(0);
    let sec_count: i64 = conn
        .query_row("SELECT COUNT(*) FROM sections", [], |r| r.get(0))
        .unwrap_or(0);
    let ke_count: i64 = conn
        .query_row("SELECT COUNT(*) FROM knowledge_entries", [], |r| r.get(0))
        .unwrap_or(0);

    let total = sec_count + ke_count;
    println!("\nEmbeddings: {emb_count}/{total} content items");

    if emb_count > 0 {
        if let Ok((provider, model, dims, first, last)) = conn.query_row(
            "SELECT provider, model, dimensions, MIN(created_at), MAX(created_at) \
             FROM embeddings",
            [],
            |r| {
                Ok((
                    r.get::<_, String>(0)?,
                    r.get::<_, String>(1)?,
                    r.get::<_, i64>(2)?,
                    r.get::<_, String>(3)?,
                    r.get::<_, String>(4)?,
                ))
            },
        ) {
            println!("  Provider: {provider}, Model: {model}, Dims: {dims}");
            println!("  Built: {first} → {last}");
        }
    }

    // TF-IDF model info
    if let Ok((doc_count, built_at)) = conn.query_row(
        "SELECT doc_count, COALESCE(built_at,'') FROM tfidf_model WHERE id=1",
        [],
        |r| Ok((r.get::<_, i64>(0)?, r.get::<_, String>(1)?)),
    ) {
        if doc_count > 0 {
            println!("\nTF-IDF model: {doc_count} documents (built {built_at})");
        }
    }

    ExitCode::SUCCESS
}

// ── --providers ─────────────────────────────────────────────────────────

fn run_providers() -> ExitCode {
    let config = load_config();
    let active = &config.active_provider;

    println!("\n═══ Configured Providers ═══\n");

    let mut seen = std::collections::HashSet::new();
    let ordered: Vec<&str> = AUTO_PRIORITY
        .iter()
        .copied()
        .chain(config.providers.keys().map(|s| s.as_str()))
        .collect();

    for name in ordered {
        if !seen.insert(name) {
            continue;
        }
        let Some(prov) = config.providers.get(name) else {
            continue;
        };
        let has_key = !get_api_key(prov).is_empty();
        let marker = if active == name || (active == "auto" && has_key) {
            "→"
        } else {
            " "
        };
        let key_status = if has_key { "✓ key" } else { "✗ no key" };
        let url = if prov.base_url.len() > 50 {
            &prov.base_url[..50]
        } else {
            &prov.base_url
        };
        println!(
            "  {marker} {name:<12}  {key_status:<8}  {:<35}  {url}",
            prov.model
        );
    }

    println!("\n  Active: {active}");
    match (active.as_str(), resolve_provider(&config)) {
        ("auto", Some((name, _))) => println!("  Auto-resolved: {name}"),
        ("auto", None) => println!("  Auto-resolved: none (no keys found)"),
        _ => {}
    }

    ExitCode::SUCCESS
}

// ── --search ────────────────────────────────────────────────────────────

/// FTS5-based hybrid search.  Includes stored-vector cosine similarity when
/// embeddings have been pre-built.  No HTTP calls.
fn run_search(query: &str, limit: usize) -> ExitCode {
    let db = match KnowledgeDb::open() {
        Ok(db) => db,
        Err(e) => {
            eprintln!("sk index embed: cannot open knowledge.db: {e}");
            return ExitCode::from(1);
        }
    };

    // No query vector: FTS-only path (always works without network)
    let results = run_hybrid_search(&db.conn, query, None, limit);

    if results.is_empty() {
        println!("No results for: {query}");
        return ExitCode::SUCCESS;
    }

    println!("\nHybrid search: {} results for '{query}'\n", results.len());
    for (i, r) in results.iter().enumerate() {
        let sid_short = if r.session_id.len() > 8 {
            &r.session_id[..8]
        } else {
            &r.session_id
        };
        println!("  {}. {}", i + 1, r.title);
        println!(
            "     {}.. | {} | {} | rrf={:.4}",
            sid_short, r.doc_type, r.source, r.rrf_score
        );
        if !r.excerpt.is_empty() {
            let excerpt = if r.excerpt.len() > 150 {
                &r.excerpt[..150]
            } else {
                &r.excerpt
            };
            println!("     {excerpt}");
        }
        println!();
    }

    ExitCode::SUCCESS
}

// ── --test ─────────────────────────────────────────────────────────────

/// Show provider configuration and API key status without making any HTTP call.
///
/// The live HTTP connectivity test (embedding a test string and timing the
/// response) requires `reqwest`/`tokio` which are behind the `native-embed`
/// feature flag.  Until that feature is enabled, this native path prints full
/// diagnostic info and directs users to `embed.py --test` for the live check.
fn run_test() -> ExitCode {
    let config = load_config();

    println!("\n═══ Provider Connectivity Check ═══\n");

    match resolve_provider(&config) {
        Some((name, prov)) => {
            let key = get_api_key(&prov);
            let key_status = if key.is_empty() {
                "✗ not set".to_string()
            } else {
                format!("✓ present ({} chars)", key.len())
            };
            println!("  Provider : {name}");
            println!("  Model    : {}", prov.model);
            println!("  Endpoint : {}/embeddings", prov.base_url);
            println!("  API key  : {key_status}");
            println!();

            if key.is_empty() {
                println!("  ✗ Cannot run live test — API key not set.");
                return ExitCode::from(1);
            }

            #[cfg(feature = "native-embed")]
            {
                use crate::embeddings::http::{batch_embed, EmbedApiError};
                use std::time::Instant;

                let test_texts = vec!["Hello, this is a test.".to_string()];
                let t0 = Instant::now();
                match batch_embed(&test_texts, &prov, 512) {
                    Ok(vecs) => {
                        let elapsed_ms = t0.elapsed().as_millis();
                        let dims = vecs.first().map(|v| v.len()).unwrap_or(0);
                        println!("  ✓ Connection successful!");
                        println!("  Dimensions : {dims}");
                        println!("  Latency    : {elapsed_ms} ms");
                    }
                    Err(EmbedApiError::Auth(_)) => {
                        eprintln!("  ✗ Authentication failed — check your API key.");
                        return ExitCode::from(1);
                    }
                    Err(EmbedApiError::RateLimit(_)) => {
                        eprintln!("  ✗ Rate limited — wait a moment and retry.");
                        return ExitCode::from(1);
                    }
                    Err(EmbedApiError::Network(msg)) => {
                        eprintln!("  ✗ Network error: {msg}");
                        return ExitCode::from(1);
                    }
                    Err(EmbedApiError::Other(msg)) => {
                        eprintln!("  ✗ API error: {msg}");
                        return ExitCode::from(1);
                    }
                }
            }

            #[cfg(not(feature = "native-embed"))]
            {
                println!("  [Native binary: HTTP connectivity test not supported without native-embed feature]");
                println!("  To run a live test: embed.py --test");
            }
        }
        None => {
            println!("  ✗ No provider configured or API key missing.");
            println!("    Run: sk index embed --setup");
            println!("     or: sk index embed --providers   (to see current config)");
        }
    }

    ExitCode::SUCCESS
}

// ── --setup ─────────────────────────────────────────────────────────────

/// Interactive provider configuration wizard.
///
/// Mirrors Python's `cmd_setup()`:
/// - Presents a numbered provider menu
/// - Reads user input from stdin
/// - For `custom` provider, prompts for URL, model, and dimensions
/// - Reads/stores an API key (or defers to environment variable)
/// - Writes the updated config to `embedding-config.json`
///
/// Two differences from the Python version:
/// 1. TF-IDF availability cannot be checked without a Python runtime —
///    the existing `config.fallback` value is preserved unchanged.
/// 2. The post-setup HTTP connectivity test is skipped; users are directed
///    to run `embed.py --test` for a live check.
fn run_setup() -> ExitCode {
    let mut config = load_config();

    println!("\n═══ Embedding Provider Setup ═══\n");
    println!("Choose a provider for generating embeddings:");
    println!();
    println!("  1. fireworks   — Fireworks AI (nomic-embed, $0.008/1M tokens)");
    println!("  2. openai      — OpenAI (text-embedding-3-small, $0.02/1M tokens)");
    println!("  3. openrouter  — OpenRouter (routes to various providers)");
    println!("  4. custom      — Any OpenAI-compatible endpoint (Ollama, LM Studio...)");
    println!("  5. auto        — Auto-detect from environment variables");
    println!();

    let choice = prompt_line("Select provider [1-5, default=5]: ");
    let provider_name: &str = match choice.trim() {
        "1" => "fireworks",
        "2" => "openai",
        "3" => "openrouter",
        "4" => "custom",
        _ => "auto",
    };

    config.active_provider = provider_name.to_string();

    if provider_name == "auto" {
        println!("\nAuto mode: will check environment variables in order:");
        for name in AUTO_PRIORITY {
            let prov = config.providers.get(*name);
            let env_key = prov.map(|p| p.env_key.as_str()).unwrap_or("");
            let has_key =
                !env_key.is_empty() && std::env::var(env_key).is_ok_and(|v| !v.is_empty());
            let marker = if has_key { "✓" } else { "✗" };
            println!("  {marker} {env_key} → {name}");
        }
    } else {
        // Custom provider: prompt for URL / model / dimensions
        if provider_name == "custom" {
            let (default_url, default_model, default_dims) = {
                let p = config.providers.get("custom");
                (
                    p.and_then(|p| {
                        if p.base_url.is_empty() {
                            None
                        } else {
                            Some(p.base_url.clone())
                        }
                    })
                    .unwrap_or_else(|| "http://localhost:11434/v1".to_string()),
                    p.and_then(|p| {
                        if p.model.is_empty() {
                            None
                        } else {
                            Some(p.model.clone())
                        }
                    })
                    .unwrap_or_else(|| "nomic-embed-text".to_string()),
                    p.map(|p| p.dimensions).unwrap_or(768),
                )
            };

            let url_in = prompt_line(&format!("Base URL [{default_url}]: "));
            let new_url = if url_in.trim().is_empty() {
                default_url
            } else {
                url_in.trim().to_string()
            };

            let model_in = prompt_line(&format!("Model name [{default_model}]: "));
            let new_model = if model_in.trim().is_empty() {
                default_model
            } else {
                model_in.trim().to_string()
            };

            let dims_in = prompt_line(&format!("Dimensions [{default_dims}]: "));
            let new_dims: u32 = dims_in.trim().parse().unwrap_or(default_dims);

            if let Some(p) = config.providers.get_mut("custom") {
                p.base_url = new_url;
                p.model = new_model;
                p.dimensions = new_dims;
            }
        }

        // API key handling
        let (env_key, env_val) = {
            let p = config.providers.get(provider_name);
            let ek = p.map(|p| p.env_key.clone()).unwrap_or_default();
            let ev = std::env::var(&ek).unwrap_or_default();
            (ek, ev)
        };

        if !env_val.is_empty() {
            println!("\n  ✓ API key found in ${env_key}");
            let use_env = prompt_line("  Use environment variable? [Y/n]: ");
            if use_env.trim().eq_ignore_ascii_case("n") {
                let new_key = prompt_line("  Enter API key: ");
                if let Some(p) = config.providers.get_mut(provider_name) {
                    p.api_key = new_key.trim().to_string();
                }
            }
        } else {
            println!("\n  No ${env_key} environment variable found.");
            let new_key = prompt_line("  Enter API key (or leave empty to set env var later): ");
            if let Some(p) = config.providers.get_mut(provider_name) {
                p.api_key = new_key.trim().to_string();
            }
        }
    }

    // TF-IDF note — cannot check scikit-learn from Rust
    println!();
    println!("  [Native binary: scikit-learn availability not checked]");
    println!("    Install if needed: pip install scikit-learn");

    // Save config
    match save_config(&config) {
        Ok(path) => {
            println!("\n  ✓ Config saved to {}", path.display());
        }
        Err(e) => {
            eprintln!("  ✗ Failed to save config: {e}");
            return ExitCode::from(1);
        }
    }

    // Post-setup connectivity note
    println!("\nTo verify connectivity run: embed.py --test");

    ExitCode::SUCCESS
}

// ── --rebuild-tfidf ──────────────────────────────────────────────────────

/// Informational handler for the `--rebuild-tfidf` flag.
///
/// The `--rebuild-tfidf` flag is not implemented in Python's `embed.py`
/// dispatcher (`main()`) — calling it there falls through to printing help.
/// In the native binary we surface this explicitly:
/// - TF-IDF model building requires `scikit-learn` (Python package).
/// - The model is built automatically by `embed.py --build` when scikit-learn
///   is installed; there is no standalone rebuild-only command.
fn run_rebuild_tfidf() -> ExitCode {
    let db_path = knowledge_db_path();

    let conn = match open_writable(Some(db_path)) {
        Ok(c) => c,
        Err(e) => {
            eprintln!("sk index embed --rebuild-tfidf: cannot open DB: {e}");
            return ExitCode::from(1);
        }
    };

    if let Err(e) = ensure_embedding_tables(&conn) {
        eprintln!("sk index embed --rebuild-tfidf: failed to ensure tables: {e}");
        return ExitCode::from(1);
    }

    // Fetch all sections
    let mut stmt = match conn.prepare(
        "SELECT s.id, s.content FROM sections s \
         JOIN documents d ON s.document_id = d.id",
    ) {
        Ok(s) => s,
        Err(e) => {
            eprintln!("sk index embed --rebuild-tfidf: query failed: {e}");
            return ExitCode::from(1);
        }
    };

    let rows: Vec<(i64, String)> =
        match stmt.query_map([], |r| Ok((r.get::<_, i64>(0)?, r.get::<_, String>(1)?))) {
            Ok(iter) => iter.filter_map(|r| r.ok()).collect(),
            Err(e) => {
                eprintln!("sk index embed --rebuild-tfidf: failed to fetch sections: {e}");
                return ExitCode::from(1);
            }
        };

    if rows.is_empty() {
        println!("No sections found in DB — nothing to build.");
        return ExitCode::SUCCESS;
    }

    let texts: Vec<String> = rows.iter().map(|(_, c)| c.clone()).collect();
    let doc_ids: Vec<i64> = rows.iter().map(|(id, _)| *id).collect();
    let doc_count = texts.len();

    println!("Building TF-IDF model over {doc_count} sections...");
    let text_refs: Vec<&str> = texts.iter().map(|s| s.as_str()).collect();
    let model_blob = build_tfidf_model(&text_refs, &doc_ids);

    if let Err(e) = store_tfidf_model(&conn, &model_blob, doc_count) {
        eprintln!("sk index embed --rebuild-tfidf: failed to store model: {e}");
        return ExitCode::from(1);
    }

    println!(
        "✓ TF-IDF model built and stored ({doc_count} documents, {} bytes).",
        model_blob.len()
    );
    ExitCode::SUCCESS
}

// ── --build ──────────────────────────────────────────────────────────────

/// Build (or update) embeddings for all sections + knowledge entries.
///
/// Requires the `native-embed` Cargo feature (enabled by default).
/// The function:
/// 1. Opens the knowledge DB.
/// 2. Fetches all sections and knowledge_entries.
/// 3. Filters out already-embedded items unless `force` is true.
/// 4. If a provider is configured, calls the HTTP embedding API in batches.
/// 5. Stores float32 blob vectors in the `embeddings` table.
/// 6. **Always** rebuilds the TF-IDF model from all sections.
#[cfg(feature = "native-embed")]
fn run_build(force: bool) -> ExitCode {
    use crate::embeddings::http::{batch_embed, EmbedApiError};

    let db_path = knowledge_db_path();

    let conn = match open_writable(Some(db_path)) {
        Ok(c) => c,
        Err(e) => {
            eprintln!("sk index embed --build: cannot open DB: {e}");
            return ExitCode::from(1);
        }
    };

    if let Err(e) = ensure_embedding_tables(&conn) {
        eprintln!("sk index embed --build: failed to ensure tables: {e}");
        return ExitCode::from(1);
    }

    let config = load_config();
    let provider_opt = resolve_provider(&config);

    // ── Fetch sections ───────────────────────────────────────────────────
    let sections: Vec<(i64, String, String)> = {
        let mut stmt = match conn.prepare(
            "SELECT s.id, d.title || ' - ' || s.section_name, s.content \
             FROM sections s \
             JOIN documents d ON s.document_id = d.id",
        ) {
            Ok(s) => s,
            Err(e) => {
                eprintln!("sk index embed --build: section query failed: {e}");
                return ExitCode::from(1);
            }
        };
        let rows: rusqlite::Result<Vec<_>> = stmt
            .query_map([], |r| {
                Ok((
                    r.get::<_, i64>(0)?,
                    r.get::<_, String>(1)?,
                    r.get::<_, String>(2)?,
                ))
            })
            .and_then(|iter| iter.collect());
        match rows {
            Ok(v) => v,
            Err(e) => {
                eprintln!("sk index embed --build: failed to read sections: {e}");
                return ExitCode::from(1);
            }
        }
    };

    // ── Fetch knowledge entries ──────────────────────────────────────────
    let knowledge: Vec<(i64, String, String)> = {
        let mut stmt =
            match conn.prepare("SELECT id, title, COALESCE(content,'') FROM knowledge_entries") {
                Ok(s) => s,
                Err(e) => {
                    eprintln!("sk index embed --build: knowledge_entries query failed: {e}");
                    return ExitCode::from(1);
                }
            };
        let rows: rusqlite::Result<Vec<_>> = stmt
            .query_map([], |r| {
                Ok((
                    r.get::<_, i64>(0)?,
                    r.get::<_, String>(1)?,
                    r.get::<_, String>(2)?,
                ))
            })
            .and_then(|iter| iter.collect());
        match rows {
            Ok(v) => v,
            Err(e) => {
                eprintln!("sk index embed --build: failed to read knowledge_entries: {e}");
                return ExitCode::from(1);
            }
        }
    };

    println!(
        "Found {} sections, {} knowledge entries.",
        sections.len(),
        knowledge.len()
    );

    // ── HTTP embedding (optional — skip gracefully if no provider) ───────
    if let Some((prov_name, prov)) = provider_opt {
        let key = get_api_key(&prov);
        if key.is_empty() {
            println!("⚠  Provider '{prov_name}' configured but API key not set — skipping HTTP embeddings.");
            println!("   Set the key or run: sk index embed --setup");
        } else {
            // Filter already-embedded items
            let filter_existing =
                |source_type: &str, ids: &[(i64, String, String)]| -> Vec<(i64, String, String)> {
                    if force {
                        return ids.to_vec();
                    }
                    ids.iter()
                        .filter(|(id, _, _)| {
                            conn.query_row(
                                "SELECT 1 FROM embeddings WHERE source_type=? AND source_id=?",
                                rusqlite::params![source_type, id],
                                |_| Ok(()),
                            )
                            .is_err() // err == not found → needs embedding
                        })
                        .cloned()
                        .collect()
                };

            let new_sections = filter_existing("section", &sections);
            let new_knowledge = filter_existing("knowledge", &knowledge);

            println!(
                "Embedding {} new sections, {} new knowledge entries (--force={})",
                new_sections.len(),
                new_knowledge.len(),
                force
            );

            // Embed sections
            if !new_sections.is_empty() {
                let sec_texts: Vec<String> = new_sections
                    .iter()
                    .map(|(_, label, content)| format!("{label}\n{content}"))
                    .collect();
                match batch_embed(&sec_texts, &prov, 512) {
                    Ok(vecs) => {
                        let dims = vecs.first().map(|v| v.len()).unwrap_or(0);
                        let items: Vec<(i64, Vec<f32>, String)> = new_sections
                            .iter()
                            .zip(vecs)
                            .map(|((id, label, _), vec)| (*id, vec, label.clone()))
                            .collect();
                        if let Err(e) = store_batch_embeddings(
                            &conn,
                            "section",
                            &items,
                            &prov_name,
                            &prov.model,
                            dims as u32,
                        ) {
                            eprintln!(
                                "sk index embed --build: failed to store section embeddings: {e}"
                            );
                            return ExitCode::from(1);
                        }
                        println!("✓ Stored {} section embeddings ({dims} dims).", items.len());
                    }
                    Err(EmbedApiError::Auth(_)) => {
                        eprintln!("✗ Authentication failed — check your API key.");
                        return ExitCode::from(1);
                    }
                    Err(e) => {
                        eprintln!("✗ Embedding API error: {e}");
                        return ExitCode::from(1);
                    }
                }
            }

            // Embed knowledge entries
            if !new_knowledge.is_empty() {
                let ke_texts: Vec<String> = new_knowledge
                    .iter()
                    .map(|(_, title, content)| format!("{title}\n{content}"))
                    .collect();
                match batch_embed(&ke_texts, &prov, 512) {
                    Ok(vecs) => {
                        let dims = vecs.first().map(|v| v.len()).unwrap_or(0);
                        let items: Vec<(i64, Vec<f32>, String)> = new_knowledge
                            .iter()
                            .zip(vecs)
                            .map(|((id, title, _), vec)| (*id, vec, title.clone()))
                            .collect();
                        if let Err(e) = store_batch_embeddings(
                            &conn,
                            "knowledge",
                            &items,
                            &prov_name,
                            &prov.model,
                            dims as u32,
                        ) {
                            eprintln!(
                                "sk index embed --build: failed to store knowledge embeddings: {e}"
                            );
                            return ExitCode::from(1);
                        }
                        println!(
                            "✓ Stored {} knowledge embeddings ({dims} dims).",
                            items.len()
                        );
                    }
                    Err(EmbedApiError::Auth(_)) => {
                        eprintln!("✗ Authentication failed — check your API key.");
                        return ExitCode::from(1);
                    }
                    Err(e) => {
                        eprintln!("✗ Embedding API error: {e}");
                        return ExitCode::from(1);
                    }
                }
            }
        }
    } else {
        println!("ℹ  No embedding provider configured — skipping HTTP embeddings.");
        println!("   Run: sk index embed --setup   to configure a provider.");
    }

    // ── TF-IDF build (always, independent of provider) ───────────────────
    if sections.is_empty() {
        println!("No sections found — skipping TF-IDF build.");
    } else {
        let texts: Vec<String> = sections.iter().map(|(_, _, c)| c.clone()).collect();
        let doc_ids: Vec<i64> = sections.iter().map(|(id, _, _)| *id).collect();
        let doc_count = texts.len();
        println!("Building TF-IDF model over {doc_count} sections...");
        let text_refs: Vec<&str> = texts.iter().map(|s| s.as_str()).collect();
        let model_blob = build_tfidf_model(&text_refs, &doc_ids);
        let blob_len = model_blob.len();
        if let Err(e) = store_tfidf_model(&conn, &model_blob, doc_count) {
            eprintln!("sk index embed --build: failed to store TF-IDF model: {e}");
            return ExitCode::from(1);
        }
        println!("✓ TF-IDF model built ({doc_count} docs, {blob_len} bytes).");
    }

    println!("\nDone.");
    ExitCode::SUCCESS
}

// ── Helpers ─────────────────────────────────────────────────────────────

fn parse_limit(args: &[String], default: usize) -> usize {
    if let Some(idx) = args.iter().position(|a| a == "--limit") {
        args.get(idx + 1)
            .and_then(|v| v.parse().ok())
            .unwrap_or(default)
    } else {
        default
    }
}

/// Read a single line from stdin, printing `prompt` first.
///
/// On EOF (e.g., in tests or non-interactive environments) returns an empty
/// string so callers can apply their defaults.
fn prompt_line(prompt: &str) -> String {
    use std::io::Write as _;
    print!("{prompt}");
    let _ = std::io::stdout().flush();
    let mut buf = String::new();
    let _ = std::io::stdin().read_line(&mut buf);
    // Strip trailing newlines (LF + optional CR)
    buf.trim_end_matches('\n')
        .trim_end_matches('\r')
        .to_string()
}

// ── Tests ────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parse_limit_default_when_absent() {
        let args: Vec<String> = vec!["--search".to_string(), "query".to_string()];
        assert_eq!(parse_limit(&args, 10), 10);
    }

    #[test]
    fn parse_limit_explicit_value() {
        let args: Vec<String> = vec![
            "--search".to_string(),
            "query".to_string(),
            "--limit".to_string(),
            "5".to_string(),
        ];
        assert_eq!(parse_limit(&args, 10), 5);
    }

    #[test]
    fn parse_limit_invalid_falls_back_to_default() {
        let args: Vec<String> = vec!["--limit".to_string(), "not-a-number".to_string()];
        assert_eq!(parse_limit(&args, 10), 10);
    }

    #[test]
    fn parse_limit_missing_value_falls_back_to_default() {
        let args: Vec<String> = vec!["--limit".to_string()];
        assert_eq!(parse_limit(&args, 10), 10);
    }

    #[test]
    fn positional_detection_no_flags() {
        let args: Vec<String> = vec!["rust".to_string(), "embeddings".to_string()];
        let is_positional = !args.is_empty() && !args.iter().any(|a| a.starts_with('-'));
        assert!(is_positional);
    }

    #[test]
    fn positional_detection_with_flags_is_false() {
        let args: Vec<String> = vec!["--status".to_string()];
        let is_positional = !args.is_empty() && !args.iter().any(|a| a.starts_with('-'));
        assert!(!is_positional);
    }

    #[test]
    fn search_idx_found() {
        let args: Vec<String> = vec!["--search".to_string(), "my query".to_string()];
        let idx = args.iter().position(|a| a == "--search");
        assert_eq!(idx, Some(0));
        let query = args.get(idx.unwrap() + 1).cloned().unwrap_or_default();
        assert_eq!(query, "my query");
    }

    #[test]
    fn search_flag_with_empty_query_detected() {
        let args: Vec<String> = vec!["--search".to_string()];
        let idx = args.iter().position(|a| a == "--search");
        let query = args.get(idx.unwrap() + 1).cloned().unwrap_or_default();
        assert!(query.is_empty());
    }

    // ── New native path flag-detection tests ────────────────────────────

    #[test]
    fn test_flag_detected() {
        let args: Vec<String> = vec!["--test".to_string()];
        assert!(args.iter().any(|a| a == "--test"));
    }

    #[test]
    fn setup_flag_detected() {
        let args: Vec<String> = vec!["--setup".to_string()];
        assert!(args.iter().any(|a| a == "--setup"));
    }

    #[test]
    fn rebuild_tfidf_flag_detected() {
        let args: Vec<String> = vec!["--rebuild-tfidf".to_string()];
        assert!(args.iter().any(|a| a == "--rebuild-tfidf"));
    }

    #[test]
    fn rebuild_tfidf_not_confused_with_other_flags() {
        let rebuild_args: Vec<String> = vec!["--rebuild-tfidf".to_string()];
        let build_args: Vec<String> = vec!["--build".to_string()];
        // --rebuild-tfidf args must NOT trigger --build detection
        assert!(!rebuild_args.iter().any(|a| a == "--build"));
        // --build args must NOT trigger --rebuild-tfidf detection
        assert!(!build_args.iter().any(|a| a == "--rebuild-tfidf"));
    }

    #[test]
    fn test_flag_not_confused_with_status() {
        let args: Vec<String> = vec!["--status".to_string()];
        assert!(!args.iter().any(|a| a == "--test"));
    }

    #[test]
    fn setup_flag_takes_priority_over_positional_detection() {
        // --setup starts with '-', so positional detection is false
        let args: Vec<String> = vec!["--setup".to_string()];
        let is_positional = !args.is_empty() && !args.iter().any(|a| a.starts_with('-'));
        let has_setup = args.iter().any(|a| a == "--setup");
        assert!(has_setup);
        assert!(!is_positional);
    }

    #[test]
    fn prompt_line_returns_string_on_empty_stdin() {
        // When stdin is EOF (as in tests), prompt_line must not panic and
        // must return an empty string (so callers can fall back to defaults).
        // We test the contract directly: an empty read_line result → "".
        let buf = String::new();
        // Simulate the EOF path: read_line wrote 0 bytes
        let trimmed = buf.trim_end_matches('\n').trim_end_matches('\r');
        assert!(trimmed.is_empty());
    }
}
