# Orchestrator Agents

A reusable, host-neutral **"main session orchestrates, subagents execute"** workflow for the
two hosts this tool supports — **Claude Code** and **GitHub Copilot CLI**. Install it once and
every project on the machine inherits the same disciplined delegation pattern.

## Why the conductor is the main session (not a subagent)

Both hosts prevent a subagent from spawning other subagents. A subagent therefore has no
working delegation surface — if you made the conductor a subagent, it would be forced to do the
work itself, defeating the purpose.

The only layer that can delegate is the **main session**. So the orchestration policy is injected
into each host's **global instruction file** (which auto-loads into the main session), while the
actual workers stay as the host's native subagents.

## What gets installed

`python install.py --deploy-orchestrator` deploys:

| Target | Host | Content |
| ------ | ---- | ------- |
| `~/.claude/CLAUDE.md` | Claude Code | Orchestration policy injected between markers |
| `~/.copilot/copilot-instructions.md` | Copilot CLI | Same policy injected between markers |
| `~/.claude/agents/researcher.md` | Claude Code | Read-only evidence gatherer |
| `~/.claude/agents/proposer.md` | Claude Code | Proposes solutions / designs |
| `~/.claude/agents/challenger.md` | Claude Code | Red-teams and challenges proposals |
| `~/.claude/agents/judge.md` | Claude Code | Adjudicates debates, decides confidence |
| `~/.claude/agents/implementer.md` | Claude Code | Makes the actual code changes |
| `~/.claude/agents/code-reviewer.md` | Claude Code | Independent review of changes |

The policy block is wrapped in idempotent markers in **each** instruction file:

```text
<!-- ORCHESTRATOR-POLICY-START -->
... orchestration policy ...
<!-- ORCHESTRATOR-POLICY-END -->
```

Re-running the deploy replaces only the marked span, so your own instruction content is preserved.

### Why workers ship only for Claude Code

Claude Code custom subagents are standalone files (`~/.claude/agents/*.md`) with their own
frontmatter (`tools`, `model`). Copilot CLI does not load Claude-format agent files; instead it
exposes **built-in `task` agent types** (`research`, `general-purpose`, `code-review`,
`rubber-duck`) plus optional project-local `.github/agents/*.agent.md`. The injected policy
therefore tells each host to delegate using the mechanism that **actually exists** there — it
never points Copilot at agents it cannot load.

## How the workflow runs

1. The **main session** (the conductor) never implements directly — it routes every unit of work
   to a subagent via the host's delegation tool.
2. When confidence is low, the conductor has two subagents **debate and challenge** each other
   (propose ⚔️ red-team), and a third (or the conductor) judges when the answer is solid.
3. Research backs every decision; an independent reviewer validates changes.
4. The loop repeats until confidence is high enough to proceed.

Per host, delegation maps to:

| Role | Claude Code | Copilot CLI |
| ---- | ----------- | ----------- |
| Investigate | `researcher` agent | `task` → `research` |
| Propose / implement | `proposer` / `implementer` | `task` → `general-purpose` |
| Challenge | `challenger` agent | `task` → `rubber-duck` |
| Review | `code-reviewer` agent | `task` → `code-review` |

## Install

```bash
# From the copilot-session-knowledge tools checkout
python install.py --deploy-orchestrator
```

`--deploy-claude-agents` remains as a deprecated alias for backward compatibility. After
installing, open Claude Code and run `/agents` to confirm the six workers are listed; in Copilot
CLI the policy loads automatically from `~/.copilot/copilot-instructions.md`.

## Idempotency & safety

- Agent files are written only when their content differs (compare-then-write).
- Each global instruction file's policy is injected/replaced via the same marker pattern used by
  the other `inject_*` helpers, so repeated installs never duplicate the block.
- A non-existent instruction file is created with an appropriate header; an existing one keeps
  your content and gets the policy appended after it.

## Source templates

- `templates/claude-agents/*.md` — the six Claude Code worker subagent definitions.
- `templates/orchestrator-policy.md` — canonical, host-neutral policy source. The editable
  preamble above `<!-- ORCHESTRATOR-POLICY-START -->` documents the file; only the marked span is
  injected.

## Tests

`tests/test_orchestrator.py` covers policy extraction, fresh install into both hosts, idempotent
re-runs, preservation of existing instruction content, in-place replacement of a stale policy
block, and the deprecated alias.
