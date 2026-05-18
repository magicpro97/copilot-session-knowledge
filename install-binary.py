#!/usr/bin/env python3
"""
install-binary.py — Download and install sk binary from GitHub Releases.

Cross-platform installer (Python 3.6+). Works on Windows, Linux, macOS.

Usage:
    python install-binary.py                    # Install latest version
    python install-binary.py --version v1.1.0   # Install specific version
    python install-binary.py --dir /usr/local/bin  # Custom install directory

Alternative (curl one-liner, requires Python on target):
    curl -fsSL https://raw.githubusercontent.com/magicpro97/copilot-session-knowledge/main/install-binary.py | python3
"""

import hashlib
import io
import json
import os
import platform
import shutil
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

if os.name == "nt":
    for _stream in (sys.stdout, sys.stderr):
        if hasattr(_stream, "reconfigure"):
            _stream.reconfigure(encoding="utf-8", errors="replace")

REPO = "magicpro97/copilot-session-knowledge"
BINARY = "sk"
API_URL = f"https://api.github.com/repos/{REPO}/releases/latest"


def detect_platform() -> tuple[str, str]:
    """Detect OS and architecture."""
    system = platform.system().lower()
    machine = platform.machine().lower()

    os_map = {"linux": "linux", "darwin": "darwin", "windows": "windows"}
    arch_map = {
        "x86_64": "x64",
        "amd64": "x64",
        "aarch64": "arm64",
        "arm64": "arm64",
    }

    os_name = os_map.get(system)
    arch = arch_map.get(machine)

    if not os_name:
        print(f"Error: Unsupported OS: {platform.system()}", file=sys.stderr)
        sys.exit(1)
    if not arch:
        print(f"Error: Unsupported architecture: {platform.machine()}", file=sys.stderr)
        sys.exit(1)

    return os_name, arch


def get_latest_version() -> str:
    """Fetch latest release tag from GitHub API."""
    try:
        req = urllib.request.Request(API_URL, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode())
            return data["tag_name"]
    except Exception as e:
        print(f"Error: Could not determine latest version: {e}", file=sys.stderr)
        sys.exit(1)


def download_file(url: str, dest: Path) -> None:
    """Download a file from URL."""
    try:
        urllib.request.urlretrieve(url, str(dest))
    except Exception as e:
        print(f"Error: Download failed: {e}", file=sys.stderr)
        sys.exit(1)


def _check_safe_member(member_name: str, dest_dir: Path) -> Path:
    """Validate an archive member path to reject traversal attacks (WBS-003).

    Returns the resolved target Path on success.
    Raises ValueError when the path would escape dest_dir.
    """
    if not member_name:
        raise ValueError("Unsafe archive member: empty name")
    if "\x00" in member_name:
        raise ValueError(f"Unsafe archive member: null byte in path {member_name!r}")
    dest_resolved = dest_dir.resolve()
    mem_path = Path(member_name)
    if mem_path.is_absolute():
        raise ValueError(f"Unsafe archive member: absolute path {member_name!r}")
    target = (dest_dir / member_name).resolve()
    try:
        target.relative_to(dest_resolved)
    except ValueError as err:
        raise ValueError(f"Unsafe archive member: path traversal detected in {member_name!r}") from err
    return target


def verify_checksum(file_path: Path, checksum_url: str) -> bool:
    """Verify SHA-256 checksum (WBS-009: hard-fail when sidecar absent).

    Returns True only when checksum is verified.
    Returns False (hard-fail) when sidecar is unavailable or hash mismatches,
    unless SK_SKIP_CHECKSUM=1 is set (escape hatch, prints a warning).
    On mismatch the archive is deleted to avoid installation of tampered binaries.
    """
    skip = os.environ.get("SK_SKIP_CHECKSUM", "").strip() == "1"
    try:
        req = urllib.request.Request(checksum_url)
        with urllib.request.urlopen(req, timeout=10) as resp:
            expected = resp.read().decode().strip().split()[0].lower()
    except Exception:
        if skip:
            print("  Warning: Checksum sidecar not available (SK_SKIP_CHECKSUM=1 bypass active)", file=sys.stderr)
            return True
        print(
            "Error: Checksum sidecar unavailable — aborting install.",
            file=sys.stderr,
        )
        print("  Set SK_SKIP_CHECKSUM=1 to bypass (not recommended).", file=sys.stderr)
        return False

    sha256 = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            sha256.update(chunk)
    actual = sha256.hexdigest()

    if expected != actual:
        print("Error: Checksum mismatch!", file=sys.stderr)
        print(f"  Expected: {expected}", file=sys.stderr)
        print(f"  Got:      {actual}", file=sys.stderr)
        try:
            file_path.unlink(missing_ok=True)
        except Exception:
            pass
        return False

    print("  Checksum verified ✓")
    return True


def extract_archive(archive_path: Path, dest_dir: Path, os_name: str) -> None:
    """Extract tar.gz or zip archive safely, rejecting path traversal (WBS-003).

    Validates every member path before extraction.  Raises ValueError on any
    traversal attempt.  Cleans up partially extracted files on any error.
    Supports Python 3.6+ including 3.11/3.12/3.14.
    """
    import tarfile as _tarfile

    archive_name = archive_path.name.lower()
    extracted: list[Path] = []

    def _cleanup():
        for p in extracted:
            try:
                if p.is_file() or p.is_symlink():
                    p.unlink(missing_ok=True)
            except Exception:
                pass

    if archive_name.endswith(".zip") or zipfile.is_zipfile(archive_path):
        try:
            with zipfile.ZipFile(archive_path, "r") as zf:
                for name in zf.namelist():
                    if name.endswith("/"):
                        continue  # directory entry
                    target = _check_safe_member(name, dest_dir)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    data = zf.read(name)
                    target.write_bytes(data)
                    extracted.append(target)
        except Exception:
            _cleanup()
            raise
        return

    if archive_name.endswith((".tar.gz", ".tgz")) or _tarfile.is_tarfile(archive_path):
        try:
            with _tarfile.open(archive_path, "r:*") as tf:
                for member in tf.getmembers():
                    if not member.isfile():
                        continue
                    target = _check_safe_member(member.name, dest_dir)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    src = tf.extractfile(member)
                    if src is None:
                        continue
                    with src:
                        target.write_bytes(src.read())
                    if os_name != "windows":
                        target.chmod(member.mode & 0o777 or 0o644)
                    extracted.append(target)
        except Exception:
            _cleanup()
            raise
        return

    raise ValueError(f"Unsupported archive format: {archive_path.name}")


def setup_path_posix(install_dir: Path) -> None:
    """Suggest PATH setup for POSIX systems."""
    path_dirs = os.environ.get("PATH", "").split(":")
    if str(install_dir) in path_dirs:
        print("✅ sk is ready! Run: sk --version")
        return

    shell = os.environ.get("SHELL", "/bin/sh")
    shell_name = Path(shell).name

    print("⚠️  Add to your PATH:")
    print()
    if shell_name == "zsh":
        print(f"  echo 'export PATH=\"{install_dir}:$PATH\"' >> ~/.zshrc")
        print("  source ~/.zshrc")
    elif shell_name == "bash":
        print(f"  echo 'export PATH=\"{install_dir}:$PATH\"' >> ~/.bashrc")
        print("  source ~/.bashrc")
    elif shell_name == "fish":
        print(f"  fish_add_path {install_dir}")
    else:
        print(f'  export PATH="{install_dir}:$PATH"')
    print()
    print("Then run: sk --version")


def setup_path_windows(install_dir: Path) -> None:
    """Add to PATH on Windows via registry."""
    try:
        import winreg

        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Environment", 0, winreg.KEY_ALL_ACCESS)
        try:
            current_path, _ = winreg.QueryValueEx(key, "Path")
        except FileNotFoundError:
            current_path = ""

        if str(install_dir).lower() not in current_path.lower():
            new_path = f"{install_dir};{current_path}" if current_path else str(install_dir)
            winreg.SetValueEx(key, "Path", 0, winreg.REG_EXPAND_SZ, new_path)
            print(f"✅ Added {install_dir} to user PATH")
            print("   Restart your terminal, then run: sk --version")
        else:
            print("✅ sk is ready! Run: sk --version")
        winreg.CloseKey(key)
    except Exception as e:
        print(f"⚠️  Could not update PATH automatically: {e}")
        print(f"   Manually add {install_dir} to your PATH")


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Install sk binary from GitHub Releases")
    parser.add_argument("--version", help="Version to install (default: latest)")
    parser.add_argument("--dir", help="Install directory (default: ~/.copilot/bin)")
    args = parser.parse_args()

    os_name, arch = detect_platform()

    # SK_LOCAL_ARCHIVE: opt-in override for testing/offline use.
    # When set, the installer uses the given local archive file instead of
    # downloading from GitHub. The default GitHub release flow is unchanged
    # when this variable is not set.
    local_archive = os.environ.get("SK_LOCAL_ARCHIVE")

    # Determine version (not used for download when local_archive is set,
    # but still included in progress messages)
    if local_archive:
        version = args.version or os.environ.get("SK_VERSION") or "local"
    else:
        version = args.version or os.environ.get("SK_VERSION") or get_latest_version()

    # Determine asset name and URLs (used only when not overriding locally)
    ext = "zip" if os_name == "windows" else "tar.gz"
    asset = f"{BINARY}-{os_name}-{arch}.{ext}"
    url = f"https://github.com/{REPO}/releases/download/{version}/{asset}"
    checksum_url = f"{url}.sha256"

    # Determine install directory
    if args.dir:
        install_dir = Path(args.dir)
    elif os.environ.get("SK_INSTALL_DIR"):
        install_dir = Path(os.environ["SK_INSTALL_DIR"])
    else:
        install_dir = Path.home() / ".copilot" / "bin"

    install_dir.mkdir(parents=True, exist_ok=True)

    print(f"Installing sk {version} ({os_name}/{arch})...")

    tmp_dir = Path(tempfile.mkdtemp())
    try:
        if local_archive:
            # Use the provided local archive — no download, no remote checksum.
            archive_path = Path(local_archive)
            if not archive_path.exists():
                print(f"Error: SK_LOCAL_ARCHIVE path not found: {archive_path}", file=sys.stderr)
                return 1
            print(f"  Using local archive: {archive_path}")
            # Honour a sidecar .sha256 file if present (optional integrity check)
            sidecar = Path(str(archive_path) + ".sha256")
            if sidecar.exists():
                expected = sidecar.read_text(encoding="utf-8").strip().split()[0].lower()
                sha256 = hashlib.sha256()
                with open(archive_path, "rb") as f:
                    for chunk in iter(lambda: f.read(8192), b""):
                        sha256.update(chunk)
                if sha256.hexdigest() != expected:
                    print("Error: Local archive checksum mismatch!", file=sys.stderr)
                    return 1
                print("  Local checksum verified ✓")
            # Extract directly into install_dir (archive already at final path)
            extract_archive(archive_path, install_dir, os_name)
        else:
            # Normal GitHub release flow
            print(f"  Downloading: {url}")
            archive_path = tmp_dir / asset
            download_file(url, archive_path)

            # Verify checksum
            if not verify_checksum(archive_path, checksum_url):
                return 1

            # Extract
            extract_archive(archive_path, install_dir, os_name)

        # Make executable (POSIX)
        exe_name = f"{BINARY}.exe" if os_name == "windows" else BINARY
        exe_path = install_dir / exe_name
        if os_name != "windows" and exe_path.exists():
            exe_path.chmod(0o755)

        print(f"  Installed: {exe_path}")
        print()

        # PATH setup
        if os_name == "windows":
            setup_path_windows(install_dir)
        else:
            setup_path_posix(install_dir)

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    return 0


if __name__ == "__main__":
    sys.exit(main())
