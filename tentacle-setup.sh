#!/bin/bash
# tentacle-setup.sh — Setup tentacle orchestration for a new project
#
# DEPRECATED: prefer `python3 ~/.copilot/tools/setup-project.py` which handles
# tentacle setup (and more) in one step. This script remains for backwards
# compatibility and simple shell-only environments.
#
# Usage:
#   bash ~/.copilot/tools/tentacle-setup.sh          # Run from project root
#   bash ~/.copilot/tools/tentacle-setup.sh /path/to/project
#
# What it does:
#   1. Copies SKILL.md to .github/skills/tentacle-orchestration/
#   2. Adds .octogent/ to .gitignore
#   3. Verifies tentacle.py is accessible

set -euo pipefail

PROJECT_DIR="${1:-.}"
cd "$PROJECT_DIR"

# Skills now live under tools/skills/, not ~/.copilot/skills/
SKILL_SOURCE="$HOME/.copilot/tools/skills/tentacle-orchestration/SKILL.md"
TOOL_PATH="$HOME/.copilot/tools/tentacle.py"

echo "🐙 Setting up Tentacle Orchestration"
echo "   Project: $(pwd)"
echo ""

# 1. Check prerequisites
if [[ ! -f "$TOOL_PATH" ]]; then
    echo "❌ tentacle.py not found at $TOOL_PATH"
    exit 1
fi
echo "✅ tentacle.py found"

if [[ ! -f "$SKILL_SOURCE" ]]; then
    echo "❌ SKILL.md not found at $SKILL_SOURCE"
    exit 1
fi
echo "✅ SKILL.md source found"

# 2. Copy SKILL.md to project
SKILL_DEST=".github/skills/tentacle-orchestration"
mkdir -p "$SKILL_DEST"
cp "$SKILL_SOURCE" "$SKILL_DEST/SKILL.md"
echo "✅ SKILL.md copied to $SKILL_DEST/"

# 2b. Copy tentacle-creator skill (meta-skill for re-generating)
CREATOR_SOURCE="$HOME/.copilot/tools/skills/tentacle-creator/SKILL.md"
if [[ -f "$CREATOR_SOURCE" ]]; then
    CREATOR_DEST=".github/skills/tentacle-creator"
    mkdir -p "$CREATOR_DEST"
    cp "$CREATOR_SOURCE" "$CREATOR_DEST/SKILL.md"
    echo "✅ tentacle-creator skill copied to $CREATOR_DEST/"
fi

# 3. Add .octogent/ to .gitignore
if [[ -f .gitignore ]]; then
    if ! grep -qF '.octogent/' .gitignore; then
        echo "" >> .gitignore
        echo "# Tentacle orchestration (local work contexts)" >> .gitignore
        echo ".octogent/" >> .gitignore
        echo "✅ Added .octogent/ to .gitignore"
    else
        echo "✅ .octogent/ already in .gitignore"
    fi
else
    echo ".octogent/" > .gitignore
    echo "✅ Created .gitignore with .octogent/"
fi

echo ""
echo "🎉 Setup complete! Usage:"
echo "   python3 ~/.copilot/tools/tentacle.py create <name> --desc '<desc>' --briefing"
echo "   python3 ~/.copilot/tools/tentacle.py status"
echo ""
echo "   Or invoke skill: /tentacle-orchestration \"do task...\""
