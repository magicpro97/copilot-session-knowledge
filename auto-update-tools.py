#!/usr/bin/env python3
"""
auto-update-tools.py — Auto-update session-knowledge tools (cross-platform)

~/.copilot/tools/ IS the git clone. Update = git pull + smart pipeline + restart.

Features:
  - Smart diff: only updates what actually changed (services, plists, skills, etc.)
  - Self-exec: re-executes with new code if this script itself was updated
  - Version manifest: tracks component versions for verification
  - Post-merge hook: auto-triggers pipeline on manual `git pull`

Usage:
    python auto-update-tools.py              # Update (24h cooldown)
    python auto-update-tools.py --force      # Force update now
    python auto-update-tools.py --check      # Check only
    python auto-update-tools.py --status     # Show state
    python auto-update-tools.py --doctor     # Verify health (includes manifest check)
    python auto-update-tools.py --skip-pull  # Run pipeline without pulling (used by self-exec)
"""

import json
import os
import platform
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# Windows console encoding fix
# ---------------------------------------------------------------------------
if os.name == "nt":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
HOME = Path.home()
TOOLS_DIR = HOME / ".copilot" / "tools"
DB_PATH = HOME / ".copilot" / "session-state" / "knowledge.db"
SOURCE_REPO = "magicpro97/copilot-session-knowledge"
CLONE_URL = f"https://github.com/{SOURCE_REPO}.git"
COOLDOWN = 86400  # 24 hours
STATE_FILE = TOOLS_DIR / ".update-state.json"
MANIFEST_FILE = TOOLS_DIR / ".update-manifest.json"

# Registry written by setup-project.py and install.py --deploy-skill; records
# every project that has received a skill deployment so that auto-update can
# propagate vendored-skill updates there even when the current directory is the
# tools repo or a non-project directory (e.g. launchd / shell auto-start).
REGISTRY_PATH = HOME / ".copilot" / "session-state" / "tools-managed-projects.json"

# ---------------------------------------------------------------------------
# Vendored skills: non-template skills whose deployed bodies should be
# refreshed when skills/ source changes (Copilot CLI + Claude Code only).
# Paths are derived at runtime from HOST_SKILL_SUBPATHS to avoid duplication.
# ---------------------------------------------------------------------------
VENDORED_SKILLS: tuple[str, ...] = ("karpathy-guidelines",)

# ---------------------------------------------------------------------------
# Non-vendored built-in project skills: deployed by setup-project.py to
# .github/skills/<name>/ (Copilot CLI project path only).  deploy_skills()
# refreshes already-deployed SKILL.md and asset files here (update-only;
# never creates new deployments).  Keep in sync with INSTALL_ITEMS["skills"]
# in setup-project.py (excluding VENDORED_SKILLS entries).
# ---------------------------------------------------------------------------
BUILTIN_PROJECT_SKILLS: tuple[str, ...] = (
    "session-knowledge-creator",
    "tentacle-creator",
    "tentacle-orchestration",
    "agent-creator",
    "hook-creator",
    "workflow-creator",
    "find-skills",
    "agent-instructions-auditor",
    "forge-ecosystem",
    "code-reviewer",
    "task-step-generator",
    "conductor-creator",
    "project-onboarding",
)

# Global Copilot CLI skills directory.  deploy_skills() creates missing VENDORED
# skill dirs here and updates both VENDORED and already-installed BUILTIN dirs.
GLOBAL_COPILOT_SKILLS_DIR = HOME / ".copilot" / "skills"


def _running_in_wsl() -> bool:
    """Return True when this process is running inside WSL."""
    if platform.system() != "Linux":
        return False
    if os.environ.get("WSL_INTEROP") or os.environ.get("WSL_DISTRO_NAME"):
        return True
    for probe in (Path("/proc/sys/kernel/osrelease"), Path("/proc/version")):
        try:
            if "microsoft" in probe.read_text(encoding="utf-8", errors="ignore").lower():
                return True
        except OSError:
            continue
    return False


def _windows_path_to_wsl_path(path_str: str) -> Path | None:
    """Convert a Windows profile path (e.g. C:\\Users\\Name) to /mnt/<drive>/..."""
    candidate = path_str.strip().strip('"')
    if not candidate or len(candidate) > 256 or "\n" in candidate or "\r" in candidate:
        return None

    if candidate.startswith("/mnt/"):
        path = Path(candidate)
        return None if ".." in path.parts else path

    if len(candidate) < 3 or not candidate[0].isalpha() or candidate[1] != ":" or candidate[2] not in ("\\", "/"):
        return None

    tail = candidate[3:].replace("\\", "/").strip("/")
    parts = [part for part in tail.split("/") if part]
    if not parts or any(part == ".." for part in parts):
        return None

    return Path("/mnt") / candidate[0].lower() / Path(*parts)


def _windows_userprofile_candidates_from_wsl() -> tuple[str, ...]:
    """Return candidate Windows user-profile strings discoverable from WSL."""
    candidates: list[str] = []

    env_profile = os.environ.get("USERPROFILE", "").strip()
    if env_profile:
        candidates.append(env_profile)

    commands = (
        ["powershell.exe", "-NoProfile", "-Command", "[Environment]::GetFolderPath('UserProfile')"],
        ["cmd.exe", "/c", "echo", "%USERPROFILE%"],
    )
    for cmd in commands:
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        except Exception:
            continue
        if result.returncode == 0:
            value = result.stdout.strip()
            if value:
                candidates.append(value)

    unique: list[str] = []
    seen: set[str] = set()
    for value in candidates:
        if value not in seen:
            seen.add(value)
            unique.append(value)
    return tuple(unique)


def _windows_copilot_skills_dir_from_wsl() -> Path | None:
    """Return the current Windows user's global Copilot skills dir when on WSL."""
    if not _running_in_wsl():
        return None

    for candidate in _windows_userprofile_candidates_from_wsl():
        windows_home = _windows_path_to_wsl_path(candidate)
        if windows_home is None:
            continue
        skills_dir = windows_home / ".copilot" / "skills"
        try:
            if skills_dir.exists():
                return skills_dir
        except OSError:
            continue
    return None


def _global_copilot_skill_dirs() -> tuple[Path, ...]:
    """Return global Copilot CLI skill roots to refresh for this environment.

    On WSL, also include the current Windows user's Copilot CLI global skills
    directory when it can be resolved and accessed from /mnt/<drive>/...
    """
    dirs: list[Path] = [GLOBAL_COPILOT_SKILLS_DIR]

    windows_skills_dir = _windows_copilot_skills_dir_from_wsl()
    if windows_skills_dir is not None:
        dirs.append(windows_skills_dir)

    unique: list[Path] = []
    seen: set[str] = set()
    for path in dirs:
        key = str(path)
        if key not in seen:
            seen.add(key)
            unique.append(path)
    return tuple(unique)


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
def log(msg: str):
    print(f"[sk-update] {msg}")

def ok(msg: str):
    print(f"[sk-update] ✅ {msg}")

def warn(msg: str):
    print(f"[sk-update] ⚠️  {msg}", file=sys.stderr)

def err(msg: str):
    print(f"[sk-update] ❌ {msg}", file=sys.stderr)


# ---------------------------------------------------------------------------
# State helpers
# ---------------------------------------------------------------------------
def _load_state() -> dict:
    try:
        if STATE_FILE.exists():
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _save_state(state: dict):
    try:
        STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")
    except Exception as e:
        warn(f"Could not save state: {e}")


def _state_get(key: str, default: str = "") -> str:
    return str(_load_state().get(key, default))


def _state_set(key: str, value: str):
    state = _load_state()
    state[key] = value
    _save_state(state)


def _load_project_registry() -> list[Path]:
    """Return a list of registered project root Paths from the persistent registry.

    Written by setup-project.py on each successful (non-dry-run) deployment so
    that deploy_skills() can propagate vendored-skill updates to every managed
    project even when called from the tools repo or a non-project context.
    """
    try:
        if REGISTRY_PATH.exists():
            data = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
            return [Path(p) for p in data.get("projects", []) if isinstance(p, str)]
    except Exception:
        pass
    return []


# ---------------------------------------------------------------------------
# Git helpers
# ---------------------------------------------------------------------------
def _git(*args, cwd=None) -> subprocess.CompletedProcess:
    cmd = ["git"] + list(args)
    return subprocess.run(
        cmd,
        cwd=cwd or str(TOOLS_DIR),
        capture_output=True,
        text=True,
        timeout=60,
    )


def _git_output(*args, cwd=None) -> str:
    r = _git(*args, cwd=cwd)
    return r.stdout.strip() if r.returncode == 0 else ""


# ---------------------------------------------------------------------------
# Core: ensure tools dir is a git clone
# ---------------------------------------------------------------------------
def ensure_clone() -> bool:
    if (TOOLS_DIR / ".git").is_dir():
        return True

    log(f"First-time setup: cloning {SOURCE_REPO}...")
    tmp = Path(tempfile.mkdtemp())
    try:
        r = _git("clone", "--quiet", CLONE_URL, str(tmp / "repo"), cwd=str(tmp))
        if r.returncode != 0:
            err("Clone failed — check network")
            return False

        TOOLS_DIR.mkdir(parents=True, exist_ok=True)
        shutil.move(str(tmp / "repo" / ".git"), str(TOOLS_DIR / ".git"))
        _git("checkout", "--", ".")
        ok("Cloned successfully")
        return True
    except Exception as e:
        err(f"Clone failed: {e}")
        return False
    finally:
        shutil.rmtree(str(tmp), ignore_errors=True)


# ---------------------------------------------------------------------------
# Pull latest
# ---------------------------------------------------------------------------
def pull_latest() -> tuple[bool, str, str]:
    """Pull latest changes. Returns (updated, old_sha, new_sha)."""
    old_sha = _git_output("rev-parse", "HEAD")

    # Stash local changes, pull, re-apply
    _git("stash", "--quiet")

    r = _git("pull", "--ff-only", "--quiet", "origin", "main")
    if r.returncode != 0:
        # ff-only failed → reset hard (source repo is authoritative)
        _git("fetch", "--quiet", "origin")
        _git("reset", "--hard", "origin/main", "--quiet")

    _git("stash", "pop", "--quiet")

    new_sha = _git_output("rev-parse", "HEAD")
    short_old = old_sha[:8] if old_sha else "unknown"
    short_new = new_sha[:8] if new_sha else "unknown"

    if old_sha == new_sha:
        ok(f"Already up to date ({short_old})")
        return False, old_sha, new_sha

    log(f"Updated: {short_old} → {short_new}")
    _state_set("current_version", short_new)
    _state_set("previous_version", short_old)
    _state_set("last_update", datetime.now().isoformat())
    return True, old_sha, new_sha


# ---------------------------------------------------------------------------
# Smart diff: classify what changed between two commits
# ---------------------------------------------------------------------------
def classify_changes(old_sha: str, new_sha: str) -> dict:
    """Analyze git diff and classify changed files into categories."""
    diff_output = _git_output("diff", "--name-only", old_sha, new_sha)
    if not diff_output:
        return {}

    changed = diff_output.splitlines()
    return {
        "all": changed,
        "py_scripts": [f for f in changed if f.endswith(".py")],
        "launchd": [f for f in changed if f.startswith("launchd/")],
        "templates": [f for f in changed if f.startswith("templates/")],
        "skills": [f for f in changed if f.startswith("skills/")],
        "hooks": [f for f in changed if f.startswith("hooks/")],
        "embed": [f for f in changed if "embed" in f.lower() and f.endswith(".py")],
        "migrate": "migrate.py" in changed,
        "self_update": "auto-update-tools.py" in changed,
        "watch_sessions": "watch-sessions.py" in changed,
    }


# ---------------------------------------------------------------------------
# Post-pull pipeline: run targeted updates based on what changed
# ---------------------------------------------------------------------------
def post_pull_pipeline(old_sha: str, new_sha: str):
    """Smart pipeline: only update components whose source files changed."""
    changes = classify_changes(old_sha, new_sha)
    if not changes:
        ok("No changes to process")
        return

    changed_files = changes.get("all", [])
    log(f"Changed files: {len(changed_files)}")

    # 1. Self-exec: if this script changed, re-exec with new code
    if changes.get("self_update") and "--skip-pull" not in sys.argv:
        log("auto-update-tools.py changed — re-executing with new code...")
        _state_set("self_exec_from", old_sha[:8])
        args = [sys.executable, str(TOOLS_DIR / "auto-update-tools.py"), "--skip-pull",
                f"--old-sha={old_sha}", f"--new-sha={new_sha}"]
        # Add original flags
        for a in sys.argv[1:]:
            if a not in ("--force", "--skip-pull") and not a.startswith("--old-sha") and not a.startswith("--new-sha"):
                args.append(a)
        if "--force" in sys.argv:
            args.append("--force")
        if platform.system() == "Windows":
            # os.execl behaves differently on Windows
            subprocess.Popen(args)
            sys.exit(0)
        else:
            os.execl(sys.executable, *args)

    # 2. DB migrations (always — idempotent and safe)
    run_migrations()

    # 3. LaunchAgent templates changed → reinstall
    if changes.get("launchd"):
        reinstall_launchagents()

    # 3b. Git hook scripts changed → remind user to re-install per-repo hooks.
    # auto-update deliberately does NOT auto-reinstall git hooks into other repos:
    # it has no registry of which repos have hooks installed, and silently modifying
    # .git/hooks/ in arbitrary repos would be unsafe.  Users must re-run install.py.
    if changes.get("hooks"):
        hook_files = [f for f in changes["hooks"]
                      if "pre-commit" in f or "pre-push" in f or "check_subagent" in f]
        if hook_files:
            warn("Git hook scripts updated — installed per-repo hooks are NOT automatically refreshed.")
            warn("ACTION REQUIRED to pick up the cross-repo isolation fix (and future hook changes):")
            warn("  Re-run in EVERY protected repo: python3 ~/.copilot/tools/install.py --install-git-hooks")

    # 4. Template/SKILL.md changed → redeploy
    if changes.get("templates") or changes.get("skills"):
        deploy_skills()

    # 5. Python scripts changed → restart watcher service
    if changes.get("py_scripts"):
        restart_processes()

    # 6. Embedding logic changed → trigger rebuild (async, non-blocking)
    if changes.get("embed"):
        trigger_embedding_rebuild()

    # 7. Install/update post-merge hook
    ensure_post_merge_hook()

    # 8. Write version manifest
    write_manifest(new_sha, changes)

    ok("Pipeline complete")


# ---------------------------------------------------------------------------
# Reinstall LaunchAgents (macOS only)
# ---------------------------------------------------------------------------
def reinstall_launchagents():
    if platform.system() != "Darwin":
        return
    installer = TOOLS_DIR / "launchd" / "install-launchd.sh"
    if installer.exists():
        log("LaunchAgent templates changed — reinstalling...")
        r = subprocess.run(
            ["bash", str(installer)],
            capture_output=True, text=True, timeout=30,
        )
        if r.returncode == 0:
            ok("LaunchAgents reinstalled")
        else:
            warn(f"LaunchAgent reinstall failed: {r.stderr[:200]}")
    else:
        warn("install-launchd.sh not found — skipping LaunchAgent update")


# ---------------------------------------------------------------------------
# Trigger embedding rebuild (non-blocking)
# ---------------------------------------------------------------------------
def trigger_embedding_rebuild():
    embed_script = TOOLS_DIR / "embed.py"
    config = TOOLS_DIR / "embedding-config.json"
    if not embed_script.exists() or not config.exists():
        return
    log("Embedding logic changed — triggering rebuild...")
    subprocess.Popen(
        [sys.executable, str(embed_script), "--build"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True if platform.system() != "Windows" else False,
        **({"creationflags": subprocess.DETACHED_PROCESS | subprocess.CREATE_NO_WINDOW}
           if platform.system() == "Windows" else {}),
    )
    ok("Embedding rebuild triggered (background)")


# ---------------------------------------------------------------------------
# Ensure git post-merge hook (auto-pipeline on manual git pull)
# ---------------------------------------------------------------------------
def ensure_post_merge_hook():
    hook_path = TOOLS_DIR / ".git" / "hooks" / "post-merge"

    if platform.system() == "Windows":
        # Windows: Git for Windows runs hooks via bash (Git Bash/MSYS2)
        # Use python directly for reliability
        hook_content = f"""#!/bin/sh
# Auto-generated by auto-update-tools.py — triggers pipeline after git pull
# Re-created on each update; do not edit manually.
# Works on Windows (Git Bash), macOS, and Linux.

TOOLS_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
OLD_SHA="$(git -C "$TOOLS_DIR" rev-parse HEAD@{{1}} 2>/dev/null || echo "")"
NEW_SHA="$(git -C "$TOOLS_DIR" rev-parse HEAD)"

if [ "$OLD_SHA" = "$NEW_SHA" ]; then exit 0; fi

echo "[post-merge] Tools updated: ${{OLD_SHA:0:8}} → ${{NEW_SHA:0:8}}"

# Use python/python3 depending on platform
if command -v python3 >/dev/null 2>&1; then
    python3 "$TOOLS_DIR/auto-update-tools.py" --skip-pull --old-sha="$OLD_SHA" --new-sha="$NEW_SHA" --force &
else
    python "$TOOLS_DIR/auto-update-tools.py" --skip-pull --old-sha="$OLD_SHA" --new-sha="$NEW_SHA" --force &
fi
"""
    else:
        hook_content = f"""#!/bin/bash
# Auto-generated by auto-update-tools.py — triggers pipeline after git pull
# Re-created on each update; do not edit manually.

TOOLS_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
OLD_SHA="$(git -C "$TOOLS_DIR" rev-parse HEAD@{{1}} 2>/dev/null || echo "")"
NEW_SHA="$(git -C "$TOOLS_DIR" rev-parse HEAD)"

if [ "$OLD_SHA" = "$NEW_SHA" ]; then exit 0; fi

echo "[post-merge] Tools updated: ${{OLD_SHA:0:8}} → ${{NEW_SHA:0:8}}"

# Run pipeline with --skip-pull (pull already done by git)
python3 "$TOOLS_DIR/auto-update-tools.py" --skip-pull --old-sha="$OLD_SHA" --new-sha="$NEW_SHA" --force &
"""
    try:
        hook_path.parent.mkdir(parents=True, exist_ok=True)
        desired_bytes = hook_content.encode("utf-8")
        # Compare exact bytes so CRLF-generated hooks get normalized back to LF.
        if hook_path.exists() and hook_path.read_bytes() == desired_bytes:
            return
        hook_path.write_text(hook_content, encoding="utf-8", newline="\n")
        if platform.system() != "Windows":
            hook_path.chmod(0o755)
        ok("Post-merge hook installed")
    except Exception as e:
        warn(f"Could not install post-merge hook: {e}")


# ---------------------------------------------------------------------------
# Version manifest
# ---------------------------------------------------------------------------
def write_manifest(sha: str, changes: dict):
    """Write .update-manifest.json for verification."""
    short_sha = sha[:8] if sha else "unknown"
    manifest = {
        "version": short_sha,
        "full_sha": sha,
        "updated_at": datetime.now().isoformat(),
        "changed_files": len(changes.get("all", [])),
        "pipeline_actions": [],
    }

    actions = manifest["pipeline_actions"]
    if changes.get("self_update"):
        actions.append("self-exec")
    actions.append("migrate")  # always runs
    if changes.get("launchd"):
        actions.append("reinstall-launchagents")
    if changes.get("templates") or changes.get("skills"):
        actions.append("deploy-skills")
    if changes.get("py_scripts"):
        actions.append("restart-services")
    if changes.get("embed"):
        actions.append("rebuild-embeddings")
    actions.append("post-merge-hook")

    # Service status
    manifest["services"] = {}
    system = platform.system()
    if system == "Darwin":
        r = subprocess.run(
            ["launchctl", "list", "com.copilot.watch-sessions"],
            capture_output=True, text=True,
        )
        # launchctl list exits 0 when the job is loaded, even when it is not
        # actively running (e.g. waiting for restart after repeated failures).
        # A job is truly running only when launchd has a live PID for it.
        launchd_loaded = r.returncode == 0
        launchd_running = launchd_loaded and '"PID"' in r.stdout
        manifest["services"]["watch-sessions"] = {
            "managed_by": "launchd",
            "running": launchd_running,
        }
    elif system == "Linux":
        r = subprocess.run(
            ["systemctl", "--user", "is-active", "copilot-watch-sessions.service"],
            capture_output=True, text=True,
        )
        manifest["services"]["watch-sessions"] = {
            "managed_by": "systemd",
            "running": r.stdout.strip() == "active",
        }
    elif system == "Windows":
        r = subprocess.run(
            ["schtasks", "/Query", "/TN", "CopilotSessionWatcher"],
            capture_output=True, text=True,
        )
        manifest["services"]["watch-sessions"] = {
            "managed_by": "task-scheduler",
            "running": r.returncode == 0,
        }

    try:
        MANIFEST_FILE.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    except Exception as e:
        warn(f"Could not write manifest: {e}")


# ---------------------------------------------------------------------------
# Migrate DB
# ---------------------------------------------------------------------------
def run_migrations():
    if not DB_PATH.exists():
        return
    migrate_script = TOOLS_DIR / "migrate.py"
    if migrate_script.exists():
        try:
            subprocess.run(
                [sys.executable, str(migrate_script), str(DB_PATH)],
                capture_output=True,
                text=True,
                timeout=30,
            )
        except subprocess.TimeoutExpired:
            warn("Migration timed out after 30s (DB may be locked by another process)")


# ---------------------------------------------------------------------------
# Deploy SKILL.md to projects
# ---------------------------------------------------------------------------
def deploy_skills():
    # Collect all project roots to update:
    # 1. Registered projects written by setup-project.py (primary path — works
    #    even when called from the tools repo / launchd / shell auto-start).
    # 2. Fallback: current git root (legacy behaviour, handles ad-hoc installs).
    registered = _load_project_registry()
    project_roots: list[Path] = [p for p in registered if p.is_dir()]

    r = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
    )
    if r.returncode == 0:
        fallback = Path(r.stdout.strip())
        if fallback not in project_roots:
            project_roots.append(fallback)

    if not project_roots:
        pass  # no registered projects / git root; global update still runs below
    # Import host metadata from the manifest so host paths stay centralised.
    # TOOLS_DIR is inserted so host_manifest is importable regardless of cwd.
    try:
        if str(TOOLS_DIR) not in sys.path:
            sys.path.insert(0, str(TOOLS_DIR))
        from host_manifest import HOST_DIRS, HOST_SKILL_SUBPATHS  # noqa: PLC0415
    except ImportError:
        # Fallback when manifest is unavailable (broken or first-run state).
        HOST_DIRS = {
            "Copilot CLI": Path.home() / ".copilot",
            "Claude Code":  Path.home() / ".claude",
        }
        HOST_SKILL_SUBPATHS = {
            "Copilot CLI": ".github/skills/session-knowledge/SKILL.md",
            "Claude Code":  ".claude/skills/session-knowledge/SKILL.md",
        }

    # Pre-read skill sources once; skip if source doesn't exist.
    template = TOOLS_DIR / "templates" / "SKILL.md"
    template_content = template.read_text(encoding="utf-8") if template.exists() else None

    vendored_sources: dict[str, tuple[str, Path]] = {}  # skill_name → (content, src_dir)
    for skill_name in VENDORED_SKILLS:
        skill_src = TOOLS_DIR / "skills" / skill_name / "SKILL.md"
        if skill_src.exists():
            vendored_sources[skill_name] = (
                skill_src.read_text(encoding="utf-8"),
                TOOLS_DIR / "skills" / skill_name,
            )

    for project_root in project_roots:
        # --- session-knowledge template (guarded by its own file existence) --
        if template_content is not None:
            for host_name in HOST_DIRS:
                subpath = HOST_SKILL_SUBPATHS.get(host_name)
                if subpath is None:
                    continue
                skill_path = project_root / subpath
                if skill_path.exists():
                    try:
                        if skill_path.read_text(encoding="utf-8") != template_content:
                            skill_path.write_text(template_content, encoding="utf-8")
                            ok(f"Updated {host_name} SKILL.md in {project_root.name}")
                    except Exception:
                        pass

        # --- vendored skill bodies (independent of template existence) -------
        # Paths derived from HOST_SKILL_SUBPATHS to avoid duplicating strings:
        #   .github/skills/session-knowledge/SKILL.md → .github/skills/<name>/SKILL.md
        for skill_name, (skill_content, skill_src_dir) in vendored_sources.items():
            for host_name in HOST_DIRS:
                ref = HOST_SKILL_SUBPATHS.get(host_name)
                if ref is None:
                    continue
                skills_base = Path(ref).parent.parent  # e.g. ".github/skills"
                target = project_root / skills_base / skill_name / "SKILL.md"
                if target.exists():
                    try:
                        if target.read_text(encoding="utf-8") != skill_content:
                            target.write_text(skill_content, encoding="utf-8")
                            ok(f"Updated {host_name} {skill_name}/SKILL.md in {project_root.name}")
                    except Exception:
                        pass
                # Asset subdirs — update only, never create.
                for subdir in sorted(skill_src_dir.iterdir()):
                    if not subdir.is_dir():
                        continue
                    for asset_file in subdir.rglob("*"):
                        if not asset_file.is_file():
                            continue
                        rel = asset_file.relative_to(skill_src_dir)
                        asset_target = project_root / skills_base / skill_name / rel
                        if asset_target.exists():
                            try:
                                content = asset_file.read_bytes()
                                if asset_target.read_bytes() != content:
                                    asset_target.write_bytes(content)
                                    ok(f"Updated {host_name} {skill_name}/{rel} in {project_root.name}")
                            except Exception:
                                pass

        # --- non-vendored built-in project skills (Copilot CLI path only) ----
        # These are deployed by setup-project.py to .github/skills/<name>/.
        # Update-only: only refresh files that already exist at the destination.
        for skill_name in BUILTIN_PROJECT_SKILLS:
            skill_src = TOOLS_DIR / "skills" / skill_name / "SKILL.md"
            if not skill_src.exists():
                continue
            skill_content = skill_src.read_text(encoding="utf-8")
            skill_src_dir = TOOLS_DIR / "skills" / skill_name
            target = project_root / ".github" / "skills" / skill_name / "SKILL.md"
            if target.exists():
                try:
                    if target.read_text(encoding="utf-8") != skill_content:
                        target.write_text(skill_content, encoding="utf-8")
                        ok(f"Updated {skill_name}/SKILL.md in {project_root.name}")
                except Exception:
                    pass
            # Asset subdirs — update only, never create.
            for subdir in sorted(skill_src_dir.iterdir()):
                if not subdir.is_dir():
                    continue
                for asset_file in subdir.rglob("*"):
                    if not asset_file.is_file():
                        continue
                    rel = asset_file.relative_to(skill_src_dir)
                    asset_target = project_root / ".github" / "skills" / skill_name / rel
                    if asset_target.exists():
                        try:
                            content = asset_file.read_bytes()
                            if asset_target.read_bytes() != content:
                                asset_target.write_bytes(content)
                                ok(f"Updated {skill_name}/{rel} in {project_root.name}")
                        except Exception:
                            pass

    # --- global Copilot CLI skills ----------------------------------------
    # VENDORED skills: create the dir from scratch if it is missing so newly
    # added skills (e.g. karpathy-guidelines) land on the machine after a
    # pull+update without requiring a manual rsync step.
    # BUILTIN_PROJECT_SKILLS: update already-installed global dirs + fill in
    # any missing asset files (e.g. forge-ecosystem/references/).  Dir
    # creation is deliberately skipped for BUILTIN skills — those are
    # installed by the Copilot CLI marketplace / install.py, not this updater.
    for global_skills_root in _global_copilot_skill_dirs():
        # VENDORED: create dir if missing, sync SKILL.md and all asset files.
        for skill_name, (skill_content, skill_src_dir) in vendored_sources.items():
            global_skill_dir = global_skills_root / skill_name
            global_skill_md = global_skill_dir / "SKILL.md"
            try:
                global_skill_dir.mkdir(parents=True, exist_ok=True)
                existed = global_skill_md.exists()
                if not existed or global_skill_md.read_text(encoding="utf-8") != skill_content:
                    global_skill_md.write_text(skill_content, encoding="utf-8")
                    ok(f"{'Created' if not existed else 'Updated'} global Copilot CLI {skill_name}/SKILL.md in {global_skills_root}")
            except Exception:
                pass
            for subdir in sorted(skill_src_dir.iterdir()):
                if not subdir.is_dir():
                    continue
                for asset_file in subdir.rglob("*"):
                    if not asset_file.is_file():
                        continue
                    rel = asset_file.relative_to(skill_src_dir)
                    asset_target = global_skill_dir / rel
                    try:
                        content = asset_file.read_bytes()
                        existed = asset_target.exists()
                        if not existed or asset_target.read_bytes() != content:
                            asset_target.parent.mkdir(parents=True, exist_ok=True)
                            asset_target.write_bytes(content)
                            ok(f"{'Created' if not existed else 'Updated'} global Copilot CLI {skill_name}/{rel} in {global_skills_root}")
                    except Exception:
                        pass

        # BUILTIN_PROJECT_SKILLS: update existing global dirs + sync missing assets.
        for skill_name in BUILTIN_PROJECT_SKILLS:
            skill_src = TOOLS_DIR / "skills" / skill_name / "SKILL.md"
            if not skill_src.exists():
                continue
            global_skill_dir = global_skills_root / skill_name
            if not global_skill_dir.is_dir():
                continue  # update-only: never auto-create global dirs for builtin skills
            skill_content = skill_src.read_text(encoding="utf-8")
            skill_src_dir = TOOLS_DIR / "skills" / skill_name
            global_skill_md = global_skill_dir / "SKILL.md"
            try:
                if not global_skill_md.exists() or global_skill_md.read_text(encoding="utf-8") != skill_content:
                    global_skill_md.write_text(skill_content, encoding="utf-8")
                    ok(f"Updated global Copilot CLI {skill_name}/SKILL.md in {global_skills_root}")
            except Exception:
                pass
            for subdir in sorted(skill_src_dir.iterdir()):
                if not subdir.is_dir():
                    continue
                for asset_file in subdir.rglob("*"):
                    if not asset_file.is_file():
                        continue
                    rel = asset_file.relative_to(skill_src_dir)
                    asset_target = global_skill_dir / rel
                    try:
                        content = asset_file.read_bytes()
                        existed = asset_target.exists()
                        if not existed or asset_target.read_bytes() != content:
                            asset_target.parent.mkdir(parents=True, exist_ok=True)
                            asset_target.write_bytes(content)
                            ok(f"{'Created' if not existed else 'Updated'} global Copilot CLI {skill_name}/{rel} in {global_skills_root}")
                    except Exception:
                        pass


# ---------------------------------------------------------------------------
# Restart processes
# ---------------------------------------------------------------------------
def restart_processes():
    system = platform.system()

    # Linux: prefer systemd
    if system == "Linux":
        r = subprocess.run(
            ["systemctl", "--user", "is-enabled", "copilot-watch-sessions.service"],
            capture_output=True,
            text=True,
        )
        if r.returncode == 0:
            log("Restarting watch-sessions via systemd...")
            subprocess.run(
                ["systemctl", "--user", "restart", "copilot-watch-sessions.service"],
                capture_output=True,
            )
            ok("watch-sessions restarted (systemd)")
            return

    # macOS: prefer launchd
    if system == "Darwin":
        plist = HOME / "Library" / "LaunchAgents" / "com.copilot.watch-sessions.plist"
        if plist.exists():
            uid = os.getuid()
            label = "com.copilot.watch-sessions"
            # kickstart -k kills any running instance and starts a fresh one,
            # which is required after reinstalling a plist.  stop+start leaves
            # the agent in "spawn scheduled" state with no live PID.
            subprocess.run(
                ["launchctl", "kickstart", "-k", f"gui/{uid}/{label}"],
                capture_output=True,
            )
            ok("watch-sessions restarted (launchd)")
            return

    # Windows: use Task Scheduler
    if system == "Windows":
        r = subprocess.run(
            ["schtasks", "/Query", "/TN", "CopilotSessionWatcher"],
            capture_output=True,
            text=True,
        )
        if r.returncode == 0:
            log("Restarting watch-sessions via Task Scheduler...")
            subprocess.run(
                ["schtasks", "/End", "/TN", "CopilotSessionWatcher"],
                capture_output=True,
            )
            time.sleep(1)
            subprocess.run(
                ["schtasks", "/Run", "/TN", "CopilotSessionWatcher"],
                capture_output=True,
            )
            ok("watch-sessions restarted (Task Scheduler)")
            return

    # Fallback: find and restart manually
    _restart_manual()


def _restart_manual():
    """Kill existing watcher and start a new one."""
    system = platform.system()

    if system == "Windows":
        # Find pythonw processes running watch-sessions.py
        r = subprocess.run(
            ["powershell", "-Command",
             "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*watch-sessions.py*' } | Select-Object ProcessId"],
            capture_output=True,
            text=True,
        )
        for line in r.stdout.splitlines():
            line = line.strip()
            if line.isdigit():
                pid = int(line)
                try:
                    os.kill(pid, signal.SIGTERM)
                except Exception:
                    pass
        time.sleep(1)

        pythonw = shutil.which("pythonw") or shutil.which("python")
        subprocess.Popen(
            [pythonw, str(TOOLS_DIR / "watch-sessions.py"), "--service"],
            creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NO_WINDOW,
        )
        ok("watch-sessions restarted (manual)")
    else:
        # Unix fallback
        r = subprocess.run(
            ["pgrep", "-f", "watch-sessions.py"],
            capture_output=True,
            text=True,
        )
        for line in r.stdout.splitlines():
            pid = line.strip()
            if pid.isdigit() and int(pid) > 2:
                try:
                    os.kill(int(pid), signal.SIGTERM)
                except Exception:
                    pass
        time.sleep(1)

        subprocess.Popen(
            [sys.executable, str(TOOLS_DIR / "watch-sessions.py")],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        ok("watch-sessions restarted (manual)")


# ---------------------------------------------------------------------------
# Doctor
# ---------------------------------------------------------------------------
def doctor():
    print("=== sk-update doctor ===")
    issues = 0

    # Python check
    ok(f"python {sys.version.split()[0]}")

    # Core tools
    core_files = ["learn.py", "briefing.py", "query-session.py", "extract-knowledge.py"]
    missing = [f for f in core_files if not (TOOLS_DIR / f).exists()]
    if missing:
        for f in missing:
            err(f"Missing: {f}")
            issues += 1
    else:
        ok("Core tools present")

    # DB
    if DB_PATH.exists():
        try:
            import sqlite3
            conn = sqlite3.connect(str(DB_PATH))
            count = conn.execute("SELECT COUNT(*) FROM knowledge_entries").fetchone()[0]
            conn.close()
            ok(f"DB: {count} entries")
        except Exception as e:
            warn(f"DB: {e}")
    else:
        warn("DB not found")

    # Git
    if (TOOLS_DIR / ".git").is_dir():
        sha = _git_output("rev-parse", "--short=8", "HEAD")
        age = _git_output("log", "-1", "--format=%cr")
        ok(f"Git: {sha} ({age})")
    else:
        warn("Not a git clone")

    # Version manifest
    if MANIFEST_FILE.exists():
        try:
            manifest = json.loads(MANIFEST_FILE.read_text(encoding="utf-8"))
            head_sha = _git_output("rev-parse", "--short=8", "HEAD")
            m_ver = manifest.get("version", "?")
            m_time = manifest.get("updated_at", "?")
            if m_ver == head_sha:
                ok(f"Manifest: {m_ver} ({m_time})")
            else:
                warn(f"Manifest stale: {m_ver} != HEAD {head_sha} — run update")
                issues += 1

            # Check services
            services = manifest.get("services", {})
            for name, info in services.items():
                if info.get("running"):
                    ok(f"Service {name}: running ({info.get('managed_by', '?')})")
                else:
                    warn(f"Service {name}: not running")
                    issues += 1
        except Exception as e:
            warn(f"Manifest: {e}")
    else:
        warn("No manifest — run update to create")

    # Post-merge hook
    hook = TOOLS_DIR / ".git" / "hooks" / "post-merge"
    if hook.exists():
        ok("Post-merge hook: installed")
    else:
        warn("Post-merge hook: missing — run update to install")
        issues += 1

    # LaunchAgents (macOS) / systemd (Linux) / Task Scheduler (Windows)
    system = platform.system()
    if system == "Darwin":
        for agent in ["com.copilot.watch-sessions", "com.copilot.auto-update"]:
            plist = HOME / "Library" / "LaunchAgents" / f"{agent}.plist"
            if plist.exists():
                r = subprocess.run(["launchctl", "list", agent], capture_output=True, text=True)
                if r.returncode != 0:
                    warn(f"LaunchAgent {agent}: plist exists but not loaded")
                    issues += 1
                elif '"PID"' in r.stdout:
                    ok(f"LaunchAgent {agent}: running")
                elif agent == "com.copilot.auto-update":
                    # Scheduled agent — loaded/idle between daily runs is healthy;
                    # a live PID is only present while the update job is executing.
                    ok(f"LaunchAgent {agent}: loaded/scheduled")
                else:
                    warn(f"LaunchAgent {agent}: loaded but not running (check logs)")
                    issues += 1
            else:
                warn(f"LaunchAgent {agent}: not installed")
    elif system == "Linux":
        r = subprocess.run(
            ["systemctl", "--user", "is-active", "copilot-watch-sessions.service"],
            capture_output=True, text=True,
        )
        if r.returncode == 0:
            ok("systemd copilot-watch-sessions: active")
        else:
            warn("systemd copilot-watch-sessions: not active (optional)")
    elif system == "Windows":
        r = subprocess.run(
            ["schtasks", "/Query", "/TN", "CopilotSessionWatcher"],
            capture_output=True, text=True,
        )
        if r.returncode == 0:
            ok("Task Scheduler CopilotSessionWatcher: registered")
        else:
            warn("Task Scheduler CopilotSessionWatcher: not registered (optional)")

    if issues == 0:
        ok("All good")
    else:
        err(f"{issues} issue(s)")


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------
def show_status():
    print("=== Session-Knowledge Tools ===")
    if (TOOLS_DIR / ".git").is_dir():
        print(f"  Version: {_git_output('rev-parse', '--short=8', 'HEAD')}")
        print(f"  Updated: {_git_output('log', '-1', '--format=%ci')}")
        print(f"  Branch:  {_git_output('rev-parse', '--abbrev-ref', 'HEAD')}")
    else:
        print("  Not a git clone (run with --force to setup)")
    print(f"  Source:  {SOURCE_REPO}")
    py_count = len(list(TOOLS_DIR.glob("*.py")))
    print(f"  Files:   {py_count} Python scripts")


# ---------------------------------------------------------------------------
# Cooldown
# ---------------------------------------------------------------------------
def check_cooldown() -> bool:
    """Returns True if cooldown has expired (OK to update)."""
    last = int(_state_get("last_check_epoch", "0"))
    now = int(time.time())
    elapsed = now - last

    if elapsed < COOLDOWN:
        remaining = (COOLDOWN - elapsed) // 3600
        ok(f"Up to date (next check in ~{remaining}h). Use --force to override.")
        return False
    return True


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    force = False
    check_only = False
    skip_pull = False
    old_sha_arg = ""
    new_sha_arg = ""

    for arg in sys.argv[1:]:
        if arg == "--force":
            force = True
        elif arg == "--check":
            check_only = True
        elif arg == "--skip-pull":
            skip_pull = True
        elif arg.startswith("--old-sha="):
            old_sha_arg = arg.split("=", 1)[1]
        elif arg.startswith("--new-sha="):
            new_sha_arg = arg.split("=", 1)[1]
        elif arg == "--status":
            show_status()
            return
        elif arg == "--doctor":
            doctor()
            return
        elif arg in ("--help", "-h"):
            print(__doc__)
            return

    # --skip-pull mode: pipeline only (used by self-exec and post-merge hook)
    if skip_pull:
        old_sha = old_sha_arg
        new_sha = new_sha_arg or _git_output("rev-parse", "HEAD")
        if old_sha and new_sha:
            log("Running post-pull pipeline (skip-pull mode)...")
            post_pull_pipeline(old_sha, new_sha)
        else:
            # No SHAs available — run full pipeline as fallback
            run_migrations()
            deploy_skills()
            restart_processes()
            ensure_post_merge_hook()
            ok("Fallback pipeline complete")
        return

    # Cooldown
    if not force and not check_only:
        if not check_cooldown():
            return

    _state_set("last_check_epoch", str(int(time.time())))

    # Ensure git clone
    if not ensure_clone():
        sys.exit(1)

    # Pull
    updated, old_sha, new_sha = pull_latest()
    if updated:
        if check_only:
            log("Update available")
            return
        post_pull_pipeline(old_sha, new_sha)
    else:
        # Even if no update, ensure post-merge hook exists
        ensure_post_merge_hook()


if __name__ == "__main__":
    main()
