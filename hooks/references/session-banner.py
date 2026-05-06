#!/usr/bin/env python3
"""postToolUse template: print a project session checklist banner."""

import os
import sys

if os.name == "nt":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

print(
    """
  PROJECT NAME - SESSION START CHECKLIST
  ------------------------------------------------------------
  STOP. Before ANY work, complete these steps IN ORDER:

  [ ] 1. Run: python3 ~/.copilot/tools/briefing.py "<task>"
  [ ] 2. Read: WORKFLOW.md or project docs
  [ ] 3. Read: AGENTS.md (if multi-agent project)
  [ ] 4. THEN analyze code

  Architecture rules enforced by hooks.
  Use session-knowledge to learn from past mistakes.
"""
)
