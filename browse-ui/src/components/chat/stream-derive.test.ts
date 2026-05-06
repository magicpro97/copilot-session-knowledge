import { describe, expect, it } from "vitest";
import { deriveChunks, extractFilePaths } from "./stream-derive";
import type { CopilotStreamFrame } from "@/lib/api/types";

// ── helpers ──────────────────────────────────────────────────────────────────

function eventFrame(type: string, data: Record<string, unknown>, idx = 0): CopilotStreamFrame {
  return { type, idx, event: { type, data }, data } as CopilotStreamFrame;
}

function rawFrame(text: string): CopilotStreamFrame {
  return { type: "raw", idx: 0, text } as CopilotStreamFrame;
}

function statusFrame(): CopilotStreamFrame {
  return { type: "status", status: "done", exit_code: 0 } as CopilotStreamFrame;
}

// ── deriveChunks ─────────────────────────────────────────────────────────────

describe("deriveChunks — real event names", () => {
  it("accumulates assistant.message_delta via deltaContent", () => {
    const frames: CopilotStreamFrame[] = [
      eventFrame("assistant.message_delta", { deltaContent: "Hello" }),
      eventFrame("assistant.message_delta", { deltaContent: " world" }),
    ];
    const chunks = deriveChunks(frames);
    expect(chunks).toHaveLength(1);
    expect(chunks[0]).toEqual({ kind: "text", text: "Hello world" });
  });

  it("does NOT produce text from the old assistant.text_delta name", () => {
    const frames: CopilotStreamFrame[] = [
      eventFrame("assistant.text_delta", { text: "should be ignored" }),
    ];
    const chunks = deriveChunks(frames);
    // text_delta is no longer a known event — results in no text output
    expect(chunks).toHaveLength(0);
  });

  it("uses assistant.message content as authoritative final text", () => {
    const frames: CopilotStreamFrame[] = [
      eventFrame("assistant.message_delta", { deltaContent: "partial" }),
      eventFrame("assistant.message", { content: "complete final text" }),
    ];
    const chunks = deriveChunks(frames);
    // The message event replaces accumulated deltas
    expect(chunks).toHaveLength(1);
    expect(chunks[0]).toEqual({ kind: "text", text: "complete final text" });
  });

  it("handles assistant.message alone (historical run without deltas)", () => {
    const frames: CopilotStreamFrame[] = [
      eventFrame("assistant.message", { content: "stored response" }),
    ];
    const chunks = deriveChunks(frames);
    expect(chunks).toHaveLength(1);
    expect(chunks[0]).toEqual({ kind: "text", text: "stored response" });
  });

  it("creates tool chunk from tool.execution_start", () => {
    const frames: CopilotStreamFrame[] = [
      eventFrame("tool.execution_start", {
        toolName: "apply_patch",
        arguments: "--- a/foo.ts\n+++ b/foo.ts\n@@ -1 +1 @@\n-old\n+new",
      }),
    ];
    const chunks = deriveChunks(frames);
    expect(chunks).toHaveLength(1);
    expect(chunks[0]).toMatchObject({ kind: "tool", name: "apply_patch" });
  });

  it("does NOT create tool chunk from the old tool_call_start name", () => {
    const frames: CopilotStreamFrame[] = [
      eventFrame("tool_call_start", { id: "x", name: "apply_patch", input: {} }),
    ];
    const chunks = deriveChunks(frames);
    expect(chunks).toHaveLength(0);
  });

  it("updates tool chunk output and filePaths from tool.execution_complete", () => {
    const frames: CopilotStreamFrame[] = [
      eventFrame("tool.execution_start", { toolName: "apply_patch", arguments: "patch" }),
      eventFrame("tool.execution_complete", {
        result: { detailedContent: "--- a/foo.ts\n+++ b/foo.ts\n@@ -1 +1 @@\n-old\n+new" },
        toolTelemetry: {
          restrictedProperties: { filePaths: ["src/foo.ts"] },
        },
      }),
    ];
    const chunks = deriveChunks(frames);
    expect(chunks).toHaveLength(1);
    const tool = chunks[0];
    expect(tool.kind).toBe("tool");
    if (tool.kind === "tool") {
      expect(tool.output).toContain("@@");
      expect(tool.filePaths).toEqual(["src/foo.ts"]);
    }
  });

  it("parses stringified telemetry filePaths from real apply_patch payloads", () => {
    const frames: CopilotStreamFrame[] = [
      eventFrame("tool.execution_start", { toolName: "apply_patch", arguments: "patch" }),
      eventFrame("tool.execution_complete", {
        result: { detailedContent: "--- a/foo.ts\n+++ b/foo.ts\n@@ -1 +1 @@\n-old\n+new" },
        toolTelemetry: {
          restrictedProperties: {
            filePaths: '["src/foo.ts"]',
            addedPaths: "[]",
          },
        },
      }),
    ];
    const chunks = deriveChunks(frames);
    const tool = chunks[0];
    expect(tool.kind).toBe("tool");
    if (tool.kind === "tool") {
      expect(tool.filePaths).toEqual(["src/foo.ts"]);
      expect(tool.addedPaths).toBeUndefined();
    }
  });

  it("handles raw frames", () => {
    const frames: CopilotStreamFrame[] = [rawFrame("raw output line")];
    const chunks = deriveChunks(frames);
    expect(chunks).toHaveLength(1);
    expect(chunks[0]).toEqual({ kind: "raw", text: "raw output line" });
  });

  it("ignores status frames", () => {
    const frames: CopilotStreamFrame[] = [
      eventFrame("assistant.message_delta", { deltaContent: "hi" }),
      statusFrame(),
    ];
    const chunks = deriveChunks(frames);
    expect(chunks).toHaveLength(1);
    expect(chunks[0]).toMatchObject({ kind: "text", text: "hi" });
  });

  it("parses JSON string arguments for tool.execution_start", () => {
    const args = JSON.stringify({ command: "ls", path: "/tmp" });
    const frames: CopilotStreamFrame[] = [
      eventFrame("tool.execution_start", { toolName: "run_command", arguments: args }),
    ];
    const chunks = deriveChunks(frames);
    expect(chunks[0]).toMatchObject({
      kind: "tool",
      name: "run_command",
      input: { command: "ls", path: "/tmp" },
    });
  });

  // ── Regression: empty assistant.message must not erase accumulated deltas ──

  it("empty assistant.message preserves accumulated delta text", () => {
    // Real Test-session shape: assistant.message arrives with content: ""
    // but useful text was already accumulated via message_delta events.
    const frames: CopilotStreamFrame[] = [
      eventFrame("assistant.message_delta", { deltaContent: "Hello from delta" }),
      eventFrame("assistant.message", { content: "" }),
    ];
    const chunks = deriveChunks(frames);
    expect(chunks).toHaveLength(1);
    expect(chunks[0]).toEqual({ kind: "text", text: "Hello from delta" });
  });

  // ── Regression: task_complete tool result must be promoted as visible text ──

  it("promotes task_complete tool.execution_complete content as visible text", () => {
    // Real Test-session shape: answer is in tool.execution_complete.result.content
    // for the task_complete tool, not in assistant.message.
    const frames: CopilotStreamFrame[] = [
      eventFrame("tool.execution_start", {
        toolName: "task_complete",
        arguments: { summary: "The answer is 42." },
      }),
      eventFrame("tool.execution_complete", {
        result: { content: "The answer is 42.", detailedContent: "The answer is 42." },
      }),
    ];
    const chunks = deriveChunks(frames);
    const textChunks = chunks.filter((c) => c.kind === "text");
    expect(textChunks).toHaveLength(1);
    expect(textChunks[0]).toEqual({ kind: "text", text: "The answer is 42." });
  });

  it("promotes user-facing session.task_complete summary as visible text", () => {
    // Real Test-session shape: session.task_complete event carries the final summary.
    const frames: CopilotStreamFrame[] = [
      eventFrame("session.task_complete", {
        summary: "Mình được vận hành bởi GPT-5.5.",
        success: true,
      }),
    ];
    const chunks = deriveChunks(frames);
    expect(chunks).toHaveLength(1);
    expect(chunks[0]).toEqual({ kind: "text", text: "Mình được vận hành bởi GPT-5.5." });
  });

  it("does not promote procedural task_complete summaries as assistant answers", () => {
    const frames: CopilotStreamFrame[] = [
      eventFrame("tool.execution_start", {
        toolName: "task_complete",
        arguments: { summary: "Acknowledging the greeting and closing the turn." },
      }),
      eventFrame("tool.execution_complete", {
        result: {
          content: "Acknowledging the greeting and closing the turn.",
          detailedContent: "Acknowledging the greeting and closing the turn.",
        },
      }),
      eventFrame("session.task_complete", {
        summary: "Acknowledging the greeting and closing the turn.",
        success: true,
      }),
    ];

    const chunks = deriveChunks(frames);
    expect(chunks.filter((chunk) => chunk.kind === "text")).toHaveLength(0);
    expect(chunks.some((chunk) => chunk.kind === "tool" && chunk.name === "task_complete")).toBe(
      true
    );
  });

  it("does not render procedural assistant text when the real answer is task_complete content", () => {
    const frames: CopilotStreamFrame[] = [
      eventFrame("assistant.message_delta", { deltaContent: "Acknowledging the greeting" }),
      eventFrame("assistant.message_delta", { deltaContent: " and closing the turn." }),
      eventFrame("assistant.message", {
        content: "Acknowledging the greeting and closing the turn.",
        toolRequests: [
          { name: "report_intent", arguments: { intent: "Answering greeting" } },
          { name: "task_complete", arguments: { summary: "Hi hi." } },
        ],
      }),
      eventFrame("tool.execution_start", {
        toolName: "report_intent",
        arguments: { intent: "Answering greeting" },
      }),
      eventFrame("tool.execution_complete", { result: { content: "Intent logged" } }),
      eventFrame("tool.execution_start", {
        toolName: "task_complete",
        arguments: { summary: "Hi hi." },
      }),
      eventFrame("tool.execution_complete", {
        result: { content: "Hi hi.", detailedContent: "✓ Task completed: Hi hi." },
      }),
      eventFrame("session.task_complete", { summary: "Hi hi.", success: true }),
    ];

    const chunks = deriveChunks(frames);
    const textChunks = chunks.filter((chunk) => chunk.kind === "text");
    expect(textChunks).toEqual([{ kind: "text", text: "Hi hi." }]);
    expect(textChunks.some((chunk) => chunk.text.includes("Acknowledging the greeting"))).toBe(
      false
    );
  });

  it("still promotes terse but user-facing completion answers", () => {
    const frames: CopilotStreamFrame[] = [
      eventFrame("tool.execution_start", {
        toolName: "task_complete",
        arguments: { summary: "Task completed." },
      }),
      eventFrame("tool.execution_complete", {
        result: { content: "Task completed.", detailedContent: "Task completed." },
      }),
    ];

    const chunks = deriveChunks(frames);
    expect(chunks.filter((chunk) => chunk.kind === "text")).toEqual([
      { kind: "text", text: "Task completed." },
    ]);
  });

  it("does not double-promote when both task_complete tool and session.task_complete are present", () => {
    // Real Test-session: both fire with the same text. Only one text chunk should appear.
    const frames: CopilotStreamFrame[] = [
      eventFrame("tool.execution_start", {
        toolName: "task_complete",
        arguments: { summary: "Answer." },
      }),
      eventFrame("tool.execution_complete", {
        result: { content: "Answer." },
      }),
      eventFrame("session.task_complete", { summary: "Answer.", success: true }),
    ];
    const chunks = deriveChunks(frames);
    const textChunks = chunks.filter((c) => c.kind === "text");
    expect(textChunks).toHaveLength(1);
  });
});

// ── extractFilePaths ──────────────────────────────────────────────────────────

describe("extractFilePaths", () => {
  it("returns telemetry-reported file paths from apply_patch", () => {
    const frames: CopilotStreamFrame[] = [
      eventFrame("tool.execution_start", { toolName: "apply_patch", arguments: "patch" }),
      eventFrame("tool.execution_complete", {
        result: { detailedContent: "@@ -1 +1 @@\n-old\n+new" },
        toolTelemetry: {
          restrictedProperties: { filePaths: ["src/a.ts", "src/b.ts"] },
        },
      }),
    ];
    const chunks = deriveChunks(frames);
    const files = extractFilePaths(chunks);
    expect(files.map((f) => f.path)).toEqual(["src/a.ts", "src/b.ts"]);
    expect(files[0].created).toBe(false);
  });

  it("uses stringified telemetry filePaths from real apply_patch payloads", () => {
    const frames: CopilotStreamFrame[] = [
      eventFrame("tool.execution_start", { toolName: "apply_patch", arguments: "patch" }),
      eventFrame("tool.execution_complete", {
        result: { detailedContent: "@@ -1 +1 @@\n-old\n+new" },
        toolTelemetry: {
          restrictedProperties: { filePaths: '["src/a.ts","src/b.ts"]' },
        },
      }),
    ];
    const chunks = deriveChunks(frames);
    const files = extractFilePaths(chunks);
    expect(files.map((f) => f.path)).toEqual(["src/a.ts", "src/b.ts"]);
  });

  it("attaches unifiedDiff when output looks like a real diff", () => {
    const diff = "@@ -1 +1 @@\n-old\n+new";
    const frames: CopilotStreamFrame[] = [
      eventFrame("tool.execution_start", { toolName: "apply_patch", arguments: "patch" }),
      eventFrame("tool.execution_complete", {
        result: { detailedContent: diff },
        toolTelemetry: { restrictedProperties: { filePaths: ["foo.ts"] } },
      }),
    ];
    const chunks = deriveChunks(frames);
    const files = extractFilePaths(chunks);
    expect(files[0].unifiedDiff).toBe(diff);
  });

  it("does NOT attach unifiedDiff when a patch touches multiple files", () => {
    const diff = "@@ -1 +1 @@\n-old\n+new";
    const frames: CopilotStreamFrame[] = [
      eventFrame("tool.execution_start", { toolName: "apply_patch", arguments: "patch" }),
      eventFrame("tool.execution_complete", {
        result: { detailedContent: diff },
        toolTelemetry: { restrictedProperties: { filePaths: ["foo.ts", "bar.ts"] } },
      }),
    ];
    const chunks = deriveChunks(frames);
    const files = extractFilePaths(chunks);
    expect(files).toHaveLength(2);
    expect(files[0].unifiedDiff).toBeUndefined();
    expect(files[1].unifiedDiff).toBeUndefined();
  });

  it("does NOT attach unifiedDiff when output lacks @@", () => {
    const frames: CopilotStreamFrame[] = [
      eventFrame("tool.execution_start", { toolName: "apply_patch", arguments: "patch" }),
      eventFrame("tool.execution_complete", {
        result: { detailedContent: "Success: file written" },
        toolTelemetry: { restrictedProperties: { filePaths: ["foo.ts"] } },
      }),
    ];
    const chunks = deriveChunks(frames);
    const files = extractFilePaths(chunks);
    expect(files[0].unifiedDiff).toBeUndefined();
  });

  it("marks create_file tool as created=true", () => {
    const frames: CopilotStreamFrame[] = [
      eventFrame("tool.execution_start", {
        toolName: "create_file",
        arguments: '{"path":"new.ts"}',
      }),
      eventFrame("tool.execution_complete", {
        result: {},
        toolTelemetry: { restrictedProperties: { filePaths: ["new.ts"] } },
      }),
    ];
    const chunks = deriveChunks(frames);
    const files = extractFilePaths(chunks);
    expect(files[0].created).toBe(true);
  });

  it("marks addedPaths entries as created when telemetry reports them", () => {
    const frames: CopilotStreamFrame[] = [
      eventFrame("tool.execution_start", { toolName: "apply_patch", arguments: "patch" }),
      eventFrame("tool.execution_complete", {
        result: { detailedContent: "@@ -0,0 +1 @@\n+hello" },
        toolTelemetry: {
          restrictedProperties: {
            filePaths: '["new.ts"]',
            addedPaths: '["new.ts"]',
          },
        },
      }),
    ];
    const chunks = deriveChunks(frames);
    const files = extractFilePaths(chunks);
    expect(files).toHaveLength(1);
    expect(files[0].created).toBe(true);
  });

  it("falls back to input.path when no telemetry filePaths", () => {
    const frames: CopilotStreamFrame[] = [
      eventFrame("tool.execution_start", {
        toolName: "write_file",
        arguments: JSON.stringify({ path: "src/out.ts", content: "x" }),
      }),
    ];
    const chunks = deriveChunks(frames);
    const files = extractFilePaths(chunks);
    expect(files).toHaveLength(1);
    expect(files[0].path).toBe("src/out.ts");
    expect(files[0].created).toBe(true);
  });

  it("deduplicates repeated paths", () => {
    const frames: CopilotStreamFrame[] = [
      eventFrame("tool.execution_start", { toolName: "apply_patch", arguments: "p1" }),
      eventFrame("tool.execution_complete", {
        result: { detailedContent: "@@ @@" },
        toolTelemetry: { restrictedProperties: { filePaths: ["dup.ts"] } },
      }),
      eventFrame("tool.execution_start", { toolName: "apply_patch", arguments: "p2" }),
      eventFrame("tool.execution_complete", {
        result: { detailedContent: "@@ @@" },
        toolTelemetry: { restrictedProperties: { filePaths: ["dup.ts"] } },
      }),
    ];
    const chunks = deriveChunks(frames);
    const files = extractFilePaths(chunks);
    expect(files).toHaveLength(1);
  });
});
