"""Hook rules registry.

Each rule handles specific events and optionally filters by tool name.
The hook_runner dispatches to matching rules in registration order.
"""


class Rule:
    """Base class for hook rules."""

    name = ""
    events = []   # e.g., ["preToolUse"]
    tools = []    # e.g., ["edit", "create", "bash"]. Empty = all tools

    def evaluate(self, event, data):
        """Evaluate this rule. Returns dict with decision or None to pass."""
        return None


def get_rules_for_event(event):
    """Import and return rules matching the given event type."""
    from .briefing import AutoBriefingRule, EnforceBriefingRule
    from .learn_gate import EnforceLearnRule
    from .learn_reminder import LearnReminderRule
    from .tentacle import TentacleEnforceRule, TentacleSuggestRule
    from .edit_tracker import TrackEditsRule, TestReminderRule
    from .error_kb import ErrorKBRule
    from .integrity import IntegrityRule
    from .session_lifecycle import SessionEndRule
    from .subagent_guard import SubagentGitGuardRule

    ALL_RULES = [
        # sessionStart (order: briefing first, then integrity)
        AutoBriefingRule(),
        IntegrityRule(),
        # preToolUse (order matters — first deny wins)
        EnforceBriefingRule(),
        EnforceLearnRule(),
        TentacleEnforceRule(),
        SubagentGitGuardRule(),
        # postToolUse (all run, output is informational)
        TrackEditsRule(),
        LearnReminderRule(),
        TestReminderRule(),
        TentacleSuggestRule(),
        # errorOccurred
        ErrorKBRule(),
        # sessionEnd
        SessionEndRule(),
    ]

    return [r for r in ALL_RULES if event in r.events]
