#!/usr/bin/env python3
"""skill-catalog.py — Extension Catalog for skills (sk skill catalog/add/remove).

Manages installation and removal of skills from:
  - Official catalog: bundled skills/ directory in this repo
  - Community catalog: ZIP packages from HTTPS URLs (--from <url>)

Registry: <repo>/.copilot/skills/.registry  (JSON, SHA-256 tracking)

Usage:
    python skill-catalog.py catalog               List official skills
    python skill-catalog.py catalog --json        Output as JSON
    python skill-catalog.py add <name>            Install official skill
    python skill-catalog.py add --from <url>      Install community skill (HTTPS only)
    python skill-catalog.py remove <name>         Remove installed skill (checks digest)
    python skill-catalog.py remove <name> --force Force removal even if digest mismatch
    python skill-catalog.py --help                Show this help
"""

import argparse
import hashlib
import io
import json
import os
import sys
import zipfile
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import urlopen

if os.name == "nt":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
_TOOLS_DIR = Path(__file__).parent.resolve()
_OFFICIAL_SKILLS_DIR = _TOOLS_DIR / "skills"

_SKILL_YML_SCHEMA_VERSION = 1
_REGISTRY_FILENAME = ".registry"

# Minimum required fields in skill.yml
_SKILL_YML_REQUIRED = ("schema_version", "provides", "requires")

# ---------------------------------------------------------------------------
# skill.yml parsing / validation (pure stdlib — no YAML library)
# ---------------------------------------------------------------------------


def _parse_skill_yml(text: str) -> dict:
    """Parse a minimal skill.yml using a hand-rolled parser (no PyYAML dependency).

    Supports the subset needed by this spec:
      schema_version: <int>
      provides:
        commands:
          - <str>
          ...
      requires:
        sk_version: <str>
      hooks:           (optional)
        before_plan: <str>
        after_implement: <str>
    """
    result: dict = {}
    lines = text.splitlines()
    i = 0

    def _strip_comment(s: str) -> str:
        # Remove inline YAML comments
        in_q = False
        for idx, ch in enumerate(s):
            if ch == '"':
                in_q = not in_q
            elif ch == "#" and not in_q:
                return s[:idx]
        return s

    def _unquote(s: str) -> str:
        """Strip surrounding YAML quotes from a scalar value."""
        s = s.strip()
        if len(s) >= 2 and ((s[0] == '"' and s[-1] == '"') or (s[0] == "'" and s[-1] == "'")):
            return s[1:-1]
        return s

    while i < len(lines):
        raw = lines[i]
        stripped = raw.strip()
        i += 1

        if not stripped or stripped.startswith("#"):
            continue

        # Top-level key: value
        if not raw.startswith(" ") and ":" in stripped:
            key, _, val = stripped.partition(":")
            key = key.strip()
            val = _strip_comment(val).strip()
            if val:
                # Try int (unquoted)
                try:
                    result[key] = int(val)
                except ValueError:
                    result[key] = _unquote(val)
            else:
                # Block value — collect children
                children: dict = {}
                list_items: list = []
                while i < len(lines):
                    child_raw = lines[i]
                    child_stripped = child_raw.strip()
                    if not child_stripped or child_stripped.startswith("#"):
                        i += 1
                        continue
                    indent = len(child_raw) - len(child_raw.lstrip())
                    if indent == 0:
                        break
                    i += 1
                    if child_stripped.startswith("- "):
                        list_items.append(child_stripped[2:].strip())
                    elif ":" in child_stripped:
                        ck, _, cv = child_stripped.partition(":")
                        cv = _strip_comment(cv).strip()
                        # Sub-block (e.g. provides.commands)
                        if not cv:
                            sub_items: list = []
                            while i < len(lines):
                                sub_raw = lines[i]
                                sub_stripped = sub_raw.strip()
                                if not sub_stripped or sub_stripped.startswith("#"):
                                    i += 1
                                    continue
                                sub_indent = len(sub_raw) - len(sub_raw.lstrip())
                                if sub_indent <= indent:
                                    break
                                i += 1
                                if sub_stripped.startswith("- "):
                                    sub_items.append(sub_stripped[2:].strip())
                            children[ck.strip()] = sub_items
                        else:
                            try:
                                children[ck.strip()] = int(cv)
                            except ValueError:
                                children[ck.strip()] = _unquote(cv)
                result[key] = children if children else (list_items if list_items else {})
    return result


def _validate_skill_yml(data: dict) -> list[str]:
    """Return list of validation error strings (empty = valid)."""
    errors: list[str] = []

    if "schema_version" not in data:
        errors.append("missing 'schema_version'")
    elif not isinstance(data["schema_version"], int):
        errors.append("'schema_version' must be an integer")
    elif data["schema_version"] != _SKILL_YML_SCHEMA_VERSION:
        errors.append(f"'schema_version' must be {_SKILL_YML_SCHEMA_VERSION} (got {data['schema_version']})")

    provides = data.get("provides", {})
    if not isinstance(provides, dict):
        errors.append("'provides' must be a mapping")
    else:
        cmds = provides.get("commands")
        if cmds is None:
            errors.append("'provides.commands' is required")
        elif not isinstance(cmds, list) or len(cmds) == 0:
            errors.append("'provides.commands' must be a non-empty list")

    requires = data.get("requires", {})
    if not isinstance(requires, dict):
        errors.append("'requires' must be a mapping")
    else:
        if "sk_version" not in requires:
            errors.append("'requires.sk_version' is required")

    hooks = data.get("hooks", {})
    if hooks and not isinstance(hooks, dict):
        errors.append("'hooks' must be a mapping")

    return errors


# ---------------------------------------------------------------------------
# Registry helpers
# ---------------------------------------------------------------------------


def _registry_path(project_root: Path) -> Path:
    return project_root / ".copilot" / "skills" / _REGISTRY_FILENAME


def _load_registry(project_root: Path) -> dict:
    rp = _registry_path(project_root)
    if not rp.exists():
        return {"skills": {}}
    try:
        data = json.loads(rp.read_text(encoding="utf-8"))
        if "skills" not in data:
            data["skills"] = {}
        return data
    except (json.JSONDecodeError, OSError):
        return {"skills": {}}


def _save_registry(project_root: Path, registry: dict) -> None:
    rp = _registry_path(project_root)
    rp.parent.mkdir(parents=True, exist_ok=True)
    tmp = rp.with_suffix(".tmp")
    try:
        tmp.write_bytes(json.dumps(registry, indent=2, sort_keys=True).encode("utf-8"))
        os.replace(str(tmp), str(rp))
    except Exception:
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass
        raise


# ---------------------------------------------------------------------------
# SHA-256 helpers
# ---------------------------------------------------------------------------


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_dir(directory: Path) -> str:
    """Return a stable SHA-256 digest over all files in a directory (sorted)."""
    digest = hashlib.sha256()
    for p in sorted(directory.rglob("*")):
        if p.is_file():
            rel = p.relative_to(directory).as_posix()
            digest.update(rel.encode("utf-8"))
            digest.update(b"\x00")
            with p.open("rb") as fh:
                for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                    digest.update(chunk)
    return digest.hexdigest()


# ---------------------------------------------------------------------------
# Official catalog helpers
# ---------------------------------------------------------------------------


def _list_official_skills() -> list[dict]:
    """Return list of official skill info dicts from the bundled skills/ dir."""
    skills = []
    if not _OFFICIAL_SKILLS_DIR.exists():
        return skills
    for skill_dir in sorted(_OFFICIAL_SKILLS_DIR.iterdir()):
        if not skill_dir.is_dir():
            continue
        skill_md = skill_dir / "SKILL.md"
        skill_yml = skill_dir / "skill.yml"
        if not skill_md.exists():
            continue
        entry: dict = {"name": skill_dir.name, "source": "official", "path": str(skill_dir)}
        if skill_yml.exists():
            try:
                data = _parse_skill_yml(skill_yml.read_text(encoding="utf-8"))
                entry["commands"] = data.get("provides", {}).get("commands", [])
                entry["sk_version"] = data.get("requires", {}).get("sk_version", "")
            except Exception:
                pass
        # Parse description from SKILL.md front-matter
        lines = skill_md.read_text(encoding="utf-8").splitlines()
        for line in lines:
            if line.strip().startswith("description:"):
                entry["description"] = line.split(":", 1)[1].strip().strip('"').strip("'")
                break
        skills.append(entry)
    return skills


# ---------------------------------------------------------------------------
# Install / uninstall helpers
# ---------------------------------------------------------------------------


def _find_project_root(cwd: Path | None = None) -> Path:
    """Walk up from cwd looking for a .git directory or .copilot directory."""
    start = cwd or Path.cwd()
    for candidate in [start, *start.parents]:
        if (candidate / ".git").exists() or (candidate / ".copilot").exists():
            return candidate
    # Fallback: cwd
    return start


def _skill_install_dir(project_root: Path, name: str) -> Path:
    return project_root / ".copilot" / "skills" / name


def _copy_official_skill(name: str, dest: Path) -> None:
    """Copy official skill directory into dest."""
    src = _OFFICIAL_SKILLS_DIR / name
    if not src.exists():
        raise FileNotFoundError(f"Official skill '{name}' not found in {_OFFICIAL_SKILLS_DIR}")
    if dest.exists():
        import shutil

        shutil.rmtree(dest)
    import shutil

    shutil.copytree(str(src), str(dest))


def _download_https_zip(url: str, timeout: int = 30) -> bytes:
    """Download a ZIP from an HTTPS URL. Raises on non-HTTPS or HTTP errors."""
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise ValueError(f"Only HTTPS community sources are allowed (got: {url!r})")
    with urlopen(url, timeout=timeout) as resp:  # noqa: S310 — HTTPS enforced above
        return resp.read()


def _find_skill_root_in_zip(zf: zipfile.ZipFile) -> str | None:
    """Return the common prefix path (or '') where skill.yml lives in the ZIP."""
    names = zf.namelist()
    for name in names:
        if name.endswith("skill.yml") and not name.startswith("__"):
            prefix = name[: name.rfind("skill.yml")]
            return prefix
    return None


# ---------------------------------------------------------------------------
# Subcommand: catalog
# ---------------------------------------------------------------------------


def cmd_catalog(args: argparse.Namespace) -> int:
    project_root = _find_project_root()
    registry = _load_registry(project_root)
    installed = registry.get("skills", {})

    skills = _list_official_skills()

    if args.json:
        for s in skills:
            s["installed"] = s["name"] in installed
        print(json.dumps({"official": skills, "installed": list(installed.keys())}, indent=2))
        return 0

    if not skills:
        print("No official skills found.")
        return 0

    print(f"Official skills ({len(skills)}):")
    for s in skills:
        marker = "✓" if s["name"] in installed else " "
        desc = s.get("description", "")
        cmds = ", ".join(s.get("commands", []))
        suffix = f"  [{cmds}]" if cmds else ""
        print(f"  [{marker}] {s['name']:<30} {desc[:60]}{suffix}")

    if installed:
        # Show community-installed skills not in official list
        official_names = {s["name"] for s in skills}
        community = {k: v for k, v in installed.items() if k not in official_names}
        if community:
            print(f"\nCommunity-installed skills ({len(community)}):")
            for name, meta in community.items():
                src = meta.get("source_url", "<local>")
                print(f"  [✓] {name:<30} from {src}")

    return 0


# ---------------------------------------------------------------------------
# Subcommand: add
# ---------------------------------------------------------------------------


def cmd_add(args: argparse.Namespace) -> int:
    project_root = _find_project_root()
    registry = _load_registry(project_root)

    from_url: str | None = getattr(args, "from", None)

    if from_url:
        # Community install
        return _add_community(args, project_root, registry, from_url)
    else:
        # Official install
        if not args.name:
            print("skill-catalog add: provide a skill name or --from <url>", file=sys.stderr)
            return 2
        return _add_official(args, project_root, registry, args.name)


def _add_official(args: argparse.Namespace, project_root: Path, registry: dict, name: str) -> int:
    # Validate exists in official catalog
    official = {s["name"] for s in _list_official_skills()}
    if name not in official:
        print(f"skill-catalog add: official skill '{name}' not found.", file=sys.stderr)
        print(f"  Available: {', '.join(sorted(official))}", file=sys.stderr)
        return 1

    dest = _skill_install_dir(project_root, name)

    if dest.exists() and not getattr(args, "force", False):
        print(f"Skill '{name}' is already installed at {dest}")
        print("Use --force to reinstall.")
        return 0

    print(f"Installing official skill '{name}' → {dest} ...")
    _copy_official_skill(name, dest)

    # Validate skill.yml if present
    skill_yml_path = dest / "skill.yml"
    if skill_yml_path.exists():
        data = _parse_skill_yml(skill_yml_path.read_text(encoding="utf-8"))
        errs = _validate_skill_yml(data)
        if errs:
            print(f"  ⚠  skill.yml validation warnings: {'; '.join(errs)}")

    digest = _sha256_dir(dest)
    registry["skills"][name] = {
        "source": "official",
        "installed_at": _now_iso(),
        "digest": digest,
    }
    _save_registry(project_root, registry)
    print(f"  ✓ Installed '{name}' (digest: {digest[:16]}...)")
    return 0


def _add_community(args: argparse.Namespace, project_root: Path, registry: dict, url: str) -> int:
    parsed = urlparse(url)
    if parsed.scheme != "https":
        print("skill-catalog add: only HTTPS community sources are allowed.", file=sys.stderr)
        return 1

    print(f"Downloading community skill from {url} ...")
    try:
        data = _download_https_zip(url)
    except Exception as exc:
        print(f"skill-catalog add: download failed: {exc}", file=sys.stderr)
        return 1

    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        print(f"skill-catalog add: not a valid ZIP: {exc}", file=sys.stderr)
        return 1

    prefix = _find_skill_root_in_zip(zf)
    if prefix is None:
        print("skill-catalog add: no skill.yml found in ZIP.", file=sys.stderr)
        return 1
    if prefix + "SKILL.md" not in zf.namelist():
        print("skill-catalog add: SKILL.md is required in ZIP.", file=sys.stderr)
        return 1

    # Read and validate skill.yml from the ZIP before extracting
    yml_bytes = zf.read(prefix + "skill.yml")
    try:
        yml_data = _parse_skill_yml(yml_bytes.decode("utf-8"))
    except Exception as exc:
        print(f"skill-catalog add: failed to parse skill.yml: {exc}", file=sys.stderr)
        return 1

    errs = _validate_skill_yml(yml_data)
    if errs:
        print("skill-catalog add: skill.yml validation failed:", file=sys.stderr)
        for e in errs:
            print(f"  - {e}", file=sys.stderr)
        return 1

    # Determine skill name from skill.yml or URL
    skill_name: str | None = None
    if isinstance(yml_data.get("provides"), dict):
        cmds = yml_data["provides"].get("commands", [])
        if cmds:
            skill_name = cmds[0].replace(" ", "-").lower()
    if not skill_name:
        skill_name = parsed.path.rstrip("/").rsplit("/", 1)[-1].replace(".zip", "")

    if not skill_name:
        print("skill-catalog add: cannot determine skill name.", file=sys.stderr)
        return 1

    dest = _skill_install_dir(project_root, skill_name)
    if dest.exists() and not getattr(args, "force", False):
        print(f"Skill '{skill_name}' is already installed at {dest}")
        print("Use --force to reinstall.")
        return 0

    import shutil

    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True, exist_ok=True)

    # Extract with Zip Slip protection, stripping the prefix
    for member in zf.infolist():
        if not member.filename.startswith(prefix):
            continue
        rel = member.filename[len(prefix) :]
        if not rel:
            continue
        out_path = (dest / rel).resolve()
        # Zip Slip check
        try:
            out_path.relative_to(dest.resolve())
        except ValueError:
            print(
                f"skill-catalog add: Zip Slip detected for '{member.filename}' — aborting.",
                file=sys.stderr,
            )
            shutil.rmtree(dest, ignore_errors=True)
            return 1
        if member.is_dir():
            out_path.mkdir(parents=True, exist_ok=True)
        else:
            out_path.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(member) as src, out_path.open("wb") as dst:
                dst.write(src.read())

    digest = _sha256_dir(dest)
    registry["skills"][skill_name] = {
        "source": "community",
        "source_url": url,
        "installed_at": _now_iso(),
        "digest": digest,
    }
    _save_registry(project_root, registry)
    print(f"  ✓ Installed community skill '{skill_name}' (digest: {digest[:16]}...)")
    return 0


# ---------------------------------------------------------------------------
# Subcommand: remove
# ---------------------------------------------------------------------------


def cmd_remove(args: argparse.Namespace) -> int:
    project_root = _find_project_root()
    registry = _load_registry(project_root)
    name: str = args.name

    if name not in registry.get("skills", {}):
        print(f"Skill '{name}' is not installed (not in registry).", file=sys.stderr)
        return 1

    dest = _skill_install_dir(project_root, name)
    entry = registry["skills"][name]

    if dest.exists():
        current_digest = _sha256_dir(dest)
        registered_digest = entry.get("digest", "")
        if current_digest != registered_digest and not getattr(args, "force", False):
            print(
                f"skill-catalog remove: digest mismatch for '{name}'.",
                file=sys.stderr,
            )
            print(
                f"  registered: {registered_digest[:32]}...",
                file=sys.stderr,
            )
            print(
                f"  on-disk:    {current_digest[:32]}...",
                file=sys.stderr,
            )
            print(
                "Skill files may have been modified. Use --force to remove anyway.",
                file=sys.stderr,
            )
            return 1

    import shutil

    if dest.exists():
        shutil.rmtree(dest)

    del registry["skills"][name]
    _save_registry(project_root, registry)
    print(f"  ✓ Removed skill '{name}'")
    return 0


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="skill-catalog",
        description="Extension catalog for copilot-session-knowledge skills.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python skill-catalog.py catalog\n"
            "  python skill-catalog.py catalog --json\n"
            "  python skill-catalog.py add agent-creator\n"
            "  python skill-catalog.py add --from https://example.com/my-skill.zip\n"
            "  python skill-catalog.py remove agent-creator\n"
            "  python skill-catalog.py remove agent-creator --force\n"
        ),
    )
    sub = parser.add_subparsers(dest="subcommand", metavar="subcommand")

    # catalog
    p_catalog = sub.add_parser("catalog", help="List available skills")
    p_catalog.add_argument("--json", action="store_true", help="Output as JSON")

    # add
    p_add = sub.add_parser("add", help="Install a skill")
    p_add.add_argument("name", nargs="?", help="Official skill name")
    p_add.add_argument(
        "--from",
        dest="from",
        metavar="URL",
        help="Install community skill from HTTPS ZIP URL",
    )
    p_add.add_argument("--force", action="store_true", help="Reinstall if already installed")

    # remove
    p_remove = sub.add_parser("remove", help="Uninstall a skill")
    p_remove.add_argument("name", help="Skill name to remove")
    p_remove.add_argument("--force", action="store_true", help="Remove even if digest mismatch")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.subcommand == "catalog":
        return cmd_catalog(args)
    elif args.subcommand == "add":
        return cmd_add(args)
    elif args.subcommand == "remove":
        return cmd_remove(args)
    else:
        parser.print_help()
        return 0


if __name__ == "__main__":
    sys.exit(main())
