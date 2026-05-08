use std::process::{Command, ExitCode};

use crate::config::{python_exe, resolve_tools_dir};

/// Spawn python(3) with the given script filename and extra args.
/// The script is resolved relative to the tools directory.
/// Returns the exit code of the spawned process.
pub fn run_fallback(script: &str, extra_args: &[String]) -> ExitCode {
    let tools_dir = resolve_tools_dir();
    let script_path = tools_dir.join(script);

    if !script_path.exists() {
        eprintln!(
            "sk: script not found: {}\n\
             Hint: set SK_TOOLS_DIR to the copilot-session-knowledge checkout.",
            script_path.display()
        );
        return ExitCode::from(2);
    }

    let python = python_exe();
    let status = Command::new(python)
        .arg(&script_path)
        .args(extra_args)
        .status();

    match status {
        Ok(s) => {
            let code = s.code().unwrap_or(1) as u8;
            ExitCode::from(code)
        }
        Err(e) => {
            eprintln!("sk: failed to spawn {python}: {e}");
            ExitCode::from(1)
        }
    }
}
