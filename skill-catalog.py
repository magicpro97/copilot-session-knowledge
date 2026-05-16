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
      handoffs:        (optional)
        - label: <str>
          skill: <str>
          prompt: <str>
          send: <bool>
    """
    lines = text.splitlines()

    def _strip_comment(s: str) -> str:
        # Remove inline YAML comments
        in_double = False
        in_single = False
        for idx, ch in enumerate(s):
            if ch == '"' and not in_single:
                in_double = not in_double
            elif ch == "'" and not in_double:
                in_single = not in_single
            elif ch == "#" and not in_double and not in_single:
                return s[:idx]
        return s

    def _unquote(s: str) -> str:
        """Strip surrounding YAML quotes from a scalar value."""
        s = s.strip()
        if len(s) >= 2 and ((s[0] == '"' and s[-1] == '"') or (s[0] == "'" and s[-1] == "'")):
            return s[1:-1]
        return s

    def _parse_scalar(s: str):
        s = _unquote(_strip_comment(s).strip())
        low = s.lower()
        if low == "true":
            return True
        if low == "false":
            return False
        try:
            return int(s)
        except ValueError:
            return s

    def _parse_block(index: int, indent: int) -> tuple[object, int]:
        mapping: dict[str, object] = {}
        sequence: list[object] = []
        mode: str | None = None

        while index < len(lines):
            raw = lines[index]
            stripped = raw.strip()
            if not stripped or stripped.startswith("#"):
                index += 1
                continue

            current_indent = len(raw) - len(raw.lstrip(" "))
            if current_indent < indent:
                break
            if current_indent > indent:
                break

            content = _strip_comment(raw[current_indent:]).strip()
            if not content:
                index += 1
                continue

            if content.startswith("- "):
                if mode is None:
                    mode = "sequence"
                elif mode != "sequence":
                    break

                item_text = content[2:].strip()
                index += 1

                if not item_text:
                    item, index = _parse_block(index, current_indent + 2)
                    sequence.append(item)
                    continue

                if ":" in item_text:
                    item_key, _, item_value = item_text.partition(":")
                    item: dict[str, object] = {}
                    item_key = item_key.strip()
                    item_value = item_value.strip()
                    if item_value:
                        item[item_key] = _parse_scalar(item_value)
                    else:
                        child, index = _parse_block(index, current_indent + 4)
                        item[item_key] = child

                    extra, index = _parse_block(index, current_indent + 2)
                    if isinstance(extra, dict):
                        item.update(extra)
                    sequence.append(item)
                    continue

                sequence.append(_parse_scalar(item_text))
                continue

            if ":" not in content:
                index += 1
                continue

            if mode is None:
                mode = "mapping"
            elif mode != "mapping":
                break

            key, _, value = content.partition(":")
            key = key.strip()
            value = value.strip()
            index += 1

            if value:
                mapping[key] = _parse_scalar(value)
                continue

            child, index = _parse_block(index, current_indent + 2)
            mapping[key] = child

        if mode == "sequence":
            return sequence, index
        return mapping, index

    parsed, _ = _parse_block(0, 0)
    return parsed if isinstance(parsed, dict) else {}


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

    handoffs = data.get("handoffs", [])
    if handoffs:
        if not isinstance(handoffs, list):
            errors.append("'handoffs' must be a list")
        else:
            for idx, handoff in enumerate(handoffs):
                prefix = f"handoffs[{idx}]"
                if not isinstance(handoff, dict):
                    errors.append(f"{prefix} must be a mapping")
                    continue

                for field in ("label", "skill", "prompt", "send"):
                    if field not in handoff:
                        errors.append(f"{prefix}.{field} is required")

                for field in ("label", "skill", "prompt"):
                    if field in handoff and (not isinstance(handoff[field], str) or not handoff[field].strip()):
                        errors.append(f"{prefix}.{field} must be a non-empty string")

                if "send" in handoff and not isinstance(handoff["send"], bool):
                    errors.append(f"{prefix}.send must be a boolean")

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
                handoffs = data.get("handoffs", [])
                if isinstance(handoffs, list) and handoffs:
                    entry["handoffs"] = handoffs
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


def _format_handoff_brief(handoff: dict) -> str:
    label = str(handoff.get("label") or handoff.get("skill") or "next-step")
    skill = str(handoff.get("skill") or "unknown-skill")
    mode = "auto" if handoff.get("send") else "suggest"
    return f"{label} -> {skill} [{mode}]"


def _print_handoff_suggestions(handoffs: list[dict]) -> None:
    if not handoffs:
        return
    print("  Next skills:")
    for handoff in handoffs:
        label = str(handoff.get("label") or handoff.get("skill") or "Next step")
        skill = str(handoff.get("skill") or "unknown-skill")
        prompt = str(handoff.get("prompt") or "").strip()
        mode = "auto" if handoff.get("send") else "suggest"
        print(f"    - {label} -> {skill} ({mode})")
        if prompt:
            print(f"      {prompt}")


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
        handoffs = s.get("handoffs", [])
        if handoffs:
            summary = "; ".join(_format_handoff_brief(h) for h in handoffs)
            print(f"      handoffs: {summary}")

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
    handoffs: list[dict] = []
    if skill_yml_path.exists():
        data = _parse_skill_yml(skill_yml_path.read_text(encoding="utf-8"))
        errs = _validate_skill_yml(data)
        if errs:
            print(f"  ⚠  skill.yml validation warnings: {'; '.join(errs)}")
        parsed_handoffs = data.get("handoffs", [])
        if isinstance(parsed_handoffs, list):
            handoffs = parsed_handoffs

    digest = _sha256_dir(dest)
    registry["skills"][name] = {
        "source": "official",
        "installed_at": _now_iso(),
        "digest": digest,
    }
    _save_registry(project_root, registry)
    print(f"  ✓ Installed '{name}' (digest: {digest[:16]}...)")
    _print_handoff_suggestions(handoffs)
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
    handoffs = yml_data.get("handoffs", [])
    if isinstance(handoffs, list):
        _print_handoff_suggestions(handoffs)
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
