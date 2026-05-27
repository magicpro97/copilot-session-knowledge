mod browse;
mod commands;
mod config;
mod daemon;
mod db;
mod embeddings;
mod hooks;
mod index;
mod providers;
mod redact;
mod sync;

use std::process::ExitCode;
use std::time::Instant;

use clap::{Parser, Subcommand};

use commands::fallback::run_fallback;

const VERSION: &str = "1.2.0";

#[derive(Parser)]
#[command(
    name = "sk",
    bin_name = "sk",
    about = "Unified front-door CLI for copilot-session-knowledge tools",
    version = VERSION,
    propagate_version = true
)]
struct Cli {
    #[command(subcommand)]
    command: Option<Commands>,

    /// Print startup time (for benchmarking)
    #[arg(long, hide = true)]
    time: bool,
}

#[derive(Subcommand)]
enum Commands {
    /// Run session briefing
    Briefing {
        #[arg(trailing_var_arg = true, allow_hyphen_values = true)]
        args: Vec<String>,
    },
    /// Record a lesson (mistake / pattern / decision / discovery)
    Learn {
        #[arg(trailing_var_arg = true, allow_hyphen_values = true)]
        args: Vec<String>,
    },
    /// Query session knowledge
    Query {
        #[arg(trailing_var_arg = true, allow_hyphen_values = true)]
        args: Vec<String>,
    },
    /// Manage tentacles (orchestration)
    Tentacle {
        #[arg(trailing_var_arg = true, allow_hyphen_values = true)]
        args: Vec<String>,
    },
    /// Install or update sk and tools
    Install {
        #[arg(trailing_var_arg = true, allow_hyphen_values = true)]
        args: Vec<String>,
    },
    /// Set up a project for session-knowledge
    Setup {
        #[arg(trailing_var_arg = true, allow_hyphen_values = true)]
        args: Vec<String>,
    },
    /// Auto-update tools from upstream
    Update {
        #[arg(trailing_var_arg = true, allow_hyphen_values = true)]
        args: Vec<String>,
    },
    /// Launch the browse UI server
    Browse {
        #[arg(trailing_var_arg = true, allow_hyphen_values = true)]
        args: Vec<String>,
    },
    /// Benchmark tools performance
    Benchmark {
        #[arg(trailing_var_arg = true, allow_hyphen_values = true)]
        args: Vec<String>,
    },
    /// Generate a retrospective
    Retro {
        #[arg(trailing_var_arg = true, allow_hyphen_values = true)]
        args: Vec<String>,
    },
    /// Heal / repair the knowledge database
    Heal {
        #[arg(trailing_var_arg = true, allow_hyphen_values = true)]
        args: Vec<String>,
    },
    /// Manage session index (build / extract / migrate / status / health / embed)
    Index {
        #[arg(trailing_var_arg = true, allow_hyphen_values = true)]
        args: Vec<String>,
    },
    /// Manage sync (run / config / status / gateway / merge)
    Sync {
        #[arg(trailing_var_arg = true, allow_hyphen_values = true)]
        args: Vec<String>,
    },
    /// Manage checkpoints (save / restore / diff)
    Checkpoint {
        #[arg(trailing_var_arg = true, allow_hyphen_values = true)]
        args: Vec<String>,
    },
    /// Manage profiles (build / import / export)
    Profile {
        #[arg(trailing_var_arg = true, allow_hyphen_values = true)]
        args: Vec<String>,
    },
    /// Generate project / codebase context (project / map)
    Context {
        #[arg(trailing_var_arg = true, allow_hyphen_values = true)]
        args: Vec<String>,
    },
    /// Trend scout (run / config / status)
    Scout {
        #[arg(trailing_var_arg = true, allow_hyphen_values = true)]
        args: Vec<String>,
    },
    /// Manage Copilot CLI hooks (run / list)
    Hooks {
        #[arg(trailing_var_arg = true, allow_hyphen_values = true)]
        args: Vec<String>,
    },
    /// Watch sessions for real-time indexing
    Watch {
        #[arg(trailing_var_arg = true, allow_hyphen_values = true)]
        args: Vec<String>,
    },
    /// Manage the project registry (add / remove / list)
    Project {
        #[arg(trailing_var_arg = true, allow_hyphen_values = true)]
        args: Vec<String>,
    },
    /// Suggest new skills from knowledge DB patterns (suggestion-only)
    #[command(name = "skill-suggest")]
    SkillSuggest {
        #[arg(trailing_var_arg = true, allow_hyphen_values = true)]
        args: Vec<String>,
    },
    /// Apply a targeted patch to a SKILL.md file
    #[command(name = "skill-patch")]
    SkillPatch {
        #[arg(trailing_var_arg = true, allow_hyphen_values = true)]
        args: Vec<String>,
    },
    /// Audit hook effectiveness from audit.jsonl
    #[command(name = "audit-hooks")]
    AuditHooks {
        #[arg(trailing_var_arg = true, allow_hyphen_values = true)]
        args: Vec<String>,
    },
    /// Query audit log for learn/briefing/hook events and latency
    #[command(name = "audit-log")]
    AuditLog {
        #[arg(trailing_var_arg = true, allow_hyphen_values = true)]
        args: Vec<String>,
    },
}

// Map a grouped namespace command (e.g. "index build") to a Python script name.
fn resolve_group(group: &str, args: &[String]) -> (String, Vec<String>) {
    let (subcommand, rest) = if args.is_empty() {
        (None, vec![])
    } else {
        (Some(args[0].as_str()), args[1..].to_vec())
    };

    let script = match (group, subcommand) {
        ("index", Some("build")) => "build-session-index.py",
        ("index", Some("extract")) => "extract-knowledge.py",
        ("index", Some("migrate")) => "migrate.py",
        ("index", Some("status")) => "index-status.py",
        ("index", Some("health")) => "knowledge-health.py",
        ("index", Some("embed")) => "embed.py",
        ("sync", Some("run")) => "sync-daemon.py",
        ("sync", Some("config")) => "sync-config.py",
        ("sync", Some("status")) => "sync-status.py",
        ("sync", Some("gateway")) => "sync-gateway.py",
        ("sync", Some("merge")) => "sync-knowledge.py",
        ("checkpoint", Some("save")) => "checkpoint-save.py",
        ("checkpoint", Some("restore")) => "checkpoint-restore.py",
        ("checkpoint", Some("diff")) => "checkpoint-diff.py",
        ("profile", Some("build")) => "profile-builder.py",
        ("profile", Some("import")) => "profile-import.py",
        ("profile", Some("export")) => "profile-export.py",
        ("context", Some("project")) => "project-context.py",
        ("context", Some("map")) => "codebase-map.py",
        ("scout", Some("run")) => "trend-scout.py",
        ("scout", Some("config")) => "scout-config.py",
        ("scout", Some("status")) => "scout-status.py",
        _ => {
            // Pass through: let Python handle unknown subcommands with full args
            return (format!("{group}.py"), args.to_vec());
        }
    };

    (script.to_string(), rest)
}

fn main() -> ExitCode {
    if std::env::var("SK_WRITER_BROKER_DAEMON")
        .map(|v| v == "1")
        .unwrap_or(false)
    {
        return db::writer_broker::run_broker_daemon();
    }

    let start = Instant::now();

    let cli = Cli::parse();

    let exit_code = match cli.command {
        None => {
            // No subcommand — print help
            let _ = Cli::parse_from(["sk", "--help"]);
            ExitCode::SUCCESS
        }

        Some(Commands::Briefing { args }) => commands::briefing::run_briefing_command(&args),
        Some(Commands::Learn { args }) => commands::learn::run_learn_command(&args),
        Some(Commands::Query { args }) => commands::query::run_query_command(&args),
        Some(Commands::Tentacle { args }) => run_fallback("tentacle.py", &args),
        Some(Commands::Install { args }) => run_fallback("install.py", &args),
        Some(Commands::Setup { args }) => run_fallback("setup-project.py", &args),
        Some(Commands::Update { args }) => run_fallback("auto-update-tools.py", &args),
        Some(Commands::Browse { args }) => run_fallback("browse.py", &args),
        Some(Commands::Benchmark { args }) => run_fallback("benchmark.py", &args),
        Some(Commands::Retro { args }) => run_fallback("retro.py", &args),
        Some(Commands::Heal { args }) => run_fallback("copilot-cli-healer.py", &args),

        Some(Commands::Index { args }) => {
            // Native handlers for `sk index embed`, `sk index status`, `sk index health`.
            // All other index subcommands fall through to the Python script resolver.
            match args.first().map(|s| s.as_str()) {
                Some("embed") => {
                    let rest = args.get(1..).unwrap_or(&[]).to_vec();
                    commands::embed::run_embed_command(&rest)
                }
                Some("status") => {
                    let rest = args.get(1..).unwrap_or(&[]).to_vec();
                    commands::index_native::run_index_status_command(&rest)
                }
                Some("health") => {
                    let rest = args.get(1..).unwrap_or(&[]).to_vec();
                    commands::index_native::run_index_health_command(&rest)
                }
                _ => {
                    let (script, rest) = resolve_group("index", &args);
                    run_fallback(&script, &rest)
                }
            }
        }
        Some(Commands::Sync { args }) => {
            // Native handlers for `sk sync run` and `sk sync status`.
            // All other sync subcommands fall through to the Python script resolver.
            match args.first().map(|s| s.as_str()) {
                Some("run") => {
                    let rest = args.get(1..).unwrap_or(&[]).to_vec();
                    commands::sync_run::run_sync_run_command(&rest)
                }
                Some("status") => {
                    let rest = args.get(1..).unwrap_or(&[]).to_vec();
                    commands::sync_native::run_sync_status_command(&rest)
                }
                _ => {
                    let (script, rest) = resolve_group("sync", &args);
                    run_fallback(&script, &rest)
                }
            }
        }
        Some(Commands::Checkpoint { args }) => {
            let (script, rest) = resolve_group("checkpoint", &args);
            run_fallback(&script, &rest)
        }
        Some(Commands::Profile { args }) => {
            let (script, rest) = resolve_group("profile", &args);
            run_fallback(&script, &rest)
        }
        Some(Commands::Context { args }) => {
            let (script, rest) = resolve_group("context", &args);
            run_fallback(&script, &rest)
        }
        Some(Commands::Scout { args }) => {
            let (script, rest) = resolve_group("scout", &args);
            run_fallback(&script, &rest)
        }
        Some(Commands::Hooks { args }) => commands::hooks::run_hooks_command(&args),
        Some(Commands::Watch { args }) => commands::watch::run_watch_command(&args),
        Some(Commands::Project { args }) => {
            // Validate subcommand before forwarding to project-registry.py so
            // that bad subcommands fail fast with a consistent message even if
            // the Python script is missing or not invocable — matching the
            // behaviour of the Python sk.py _run_project() dispatcher.
            if let Some(exit_code) = commands::project::run_project_command(&args) {
                exit_code
            } else {
                const VALID_SUBS: &[&str] = &["add", "remove", "list"];
                let sub = args.first().map(|s| s.as_str());
                match sub {
                    // No subcommand or explicit help flag → show help
                    None | Some("-h") | Some("--help") => {
                        run_fallback("project-registry.py", &["--help".to_string()])
                    }
                    Some(s) if VALID_SUBS.contains(&s) => {
                        run_fallback("project-registry.py", &args)
                    }
                    Some(bad) => {
                        eprintln!(
                            "sk project: unknown subcommand '{}'. Choose from: {}",
                            bad,
                            VALID_SUBS.join(", ")
                        );
                        ExitCode::from(2)
                    }
                }
            }
        }
        Some(Commands::SkillSuggest { args }) => run_fallback("skill-suggest.py", &args),
        Some(Commands::SkillPatch { args }) => run_fallback("skill-patch.py", &args),
        Some(Commands::AuditHooks { args }) => run_fallback("audit-hooks.py", &args),
        Some(Commands::AuditLog { args }) => commands::audit_log::run_audit_log_command(&args),
    };

    if cli.time {
        eprintln!("sk startup: {:?}", start.elapsed());
    }

    exit_code
}
