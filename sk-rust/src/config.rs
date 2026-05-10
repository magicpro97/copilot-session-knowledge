use std::path::PathBuf;

/// Resolve the user's home directory, honoring explicit environment overrides
/// before falling back to the OS-specific home lookup.
pub fn resolve_home_dir() -> Option<PathBuf> {
    for key in ["HOME", "USERPROFILE"] {
        if let Ok(value) = std::env::var(key) {
            if !value.trim().is_empty() {
                return Some(PathBuf::from(value));
            }
        }
    }

    match (std::env::var("HOMEDRIVE"), std::env::var("HOMEPATH")) {
        (Ok(drive), Ok(path)) if !drive.trim().is_empty() && !path.trim().is_empty() => {
            return Some(PathBuf::from(format!("{drive}{path}")));
        }
        _ => {}
    }

    dirs::home_dir()
}

/// Resolve the tools directory: SK_TOOLS_DIR env var takes precedence,
/// otherwise falls back to ~/.copilot/tools
pub fn resolve_tools_dir() -> PathBuf {
    if let Ok(override_dir) = std::env::var("SK_TOOLS_DIR") {
        let p = PathBuf::from(override_dir);
        if p.exists() {
            return p;
        }
    }
    resolve_home_dir()
        .expect("cannot determine home directory")
        .join(".copilot")
        .join("tools")
}

/// Resolve the ~/.copilot directory.
pub fn resolve_copilot_dir() -> PathBuf {
    resolve_home_dir()
        .expect("cannot determine home directory")
        .join(".copilot")
}

/// On Windows use `python`, on Unix use `python3`
pub fn python_exe() -> &'static str {
    if cfg!(target_os = "windows") {
        "python"
    } else {
        "python3"
    }
}
