"""browse/__init__.py — Hindsight local web UI package entry point."""

import os
import sys

if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")

# Re-export for test + shim compatibility
# Register all routes (triggers @route decorators)
import browse.routes  # noqa: F401
from browse.core.fts import (  # noqa: F401
    _DEFAULT_DB,
    _SESSION_ID_RE,
    _esc,
    _open_db,
    _sanitize_fts_query,
)
from browse.core.server import _make_handler_class  # noqa: F401

HOSTED_BOOTSTRAP_ORIGINS = (
    "https://agents.linhngo.dev",
    "https://agents-linhngo-dev.web.app",
)

# ---------------------------------------------------------------------------
# Hosted-shell launcher constants (issue #57)
# ---------------------------------------------------------------------------
from pathlib import Path as _Path

_HOSTED_LAUNCHER_DIR = _Path.home() / ".copilot" / "bin"
_HOSTED_LAUNCHER_DEFAULT_PORT = 8765
_HOSTED_UI_URL = "https://agents.linhngo.dev"
_HOSTED_LAUNCHER_BACKEND_NAME = "browse-hosted"  # .cmd appended on Windows


def _hosted_launcher_script_path() -> _Path:
    """Return the platform-appropriate backend launcher script path."""
    if os.name == "nt":
        return _HOSTED_LAUNCHER_DIR / "browse-hosted.cmd"
    return _HOSTED_LAUNCHER_DIR / _HOSTED_LAUNCHER_BACKEND_NAME


def _hosted_launcher_url_path() -> _Path:
    """Return the Windows .url shortcut path (Windows only)."""
    return _HOSTED_LAUNCHER_DIR / "browse-hosted.url"


def _hosted_launcher_script_content() -> str:
    """Return the backend launcher script content for the current platform."""
    port = _HOSTED_LAUNCHER_DEFAULT_PORT
    if os.name == "nt":
        return (
            "@echo off\r\n"
            "rem browse-hosted.cmd — Start browse.py with --hosted-bootstrap\r\n"
            "rem Run: browse-hosted  (optionally: browse-hosted --token <token>)\r\n"
            f'python "%USERPROFILE%\\.copilot\\tools\\browse.py"'
            f" --hosted-bootstrap --port {port} %*\r\n"
        )
    return (
        "#!/bin/sh\n"
        "# browse-hosted — Start browse.py with --hosted-bootstrap\n"
        "# Run: browse-hosted  (optionally: browse-hosted --token <token>)\n"
        f'exec python3 "$HOME/.copilot/tools/browse.py"'
        f' --hosted-bootstrap --port {port} "$@"\n'
    )


def _hosted_launcher_url_content() -> str:
    """Return the Windows .url Internet shortcut content."""
    return f"[InternetShortcut]\r\nURL={_HOSTED_UI_URL}\r\n"


# ---------------------------------------------------------------------------
# Desktop .lnk shortcut helpers (Windows, issue #57)
# ---------------------------------------------------------------------------

_DESKTOP_LNK_BACKEND_NAME = "Browse Backend.lnk"
_DESKTOP_LNK_UI_NAME = "Browse UI.lnk"

# Common msedge.exe installation paths (checked in order; first match wins).
_EDGE_CANDIDATE_PATHS = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]


def _windows_desktop_path() -> _Path:
    """Return the Windows user Desktop path (~\\Desktop)."""
    return _Path.home() / "Desktop"


def _browse_backend_lnk_path() -> _Path:
    """Return the path for the Browse Backend desktop shortcut."""
    return _windows_desktop_path() / _DESKTOP_LNK_BACKEND_NAME


def _browse_ui_lnk_path() -> _Path:
    """Return the path for the Browse UI desktop shortcut."""
    return _windows_desktop_path() / _DESKTOP_LNK_UI_NAME


def _lnk_powershell_script(has_working_dir: bool = False) -> str:
    """Return a PowerShell one-liner that creates a .lnk via WScript.Shell COM.

    All variable data (_LNK_PATH, _LNK_TARGET, _LNK_ARGS, _LNK_DESC,
    _LNK_WORKDIR) is passed through environment variables to avoid quoting and
    injection issues in the PowerShell -Command string.
    """
    parts = [
        "$ws = New-Object -ComObject WScript.Shell",
        "$sc = $ws.CreateShortcut($env:_LNK_PATH)",
        "$sc.TargetPath = $env:_LNK_TARGET",
        "$sc.Arguments = $env:_LNK_ARGS",
        "$sc.Description = $env:_LNK_DESC",
    ]
    if has_working_dir:
        parts.append("$sc.WorkingDirectory = $env:_LNK_WORKDIR")
    parts.append("$sc.Save()")
    return "; ".join(parts)


def _create_lnk_via_powershell(
    lnk_path: _Path,
    target: str,
    arguments: str,
    description: str,
    working_dir: str = "",
) -> None:
    """Create a Windows .lnk shortcut via PowerShell WScript.Shell COM.

    Passes all variable data through environment variables to avoid quoting and
    injection issues.  Raises RuntimeError if PowerShell exits non-zero.

    Only callable on Windows (os.name == 'nt').
    Security: callers must ensure *arguments* contains no browser security-bypass
    flags (``--disable-web-security``, ``--allow-insecure-localhost``, etc.).
    """
    if os.name != "nt":
        raise RuntimeError("_create_lnk_via_powershell is Windows-only")

    import subprocess

    ps_cmd = _lnk_powershell_script(has_working_dir=bool(working_dir))

    env = os.environ.copy()
    env["_LNK_PATH"] = str(lnk_path)
    env["_LNK_TARGET"] = target
    env["_LNK_ARGS"] = arguments
    env["_LNK_DESC"] = description
    if working_dir:
        env["_LNK_WORKDIR"] = working_dir

    result = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_cmd],
        capture_output=True,
        text=True,
        env=env,
    )
    if result.returncode != 0:
        raise RuntimeError(f"PowerShell .lnk creation failed (rc={result.returncode}): {result.stderr.strip()}")


def _find_edge_path() -> str:
    """Return the first msedge.exe path found at standard install locations.

    Returns an empty string when Edge is not installed or on non-Windows systems.
    Callers should treat an empty return as 'Edge not available'.
    """
    for p in _EDGE_CANDIDATE_PATHS:
        if _Path(p).is_file():
            return p
    return ""


def _install_desktop_shortcuts(quiet: bool = False) -> int:
    """Create Browse Backend.lnk and Browse UI.lnk on the Windows Desktop.

    Both shortcuts are created via PowerShell WScript.Shell COM.  No browser
    security-bypass flags are ever passed to either shortcut target.

    Returns the count of shortcuts created (0, 1, or 2).  Already-existing
    shortcuts are skipped (idempotent).  No-op on non-Windows (returns 0).

    Edge caveat: Browse UI.lnk requires msedge.exe at a standard install path.
    If Edge is absent, that shortcut is skipped and a warning is printed.
    Actual launch behaviour must be manually verified on Windows — this
    macOS/Linux environment cannot exercise PowerShell or the COM object.
    """
    if os.name != "nt":
        if not quiet:
            print("  [desktop-shortcuts] Skipped — desktop .lnk shortcuts are Windows-only.")
        return 0

    tools_dir = str(_Path.home() / ".copilot" / "tools")
    browse_script = str(_Path.home() / ".copilot" / "tools" / "browse.py")
    created = 0

    # ── Browse Backend.lnk ──────────────────────────────────────────────────
    backend_lnk = _browse_backend_lnk_path()
    if backend_lnk.is_file():
        if not quiet:
            print(f"  [desktop-shortcuts] Browse Backend.lnk already exists: {backend_lnk}")
    else:
        try:
            _create_lnk_via_powershell(
                lnk_path=backend_lnk,
                target="python.exe",
                arguments=(f'"{browse_script}" --hosted-bootstrap --port {_HOSTED_LAUNCHER_DEFAULT_PORT}'),
                description="Start browse.py hosted-bootstrap backend (issue #57)",
                working_dir=tools_dir,
            )
            created += 1
            if not quiet:
                print(f"  [desktop-shortcuts] created: {backend_lnk}")
        except Exception as exc:
            if not quiet:
                print(f"  [desktop-shortcuts] WARNING: could not create Browse Backend.lnk: {exc}")

    # ── Browse UI.lnk ───────────────────────────────────────────────────────
    edge_path = _find_edge_path()
    if not edge_path:
        if not quiet:
            print(
                "  [desktop-shortcuts] WARNING: msedge.exe not found at standard paths; "
                "Browse UI.lnk not created. "
                "Install Edge or create the shortcut manually "
                f"(target: msedge.exe, argument: {_HOSTED_UI_URL})."
            )
    else:
        ui_lnk = _browse_ui_lnk_path()
        if ui_lnk.is_file():
            if not quiet:
                print(f"  [desktop-shortcuts] Browse UI.lnk already exists: {ui_lnk}")
        else:
            try:
                _create_lnk_via_powershell(
                    lnk_path=ui_lnk,
                    target=edge_path,
                    arguments=_HOSTED_UI_URL,
                    description=("Open hosted browse UI in Microsoft Edge default profile (issue #57)"),
                    working_dir="",
                )
                created += 1
                if not quiet:
                    print(f"  [desktop-shortcuts] created: {ui_lnk}")
            except Exception as exc:
                if not quiet:
                    print(f"  [desktop-shortcuts] WARNING: could not create Browse UI.lnk: {exc}")

    return created


def _uninstall_desktop_shortcuts(quiet: bool = False) -> int:
    """Remove Browse Backend.lnk and Browse UI.lnk from the Windows Desktop.

    Returns the count of shortcuts removed.  No-op on non-Windows systems.
    """
    if os.name != "nt":
        return 0

    removed = 0
    for lnk_path in (_browse_backend_lnk_path(), _browse_ui_lnk_path()):
        if not lnk_path.is_file():
            continue
        try:
            lnk_path.unlink()
            removed += 1
            if not quiet:
                print(f"  [desktop-shortcuts] removed: {lnk_path}")
        except Exception as exc:
            if not quiet:
                print(f"  [desktop-shortcuts] could not remove {lnk_path}: {exc}")

    return removed


def install_browse_hosted_launcher(quiet: bool = False) -> bool:
    """Install the hosted-shell backend launcher, (Windows) UI shortcut, and desktop shortcuts.

    Creates:
      - ~/.copilot/bin/browse-hosted          (POSIX executable)
      - ~/.copilot/bin/browse-hosted.cmd      (Windows CMD script)
      - ~/.copilot/bin/browse-hosted.url      (Windows Internet shortcut, Windows only)
      - ~/Desktop/Browse Backend.lnk         (Windows desktop shortcut, Windows only)
      - ~/Desktop/Browse UI.lnk              (Windows desktop shortcut for Edge, Windows only)

    Desktop .lnk shortcuts are created via PowerShell WScript.Shell COM.  No
    browser security-bypass flags are used in any shortcut target.

    Idempotent — safe to call repeatedly.
    Returns True if any file was created or updated.
    """
    import stat

    _HOSTED_LAUNCHER_DIR.mkdir(parents=True, exist_ok=True)
    changed = False

    # Backend launcher script
    script = _hosted_launcher_script_path()
    new_content = _hosted_launcher_script_content()
    existing = script.read_bytes().decode("utf-8") if script.is_file() else None
    if existing == new_content:
        if not quiet:
            print(f"  [hosted-launcher] backend script already up to date: {script}")
    else:
        _path_atomic_write(script, new_content)
        if os.name != "nt":
            script.chmod(script.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        verb = "updated" if existing is not None else "created"
        if not quiet:
            print(f"  [hosted-launcher] backend script {verb}: {script}")
        changed = True

    # Windows-only: .url Internet shortcut
    if os.name == "nt":
        url_path = _hosted_launcher_url_path()
        url_content = _hosted_launcher_url_content()
        existing_url = url_path.read_bytes().decode("utf-8") if url_path.is_file() else None
        if existing_url == url_content:
            if not quiet:
                print(f"  [hosted-launcher] UI shortcut already up to date: {url_path}")
        else:
            _path_atomic_write(url_path, url_content)
            verb = "updated" if existing_url is not None else "created"
            if not quiet:
                print(f"  [hosted-launcher] UI shortcut {verb}: {url_path}")
            changed = True

    # Windows-only: desktop .lnk shortcuts (Browse Backend.lnk + Browse UI.lnk)
    if os.name == "nt":
        lnk_created = _install_desktop_shortcuts(quiet=quiet)
        if lnk_created:
            changed = True

    if not quiet:
        print(
            f"  [hosted-launcher] Open {_HOSTED_UI_URL} to connect the hosted shell.",
        )
        print(
            f"  [hosted-launcher] Run 'browse-hosted' (or browse-hosted.cmd) to start"
            f" the backend on port {_HOSTED_LAUNCHER_DEFAULT_PORT}.",
        )
        if os.name == "nt":
            print(
                "  [hosted-launcher] Desktop shortcuts: double-click 'Browse Backend.lnk' "
                "to start the backend, 'Browse UI.lnk' to open the hosted UI in Edge."
            )

    return changed


def uninstall_browse_hosted_launcher(quiet: bool = False) -> int:
    """Remove the hosted-shell launcher script, (Windows) UI shortcut, and desktop shortcuts.

    Returns the number of items removed.
    """
    removed = 0

    for path in (
        _hosted_launcher_script_path(),
        _hosted_launcher_url_path() if os.name == "nt" else None,
    ):
        if path is None or not path.is_file():
            continue
        try:
            path.unlink()
            removed += 1
            if not quiet:
                print(f"  [hosted-launcher] removed: {path}")
        except Exception as exc:
            if not quiet:
                print(f"  [hosted-launcher] could not remove {path}: {exc}")

    # Windows-only: remove desktop .lnk shortcuts
    if os.name == "nt":
        removed += _uninstall_desktop_shortcuts(quiet=quiet)

    if not removed and not quiet:
        print("  [hosted-launcher] nothing to remove — launcher was not installed")

    return removed


def _path_atomic_write(path: _Path, content: str, encoding: str = "utf-8") -> None:
    """Write content to path atomically via a .tmp sibling + os.replace."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_bytes(content.encode(encoding))
        os.replace(str(tmp), str(path))
    except Exception:
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass
        raise


def _configure_hosted_bootstrap_cors() -> tuple[list[str], list[str]]:
    """Append hosted-shell origins to BROWSE_CORS_ORIGINS and return (added, all)."""
    existing_cors = os.environ.get("BROWSE_CORS_ORIGINS", "").strip()
    origins = [o.strip().rstrip("/") for o in existing_cors.split(",") if o.strip()]
    added: list[str] = []
    for origin in HOSTED_BOOTSTRAP_ORIGINS:
        if origin not in origins:
            origins.append(origin)
            added.append(origin)
    os.environ["BROWSE_CORS_ORIGINS"] = ",".join(origins)
    return added, origins


def _token_display_value(token: str, token_env_source: str) -> str:
    """Return a token display value that redacts env-sourced secrets."""
    if token and token_env_source:
        return f"<set in ${token_env_source}; not printed>"
    return token


def _start_cloudflared(local_base_url: str, token: str, token_env_source: str = ""):
    """Attempt to start a cloudflared quick tunnel in a daemon thread.

    *local_base_url* must be ``http://host:port`` (no trailing slash, no token
    query string). When the tunnel yields a public URL it is printed to stdout
    in web-app-friendly form: the base URL is printed separately from the token
    so the operator can paste them into distinct host-profile fields. Any
    failure is printed to stderr; the local server is never blocked or killed.

    Returns a best-effort cleanup callback that terminates the spawned
    ``cloudflared`` subprocess.
    """
    import re
    import subprocess
    import threading
    import time
    import urllib.error
    import urllib.request

    _URL_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")
    stop_event = threading.Event()
    state = {"proc": None}

    def _terminate_proc() -> None:
        proc = state.get("proc")
        if proc is None:
            return
        try:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=3)
        except Exception:
            pass

    def _cleanup() -> None:
        stop_event.set()
        _terminate_proc()

    def _probe_public_url(public_root: str) -> tuple[bool, str]:
        probe_url = f"{public_root}/api/operator/capabilities"
        headers = {}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        deadline = time.monotonic() + 15
        last_error = "timed out"
        # Use an opener with an explicit empty ProxyHandler so tunnel/capability
        # probes are not silently routed through HTTP(S)_PROXY env vars.
        no_proxy_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        while time.monotonic() < deadline and not stop_event.is_set():
            req = urllib.request.Request(probe_url, headers=headers)
            try:
                with no_proxy_opener.open(req, timeout=3) as resp:
                    status = int(getattr(resp, "status", 200) or 200)
                    if 200 <= status < 300:
                        return True, ""
                    last_error = f"HTTP {status}"
            except urllib.error.HTTPError as exc:
                last_error = f"HTTP {exc.code}"
            except Exception as exc:
                last_error = str(exc)
            stop_event.wait(1)
        return False, last_error

    def _run() -> None:
        proc = None
        try:
            proc = subprocess.Popen(
                ["cloudflared", "tunnel", "--url", local_base_url],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
            state["proc"] = proc
        except FileNotFoundError:
            print(
                "cloudflared: not found — serving local URL only",
                file=sys.stderr,
                flush=True,
            )
            return
        except Exception as exc:
            print(f"cloudflared: failed to start — {exc}", file=sys.stderr, flush=True)
            return

        found = False
        probe_failed = False
        output_tail: list[str] = []
        try:
            for line in proc.stdout:  # type: ignore[union-attr]
                if stop_event.is_set():
                    return
                clean_line = (line or "").strip()
                if clean_line:
                    output_tail.append(clean_line)
                    output_tail = output_tail[-5:]
                m = _URL_RE.search(line)
                if m:
                    public_root = m.group(0)
                    reachable, probe_error = _probe_public_url(public_root)
                    if not reachable:
                        probe_failed = True
                        print(
                            "cloudflared: quick tunnel URL stayed unreachable "
                            f"({probe_error}) — serving local URL only",
                            file=sys.stderr,
                            flush=True,
                        )
                        return
                    print(f"Public URL:  {public_root}/", flush=True)
                    if token:
                        print(
                            f"Public Token: {_token_display_value(token, token_env_source)}",
                            flush=True,
                        )
                    found = True
                    stop_event.wait()
                    return
        except Exception as exc:
            print(f"cloudflared: error reading output — {exc}", file=sys.stderr, flush=True)
            return
        finally:
            if not found and not probe_failed and not stop_event.is_set():
                msg = " | ".join(output_tail) or "(no output)"
                print(
                    f"cloudflared: exited without yielding a public URL — {msg}",
                    file=sys.stderr,
                    flush=True,
                )
            _terminate_proc()

    t = threading.Thread(target=_run, daemon=True, name="cloudflared-tunnel")
    t.start()
    return _cleanup


def main() -> None:
    import argparse
    import urllib.parse
    from http.server import ThreadingHTTPServer
    from pathlib import Path

    p = argparse.ArgumentParser(
        description="Hindsight local web UI (read-only)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--port", type=int, default=0, help="Port (0 = random free port)")
    p.add_argument("--token", default="", help="Auth token")
    p.add_argument(
        "--token-env",
        metavar="VARNAME",
        default="",
        help="Read auth token from this environment variable",
    )
    p.add_argument("--db", default=str(_DEFAULT_DB), help="Path to knowledge.db")
    p.add_argument(
        "--no-tunnel",
        action="store_true",
        default=False,
        help="Disable automatic cloudflared tunnel startup",
    )
    p.add_argument(
        "--hosted-bootstrap",
        action="store_true",
        default=False,
        help=(
            "Bootstrap for hosted-shell origin access: configure the canonical hosted origin "
            "as a CORS-allowed origin (unless BROWSE_CORS_ORIGINS is already set), "
            "and print actionable startup guidance for token-based auth."
        ),
    )
    p.add_argument(
        "--print-pairing-qr",
        action="store_true",
        default=False,
        help=(
            "After startup, print a browse://connect?ticket=... pairing URL and attempt to "
            "render it as a QR code in the terminal (requires 'qrcode' package for scannable "
            "output; falls back to URL-in-box display). "
            "Backward compatible: the existing manual URL+token flow continues to work."
        ),
    )
    p.add_argument(
        "--static-pairing",
        action="store_true",
        default=False,
        help=(
            "Enable a static, reusable, read-only pairing slot for demos and store review. "
            "The slot is active for the current daemon lifetime only (opt-in, off by default). "
            "Surfaced in the UI as 'Demo mode'. Audit logs distinguish static vs operator access."
        ),
    )
    p.add_argument(
        "--terminate-static",
        action="store_true",
        default=False,
        help=(
            "Terminate the active static pairing slot on the currently running browse.py server "
            "by calling DELETE /api/operator/pairing/static on the local server. "
            "Requires --port (and --token if the server requires auth) to match the running instance. "
            "Exits after the API call — does NOT start a server."
        ),
    )
    p.add_argument(
        "--broker-mode",
        metavar="BACKEND",
        default="",
        choices=["telegram", "discord", "ably", "slack"],
        help=(
            "Start in outbound-only broker mode instead of the local HTTP server. "
            "No inbound port is opened. "
            "BACKEND='telegram': long-polls api.telegram.org using "
            "BROWSE_BROKER_TELEGRAM_TOKEN and BROWSE_BROKER_AUTHORIZED_USER_ID env vars. "
            "BACKEND='discord': HTTP-polls discord.com REST API using "
            "BROWSE_BROKER_DISCORD_TOKEN, BROWSE_BROKER_DISCORD_CHANNEL_ID, and "
            "BROWSE_BROKER_DISCORD_AUTHORIZED_USER_ID env vars (~2s poll latency). "
            "BACKEND='ably': HTTP REST-polls rest.ably.io using "
            "BROWSE_BROKER_ABLY_API_KEY env var (optionally BROWSE_BROKER_ABLY_CHANNEL_IN, "
            "BROWSE_BROKER_ABLY_CHANNEL_OUT, BROWSE_BROKER_ABLY_AUTHORIZED_CLIENT_ID). "
            "BACKEND='slack': HTTP-polls slack.com conversations.history using "
            "BROWSE_BROKER_SLACK_BOT_TOKEN, BROWSE_BROKER_SLACK_CHANNEL_ID, and "
            "BROWSE_BROKER_SLACK_AUTHORIZED_USER_ID env vars (~2s poll latency). "
            "All backends support read-only commands: /status /search /briefing /recent /help. "
            "Discord/Ably/Slack require live credentials for end-to-end use. "
            "See docs/OPERATOR-PLAYBOOK.md § Broker Mode for startup instructions."
        ),
    )
    p.add_argument(
        "--install-launcher",
        action="store_true",
        default=False,
        help=(
            "Install the hosted-shell launcher: creates browse-hosted (POSIX) or "
            "browse-hosted.cmd (Windows) in ~/.copilot/bin/ — a one-shot wrapper that "
            "starts browse.py --hosted-bootstrap --port 8765. "
            "On Windows also creates browse-hosted.url (Internet shortcut to "
            f"{_HOSTED_UI_URL}). "
            "Exits after install — does NOT start a server. "
            "See docs/OPERATOR-PLAYBOOK.md § Hosted Launcher for details."
        ),
    )
    p.add_argument(
        "--uninstall-launcher",
        action="store_true",
        default=False,
        help=(
            "Remove the hosted-shell launcher files installed by --install-launcher "
            "(browse-hosted / browse-hosted.cmd and browse-hosted.url). "
            "Exits after removal — does NOT start a server."
        ),
    )
    # ── Debug-log storage flags (WBS-103) ─────────────────────────────────────
    p.add_argument(
        "--debug-log",
        action="store_true",
        default=False,
        help=(
            "Enable debug-log storage and the /api/debug-log/healthz probe route. "
            "Disabled by default.  Also enabled via BROWSE_DEBUG_LOG_ENABLED=1. "
            "Debug routes require Bearer or cookie auth — ?token= is rejected. "
            "See docs/DEBUG-LOG-CONTRACT.md for the full contract."
        ),
    )
    p.add_argument(
        "--debug-log-dir",
        metavar="DIR",
        default="",
        help=(
            "Directory for the debug-log SQLite DB (default: "
            "~/.copilot/operator-console/debug-log/). "
            "Also configurable via BROWSE_DEBUG_LOG_DIR."
        ),
    )
    p.add_argument(
        "--debug-log-max-age-seconds",
        type=int,
        default=0,
        metavar="SECONDS",
        help="Max age in seconds for debug-log events (default: 86400). Also via BROWSE_DEBUG_LOG_MAX_AGE_S.",
    )
    p.add_argument(
        "--debug-log-max-bytes",
        type=int,
        default=0,
        metavar="BYTES",
        help="Max total size in bytes for debug-log events (default: 52428800). Also via BROWSE_DEBUG_LOG_MAX_BYTES.",
    )
    p.add_argument(
        "--debug-log-retention-interval",
        type=int,
        default=0,
        metavar="SECONDS",
        help="Interval in seconds between retention runs (default: 300). "
        "Also via BROWSE_DEBUG_LOG_RETENTION_INTERVAL_S.",
    )
    p.add_argument(
        "--debug-log-ephemeral",
        action="store_true",
        default=False,
        help=("Remove the debug-log DB and WAL/SHM files on shutdown. Also via BROWSE_DEBUG_LOG_EPHEMERAL=1."),
    )
    args = p.parse_args()

    # --install-launcher: one-shot install — exit after writing launcher files.
    if args.install_launcher:
        install_browse_hosted_launcher(quiet=False)
        return

    # --uninstall-launcher: one-shot removal — exit after removing launcher files.
    if args.uninstall_launcher:
        uninstall_browse_hosted_launcher(quiet=False)
        return

    env_token = ""
    if args.token_env:
        env_token = os.environ.get(args.token_env, "")
    token = env_token or args.token
    token_env_source = args.token_env if env_token else ""

    # --broker-mode: start outbound-only control-bus loop (no inbound port).
    # This branch exits when the broker loop returns (Ctrl-C or fatal error) —
    # it does NOT start the local HTTP server.
    if args.broker_mode == "telegram":
        # NOTE: _open_db is imported at module level — do NOT re-import it here.
        # A local `from browse.core.fts import _open_db` inside main() would shadow
        # the module-level name with a local variable for the *entire* function,
        # causing UnboundLocalError on the non-broker path (issue regression).
        from browse.broker.telegram import TelegramBroker

        tg_token = os.environ.get("BROWSE_BROKER_TELEGRAM_TOKEN", "").strip()
        authorized_uid_raw = os.environ.get("BROWSE_BROKER_AUTHORIZED_USER_ID", "").strip()

        if not tg_token:
            print(
                "[broker/telegram] FATAL: BROWSE_BROKER_TELEGRAM_TOKEN is not set.",
                file=sys.stderr,
                flush=True,
            )
            sys.exit(1)
        if not authorized_uid_raw:
            print(
                "[broker/telegram] FATAL: BROWSE_BROKER_AUTHORIZED_USER_ID is not set.",
                file=sys.stderr,
                flush=True,
            )
            sys.exit(1)
        try:
            authorized_uid = int(authorized_uid_raw)
        except ValueError:
            print(
                f"[broker/telegram] FATAL: BROWSE_BROKER_AUTHORIZED_USER_ID must be an "
                f"integer, got: {authorized_uid_raw!r}",
                file=sys.stderr,
                flush=True,
            )
            sys.exit(1)

        db_path = Path(args.db)
        if not db_path.exists():
            print(
                f"Warning: DB not found at {db_path} — creating empty DB",
                file=sys.stderr,
            )
            db_path.parent.mkdir(parents=True, exist_ok=True)
        db = _open_db(db_path)

        broker = TelegramBroker(
            db=db,
            token=tg_token,
            authorized_user_id=authorized_uid,
        )
        try:
            broker.run()
        finally:
            db.close()
        return

    if args.broker_mode == "discord":
        from browse.broker.discord import DiscordBroker

        discord_token = os.environ.get("BROWSE_BROKER_DISCORD_TOKEN", "").strip()
        discord_channel = os.environ.get("BROWSE_BROKER_DISCORD_CHANNEL_ID", "").strip()
        discord_uid = os.environ.get("BROWSE_BROKER_DISCORD_AUTHORIZED_USER_ID", "").strip()

        for var, val in [
            ("BROWSE_BROKER_DISCORD_TOKEN", discord_token),
            ("BROWSE_BROKER_DISCORD_CHANNEL_ID", discord_channel),
            ("BROWSE_BROKER_DISCORD_AUTHORIZED_USER_ID", discord_uid),
        ]:
            if not val:
                print(
                    f"[broker/discord] FATAL: {var} is not set.",
                    file=sys.stderr,
                    flush=True,
                )
                sys.exit(1)

        db_path = Path(args.db)
        if not db_path.exists():
            print(f"Warning: DB not found at {db_path} — creating empty DB", file=sys.stderr)
            db_path.parent.mkdir(parents=True, exist_ok=True)
        db = _open_db(db_path)
        broker = DiscordBroker(
            db=db,
            token=discord_token,
            channel_id=discord_channel,
            authorized_user_id=discord_uid,
        )
        try:
            broker.run()
        finally:
            db.close()
        return

    if args.broker_mode == "ably":
        from browse.broker.ably import AblyBroker

        ably_key = os.environ.get("BROWSE_BROKER_ABLY_API_KEY", "").strip()
        if not ably_key:
            print(
                "[broker/ably] FATAL: BROWSE_BROKER_ABLY_API_KEY is not set.",
                file=sys.stderr,
                flush=True,
            )
            sys.exit(1)

        channel_in = os.environ.get("BROWSE_BROKER_ABLY_CHANNEL_IN", "browse-commands").strip()
        channel_out = os.environ.get("BROWSE_BROKER_ABLY_CHANNEL_OUT", "browse-responses").strip()
        authorized_client = os.environ.get("BROWSE_BROKER_ABLY_AUTHORIZED_CLIENT_ID", "operator").strip()

        db_path = Path(args.db)
        if not db_path.exists():
            print(f"Warning: DB not found at {db_path} — creating empty DB", file=sys.stderr)
            db_path.parent.mkdir(parents=True, exist_ok=True)
        db = _open_db(db_path)
        broker = AblyBroker(
            db=db,
            api_key=ably_key,
            channel_in=channel_in,
            channel_out=channel_out,
            authorized_client_id=authorized_client,
        )
        try:
            broker.run()
        finally:
            db.close()
        return

    if args.broker_mode == "slack":
        from browse.broker.slack import SlackBroker

        slack_token = os.environ.get("BROWSE_BROKER_SLACK_BOT_TOKEN", "").strip()
        slack_channel = os.environ.get("BROWSE_BROKER_SLACK_CHANNEL_ID", "").strip()
        slack_uid = os.environ.get("BROWSE_BROKER_SLACK_AUTHORIZED_USER_ID", "").strip()

        for var, val in [
            ("BROWSE_BROKER_SLACK_BOT_TOKEN", slack_token),
            ("BROWSE_BROKER_SLACK_CHANNEL_ID", slack_channel),
            ("BROWSE_BROKER_SLACK_AUTHORIZED_USER_ID", slack_uid),
        ]:
            if not val:
                print(
                    f"[broker/slack] FATAL: {var} is not set.",
                    file=sys.stderr,
                    flush=True,
                )
                sys.exit(1)

        db_path = Path(args.db)
        if not db_path.exists():
            print(f"Warning: DB not found at {db_path} — creating empty DB", file=sys.stderr)
            db_path.parent.mkdir(parents=True, exist_ok=True)
        db = _open_db(db_path)
        broker = SlackBroker(
            db=db,
            bot_token=slack_token,
            channel_id=slack_channel,
            authorized_user_id=slack_uid,
        )
        try:
            broker.run()
        finally:
            db.close()
        return

    # --terminate-static: call DELETE /api/operator/pairing/static on the running server.
    # This is a one-shot command that exits after the API call — does not start a server.
    if args.terminate_static:
        import json as _json_ts
        import urllib.request

        url = f"http://127.0.0.1:{args.port}/api/operator/pairing/static"
        req = urllib.request.Request(url, method="DELETE")
        if token:
            req.add_header("Authorization", f"Bearer {token}")
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = _json_ts.loads(resp.read())
                if data.get("terminated"):
                    print("[terminate-static] Static slot terminated successfully.", flush=True)
                else:
                    print("[terminate-static] No active static slot found on the running server.", flush=True)
        except Exception as exc:
            print(f"[terminate-static] Error calling server: {exc}", file=sys.stderr, flush=True)
            sys.exit(1)
        return

    # --hosted-bootstrap: configure CORS for the hosted shell origins while
    # preserving any operator-provided allowlist entries.
    if args.hosted_bootstrap:
        existing_cors = os.environ.get("BROWSE_CORS_ORIGINS", "").strip()
        added_origins, all_origins = _configure_hosted_bootstrap_cors()
        if not existing_cors:
            print(
                "[hosted-bootstrap] CORS allowlist set to: " + ", ".join(all_origins),
                flush=True,
            )
        elif added_origins:
            print(
                "[hosted-bootstrap] Appended hosted origins to existing CORS allowlist: " + ", ".join(added_origins),
                flush=True,
            )
        else:
            print(
                "[hosted-bootstrap] Hosted origins already in CORS allowlist — no change",
                flush=True,
            )

    db_path = Path(args.db)
    if not db_path.exists():
        print(
            f"Warning: DB not found at {db_path} — creating empty DB",
            file=sys.stderr,
        )
        db_path.parent.mkdir(parents=True, exist_ok=True)

    db = _open_db(db_path)
    HandlerClass = _make_handler_class(db, token)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), HandlerClass)
    host, port = server.server_address

    if token and not token_env_source:
        local_url = f"http://{host}:{port}/?token={urllib.parse.quote(token)}"
    else:
        local_url = f"http://{host}:{port}/"

    print(f"Local URL:   {local_url}", flush=True)
    print(f"Bound:       {host}:{port}", flush=True)

    if args.hosted_bootstrap:
        discovery_url = f"http://{host}:{port}/.well-known/browse-host"
        print("", flush=True)
        print("[hosted-bootstrap] Startup guidance:", flush=True)
        print(f"  Discovery endpoint: {discovery_url}", flush=True)
        if token:
            print(
                "  Auth:              Bearer token required — pass as Authorization: Bearer <token>",
                flush=True,
            )
            print(
                f"  Token:             {_token_display_value(token, token_env_source)}",
                flush=True,
            )
            token_hint = f"the token from ${token_env_source}" if token_env_source else "the token above"
        else:
            print(
                "  Auth:              No token set — set --token or --token-env for production use",
                flush=True,
            )
            token_hint = "a production token"
        print(
            f"  Configure the hosted shell with host=http://{host}:{port} and {token_hint}.",
            flush=True,
        )
        print("", flush=True)

    local_base_url = f"http://{host}:{port}"

    # --static-pairing: activate the read-only demo slot before serving.
    if args.static_pairing:
        from browse.core.pairing import create_static_slot, render_terminal_qr

        static_slot = create_static_slot(local_base_url)
        print("", flush=True)
        print("[static-pairing] Demo / read-only pairing slot activated.", flush=True)
        print(
            "[static-pairing] UI will show a 'Demo mode' badge for connections via this slot.",
            flush=True,
        )
        print(
            "[static-pairing] Audit log will record session_kind=static for these requests.",
            flush=True,
        )
        print("", flush=True)
        print("[static-pairing] Static pairing URL:", flush=True)
        render_terminal_qr(static_slot["ticket_url"])
        print("", flush=True)

    # --print-pairing-qr: print the operator pairing ticket.
    if args.print_pairing_qr:
        from browse.core.pairing import create_pairing_ticket, render_terminal_qr

        pairing_url = create_pairing_ticket(token, local_base_url)
        print("", flush=True)
        print("[pairing-qr] Pairing URL (valid 5 minutes):", flush=True)
        render_terminal_qr(pairing_url)
        if token:
            print(
                "[pairing-qr] Scan this QR or paste the browse:// URL in the hosted UI → Add host.",
                flush=True,
            )
        else:
            print(
                "[pairing-qr] Open-auth backend — no token required after pairing.",
                flush=True,
            )
        print("", flush=True)

    stop_tunnel = lambda: None
    if not args.no_tunnel:
        stop_tunnel = _start_cloudflared(local_base_url, token, token_env_source=token_env_source)

    # ── Debug-log storage initialization (WBS-103) ────────────────────────────
    # Check both CLI flag and env variable; set env so is_enabled() returns True.
    _dl_enabled = args.debug_log or os.environ.get("BROWSE_DEBUG_LOG_ENABLED", "").strip().lower() in (
        "1",
        "true",
        "yes",
    )
    if _dl_enabled:
        os.environ["BROWSE_DEBUG_LOG_ENABLED"] = "1"
        import browse.routes.debug_log  # noqa: F401 — registers /api/debug-log/healthz
        from browse.core.debug_log_storage import (  # noqa: PLC0415
            init_storage as _dl_init,
        )
        from browse.core.debug_log_storage import (
            start_retention_thread as _dl_start,
        )

        _dl_db_path = Path(args.debug_log_dir) / "debug-log.db" if args.debug_log_dir else None
        # init_storage raises OSError/sqlite3.OperationalError on failure (no silent fallback)
        _dl_init(
            db_path=_dl_db_path,
            max_age_s=args.debug_log_max_age_seconds or None,
            max_bytes=args.debug_log_max_bytes or None,
            retention_interval_s=args.debug_log_retention_interval or None,
            ephemeral=args.debug_log_ephemeral or None,
        )
        _dl_start()
        print("[debug-log] storage initialized; /api/debug-log/healthz registered.", flush=True)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop_tunnel()
        server.server_close()
        db.close()
        # Explicit shutdown — NO atexit, NO signal handler (WBS-103)
        if _dl_enabled:
            from browse.core.debug_log_storage import shutdown_storage as _dl_shutdown  # noqa: PLC0415

            _dl_shutdown()
