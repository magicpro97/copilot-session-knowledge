import "@testing-library/jest-dom";
import { render, screen, fireEvent } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { MetadataBar } from "./metadata-bar";
import type { OperatorSession } from "@/lib/api/types";
import { POPUP_SURFACE_BASE } from "@/components/ui/popup-surface";

const baseSession: OperatorSession = {
  id: "sess-001",
  name: "My Session",
  model: "claude-sonnet-4.6",
  mode: "default",
  workspace: "/Users/user/projects/app",
  add_dirs: [],
  created_at: "2026-05-01T10:00:00Z",
  updated_at: "2026-05-01T10:05:00Z",
  run_count: 3,
  last_run_id: "run-abc",
  resume_ready: false,
};

describe("MetadataBar", () => {
  it("renders workspace path", () => {
    render(<MetadataBar session={baseSession} />);
    expect(screen.getByText("/Users/user/projects/app")).toBeInTheDocument();
  });

  it("renders model name", () => {
    render(<MetadataBar session={baseSession} />);
    expect(screen.getByText("claude-sonnet-4.6")).toBeInTheDocument();
  });

  it("renders mode", () => {
    render(<MetadataBar session={baseSession} />);
    expect(screen.getByText("default")).toBeInTheDocument();
  });

  it("renders run count", () => {
    render(<MetadataBar session={baseSession} />);
    expect(screen.getByText("3 runs")).toBeInTheDocument();
  });

  // ── Regression: resume_ready context badge ──────────────────────────────────

  it("shows context-ready indicator when resume_ready is true", () => {
    render(<MetadataBar session={{ ...baseSession, resume_ready: true }} />);
    // A label indicating context is available for resumption
    expect(screen.getByText(/context ready/i)).toBeInTheDocument();
  });

  it("shows new-context indicator when resume_ready is false", () => {
    render(<MetadataBar session={{ ...baseSession, resume_ready: false }} />);
    expect(screen.queryByText(/context ready/i)).not.toBeInTheDocument();
    expect(screen.getByText(/new context/i)).toBeInTheDocument();
  });
});

// ── Edit affordance ──────────────────────────────────────────────────────────

describe("MetadataBar — edit affordance", () => {
  const editSession: OperatorSession = {
    ...baseSession,
    mode: "interactive",
    name: "Edit Me",
    model: "gpt-5.4",
  };

  it("does not render the edit button when onUpdate is not supplied", () => {
    render(<MetadataBar session={editSession} />);
    expect(screen.queryByTestId("edit-session-btn")).not.toBeInTheDocument();
  });

  it("renders the edit button when onUpdate is supplied", () => {
    render(<MetadataBar session={editSession} onUpdate={vi.fn()} />);
    expect(screen.getByTestId("edit-session-btn")).toBeInTheDocument();
  });

  it("edit button is disabled when isRunning is true", () => {
    render(<MetadataBar session={editSession} onUpdate={vi.fn()} isRunning />);
    const btn = screen.getByTestId("edit-session-btn");
    expect(btn).toBeDisabled();
    expect(btn).toHaveAttribute("aria-label", "Editing disabled during active run");
  });

  it("edit button is disabled when isUpdating is true", () => {
    render(<MetadataBar session={editSession} onUpdate={vi.fn()} isUpdating />);
    expect(screen.getByTestId("edit-session-btn")).toBeDisabled();
  });

  it("opens the edit popover on click and shows form fields", () => {
    render(<MetadataBar session={editSession} onUpdate={vi.fn()} />);
    fireEvent.click(screen.getByTestId("edit-session-btn"));

    expect(screen.getByPlaceholderText("Session name")).toBeInTheDocument();
    expect(screen.getByPlaceholderText("CLI default")).toBeInTheDocument();
  });

  it("shows the next-run and operator-approval notice in the edit popover", () => {
    render(<MetadataBar session={editSession} onUpdate={vi.fn()} />);
    fireEvent.click(screen.getByTestId("edit-session-btn"));

    const note = screen.getByTestId("next-run-note");
    expect(note).toBeInTheDocument();
    expect(note.textContent).toMatch(/next run/i);
    expect(note.textContent).toMatch(/operator-approved/i);
  });

  it("calls onUpdate with only changed fields on save", () => {
    const onUpdate = vi.fn();
    render(<MetadataBar session={editSession} onUpdate={onUpdate} />);
    fireEvent.click(screen.getByTestId("edit-session-btn"));

    // Change the name
    const nameInput = screen.getByPlaceholderText("Session name");
    fireEvent.change(nameInput, { target: { value: "New Name" } });

    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    expect(onUpdate).toHaveBeenCalledOnce();
    expect(onUpdate).toHaveBeenCalledWith(expect.objectContaining({ name: "New Name" }));
    // Model and mode unchanged — should not appear in payload
    const payload = onUpdate.mock.calls[0][0];
    expect(payload.model).toBeUndefined();
    expect(payload.mode).toBeUndefined();
  });

  it("allows clearing model to return to the CLI default", () => {
    const onUpdate = vi.fn();
    render(<MetadataBar session={editSession} onUpdate={onUpdate} />);
    fireEvent.click(screen.getByTestId("edit-session-btn"));

    const modelInput = screen.getByPlaceholderText("CLI default");
    fireEvent.change(modelInput, { target: { value: "" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    expect(onUpdate).toHaveBeenCalledOnce();
    expect(onUpdate).toHaveBeenCalledWith(expect.objectContaining({ model: "" }));
  });

  it("does not call onUpdate when no fields have changed", () => {
    const onUpdate = vi.fn();
    render(<MetadataBar session={editSession} onUpdate={onUpdate} />);
    fireEvent.click(screen.getByTestId("edit-session-btn"));

    // Submit without changing anything
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(onUpdate).not.toHaveBeenCalled();
  });

  it("closes the popover on Cancel without calling onUpdate", () => {
    const onUpdate = vi.fn();
    render(<MetadataBar session={editSession} onUpdate={onUpdate} />);
    fireEvent.click(screen.getByTestId("edit-session-btn"));

    // Verify popover opened
    expect(screen.getByPlaceholderText("Session name")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(onUpdate).not.toHaveBeenCalled();
  });

  it("form fields are pre-populated with the current session values", () => {
    render(<MetadataBar session={editSession} onUpdate={vi.fn()} />);
    fireEvent.click(screen.getByTestId("edit-session-btn"));

    expect((screen.getByPlaceholderText("Session name") as HTMLInputElement).value).toBe(
      editSession.name
    );
    expect((screen.getByPlaceholderText("CLI default") as HTMLInputElement).value).toBe(
      editSession.model
    );
  });
});

// ── External editor open (slash /session command) ────────────────────────────

describe("MetadataBar — openEditor prop", () => {
  it("opens the editor when openEditor transitions to true", () => {
    const { rerender } = render(
      <MetadataBar session={baseSession} onUpdate={vi.fn()} openEditor={false} />
    );
    expect(screen.queryByPlaceholderText("Session name")).not.toBeInTheDocument();

    rerender(<MetadataBar session={baseSession} onUpdate={vi.fn()} openEditor={true} />);
    expect(screen.getByPlaceholderText("Session name")).toBeInTheDocument();
  });

  it("does not open the editor when isRunning is true", () => {
    const { rerender } = render(
      <MetadataBar session={baseSession} onUpdate={vi.fn()} openEditor={false} isRunning />
    );
    rerender(<MetadataBar session={baseSession} onUpdate={vi.fn()} openEditor={true} isRunning />);
    expect(screen.queryByPlaceholderText("Session name")).not.toBeInTheDocument();
  });

  it("calls onEditorClose when the editor is dismissed", () => {
    const onEditorClose = vi.fn();
    render(
      <MetadataBar
        session={baseSession}
        onUpdate={vi.fn()}
        openEditor={true}
        onEditorClose={onEditorClose}
      />
    );

    // Editor should be open; cancel it
    expect(screen.getByPlaceholderText("Session name")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(onEditorClose).toHaveBeenCalledOnce();
  });

  it("closes the editor when the active session changes", () => {
    const onEditorClose = vi.fn();
    const { rerender } = render(
      <MetadataBar
        session={{ ...baseSession, id: "sess-001", name: "Session A" }}
        onUpdate={vi.fn()}
        openEditor={true}
        onEditorClose={onEditorClose}
      />
    );

    expect(screen.getByPlaceholderText("Session name")).toBeInTheDocument();

    rerender(
      <MetadataBar
        session={{ ...baseSession, id: "sess-002", name: "Session B" }}
        onUpdate={vi.fn()}
        openEditor={false}
        onEditorClose={onEditorClose}
      />
    );

    expect(screen.queryByPlaceholderText("Session name")).not.toBeInTheDocument();
    expect(onEditorClose).toHaveBeenCalledOnce();
  });
});

// ── Surface regression: edit-session popover uses shared popup surface ────────

describe("MetadataBar — edit popover surface contract", () => {
  const editSession: OperatorSession = {
    id: "sess-surf",
    name: "Surface Test",
    model: "claude-sonnet-4.6",
    mode: "interactive",
    workspace: "/home/user/project",
    add_dirs: [],
    created_at: "2026-05-01T10:00:00Z",
    updated_at: "2026-05-01T10:05:00Z",
    run_count: 1,
    last_run_id: null,
    resume_ready: false,
  };

  it("edit popover surface carries all shared popup surface tokens", () => {
    render(<MetadataBar session={editSession} onUpdate={vi.fn()} />);
    fireEvent.click(screen.getByTestId("edit-session-btn"));

    // PopoverContent renders into a portal — findable via data-slot attribute
    const popoverContent = document.querySelector('[data-slot="popover-content"]');
    expect(popoverContent).not.toBeNull();
    for (const token of POPUP_SURFACE_BASE.split(" ")) {
      expect(popoverContent!.className).toContain(token);
    }
  });

  it("edit popover surface does not use semi-transparent background overrides", () => {
    render(<MetadataBar session={editSession} onUpdate={vi.fn()} />);
    fireEvent.click(screen.getByTestId("edit-session-btn"));

    const popoverContent = document.querySelector('[data-slot="popover-content"]');
    expect(popoverContent).not.toBeNull();
    const cls = popoverContent!.className;
    // No opacity fractions on the background (would make surface see-through)
    expect(cls).not.toMatch(/bg-popover\/\d+/);
    expect(cls).not.toContain("bg-transparent");
    expect(cls).not.toContain("backdrop-blur");
  });
});
