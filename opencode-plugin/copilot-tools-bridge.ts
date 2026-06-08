import type { Plugin } from "@opencode-ai/plugin"
import { homedir } from "os"
import { join } from "path"

const HOME = process.env.HOME || homedir()
const TOOLS_DIR = join(HOME, ".copilot", "tools")
const HOOK_RUNNER = join(TOOLS_DIR, "hooks", "hook_runner.py")
const PYTHON = "python3"

const log = (client: any, level: string, message: string) => {
  try { client.app.log({ body: { service: "copilot-tools-bridge", level, message } }) } catch {}
}

async function callHookRunner(
  $: any,
  client: any,
  event: string,
  data: Record<string, unknown>,
): Promise<string | null> {
  const json = JSON.stringify(data)
  try {
    const proc = Bun.spawn([PYTHON, HOOK_RUNNER, event], {
      stdin: new Blob([json]).stream(),
      stdout: "pipe",
      stderr: "pipe",
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
    return text || null
  } catch (e) {
    log(client, "warn", `hook_runner ${event} error: ${e}`)
    return null
  }
}

function mapToolName(tool: string): string {
  if (tool === "write") return "create"
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

      const result = await callHookRunner($, client, "preToolUse", {
        toolName,
        toolArgs: output.args,
        toolInput: output.args,
        sessionId: input.sessionID,
        callId: input.callID,
      })

      if (!result) return

      try {
        const parsed = JSON.parse(result)
        if (parsed.permissionDecision === "deny") {
          throw new Error(parsed.permissionDecisionReason || `Blocked by ${toolName} rule`)
        }
      } catch (e) {
        if (e instanceof SyntaxError) {
          if (result) process.stderr.write("[copilot-tools] " + result + "\n")
          return
        }
        throw e
      }
    },

    "tool.execute.after": async (input, output) => {
      const toolName = mapToolName(input.tool)

      const toolResult: Record<string, unknown> = {
        title: output.title,
        output: output.output,
      }
      if (typeof input.args === "object" && input.args && (input.args as any).filePath) {
        toolResult.filePath = (input.args as any).filePath
      }

      const result = await callHookRunner($, client, "postToolUse", {
        toolName,
        toolArgs: input.args,
        toolInput: input.args,
        toolResult,
        sessionId: input.sessionID,
      })

      if (!result) return

      try {
        const parsed = JSON.parse(result)
        if (parsed.title) output.title = parsed.title
      } catch {
        if (result) {
          output.output = (output.output || "") + "\n" + result
        }
      }
    },

    "tool.use": async (input, output) => {
      const toolName = mapToolName(input.tool)

      const result = await callHookRunner($, client, "preToolUse", {
        toolName,
        toolArgs: output.args,
        toolInput: output.args,
        sessionId: input.sessionID,
        callId: input.callID,
      })

      if (!result) return

      try {
        const parsed = JSON.parse(result)
        if (parsed.permissionDecision === "deny") {
          throw new Error(parsed.permissionDecisionReason || `Blocked by ${toolName} rule`)
        }
      } catch (e) {
        if (e instanceof SyntaxError) {
          if (result) process.stderr.write("[copilot-tools] " + result + "\n")
          return
        }
        throw e
      }
    },

    "chat.message": async (input, output) => {
      if (!state.sessionStartFired) {
        await fireSessionStart(input.sessionID)
      }

      const parts = output.parts || []
      const prompt = parts.map((p: any) => p.text || "").filter(Boolean).join("\n")

      const result = await callHookRunner($, client, "userPromptSubmitted", {
        sessionId: input.sessionID,
        prompt,
        additionalContext: [],
      })

      if (!result) return
      try {
        const parsed = JSON.parse(result)
        if (parsed.additionalContext && Array.isArray(parsed.additionalContext)) {
          const targetParts = output.parts || []
          for (const ctx of parsed.additionalContext) {
            if (typeof ctx === "string") {
              targetParts.push({
                type: "text",
                text: ctx,
                synthetic: true,
              } as any)
            }
          }
          output.parts = targetParts
        }
      } catch {
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
