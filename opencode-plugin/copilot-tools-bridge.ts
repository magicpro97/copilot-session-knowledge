import type { Plugin } from "@opencode-ai/plugin"
import { homedir } from "os"
import { join } from "path"

const HOME = process.env.HOME || homedir()
const TOOLS_DIR = join(HOME, ".copilot", "tools")
const HOOK_RUNNER = join(TOOLS_DIR, "hooks", "hook_runner.py")
const PYTHON = "python3"
const HOOK_TIMEOUT = 15_000

const log = (client: any, level: string, message: string) => {
  try { client.app.log({ body: { service: "copilot-tools-bridge", level, message } }) } catch {}
}

async function callHookRunner(
  $: any,
  client: any,
  event: string,
  data: Record<string, unknown>,
): Promise<Record<string, unknown>[] | null> {
  const json = JSON.stringify(data)
  const signal = AbortSignal.timeout(HOOK_TIMEOUT)
  try {
    const proc = Bun.spawn([PYTHON, HOOK_RUNNER, event], {
      stdin: new Blob([json]).stream(),
      stdout: "pipe",
      stderr: "pipe",
      signal,
    })
    const [stdout, stderr] = await Promise.all([
      new Response(proc.stdout).text(),
      new Response(proc.stderr).text(),
    ])
    const exitCode = await proc.exited
    if (exitCode !== 0) {
      log(client, "warn", `hook_runner ${event} exited ${exitCode}: ${stderr.trim()}`)
      return null
    }
    const stderrText = stderr.trim()
    if (stderrText) {
      log(client, "debug", `[${event}] ${stderrText}`)
    }
    const text = stdout.trim()
    if (!text) return null
    const lines = text.split("\n").map(l => l.replace(/\r$/, ""))
    const results: Record<string, unknown>[] = []
    for (const line of lines) {
      if (!line) continue
      try {
        results.push(JSON.parse(line))
      } catch {
        results.push({ message: line })
      }
    }
    return results.length > 0 ? results : null
  } catch (e) {
    if ((e as any)?.name === "TimeoutError") {
      log(client, "warn", `hook_runner ${event} timed out after ${HOOK_TIMEOUT}ms`)
    } else {
      log(client, "warn", `hook_runner ${event} error: ${e}`)
    }
    return null
  }
}

function mapToolName(tool: string): string {
  if (tool === "write") return "create"
  if (tool === "read") return "view"
  if (tool === "apply_patch") return "edit"
  return tool
}

function normalizeToolArgs(toolName: string, args: Record<string, unknown>): Record<string, unknown> {
  const normalized = { ...args }
  if (toolName === "skill" && typeof normalized.name === "string" && typeof normalized.skill !== "string") {
    normalized.skill = normalized.name
  }
  if (toolName === "edit") {
    if (typeof normalized.oldString === "string" && typeof normalized.old_str !== "string") normalized.old_str = normalized.oldString
    if (typeof normalized.newString === "string" && typeof normalized.new_str !== "string") normalized.new_str = normalized.newString
  }
  if (toolName === "create") {
    if (typeof normalized.content === "string" && typeof normalized.file_text !== "string") normalized.file_text = normalized.content
  }
  if (typeof normalized.path === "string" && typeof normalized.filePath !== "string") {
    normalized.filePath = normalized.path
  }
  return normalized
}

type SubagentInfo = {
  parentSessionId: string
  title: string
}

type HookState = {
  sessionStartFired: boolean
  sessionId: string
  subagentSessions: Map<string, SubagentInfo>
}

export const CopilotToolsBridge: Plugin = async ({ project, client, $, directory, worktree }) => {
  const state: HookState = { sessionStartFired: false, sessionId: "", subagentSessions: new Map() }

  const fireSessionStart = async (sessionId?: string) => {
    if (state.sessionStartFired) return
    if (sessionId) state.sessionId = sessionId
    state.sessionStartFired = true
    await callHookRunner($, client, "sessionStart", {
      sessionId: state.sessionId || sessionId || "",
      additionalContext: [],
    })
  }

  const fireSessionEnd = async (sessionId?: string, reason?: string) => {
    await callHookRunner($, client, "sessionEnd", {
      sessionId: sessionId || state.sessionId || "",
      reason: reason || "unknown",
    })
  }

  return {
    "session.start": async (input) => {
      await fireSessionStart(input.sessionID)
    },

    "session.stop": async (input) => {
      await fireSessionEnd(input.sessionID)
    },

    "tool.execute.before": async (input, output) => {
      const toolName = mapToolName(input.tool)
      const toolArgs = normalizeToolArgs(toolName, output.args ?? {})

      const results = await callHookRunner($, client, "preToolUse", {
        toolName,
        toolArgs,
        toolInput: toolArgs,
        sessionId: input.sessionID,
        callId: input.callID,
        cwd: process.cwd(),
      })

      if (!results) return

      for (const parsed of results) {
        if (parsed.permissionDecision === "deny") {
          throw new Error(parsed.permissionDecisionReason || `Blocked by ${toolName} rule`)
        }
      }
    },

    "tool.execute.after": async (input, output) => {
      const toolName = mapToolName(input.tool)
      const toolArgs = normalizeToolArgs(toolName, (input.args ?? {}) as Record<string, unknown>)

      const isError = !!(output as any).isError
      const metadata = (output as any).metadata || {}
      const exitCode = metadata.exitCode ?? metadata.exit_code ?? (isError ? 1 : 0)

      const toolResult: Record<string, unknown> = {
        title: output.title,
        output: output.output,
        stdout: output.output,
        resultType: isError ? "error" : "success",
        exitCode,
        exit_code: exitCode,
      }
      if (typeof toolArgs.filePath === "string") {
        toolResult.filePath = toolArgs.filePath
      }

      const results = await callHookRunner($, client, "postToolUse", {
        toolName,
        toolArgs,
        toolInput: toolArgs,
        toolResult,
        sessionId: input.sessionID,
        cwd: process.cwd(),
      })

      if (!results) return

      for (const parsed of results) {
        if (parsed.title) output.title = parsed.title
        if (parsed.message) {
          output.output = (output.output || "") + "\n" + parsed.message
        }
      }
    },

    "chat.message": async (input, output) => {
      if (!state.sessionStartFired) {
        await fireSessionStart(input.sessionID)
      }

      const parts = output.parts || []
      const prompt = parts.map((p: any) => p.text || "").filter(Boolean).join("\n")

      const results = await callHookRunner($, client, "userPromptSubmitted", {
        sessionId: input.sessionID,
        prompt,
        additionalContext: [],
        cwd: process.cwd(),
      })

      if (!results) return

      const targetParts = output.parts || []
      for (const parsed of results) {
        if (parsed.additionalContext) {
          const ctx = parsed.additionalContext
          const texts = Array.isArray(ctx) ? ctx : [ctx]
          for (const text of texts) {
            if (typeof text === "string") {
              targetParts.push({ type: "text", text, synthetic: true } as any)
            }
          }
        }
        if (parsed.modifiedPrompt && typeof parsed.modifiedPrompt === "string") {
          for (let i = targetParts.length - 1; i >= 0; i--) {
            if (!(targetParts[i] as any).synthetic) {
              ;(targetParts[i] as any).text = parsed.modifiedPrompt
              break
            }
          }
        }
      }
      output.parts = targetParts
    },

    "experimental.session.compacting": async (input, output) => {
      const results = await callHookRunner($, client, "preCompact", {
        sessionId: input.sessionID,
      })
      if (!results) return
      for (const parsed of results) {
        if (parsed.additionalContext) {
          const ctx = parsed.additionalContext
          if (Array.isArray(ctx)) {
            for (const c of ctx) {
              if (typeof c === "string") output.context.push(c)
            }
          } else if (typeof ctx === "string") {
            output.context.push(ctx)
          }
        }
      }
    },

    task: async (input, output) => {
      const results = await callHookRunner($, client, "preToolUse", {
        toolName: "task",
        toolArgs: { description: input.description, subtask: input.subtask },
        toolInput: { description: input.description, subtask: input.subtask },
        sessionId: input.sessionID,
        callId: input.callID,
        cwd: process.cwd(),
      })

      if (!results) return

      for (const parsed of results) {
        if (parsed.permissionDecision === "deny") {
          throw new Error(parsed.permissionDecisionReason || "Blocked by task rule")
        }
      }
    },

    event: async ({ event }) => {
      const props = (event as any).properties || {}

      switch (event.type) {
        case "session.created": {
          const info = props.info || {}
          if (info.parentID) {
            state.subagentSessions.set(info.id, {
              parentSessionId: info.parentID,
              title: info.title || "",
            })
          } else {
            await fireSessionStart(info.id || props.sessionID || "")
          }
          break
        }
        case "session.status": {
          const { sessionID, status } = props
          if (status?.type === "idle" && state.subagentSessions.has(sessionID)) {
            const sub = state.subagentSessions.get(sessionID)!
            state.subagentSessions.delete(sessionID)
            await callHookRunner($, client, "subagentStop", {
              sessionId: sessionID,
              subagentId: sessionID,
              subagentName: sub.title,
              parentSessionId: sub.parentSessionId,
            })
          }
          break
        }
        case "session.idle":
          await fireSessionEnd(props.sessionID || "", "idle")
          break
        case "session.error":
          await callHookRunner($, client, "errorOccurred", {
            sessionId: props.sessionID || "",
            error: props.error || props.message || "",
          })
          break
        case "session.compacted":
          await callHookRunner($, client, "postCompact", {
            sessionId: props.sessionID || "",
          })
          break
      }
    },
  }
}
