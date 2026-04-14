#!/usr/bin/env bash
# sk-update — Auto-update session-knowledge tools
#
# ~/.copilot/tools/ IS the git clone. Update = git pull + migrate + restart.
#
# Usage:
#   sk-update              # Update (24h cooldown)
#   sk-update --force      # Force update now
#   sk-update --check      # Check only
#   sk-update --status     # Show state
#   sk-update --doctor     # Verify health

set -euo pipefail

TOOLS_DIR="${HOME}/.copilot/tools"
DB_PATH="${HOME}/.copilot/session-state/knowledge.db"
SOURCE_REPO="magicpro97/copilot-session-knowledge"
CLONE_URL="https://github.com/${SOURCE_REPO}.git"
COOLDOWN=86400
STATE_FILE="${TOOLS_DIR}/.update-state.json"

log()  { echo "[sk-update] $*"; }
ok()   { echo "[sk-update] ✅ $*"; }
warn() { echo "[sk-update] ⚠️  $*" >&2; }
err()  { echo "[sk-update] ❌ $*" >&2; }

_state() {
    python3 -c "
import json,os,sys; p='${STATE_FILE}'
d=json.load(open(p)) if os.path.exists(p) else {}
if sys.argv[1]=='get': print(d.get(sys.argv[2],sys.argv[3] if len(sys.argv)>3 else ''))
elif sys.argv[1]=='set': d[sys.argv[2]]=sys.argv[3]; json.dump(d,open(p,'w'),indent=2)
" "$@" 2>/dev/null
}

# --- Core: ensure tools dir is a git clone ---
ensure_clone() {
    if [[ -d "${TOOLS_DIR}/.git" ]]; then
        return 0
    fi
    # First time: clone into temp, move .git into existing tools dir
    log "First-time setup: cloning ${SOURCE_REPO}..."
    local tmp; tmp=$(mktemp -d)
    if git clone --quiet "$CLONE_URL" "$tmp/repo" 2>/dev/null; then
        mkdir -p "$TOOLS_DIR"
        mv "$tmp/repo/.git" "${TOOLS_DIR}/.git"
        # Reset to get upstream files without clobbering local data (knowledge.db etc is elsewhere)
        git -C "$TOOLS_DIR" checkout -- . 2>/dev/null || true
        rm -rf "$tmp"
        ok "Cloned successfully"
        return 0
    fi
    rm -rf "$tmp"
    err "Clone failed — check network"
    return 1
}

# --- Pull latest ---
pull_latest() {
    local old_sha; old_sha=$(git -C "$TOOLS_DIR" rev-parse --short=8 HEAD 2>/dev/null)
    
    # Stash local changes (if any), pull, re-apply
    git -C "$TOOLS_DIR" stash --quiet 2>/dev/null || true
    if ! git -C "$TOOLS_DIR" pull --ff-only --quiet origin main 2>/dev/null; then
        # ff-only failed → reset hard (source repo is authoritative)
        git -C "$TOOLS_DIR" fetch --quiet origin 2>/dev/null
        git -C "$TOOLS_DIR" reset --hard origin/main --quiet 2>/dev/null
    fi
    git -C "$TOOLS_DIR" stash pop --quiet 2>/dev/null || true
    
    local new_sha; new_sha=$(git -C "$TOOLS_DIR" rev-parse --short=8 HEAD 2>/dev/null)
    
    if [[ "$old_sha" == "$new_sha" ]]; then
        ok "Already up to date (${old_sha})"
        return 1  # no update needed
    fi
    
    log "Updated: ${old_sha} → ${new_sha}"
    _state set current_version "$new_sha"
    _state set previous_version "$old_sha"
    _state set last_update "$(date -Iseconds)"
    return 0
}

# --- Migrate DB ---
run_migrations() {
    [[ ! -f "$DB_PATH" ]] && return 0
    if [[ -f "${TOOLS_DIR}/migrate.py" ]]; then
        python3 "${TOOLS_DIR}/migrate.py" "$DB_PATH"
    fi
}

# --- Deploy SKILL.md to projects ---
deploy_skills() {
    local template="${TOOLS_DIR}/templates/SKILL.md"
    [[ ! -f "$template" ]] && return 0
    
    # Find git root of current directory (if in a project)
    local project_root
    project_root=$(git rev-parse --show-toplevel 2>/dev/null || true)
    [[ -z "$project_root" ]] && return 0

    # Deploy to Copilot CLI skill path
    local copilot_skill="${project_root}/.github/skills/session-knowledge/SKILL.md"
    if [[ -f "$copilot_skill" ]]; then
        if ! diff -q "$template" "$copilot_skill" &>/dev/null; then
            cp "$template" "$copilot_skill"
            ok "Updated SKILL.md in ${project_root##*/}"
        fi
    fi

    # Deploy to Claude Code skill path
    local claude_skill="${project_root}/.claude/skills/session-knowledge/SKILL.md"
    if [[ -f "$claude_skill" ]]; then
        if ! diff -q "$template" "$claude_skill" &>/dev/null; then
            cp "$template" "$claude_skill"
            ok "Updated Claude SKILL.md in ${project_root##*/}"
        fi
    fi
}

# --- Restart processes ---
restart_processes() {
    # Prefer systemd if the service exists
    if systemctl --user is-enabled copilot-watch-sessions.service &>/dev/null; then
        log "Restarting watch-sessions via systemd..."
        systemctl --user restart copilot-watch-sessions.service
        ok "watch-sessions restarted (systemd)"
        return
    fi

    # Fallback: manual restart
    local pids
    pids=$(pgrep -f "watch-sessions.py" 2>/dev/null || true)
    if [[ -n "$pids" ]]; then
        log "Restarting watch-sessions..."
        echo "$pids" | while read -r pid; do
            [[ -n "$pid" && "$pid" -gt 2 ]] && command kill -TERM "$pid" 2>/dev/null || true
        done
        sleep 1
        nohup python3 "${TOOLS_DIR}/watch-sessions.py" > /dev/null 2>&1 &
        ok "watch-sessions restarted (PID $!)"
    fi
}

# --- Doctor ---
doctor() {
    echo "=== sk-update doctor ==="
    local issues=0
    python3 --version &>/dev/null && ok "python3 OK" || { err "python3 missing"; ((issues++)); }
    for f in learn.py briefing.py query-session.py extract-knowledge.py; do
        [[ -f "${TOOLS_DIR}/${f}" ]] || { err "Missing: ${f}"; ((issues++)); }
    done
    [[ $issues -eq 0 ]] && ok "Core tools present"
    if [[ -f "$DB_PATH" ]]; then
        ok "DB: $(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM knowledge_entries" 2>/dev/null || echo '?') entries"
    fi
    [[ -d "${TOOLS_DIR}/.git" ]] && ok "Git: $(git -C "$TOOLS_DIR" rev-parse --short=8 HEAD) ($(git -C "$TOOLS_DIR" log -1 --format='%cr'))" || warn "Not a git clone"
    [[ $issues -eq 0 ]] && ok "All good" || err "${issues} issue(s)"
}

# --- Status ---
show_status() {
    echo "=== Session-Knowledge Tools ==="
    if [[ -d "${TOOLS_DIR}/.git" ]]; then
        echo "  Version: $(git -C "$TOOLS_DIR" rev-parse --short=8 HEAD)"
        echo "  Updated: $(git -C "$TOOLS_DIR" log -1 --format='%ci')"
        echo "  Branch:  $(git -C "$TOOLS_DIR" rev-parse --abbrev-ref HEAD)"
    else
        echo "  Not a git clone (run sk-update --force to setup)"
    fi
    echo "  Source:  ${SOURCE_REPO}"
    echo "  Files:   $(ls "$TOOLS_DIR"/*.py 2>/dev/null | wc -l) Python scripts"
}

# --- Check cooldown ---
check_cooldown() {
    local last; last=$(_state get last_check_epoch 0)
    local now; now=$(date +%s)
    local elapsed=$((now - last))
    if [[ $elapsed -lt $COOLDOWN ]]; then
        local remaining=$(( (COOLDOWN - elapsed) / 3600 ))
        ok "Up to date (next check in ~${remaining}h). Use --force to override."
        return 1
    fi
    return 0
}

# === Main ===
main() {
    local force=false check_only=false
    for arg in "$@"; do
        case "$arg" in
            --force)   force=true ;;
            --check)   check_only=true ;;
            --status)  show_status; return 0 ;;
            --doctor)  doctor; return 0 ;;
            --help|-h) head -12 "$0" | tail -7; return 0 ;;
        esac
    done

    # Cooldown
    if ! $force && ! $check_only; then
        check_cooldown || return 0
    fi
    _state set last_check_epoch "$(date +%s)"

    # Ensure git clone
    ensure_clone || return 1

    # Pull
    if pull_latest; then
        if $check_only; then
            log "Update available"
            return 0
        fi
        run_migrations
        deploy_skills
        restart_processes
        ok "Done"
    fi
}

main "$@"
