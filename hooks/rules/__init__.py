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
    from .auto_bug_detector import AutoBugDetectorRule
    from .block_edit_dist import BlockEditDistRule
    from .block_unsafe_html import BlockUnsafeHtmlRule
    from .briefing import AutoBriefingRule, EnforceBriefingRule
    from .confidence_gate import ConfidenceGateRule
    from .constitution_gate import ConstitutionGateRule
    from .edit_tracker import TestReminderRule, TrackEditsRule
    from .episode_batcher import EpisodeBatcherRule
    from .error_kb import ErrorFixNudgeRule, ErrorKBRule
    from .file_size_advisory import FileSizeAdvisoryRule
    from .integrity import IntegrityRule
    from .learn_gate import EnforceLearnRule
    from .learn_reminder import LearnReminderRule
    from .loop_detector import LoopDetectorRule
    from .new_file_advisory import NewFileAdvisoryRule
    from .nextjs_typecheck import NextjsTypecheckRule
    from .pnpm_lockfile_guard import PnpmLockfileGuardRule
    from .read_before_edit import ReadBeforeEditRule
    from .read_tracker import ReadTrackerRule
    from .recurrence_detector import RecurrenceDetectorRule
    from .session_compiler import SessionCompilerRule
    from .session_lifecycle import SessionEndRule, SubagentStopRule
    from .skill_improvement_advisor import SkillImprovementAdvisorRule
    from .skill_nudge import SkillNudgeRule
    from .skill_usage import SkillUsageRule
    from .subagent_guard import SubagentGitGuardRule
    from .syntax_gate import SyntaxGateRule
    from .tentacle import TentacleEnforceRule, TentacleSuggestRule
    from .token_tracker import TokenTrackerRule
    from .user_prompt_audit import UserPromptAuditRule
    from .verification_gate import VerificationGateRule

    ALL_RULES = [
        # sessionStart (order: briefing first, then integrity)
        AutoBriefingRule(),
        IntegrityRule(),
        # preToolUse (order matters — first deny wins)
        EnforceBriefingRule(),
        EnforceLearnRule(),
        TentacleEnforceRule(),
        SubagentGitGuardRule(),
        ConfidenceGateRule(),
        ConstitutionGateRule(),
        SyntaxGateRule(),
        BlockEditDistRule(),
        PnpmLockfileGuardRule(),
        BlockUnsafeHtmlRule(),
        VerificationGateRule(),
        FileSizeAdvisoryRule(),
        NewFileAdvisoryRule(),
        ReadBeforeEditRule(),
        ReadTrackerRule(),  # Issue #85: warn on repeat reads (preToolUse)
        LoopDetectorRule(),  # Issue #663: detect repeated identical tool calls
        # postToolUse (all run, output is informational)
        TrackEditsRule(),
        LearnReminderRule(),
        TestReminderRule(),
        AutoBugDetectorRule(),  # Issue #86: five-category bug-fix pattern detector
        TentacleSuggestRule(),
        NextjsTypecheckRule(),
        ReadBeforeEditRule(),  # also postToolUse for tracking views
        SkillNudgeRule(),  # Issue #116: skill-creation nudge after threshold tool calls
        SkillUsageRule(),  # Issue #119: event-level skill usage tracking
        TokenTrackerRule(),  # Issue #84: token usage tracking (postToolUse)
        EpisodeBatcherRule(),  # Issue #394: batch episode auto-learn (opt-in)
        # VerificationGateRule also handles postToolUse (already registered above)
        # errorOccurred
        ErrorKBRule(),
        # postToolUse — error-fix nudge (WBS-024)
        ErrorFixNudgeRule(),
        # sessionEnd
        SessionEndRule(),
        RecurrenceDetectorRule(),
        SessionCompilerRule(),  # Issue #395: session compiler (opt-in)
        SkillImprovementAdvisorRule(),  # Skill improvement queue (sessionEnd)
        # agentStop / subagentStop
        SubagentStopRule(),
        # userPromptSubmitted (WBS-025)
        UserPromptAuditRule(),
    ]

    return [r for r in ALL_RULES if event in r.events]
