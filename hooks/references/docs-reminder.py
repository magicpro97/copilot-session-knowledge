#!/usr/bin/env python3
"""
docs-reminder.py — TEMPLATE (cross-platform: Windows, macOS, Linux)

postToolUse hook that counts code/config file edits and warns when
documentation hasn't been updated alongside code changes.
Warns after 3+ code edits without any doc file edit. Resets on doc edit.

Customize DOC_PATTERNS and CODE_PATTERNS for your project.
"""

import json
import os
import re
import sys
import tempfile

# --- Customize these for your project ---
DOC_PATTERNS = [
    r'(README|AGENTS|WORKFLOW|SKILL|copilot-instructions|copilot-rules|CHANGELOG)\.md$',
]
CODE_PATTERNS = {
    'tools': r'\.copilot[/\\]tools[/\\].*\.(py|sh)$',
    'hooks/agents': r'\.(github|claude)[/\\](hooks|agents|skills)[/\\].*\.(sh|ps1|md|json)$',
    'config': r'\.(github|claude)[/\\].*(\.md|\.json|\.yml)$',
}
WARN_THRESHOLD = 3
# -----------------------------------------

STATE_FILE = os.path.join(tempfile.gettempdir(), 'copilot-docs-tracker')


def main():
    raw = sys.stdin.read()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        sys.exit(0)

    tool_name = data.get('toolName', '')
    result_type = (data.get('toolResult') or {}).get('resultType', '')

    if result_type != 'success' or tool_name not in ('edit', 'create'):
        sys.exit(0)

    tool_args = data.get('toolArgs', {})
    if isinstance(tool_args, str):
        try:
            tool_args = json.loads(tool_args)
        except json.JSONDecodeError:
            sys.exit(0)

    file_path = tool_args.get('path', '')
    if not file_path:
        sys.exit(0)

    # Normalize path separators
    file_path = file_path.replace('\\', '/')

    # Check if it's a doc file
    for pattern in DOC_PATTERNS:
        if re.search(pattern, file_path, re.IGNORECASE):
            _write_state('docs_updated=true\n')
            sys.exit(0)

    # Check if it's a code file that needs docs
    needs_docs = ''
    for category, pattern in CODE_PATTERNS.items():
        if re.search(pattern, file_path):
            needs_docs = category
            break

    if not needs_docs:
        sys.exit(0)

    # Read state
    code_count = 0
    docs_updated = False
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, 'r') as f:
            content = f.read()
        docs_updated = 'docs_updated=true' in content
        code_count = content.count('code_edit')

    # Track this edit
    with open(STATE_FILE, 'a') as f:
        f.write('code_edit\n')
    code_count += 1

    # Warn after threshold
    if code_count >= WARN_THRESHOLD and not docs_updated:
        print(f'\n  📝 DOCS REMINDER: {code_count} {needs_docs} files changed, '
              f'no docs updated yet.')
        print('  Check if README.md, AGENTS.md, or SKILL.md need updates.\n')
        # Reset counter
        _write_state('')


def _write_state(content: str):
    with open(STATE_FILE, 'w') as f:
        f.write(content)


if __name__ == '__main__':
    main()
