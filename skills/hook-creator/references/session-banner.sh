#!/bin/bash
# session-banner.sh — TEMPLATE
#
# postToolUse hook that displays a session start banner.
# Customize the checklist items for your project's workflow.
#
# This is a "chatStart" hook — runs once at session beginning.
# Shows mandatory steps the AI must follow before starting work.
cat << 'EOF'

  ╔══════════════════════════════════════════════════════════════╗
  ║  PROJECT NAME — SESSION START CHECKLIST                      ║
  ╠══════════════════════════════════════════════════════════════╣
  ║                                                              ║
  ║  STOP. Before ANY work, complete these steps IN ORDER:       ║
  ║                                                              ║
  ║  □ 1. Run: python3 ~/.copilot/tools/briefing.py "<task>"    ║
  ║  □ 2. Read: WORKFLOW.md or project docs                      ║
  ║  □ 3. Read: AGENTS.md (if multi-agent project)               ║
  ║  □ 4. THEN analyze code                                      ║
  ║                                                              ║
  ║  Architecture rules enforced by hooks.                       ║
  ║  Use session-knowledge to learn from past mistakes.          ║
  ╚══════════════════════════════════════════════════════════════╝

EOF
exit 0
