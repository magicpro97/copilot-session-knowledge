# Phase 0: Spec Clarification

This phase takes a raw specification and makes it implementation-ready through iterative Q&A. No planning or coding happens until the spec is CLEAN.

## Step 0.0: Co-Author Spec (Optional — when no spec exists)

If the user has no written spec, PRD, or issue — only ideas in their head — help them create one before analysis. Skip this step if a spec already exists.

**When to trigger:**
- User says "I want to build X" without a document
- Task description is verbal/informal with no structured requirements
- User asks "where do I start?" or "help me figure out what to build"

**Process (adapted from [doc-coauthoring](https://github.com/anthropics/skills/blob/main/skills/doc-coauthoring/SKILL.md)):**

### Context Gathering

Ask the user for meta-context:
1. What type of feature/change is this?
2. Who are the stakeholders or users affected?
3. What's the desired outcome?
4. Any constraints (timeline, tech stack, backward compat)?

Then encourage an **info dump** — let them pour out everything they know in any format. Don't worry about structure yet. Ask 5-10 clarifying questions after the dump to close gaps.

### Structured Drafting

Build the spec section by section:
1. For each section: brainstorm 5-15 points → user curates → draft → refine via surgical edits
2. Start with the section that has the most unknowns (usually the core problem/solution)
3. Leave summary sections for last

Output format: a structured spec document with clear requirements, flows, and acceptance criteria — ready for Step 0.1 analysis.

**Exit condition:** User confirms the spec captures their intent. Proceed to Step 0.1.

## Step 0.1: Analyze the Specification

Read the spec and evaluate it against 8 quality dimensions:

| Dimension | What to check |
|-----------|--------------|
| **Clarity** | Are terms unambiguous? Could two engineers build the same thing from this spec? |
| **Completeness** | Are all flows covered (happy + error + edge)? Boundary conditions? Non-functional requirements? |
| **Consistency** | Do any requirements contradict each other or conflict with existing system behavior? |
| **Testability** | Can each requirement be verified with a concrete test? Are acceptance criteria measurable? |
| **Feasibility** | Are there technically impossible requirements? Undeclared dependencies? |
| **Impact** | What existing features, modules, APIs, schemas are affected? What is the blast radius? |
| **Trade-offs** | Are there competing concerns (perf vs simplicity)? What alternatives weren't considered? |
| **Risks** | What could go wrong? What are the unknowns? Backward compatibility? Rollback strategy? |

Investigate the codebase to understand current behavior BEFORE generating questions. Use `grep`, `glob`, and `view` to find relevant code — questions backed by code evidence get faster, better answers.

## Step 0.2: Generate the Spec Health Report

Produce a structured report with:

```markdown
## Spec Health Report: <feature name>

### Summary
- **Clarity**: 🟢 Clear / 🟡 Minor gaps / 🔴 Ambiguous
- **Completeness**: 🟢 / 🟡 / 🔴
- **Consistency**: 🟢 / 🟡 / 🔴
- **Testability**: 🟢 / 🟡 / 🔴
- **Feasibility**: 🟢 / 🟡 / 🔴
- **Verdict**: ✅ CLEAN — ready to plan / ⚠️ BLOCKED — N questions need answers

### Impact Analysis
- **Affected modules**: <list>
- **Affected APIs**: <list>
- **Database changes**: <yes/no, details>
- **Blast radius**: <low/medium/high> — <explanation>

### Trade-offs
| Decision | Option A | Option B | Recommendation |
|----------|----------|----------|----------------|

### Risks
| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|

### Blocking Questions
Q1. <question with context, code evidence, and A/B/C options>

### Non-blocking Questions (have reasonable defaults)
Q2. <question> — Default: <recommended option>
```

Question writing rules:
- **Closed-form** — provide A/B/C options, never open-ended "how should we handle this?"
- **Context-rich** — quote the spec, show the code, explain your reasoning
- **Code-backed** — investigate the codebase FIRST, then ask for confirmation of what you found
- **Prioritized** — blocking questions first, non-blocking second

## Step 0.3: Iterative Refinement

After receiving answers:
1. Re-evaluate the spec with the new information
2. Check if answers introduced NEW ambiguities or conflicts
3. Generate follow-up questions if needed
4. Update the Spec Health Report
5. Repeat until all blocking questions are resolved

Each round should produce fewer questions — if questions are increasing, you're asking the wrong questions or the spec has fundamental design issues that need escalation.

## Step 0.4: Declare CLEAN or Escalate

The spec is **CLEAN** when:
- Zero blocking questions remain
- All 8 dimensions are 🟢 or 🟡 (no 🔴)
- Impact analysis is complete with blast radius assessed
- At least one risk mitigation exists for each high-impact risk
- Trade-offs are documented with explicit decisions

If the spec cannot reach CLEAN after 3 rounds of Q&A, escalate — the problem is likely at the requirements level, not the clarification level.

**Gate**: Planning on an unclear spec produces incorrect decomposition, wasted agent work, and rework.

## Step 0.5: Reader Testing (verify spec comprehension)

After the spec is CLEAN, test whether someone with no prior context can correctly understand it. This catches blind spots — things obvious to the authors but confusing to implementers.

### With sub-agents (recommended)

1. **Predict reader questions** — generate 5-10 questions an engineer would ask when implementing from this spec (e.g., "What happens when X fails?", "Which API handles Y?")

2. **Test with fresh agent** — dispatch an `explore` agent with ONLY the spec text and each question. No context from this conversation:

```
task(agent_type="explore", prompt="""
You are an engineer reading this spec for the first time.
Answer the following question based ONLY on the spec below.
If the spec doesn't cover it, say "NOT SPECIFIED".

Question: <question>

Spec:
<full spec text>
""")
```

3. **Analyze results** — for each question:
   - ✅ Correct answer → spec is clear on this point
   - ⚠️ Partially correct → spec needs clarification
   - ❌ Wrong or "NOT SPECIFIED" → spec has a gap

4. **Fix gaps** — loop back to Step 0.3 for any ⚠️/❌ results. Update the spec, then re-test.

### Without sub-agents

Provide the user with instructions:
1. Open a fresh AI conversation (no shared context)
2. Paste the spec
3. Ask the predicted questions
4. Report back which answers were wrong or incomplete

### Additional checks

Ask the fresh agent to evaluate:
- "What in this spec is ambiguous or could be interpreted multiple ways?"
- "What knowledge does this spec assume the reader already has?"
- "Are there any internal contradictions?"

**Exit condition:** Fresh agent answers all questions correctly and surfaces no new ambiguities. The spec is **implementation-ready**.
