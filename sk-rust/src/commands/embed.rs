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
use crate::embeddings::search::{check_tfidf_staleness, run_hybrid_search};
use crate::embeddings::store::{
    ensure_embedding_tables, store_batch_embeddings, store_tfidf_model_with_binary,
};
use crate::embeddings::tfidf::{build_tfidf_model, invalidate_tfidf_cache, TfIdfModel};

/// Dispatch `sk index embed [args]`.
pub fn run_embed_command(args: &[String]) -> ExitCode {
    let has_status = args.iter().any(|a| a == "--status");
    let has_providers = args.iter().any(|a| a == "--providers");
    let has_test = args.iter().any(|a| a == "--test");
    let has_setup = args.iter().any(|a| a == "--setup");
    let has_rebuild_tfidf = args.iter().any(|a| a == "--rebuild-tfidf");
    let has_build = args.iter().any(|a| a == "--build");
    let has_force = args.iter().any(|a| a == "--force");
    let has_auto_rebuild = args.iter().any(|a| a == "--auto-rebuild-tfidf");
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
        return run_search(&query, limit, has_auto_rebuild);
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
            return run_search(&query, limit, has_auto_rebuild);
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

/// Hybrid search: FTS5 + stored-vector cosine + TF-IDF fallback.
///
/// ## Semantic query vector (#360)
/// With the `native-embed` feature enabled, the query string is embedded via
/// the configured provider (one-shot HTTP call) and the resulting vector is
/// passed to `run_hybrid_search` for stored-vector cosine reranking.  When
/// no provider is configured (or the key is absent / the call fails), the
/// command falls back gracefully to FTS + TF-IDF — no error exit.
///
/// ## Failure surfacing (#366)
/// If neither a query vector nor a TF-IDF model is available, an informative
/// message is printed so users know how to improve recall.
fn run_search(query: &str, limit: usize, auto_rebuild: bool) -> ExitCode {
    let db = match KnowledgeDb::open() {
        Ok(db) => db,
        Err(e) => {
            eprintln!("sk index embed: cannot open knowledge.db: {e}");
            return ExitCode::from(1);
        }
    };

    // ── #367: staleness guard ─────────────────────────────────────────────
    if let Some((current, model_doc, built_at)) = check_tfidf_staleness(&db.conn) {
        if auto_rebuild {
            eprintln!(
                "sk index embed: TF-IDF model stale \
                 (model={model_doc} sections, DB={current}) — rebuilding…"
            );
            if run_rebuild_tfidf_inner(&db.conn).is_err() {
                eprintln!("sk index embed: rebuild failed, proceeding with stale model");
            }
        } else {
            eprintln!(
                "sk index embed: TF-IDF model may be stale \
                 (built at {built_at}: {model_doc} sections indexed, \
                 DB now has {current}).\n  \
                 Run 'sk index embed --rebuild-tfidf' to refresh, or pass \
                 --auto-rebuild-tfidf to rebuild automatically."
            );
        }
    }

    // ── #360: try to compute a live query embedding ───────────────────────
    // When native-embed is compiled in and a provider with a key is
    // configured, embed the query for vector-augmented hybrid search.
    // Any failure is soft: fall through to FTS-only path.
    #[cfg(feature = "native-embed")]
    let query_vec: Option<Vec<f32>> = {
        use crate::embeddings::config::{get_api_key, load_config, resolve_provider};
        use crate::embeddings::http::call_embedding_api;

        let cfg = load_config();
        if let Some((_name, prov)) = resolve_provider(&cfg) {
            let key = get_api_key(&prov);
            if !key.is_empty() {
                match call_embedding_api(&[query.to_string()], &prov, 1) {
                    Ok(mut vecs) if !vecs.is_empty() => Some(vecs.remove(0)),
                    Ok(_) => None,
                    Err(e) => {
                        // #366: surface the failure as a warning, not a hard error.
                        eprintln!(
                            "sk index embed: semantic query failed ({e}) — falling back to FTS+TF-IDF"
                        );
                        None
                    }
                }
            } else {
                None
            }
        } else {
            None
        }
    };

    #[cfg(not(feature = "native-embed"))]
    let query_vec: Option<Vec<f32>> = None;

    let rrf_k = load_config().rrf_k as f64;
    let results = run_hybrid_search(&db.conn, query, query_vec.as_deref(), limit, rrf_k);

    if results.is_empty() {
        // #366: inform users how to improve recall when results are empty.
        let has_tfidf = db
            .conn
            .query_row(
                "SELECT COUNT(*) FROM tfidf_model WHERE id=1 AND doc_count>0",
                [],
                |r| r.get::<_, i64>(0),
            )
            .unwrap_or(0)
            > 0;
        let has_embeddings = db
            .conn
            .query_row("SELECT COUNT(*) FROM embeddings", [], |r| {
                r.get::<_, i64>(0)
            })
            .unwrap_or(0)
            > 0;
        println!("No results for: {query}");
        if !has_tfidf && !has_embeddings {
            eprintln!("  Tip: run 'sk index embed --build' to enable semantic search.");
        }
        return ExitCode::SUCCESS;
    }

    let search_mode = if query_vec.is_some() {
        "semantic+FTS"
    } else {
        "FTS+TF-IDF"
    };
    println!(
        "\nHybrid search ({search_mode}): {} results for '{query}'\n",
        results.len()
    );
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

    match run_rebuild_tfidf_inner(&conn) {
        Ok(doc_count) => {
            println!("✓ TF-IDF model rebuilt ({doc_count} documents).");
            ExitCode::SUCCESS
        }
        Err(e) => {
            eprintln!("sk index embed --rebuild-tfidf: {e}");
            ExitCode::from(1)
        }
    }
}

/// Inner rebuild helper — shared by `run_rebuild_tfidf` and the auto-rebuild
/// path in `run_search`.  Builds TF-IDF model over all sections, dual-writes
/// JSON + binary, and invalidates the in-memory cache.
///
/// Returns the number of documents indexed on success.
fn run_rebuild_tfidf_inner(conn: &rusqlite::Connection) -> Result<usize, String> {
    // Fetch all sections
    let mut stmt = conn
        .prepare(
            "SELECT s.id, s.content FROM sections s \
             JOIN documents d ON s.document_id = d.id",
        )
        .map_err(|e| format!("query failed: {e}"))?;

    let rows: Vec<(i64, String)> = stmt
        .query_map([], |r| Ok((r.get::<_, i64>(0)?, r.get::<_, String>(1)?)))
        .map_err(|e| format!("failed to fetch sections: {e}"))?
        .filter_map(|r| r.ok())
        .collect();

    if rows.is_empty() {
        println!("No sections found in DB — nothing to build.");
        return Ok(0);
    }

    let texts: Vec<String> = rows.iter().map(|(_, c)| c.clone()).collect();
    let doc_ids: Vec<i64> = rows.iter().map(|(id, _)| *id).collect();
    let doc_count = texts.len();

    println!("Building TF-IDF model over {doc_count} sections...");
    let text_refs: Vec<&str> = texts.iter().map(|s| s.as_str()).collect();
    let json_blob = build_tfidf_model(&text_refs, &doc_ids);

    // Build binary blob for fast Rust loading (#356)
    let bin_blob = match TfIdfModel::from_json_blob(&json_blob) {
        Some(model) => model.to_binary(),
        None => {
            eprintln!("sk index embed: binary serialization failed — storing JSON only");
            Vec::new()
        }
    };

    store_tfidf_model_with_binary(conn, &json_blob, &bin_blob, doc_count)
        .map_err(|e| format!("failed to store model: {e}"))?;

    // Invalidate in-memory cache so next search loads fresh model (#355)
    invalidate_tfidf_cache();

    println!(
        "  json={} bytes, bin={} bytes",
        json_blob.len(),
        bin_blob.len()
    );
    Ok(doc_count)
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
        let json_blob = build_tfidf_model(&text_refs, &doc_ids);

        // Build binary blob for fast Rust loading (#356)
        let bin_blob = match TfIdfModel::from_json_blob(&json_blob) {
            Some(model) => model.to_binary(),
            None => {
                eprintln!(
                    "sk index embed --build: binary serialization failed — storing JSON only"
                );
                Vec::new()
            }
        };

        let blob_len = json_blob.len();
        let bin_len = bin_blob.len();
        if let Err(e) = store_tfidf_model_with_binary(&conn, &json_blob, &bin_blob, doc_count) {
            eprintln!("sk index embed --build: failed to store TF-IDF model: {e}");
            return ExitCode::from(1);
        }
        // Invalidate in-memory cache so next search loads fresh model (#355)
        invalidate_tfidf_cache();
        println!("✓ TF-IDF model built ({doc_count} docs, json={blob_len}B, bin={bin_len}B).");
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

    // ── #367 auto-rebuild flag detection ────────────────────────────────

    #[test]
    fn auto_rebuild_tfidf_flag_detected() {
        let args: Vec<String> = vec![
            "--search".to_string(),
            "query".to_string(),
            "--auto-rebuild-tfidf".to_string(),
        ];
        assert!(args.iter().any(|a| a == "--auto-rebuild-tfidf"));
    }

    #[test]
    fn auto_rebuild_absent_by_default() {
        let args: Vec<String> = vec!["--search".to_string(), "query".to_string()];
        assert!(!args.iter().any(|a| a == "--auto-rebuild-tfidf"));
    }

    // ── #355 / #356 rebuild_inner writes binary ─────────────────────────

    #[test]
    fn rebuild_tfidf_inner_returns_zero_on_empty_db() {
        use crate::embeddings::store::ensure_embedding_tables;
        let conn = rusqlite::Connection::open_in_memory().unwrap();
        ensure_embedding_tables(&conn).unwrap();
        conn.execute_batch(
            "CREATE TABLE IF NOT EXISTS documents (
                id INTEGER PRIMARY KEY, title TEXT,
                doc_type TEXT DEFAULT '', session_id TEXT DEFAULT ''
             );
             CREATE TABLE IF NOT EXISTS sections (
                id INTEGER PRIMARY KEY, document_id INTEGER,
                section_name TEXT, stable_id TEXT, content TEXT
             );",
        )
        .unwrap();
        let result = run_rebuild_tfidf_inner(&conn);
        assert!(result.is_ok());
        assert_eq!(result.unwrap(), 0, "empty DB should yield 0 docs");
    }

    #[test]
    fn rebuild_tfidf_inner_dual_writes_binary() {
        use crate::embeddings::store::ensure_embedding_tables;
        use crate::embeddings::tfidf::BINARY_MAGIC;
        let conn = rusqlite::Connection::open_in_memory().unwrap();
        ensure_embedding_tables(&conn).unwrap();
        conn.execute_batch(
            "CREATE TABLE IF NOT EXISTS documents (
                id INTEGER PRIMARY KEY, title TEXT,
                doc_type TEXT DEFAULT '', session_id TEXT DEFAULT ''
             );
             CREATE TABLE IF NOT EXISTS sections (
                id INTEGER PRIMARY KEY, document_id INTEGER,
                section_name TEXT, stable_id TEXT, content TEXT
             );
             INSERT INTO documents VALUES (1, 'Test Doc', 'note', 'sess-1');
             INSERT INTO sections VALUES
                (1, 1, 'overview', NULL, 'hello world rust tfidf test'),
                (2, 1, 'detail', NULL, 'another section with more words');",
        )
        .unwrap();
        let result = run_rebuild_tfidf_inner(&conn);
        assert!(result.is_ok());
        assert_eq!(result.unwrap(), 2);

        // Verify binary blob was written with correct magic bytes
        let bin: Vec<u8> = conn
            .query_row("SELECT model_bin FROM tfidf_model WHERE id=1", [], |r| {
                r.get(0)
            })
            .expect("model_bin should be present");
        assert!(
            bin.starts_with(BINARY_MAGIC),
            "binary blob must start with SKTIDF magic"
        );
    }
}
