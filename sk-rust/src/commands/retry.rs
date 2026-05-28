//! `sk retry` operator surface for issue #614 queue telemetry.

use std::fs;
use std::process::ExitCode;

use crate::hooks::rules::retry::queue::{queue_path, read_records, state_key_path, RetryRecord};

struct RetryArgs {
    subcommand: String,
    json: bool,
    agent: Option<String>,
}

fn parse_args(args: &[String]) -> Result<RetryArgs, String> {
    let subcommand = args.first().cloned().unwrap_or_else(|| "list".to_string());
    let mut json = false;
    let mut agent = None;
    let mut i = if matches!(subcommand.as_str(), "list" | "clear" | "status") {
        1
    } else {
        0
    };

    if matches!(subcommand.as_str(), "-h" | "--help") {
        print_help();
        std::process::exit(0);
    }

    if !matches!(subcommand.as_str(), "list" | "clear" | "status") {
        return Err(format!("unknown subcommand: {subcommand}"));
    }

    while i < args.len() {
        match args[i].as_str() {
            "--json" => {
                json = true;
                i += 1;
            }
            "--agent" => {
                agent = Some(
                    args.get(i + 1)
                        .cloned()
                        .ok_or_else(|| "--agent requires a value".to_string())?,
                );
                i += 2;
            }
            "-h" | "--help" => {
                print_help();
                std::process::exit(0);
            }
            other => return Err(format!("unknown flag: {other}")),
        }
    }

    Ok(RetryArgs {
        subcommand,
        json,
        agent,
    })
}

fn print_help() {
    println!("Usage: sk retry [list|status|clear] [--agent <name>] [--json]");
    println!("       sk retry list --json");
}

pub fn run_retry_command(args: &[String]) -> ExitCode {
    let parsed = match parse_args(args) {
        Ok(p) => p,
        Err(e) => {
            eprintln!("sk retry: {e}");
            return ExitCode::from(2);
        }
    };

    match parsed.subcommand.as_str() {
        "list" => run_list(&parsed),
        "status" => run_status(&parsed),
        "clear" => run_clear(),
        _ => ExitCode::from(2),
    }
}

fn load_verified_records() -> Result<(Vec<RetryRecord>, usize), String> {
    let key = match fs::read_to_string(state_key_path()) {
        Ok(k) => k,
        Err(err) if err.kind() == std::io::ErrorKind::NotFound => return Ok((Vec::new(), 0)),
        Err(err) => return Err(format!("could not read retry state key: {err}")),
    };
    read_records(key.trim()).map_err(|err| format!("could not read retry queue: {err}"))
}

fn filter_records(records: Vec<RetryRecord>, agent: Option<&str>) -> Vec<RetryRecord> {
    match agent {
        Some(agent) => records.into_iter().filter(|r| r.agent == agent).collect(),
        None => records,
    }
}

fn run_list(parsed: &RetryArgs) -> ExitCode {
    let (records, skipped) = match load_verified_records() {
        Ok(v) => v,
        Err(e) => {
            eprintln!("sk retry: {e}");
            return ExitCode::from(1);
        }
    };
    let records = filter_records(records, parsed.agent.as_deref());

    if parsed.json {
        for rec in records {
            match serde_json::to_string(&rec) {
                Ok(line) => println!("{line}"),
                Err(err) => eprintln!("sk retry: could not encode record: {err}"),
            }
        }
        if skipped > 0 {
            eprintln!("sk retry: skipped {skipped} tampered record(s)");
        }
        return ExitCode::SUCCESS;
    }

    if records.is_empty() {
        println!("No retry records.");
    } else {
        println!(
            "{:<20} {:<12} {:<7} {:<10} {:<12} {:<12} pattern",
            "ts", "agent", "attempt", "outcome", "delay", "source"
        );
        for rec in records {
            println!(
                "{:<20} {:<12} {:<7} {:<10} {:<12.2} {:<12} {}",
                rec.ts,
                rec.agent,
                rec.attempt,
                rec.outcome,
                rec.computed_delay_seconds,
                rec.delay_source,
                rec.detected_pattern
            );
        }
    }
    if skipped > 0 {
        eprintln!("sk retry: skipped {skipped} tampered record(s)");
    }
    ExitCode::SUCCESS
}

fn run_status(parsed: &RetryArgs) -> ExitCode {
    let (records, skipped) = match load_verified_records() {
        Ok(v) => v,
        Err(e) => {
            eprintln!("sk retry: {e}");
            return ExitCode::from(1);
        }
    };
    let records = filter_records(records, parsed.agent.as_deref());
    let queued = records.iter().filter(|r| r.outcome == "queued").count();
    let stopped = records.iter().filter(|r| r.outcome == "stopped").count();
    if parsed.json {
        println!(
            "{}",
            serde_json::json!({
                "records": records.len(),
                "queued": queued,
                "stopped": stopped,
                "skipped_tampered": skipped,
                "queue_path": queue_path().display().to_string(),
            })
        );
    } else {
        println!(
            "retry records: {} queued={} stopped={} skipped_tampered={}",
            records.len(),
            queued,
            stopped,
            skipped
        );
    }
    ExitCode::SUCCESS
}

fn run_clear() -> ExitCode {
    match fs::remove_file(queue_path()) {
        Ok(()) => {
            println!("Retry queue cleared.");
            ExitCode::SUCCESS
        }
        Err(err) if err.kind() == std::io::ErrorKind::NotFound => {
            println!("Retry queue already empty.");
            ExitCode::SUCCESS
        }
        Err(err) => {
            eprintln!("sk retry: could not clear queue: {err}");
            ExitCode::from(1)
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parse_defaults_to_list() {
        let parsed = parse_args(&[]).unwrap();
        assert_eq!(parsed.subcommand, "list");
        assert!(!parsed.json);
    }

    #[test]
    fn parse_list_json_agent() {
        let parsed = parse_args(&[
            "list".to_string(),
            "--json".to_string(),
            "--agent".to_string(),
            "copilot".to_string(),
        ])
        .unwrap();
        assert_eq!(parsed.subcommand, "list");
        assert!(parsed.json);
        assert_eq!(parsed.agent.as_deref(), Some("copilot"));
    }

    #[test]
    fn parse_rejects_unknown_subcommand() {
        assert!(parse_args(&["wat".to_string()]).is_err());
    }
}
