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

# com.copilot.cli-healer uses a .plist (not .plist.template) with the same tokens
HEALER_AGENT="com.copilot.cli-healer"

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
    # Remove healer agent
    local healer_plist="${TARGET_DIR}/${HEALER_AGENT}.plist"
    if [[ -f "$healer_plist" ]]; then
        launchctl unload "$healer_plist" 2>/dev/null || true
        rm -f "$healer_plist"
        ok "Removed ${HEALER_AGENT}"
    else
        log "${HEALER_AGENT} not installed, skipping"
    fi
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

    # Install healer agent (uses .plist, not .plist.template)
    local healer_src="${LAUNCHD_DIR}/${HEALER_AGENT}.plist"
    local healer_dst="${TARGET_DIR}/${HEALER_AGENT}.plist"
    if [[ -f "$healer_src" ]]; then
        if [[ -f "$healer_dst" ]]; then
            launchctl unload "$healer_dst" 2>/dev/null || true
        fi
        sed -e "s|__HOME__|${HOME}|g" \
            -e "s|__PYTHON3__|${PYTHON3}|g" \
            "$healer_src" > "$healer_dst"
        launchctl load "$healer_dst"
        ok "Installed ${HEALER_AGENT}"
    else
        log "${HEALER_AGENT}.plist not found, skipping"
    fi
}

if [[ "${1:-}" == "--remove" ]]; then
    remove_agents
else
    install_agents
fi
