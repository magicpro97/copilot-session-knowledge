#!/usr/bin/env python3
"""
audit-instructions.py - Instruction effectiveness audit for copilot-session-knowledge.

Parses the instruction surfaces used by the repo, audits the numbered rules in
.github/copilot-instructions.md, cross-references learn.py knowledge signals from
the shared SQLite DB, and reports drift between the canonical/full instruction
files.

Usage:
    python audit-instructions.py
    python audit-instructions.py --json
    python audit-instructions.py --repo-root /path/to/repo
    python audit-instructions.py --db-path /path/to/knowledge.db
    python audit-instructions.py --top 10
    python audit-instructions.py --fail-on-drift   # opt-in CI gate

Exit codes:
0 - report produced successfully (default; preserved unless --fail-on-drift)
1 - required instruction files missing, no numbered rules found, OR
    (with --fail-on-drift) parity / Quality Checklist anchor drift detected
2 - bad arguments
"""

import argparse
import json
import os
import re
import sqlite3
import sys
from pathlib import Path

if os.name == "nt":
    for _stream in (sys.stdout, sys.stderr):
        if hasattr(_stream, "reconfigure"):
            _stream.reconfigure(encoding="utf-8", errors="replace")

DEFAULT_REPO_ROOT = Path(__file__).resolve().parent
DEFAULT_DB_PATH = Path.home() / ".copilot" / "session-state" / "knowledge.db"
DEFAULT_TOP = 10

COPILOT_INSTRUCTIONS = Path(".github") / "copilot-instructions.md"
AGENT_RULES = Path("docs") / "AGENT-RULES.md"
AGENTS_SUMMARY = Path("AGENTS.md")

QUALITY_CHECKLIST_ANCHOR = "quality checklist"

_MARKDOWN_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]+\)")
_HEADING_RE = re.compile(r"^(#{2,6})\s+(.*)$")
_RULE_HEADING_RE = re.compile(r"^(?:Rule\s+)?(\d+)\s*[—\-:]\s*(.+)$", re.IGNORECASE)
_NUMBERED_HEADING_RE = re.compile(r"^(\d+)\.\s+(.+)$")
_AGENTS_RULE_RE = re.compile(r"^\s*(\d+)\.\s+(.*)$")
_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9-]{3,}")
_STOPWORDS = frozenset(
    {
        "about",
        "action",
        "acting",
        "agent",
        "agents",
        "all",
        "also",
        "always",
        "before",
        "being",
        "build",
        "code",
        "command",
        "commands",
        "complex",
        "docs",
        "during",
        "each",
        "every",
        "file",
        "files",
        "from",
        "into",
        "instruction",
        "instructions",
        "must",
        "never",
        "only",
        "output",
        "phase",
        "prompt",
        "quality",
        "report",
        "repo",
        "require",
        "requires",
        "rule",
        "rules",
        "section",
        "should",
        "source",
        "still",
        "summary",
        "task",
        "tasks",
        "that",
        "then",
        "these",
        "they",
        "this",
        "those",
        "tool",
        "tools",
        "when",
        "with",
        "without",
        "work",
    }
)


def _strip_markdown(text: str) -> str:
    text = _MARKDOWN_LINK_RE.sub(r"\1", text)
    text = text.replace("**", "").replace("__", "").replace("`", "")
    return re.sub(r"\s+", " ", text).strip()


def _normalize_title(text: str) -> str:
    normalized = _strip_markdown(text).lower()
    normalized = re.sub(r"[^a-z0-9]+", " ", normalized)
    return re.sub(r"\s+", " ", normalized).strip()


def _normalize_body(text: str) -> str:
    body = _strip_markdown(text).lower()
    body = re.sub(r"https?://\S+", "", body)
    body = re.sub(r"[^a-z0-9\s]", " ", body)
    return re.sub(r"\s+", " ", body).strip()


def _parse_rule_heading(raw_heading: str) -> tuple[int, str] | None:
    heading = _strip_markdown(raw_heading)
    match = _RULE_HEADING_RE.match(heading)
    if match:
        return int(match.group(1)), match.group(2).strip()
    match = _NUMBERED_HEADING_RE.match(heading)
    if match:
        return int(match.group(1)), match.group(2).strip()
    return None


def _finalize_rule(current: dict | None, dest: list[dict]) -> None:
    if not current:
        return
    body_lines = current.pop("body_lines")
    body = "\n".join(body_lines).strip()
    current["body"] = body
    current["line_count"] = len([line for line in body_lines if line.strip()])
    dest.append(current)


def _extract_heading_rules(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []

    rules: list[dict] = []
    current: dict | None = None
    in_code = False

    for lineno, raw_line in enumerate(lines, 1):
        line = raw_line.rstrip("\n")
        stripped = line.strip()
        if stripped.startswith("```"):
            in_code = not in_code
            if current:
                current["body_lines"].append(line)
            continue
        if in_code:
            if current:
                current["body_lines"].append(line)
            continue

        heading_match = _HEADING_RE.match(line)
        if heading_match:
            parsed = _parse_rule_heading(heading_match.group(2))
            if parsed:
                _finalize_rule(current, rules)
                number, title = parsed
                current = {
                    "number": number,
                    "title": title,
                    "body_lines": [],
                    "source": str(path),
                    "line_start": lineno,
                }
            else:
                _finalize_rule(current, rules)
                current = None
            continue

        if current:
            current["body_lines"].append(line)

    _finalize_rule(current, rules)
    return rules


def _extract_agents_rules(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []

    rules: list[dict] = []
    in_section = False
    for lineno, raw_line in enumerate(lines, 1):
        line = raw_line.rstrip("\n")
        if line.startswith("## "):
            heading = _normalize_title(line[3:])
            if heading == "mandatory rules":
                in_section = True
                continue
            if in_section:
                break
        if not in_section:
            continue
        match = _AGENTS_RULE_RE.match(line)
        if not match:
            continue
        number = int(match.group(1))
        rest = _strip_markdown(match.group(2))
        title = rest.split("—", 1)[0].split(" - ", 1)[0].strip().rstrip(".")
        rules.append(
            {
                "number": number,
                "title": title,
                "body": rest,
                "source": str(path),
                "line_start": lineno,
                "line_count": 1,
            }
        )
    return rules


def _load_knowledge_entries(db_path: Path, limit: int = 250) -> tuple[list[dict], bool]:
    if not db_path.exists():
        return [], False
    try:
        db = sqlite3.connect(str(db_path))
    except sqlite3.Error:
        return [], False
    try:
        columns = {row[1] for row in db.execute("PRAGMA table_info(knowledge_entries)").fetchall()}
        if "knowledge_entries" not in {
            row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }:
            return [], False
        ts_column = ""
        for candidate in ("created_at", "last_seen", "first_seen"):
            if candidate in columns:
                ts_column = candidate
                break
        ts_expr = f"COALESCE({ts_column}, '')" if ts_column else "''"
        rows = db.execute(
            f"""
            SELECT category, title, content, COALESCE(created_at, '')
            FROM knowledge_entries
            WHERE category IN ('mistake', 'pattern', 'decision', 'discovery', 'feature', 'refactor', 'tool')
            ORDER BY {ts_expr} DESC, id DESC
            LIMIT ?
            """.replace("COALESCE(created_at, '')", ts_expr),
            (limit,),
        ).fetchall()
    except sqlite3.Error:
        return [], False
    finally:
        db.close()
    entries = [
        {
            "category": str(row[0] or ""),
            "title": str(row[1] or ""),
            "content": str(row[2] or ""),
            "created_at": str(row[3] or ""),
        }
        for row in rows
    ]
    return entries, True


def _rule_keywords(rule: dict, limit: int = 8) -> list[str]:
    ordered_tokens: list[str] = []
    seen: set[str] = set()
    text = f"{rule.get('title', '')} {rule.get('body', '')[:240]}"
    for token in _TOKEN_RE.findall(text.lower()):
        if token in _STOPWORDS:
            continue
        if token not in seen:
            seen.add(token)
            ordered_tokens.append(token)
        if len(ordered_tokens) >= limit:
            break
    return ordered_tokens


def _match_knowledge_entries(rule: dict, entries: list[dict]) -> dict:
    keywords = _rule_keywords(rule)
    category_counts: dict[str, int] = {}
    matches: list[dict] = []
    for entry in entries:
        haystack = f"{entry['title']} {entry['content']}".lower()
        matched = [keyword for keyword in keywords if keyword in haystack]
        if not matched:
            continue
        category = entry["category"] or "unknown"
        category_counts[category] = category_counts.get(category, 0) + 1
        matches.append(
            {
                "category": category,
                "title": entry["title"],
                "created_at": entry["created_at"],
                "matched_keywords": matched,
            }
        )

    matches.sort(key=lambda item: (-len(item["matched_keywords"]), item["title"].lower()))
    return {
        "keywords": keywords,
        "category_counts": category_counts,
        "total_matches": len(matches),
        "top_matches": matches[:3],
    }


def _rule_signals(rule: dict, evidence: dict) -> list[str]:
    signals: list[str] = []
    mistake_hits = evidence["category_counts"].get("mistake", 0)
    if mistake_hits > 0:
        signals.append("needs_emphasis")
    if evidence["total_matches"] == 0:
        signals.append("candidate_for_removal")
    if rule.get("line_count", 0) >= 25 or len(rule.get("body", "")) >= 1800:
        signals.append("candidate_for_condense")
    if not signals:
        signals.append("healthy")
    return signals


def _recommendation_for_rule(rule: dict, signals: list[str], evidence: dict) -> str:
    number = rule["number"]
    title = rule["title"]
    mistake_hits = evidence["category_counts"].get("mistake", 0)
    if "needs_emphasis" in signals and "candidate_for_condense" in signals:
        return (
            f"Rule {number} ({title}): shorten repeated explanation, but move the core command or "
            f"obligation closer to the heading; {mistake_hits} mistake-linked knowledge entries still match it."
        )
    if "needs_emphasis" in signals:
        return (
            f"Rule {number} ({title}): raise emphasis near the top of the injected prompt; "
            f"{mistake_hits} mistake-linked knowledge entries still reference this behavior."
        )
    if "candidate_for_removal" in signals and "candidate_for_condense" in signals:
        return (
            f"Rule {number} ({title}): no matching learn-db evidence was found and the section is long; "
            "review with a human owner whether it should be removed or collapsed into a shorter summary."
        )
    if "candidate_for_removal" in signals:
        return (
            f"Rule {number} ({title}): no matching learn-db evidence was found; review whether this rule still "
            "deserves prompt budget or belongs only in the canonical long-form docs."
        )
    if "candidate_for_condense" in signals:
        return (
            f"Rule {number} ({title}): keep the rule, but condense the body to protect prompt budget without "
            "dropping the obligation."
        )
    return f"Rule {number} ({title}): keep as-is; learn-db evidence exists and no effectiveness smell was detected."


def _audit_copilot_rules(rules: list[dict], knowledge_entries: list[dict]) -> list[dict]:
    audited: list[dict] = []
    for rule in rules:
        evidence = _match_knowledge_entries(rule, knowledge_entries)
        signals = _rule_signals(rule, evidence)
        audited.append(
            {
                "number": rule["number"],
                "title": rule["title"],
                "source": rule["source"],
                "line_start": rule["line_start"],
                "line_count": rule["line_count"],
                "body_length": len(rule.get("body", "")),
                "signals": signals,
                "evidence": evidence,
                "recommendation": _recommendation_for_rule(rule, signals, evidence),
            }
        )

    priority = {
        "needs_emphasis": 0,
        "candidate_for_removal": 1,
        "candidate_for_condense": 2,
        "healthy": 3,
    }
    audited.sort(
        key=lambda item: (
            min(priority.get(signal, 9) for signal in item["signals"]),
            item["number"],
        )
    )
    return audited


def _has_quality_checklist_anchor(path: Path) -> bool:
    """Return True iff the file contains a '## Quality Checklist' (any case) heading."""
    if not path.exists():
        return False
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return False
    in_code = False
    for raw_line in lines:
        line = raw_line.rstrip("\n")
        stripped = line.strip()
        if stripped.startswith("```"):
            in_code = not in_code
            continue
        if in_code:
            continue
        m = _HEADING_RE.match(line)
        if not m:
            continue
        if _normalize_title(m.group(2)) == QUALITY_CHECKLIST_ANCHOR:
            return True
    return False


def _detect_parity(rule_sets: dict[str, list[dict]]) -> dict:
    """Compute rule-count + title parity across the three instruction surfaces.

    Returns a dict suitable for embedding in the JSON report and for rendering
    in the text report.  Surfaces with zero parsed rules are reported as
    `missing_rules` so consumers can surface them in CI without blowing up the
    drift counts.
    """
    counts = {name: len(rules) for name, rules in rule_sets.items()}
    unique_counts = {count for count in counts.values() if count > 0}
    rule_count_matches = len(unique_counts) <= 1 and all(count > 0 for count in counts.values())

    # Title parity uses AGENT_RULES as the canonical baseline.
    baseline_name = str(AGENT_RULES)
    baseline = {rule["number"]: rule for rule in rule_sets.get(baseline_name, [])}
    title_mismatches: list[dict] = []
    for name, rules in rule_sets.items():
        if name == baseline_name:
            continue
        other = {rule["number"]: rule for rule in rules}
        for number in sorted(set(baseline) | set(other)):
            if number not in baseline or number not in other:
                # Already surfaced via drift / mirror findings; skip here.
                continue
            if _normalize_title(baseline[number]["title"]) != _normalize_title(other[number]["title"]):
                title_mismatches.append(
                    {
                        "rule_number": number,
                        "baseline": baseline_name,
                        "baseline_title": baseline[number]["title"],
                        "surface": name,
                        "surface_title": other[number]["title"],
                    }
                )

    return {
        "counts": counts,
        "rule_count_matches": rule_count_matches,
        "title_mismatches": title_mismatches,
    }


def _detect_rule_drift(
    primary_rules: list[dict], mirror_rules: list[dict], *, primary_name: str, mirror_name: str
) -> list[dict]:
    primary_map = {rule["number"]: rule for rule in primary_rules}
    mirror_map = {rule["number"]: rule for rule in mirror_rules}
    findings: list[dict] = []
    for number in sorted(set(primary_map) | set(mirror_map)):
        if number not in primary_map:
            findings.append(
                {
                    "kind": "missing_rule",
                    "rule_number": number,
                    "missing_from": primary_name,
                    "present_in": mirror_name,
                }
            )
            continue
        if number not in mirror_map:
            findings.append(
                {
                    "kind": "missing_rule",
                    "rule_number": number,
                    "missing_from": mirror_name,
                    "present_in": primary_name,
                }
            )
            continue
        primary_title = primary_map[number]["title"]
        mirror_title = mirror_map[number]["title"]
        if _normalize_title(primary_title) != _normalize_title(mirror_title):
            findings.append(
                {
                    "kind": "title_mismatch",
                    "rule_number": number,
                    "primary_title": primary_title,
                    "mirror_title": mirror_title,
                    "primary": primary_name,
                    "mirror": mirror_name,
                }
            )
    return findings


def _build_report(repo_root: Path, db_path: Path) -> dict | None:
    copilot_path = repo_root / COPILOT_INSTRUCTIONS
    agent_rules_path = repo_root / AGENT_RULES
    agents_path = repo_root / AGENTS_SUMMARY

    copilot_rules = _extract_heading_rules(copilot_path)
    agent_rules = _extract_heading_rules(agent_rules_path)
    agents_rules = _extract_agents_rules(agents_path)

    if not copilot_path.exists() or not agent_rules_path.exists():
        return None
    if not copilot_rules or not agent_rules:
        return None

    knowledge_entries, db_available = _load_knowledge_entries(db_path)
    audited = _audit_copilot_rules(copilot_rules, knowledge_entries)
    ineffective = [rule for rule in audited if "healthy" not in rule["signals"]]
    drift = _detect_rule_drift(
        agent_rules,
        copilot_rules,
        primary_name=str(AGENT_RULES),
        mirror_name=str(COPILOT_INSTRUCTIONS),
    )
    mirror_gaps = _detect_rule_drift(
        agent_rules,
        agents_rules,
        primary_name=str(AGENT_RULES),
        mirror_name=str(AGENTS_SUMMARY),
    )

    recommendations = [rule["recommendation"] for rule in ineffective]
    for finding in drift:
        number = finding["rule_number"]
        if finding["kind"] == "missing_rule":
            recommendations.append(
                f"Sync rule {number}: {finding['missing_from']} is missing it while {finding['present_in']} still carries it."
            )
        else:
            recommendations.append(f"Sync rule {number} titles between {finding['primary']} and {finding['mirror']}.")
    for finding in mirror_gaps:
        number = finding["rule_number"]
        if finding["kind"] == "missing_rule":
            recommendations.append(
                f"Update AGENTS.md summary: rule {number} exists in docs/AGENT-RULES.md but is missing from the concise mirror."
            )

    summary = {
        "rules_parsed": len(copilot_rules),
        "ineffective_count": len(ineffective),
        "needs_emphasis_count": sum("needs_emphasis" in rule["signals"] for rule in audited),
        "candidate_for_removal_count": sum("candidate_for_removal" in rule["signals"] for rule in audited),
        "candidate_for_condense_count": sum("candidate_for_condense" in rule["signals"] for rule in audited),
        "drift_count": len(drift),
        "mirror_gap_count": len(mirror_gaps),
        "knowledge_entries_scanned": len(knowledge_entries),
        "db_available": db_available,
    }

    # Parity across the three instruction surfaces (rule count + title).
    parity = _detect_parity(
        {
            str(AGENT_RULES): agent_rules,
            str(COPILOT_INSTRUCTIONS): copilot_rules,
            str(AGENTS_SUMMARY): agents_rules,
        }
    )
    summary["parity_counts"] = parity["counts"]
    summary["rule_count_parity"] = parity["rule_count_matches"]
    summary["title_mismatch_count"] = len(parity["title_mismatches"])

    # Required '## Quality Checklist' anchor in every surface.
    quality_checklist = {
        str(AGENT_RULES): _has_quality_checklist_anchor(agent_rules_path),
        str(COPILOT_INSTRUCTIONS): _has_quality_checklist_anchor(copilot_path),
        str(AGENTS_SUMMARY): _has_quality_checklist_anchor(agents_path),
    }
    checklist_missing = [name for name, present in quality_checklist.items() if not present]
    summary["quality_checklist_present"] = quality_checklist
    summary["quality_checklist_missing"] = checklist_missing

    for entry in checklist_missing:
        recommendations.append(
            f"Add '## Quality Checklist' anchor to {entry} so the runtime mirror and canonical doc stay in sync."
        )
    if not parity["rule_count_matches"]:
        recommendations.append(
            "Rule-count parity broken across instruction surfaces: "
            + ", ".join(f"{name}={count}" for name, count in parity["counts"].items())
        )
    for mismatch in parity["title_mismatches"]:
        recommendations.append(
            f"Rule {mismatch['rule_number']} title differs between {mismatch['baseline']} "
            f"('{mismatch['baseline_title']}') and {mismatch['surface']} ('{mismatch['surface_title']}')."
        )

    return {
        "summary": summary,
        "repo_root": str(repo_root),
        "db_path": str(db_path),
        "copilot_rules": audited,
        "ineffective_rules": ineffective,
        "drift_findings": drift,
        "mirror_findings": mirror_gaps,
        "parity": parity,
        "quality_checklist": quality_checklist,
        "recommendations": recommendations,
    }


def _render_text(report: dict, top_n: int) -> None:
    print("=" * 72)
    print("  sk audit-instructions - Instruction Effectiveness Audit")
    print("=" * 72)
    print(f"  Repo root           : {report['repo_root']}")
    print(f"  Knowledge DB        : {report['db_path']}")

    summary = report["summary"]
    print("\n-- Summary ------------------------------------------------------")
    print(f"  Numbered rules      : {summary['rules_parsed']}")
    print(f"  Ineffective rules   : {summary['ineffective_count']}")
    print(f"  Needs emphasis      : {summary['needs_emphasis_count']}")
    print(f"  Candidate removal   : {summary['candidate_for_removal_count']}")
    print(f"  Candidate condense  : {summary['candidate_for_condense_count']}")
    print(f"  Drift findings      : {summary['drift_count']}")
    print(f"  AGENTS mirror gaps  : {summary['mirror_gap_count']}")
    parity_label = "yes" if summary.get("rule_count_parity") else "no"
    print(f"  Rule-count parity   : {parity_label}")
    counts_str = ", ".join(f"{name}={count}" for name, count in summary.get("parity_counts", {}).items())
    if counts_str:
        print(f"  Parity counts       : {counts_str}")
    print(f"  Title mismatches    : {summary.get('title_mismatch_count', 0)}")
    checklist_present = summary.get("quality_checklist_present", {})
    if checklist_present:
        checklist_str = ", ".join(f"{name}={'yes' if present else 'NO'}" for name, present in checklist_present.items())
        print(f"  Quality checklist   : {checklist_str}")
    db_label = "yes" if summary["db_available"] else "no"
    print(f"  Learn DB available  : {db_label}")
    print(f"  Knowledge scanned   : {summary['knowledge_entries_scanned']}")

    print("\n-- Ineffective Rules -------------------------------------------")
    ineffective = report["ineffective_rules"][:top_n]
    if not ineffective:
        print("  (no ineffective rules detected with the current heuristics)")
    else:
        for rule in ineffective:
            signals = ", ".join(rule["signals"])
            evidence = rule["evidence"]
            category_counts = evidence["category_counts"]
            counts = ", ".join(f"{key}={value}" for key, value in sorted(category_counts.items())) or "none"
            keyword_str = ", ".join(evidence["keywords"]) or "none"
            print(f"  Rule {rule['number']}: {rule['title']}")
            print(f"    Signals         : {signals}")
            print(f"    Keywords        : {keyword_str}")
            print(f"    Evidence counts : {counts}")
            print(f"    Recommendation  : {rule['recommendation']}")

    print("\n-- Drift Findings ----------------------------------------------")
    if not report["drift_findings"]:
        print("  (no numbered-rule drift found between docs/AGENT-RULES.md and .github/copilot-instructions.md)")
    else:
        for finding in report["drift_findings"]:
            if finding["kind"] == "missing_rule":
                print(
                    f"  Rule {finding['rule_number']}: missing from {finding['missing_from']} "
                    f"(present in {finding['present_in']})"
                )
            else:
                print(
                    f"  Rule {finding['rule_number']}: title mismatch - "
                    f"{finding['primary_title']} vs {finding['mirror_title']}"
                )

    print("\n-- AGENTS Mirror Findings --------------------------------------")
    if not report["mirror_findings"]:
        print("  (no numbered-rule gaps found in AGENTS.md)")
    else:
        for finding in report["mirror_findings"]:
            if finding["kind"] == "missing_rule":
                print(
                    f"  Rule {finding['rule_number']}: missing from {finding['missing_from']} "
                    f"(present in {finding['present_in']})"
                )
            else:
                print(
                    f"  Rule {finding['rule_number']}: title mismatch - "
                    f"{finding['primary_title']} vs {finding['mirror_title']}"
                )

    print("\n-- Recommendations ---------------------------------------------")
    recommendations = report["recommendations"][: max(top_n, 1)]
    if not recommendations:
        print("  (no recommendations)")
    else:
        for idx, recommendation in enumerate(recommendations, 1):
            print(f"  {idx}. {recommendation}")
    print()


def _render_json(report: dict, top_n: int) -> None:
    payload = dict(report)
    payload["ineffective_rules"] = payload["ineffective_rules"][:top_n]
    payload["recommendations"] = payload["recommendations"][: max(top_n, 1)]
    print(json.dumps(payload, indent=2, ensure_ascii=False))


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="audit-instructions",
        description="Audit numbered instruction rules for drift, bloat, and effectiveness signals.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="json_out",
        help="Emit JSON instead of text",
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=DEFAULT_REPO_ROOT,
        metavar="DIR",
        help="Override repository root (default: script sibling checkout)",
    )
    parser.add_argument(
        "--db-path",
        type=Path,
        default=DEFAULT_DB_PATH,
        metavar="PATH",
        help="Override knowledge DB path (default: ~/.copilot/session-state/knowledge.db)",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=DEFAULT_TOP,
        metavar="N",
        help="Limit ineffective-rule and recommendation output to top N items (default 10)",
    )
    parser.add_argument(
        "--fail-on-drift",
        action="store_true",
        dest="fail_on_drift",
        help=(
            "Exit non-zero when rule-count / title parity is broken across the three "
            "instruction surfaces or when any surface is missing the '## Quality Checklist' "
            "anchor. Existing CLI default remains exit 0 on a successful audit."
        ),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    report = _build_report(args.repo_root.resolve(), args.db_path.expanduser())
    if report is None:
        message = (
            "audit-instructions: required instruction files missing, or no numbered rules were found in "
            ".github/copilot-instructions.md / docs/AGENT-RULES.md"
        )
        if args.json_out:
            print(json.dumps({"error": message}, ensure_ascii=False))
        else:
            print(message, file=sys.stderr)
        return 1

    if args.json_out:
        _render_json(report, args.top)
    else:
        _render_text(report, args.top)

    if args.fail_on_drift:
        summary = report.get("summary", {})
        if (
            not summary.get("rule_count_parity", True)
            or summary.get("title_mismatch_count", 0) > 0
            or summary.get("quality_checklist_missing")
            or summary.get("drift_count", 0) > 0
            or summary.get("mirror_gap_count", 0) > 0
        ):
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
