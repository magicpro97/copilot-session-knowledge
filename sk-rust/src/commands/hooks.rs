/// Native hook runner / compatibility wrapper for Copilot CLI hook events.
///
/// Supported forms:
/// - `sk hooks <event>`              → native runner (for direct testing/dev)
/// - `sk hooks run <event>`          → compatibility path used by managed hooks
/// - `sk hooks list`                 → print supported events
///
/// Managed `hooks.json` invokes `sk hooks run <event>`.  As of wave13, all
/// events including `preToolUse` are routed natively for Rust-binary installs:
///   - `agentStop`/`subagentStop`: `AgentStopRule` calls
///     `tentacle.py marker-cleanup --from-stop-event` as a stable subprocess
///     boundary (wave3).
///   - `sessionEnd`: `SessionEndRule` cleans per-session markers via
///     `COPILOT_AGENT_SESSION_ID` and writes `session.log` (wave4).
///     `RecurrenceDetectorRule` updates the `recurrence_after_briefing` counter
///     in knowledge.db (wave9; informational side-effect only).
///   - `errorOccurred`: `ErrorOccurredRule` spawns `query-session.py` for
///     KB search (wave4); no HMAC involved.
///   - `sessionStart`: `AutoBriefingRule` spawns `briefing.py` and signs HMAC
///     markers; `IntegrityRule` checks the SHA256 manifest (wave9).
///   - `postToolUse`: all eight informational rules fully ported natively
///     (`TrackEditsRule`, `LearnReminderRule`, `TestReminderRule`,
///     `NextjsTypecheckReminderRule`, `VerificationGatePostRule`,
///     `ReadBeforeEditRule`, `TentacleSuggestRule`); `sync_markers.rs`
///     writes `sync-nudge.json` after rule dispatch (wave10).
///     `SkillUsageRule` added in wave28 (issue #119) — records triggered /
///     loaded / skipped events to `skill-metrics.db` for the `skill` tool.
///   - `preToolUse`: all enforcement rules now ported natively (wave13):
///     `EnforceBriefingRule`, `EnforceLearnRule`, `TentacleEnforceRule`,
///     `SubagentGitGuardRule`, `SyntaxGateRule`, `BlockEditDistRule`,
///     `PnpmLockfileGuardRule`, `BlockUnsafeHtmlRule`, `VerificationGatePreRule`,
///     `ReadBeforeEditRule`.  `SyntaxGateRule` uses a Python subprocess boundary
///     via `python_exe()` + `py_compile` (no HMAC required).
///
/// Python `sk.py` shim and non-Rust installs continue to fall back to
/// `hook_runner.py` for ALL events regardless of `NATIVE_EVENTS` — the Python
/// managed path is unchanged and remains required for those install types.
///
/// Routing table for `sk hooks run <event>`:
///   agentStop, subagentStop          → native Rust path (run_hook)  [wave3]
///   sessionEnd, errorOccurred        → native Rust path (run_hook)  [wave4]
///   sessionStart                     → native Rust path (run_hook)  [wave9]
///   postToolUse                      → native Rust path (run_hook)  [wave10]
///   preToolUse                       → native Rust path (run_hook)  [wave13]
use std::process::ExitCode;

use crate::commands::fallback::run_fallback;
use crate::hooks::runner::run_hook;

const SUPPORTED_EVENTS: &[&str] = &[
    "sessionStart",
    "sessionEnd",
    "preToolUse",
    "postToolUse",
    "agentStop",
    "subagentStop",
    "errorOccurred",
];

/// Events that the native Rust runner owns completely (no Python fallback needed).
///
/// These events are routed to `run_hook()` by `sk hooks run` instead of
/// `hook_runner.py`.  The invariant for inclusion:
///   - All meaningful work for the event is either implemented natively in Rust
///     OR is delegated to a stable Python subprocess boundary.
///
/// wave3 additions: agentStop, subagentStop
///   AgentStopRule → `tentacle.py marker-cleanup --from-stop-event` subprocess.
///
/// wave4 additions: sessionEnd, errorOccurred
///   SessionEndRule → per-session marker cleanup via COPILOT_AGENT_SESSION_ID
///                    + session.log write (pure filesystem, no HMAC).
///   ErrorOccurredRule → `query-session.py <query>` subprocess (no HMAC).
///
/// wave9 addition: sessionStart
///   AutoBriefingRule → spawns `briefing.py` via `python_exe()` + signs
///     HMAC markers via `marker_auth::sign_marker`.
///   IntegrityRule → reads/writes SHA256 manifest at
///     `~/.copilot/hooks/integrity-manifest.json`; no HMAC enforcement.
///
/// wave10 addition: postToolUse
///   All seven postToolUse rules are informational-only and fully ported:
///     TrackEditsRule, LearnReminderRule, TestReminderRule,
///     NextjsTypecheckReminderRule, VerificationGatePostRule,
///     ReadBeforeEditRule, TentacleSuggestRule.
///   `sync_markers::record_sync_signal` writes `sync-nudge.json` after
///   rule dispatch (already called in runner.rs for postToolUse).
///
/// wave13 addition: preToolUse
///   All preToolUse enforcement rules now ported natively:
///     EnforceBriefingRule, EnforceLearnRule, TentacleEnforceRule,
///     SubagentGitGuardRule, SyntaxGateRule (Python subprocess via python_exe()),
///     BlockEditDistRule, PnpmLockfileGuardRule, BlockUnsafeHtmlRule,
///     VerificationGatePreRule, ReadBeforeEditRule.
///   `SyntaxGateRule` uses `py_compile` via Python subprocess — no HMAC required.
///   Python `sk.py` shim and non-Rust installs continue to fall back to
///   `hook_runner.py` for all events regardless of this list.
const NATIVE_EVENTS: &[&str] = &[
    "agentStop",
    "subagentStop",
    "sessionEnd",
    "errorOccurred",
    "sessionStart", // wave9: AutoBriefingRule + IntegrityRule
    "postToolUse", // wave10: all postToolUse rules natively ported; sync-nudge.json via sync_markers
    "preToolUse", // wave13: all preToolUse rules natively ported; SyntaxGateRule via Python subprocess
];

pub fn run_hooks_command(args: &[String]) -> ExitCode {
    match args {
        [] => {
            eprintln!("Usage: sk hooks run <event> | sk hooks <event> | sk hooks list");
            ExitCode::from(2)
        }
        [cmd] if cmd == "list" => {
            for event in SUPPORTED_EVENTS {
                println!("{event}");
            }
            ExitCode::SUCCESS
        }
        [cmd] if cmd == "run" => {
            eprintln!("sk hooks run: missing event name");
            ExitCode::from(2)
        }
        [cmd, event, rest @ ..] if cmd == "run" => {
            if !rest.is_empty() {
                eprintln!("sk hooks run: unexpected extra arguments");
                return ExitCode::from(2);
            }
            if event.is_empty() {
                eprintln!("sk hooks run: missing event name");
                return ExitCode::from(2);
            }
            if NATIVE_EVENTS.contains(&event.as_str()) {
                // Native path: Rust handles everything for this event
                // (AgentStopRule calls tentacle.py marker-cleanup --from-stop-event
                // as a stable subprocess boundary for marker cleanup;
                // postToolUse rules are all informational and fully ported in wave10;
                // preToolUse rules are all ported natively in wave13, with
                // SyntaxGateRule using a Python subprocess for py_compile checks).
                run_hook(event);
                ExitCode::SUCCESS
            } else {
                // Python fallback: Python sk.py shim and non-Rust installs.
                run_fallback("hooks/hook_runner.py", std::slice::from_ref(event))
            }
        }
        [event, ..] => {
            run_hook(event);
            ExitCode::SUCCESS
        }
    }
}
