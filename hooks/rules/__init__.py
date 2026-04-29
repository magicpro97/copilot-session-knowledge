"""Hook rules registry.

Each rule handles specific events and optionally filters by tool name.
The hook_runner dispatches to matching rules in registration order.
"""


class Rule:
    """Base class for hook rules."""

    name = ""
    events = []  # e.g., ["preToolUse"]
    tools = []  # e.g., ["edit", "create", "bash"]. Empty = all tools

    def evaluate(self, event, data):
        """Evaluate this rule. Returns dict with decision or None to pass."""
        return None


def get_rules_for_event(event):
    """Import and return rules matching the given event type."""
    from .block_edit_dist import BlockEditDistRule
    from .block_unsafe_html import BlockUnsafeHtmlRule
    from .briefing import AutoBriefingRule, EnforceBriefingRule
    from .edit_tracker import TestReminderRule, TrackEditsRule
    from .error_kb import ErrorKBRule
    from .integrity import IntegrityRule
    from .learn_gate import EnforceLearnRule
    from .learn_reminder import LearnReminderRule
    from .nextjs_typecheck import NextjsTypecheckRule
    from .pnpm_lockfile_guard import PnpmLockfileGuardRule
    from .session_lifecycle import SessionEndRule, SubagentStopRule
    from .subagent_guard import SubagentGitGuardRule
    from .syntax_gate import SyntaxGateRule
    from .tentacle import TentacleEnforceRule, TentacleSuggestRule

    ALL_RULES = [
        # sessionStart (order: briefing first, then integrity)
        AutoBriefingRule(),
        IntegrityRule(),
        # preToolUse (order matters — first deny wins)
        EnforceBriefingRule(),
        EnforceLearnRule(),
        TentacleEnforceRule(),
        SubagentGitGuardRule(),
        SyntaxGateRule(),
        BlockEditDistRule(),
        PnpmLockfileGuardRule(),
        BlockUnsafeHtmlRule(),
        # postToolUse (all run, output is informational)
        TrackEditsRule(),
        LearnReminderRule(),
        TestReminderRule(),
        TentacleSuggestRule(),
        NextjsTypecheckRule(),
        # errorOccurred
        ErrorKBRule(),
        # sessionEnd
        SessionEndRule(),
        # agentStop / subagentStop
        SubagentStopRule(),
    ]

    return [r for r in ALL_RULES if event in r.events]
