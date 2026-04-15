#!/usr/bin/env bash
# install-launchd.sh — Install macOS LaunchAgents for session-knowledge tools
#
# Installs two agents:
#   - com.copilot.watch-sessions: Daemon that auto-indexes new sessions + embeds
#   - com.copilot.auto-update: Daily job (9 AM) that git-pulls tool updates
#
# Usage:
#   bash install-launchd.sh           # Install and load agents
#   bash install-launchd.sh --remove  # Unload and remove agents

set -euo pipefail

TOOLS_DIR="$(cd "$(dirname "$0")/.." && pwd)"
LAUNCHD_DIR="${TOOLS_DIR}/launchd"
TARGET_DIR="${HOME}/Library/LaunchAgents"
PYTHON3="$(which python3)"

log()  { echo "[launchd] $*"; }
ok()   { echo "[launchd] ✅ $*"; }
err()  { echo "[launchd] ❌ $*" >&2; }

AGENTS=(
    "com.copilot.watch-sessions"
    "com.copilot.auto-update"
)

remove_agents() {
    for agent in "${AGENTS[@]}"; do
        local plist="${TARGET_DIR}/${agent}.plist"
        if [[ -f "$plist" ]]; then
            launchctl unload "$plist" 2>/dev/null || true
            rm -f "$plist"
            ok "Removed ${agent}"
        else
            log "${agent} not installed, skipping"
        fi
    done
}

install_agents() {
    mkdir -p "$TARGET_DIR"

    for agent in "${AGENTS[@]}"; do
        local template="${LAUNCHD_DIR}/${agent}.plist.template"
        local plist="${TARGET_DIR}/${agent}.plist"

        if [[ ! -f "$template" ]]; then
            err "Template not found: ${template}"
            continue
        fi

        # Unload existing
        if [[ -f "$plist" ]]; then
            launchctl unload "$plist" 2>/dev/null || true
        fi

        # Render template
        sed -e "s|__HOME__|${HOME}|g" \
            -e "s|__PYTHON3__|${PYTHON3}|g" \
            "$template" > "$plist"

        # Load
        launchctl load "$plist"
        ok "Installed ${agent}"
    done

    log ""
    log "Status:"
    launchctl list | grep copilot || log "  (none running yet)"
}

if [[ "${1:-}" == "--remove" ]]; then
    remove_agents
else
    install_agents
fi
