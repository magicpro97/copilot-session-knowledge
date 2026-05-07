import type {
  CopilotStreamFrame as StreamFrame,
  CopilotEventFrame,
  CopilotRawFrame,
} from "@/lib/api/types";

export type AssistantTextChunk = { kind: "text"; text: string };
export type AssistantToolChunk = {
  kind: "tool";
  name: string;
  input: Record<string, unknown>;
  output?: string;
  /** File paths reported by tool telemetry (e.g. from apply_patch). */
  filePaths?: string[];
  /** Added file paths reported by tool telemetry. */
  addedPaths?: string[];
};
export type AssistantRawChunk = { kind: "raw"; text: string };
/** Skills loaded at session start — from session.skills_loaded runtime events. */
export type AssistantSkillsChunk = {
  kind: "skills";
  count: number;
  /** Skill names only — descriptions and paths are intentionally omitted from inline rendering. */
  names: string[];
};

export type AssistantChunk =
  | AssistantTextChunk
  | AssistantToolChunk
  | AssistantRawChunk
  | AssistantSkillsChunk;

/** Parse raw tool arguments into a key/value record.
 *  - If already an object, return as-is.
 *  - If a JSON string, parse it.
 *  - Otherwise treat the raw value as a `patch` string (apply_patch style).
 */
function parseToolArguments(raw: unknown): Record<string, unknown> {
  if (raw === null || raw === undefined) return {};
  if (typeof raw === "object" && !Array.isArray(raw)) return raw as Record<string, unknown>;
  if (typeof raw === "string") {
    try {
      const parsed: unknown = JSON.parse(raw);
      if (typeof parsed === "object" && parsed !== null && !Array.isArray(parsed)) {
        return parsed as Record<string, unknown>;
      }
    } catch {
      // not JSON — treat as raw patch/command text
    }
    return { patch: raw };
  }
  return { value: raw };
}

function parseTelemetryPathList(raw: unknown): string[] {
  if (Array.isArray(raw)) {
    return raw.filter((value): value is string => typeof value === "string");
  }
  if (typeof raw === "string") {
    try {
      const parsed: unknown = JSON.parse(raw);
      if (Array.isArray(parsed)) {
        return parsed.filter((value): value is string => typeof value === "string");
      }
    } catch {
      return raw.trim() ? [raw] : [];
    }
  }
  return [];
}

function isCompletionStatusSummary(text: string): boolean {
  const normalized = text.trim().replace(/\s+/g, " ").toLowerCase();
  if (!normalized) return false;
  return (
    /^acknowledg(?:e|ing)\b.*\bclosing the turn\.?$/.test(normalized) ||
    /^closing the turn\.?$/.test(normalized) ||
    /^session finished successfully\.?$/.test(normalized)
  );
}

/** Derive display chunks from a flat list of stream frames.
 *
 *  Real Copilot CLI 1.0.40 event names:
 *    - assistant.message_delta  →  data.deltaContent  (streaming text)
 *    - assistant.message        →  data.content       (final complete message)
 *    - tool.execution_start     →  data.toolName, data.arguments
 *    - tool.execution_complete  →  data.result.detailedContent,
 *                                  data.toolTelemetry.restrictedProperties.filePaths
 */
export function deriveChunks(frames: StreamFrame[]): AssistantChunk[] {
  const chunks: AssistantChunk[] = [];
  let currentText = "";
  const pendingTools = new Map<string, AssistantToolChunk>();
  // FIFO queue so tool.execution_complete resolves in insertion order
  const pendingToolQueue: string[] = [];
  // Prevent double-promoting task_complete content as visible text
  let taskCompletePromoted = false;

  function flushText() {
    if (currentText) {
      if (!isCompletionStatusSummary(currentText)) {
        chunks.push({ kind: "text", text: currentText });
      }
      currentText = "";
    }
  }

  for (const frame of frames) {
    if (frame.type === "raw") {
      flushText();
      chunks.push({ kind: "raw", text: (frame as CopilotRawFrame).text });
      continue;
    }

    if (frame.type === "status") continue;

    // Structured event frame
    const ef = frame as CopilotEventFrame;
    const eventType = ef.type as string;
    const data = ef.data as Record<string, unknown> | undefined;

    // Real streaming text delta — payload field is deltaContent
    if (eventType === "assistant.message_delta") {
      const delta = (data as { deltaContent?: string } | undefined)?.deltaContent ?? "";
      currentText += delta;
      continue;
    }

    // Final complete message — only replace delta accumulation when authoritative content exists.
    // If content is empty (real Test-session shape), preserve accumulated deltas.
    if (eventType === "assistant.message") {
      const content = (data as { content?: string } | undefined)?.content ?? "";
      if (content) {
        // Authoritative final content exists — discard deltas and use it
        currentText = "";
        if (!isCompletionStatusSummary(content)) {
          chunks.push({ kind: "text", text: content });
        }
      }
      // If content is empty, keep accumulated deltas unchanged
      continue;
    }

    // Keep reasoning stream; treat like a text delta
    if (eventType === "assistant.reasoning") {
      const delta = (data as { text?: string } | undefined)?.text ?? "";
      currentText += delta;
      continue;
    }

    if (eventType === "assistant.message_start") {
      flushText();
      continue;
    }

    // Real tool start event
    if (eventType === "tool.execution_start") {
      flushText();
      const toolId = String(pendingToolQueue.length + pendingTools.size + chunks.length);
      const name = (data as { toolName?: string } | undefined)?.toolName ?? "unknown";
      const rawArgs = (data as { arguments?: unknown } | undefined)?.arguments;
      const input = parseToolArguments(rawArgs);
      const toolChunk: AssistantToolChunk = { kind: "tool", name, input };
      pendingTools.set(toolId, toolChunk);
      pendingToolQueue.push(toolId);
      chunks.push(toolChunk);
      continue;
    }

    // Real tool complete event — match FIFO
    if (eventType === "tool.execution_complete") {
      const toolId = pendingToolQueue.shift();
      if (toolId) {
        const toolChunk = pendingTools.get(toolId);
        if (toolChunk) {
          const result = (data as { result?: Record<string, unknown> } | undefined)?.result;
          const detailedContent = result?.detailedContent;
          toolChunk.output =
            typeof detailedContent === "string" ? detailedContent : JSON.stringify(result);

          // Capture file paths from telemetry (apply_patch and similar tools)
          const telemetry = (data as { toolTelemetry?: Record<string, unknown> } | undefined)
            ?.toolTelemetry as Record<string, unknown> | undefined;
          const restricted = telemetry?.restrictedProperties as Record<string, unknown> | undefined;
          const filePaths = parseTelemetryPathList(restricted?.filePaths);
          const addedPaths = parseTelemetryPathList(restricted?.addedPaths);
          if (filePaths.length > 0) {
            toolChunk.filePaths = filePaths;
          }
          if (addedPaths.length > 0) {
            toolChunk.addedPaths = addedPaths;
          }

          // Promote task_complete tool result only when it carries user-facing
          // answer text. Some Copilot turns emit procedural summaries such as
          // "Acknowledging the greeting and closing the turn."; those are
          // completion status, not the assistant's reply.
          if (toolChunk.name === "task_complete" && !taskCompletePromoted) {
            const promotedContent =
              typeof result?.content === "string"
                ? result.content
                : typeof detailedContent === "string"
                  ? detailedContent
                  : "";
            if (promotedContent && !isCompletionStatusSummary(promotedContent)) {
              taskCompletePromoted = true;
              flushText();
              chunks.push({ kind: "text", text: promotedContent });
            }
          }

          pendingTools.delete(toolId);
        }
      }
      continue;
    }

    // session.task_complete carries the final summary when task_complete tool fires.
    // Promote as visible text only if not already done via tool.execution_complete.
    if (eventType === "session.task_complete") {
      const summary = (data as { summary?: string } | undefined)?.summary ?? "";
      if (summary && !taskCompletePromoted && !isCompletionStatusSummary(summary)) {
        taskCompletePromoted = true;
        flushText();
        chunks.push({ kind: "text", text: summary });
      }
      continue;
    }

    // session.skills_loaded — emitted once (empty bootstrap) then once with all skills.
    // Skip the noisy empty bootstrap event; emit a compact skills chunk only when at
    // least one valid skill name remains after filtering malformed entries.
    if (eventType === "session.skills_loaded") {
      const rawSkills = (data as { skills?: unknown } | undefined)?.skills;
      if (!Array.isArray(rawSkills) || rawSkills.length === 0) continue;
      const names = rawSkills
        .map((s) => {
          const obj = s as Record<string, unknown>;
          return typeof obj?.name === "string" ? obj.name : null;
        })
        .filter((n): n is string => n !== null && n.length > 0);
      if (names.length === 0) continue;
      flushText();
      chunks.push({ kind: "skills", count: names.length, names });
      continue;
    }
  }

  flushText();
  return chunks;
}

export type FileEntry = {
  path: string;
  created: boolean;
  /** Unified diff text from tool payload (e.g. apply_patch detailedContent). */
  unifiedDiff?: string;
};

/**
 * Extract touched file paths from tool chunks.
 *
 * Priority:
 *  1. Telemetry-reported filePaths (apply_patch, etc.) — most reliable.
 *  2. Input field scan (path / file_path / target_file) — for create/write tools.
 *
 * Returns entries with optional `unifiedDiff` from tool output when available.
 */
export function extractFilePaths(chunks: AssistantChunk[]): FileEntry[] {
  const seen = new Set<string>();
  const files: FileEntry[] = [];

  const CREATE_TOOLS = new Set(["create_file", "write_file", "new_file"]);

  for (const chunk of chunks) {
    if (chunk.kind !== "tool") continue;

    // Use telemetry file paths first — these are verified by the CLI runtime
    if (chunk.filePaths && chunk.filePaths.length > 0) {
      // Only carry diff content when the output looks like a real single-file
      // unified diff. Multi-file patches would otherwise show the same full
      // patch under every file row, which is misleading.
      const unifiedDiff =
        chunk.filePaths.length === 1 &&
        typeof chunk.output === "string" &&
        chunk.output.includes("@@")
          ? chunk.output
          : undefined;
      for (const p of chunk.filePaths) {
        if (seen.has(p)) continue;
        seen.add(p);
        const created = CREATE_TOOLS.has(chunk.name) || chunk.addedPaths?.includes(p) === true;
        files.push({ path: p, created, unifiedDiff });
      }
      continue;
    }

    // Fallback: inspect input fields for a file path
    const input = chunk.input as Record<string, unknown>;
    const path =
      (input.path as string | undefined) ??
      (input.file_path as string | undefined) ??
      (input.target_file as string | undefined);
    if (!path || seen.has(path)) continue;
    seen.add(path);
    const created = CREATE_TOOLS.has(chunk.name);
    files.push({ path, created });
  }

  return files;
}
