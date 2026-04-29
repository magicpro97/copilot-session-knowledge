#!/usr/bin/env python3
"""
browse/static/vendor/_download.py — Download and verify vendored JS/CSS libs.

Run once from the repo root or this directory:
    python browse/static/vendor/_download.py

Re-running is safe: files are skipped if already present (use --force to re-download).
SHA-384 hashes are computed from downloaded content and written to VENDOR.md.
"""

import argparse
import base64
import hashlib
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")

VENDOR_DIR = Path(__file__).parent

LIBS = [
    {
        "name": "cytoscape.min.js",
        "url": "https://cdn.jsdelivr.net/npm/cytoscape@3.28.1/dist/cytoscape.min.js",
        "version": "3.28.1",
        "package": "cytoscape",
        "license": "MIT",
    },
    {
        "name": "cytoscape-dagre.js",
        "url": "https://cdn.jsdelivr.net/npm/cytoscape-dagre@2.5.0/cytoscape-dagre.js",
        "version": "2.5.0",
        "package": "cytoscape-dagre",
        "license": "MIT",
    },
    {
        "name": "dagre.min.js",
        "url": "https://cdn.jsdelivr.net/npm/dagre@0.8.5/dist/dagre.min.js",
        "version": "0.8.5",
        "package": "dagre",
        "license": "MIT",
    },
    {
        "name": "ninja-keys.min.js",
        "url": "https://cdn.jsdelivr.net/npm/ninja-keys@1.2.2/dist/ninja-keys.min.js",
        "version": "1.2.2",
        "package": "ninja-keys",
        "license": "MIT",
    },
    {
        "name": "pico.min.css",
        "url": "https://cdn.jsdelivr.net/npm/@picocss/pico@2.0.6/css/pico.min.css",
        "version": "2.0.6",
        "package": "@picocss/pico",
        "license": "MIT",
    },
]


def sha384_b64(data: bytes) -> str:
    digest = hashlib.sha384(data).digest()
    return "sha384-" + base64.b64encode(digest).decode("ascii")


def download_lib(lib: dict, force: bool = False) -> dict | None:
    dest = VENDOR_DIR / lib["name"]
    if dest.exists() and not force:
        print(f"  [skip] {lib['name']} already exists (use --force to re-download)")
        data = dest.read_bytes()
        return {**lib, "sha384": sha384_b64(data), "size": len(data)}

    print(f"  [download] {lib['url']}")
    try:
        req = urllib.request.Request(
            lib["url"],
            headers={"User-Agent": "hindsight-vendor-downloader/1.0"},
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = resp.read()
    except urllib.error.URLError as e:
        print(f"  [ERROR] Failed to download {lib['url']}: {e}", file=sys.stderr)
        return None

    dest.write_bytes(data)
    sha = sha384_b64(data)
    print(f"  [ok] {lib['name']} ({len(data):,} bytes) SHA-384: {sha}")
    return {**lib, "sha384": sha, "size": len(data)}


def write_vendor_md(results: list) -> None:
    lines = [
        "# Vendor Libraries",
        "",
        "Vendored JS/CSS libs used by Hindsight browse UI.",
        "Downloaded by `_download.py`. Do not edit manually.",
        "",
        "| File | Package | Version | License | SHA-384 | Source |",
        "|------|---------|---------|---------|---------|--------|",
    ]
    for r in results:
        sha = r.get("sha384", "N/A")
        lines.append(
            f"| `{r['name']}` | {r['package']} | {r['version']} | {r['license']} | `{sha}` | [{r['url']}]({r['url']}) |"
        )
    lines += [
        "",
        "## Re-downloading",
        "",
        "```bash",
        "python browse/static/vendor/_download.py --force",
        "```",
        "",
        "## SRI usage",
        "",
        "Use the SHA-384 values above as `integrity` attributes on `<script>` and `<link>` tags.",
        "",
    ]
    md_path = VENDOR_DIR / "VENDOR.md"
    md_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"  [ok] VENDOR.md written ({md_path})")


def main() -> int:
    p = argparse.ArgumentParser(description="Download vendored JS/CSS libs")
    p.add_argument("--force", action="store_true", help="Re-download even if file exists")
    args = p.parse_args()

    print(f"Vendor directory: {VENDOR_DIR}")
    results = []
    ok = True
    for lib in LIBS:
        result = download_lib(lib, force=args.force)
        if result is None:
            ok = False
        else:
            results.append(result)

    if results:
        write_vendor_md(results)

    if not ok:
        print("\nSome downloads failed. Check your network connection.", file=sys.stderr)
        return 1
    print(f"\nAll {len(results)} vendor files ready.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
