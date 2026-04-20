#!/usr/bin/env python3
"""
Tests for conductor.py — each test = 1 trust guarantee.

Run: python3 .github/skills/conductor/scripts/test-conductor.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from conductor import (
    load_rules, classify_task, select_workflow, select_skills,
    select_agents, build_plan, check_phrase_priority, detect_multi_module,
    word_match,
)

rules = load_rules()

passed = 0
failed = 0
total = 0


def test(name: str, condition: bool, detail: str = ""):
    global passed, failed, total
    total += 1
    if condition:
        passed += 1
        print(f"  ✅ {name}")
    else:
        failed += 1
        print(f"  ❌ {name}")
        if detail:
            print(f"     → {detail}")


print("=" * 60)
print("  Conductor Trust Tests")
print("=" * 60)

# ── Word Boundary Matching ──────────────────────────────────

print("\n## Word Boundary Matching")

# Positive matches (should match)
test("'add' matches 'add field'", word_match("add", "add field"))
test("'api' matches 'create api'", word_match("api", "create api"))
test("'new' matches 'create new handler'", word_match("new", "create new handler"))
test("'test' matches 'write test'", word_match("test", "write test"))
test("'log' matches 'check log output'", word_match("log", "check log output"))

# Negative matches (should NOT match — substring false positives)
test("'add' NOT in 'address'", not word_match("add", "update address field"))
test("'api' NOT in 'capital'", not word_match("api", "check capital budget"))
test("'new' NOT in 'renewal'", not word_match("new", "discuss renewal process"))
test("'log' NOT in 'login'", not word_match("log", "create login screen"))
test("'log' NOT in 'dialog'", not word_match("log", "show dialog box"))
test("'test' NOT in 'contest'", not word_match("test", "enter contest now"))
test("'fix' NOT in 'prefix'", not word_match("fix", "add prefix to name"))

# Multi-word phrases (substring match is fine)
test("'unit test' matches in text", word_match("unit test", "write unit test for handler"))
test("'new feature' matches in text", word_match("new feature", "add new feature to app"))
test("'pull request' matches in text", word_match("pull request", "create pull request"))

# ── Task Classification ─────────────────────────────────────

print("\n## Task Classification")

# Features
for task in [
    "implement patient export API",
    "create new Lambda handler",
    "add search screen",
    "build login component",
    "tạo endpoint mới cho patient",
]:
    result = classify_task(task, rules)
    test(f"'{task}' → feature", result.chosen == "feature", f"got: {result.chosen}")

# Bugs
for task in [
    "fix DynamoDB timeout error",
    "sửa lỗi null pointer in patient query",
    "bug: crash when login",
    "investigate regression in export",
    "điều tra lỗi 500 trong API",
]:
    result = classify_task(task, rules)
    test(f"'{task}' → bug", result.chosen == "bug", f"got: {result.chosen}")

# Refactor
for task in [
    "refactor mapping functions",
    "rename patient fields",
    "simplify repository code",
    "optimize DynamoDB queries",
]:
    result = classify_task(task, rules)
    test(f"'{task}' → refactor", result.chosen == "refactor", f"got: {result.chosen}")

# Ops
for task in [
    "check CloudWatch logs for errors",
    "deploy CDK stack to staging",
    "send test message to SQS queue",
    "refresh AWS token",
]:
    result = classify_task(task, rules)
    test(f"'{task}' → ops", result.chosen == "ops", f"got: {result.chosen}")

# Docs
for task in [
    "create mermaid diagram",
    "write README documentation",
    "generate PDF report",
    "convert excel spec to markdown",
]:
    result = classify_task(task, rules)
    test(f"'{task}' → docs", result.chosen == "docs", f"got: {result.chosen}")

# Tests
for task in [
    "write e2e tests for login",
    "add missing unit tests",
    "playwright test for patient flow",
]:
    result = classify_task(task, rules)
    test(f"'{task}' → test", result.chosen == "test", f"got: {result.chosen}")

# ── Vietnamese Classification ────────────────────────────────

print("\n## Vietnamese Classification")

vn_tests = [
    ("tạo chức năng xuất dữ liệu", "feature"),
    ("thêm tính năng mới cho bệnh nhân", "feature"),
    ("sửa lỗi không hoạt động khi đăng nhập", "bug"),
    ("tìm nguyên nhân sự cố timeout", "bug"),
    ("tái cấu trúc mã nguồn repository", "refactor"),
    ("đơn giản hóa logic xử lý", "refactor"),
    ("triển khai lên staging", "ops"),
    ("giám sát hệ thống", "ops"),
    ("viết tài liệu kiến trúc", "docs"),
    ("tạo biểu đồ luồng xử lý", "docs"),
    ("kiểm thử chức năng đăng nhập", "test"),
    ("thiết lập môi trường dev", "config"),
]

for task, expected in vn_tests:
    result = classify_task(task, rules)
    test(f"VN: '{task}' → {expected}", result.chosen == expected, f"got: {result.chosen}")

# ── Phrase Priority ──────────────────────────────────────────

print("\n## Phrase Priority Rules")

result = classify_task("review and fix the authentication code", rules)
test("'review and fix' → bug (phrase rule)", result.chosen == "bug", f"got: {result.chosen}")
test("'review and fix' → high confidence", result.confidence == "high", f"got: {result.confidence}")

result = classify_task("optimize performance of DynamoDB queries", rules)
test("'optimize performance' → refactor (phrase rule)", result.chosen == "refactor", f"got: {result.chosen}")
test("'optimize performance' → high confidence", result.confidence == "high", f"got: {result.confidence}")

result = classify_task("create a test account for staging", rules)
test("'create test account' → ops (phrase rule)", result.chosen == "ops", f"got: {result.chosen}")

result = classify_task("write test for patient handler", rules)
test("'write test' → test (phrase rule)", result.chosen == "test", f"got: {result.chosen}")

result = classify_task("add test for export function", rules)
test("'add test' → test (phrase rule)", result.chosen == "test", f"got: {result.chosen}")

result = classify_task("viết test cho DynamoDB repository", rules)
test("'viết test' → test (phrase rule)", result.chosen == "test", f"got: {result.chosen}")

result = classify_task("investigate and fix the null pointer crash", rules)
test("'investigate and fix' → bug (phrase rule)", result.chosen == "bug", f"got: {result.chosen}")

result = classify_task("check log for deployment errors", rules)
test("'check log' → ops (phrase rule)", result.chosen == "ops", f"got: {result.chosen}")

# Vietnamese phrase rules
result = classify_task("tối ưu hiệu năng Lambda functions", rules)
test("VN: 'tối ưu hiệu năng' → refactor", result.chosen == "refactor", f"got: {result.chosen}")

result = classify_task("viết tài liệu hướng dẫn sử dụng", rules)
test("VN: 'viết tài liệu' → docs", result.chosen == "docs", f"got: {result.chosen}")

result = classify_task("rà soát và sửa lỗi authentication", rules)
test("VN: 'rà soát và sửa' → bug", result.chosen == "bug", f"got: {result.chosen}")

# ── Substring Safety (Word Boundary) ────────────────────────

print("\n## Substring Safety")

result = classify_task("update address field on patient form", rules)
test("'address' does NOT trigger 'add'", "add" not in (result.matches[0].matched_keywords if result.matches else []), f"got: {result.matches[0].matched_keywords if result.matches else 'no matches'}")

result = classify_task("show confirmation dialog to user", rules)
test("'dialog' does NOT trigger 'log'", result.chosen != "ops", f"got: {result.chosen} (should not be ops)")

result = classify_task("add prefix to patient names", rules)
test("'prefix' does NOT trigger 'fix'", result.chosen != "bug", f"got: {result.chosen} (should not be bug)")

# ── Multi-Module Detection ──────────────────────────────────

print("\n## Multi-Module Detection")

zones = detect_multi_module("implement Lambda API + frontend screen", rules)
test("Lambda + screen → 2 zones", len(zones) >= 2, f"got: {zones}")
test("Lambda + screen → backend zone", "backend" in zones, f"got: {zones}")
test("Lambda + screen → frontend zone", "frontend" in zones, f"got: {zones}")

zones = detect_multi_module("create DynamoDB repository with shared model", rules)
test("DynamoDB + shared → 2 zones", len(zones) >= 2, f"got: {zones}")

zones = detect_multi_module("fix Lambda timeout", rules)
test("Lambda only → 1 zone", len(zones) == 1, f"got: {zones}")

zones = detect_multi_module("write README", rules)
test("No module keywords → 0 zones", len(zones) == 0, f"got: {zones}")

plan = build_plan("implement patient search: Lambda API + DynamoDB query + frontend screen", rules)
test("Multi-module plan → has tentacle-orchestration", "tentacle-orchestration" in plan.skills, f"got: {plan.skills}")
test("Multi-module plan → has multi-module warning", any("Multi-module" in w for w in plan.warnings), f"got: {plan.warnings}")

# ── Workflow Selection ──────────────────────────────────────

print("\n## Workflow Selection")

workflow_map = {
    "feature": "strict-tdd",
    "bug": "lite-fix",
    "refactor": "refactor-flow",
    "ops": "ops-only",
    "docs": "docs-only",
    "analysis": "analysis-only",
    "test": "test-only",
    "config": "config-only",
}

for task_type, expected_wf in workflow_map.items():
    result = select_workflow(task_type, rules)
    test(f"{task_type} → {expected_wf}", result.chosen == expected_wf, f"got: {result.chosen}")

# ── Skill Selection ─────────────────────────────────────────

print("\n## Skill Selection")

skills, _ = select_skills("create Lambda handler for API", "feature", rules)
test("Lambda feature → api-development", "api-development" in skills, f"got: {skills}")
test("Lambda feature → lambda-registration", "lambda-registration" in skills, f"got: {skills}")
test("Lambda feature → coding-standards (always)", "coding-standards" in skills, f"got: {skills}")

skills, _ = select_skills("implement DynamoDB repository for patient", "feature", rules)
test("DynamoDB feature → dynamodb-guidelines", "dynamodb-guidelines" in skills, f"got: {skills}")

skills, _ = select_skills("create data export function", "feature", rules)
test("Export feature → data-export-patterns", "data-export-patterns" in skills, f"got: {skills}")

skills, _ = select_skills("fix DynamoDB query timeout", "bug", rules)
test("DynamoDB bug → bug-investigation (always)", "bug-investigation" in skills, f"got: {skills}")
test("DynamoDB bug → dynamodb-guidelines", "dynamodb-guidelines" in skills, f"got: {skills}")

skills, _ = select_skills("check CloudWatch logs", "ops", rules)
test("CloudWatch ops → aws-cloudwatch", "aws-cloudwatch" in skills, f"got: {skills}")
test("CloudWatch ops → infra-quick-ref (always)", "infra-quick-ref" in skills, f"got: {skills}")

skills, _ = select_skills("create mermaid diagram", "docs", rules)
test("Mermaid docs → mermaid-diagram-generator", "mermaid-diagram-generator" in skills, f"got: {skills}")

skills, _ = select_skills("write e2e playwright tests", "test", rules)
test("E2E test → testing-patterns (always)", "testing-patterns" in skills, f"got: {skills}")
test("E2E test → e2e-testing", "e2e-testing" in skills, f"got: {skills}")

skills, _ = select_skills("write architecture documentation", "docs", rules)
test("Docs → technical-writing (always)", "technical-writing" in skills, f"got: {skills}")

skills, _ = select_skills("write markdown with fact verification", "docs", rules)
test("Docs fact → fact-check", "fact-check" in skills, f"got: {skills}")
test("Docs md → markdownlint-configuration", "markdownlint-configuration" in skills, f"got: {skills}")

skills, _ = select_skills("customer reported login bug", "bug", rules)
test("Customer bug → customer-qa", "customer-qa" in skills, f"got: {skills}")

skills, _ = select_skills("analyze prompt engineering approach", "analysis", rules)
test("Prompt analysis → prompt-engineering", "prompt-engineering" in skills, f"got: {skills}")

skills, _ = select_skills("setup copilot agent configuration", "config", rules)
test("Agent config → agent-creator", "agent-creator" in skills, f"got: {skills}")

skills, _ = select_skills("test patient API endpoint", "test", rules)
test("API test → api-testing", "api-testing" in skills, f"got: {skills}")

# New orphan routing tests
skills, _ = select_skills("analyze framework documentation", "analysis", rules)
test("Analysis docs → context7", "context7" in skills, f"got: {skills}")
test("Analysis docs → context-hub", "context-hub" in skills, f"got: {skills}")

skills, _ = select_skills("check timestamp duration for Lambda", "ops", rules)
test("Timestamp ops → date-time", "date-time" in skills, f"got: {skills}")

skills, _ = select_skills("investigate bug with timestamp issues", "bug", rules)
test("Timestamp bug → date-time", "date-time" in skills, f"got: {skills}")

# ── Agent Selection ─────────────────────────────────────────

print("\n## Agent Selection")

agents, models = select_agents("create Lambda handler API", "feature", rules)
test("Backend feature → lambda-developer", agents.get("build") == "lambda-developer", f"got: {agents}")
test("Backend feature → test-engineer", agents.get("test") == "test-engineer", f"got: {agents}")
test("Backend feature → code-reviewer", agents.get("review") == "code-reviewer", f"got: {agents}")

agents, models = select_agents("create patient screen UI component", "feature", rules)
test("Frontend feature → frontend-developer", agents.get("build") == "frontend-developer", f"got: {agents}")

agents, models = select_agents("fix timeout error", "bug", rules)
test("Bug → explore (investigate)", agents.get("investigate") == "explore", f"got: {agents}")
test("Bug → general-purpose (fix)", agents.get("fix") == "general-purpose", f"got: {agents}")

agents, models = select_agents("write unit tests for patient", "test", rules)
test("Test → test-engineer (build)", agents.get("build") == "test-engineer", f"got: {agents}")
test("Test → code-reviewer (review)", agents.get("review") == "code-reviewer", f"got: {agents}")

agents, models = select_agents("write e2e playwright tests", "test", rules)
test("E2E test → e2e-test-agent (build)", agents.get("build") == "e2e-test-agent", f"got: {agents}")

agents, models = select_agents("write technical documentation", "docs", rules)
test("Docs → doc-generator (build)", agents.get("build") == "doc-generator", f"got: {agents}")

agents, models = select_agents("deploy CDK stack", "ops", rules)
test("Ops → empty agents (direct work)", len(agents) == 0, f"got: {agents}")

agents, models = select_agents("setup eslint config", "config", rules)
test("Config → empty agents (direct work)", len(agents) == 0, f"got: {agents}")

# ── Model Assignments ──────────────────────────────────────

print("\n## Model Assignments")

agents, models = select_agents("create Lambda handler API", "feature", rules)
test("lambda-developer → sonnet-4.6", models.get("lambda-developer") == "claude-sonnet-4.6", f"got: {models}")
test("test-engineer → sonnet-4.6", models.get("test-engineer") == "claude-sonnet-4.6", f"got: {models}")
test("code-reviewer → sonnet-4.6", models.get("code-reviewer") == "claude-sonnet-4.6", f"got: {models}")
test("qa-auditor → opus-4.6", models.get("qa-auditor") == "claude-opus-4.6", f"got: {models}")

agents, models = select_agents("fix error", "bug", rules)
test("explore → haiku-4.5", models.get("explore") == "claude-haiku-4.5", f"got: {models}")

agents, models = select_agents("write unit tests", "test", rules)
test("test-engineer (test type) → sonnet-4.6", models.get("test-engineer") == "claude-sonnet-4.6", f"got: {models}")

# ── Full Plan Integration ────────────────────────────────────

print("\n## Full Plan Integration")

plan = build_plan("implement patient export API with SQS queue", rules)
test("Full plan: type=feature", plan.task_type.chosen == "feature")
test("Full plan: workflow=strict-tdd", plan.workflow.chosen == "strict-tdd")
test("Full plan: has data-export-patterns", "data-export-patterns" in plan.skills)
test("Full plan: has aws-serverless-eda", "aws-serverless-eda" in plan.skills)
test("Full plan: has mandatory briefing", "session-knowledge:briefing" in plan.mandatory_steps)
test("Full plan: has mandatory learn", "session-knowledge:learn" in plan.mandatory_steps)
test("Full plan: has mandatory review", "code-reviewer:review-fix-loop" in plan.mandatory_steps)
test("Full plan: has mandatory PR", "pr-workflow:create-pr" in plan.mandatory_steps)

plan = build_plan("fix crash", rules, override_type="feature")
test("Override type: feature", plan.task_type.chosen == "feature")
test("Override type: reason says OVERRIDE", "OVERRIDE" in plan.task_type.reason)

plan = build_plan("fix crash", rules, override_workflow="strict-tdd")
test("Override workflow: strict-tdd", plan.workflow.chosen == "strict-tdd")

# ── Confidence Scoring ──────────────────────────────────────

print("\n## Confidence Scoring")

result = classify_task("implement new Lambda API endpoint", rules)
test("Strong feature signal → high confidence", result.confidence == "high", f"got: {result.confidence}")

result = classify_task("update something", rules)
test("Weak signal → not high confidence", result.confidence != "high", f"got: {result.confidence}")

# ── Edge Cases ──────────────────────────────────────────────

print("\n## Edge Cases")

result = classify_task("asdfghjkl", rules)
test("Garbage input → feature (default)", result.chosen == "feature", f"got: {result.chosen}")
test("Garbage input → low confidence", result.confidence == "low", f"got: {result.confidence}")

result = classify_task("tạo new API để fix bug cũ", rules)
test("Mixed intent → resolves (not crash)", result.chosen in ["feature", "bug"], f"got: {result.chosen}")

result = classify_task("create login screen for hospital", rules)
test("'login' → feature (not ops)", result.chosen == "feature", f"got: {result.chosen}")

skills, _ = select_skills("do random things", "feature", rules)
test("Random task → at least coding-standards", "coding-standards" in skills, f"got: {skills}")

# ── Orphan Audit ────────────────────────────────────────────

print("\n## Orphan Audit")

import os
referenced = set()
for task_type, routing in rules["skill_routing"].items():
    referenced.update(routing.get("always", []))
    for pattern, skills_list in routing.get("conditional", {}).items():
        referenced.update(skills_list)

installed = set()
for d in os.listdir(str(Path(__file__).parent / "../../../")):
    skill_path = Path(__file__).parent / f"../../../{d}/SKILL.md"
    if skill_path.exists():
        installed.add(d)

# Skills that are intentionally not in routing
intentional = {"find-skills", "conductor", "conductor-creator", "session-knowledge",
               "strict-tdd-workflow", "tentacle-orchestration", "tentacle-creator",
               "session-knowledge-creator", "workflow-creator"}
orphans = sorted(installed - referenced - intentional)

test(f"Orphan count ≤ 3 (got {len(orphans)})", len(orphans) <= 3, f"orphans: {orphans}")
test(f"Referenced ≥ 50 skills (got {len(referenced)})", len(referenced) >= 50, f"referenced: {len(referenced)}")

# ── Sync Feature ─────────────────────────────────────────────

print("\n## Sync Feature")

from conductor import (
    scan_disk_skills, collect_routed_skills, get_excluded_skills,
    guess_task_type, run_sync, format_sync,
)

disk = scan_disk_skills()
test(f"Scan finds skills on disk (got {len(disk)})", len(disk) >= 50, f"count: {len(disk)}")
test("Scan returns descriptions", any(v for v in disk.values()), f"sample: {list(disk.items())[:2]}")

routed = collect_routed_skills(rules)
test(f"Collect routed (got {len(routed)})", len(routed) >= 50, f"count: {len(routed)}")

excluded = get_excluded_skills(rules)
test("Excluded includes conductor", "conductor" in excluded, f"got: {excluded}")
test("Excluded includes find-skills", "find-skills" in excluded, f"got: {excluded}")
test("Excluded includes meta-skills", "session-knowledge" in excluded, f"got: {excluded}")

gtype, gconf = guess_task_type("test and verify code quality", rules)
test("Guess type from description", gtype == "test", f"got: {gtype}")

gtype2, _ = guess_task_type("deploy infrastructure to cloud", rules)
test("Guess ops from deploy desc", gtype2 == "ops", f"got: {gtype2}")

report = run_sync(rules)
test(f"Sync finds 0 new unrouted", len(report.new_skills) == 0, f"new: {report.new_skills}")
test(f"Sync finds 0 stale refs", len(report.stale) == 0, f"stale: {report.stale}")
test(f"Sync in_sync count matches", len(report.in_sync) >= 50, f"got: {len(report.in_sync)}")

sync_output = format_sync(report)
test("Sync report contains coverage", "Coverage:" in sync_output, f"output: {sync_output[:200]}")
test("Sync report says in sync", "All skills are in sync" in sync_output, f"output: {sync_output[-200:]}")

# ── Summary ─────────────────────────────────────────────────

print("\n" + "=" * 60)
print(f"  Results: {passed}/{total} passed, {failed} failed")
print("=" * 60)

if failed > 0:
    sys.exit(1)
