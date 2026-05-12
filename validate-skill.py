#!/usr/bin/env python3
"""Validate a SKILL.md file against the Agent Skills open standard.

Spec: https://agentskills.io/specification
Repo: https://github.com/agentskills/agentskills

Usage:
    python3 validate-skill.py path/to/SKILL.md
    python3 validate-skill.py path/to/skill-dir/
"""

import collections
import sys
import re
from pathlib import Path
import os
if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")

# --- Standards thresholds ---
MAX_LINES = 500
MIN_DESCRIPTION_WORDS = 10
MAX_HEAVY_HANDED = 5  # MUST/ALWAYS/NEVER without reasoning

# ---------------------------------------------------------------------------
# Security finding infrastructure
# ---------------------------------------------------------------------------

#: Severity constants (ordered from least to most severe)
SEVERITY_LOW      = "low"
SEVERITY_MEDIUM   = "medium"
SEVERITY_HIGH     = "high"
SEVERITY_CRITICAL = "critical"

_SEVERITY_ORDER = {SEVERITY_LOW: 0, SEVERITY_MEDIUM: 1, SEVERITY_HIGH: 2, SEVERITY_CRITICAL: 3}

#: A single security finding produced by validate_security().
Finding = collections.namedtuple("Finding", ["severity", "category", "message", "line"])

#: A compiled security rule used by validate_security().
_SecurityRule = collections.namedtuple("_SecurityRule", ["category", "severity", "rx", "message"])

def _rule(category: str, severity: str, flags: int, pattern: str, message: str) -> _SecurityRule:
    """Compile a security rule, raising ValueError on bad regex at import time."""
    return _SecurityRule(category=category, severity=severity,
                         rx=re.compile(pattern, flags), message=message)


def _mask_code_blocks(content: str) -> str:
    """Replace fenced code block interiors with spaces so security rules don't
    false-positive on code examples (e.g. ``system:`` in a YAML snippet).

    Newlines are preserved verbatim so that line numbers in findings remain
    accurate.  Only triple-backtick and triple-tilde fences are handled; the
    opening and closing fence lines themselves are kept as-is.

    Two important behaviours (CommonMark-compatible):
    - A closing fence may be *longer* than its opener (e.g. 5 backticks can
      close a 3-backtick opener) as long as it uses the same fence character.
    - An *unclosed* fence does NOT mask content from the opener to EOF.
      Without this guard a single forgotten closing fence would suppress all
      security findings that appear after it in the document.
    """
    lines = content.splitlines(keepends=True)

    # First pass: identify line indices that are interior to a *closed* fence.
    # Unclosed fences are intentionally excluded so their content is still
    # scanned for security findings.
    masked_indices: set[int] = set()
    in_fence = False
    fence_char = ""
    fence_min_len = 0
    interior_start = 0

    for i, line in enumerate(lines):
        stripped = line.rstrip("\r\n")
        if not in_fence:
            # CommonMark §4.5: fence opener may have at most 3 spaces of indentation.
            # 4+ leading spaces make the line an indented code block, not a fence opener.
            m = re.match(r"^ {0,3}(```+|~~~+)", stripped)
            if m:
                in_fence = True
                marker = m.group(1)
                fence_char = marker[0]
                fence_min_len = len(marker)
                interior_start = i + 1  # first interior line index
        else:
            # CommonMark §4.5: closer must be same character, at least as long as opener,
            # and may have at most 3 leading spaces (same restriction as the opener).
            # 4+ leading spaces make the line an indented code block, not a fence closer.
            close_rx = r"^ {0,3}" + re.escape(fence_char) + "{" + str(fence_min_len) + r",}\s*$"
            if re.match(close_rx, stripped):
                in_fence = False
                for j in range(interior_start, i):
                    masked_indices.add(j)
            # else: still inside fence — don't add to masked_indices yet (may be unclosed)

    # Second pass: build output, masking only confirmed-closed interior lines.
    result: list[str] = []
    for i, line in enumerate(lines):
        if i in masked_indices:
            stripped = line.rstrip("\r\n")
            eol = line[len(stripped):]
            result.append(" " * len(stripped) + eol)
        else:
            result.append(line)
    return "".join(result)

# ---------------------------------------------------------------------------
# Security rules — 35 patterns across four categories
# ---------------------------------------------------------------------------
# Findings are mapped to errors (critical/high) or warnings (medium/low) by
# validate() so that the existing (errors, warnings) return signature is
# preserved.  The SECURITY prefix "[SECURITY:<SEVERITY>]" on each message
# lets callers distinguish security findings from structural findings.
# ---------------------------------------------------------------------------

SECURITY_RULES: list[_SecurityRule] = [

    # ── Prompt Injection ────────────────────────────────────────────────────
    _rule("prompt-injection", SEVERITY_HIGH, re.IGNORECASE,
          r"ignore\s+(all\s+)?previous\s+(instructions?|commands?|context|rules?|guidelines?|prompt)",
          "Prompt injection: 'ignore previous instructions' pattern detected"),
    _rule("prompt-injection", SEVERITY_HIGH, re.IGNORECASE,
          r"disregard\s+(all\s+)?previous\s+(instructions?|commands?|context|rules?|guidelines?)",
          "Prompt injection: 'disregard previous instructions' pattern detected"),
    _rule("prompt-injection", SEVERITY_HIGH, re.IGNORECASE,
          r"forget\s+(everything|all\s+previous|your\s+(instructions?|training|guidelines?|rules?))",
          "Prompt injection: 'forget your instructions' pattern detected"),
    _rule("prompt-injection", SEVERITY_HIGH, re.IGNORECASE | re.MULTILINE,
          r"^system:\s",
          "Prompt injection: line starts with 'system:' — may hijack system prompt"),
    _rule("prompt-injection", SEVERITY_HIGH, re.IGNORECASE,
          r"<system>",
          "Prompt injection: <system> tag may inject system-level instructions"),
    _rule("prompt-injection", SEVERITY_HIGH, re.IGNORECASE,
          r"\bact\s+as\b.{0,40}\b(hacker|attacker|evil|malicious|unrestricted|jailbreak|no[- ]restriction)",
          "Prompt injection: 'act as [unconstrained role]' persona injection pattern"),
    _rule("prompt-injection", SEVERITY_HIGH, re.IGNORECASE,
          r"you\s+are\s+now\b.{0,40}\b(hacker|unrestricted|jailbreak|DAN|evil|no[- ]restriction)",
          "Prompt injection: 'you are now [persona]' identity injection pattern"),
    _rule("prompt-injection", SEVERITY_HIGH, re.IGNORECASE,
          r"\b(jailbreak|DAN)\s+mode\b",
          "Prompt injection: jailbreak/DAN mode activation pattern detected"),
    _rule("prompt-injection", SEVERITY_HIGH, re.IGNORECASE,
          r"override\s+(your|all|the)\s+(instructions?|training|guidelines?|safety[\s_-]measures?|constraints?)",
          "Prompt injection: instruction override attempt detected"),
    _rule("prompt-injection", SEVERITY_MEDIUM, re.IGNORECASE,
          r"\[INST\]|\[/INST\]",
          "Prompt injection: LLaMA/instruction-tuning special tokens detected"),
    _rule("prompt-injection", SEVERITY_MEDIUM, re.IGNORECASE,
          r"pretend\s+(you\s+are|to\s+be).{0,60}(unrestricted|no\s+rules?|no\s+restrictions?|no\s+limits?|without\s+restriction)",
          "Prompt injection: 'pretend to be unrestricted' pattern detected"),
    _rule("prompt-injection", SEVERITY_MEDIUM, re.IGNORECASE,
          r"<\|im_start\|>|<\|im_end\|>|<\|endoftext\|>|\[SYSTEM\]",
          "Prompt injection: model special tokens detected (ChatML/GPT format)"),

    # ── Destructive Commands ────────────────────────────────────────────────
    _rule("destructive", SEVERITY_CRITICAL, re.IGNORECASE,
          r"\brm\s+-[rRf]*[rf][rRf]*\s+[/~]",
          "Destructive command: recursive/force rm on root or home path"),
    _rule("destructive", SEVERITY_HIGH, re.IGNORECASE,
          r"\brm\s+-[rRf]*[rf][rRf]*\s+(?:\*|\.\.?(?:[/\\]|(?=\s|$)))",
          "Destructive command: recursive/force rm with glob or relative path (may wipe project directory)"),
    _rule("destructive", SEVERITY_HIGH, re.IGNORECASE,
          r"\bdel(?:ete)?\s+/[fsqFSQ]|\bdel\s+\*\.[*a-zA-Z]",
          "Destructive command: Windows forced/silent delete pattern"),
    _rule("destructive", SEVERITY_CRITICAL, re.IGNORECASE | re.MULTILINE,
          r"\bformat\s+[a-zA-Z]:\s*(?:/[a-zA-Z0-9]|\s*$)",
          "Destructive command: Windows disk format command detected"),
    _rule("destructive", SEVERITY_HIGH, re.IGNORECASE,
          r"\bDROP\s+(TABLE|DATABASE|SCHEMA|INDEX)\b",
          "Destructive command: SQL DROP statement detected"),
    _rule("destructive", SEVERITY_MEDIUM, re.IGNORECASE,
          r"\bDELETE\s+FROM\s+\w",
          "Destructive command: SQL DELETE FROM statement detected"),
    _rule("destructive", SEVERITY_MEDIUM, re.IGNORECASE,
          r"\bTRUNCATE\s+TABLE\b",
          "Destructive command: SQL TRUNCATE TABLE statement detected"),
    _rule("destructive", SEVERITY_HIGH, re.IGNORECASE,
          r"\bkill\s+-9\s+(-1|1)\b",
          "Destructive command: kill -9 all/init processes detected"),
    _rule("destructive", SEVERITY_HIGH, re.IGNORECASE,
          r"\bshutdown\s+(now|/[sS]|-[hHrRpP])\b",
          "Destructive command: system shutdown/reboot command detected"),
    _rule("destructive", SEVERITY_CRITICAL, re.IGNORECASE,
          r"\bmkfs\b",
          "Destructive command: filesystem format utility (mkfs) detected"),
    _rule("destructive", SEVERITY_CRITICAL, re.IGNORECASE,
          r"\bdd\b[^\n]*\bof=/dev/(?:(?:s|h|xv|v)d|nvme\d+n\d+|mmcblk\d+|dm-\d+|loop\d+|mapper/\S+)",
          "Destructive command: dd writing to raw block device detected"),

    # ── Exfiltration ────────────────────────────────────────────────────────
    _rule("exfiltration", SEVERITY_CRITICAL, re.IGNORECASE,
          r"\bcurl\b[^|\n`]*\|\s*(?:sh|bash|zsh|python3?|perl|ruby|exec)\b",
          "Exfiltration: curl-pipe-to-shell pattern (remote code execution risk)"),
    _rule("exfiltration", SEVERITY_CRITICAL, re.IGNORECASE,
          r"\bwget\b[^|\n`]*\|\s*(?:sh|bash|zsh|python3?|perl)\b",
          "Exfiltration: wget-pipe-to-shell pattern (remote code execution risk)"),
    _rule("exfiltration", SEVERITY_CRITICAL, re.IGNORECASE,
          r"\bbase64\s*(?:--decode|-d)\b[^|\n`]*\|\s*(?:sh|bash|zsh|python3?|perl)\b",
          "Exfiltration: base64 decode pipe to shell (obfuscated RCE risk)"),
    _rule("exfiltration", SEVERITY_HIGH, re.IGNORECASE,
          r"\bnc\s+\d{1,3}(?:\.\d{1,3}){3}\s+\d{2,5}\b",
          "Exfiltration: netcat to IP address/port detected"),
    _rule("exfiltration", SEVERITY_HIGH, re.IGNORECASE,
          r"(?:curl|wget)\b[^\n`]*\$\(",
          "Exfiltration: command substitution in curl/wget URL (data exfiltration risk)"),
    _rule("exfiltration", SEVERITY_HIGH, re.IGNORECASE,
          r"(?:curl|wget)\b[^\n`]*\$\{?(?:[A-Z0-9]*_)*(?:TOKEN|SECRET|KEY|PASSWORD|PASSWD|CREDENTIALS?|PRIVATE|AUTH)(?![A-Za-z])\w*\}?",
          "Exfiltration: secret-like environment variable in curl/wget (credential leak risk)"),
    _rule("exfiltration", SEVERITY_HIGH, re.IGNORECASE,
          r"/dev/tcp/[^/\s]+/\d+",
          "Exfiltration: bash /dev/tcp network redirect detected"),
    _rule("exfiltration", SEVERITY_HIGH, re.IGNORECASE,
          r"\bpython3?\s+-c\s+['\"][^'\"]*(?:import\s+socket|urllib\.request|http\.client|requests\.)"
          r"[^'\"]*(?:send|post|get|connect)\(",
          "Exfiltration: Python one-liner with network socket/HTTP call detected"),

    # ── Obfuscation ─────────────────────────────────────────────────────────
    _rule("obfuscation", SEVERITY_MEDIUM, 0,
          r"[A-Za-z0-9+/]{100,}={0,2}",
          "Obfuscation: unusually long base64-like string may hide a payload"),
    _rule("obfuscation", SEVERITY_HIGH, 0,
          "[\u202e\u2066\u2067\u2069\u200b\u200c\u200d\ufeff]",
          "Obfuscation: Unicode direction-override or invisible character detected"),
    _rule("obfuscation", SEVERITY_HIGH, re.IGNORECASE,
          r"\beval\s*\(\s*base64_decode\s*\(",
          "Obfuscation: eval(base64_decode()) PHP-style obfuscated code execution"),
    _rule("obfuscation", SEVERITY_MEDIUM, re.IGNORECASE,
          r"(?:\\x[0-9a-fA-F]{2}){4,}",
          r"Obfuscation: multiple hex escape sequences (\xNN) may hide malicious content"),
    _rule("obfuscation", SEVERITY_LOW, re.IGNORECASE,
          r"(?:%[0-9a-fA-F]{2}){4,}",
          "Obfuscation: multiple URL-encoded sequences may conceal path traversal or injection"),
]


def validate_security(content: str) -> list[Finding]:
    """Run all security rules against *content* and return a list of Findings.

    Fenced code blocks (``` or ~~~) are masked before scanning so that code
    examples — e.g. a YAML snippet containing ``system: root`` — do not produce
    false positives.  Line numbers are still reported relative to the original
    content (newlines are preserved in the mask).

    Each rule reports at most one Finding (first match) to avoid noise.
    Line numbers are 1-based.  Never raises; bad regex is caught at module
    import time by _rule().
    """
    scanned = _mask_code_blocks(content)
    findings: list[Finding] = []
    for rule in SECURITY_RULES:
        m = rule.rx.search(scanned)
        if m:
            line_no = scanned[: m.start()].count("\n") + 1
            findings.append(Finding(
                severity=rule.severity,
                category=rule.category,
                message=f"{rule.message} (line {line_no})",
                line=line_no,
            ))
    return findings

def validate(path: Path) -> tuple[list[str], list[str]]:
    """Validate a SKILL.md file. Returns (errors, warnings)."""
    errors = []
    warnings = []

    if path.is_dir():
        path = path / "SKILL.md"

    if not path.exists():
        return [f"File not found: {path}"], []

    content = path.read_text(encoding="utf-8-sig", errors="replace")
    lines = content.splitlines()

    # --- 1. YAML frontmatter ---
    if not content.startswith("---"):
        errors.append("Missing YAML frontmatter (file must start with ---)")
    else:
        fm_end = content.find("---", 3)
        if fm_end == -1:
            errors.append("Malformed YAML frontmatter (no closing ---)")
        else:
            fm = content[3:fm_end]
            if "name:" not in fm:
                errors.append("Frontmatter missing 'name' field")
            else:
                # Validate name format per Agent Skills spec:
                # 1–64 chars, a-z/0-9/hyphens, no leading/trailing/consecutive hyphens,
                # must match parent directory name.
                # Use [ \t]* (horizontal whitespace only) so a bare `name:` with no value
                # on the same line does NOT match the next line's content (e.g. description:).
                name_match = re.search(r"^name:[ \t]*['\"]?([^\s'\"#\n]+)['\"]?", fm, re.MULTILINE)
                if name_match:
                    skill_name = name_match.group(1).strip()
                    if not re.fullmatch(r"[a-z0-9][a-z0-9\-]*[a-z0-9]|[a-z0-9]", skill_name):
                        errors.append(
                            f"'name' value '{skill_name}' is invalid: must be 1–64 chars, "
                            "lowercase letters/digits/hyphens only, no leading or trailing hyphens."
                        )
                    elif "--" in skill_name:
                        errors.append(
                            f"'name' value '{skill_name}' contains consecutive hyphens (--), "
                            "which is not allowed by the Agent Skills spec."
                        )
                    elif len(skill_name) > 64:
                        errors.append(
                            f"'name' value '{skill_name}' is {len(skill_name)} chars; "
                            "max is 64 per the Agent Skills spec."
                        )
                    else:
                        # Check directory name match (only when validating a file inside a dir)
                        dir_name = path.parent.name
                        if dir_name and dir_name != "." and dir_name != skill_name:
                            warnings.append(
                                f"'name' field '{skill_name}' does not match parent directory "
                                f"'{dir_name}' — the Agent Skills spec requires them to match."
                            )
                else:
                    errors.append(
                        "'name' field has no value — it must not be empty "
                        "(e.g. `name: my-skill` not just `name:`)."
                    )
            if "description:" not in fm:
                errors.append("Frontmatter missing 'description' field")
            else:
                # Extract description text — handle YAML block scalars (>-, >+, >, |, |-…)
                # as well as quoted/unquoted single-line values.
                # Use [ \t]* (horizontal whitespace only) so a bare `description:` with no
                # value on the same line does NOT cross the newline and capture the next
                # YAML key (e.g. `name:`) as the description text.
                desc_match = re.search(r"description:[ \t]*[>|][+\-]?[ \t]*\n((?:[ \t]+.*\n)*)", fm)
                if not desc_match:
                    desc_match = re.search(r'description:[ \t]*["\']?(.+)', fm)
                if desc_match:
                    desc_text = desc_match.group(1).strip()
                    if not desc_text:
                        errors.append(
                            "'description' field has no value — it must not be empty "
                            "(e.g. `description: Use when ...` not just `description:`)."
                        )
                    else:
                        word_count = len(desc_text.split())
                        if word_count < MIN_DESCRIPTION_WORDS:
                            warnings.append(
                                f"Description only {word_count} words — aim for {MIN_DESCRIPTION_WORDS}+ "
                                f"with trigger phrases (skills under-activate without them)"
                            )
                        # Check for trigger words
                        trigger_patterns = ["use when", "trigger", "activat", "invoke", "keyword"]
                        has_triggers = any(p in desc_text.lower() for p in trigger_patterns)
                        if not has_triggers:
                            warnings.append(
                                "Description lacks trigger phrases — add 'Use when...' or keywords "
                                "to improve activation reliability"
                            )
                else:
                    errors.append(
                        "'description' field has no value — it must not be empty "
                        "(e.g. `description: Use when ...` not just `description:`)."
                    )

    # --- 2. Line count ---
    line_count = len(lines)
    if line_count > MAX_LINES:
        errors.append(
            f"File is {line_count} lines (max {MAX_LINES}). "
            f"Move detail into references/ files."
        )
    elif line_count > MAX_LINES * 0.8:
        warnings.append(
            f"File is {line_count}/{MAX_LINES} lines — approaching limit. "
            f"Consider moving detail to references/."
        )

    # --- 3. Examples ---
    example_count = content.count("<example>")
    if example_count == 0:
        errors.append(
            "No <example> blocks found. Include 1-3 realistic examples "
            "wrapped in <example> tags."
        )

    # Check for matching closing tags
    close_count = content.count("</example>")
    if example_count != close_count:
        errors.append(
            f"Mismatched example tags: {example_count} opening, {close_count} closing"
        )

    # --- 4. Required sections ---
    has_title = bool(re.search(r"^# .+", content, re.MULTILINE))
    has_when = bool(re.search(r"##.*(?:when|trigger|activat)", content, re.IGNORECASE | re.MULTILINE))
    has_workflow = bool(re.search(r"##.*(?:workflow|process|steps|how|phase|usage)", content, re.IGNORECASE | re.MULTILINE))

    if not has_title:
        errors.append("Missing title (# heading)")
    if not has_when:
        warnings.append("No 'When to use' section found — helps models decide when to activate")
    if not has_workflow:
        warnings.append("No workflow/process section found — the core content of any skill")

    # --- 5. Writing style ---
    heavy_patterns = []
    for i, line in enumerate(lines, 1):
        # Skip code blocks
        if line.strip().startswith("```") or line.strip().startswith("|"):
            continue
        # Find ALL-CAPS MUST/ALWAYS/NEVER not in a reasoning context
        matches = re.findall(r"\b(MUST|ALWAYS|NEVER|REQUIRED|MANDATORY|FORBIDDEN)\b", line)
        for m in matches:
            heavy_patterns.append((i, m, line.strip()[:80]))

    if len(heavy_patterns) > MAX_HEAVY_HANDED:
        warnings.append(
            f"Found {len(heavy_patterns)} heavy-handed directives "
            f"(MUST/ALWAYS/NEVER/etc.) — consider explaining reasoning instead. "
            f"First at line {heavy_patterns[0][0]}: '{heavy_patterns[0][2]}'"
        )

    # --- 6. Dangling references/ links ---
    # Match relative `references/<path>` links (not full paths like ~/.../references/).
    # Supports nested subdirectories (e.g. references/subdir/foo.md).
    # Non-relative patterns (shared/references/...) are excluded by the negative
    # lookbehind which rejects any `/` or word character immediately before `references/`.
    # Reports as WARNING: new check should not retroactively fail existing skills.
    skill_dir = path.parent
    # Allow `/` inside the capture so that nested paths like subdir/foo.md are captured.
    raw_mentions = re.findall(
        r"(?<![/\w])references/((?:[^\s`)\]\"]+/)*[^\s`)\]\"]+\.[a-zA-Z0-9]+)",
        content,
    )
    # Deduplicate: warn once per distinct referenced path, not once per occurrence.
    for ref_name in sorted(set(raw_mentions)):
        # Reject paths containing `..` traversal segments so that a crafted reference
        # like `references/../SKILL.md` cannot escape the references/ directory.
        # Path().parts splits on separators and preserves `..` as a literal component
        # while normalising away single `.` segments — making it safe to test each part.
        path_parts = Path(ref_name).parts  # `..` is preserved; `.` is normalised away
        if any(p == ".." for p in path_parts):
            warnings.append(
                f"Suspicious reference path skipped: `references/{ref_name}` contains "
                f"`..` — references must stay inside the skill's references/ directory."
            )
            continue
        ref_path = skill_dir / "references" / ref_name
        if not ref_path.exists():
            warnings.append(
                f"Dangling reference: `references/{ref_name}` is mentioned but "
                f"the file does not exist in the skill's references/ directory. "
                f"Create the file or remove the link (setup-project.py won't deploy it)."
            )

    # --- 7. Security checks ---
    # validate_security() inspects the full content for injection, destructive
    # commands, exfiltration patterns, and obfuscation.  Findings are folded
    # into errors (critical/high) or warnings (medium/low) to preserve the
    # existing (errors, warnings) contract.  Each message is prefixed with
    # "[SECURITY:<SEVERITY>]" so callers can distinguish security findings.
    for f in validate_security(content):
        prefix = f"[SECURITY:{f.severity.upper()}] "
        if f.severity in (SEVERITY_CRITICAL, SEVERITY_HIGH):
            errors.append(prefix + f.message)
        else:
            warnings.append(prefix + f.message)

    return errors, warnings


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 validate-skill.py <path-to-SKILL.md>")
        print("       python3 validate-skill.py <path-to-skill-dir/>")
        sys.exit(1)

    path = Path(sys.argv[1])
    errors, warnings = validate(path)

    # --- Output ---
    if path.is_dir():
        display_path = path / "SKILL.md"
    else:
        display_path = path

    line_count = 0
    if display_path.exists():
        line_count = len(display_path.read_text(encoding="utf-8-sig", errors="replace").splitlines())

    print(f"\n{'='*60}")
    print(f"  Skill Validation: {display_path.name}")
    print(f"  Path: {display_path}")
    print(f"  Lines: {line_count}")
    print(f"{'='*60}\n")

    if errors:
        print(f"❌ ERRORS ({len(errors)}):")
        for e in errors:
            print(f"  • {e}")
        print()

    if warnings:
        print(f"⚠️  WARNINGS ({len(warnings)}):")
        for w in warnings:
            print(f"  • {w}")
        print()

    # Severity summary — count security findings by severity level
    _sec_prefix = "[SECURITY:"
    def _sev(items: list[str], level: str) -> int:
        tag = f"[SECURITY:{level.upper()}]"
        return sum(1 for x in items if x.startswith(tag))

    crit = _sev(errors, SEVERITY_CRITICAL)
    high = _sev(errors, SEVERITY_HIGH)
    med  = _sev(warnings, SEVERITY_MEDIUM)
    low  = _sev(warnings, SEVERITY_LOW)
    sec_total = crit + high + med + low
    if sec_total:
        parts = []
        if crit: parts.append(f"critical={crit}")
        if high: parts.append(f"high={high}")
        if med:  parts.append(f"medium={med}")
        if low:  parts.append(f"low={low}")
        print(f"🔒 Security findings: {' '.join(parts)}\n")

    # Verdict: FAIL / WARN / PASS
    if errors:
        print("Verdict: FAIL ❌\n")
        print("❌ FAIL — fix errors above before using this skill.\n")
        sys.exit(1)
    elif warnings:
        print("Verdict: WARN ⚠️\n")
        print("✅ No errors (warnings above are suggestions)\n")
        sys.exit(0)
    else:
        print("Verdict: PASS ✅\n")
        print("✅ All checks passed!\n")
        sys.exit(0)


if __name__ == "__main__":
    main()
