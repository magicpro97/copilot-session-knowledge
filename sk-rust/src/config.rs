use std::path::PathBuf;

/// Resolve the tools directory: SK_TOOLS_DIR env var takes precedence,
/// otherwise falls back to ~/.copilot/tools
pub fn resolve_tools_dir() -> PathBuf {
    if let Ok(override_dir) = std::env::var("SK_TOOLS_DIR") {
        let p = PathBuf::from(override_dir);
        if p.exists() {
            return p;
        }
    }
    dirs::home_dir()
        .expect("cannot determine home directory")
        .join(".copilot")
        .join("tools")
}

/// On Windows use `python`, on Unix use `python3`
pub fn python_exe() -> &'static str {
    if cfg!(target_os = "windows") {
        "python"
    } else {
        "python3"
    }
}
