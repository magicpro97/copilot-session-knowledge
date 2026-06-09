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
    const text = stdout.trim()
    if (text && stderr.trim()) {
      log(client, "debug", `[${event}] ${stderr.trim()}`)
    }
    if (!text) return null
    const lines = text.split("\n").filter(l => l.trim())
    const results: Record<string, unknown>[] = []
    for (const line of lines) {
      try {
        results.push(JSON.parse(line))
      } catch {
        log(client, "debug", `[${event}] non-JSON output: ${line}`)
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

type HookState = {
  sessionStartFired: boolean
  sessionId: string
}

export const CopilotToolsBridge: Plugin = async ({ project, client, $, directory, worktree }) => {
  const state: HookState = { sessionStartFired: false, sessionId: "" }

  const fireSessionStart = async (sessionId?: string) => {
    if (state.sessionStartFired) return
    if (sessionId) state.sessionId = sessionId
    state.sessionStartFired = true
    await callHookRunner($, client, "sessionStart", {
      sessionId: state.sessionId || sessionId || "",
      additionalContext: [],
    })
  }

  const fireSessionEnd = async (sessionId?: string) => {
    await callHookRunner($, client, "sessionEnd", {
      sessionId: sessionId || state.sessionId || "",
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

      const results = await callHookRunner($, client, "preToolUse", {
        toolName,
        toolArgs: output.args,
        toolInput: output.args,
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

      const toolResult: Record<string, unknown> = {
        title: output.title,
        output: output.output,
        resultType: output.isError ? "error" : "success",
      }
      if (typeof input.args === "object" && input.args && (input.args as any).filePath) {
        toolResult.filePath = (input.args as any).filePath
      }

      const results = await callHookRunner($, client, "postToolUse", {
        toolName,
        toolArgs: input.args,
        toolInput: input.args,
        toolResult,
        sessionId: input.sessionID,
        cwd: process.cwd(),
      })

      if (!results) return

      for (const parsed of results) {
        if (parsed.title) output.title = parsed.title
      }
    },

    "tool.use": async (input, output) => {
      const toolName = mapToolName(input.tool)

      const results = await callHookRunner($, client, "preToolUse", {
        toolName,
        toolArgs: output.args,
        toolInput: output.args,
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
        case "session.created":
          await fireSessionStart(props.sessionID || props.id || "")
          break
        case "session.idle":
          await fireSessionEnd(props.sessionID || "")
          break
        case "session.error":
          await callHookRunner($, client, "errorOccurred", {
            sessionId: props.sessionID || "",
            error: props.error || props.message || "",
          })
          break
      }
    },
  }
}
