#!/usr/bin/env python3
"""
retro.py — Read-only retrospective CLI for copilot-session-knowledge.

Aggregates signals from knowledge health, skill/tentacle outcomes,
hook audit decisions, and git history into a compact operator dashboard.

Usage:
    python3 retro.py                         # Full text report (local mode)
    python3 retro.py --json                  # Full JSON payload
    python3 retro.py --score                 # Single composite score line
    python3 retro.py --subreport <section>   # One section: knowledge|skills|hooks|git
    python3 retro.py --mode repo             # Repo-only mode (git signals only, no local DBs)
    python3 retro.py --days N                # Lookback window in days (default 30)
    python3 retro.py --stale N               # Staleness threshold in days for knowledge (default 30)

Modes:
    local (default) — reads knowledge.db, skill-metrics.db, audit.jsonl, git history
    repo            — reads only git history; safe for CI and environments without local DBs

Read-only guarantees:
    - No writes to any database
    - No issue creation, PR creation, or git commits
    - No hook binding or indexing side effects
    - State cache (.retro-state.json) is written locally but never committed
"""

import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

# Fix Windows console encoding
if os.name == "nt":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

SCRIPT_DIR = Path(__file__).resolve().parent
SESSION_STATE = Path.home() / ".copilot" / "session-state"
MARKERS_DIR = Path.home() / ".copilot" / "markers"
AUDIT_JSONL = MARKERS_DIR / "audit.jsonl"
KNOWLEDGE_DB = SESSION_STATE / "knowledge.db"
SKILL_METRICS_DB = SESSION_STATE / "skill-metrics.db"
RETRO_STATE = SCRIPT_DIR / ".retro-state.json"

_VALID_SECTIONS = ("knowledge", "skills", "hooks", "git")
_VALID_MODES = ("local", "repo")

# Maximum lines to read from audit.jsonl (safety limit)
_AUDIT_MAX_LINES = 5000


# ── Import-safe module loader ────────────────────────────────────────────────


def _load_module(name: str, filename: str):
    """Load a sibling script as a module without running its main()."""
    path = SCRIPT_DIR / filename
    if not path.exists():
        return None
    try:
        spec = importlib.util.spec_from_file_location(name, str(path))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    except Exception:
        return None


# ── Signal collectors ────────────────────────────────────────────────────────


def collect_knowledge_signals(stale_days: int = 30) -> dict:
    """Collect knowledge health signals by reusing knowledge-health.py's compute_health()."""
    base: dict = {
        "available": False,
        "score": 0,
        "total": 0,
        "categories": {},
        "mistakes": 0,
        "patterns": 0,
        "mp_ratio": 0.0,
        "fresh_7d": 0,
        "stale_count": 0,
        "stale_pct": 0.0,
        "sessions": 0,
        "embed_pct": 0.0,
        "relation_density": 0.0,
        "subscores": {},
    }
    if not KNOWLEDGE_DB.exists():
        return base

    mod = _load_module("knowledge_health", "knowledge-health.py")
    if mod and hasattr(mod, "compute_health"):
        original = getattr(mod, "DB_PATH", None)
        try:
            # Temporarily redirect DB_PATH in the module to our known path.
            mod.DB_PATH = KNOWLEDGE_DB
            data = mod.compute_health(stale_days=stale_days)
            data["available"] = True
            return {**base, **data}
        except SystemExit:
            pass
        except Exception:
            pass
        finally:
            if original is not None:
                mod.DB_PATH = original

    # Fallback: minimal direct DB read if module load fails
    try:
        db = sqlite3.connect(str(KNOWLEDGE_DB))
        db.row_factory = sqlite3.Row
        total = db.execute("SELECT COUNT(*) FROM knowledge_entries").fetchone()[0]
        if total == 0:
            db.close()
            return {**base, "available": True, "total": 0}
        cats = db.execute("SELECT category, COUNT(*) as cnt FROM knowledge_entries GROUP BY category").fetchall()
        cat_counts = {r["category"]: r["cnt"] for r in cats}
        mistakes = cat_counts.get("mistake", 0)
        patterns = cat_counts.get("pattern", 0)
        db.close()
        return {
            **base,
            "available": True,
            "total": total,
            "categories": cat_counts,
            "mistakes": mistakes,
            "patterns": patterns,
        }
    except Exception:
        return {**base, "available": True}


def collect_skill_signals() -> dict:
    """Collect skill/tentacle outcome signals by reusing skill-metrics.py's collect_status()."""
    base: dict = {
        "available": False,
        "total_outcomes": 0,
        "outcomes_complete": 0,
        "outcomes_failed": 0,
        "verifications_passed": 0,
        "verifications_failed": 0,
        "total_verifications": 0,
        "skill_usage": [],
        "recent_outcomes": [],
    }
    if not SKILL_METRICS_DB.exists():
        return base

    mod = _load_module("skill_metrics", "skill-metrics.py")
    if mod and hasattr(mod, "collect_status"):
        try:
            data = mod.collect_status(db_path=SKILL_METRICS_DB)
            data["available"] = data.get("db_exists", False)
            return {**base, **data}
        except Exception:
            pass

    return base


def collect_audit_signals(audit_path: Path = AUDIT_JSONL) -> dict:
    """Parse audit.jsonl and compute hook decision stats."""
    out: dict = {
        "available": False,
        "total_entries": 0,
        "decisions": {},
        "top_rules": [],
        "top_denied_tools": [],
        "deny_rate": 0.0,
        "parse_error_rate": 0.0,
    }
    if not audit_path.exists():
        return out

    entries = []
    try:
        with open(audit_path, encoding="utf-8", errors="replace") as f:
            for i, line in enumerate(f):
                if i >= _AUDIT_MAX_LINES:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    except OSError:
        return out

    if not entries:
        return {**out, "available": True}

    decisions: dict = {}
    rules: dict = {}
    denied_tools: dict = {}

    for e in entries:
        d = e.get("decision", "")
        decisions[d] = decisions.get(d, 0) + 1
        rule = e.get("rule", "")
        if rule:
            rules[rule] = rules.get(rule, 0) + 1
        if d in ("deny", "deny-dry"):
            tool = e.get("tool", "")
            if tool:
                denied_tools[tool] = denied_tools.get(tool, 0) + 1

    total = len(entries)
    denied = decisions.get("deny", 0) + decisions.get("deny-dry", 0)
    parse_errors = decisions.get("parse-error", 0)

    top_rules = sorted(rules.items(), key=lambda x: -x[1])[:10]
    top_denied = sorted(denied_tools.items(), key=lambda x: -x[1])[:5]

    return {
        "available": True,
        "total_entries": total,
        "decisions": decisions,
        "top_rules": [{"rule": r, "count": c} for r, c in top_rules],
        "top_denied_tools": [{"tool": t, "count": c} for t, c in top_denied],
        "deny_rate": round(denied / total * 100, 1) if total > 0 else 0.0,
        "parse_error_rate": round(parse_errors / total * 100, 1) if total > 0 else 0.0,
    }


def collect_git_signals(repo_root: Path = SCRIPT_DIR, days: int = 30) -> dict:
    """Collect git history signals using subprocess (repo-visible, safe for CI)."""
    out: dict = {
        "available": False,
        "lookback_days": days,
        "commit_count": 0,
        "authors": [],
        "test_files_changed": 0,
        "py_files_changed": 0,
        "distinct_files_changed": 0,
        "recent_commits": [],
        "top_changed_files": [],
    }
    try:
        since = f"{days}.days"
        # Commit summary
        log_result = subprocess.run(
            ["git", "--no-pager", "log", f"--since={since}", "--format=%H|%as|%aN|%s"],
            capture_output=True,
            text=True,
            cwd=str(repo_root),
            timeout=15,
        )
        if log_result.returncode != 0:
            return out

        commits = []
        authors: dict = {}
        for line in log_result.stdout.strip().splitlines():
            parts = line.split("|", 3)
            if len(parts) < 4:
                continue
            sha, date, author, subject = parts
            commits.append({"sha": sha[:8], "date": date, "author": author, "subject": subject[:80]})
            authors[author] = authors.get(author, 0) + 1

        # File change stats
        files_result = subprocess.run(
            ["git", "--no-pager", "log", f"--since={since}", "--name-only", "--format="],
            capture_output=True,
            text=True,
            cwd=str(repo_root),
            timeout=15,
        )
        file_counts: dict = {}
        for line in files_result.stdout.strip().splitlines():
            line = line.strip()
            if line:
                file_counts[line] = file_counts.get(line, 0) + 1

        py_files = {f for f in file_counts if f.endswith(".py")}
        test_files = {f for f in py_files if Path(f).name.startswith("test_")}
        top_files = sorted(file_counts.items(), key=lambda x: -x[1])[:10]

        out.update(
            {
                "available": True,
                "lookback_days": days,
                "commit_count": len(commits),
                "authors": sorted(authors.items(), key=lambda x: -x[1]),
                "test_files_changed": len(test_files),
                "py_files_changed": len(py_files),
                "distinct_files_changed": len(file_counts),
                "recent_commits": commits[:5],
                "top_changed_files": [{"file": f, "changes": c} for f, c in top_files],
            }
        )
    except (subprocess.TimeoutExpired, OSError, ValueError):
        pass

    return out


# ── Scoring ──────────────────────────────────────────────────────────────────


def _score_knowledge(k: dict) -> float:
    """Subscore 0-100 from knowledge signals."""
    if not k.get("available") or k.get("total", 0) == 0:
        return 0.0
    return float(k.get("score", 0))


def _score_skills(s: dict) -> float:
    """Subscore 0-100 from skill/tentacle signals."""
    if not s.get("available"):
        return 0.0
    total_v = int(s.get("total_verifications", 0))
    if total_v == 0:
        # No verifications yet — neutral 50 if we have any outcomes
        return 50.0 if int(s.get("total_outcomes", 0)) > 0 else 0.0
    passed = int(s.get("verifications_passed", 0))
    return round(passed / total_v * 100, 1)


def _score_hooks(h: dict) -> float:
    """Subscore 0-100 from hook audit signals (lower deny/error rate = higher score)."""
    if not h.get("available") or h.get("total_entries", 0) == 0:
        return 0.0
    deny_rate = float(h.get("deny_rate", 0))
    parse_rate = float(h.get("parse_error_rate", 0))
    # High deny rate is bad; high parse error rate indicates config problems
    score = 100.0 - (deny_rate * 0.5) - (parse_rate * 1.0)
    return round(max(0.0, min(100.0, score)), 1)


def _score_git(g: dict) -> float:
    """Subscore 0-100 from git history signals."""
    if not g.get("available"):
        return 0.0
    commits = int(g.get("commit_count", 0))
    days = int(g.get("lookback_days", 30))
    distinct = int(g.get("distinct_files_changed", 0))
    test_files = int(g.get("test_files_changed", 0))
    py_files = int(g.get("py_files_changed", 0))

    # Activity score: normalise commit cadence (aim for at least 1/day over period)
    target_commits = days
    activity = min(commits / max(target_commits, 1) * 100, 100)

    # Test coverage signal: ratio of test files to all py files changed
    test_ratio = (test_files / max(py_files, 1)) * 100 if py_files > 0 else 50.0

    # File breadth: diminishing returns above 20 distinct files
    breadth = min(distinct / 20 * 100, 100)

    score = activity * 0.5 + test_ratio * 0.3 + breadth * 0.2
    return round(min(100.0, max(0.0, score)), 1)


def compute_retro(
    knowledge: dict,
    skills: dict,
    hooks: dict,
    git: dict,
    mode: str = "local",
) -> dict:
    """Compute the full retrospective payload with composite score."""
    k_score = _score_knowledge(knowledge)
    s_score = _score_skills(skills)
    h_score = _score_hooks(hooks)
    g_score = _score_git(git)

    if mode == "repo":
        # Repo-only: only git is available
        composite = g_score
        weights = {"git": 1.0}
        available_sections = ["git"]
    else:
        # Local: weighted composite
        available = []
        weights_raw: dict = {}
        if knowledge.get("available") and knowledge.get("total", 0) > 0:
            available.append("knowledge")
            weights_raw["knowledge"] = (k_score, 0.35)
        if skills.get("available"):
            available.append("skills")
            weights_raw["skills"] = (s_score, 0.30)
        if hooks.get("available") and hooks.get("total_entries", 0) > 0:
            available.append("hooks")
            weights_raw["hooks"] = (h_score, 0.15)
        if git.get("available"):
            available.append("git")
            weights_raw["git"] = (g_score, 0.20)

        available_sections = available

        if not weights_raw:
            composite = 0.0
            weights = {}
        else:
            # Renormalize weights to sum to 1.0
            total_w = sum(w for _, w in weights_raw.values())
            composite = sum(sc * (w / total_w) for sc, w in weights_raw.values())
            composite = round(composite, 1)
            weights = {k: round(w / total_w, 3) for k, (_, w) in weights_raw.items()}

    if composite >= 80:
        grade, grade_emoji = "Excellent", "🏆"
    elif composite >= 60:
        grade, grade_emoji = "Good", "✅"
    elif composite >= 40:
        grade, grade_emoji = "Fair", "🟡"
    else:
        grade, grade_emoji = "Needs Work", "🔴"

    return {
        "retro_score": round(composite, 1),
        "grade": grade,
        "grade_emoji": grade_emoji,
        "mode": mode,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "available_sections": available_sections,
        "weights": weights,
        "subscores": {
            "knowledge": k_score,
            "skills": s_score,
            "hooks": h_score,
            "git": g_score,
        },
        "knowledge": knowledge,
        "skills": skills,
        "hooks": hooks,
        "git": git,
    }


# ── State cache ───────────────────────────────────────────────────────────────


def load_state(path: Path = RETRO_STATE) -> dict:
    """Load cached retro state (gracefully returns empty dict on any error)."""
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_state(payload: dict, path: Path = RETRO_STATE) -> None:
    """Write retro state cache. Best-effort; never raises."""
    try:
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


# ── Formatters ────────────────────────────────────────────────────────────────


def _bar(value: float, width: int = 20) -> str:
    filled = int(round(value / 100 * width))
    return "█" * filled + "░" * (width - filled)


def format_score_line(payload: dict) -> str:
    score = payload.get("retro_score", 0)
    grade = payload.get("grade", "")
    emoji = payload.get("grade_emoji", "")
    mode = payload.get("mode", "local")
    bar = _bar(score)
    return f"{emoji} Retro score: {score}/100 ({grade})  [{bar}]  mode={mode}"


def format_knowledge_section(k: dict) -> list:
    lines = ["Knowledge Health"]
    if not k.get("available"):
        lines.append("  (not available — run in local mode with knowledge.db)")
        return lines
    total = k.get("total", 0)
    if total == 0:
        lines.append("  (empty knowledge base)")
        return lines
    score = k.get("score", 0)
    lines.append(f"  Score:          {score}/100  [{_bar(score)}]")
    lines.append(f"  Total entries:  {total:,}  (sessions: {k.get('sessions', 0)})")
    lines.append(
        f"  Fresh (7d):     {k.get('fresh_7d', 0)}   Stale: {k.get('stale_count', 0)} ({k.get('stale_pct', 0)}%)"
    )
    mp = k.get("mp_ratio", 0)
    mp_str = f"{mp}x" if mp != float("inf") else "∞"
    lines.append(
        f"  Pattern/Mistake ratio: {mp_str}  (mistakes={k.get('mistakes', 0)}, patterns={k.get('patterns', 0)})"
    )
    lines.append(f"  Embed coverage: {k.get('embed_pct', 0)}%   Relation density: {k.get('relation_density', 0)}")
    cats = k.get("categories", {})
    if cats:
        top = sorted(cats.items(), key=lambda x: -x[1])[:5]
        lines.append("  Categories:     " + "  ".join(f"{c}={n}" for c, n in top))
    return lines


def format_skills_section(s: dict) -> list:
    lines = ["Skill & Tentacle Outcomes"]
    if not s.get("available"):
        lines.append("  (not available — run in local mode with skill-metrics.db)")
        return lines
    total = s.get("total_outcomes", 0)
    if total == 0:
        lines.append("  (no outcomes recorded yet)")
        return lines
    complete = s.get("outcomes_complete", 0)
    failed = s.get("outcomes_failed", 0)
    total_v = s.get("total_verifications", 0)
    passed_v = s.get("verifications_passed", 0)
    failed_v = s.get("verifications_failed", 0)
    pass_rate = round(passed_v / max(total_v, 1) * 100, 1) if total_v > 0 else 0.0
    lines.append(f"  Outcomes:       {total} total  ({complete} complete, {failed} failed)")
    lines.append(f"  Verifications:  {total_v} total  ({passed_v} passed, {failed_v} failed)  pass rate: {pass_rate}%")
    skill_usage = s.get("skill_usage", [])
    if skill_usage:
        top = skill_usage[:5]
        lines.append("  Top skills:     " + "  ".join(f"{e['skill']}×{e['uses']}" for e in top))
    recent = s.get("recent_outcomes", [])
    if recent:
        lines.append("  Recent:")
        for r in recent[:3]:
            vp = r.get("verification_passed", 0)
            vf = r.get("verification_failed", 0)
            lines.append(
                f"    [{r['outcome_status']:<8}] {r['tentacle_name'][:30]}"
                f"  v={vp}✓/{vf}✗  {r.get('recorded_at', '')[:10]}"
            )
    return lines


def format_hooks_section(h: dict) -> list:
    lines = ["Hook Audit Activity"]
    if not h.get("available"):
        lines.append("  (not available — audit.jsonl not found)")
        return lines
    total = h.get("total_entries", 0)
    if total == 0:
        lines.append("  (no audit entries)")
        return lines
    decisions = h.get("decisions", {})
    deny_rate = h.get("deny_rate", 0.0)
    parse_rate = h.get("parse_error_rate", 0.0)
    lines.append(f"  Total entries:  {total}")
    decision_parts = "  ".join(f"{d}={n}" for d, n in sorted(decisions.items()))
    lines.append(f"  Decisions:      {decision_parts}")
    lines.append(f"  Deny rate:      {deny_rate}%   Parse-error rate: {parse_rate}%")
    top_rules = h.get("top_rules", [])
    if top_rules:
        lines.append("  Top rules:      " + "  ".join(f"{e['rule']}×{e['count']}" for e in top_rules[:5]))
    denied_tools = h.get("top_denied_tools", [])
    if denied_tools:
        lines.append("  Denied tools:   " + "  ".join(f"{e['tool']}×{e['count']}" for e in denied_tools[:5]))
    return lines


def format_git_section(g: dict) -> list:
    lines = ["Git Activity"]
    if not g.get("available"):
        lines.append("  (git signals unavailable)")
        return lines
    days = g.get("lookback_days", 30)
    commits = g.get("commit_count", 0)
    distinct = g.get("distinct_files_changed", 0)
    py_files = g.get("py_files_changed", 0)
    test_files = g.get("test_files_changed", 0)
    lines.append(f"  Lookback:       {days} days")
    lines.append(f"  Commits:        {commits}")
    lines.append(f"  Files changed:  {distinct} distinct  ({py_files} .py, {test_files} test_*.py)")
    authors = g.get("authors", [])
    if authors:
        lines.append("  Authors:        " + "  ".join(f"{a}×{n}" for a, n in authors[:3]))
    top_files = g.get("top_changed_files", [])
    if top_files:
        lines.append("  Most changed:")
        for entry in top_files[:5]:
            lines.append(f"    {entry['file']:<40} ×{entry['changes']}")
    recent = g.get("recent_commits", [])
    if recent:
        lines.append("  Recent commits:")
        for c in recent[:3]:
            lines.append(f"    {c['date']}  {c['sha']}  {c['subject']}")
    return lines


def format_text_report(payload: dict) -> str:
    lines = [
        "╔══════════════════════════════════════════════════════╗",
        f"║  {payload['grade_emoji']} Retrospective  score={payload['retro_score']}/100 ({payload['grade']})",
        f"║  [{_bar(payload['retro_score'], 20)}]  mode={payload['mode']}  {payload['generated_at']}",
        "╚══════════════════════════════════════════════════════╝",
        "",
    ]

    subscores = payload.get("subscores", {})
    weights = payload.get("weights", {})
    lines.append("Subscores")
    for section in _VALID_SECTIONS:
        sc = subscores.get(section, 0.0)
        w = weights.get(section)
        w_str = f"  (weight={w:.0%})" if w is not None else "  (n/a)"
        avail = section in payload.get("available_sections", [])
        flag = "" if avail else "  [unavailable]"
        lines.append(f"  {section:<12} {sc:5.1f}/100  [{_bar(sc, 12)}]{w_str}{flag}")

    lines.append("")
    lines.extend(format_knowledge_section(payload.get("knowledge", {})))
    lines.append("")
    lines.extend(format_skills_section(payload.get("skills", {})))
    lines.append("")
    lines.extend(format_hooks_section(payload.get("hooks", {})))
    lines.append("")
    lines.extend(format_git_section(payload.get("git", {})))

    return "\n".join(lines)


def format_subreport(payload: dict, section: str) -> str:
    """Render a single section of the report."""
    section = section.lower()
    if section not in _VALID_SECTIONS:
        return f"Unknown section '{section}'. Valid: {', '.join(_VALID_SECTIONS)}"
    fmt = {
        "knowledge": format_knowledge_section,
        "skills": format_skills_section,
        "hooks": format_hooks_section,
        "git": format_git_section,
    }
    return "\n".join(fmt[section](payload.get(section, {})))


# ── Entry point ───────────────────────────────────────────────────────────────


def _parse_args(argv: list) -> dict:
    args = {
        "mode": "local",
        "days": 30,
        "stale": 30,
        "output": "text",
        "subreport": None,
        "help": False,
        "no_cache": False,
    }
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in ("-h", "--help"):
            args["help"] = True
        elif a == "--json":
            args["output"] = "json"
        elif a == "--score":
            args["output"] = "score"
        elif a == "--subreport":
            args["output"] = "subreport"
            if i + 1 < len(argv):
                i += 1
                args["subreport"] = argv[i]
        elif a == "--mode":
            if i + 1 < len(argv):
                i += 1
                args["mode"] = argv[i]
        elif a == "--days":
            if i + 1 < len(argv):
                i += 1
                try:
                    args["days"] = max(1, int(argv[i]))
                except ValueError:
                    pass
        elif a == "--stale":
            if i + 1 < len(argv):
                i += 1
                try:
                    args["stale"] = max(1, int(argv[i]))
                except ValueError:
                    pass
        elif a == "--no-cache":
            args["no_cache"] = True
        i += 1
    return args


def main() -> None:
    args = _parse_args(sys.argv[1:])

    if args["help"]:
        print(__doc__)
        return

    mode = args["mode"]
    if mode not in _VALID_MODES:
        print(f"Error: --mode must be one of: {', '.join(_VALID_MODES)}", file=sys.stderr)
        sys.exit(1)

    days = args["days"]
    stale = args["stale"]

    # Collect signals
    if mode == "repo":
        knowledge = {"available": False}
        skills = {"available": False}
        hooks = {"available": False}
    else:
        knowledge = collect_knowledge_signals(stale_days=stale)
        skills = collect_skill_signals()
        hooks = collect_audit_signals()

    git = collect_git_signals(days=days)

    payload = compute_retro(knowledge, skills, hooks, git, mode=mode)

    # Cache state (best-effort, skips if --no-cache)
    if not args["no_cache"]:
        save_state(payload)

    output = args["output"]
    if output == "json":
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    elif output == "score":
        print(format_score_line(payload))
    elif output == "subreport":
        section = args.get("subreport") or ""
        print(format_subreport(payload, section))
    else:
        print(format_text_report(payload))


if __name__ == "__main__":
    main()
