import "@testing-library/jest-dom";
import { render, screen, fireEvent } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { Composer } from "./composer";

// ── Helpers ──────────────────────────────────────────────────────────────────

function renderComposer(props: Partial<React.ComponentProps<typeof Composer>> = {}) {
  const onSubmit = vi.fn();
  const onCommand = vi.fn();
  render(<Composer onSubmit={onSubmit} onCommand={onCommand} {...props} />);
  return { onSubmit, onCommand };
}

function getTextarea() {
  return screen.getByRole("textbox", { name: "Prompt" }) as HTMLTextAreaElement;
}

// ── Basic submit behavior ────────────────────────────────────────────────────

describe("Composer — basic submit", () => {
  it("calls onSubmit with prompt text for regular input", () => {
    const { onSubmit, onCommand } = renderComposer();
    const ta = getTextarea();
    fireEvent.change(ta, { target: { value: "Hello world" } });
    fireEvent.submit(ta.closest("form")!);
    expect(onSubmit).toHaveBeenCalledOnce();
    expect(onSubmit).toHaveBeenCalledWith("Hello world", []);
    expect(onCommand).not.toHaveBeenCalled();
  });

  it("does not call onSubmit for empty input", () => {
    const { onSubmit } = renderComposer();
    fireEvent.submit(getTextarea().closest("form")!);
    expect(onSubmit).not.toHaveBeenCalled();
  });
});

// ── Slash command suggestions ────────────────────────────────────────────────

describe("Composer — slash suggestions", () => {
  it("shows suggestion list when typing a slash prefix", () => {
    renderComposer();
    const ta = getTextarea();
    fireEvent.change(ta, { target: { value: "/h" } });
    expect(screen.getByRole("listbox", { name: "Slash command suggestions" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: /\/help/i })).toBeInTheDocument();
  });

  it("shows all web-supported suggestions when typing only /", () => {
    renderComposer();
    fireEvent.change(getTextarea(), { target: { value: "/" } });
    const listbox = screen.getByRole("listbox", { name: "Slash command suggestions" });
    expect(listbox).toBeInTheDocument();
    expect(screen.getByRole("option", { name: /\/help/i })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: /\/skills/i })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: /\/session/i })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: /\/new/i })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: /\/clear/i })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: /\/mode/i })).toBeInTheDocument();
  });

  it("renders the suggestion list in a portal so overflow-hidden parents do not clip /skills", () => {
    const onSubmit = vi.fn();
    const onCommand = vi.fn();
    const { container } = render(
      <div className="overflow-hidden">
        <Composer onSubmit={onSubmit} onCommand={onCommand} />
      </div>
    );

    fireEvent.change(getTextarea(), { target: { value: "/" } });

    const listbox = screen.getByRole("listbox", { name: "Slash command suggestions" });
    expect(listbox).toBeInTheDocument();
    expect(screen.getByRole("option", { name: /\/skills/i })).toBeInTheDocument();
    expect(container).not.toContainElement(listbox);
  });

  it("hides suggestions when input no longer matches a prefix", () => {
    renderComposer();
    const ta = getTextarea();
    fireEvent.change(ta, { target: { value: "/h" } });
    expect(screen.getByRole("listbox")).toBeInTheDocument();
    fireEvent.change(ta, { target: { value: "hello" } });
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
  });

  it("filters suggestions by prefix", () => {
    renderComposer();
    fireEvent.change(getTextarea(), { target: { value: "/sk" } });
    expect(screen.getByRole("option", { name: /\/skills/i })).toBeInTheDocument();
    expect(screen.queryByRole("option", { name: /\/help/i })).not.toBeInTheDocument();
  });

  it("hides suggestions once the user types a space (args phase)", () => {
    renderComposer();
    fireEvent.change(getTextarea(), { target: { value: "/help " } });
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
  });

  it("dismisses suggestions on Escape", () => {
    renderComposer();
    const ta = getTextarea();
    fireEvent.change(ta, { target: { value: "/" } });
    expect(screen.getByRole("listbox")).toBeInTheDocument();
    fireEvent.keyDown(ta, { key: "Escape" });
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
  });

  it("dismisses suggestions when the textarea loses focus", () => {
    renderComposer();
    const ta = getTextarea();
    fireEvent.change(ta, { target: { value: "/h" } });
    expect(screen.getByRole("listbox")).toBeInTheDocument();
    fireEvent.blur(ta);
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
  });
});

// ── Safe path handling ───────────────────────────────────────────────────────

describe("Composer — path-like input safety", () => {
  it.each([
    "/Users/linhn/project/file.ts",
    "/home/user/.config/copilot",
    "/tmp/scratch.txt",
    "/opt/local/bin",
    "/etc/hosts",
  ])("does not show suggestions for path-like input: %s", (path) => {
    renderComposer();
    fireEvent.change(getTextarea(), { target: { value: path } });
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
  });

  it.each(["/Users/linhn/project/file.ts", "/home/user/.config/copilot", "/tmp/scratch.txt"])(
    "submits path-like input as a normal prompt: %s",
    (path) => {
      const { onSubmit, onCommand } = renderComposer();
      const ta = getTextarea();
      fireEvent.change(ta, { target: { value: path } });
      fireEvent.submit(ta.closest("form")!);
      expect(onSubmit).toHaveBeenCalledWith(path, []);
      expect(onCommand).not.toHaveBeenCalled();
    }
  );
});

// ── Command execution ────────────────────────────────────────────────────────

describe("Composer — command execution", () => {
  it("calls onCommand and clears input for /help", () => {
    const { onSubmit, onCommand } = renderComposer();
    const ta = getTextarea();
    fireEvent.change(ta, { target: { value: "/help" } });
    fireEvent.submit(ta.closest("form")!);
    expect(onCommand).toHaveBeenCalledWith("help", "");
    expect(onSubmit).not.toHaveBeenCalled();
    expect(ta.value).toBe("");
  });

  it("calls onCommand and clears input for /skills", () => {
    const { onCommand } = renderComposer();
    const ta = getTextarea();
    fireEvent.change(ta, { target: { value: "/skills" } });
    fireEvent.submit(ta.closest("form")!);
    expect(onCommand).toHaveBeenCalledWith("skills", "");
  });

  it("calls onCommand with args for /mode interactive", () => {
    const { onCommand } = renderComposer();
    const ta = getTextarea();
    fireEvent.change(ta, { target: { value: "/mode interactive" } });
    fireEvent.submit(ta.closest("form")!);
    expect(onCommand).toHaveBeenCalledWith("mode", "interactive");
  });

  it("calls onCommand for /clear", () => {
    const { onCommand } = renderComposer();
    const ta = getTextarea();
    fireEvent.change(ta, { target: { value: "/clear" } });
    fireEvent.submit(ta.closest("form")!);
    expect(onCommand).toHaveBeenCalledWith("clear", "");
  });

  it("submits normally when onCommand is not provided", () => {
    const onSubmit = vi.fn();
    render(<Composer onSubmit={onSubmit} />);
    const ta = getTextarea();
    fireEvent.change(ta, { target: { value: "/help" } });
    fireEvent.submit(ta.closest("form")!);
    // Without onCommand, even slash inputs go to onSubmit
    expect(onSubmit).toHaveBeenCalledWith("/help", []);
  });

  it("does not intercept CLI-reference-only commands", () => {
    const { onSubmit, onCommand } = renderComposer();
    const ta = getTextarea();
    fireEvent.change(ta, { target: { value: "/model gpt-5" } });
    fireEvent.submit(ta.closest("form")!);
    expect(onSubmit).toHaveBeenCalledWith("/model gpt-5", []);
    expect(onCommand).not.toHaveBeenCalled();
  });
});

// ── Suggestion selection via click ───────────────────────────────────────────

describe("Composer — suggestion selection", () => {
  it("fills a no-arg command into the textarea on click", () => {
    const { onCommand } = renderComposer();
    const ta = getTextarea();
    fireEvent.change(ta, { target: { value: "/he" } });
    const helpOption = screen.getByRole("option", { name: /\/help/i });
    fireEvent.mouseDown(helpOption.querySelector("button")!);
    expect(onCommand).not.toHaveBeenCalled();
    expect(ta.value).toBe("/help");
  });

  it("fills textarea for commands that require args (/mode)", () => {
    const { onCommand } = renderComposer();
    const ta = getTextarea();
    fireEvent.change(ta, { target: { value: "/mo" } });
    const modeOption = screen.getByRole("option", { name: /\/mode/i });
    fireEvent.mouseDown(modeOption.querySelector("button")!);
    // onCommand not called yet — user still needs to type the argument
    expect(onCommand).not.toHaveBeenCalled();
    expect(ta.value).toBe("/mode ");
  });
});

// ── Keyboard navigation ──────────────────────────────────────────────────────

describe("Composer — keyboard navigation in suggestions", () => {
  it("navigates to /skills with ArrowDown and accepts the completion with Enter", () => {
    const { onCommand } = renderComposer();
    const ta = getTextarea();
    fireEvent.change(ta, { target: { value: "/" } });

    // ArrowDown to /help, ArrowDown again to /skills, then accept completion.
    fireEvent.keyDown(ta, { key: "ArrowDown" });
    fireEvent.keyDown(ta, { key: "ArrowDown" });
    fireEvent.keyDown(ta, { key: "Enter" });
    expect(onCommand).not.toHaveBeenCalled();
    expect(ta.value).toBe("/skills");
  });

  it("uses Tab to accept the first matching completion even before arrow navigation", () => {
    const { onCommand } = renderComposer();
    const ta = getTextarea();
    fireEvent.change(ta, { target: { value: "/sk" } });

    fireEvent.keyDown(ta, { key: "Tab" });

    expect(onCommand).not.toHaveBeenCalled();
    expect(ta.value).toBe("/skills");
  });
});

// ── #557/#556: Preflight chips, hard-error gate, soft-cap override ───────────

import type { PreflightResponse } from "@/lib/api/types";
import { act, waitFor } from "@testing-library/react";

function basePreflight(overrides: Partial<PreflightResponse> = {}): PreflightResponse {
  return {
    estimated_input_tokens: 123,
    model: "gpt-5.4",
    model_known: true,
    model_cost_tier: "standard",
    model_context_window: 200_000,
    context_fit: "fits",
    context_fit_fraction: 0.05,
    cost_units: 1,
    attachment_count: 0,
    attachment_total_bytes: 0,
    redaction: { hits: 0, categories: [], safe_excerpts: [] },
    warnings: [],
    hard_errors: [],
    within_limit: true,
    quota_status: "ok",
    quota_reason: "",
    override_allowed: false,
    usage: {
      prompts_this_hour: 0,
      prompts_today: 0,
      hourly_limit: 100,
      daily_limit: 1000,
      remaining_hour: 100,
      remaining_day: 1000,
    },
    ...overrides,
  };
}

async function flushDebounce(ms = 350) {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, ms));
  });
}

describe("Composer — preflight (#557)", () => {
  it("renders preflight chips after a debounced preflight call", async () => {
    const runPreflight = vi.fn().mockResolvedValue(
      basePreflight({
        estimated_input_tokens: 42,
        attachment_count: 0,
        context_fit: "fits",
      })
    );
    const onSubmit = vi.fn();
    render(<Composer onSubmit={onSubmit} runPreflight={runPreflight} preflightDebounceMs={50} />);
    fireEvent.change(getTextarea(), { target: { value: "Hello" } });
    await flushDebounce(120);
    await waitFor(() => expect(runPreflight).toHaveBeenCalled());
    expect(runPreflight.mock.calls[0][0].prompt).toBe("Hello");
    // Attachment metadata-only contract: no `data` field present.
    expect(runPreflight.mock.calls[0][0].files).toEqual([]);
    expect(await screen.findByTestId("preflight-chips")).toBeInTheDocument();
    expect(screen.getByTestId("preflight-chip-tokens")).toHaveTextContent(/42/);
    expect(screen.getByTestId("preflight-chip-context")).toHaveTextContent(/fits/);
  });

  it("disables Send and shows banner when preflight returns hard errors", async () => {
    const runPreflight = vi.fn().mockResolvedValue(
      basePreflight({
        within_limit: false,
        quota_status: "block",
        quota_reason: "GLOBAL_HOUR_HARD",
        hard_errors: [
          { code: "QUOTA_GLOBAL_HOUR_HARD", message: "Usage quota exceeded: GLOBAL_HOUR_HARD" },
        ],
      })
    );
    const onSubmit = vi.fn();
    render(<Composer onSubmit={onSubmit} runPreflight={runPreflight} preflightDebounceMs={20} />);
    fireEvent.change(getTextarea(), { target: { value: "blocked" } });
    await flushDebounce(80);
    const banner = await screen.findByTestId("preflight-hard-error");
    expect(banner).toBeInTheDocument();
    expect(banner).toHaveTextContent(/QUOTA_GLOBAL_HOUR_HARD/);
    const sendBtn = screen.getByRole("button", { name: "Send prompt" });
    expect(sendBtn).toBeDisabled();
    // Even a submit attempt is a no-op.
    fireEvent.submit(getTextarea().closest("form")!);
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("requires explicit override before submitting on soft-cap warning", async () => {
    const runPreflight = vi.fn().mockResolvedValue(
      basePreflight({
        quota_status: "warn",
        quota_reason: "GLOBAL_HOUR_SOFT",
        override_allowed: true,
        warnings: [
          {
            code: "QUOTA_GLOBAL_HOUR_SOFT",
            message: "Usage near limit: GLOBAL_HOUR_SOFT",
            severity: "warn",
          },
        ],
      })
    );
    const onSubmit = vi.fn();
    render(<Composer onSubmit={onSubmit} runPreflight={runPreflight} preflightDebounceMs={20} />);
    fireEvent.change(getTextarea(), { target: { value: "near-quota" } });
    await flushDebounce(80);

    // Warning banner present, Send disabled until override is clicked.
    expect(await screen.findByTestId("preflight-warning")).toBeInTheDocument();
    const sendBtn = screen.getByRole("button", { name: "Send prompt" });
    expect(sendBtn).toBeDisabled();

    const overrideBtn = screen.getByTestId("preflight-override-button");
    await act(async () => {
      fireEvent.click(overrideBtn);
    });

    // #556 follow-up: clicking "Submit anyway" only acknowledges locally.
    // The audit entry is recorded server-side by `handle_run_prompt` when
    // the resubmit carries `override_acknowledged: true`, so the composer
    // does NOT call any client-side audit endpoint here.
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Send prompt" })).not.toBeDisabled()
    );
    fireEvent.submit(getTextarea().closest("form")!);
    expect(onSubmit).toHaveBeenCalledOnce();
    expect(onSubmit).toHaveBeenCalledWith("near-quota", [], { overrideAcknowledged: true });
  });

  it("hides the override button when policy disallows the warning", async () => {
    const runPreflight = vi.fn().mockResolvedValue(
      basePreflight({
        quota_status: "warn",
        quota_reason: "GLOBAL_HOUR_SOFT",
        override_allowed: false,
        warnings: [{ code: "QUOTA_GLOBAL_HOUR_SOFT", message: "near limit", severity: "warn" }],
      })
    );
    render(<Composer onSubmit={vi.fn()} runPreflight={runPreflight} preflightDebounceMs={20} />);
    fireEvent.change(getTextarea(), { target: { value: "blocked-override" } });
    await flushDebounce(80);
    await screen.findByTestId("preflight-warning");
    expect(screen.queryByTestId("preflight-override-button")).not.toBeInTheDocument();
  });

  it("re-runs preflight on prompt edit (stale invalidation)", async () => {
    const runPreflight = vi.fn().mockResolvedValue(basePreflight());
    render(<Composer onSubmit={vi.fn()} runPreflight={runPreflight} preflightDebounceMs={20} />);
    fireEvent.change(getTextarea(), { target: { value: "first" } });
    await flushDebounce(60);
    fireEvent.change(getTextarea(), { target: { value: "second" } });
    await flushDebounce(60);
    await waitFor(() => expect(runPreflight).toHaveBeenCalledTimes(2));
    expect(runPreflight.mock.calls[1][0].prompt).toBe("second");
  });

  it("does not call preflight when runPreflight is not provided", async () => {
    const onSubmit = vi.fn();
    render(<Composer onSubmit={onSubmit} preflightDebounceMs={20} />);
    fireEvent.change(getTextarea(), { target: { value: "no-preflight" } });
    await flushDebounce(80);
    expect(screen.queryByTestId("preflight-chips")).not.toBeInTheDocument();
    fireEvent.submit(getTextarea().closest("form")!);
    expect(onSubmit).toHaveBeenCalledWith("no-preflight", []);
  });

  it("clears preflight result when prompt is emptied", async () => {
    const runPreflight = vi.fn().mockResolvedValue(basePreflight());
    render(<Composer onSubmit={vi.fn()} runPreflight={runPreflight} preflightDebounceMs={20} />);
    fireEvent.change(getTextarea(), { target: { value: "hello" } });
    await flushDebounce(80);
    await screen.findByTestId("preflight-chips");
    fireEvent.change(getTextarea(), { target: { value: "" } });
    await flushDebounce(80);
    await waitFor(() => expect(screen.queryByTestId("preflight-chips")).not.toBeInTheDocument());
  });

  it("renders redaction chip with category preview", async () => {
    const runPreflight = vi.fn().mockResolvedValue(
      basePreflight({
        redaction: {
          hits: 2,
          categories: ["aws_secret", "github_pat"],
          safe_excerpts: ["[REDACTED]", "[REDACTED]"],
        },
      })
    );
    render(<Composer onSubmit={vi.fn()} runPreflight={runPreflight} preflightDebounceMs={20} />);
    fireEvent.change(getTextarea(), { target: { value: "look at AKIA…" } });
    await flushDebounce(80);
    const chip = await screen.findByTestId("preflight-chip-redaction");
    expect(chip).toHaveTextContent(/2 redacted/);
    expect(chip).toHaveTextContent(/aws_secret/);
    // Hard guarantee: the safe_excerpt sentinel must never expose raw bytes.
    expect(chip.textContent).not.toMatch(/AKIA/);
  });

  it("never re-reads attachment bytes for preflight (metadata-only payload)", async () => {
    const runPreflight = vi.fn().mockResolvedValue(basePreflight());
    render(<Composer onSubmit={vi.fn()} runPreflight={runPreflight} preflightDebounceMs={20} />);
    fireEvent.change(getTextarea(), { target: { value: "hello" } });
    await flushDebounce(80);
    await waitFor(() => expect(runPreflight).toHaveBeenCalled());
    const payload = runPreflight.mock.calls[0][0];
    // The contract: files contains only {name,type,size} — never `data`.
    for (const f of payload.files ?? []) {
      expect("data" in f).toBe(false);
    }
  });
});
