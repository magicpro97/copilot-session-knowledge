#!/usr/bin/env python3
"""preToolUse template: require valid TDD/quality evidence before task_complete."""

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

if os.name == "nt":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

EVIDENCE_BASE_DIR = Path("test-results") / "strict-tdd"
MAX_EVIDENCE_AGE_HOURS = 48
STATE_FILE = Path(".tdd-pipeline-active")
PHASES = (
    ("phase1-red", "test-output.log", "red"),
    ("phase2-green", "test-output.log", "green"),
    ("phase3-review", "review-report.md", "review"),
    ("phase4-execution", "test-output.log", "execution"),
    ("phase5-qa-audit", "audit-report.md", "audit"),
)


def deny(reason: str) -> None:
    print(json.dumps({"permissionDecision": "deny", "permissionDecisionReason": reason}))
    raise SystemExit(0)


def git_output(*args: str) -> str:
    try:
        result = subprocess.run(["git", *args], capture_output=True, text=True, timeout=10)
    except Exception:
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def load_state() -> tuple[str, str]:
    if not STATE_FILE.is_file():
        return "", ""
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return "", ""
    return str(data.get("feature", "")), str(data.get("started_sha", ""))


def current_feature() -> tuple[str, str]:
    feature, started_sha = load_state()
    if feature:
        return feature, started_sha
    branch = git_output("rev-parse", "--abbrev-ref", "HEAD")
    if branch:
        candidate = branch.rsplit("/", 1)[-1]
        if (EVIDENCE_BASE_DIR / candidate).is_dir():
            return candidate, ""
    if EVIDENCE_BASE_DIR.is_dir():
        dirs = [p for p in EVIDENCE_BASE_DIR.iterdir() if p.is_dir()]
        if dirs:
            return sorted(dirs, key=lambda p: p.stat().st_mtime)[-1].name, ""
    return "", ""


def newest_evidence_file(evidence_dir: Path) -> Path | None:
    files = [p for p in evidence_dir.rglob("*") if p.is_file() and p.suffix.lower() in {".log", ".md", ".json"}]
    return max(files, key=lambda p: p.stat().st_mtime) if files else None


def validate_red(phase_dir: Path) -> str:
    evidence = phase_dir / "evidence.json"
    if not evidence.is_file():
        return ""
    try:
        data = json.loads(evidence.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return "RED phase evidence.json is invalid JSON"
    result = data.get("result", "")
    if result not in {"FAIL", "SKIPPED"}:
        return f"RED phase must show FAIL or SKIPPED (got: {result or 'empty'})"
    if result == "SKIPPED" and not data.get("reason"):
        return "RED phase SKIPPED without reason"
    return ""


def validate_green(phase_dir: Path) -> str:
    log = phase_dir / "test-output.log"
    if log.is_file():
        text = log.read_text(encoding="utf-8", errors="ignore")
        if re.search(r"(pass|passed|OK|succeeded|All.*tests)", text, re.IGNORECASE):
            return ""
    evidence = phase_dir / "evidence.json"
    if evidence.is_file():
        try:
            if json.loads(evidence.read_text(encoding="utf-8")).get("exit_code") == 0:
                return ""
        except json.JSONDecodeError:
            pass
    return "GREEN phase: no passing tests found in output"


def validate_review(phase_dir: Path) -> str:
    report = phase_dir / "review-report.md"
    if report.is_file() and not re.search(
        r"(verdict|status).*CLEAN|\bCLEAN\b", report.read_text(encoding="utf-8", errors="ignore"), re.IGNORECASE
    ):
        return "REVIEW phase: verdict is not CLEAN"
    return ""


def validate_execution(phase_dir: Path) -> str:
    log = phase_dir / "test-output.log"
    if log.is_file():
        text = log.read_text(encoding="utf-8", errors="ignore")
        failures = len(re.findall(r"^\s*(FAIL|x|ERROR)\s", text, re.IGNORECASE | re.MULTILINE))
        if failures:
            return f"EXECUTION phase: test output contains {failures} failure(s)"
    return ""


def validate_audit(phase_dir: Path) -> str:
    report = phase_dir / "audit-report.md"
    if not report.is_file():
        return ""
    text = report.read_text(encoding="utf-8", errors="ignore")
    if re.search(r"(verdict|result)\s*[:=]\s*APPROVED", text, re.IGNORECASE):
        return ""
    verdicts = re.findall(r"APPROVED|REJECTED", text)
    if verdicts and verdicts[0] == "APPROVED" and verdicts[-1] == "APPROVED":
        return ""
    if verdicts and verdicts[0] == "REJECTED" and verdicts[-1] == "APPROVED":
        return "AUDIT: was REJECTED then APPROVED appended (suspicious)"
    return f"AUDIT: verdict is not APPROVED (found: {verdicts[-1] if verdicts else 'none'})"


VALIDATORS = {
    "red": validate_red,
    "green": validate_green,
    "review": validate_review,
    "execution": validate_execution,
    "audit": validate_audit,
}


def main() -> int:
    try:
        data = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError:
        return 0
    if data.get("toolName") != "task_complete":
        return 0

    feature, started_sha = current_feature()
    if not feature:
        return 0
    evidence_dir = EVIDENCE_BASE_DIR / feature
    if not evidence_dir.is_dir():
        return 0

    newest = newest_evidence_file(evidence_dir)
    if newest:
        age_hours = (time.time() - newest.stat().st_mtime) / 3600
        if age_hours > MAX_EVIDENCE_AGE_HOURS:
            deny(
                f"TDD evidence expired: newest file is {int(age_hours)}h old (max {MAX_EVIDENCE_AGE_HOURS}h). Re-run pipeline for '{feature}'."
            )

    missing: list[str] = []
    invalid: list[str] = []
    for dirname, required_file, validator_name in PHASES:
        phase_dir = evidence_dir / dirname
        evidence_json = phase_dir / "evidence.json"
        if not (phase_dir / required_file).is_file() and not evidence_json.is_file():
            missing.append(dirname)
            continue
        reason = VALIDATORS[validator_name](phase_dir)
        if reason:
            invalid.append(f"{dirname}({reason})")

    if missing:
        deny(
            f"TDD Pipeline incomplete for '{feature}'. Missing: {' '.join(missing)}. Complete ALL phases before task_complete."
        )
    if invalid:
        deny(f"TDD Pipeline evidence invalid for '{feature}': {' '.join(invalid)}. Fix and re-run.")

    current_sha = git_output("rev-parse", "HEAD")
    if started_sha and current_sha and started_sha != "unknown":
        result = subprocess.run(
            ["git", "merge-base", "--is-ancestor", started_sha, current_sha], capture_output=True, text=True
        )
        if result.returncode != 0:
            deny(
                f"TDD evidence created for {started_sha} but HEAD is {current_sha} (not a descendant). Evidence may be from a different branch."
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
