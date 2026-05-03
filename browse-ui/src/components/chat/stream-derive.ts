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

export type AssistantChunk = AssistantTextChunk | AssistantToolChunk | AssistantRawChunk;

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

  function flushText() {
    if (currentText) {
      chunks.push({ kind: "text", text: currentText });
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

    // Final complete message — replace any accumulated delta text with authoritative content
    if (eventType === "assistant.message") {
      const content = (data as { content?: string } | undefined)?.content ?? "";
      // Discard in-progress delta accumulation; use complete content instead
      currentText = "";
      if (content) {
        chunks.push({ kind: "text", text: content });
      }
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

          pendingTools.delete(toolId);
        }
      }
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
