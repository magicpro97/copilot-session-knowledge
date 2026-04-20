#!/usr/bin/env python3
"""
Conductor — Deterministic task router for NEO-MATCH development workflow.

Classifies a task → selects workflow → selects skills + agents.
Every decision is transparent, auditable, and override-able.

Trust mechanism: Each output shows the RULE ID and MATCHED KEYWORDS
that triggered each decision, so you can verify WHY.

Usage:
    python3 .github/skills/conductor/scripts/conductor.py "implement patient export API"
    python3 .github/skills/conductor/scripts/conductor.py "fix DynamoDB timeout error" --verbose
    python3 .github/skills/conductor/scripts/conductor.py "deploy to staging" --override-workflow ops-only
    python3 .github/skills/conductor/scripts/conductor.py --audit  # show all rules
"""

import json
import re
import sys
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional

SKILL_DIR = Path(__file__).parent
RULES_PATH = SKILL_DIR / "conductor-rules.json"


@dataclass
class MatchResult:
    """A single rule match with evidence."""
    rule_id: str
    matched_keywords: list[str]
    score: float
    source: str  # which rule set triggered this


@dataclass
class Decision:
    """A single decision with full reasoning chain."""
    category: str       # e.g., "task_type", "workflow", "skill", "agent"
    chosen: str         # e.g., "feature", "strict-tdd"
    confidence: str     # "high", "medium", "low"
    reason: str         # human-readable explanation
    matches: list[MatchResult] = field(default_factory=list)
    alternatives: list[str] = field(default_factory=list)


@dataclass
class ConductorPlan:
    """Complete task plan with all decisions."""
    task_input: str
    task_type: Decision
    workflow: Decision
    skills: list[str]
    skill_decisions: list[Decision]
    agents: dict[str, str]  # role → agent name
    model_assignments: dict[str, str]  # agent → model
    mandatory_steps: list[str]
    warnings: list[str]


def load_rules() -> dict:
    """Load rules from JSON file."""
    if not RULES_PATH.exists():
        print(f"❌ Rules file not found: {RULES_PATH}", file=sys.stderr)
        sys.exit(1)
    with open(RULES_PATH) as f:
        return json.load(f)


def normalize(text: str) -> str:
    """Normalize text for matching."""
    return text.lower().strip()


def word_match(keyword: str, text: str) -> bool:
    """Match keyword in text using word boundaries.

    Multi-word keywords (containing spaces) use substring matching.
    Single-word keywords use regex word boundaries with common English
    suffixes (s, es, ed, ing, er) to handle plurals and conjugations
    while preventing false positives like 'add' in 'address'.
    """
    if " " in keyword:
        return keyword in text
    pattern = r"\b" + re.escape(keyword) + r"(?:s|es|ed|ing|er)?\b"
    return bool(re.search(pattern, text))


def match_keywords(text: str, keywords: list[str], negative_keywords: list[str] = None) -> MatchResult:
    """Match text against keyword list, return score and matched keywords."""
    text_lower = normalize(text)
    matched = []
    for kw in keywords:
        if word_match(kw.lower(), text_lower):
            matched.append(kw)

    negative_matched = []
    if negative_keywords:
        for nkw in negative_keywords:
            if word_match(nkw.lower(), text_lower):
                negative_matched.append(nkw)

    # Score = matched count - negative penalty
    score = len(matched) - (len(negative_matched) * 0.5)
    return MatchResult(
        rule_id="keyword_match",
        matched_keywords=matched,
        score=max(0, score),
        source=f"matched={matched}, negative={negative_matched}"
    )


def check_phrase_priority(task: str, rules: dict) -> Optional[tuple[str, str]]:
    """Check phrase-level priority rules before keyword matching.

    Returns (type, reason) if a phrase rule matches, None otherwise.
    Phrase rules resolve common ambiguities like 'review and fix' → bug.
    """
    phrase_rules = rules.get("phrase_priority", {}).get("rules", [])
    task_lower = normalize(task)

    for rule in phrase_rules:
        if rule["phrase"].lower() in task_lower:
            return rule["type"], f"Phrase rule: '{rule['phrase']}' → {rule['type']}. {rule['reason']}"
    return None


def detect_multi_module(task: str, rules: dict) -> list[str]:
    """Detect if task spans multiple module zones.

    Returns list of matched zone names. If 2+ zones match, the task
    is cross-cutting and should consider tentacle-orchestration.
    """
    indicators = rules.get("multi_module_indicators", {})
    zones = indicators.get("zones", {})
    task_lower = normalize(task)

    matched_zones = []
    for zone_name, keywords in zones.items():
        if any(word_match(kw.lower(), task_lower) for kw in keywords):
            matched_zones.append(zone_name)
    return matched_zones


def classify_task(task: str, rules: dict) -> Decision:
    """Classify task into a type using phrase priority then keyword matching."""
    # Phase 1: Check phrase-level priority rules first
    phrase_match = check_phrase_priority(task, rules)
    if phrase_match:
        chosen_type, reason = phrase_match
        return Decision(
            category="task_type",
            chosen=chosen_type,
            confidence="high",
            reason=reason,
            matches=[],
            alternatives=[]
        )

    # Phase 2: Fall back to keyword scoring
    task_types = rules["task_types"]
    scores: list[tuple[str, MatchResult]] = []

    for type_name, type_def in task_types.items():
        result = match_keywords(
            task,
            type_def["keywords"],
            type_def.get("negative_keywords", [])
        )
        result.rule_id = f"task_type.{type_name}"
        if result.score > 0:
            scores.append((type_name, result))

    # Sort by score descending
    scores.sort(key=lambda x: x[1].score, reverse=True)

    if not scores:
        return Decision(
            category="task_type",
            chosen="feature",
            confidence="low",
            reason="No keywords matched — defaulting to 'feature' (safest)",
            matches=[],
            alternatives=[]
        )

    best_type, best_match = scores[0]
    alternatives = [s[0] for s in scores[1:3]]

    # Confidence based on score gap
    if len(scores) >= 2:
        gap = best_match.score - scores[1][1].score
        confidence = "high" if gap >= 2 else ("medium" if gap >= 1 else "low")
    else:
        confidence = "high" if best_match.score >= 2 else "medium"

    # Conflict detection
    if len(scores) >= 2 and confidence == "low":
        reason = (
            f"⚠️ CONFLICT: '{best_type}' (score={best_match.score:.1f}) vs "
            f"'{scores[1][0]}' (score={scores[1][1].score:.1f}). "
            f"Matched: {best_match.matched_keywords}. "
            f"Consider --override-type if incorrect."
        )
    else:
        reason = (
            f"Matched {len(best_match.matched_keywords)} keywords: "
            f"{best_match.matched_keywords}"
        )

    return Decision(
        category="task_type",
        chosen=best_type,
        confidence=confidence,
        reason=reason,
        matches=[best_match],
        alternatives=alternatives
    )


def select_workflow(task_type: str, rules: dict) -> Decision:
    """Select workflow based on task type."""
    workflows = rules["workflows"]
    matched_workflow = None

    for wf_name, wf_def in workflows.items():
        if task_type in wf_def["applies_to"]:
            matched_workflow = (wf_name, wf_def)
            break

    if not matched_workflow:
        return Decision(
            category="workflow",
            chosen="strict-tdd",
            confidence="low",
            reason=f"No workflow defined for task_type='{task_type}' — defaulting to strict-tdd",
            alternatives=[]
        )

    wf_name, wf_def = matched_workflow
    return Decision(
        category="workflow",
        chosen=wf_name,
        confidence="high",
        reason=(
            f"Rule: workflows.{wf_name}.applies_to contains '{task_type}'. "
            f"Phases: {' → '.join(wf_def['phases'])}. "
            f"Skip conditions: {wf_def['skip_conditions']}"
        ),
        alternatives=[
            name for name, d in workflows.items()
            if name != wf_name and task_type not in d["applies_to"]
        ][:2]
    )


def select_skills(task: str, task_type: str, rules: dict) -> tuple[list[str], list[Decision]]:
    """Select skills based on task type + conditional keyword matching."""
    routing = rules["skill_routing"]
    if task_type not in routing:
        return [], []

    type_routing = routing[task_type]
    selected = list(type_routing.get("always", []))
    decisions = []

    if selected:
        decisions.append(Decision(
            category="skill",
            chosen=", ".join(selected),
            confidence="high",
            reason=f"Rule: skill_routing.{task_type}.always — always included for this task type"
        ))

    # Conditional matching using word boundaries
    conditionals = type_routing.get("conditional", {})
    task_lower = normalize(task)

    for pattern, skills in conditionals.items():
        # Pattern uses | for OR — each part uses word boundary matching
        parts = [p.strip().lower() for p in pattern.split("|")]
        matched_parts = [p for p in parts if word_match(p, task_lower)]

        if matched_parts:
            for skill in skills:
                if skill not in selected:
                    selected.append(skill)
            decisions.append(Decision(
                category="skill",
                chosen=", ".join(skills),
                confidence="high" if len(matched_parts) >= 2 else "medium",
                reason=(
                    f"Rule: skill_routing.{task_type}.conditional['{pattern}'] "
                    f"— matched: {matched_parts}"
                )
            ))

    return selected, decisions


def select_agents(task: str, task_type: str, rules: dict) -> tuple[dict[str, str], dict[str, str]]:
    """Select agents and model assignments."""
    agent_routing = rules.get("agent_routing", {})
    model_rules = rules.get("model_rules", {})

    agents: dict[str, str] = {}
    models: dict[str, str] = {}

    type_agents = agent_routing.get(task_type, {})
    task_lower = normalize(task)

    # Determine sub-category
    sub_key = "default"
    if task_type == "feature":
        if any(word_match(kw, task_lower) for kw in ["frontend", "screen", "ui", "component"]):
            sub_key = "frontend"
        elif any(word_match(kw, task_lower) for kw in ["shared", "utility", "model"]):
            sub_key = "shared"
        else:
            sub_key = "backend"
    elif task_type == "analysis":
        if any(word_match(kw, task_lower) for kw in ["deep", "full", "comprehensive", "toàn diện"]):
            sub_key = "deep"
        elif any(word_match(kw, task_lower) for kw in ["dynamodb", "repository", "query"]):
            sub_key = "dynamodb"
        else:
            sub_key = "quick"
    elif task_type == "test":
        if any(word_match(kw, task_lower) for kw in ["e2e", "playwright", "browser"]):
            sub_key = "e2e"
        else:
            sub_key = "default"

    role_agents = type_agents.get(sub_key, type_agents.get("default", {}))
    agents = dict(role_agents)

    # Assign models based on role
    for role, agent in agents.items():
        if role in ("review",):
            models[agent] = model_rules.get("code_review", "claude-sonnet-4.6")
        elif role in ("qa",):
            models[agent] = model_rules.get("qa_audit", "claude-opus-4.6")
        elif role in ("investigate", "analyze"):
            models[agent] = model_rules.get("exploration", "claude-haiku-4.5")
        elif role in ("build", "fix", "refactor", "orchestrate"):
            models[agent] = model_rules.get("code_generation", "claude-sonnet-4.6")
        elif role in ("test",):
            models[agent] = model_rules.get("code_generation", "claude-sonnet-4.6")

    return agents, models


def get_mandatory_steps(task_type: str, workflow: str, rules: dict) -> list[str]:
    """Get mandatory pre/post steps."""
    mandatory = rules.get("mandatory_steps", {})
    steps = list(mandatory.get("pre_task", []))

    if task_type in ("feature", "bug", "refactor", "test"):
        steps.extend(mandatory.get("post_code", []))

    if task_type == "feature":
        steps.extend(mandatory.get("post_feature", []))

    steps.extend(mandatory.get("post_task", []))
    return steps


def build_plan(task: str, rules: dict,
               override_type: Optional[str] = None,
               override_workflow: Optional[str] = None) -> ConductorPlan:
    """Build complete task plan."""
    # 1. Classify task
    type_decision = classify_task(task, rules)
    if override_type:
        type_decision.chosen = override_type
        type_decision.confidence = "high"
        type_decision.reason = f"OVERRIDE by user: --override-type {override_type}"

    # 2. Select workflow
    wf_decision = select_workflow(type_decision.chosen, rules)
    if override_workflow:
        wf_decision.chosen = override_workflow
        wf_decision.confidence = "high"
        wf_decision.reason = f"OVERRIDE by user: --override-workflow {override_workflow}"

    # 3. Select skills
    skills, skill_decisions = select_skills(task, type_decision.chosen, rules)

    # 4. Select agents
    agents, models = select_agents(task, type_decision.chosen, rules)

    # 5. Mandatory steps
    mandatory = get_mandatory_steps(type_decision.chosen, wf_decision.chosen, rules)

    # 6. Warnings
    warnings = []
    if type_decision.confidence == "low":
        warnings.append(
            f"⚠️ Low confidence on task type '{type_decision.chosen}'. "
            f"Alternatives: {type_decision.alternatives}. "
            f"Use --override-type to correct."
        )
    if not skills:
        warnings.append("⚠️ No skills matched. Task may not have domain-specific guidance.")

    # 7. Multi-module detection
    matched_zones = detect_multi_module(task, rules)
    threshold = rules.get("multi_module_indicators", {}).get("threshold", 2)
    if len(matched_zones) >= threshold:
        warnings.append(
            f"🔀 Multi-module task detected (zones: {', '.join(matched_zones)}). "
            f"Consider using tentacle-orchestration for parallel work."
        )
        if "tentacle-orchestration" not in skills:
            skills.append("tentacle-orchestration")
            skill_decisions.append(Decision(
                category="skill",
                chosen="tentacle-orchestration",
                confidence="high",
                reason=f"Auto-added: task spans {len(matched_zones)} module zones ({', '.join(matched_zones)})"
            ))

    return ConductorPlan(
        task_input=task,
        task_type=type_decision,
        workflow=wf_decision,
        skills=skills,
        skill_decisions=skill_decisions,
        agents=agents,
        model_assignments=models,
        mandatory_steps=mandatory,
        warnings=warnings
    )


# ── Output Formatting ──────────────────────────────────────────────

CONFIDENCE_EMOJI = {"high": "🟢", "medium": "🟡", "low": "🔴"}


def format_plan(plan: ConductorPlan, verbose: bool = False) -> str:
    """Format plan as human-readable output."""
    lines = []
    lines.append("=" * 60)
    lines.append("  CONDUCTOR — Task Routing Plan")
    lines.append("=" * 60)
    lines.append(f"  Task: {plan.task_input}")
    lines.append("")

    # Task Type
    td = plan.task_type
    emoji = CONFIDENCE_EMOJI[td.confidence]
    lines.append(f"┌─ 1. TASK TYPE: {td.chosen.upper()}  {emoji} {td.confidence}")
    lines.append(f"│  Reason: {td.reason}")
    if td.alternatives:
        lines.append(f"│  Alternatives: {', '.join(td.alternatives)}")
    lines.append("│")

    # Workflow
    wd = plan.workflow
    emoji = CONFIDENCE_EMOJI[wd.confidence]
    lines.append(f"├─ 2. WORKFLOW: {wd.chosen}  {emoji} {wd.confidence}")
    lines.append(f"│  Reason: {wd.reason}")
    lines.append("│")

    # Skills
    lines.append(f"├─ 3. SKILLS ({len(plan.skills)})")
    for sd in plan.skill_decisions:
        lines.append(f"│  ✅ {sd.chosen}")
        if verbose:
            lines.append(f"│     └─ {sd.reason}")
    if not plan.skills:
        lines.append("│  (none)")
    lines.append("│")

    # Agents
    lines.append(f"├─ 4. AGENTS ({len(plan.agents)})")
    for role, agent in plan.agents.items():
        model = plan.model_assignments.get(agent, "default")
        lines.append(f"│  {role}: {agent} (model: {model})")
    if not plan.agents:
        lines.append("│  (no agents needed — direct work)")
    lines.append("│")

    # Mandatory steps
    lines.append(f"├─ 5. MANDATORY STEPS ({len(plan.mandatory_steps)})")
    for step in plan.mandatory_steps:
        lines.append(f"│  ⚠️  {step}")
    lines.append("│")

    # Warnings
    if plan.warnings:
        lines.append("├─ ⚠️  WARNINGS")
        for w in plan.warnings:
            lines.append(f"│  {w}")
        lines.append("│")

    lines.append("└─ Override: --override-type <type> | --override-workflow <workflow>")
    lines.append("")

    return "\n".join(lines)


def format_audit(rules: dict) -> str:
    """Format all rules for audit review."""
    lines = []
    lines.append("=" * 60)
    lines.append("  CONDUCTOR — Full Rule Audit")
    lines.append("=" * 60)
    lines.append("")

    # Task types
    lines.append("## Task Types")
    for name, td in rules["task_types"].items():
        lines.append(f"  {name}: {td['description']}")
        lines.append(f"    Keywords ({len(td['keywords'])}): {', '.join(td['keywords'][:10])}...")
        neg = td.get("negative_keywords", [])
        if neg:
            lines.append(f"    Negative: {', '.join(neg)}")
        lines.append("")

    # Phrase priorities
    phrase_rules = rules.get("phrase_priority", {}).get("rules", [])
    if phrase_rules:
        lines.append("## Phrase Priority Rules")
        for r in phrase_rules:
            lines.append(f"  '{r['phrase']}' → {r['type']} ({r['reason']})")
        lines.append("")

    # Multi-module indicators
    indicators = rules.get("multi_module_indicators", {})
    zones = indicators.get("zones", {})
    if zones:
        lines.append("## Multi-Module Detection")
        lines.append(f"  Threshold: {indicators.get('threshold', 2)} zones")
        for zone, kws in zones.items():
            lines.append(f"  {zone}: {', '.join(kws)}")
        lines.append("")

    # Workflows
    lines.append("## Workflows")
    for name, wd in rules["workflows"].items():
        lines.append(f"  {name} → applies to: {wd['applies_to']}")
        lines.append(f"    Phases: {' → '.join(wd['phases'])}")
        lines.append(f"    Skip: {wd['skip_conditions']}")
        lines.append("")

    # Skill routing
    lines.append("## Skill Routing")
    for task_type, routing in rules["skill_routing"].items():
        always = routing.get("always", [])
        cond = routing.get("conditional", {})
        lines.append(f"  {task_type}:")
        lines.append(f"    Always: {always or '(none)'}")
        for pattern, skills in cond.items():
            lines.append(f"    If [{pattern}] → {skills}")
        lines.append("")

    return "\n".join(lines)


def format_json(plan: ConductorPlan) -> str:
    """Format plan as JSON for programmatic consumption."""
    return json.dumps({
        "task": plan.task_input,
        "task_type": {
            "chosen": plan.task_type.chosen,
            "confidence": plan.task_type.confidence,
            "reason": plan.task_type.reason,
            "alternatives": plan.task_type.alternatives
        },
        "workflow": {
            "chosen": plan.workflow.chosen,
            "confidence": plan.workflow.confidence,
            "reason": plan.workflow.reason
        },
        "skills": plan.skills,
        "agents": plan.agents,
        "model_assignments": plan.model_assignments,
        "mandatory_steps": plan.mandatory_steps,
        "warnings": plan.warnings
    }, indent=2, ensure_ascii=False)


# ── Sync ────────────────────────────────────────────────────────────

PROJECT_ROOT = SKILL_DIR.parent.parent.parent.parent  # scripts→conductor→skills→.github→root
SKILLS_DIR = PROJECT_ROOT / ".github" / "skills"


def scan_disk_skills() -> dict[str, str]:
    """Scan .github/skills/ and return {name: description}."""
    if not SKILLS_DIR.exists():
        return {}

    result = {}
    for d in sorted(SKILLS_DIR.iterdir()):
        if not d.is_dir() or d.name.startswith('.'):
            continue

        desc = ""
        meta = d / ".skill-meta.json"
        if meta.exists():
            try:
                with open(meta) as f:
                    m = json.load(f)
                desc = (m.get("description", "") + " " + m.get("whenToUse", "")).strip()
            except (json.JSONDecodeError, KeyError):
                pass

        if not desc:
            skill_md = d / "SKILL.md"
            if skill_md.exists():
                try:
                    with open(skill_md) as f:
                        content = f.read(2000)
                    match = re.search(r'^description:\s*(.+?)$', content, re.MULTILINE)
                    if match:
                        desc = match.group(1).strip().strip('>').strip()
                except OSError:
                    pass

        result[d.name] = desc[:150]

    return result


def collect_routed_skills(rules: dict) -> set[str]:
    """Collect all skill names referenced in skill_routing."""
    routed: set[str] = set()
    for _tt, routing in rules.get("skill_routing", {}).items():
        for s in routing.get("always", []):
            routed.add(s)
        for _, skills in routing.get("conditional", {}).items():
            for s in skills:
                routed.add(s)
    return routed


def get_excluded_skills(rules: dict) -> set[str]:
    """Get intentionally unrouted skill names from _meta."""
    excluded = {"conductor", "find-skills"}
    for entry in rules.get("_meta", {}).get("intentionally_unrouted", []):
        name = entry.split("(")[0].strip().split(" ")[0].strip()
        excluded.add(name)
    return excluded


def guess_task_type(desc: str, rules: dict) -> tuple[str, str]:
    """Guess which task type a skill belongs to based on its description."""
    if not desc:
        return "feature", "low"

    desc_lower = desc.lower()
    best_type = "feature"
    best_score = 0

    for type_name, type_def in rules.get("task_types", {}).items():
        score = sum(1 for kw in type_def.get("keywords", []) if kw.lower() in desc_lower)
        if score > best_score:
            best_score = score
            best_type = type_name

    confidence = "high" if best_score >= 3 else ("medium" if best_score >= 1 else "low")
    return best_type, confidence


@dataclass
class SyncReport:
    """Skill sync report data."""
    version: str
    disk_skills: dict[str, str]
    routed: set[str]
    excluded: set[str]
    in_sync: list[str]
    new_skills: list[str]
    stale: list[str]
    suggestions: dict[str, tuple[str, str]]  # name → (type, confidence)


def run_sync(rules: dict) -> SyncReport:
    """Compare conductor rules with skills on disk."""
    disk = scan_disk_skills()
    routed = collect_routed_skills(rules)
    excluded = get_excluded_skills(rules)

    in_sync = sorted(set(disk.keys()) & routed)
    new_skills = sorted(set(disk.keys()) - routed - excluded)
    stale = sorted(routed - set(disk.keys()))

    suggestions = {}
    for name in new_skills:
        suggested_type, conf = guess_task_type(disk.get(name, ""), rules)
        suggestions[name] = (suggested_type, conf)

    return SyncReport(
        version=rules.get("_meta", {}).get("version", "?"),
        disk_skills=disk,
        routed=routed,
        excluded=excluded,
        in_sync=in_sync,
        new_skills=new_skills,
        stale=stale,
        suggestions=suggestions
    )


def format_sync(report: SyncReport) -> str:
    """Format sync report for terminal output."""
    lines = []
    lines.append("=" * 60)
    lines.append("  CONDUCTOR — Skill Sync Report")
    lines.append("=" * 60)
    lines.append(f"  Rules version: {report.version}")
    lines.append(f"  Skills on disk: {len(report.disk_skills)}")
    lines.append(f"  Skills routed: {len(report.routed)}")
    lines.append(f"  Intentionally excluded: {len(report.excluded)}")
    lines.append("")

    lines.append(f"## In Sync ({len(report.in_sync)} skills)")
    for i in range(0, len(report.in_sync), 5):
        batch = report.in_sync[i:i + 5]
        lines.append(f"  {', '.join(batch)}")
    lines.append("")

    lines.append(f"## New Skills ({len(report.new_skills)} unrouted)")
    if report.new_skills:
        for name in report.new_skills:
            desc = report.disk_skills.get(name, "")
            stype, conf = report.suggestions.get(name, ("feature", "low"))
            lines.append(f"  {name}:")
            if desc:
                lines.append(f"    {desc[:100]}")
            lines.append(f"    Suggested: {stype}.conditional[\"{name}\"] ({conf})")
    else:
        lines.append("  (none — all skills are routed!)")
    lines.append("")

    lines.append(f"## Stale References ({len(report.stale)} not on disk)")
    if report.stale:
        for name in report.stale:
            lines.append(f"  {name} — in rules but missing from .github/skills/")
    else:
        lines.append("  (none)")
    lines.append("")

    total_routable = len(report.disk_skills) - len(report.excluded)
    coverage = len(report.in_sync) / max(1, total_routable) * 100
    lines.append("## Summary")
    lines.append(f"  Coverage: {len(report.in_sync)}/{total_routable} ({coverage:.0f}%)")
    if report.new_skills or report.stale:
        lines.append(f"  Action: {len(report.new_skills)} new + {len(report.stale)} stale")
        lines.append("  Auto-fix: python3 conductor.py --sync --fix")
    else:
        lines.append("  Status: All skills are in sync!")
    lines.append("")

    return "\n".join(lines)


def apply_sync_fixes(rules: dict, report: SyncReport) -> list[str]:
    """Auto-add new skills and remove stale refs. Returns list of changes."""
    changes = []

    for name in report.new_skills:
        stype, _ = report.suggestions.get(name, ("feature", "low"))
        if stype not in rules.get("skill_routing", {}):
            continue
        routing = rules["skill_routing"][stype]
        if "conditional" not in routing:
            routing["conditional"] = {}
        already = any(name in skills for skills in routing["conditional"].values())
        if not already:
            routing["conditional"][name] = [name]
            changes.append(f"  + Added: {name} -> {stype}.conditional[\"{name}\"]")

    for stale_name in report.stale:
        for tt, routing in rules.get("skill_routing", {}).items():
            if stale_name in routing.get("always", []):
                routing["always"].remove(stale_name)
                changes.append(f"  - Removed: {stale_name} from {tt}.always")
            for pattern, skills in list(routing.get("conditional", {}).items()):
                if stale_name in skills:
                    skills.remove(stale_name)
                    if not skills:
                        del routing["conditional"][pattern]
                    changes.append(f"  - Removed: {stale_name} from {tt}.conditional[\"{pattern}\"]")

    if changes:
        with open(RULES_PATH, 'w') as f:
            json.dump(rules, f, indent=2, ensure_ascii=False)

    return changes


# ── CLI ─────────────────────────────────────────────────────────────

def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Conductor — Deterministic task router for NEO-MATCH",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 conductor.py "implement patient export API"
  python3 conductor.py "fix DynamoDB timeout" --verbose
  python3 conductor.py "deploy CDK stack" --override-type ops
  python3 conductor.py --audit
  python3 conductor.py --sync
  python3 conductor.py --sync --fix
        """
    )
    parser.add_argument("task", nargs="?", help="Task description to route")
    parser.add_argument("--verbose", "-v", action="store_true", help="Show detailed reasoning")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument("--audit", action="store_true", help="Show all rules")
    parser.add_argument("--sync", action="store_true", help="Compare rules with skills on disk")
    parser.add_argument("--fix", action="store_true", help="With --sync, auto-add new skills")
    parser.add_argument("--override-type", help="Override task type classification")
    parser.add_argument("--override-workflow", help="Override workflow selection")

    args = parser.parse_args()

    rules = load_rules()

    if args.audit:
        print(format_audit(rules))
        return

    if args.sync:
        report = run_sync(rules)
        print(format_sync(report))
        if args.fix:
            changes = apply_sync_fixes(rules, report)
            if changes:
                print("## Applied Fixes")
                for c in changes:
                    print(c)
                print(f"\n  Saved {len(changes)} changes to conductor-rules.json")
                print("  Run tests: python3 test-conductor.py")
            else:
                print("  No changes needed.")
            print()
        return

    if not args.task:
        parser.print_help()
        return

    plan = build_plan(
        args.task,
        rules,
        override_type=args.override_type,
        override_workflow=args.override_workflow
    )

    if args.json:
        print(format_json(plan))
    else:
        print(format_plan(plan, verbose=args.verbose))


if __name__ == "__main__":
    main()
