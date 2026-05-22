//! Rust port of `browse/core/operator_actions.py`.
//!
//! Operator actions are read-only, copy-safe diagnostic command suggestions
//! shown in the browse UI settings page. They are NEVER browser-executed.
//!
//! # Contract invariants
//! - `safe` is always `true` — enforced at construction.
//! - `command` must be non-empty (after trimming).
//! - Optional context fields (`requires_configured_gateway`,
//!   `requires_configured_target`) are route-specific; omit when not relevant.

use std::fmt;

use serde::Serialize;

/// A validated, read-only operator action.
///
/// JSON output omits optional keys when `None`.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct OperatorAction {
    pub id: String,
    pub title: String,
    pub description: String,
    pub command: String,
    /// Always `true` — enforced by [`make_action`].
    pub safe: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub requires_configured_gateway: Option<bool>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub requires_configured_target: Option<bool>,
}

/// Input struct for [`make_action`].
///
/// Using a named struct avoids long positional argument lists while
/// requiring zero additional dependencies.
pub struct MakeAction {
    pub id: String,
    pub title: String,
    pub description: String,
    pub command: String,
    pub requires_configured_gateway: Option<bool>,
    pub requires_configured_target: Option<bool>,
}

/// Errors returned by [`make_action`].
#[derive(Debug, PartialEq, Eq)]
pub enum OperatorActionError {
    /// `command` was empty or whitespace-only.
    EmptyCommand { id: String },
    /// Reserved for parity with the Python `safe=False` guard; not reachable
    /// from the normal constructor path.
    UnsafeAction { id: String },
}

impl fmt::Display for OperatorActionError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::EmptyCommand { id } => {
                write!(f, "operator_action '{id}': command must not be empty.")
            }
            Self::UnsafeAction { id } => write!(
                f,
                "operator_action '{id}': safe must be true — operator actions must never be write operations."
            ),
        }
    }
}

impl std::error::Error for OperatorActionError {}

/// Build a validated [`OperatorAction`].
///
/// # Errors
/// - [`OperatorActionError::EmptyCommand`] if `input.command` is empty or
///   contains only whitespace.
pub fn make_action(input: MakeAction) -> Result<OperatorAction, OperatorActionError> {
    if input.command.trim().is_empty() {
        return Err(OperatorActionError::EmptyCommand { id: input.id });
    }
    Ok(OperatorAction {
        id: input.id,
        title: input.title,
        description: input.description,
        command: input.command,
        safe: true,
        requires_configured_gateway: input.requires_configured_gateway,
        requires_configured_target: input.requires_configured_target,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn minimal() -> MakeAction {
        MakeAction {
            id: "test-action".to_string(),
            title: "Test Action".to_string(),
            description: "A test action.".to_string(),
            command: "python3 test.py".to_string(),
            requires_configured_gateway: None,
            requires_configured_target: None,
        }
    }

    #[test]
    fn make_action_minimal_serializes_without_optional_keys() {
        let action = make_action(minimal()).unwrap();
        assert!(action.safe);
        let json: serde_json::Value = serde_json::to_value(&action).unwrap();
        let obj = json.as_object().unwrap();
        assert_eq!(
            obj.keys()
                .cloned()
                .collect::<std::collections::BTreeSet<_>>(),
            ["id", "title", "description", "command", "safe"]
                .iter()
                .map(|s| s.to_string())
                .collect()
        );
        assert_eq!(json["safe"], true);
    }

    #[test]
    fn make_action_with_gateway_serializes_gateway_only() {
        let action = make_action(MakeAction {
            requires_configured_gateway: Some(true),
            ..minimal()
        })
        .unwrap();
        let json: serde_json::Value = serde_json::to_value(&action).unwrap();
        assert_eq!(json["requires_configured_gateway"], true);
        assert!(json.get("requires_configured_target").is_none());
    }

    #[test]
    fn make_action_with_target_serializes_target_only() {
        let action = make_action(MakeAction {
            requires_configured_target: Some(true),
            ..minimal()
        })
        .unwrap();
        let json: serde_json::Value = serde_json::to_value(&action).unwrap();
        assert_eq!(json["requires_configured_target"], true);
        assert!(json.get("requires_configured_gateway").is_none());
    }

    #[test]
    fn make_action_with_both_serializes_both() {
        let action = make_action(MakeAction {
            requires_configured_gateway: Some(false),
            requires_configured_target: Some(true),
            ..minimal()
        })
        .unwrap();
        let json: serde_json::Value = serde_json::to_value(&action).unwrap();
        assert_eq!(json["requires_configured_gateway"], false);
        assert_eq!(json["requires_configured_target"], true);
    }

    #[test]
    fn make_action_rejects_empty_command() {
        let err = make_action(MakeAction {
            command: "".to_string(),
            ..minimal()
        })
        .unwrap_err();
        assert_eq!(
            err,
            OperatorActionError::EmptyCommand {
                id: "test-action".to_string()
            }
        );
    }

    #[test]
    fn make_action_rejects_whitespace_command() {
        let err = make_action(MakeAction {
            command: "   ".to_string(),
            ..minimal()
        })
        .unwrap_err();
        assert_eq!(
            err,
            OperatorActionError::EmptyCommand {
                id: "test-action".to_string()
            }
        );
    }

    /// Reproduces the `trend-scout-search-only` action from
    /// `browse/routes/scout.py` and asserts the JSON output matches the
    /// Python contract shape exactly.
    #[test]
    fn parity_with_python_scout_action() {
        let action = make_action(MakeAction {
            id: "trend-scout-search-only".to_string(),
            title: "Discovery-only search pass".to_string(),
            description: "Read-only candidate discovery + shortlist without issue writes."
                .to_string(),
            command: "python3 trend-scout.py --search-only".to_string(),
            requires_configured_gateway: None,
            requires_configured_target: Some(true),
        })
        .unwrap();

        let json: serde_json::Value = serde_json::to_value(&action).unwrap();
        assert_eq!(json["id"], "trend-scout-search-only");
        assert_eq!(json["title"], "Discovery-only search pass");
        assert_eq!(
            json["description"],
            "Read-only candidate discovery + shortlist without issue writes."
        );
        assert_eq!(json["command"], "python3 trend-scout.py --search-only");
        assert_eq!(json["safe"], true);
        assert_eq!(json["requires_configured_target"], true);
        // gateway must NOT appear
        assert!(json.get("requires_configured_gateway").is_none());

        // Confirm exact key set matches Python contract
        let obj = json.as_object().unwrap();
        assert_eq!(
            obj.keys()
                .cloned()
                .collect::<std::collections::BTreeSet<_>>(),
            [
                "id",
                "title",
                "description",
                "command",
                "safe",
                "requires_configured_target"
            ]
            .iter()
            .map(|s| s.to_string())
            .collect()
        );
    }
}
