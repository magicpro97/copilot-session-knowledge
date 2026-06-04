# Claude Code Orchestrator Agents

A reusable, shareable agent set for [Claude Code](https://docs.claude.com/en/docs/claude-code)
that encodes a **"main session orchestrates, workers execute"** workflow. Install it once and
every project on the machine inherits the same disciplined delegation pattern.

## Why the orchestrator lives in CLAUDE.md (not as a subagent)

Claude Code **does not allow a subagent to spawn other subagents**. A subagent therefore has no
working delegation surface — if you made the orchestrator a subagent, it would be forced to do
the work itself, defeating the purpose.

The only layer that can delegate to subagents is the **main session**. So the orchestration
policy is injected into `~/.claude/CLAUDE.md` (which auto-loads into the main session and into
custom subagents), while the actual workers ship as ordinary subagent definitions under
`~/.claude/agents/`.

## What gets installed

`python install.py --deploy-claude-agents` deploys:

| Target | Content |
| ------ | ------- |
| `~/.claude/agents/researcher.md` | Gathers evidence, reads code/docs, reports findings |
| `~/.claude/agents/proposer.md` | Proposes solutions / designs |
| `~/.claude/agents/challenger.md` | Challenges and stress-tests proposals |
| `~/.claude/agents/judge.md` | Adjudicates debates, decides when confidence is sufficient |
| `~/.claude/agents/implementer.md` | Makes the actual code changes |
| `~/.claude/agents/code-reviewer.md` | Reviews changes for correctness and risk |
| `~/.claude/CLAUDE.md` | Orchestration policy injected between markers |

The policy block is wrapped in idempotent markers:

```text
<!-- ORCHESTRATOR-POLICY-START -->
... orchestration policy ...
<!-- ORCHESTRATOR-POLICY-END -->
```

Re-running the deploy replaces only the marked span, so your own CLAUDE.md content is preserved.

## How the workflow runs

1. The **main session** (the conductor) never implements directly — it routes every unit of work
   to a worker subagent.
2. When confidence is low, the conductor has `proposer` and `challenger` **debate and challenge**
   each other; `judge` decides when the answer is solid enough to act on.
3. `researcher` supplies evidence; `implementer` makes changes; `code-reviewer` validates them.
4. The loop repeats until confidence is high enough to proceed.

Each worker carries a note reminding it that **it is a worker** — it executes directly and ignores
the orchestration policy (only the main session orchestrates). This prevents the policy from
making a worker try to re-delegate, which it cannot do.

## Install

```bash
# From the copilot-session-knowledge tools checkout
python install.py --deploy-claude-agents
```

Then open Claude Code and run `/agents` to confirm the six workers are listed.

## Idempotency & safety

- Agent files are written only when their content differs (compare-then-write).
- The CLAUDE.md policy is injected/replaced via the same marker pattern used by the other
  `inject_*` helpers, so repeated installs never duplicate the block.
- A non-existent CLAUDE.md is created; an existing one keeps your content and gets the policy
  appended after it.

## Source templates

- `templates/claude-agents/*.md` — the six worker subagent definitions.
- `templates/claude-orchestrator-policy.md` — canonical policy source. The editable preamble
  above `<!-- ORCHESTRATOR-POLICY-START -->` documents the file; only the marked span is injected.

## Tests

`tests/test_claude_agents.py` covers policy extraction, fresh install, idempotent re-runs,
preservation of existing CLAUDE.md content, and in-place replacement of a stale policy block.
