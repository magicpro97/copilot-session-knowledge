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
