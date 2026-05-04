/**
 * Regression tests for WorkspacePicker suggestion list surface.
 *
 * These guard against the transparency bug where the suggestion dropdown
 * appeared translucent or invisible against some page backgrounds.
 * The fix uses POPUP_SURFACE_BASE (shared opaque popup surface) instead of
 * a bespoke class string with a `style={{ opacity: 1 }}` workaround.
 */
import "@testing-library/jest-dom";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { POPUP_SURFACE_BASE } from "@/components/ui/popup-surface";
import { WorkspacePicker } from "@/components/chat/workspace-picker";

vi.mock("next/navigation", () => ({
  usePathname: vi.fn(() => "/chat"),
}));

vi.mock("@/lib/host-profiles", () => ({
  LOCAL_HOST: {
    id: "local",
    label: "Local",
    base_url: "",
    token: "",
    cli_kind: "copilot",
    is_default: true,
  },
  isOperatorHostEnabled: vi.fn(() => true),
}));

vi.mock("@/lib/api/hooks", () => ({
  usePathSuggest: vi.fn(() => ({
    data: { suggestions: ["/home/user/projects", "/home/user/code"] },
  })),
}));

vi.mock("@/providers/host-provider", () => ({
  useHostState: vi.fn(() => ({
    host: {
      id: "local",
      label: "Local",
      base_url: "",
      token: "",
      cli_kind: "copilot",
      is_default: true,
    },
    diagnosticsEnabled: true,
    localDiagnosticsEnabled: true,
  })),
}));

describe("WorkspacePicker — suggestion list surface", () => {
  it("renders a suggestion listbox when input is focused", () => {
    render(<WorkspacePicker value="" onChange={() => {}} />);
    const input = screen.getByRole("textbox");
    fireEvent.focus(input);

    const list = document.querySelector('[role="listbox"]');
    expect(list).not.toBeNull();
  });

  it("suggestion list uses bg-popover (opaque background, not transparent)", () => {
    render(<WorkspacePicker value="" onChange={() => {}} />);
    fireEvent.focus(screen.getByRole("textbox"));

    const list = document.querySelector('[role="listbox"]');
    expect(list).not.toBeNull();
    expect(list!.className).toContain("bg-popover");
    expect(list!.className).not.toContain("bg-transparent");
  });

  it("suggestion list uses all POPUP_SURFACE_BASE tokens (shared opaque surface)", () => {
    render(<WorkspacePicker value="" onChange={() => {}} />);
    fireEvent.focus(screen.getByRole("textbox"));

    const list = document.querySelector('[role="listbox"]');
    expect(list).not.toBeNull();
    for (const token of POPUP_SURFACE_BASE.split(" ")) {
      expect(list!.className).toContain(token);
    }
  });

  it("suggestion list has no inline opacity override (opacity-1 workaround removed)", () => {
    render(<WorkspacePicker value="" onChange={() => {}} />);
    fireEvent.focus(screen.getByRole("textbox"));

    const list = document.querySelector('[role="listbox"]') as HTMLElement | null;
    expect(list).not.toBeNull();
    // style={{ opacity: 1 }} was a historical workaround for the transparency bug;
    // using POPUP_SURFACE_BASE makes it unnecessary and it must not come back.
    expect(list!.style.opacity).toBe("");
  });

  it("suggestions are selectable and call onChange", () => {
    const onChange = vi.fn();
    render(<WorkspacePicker value="" onChange={onChange} />);
    fireEvent.focus(screen.getByRole("textbox"));

    const options = document.querySelectorAll('[role="option"]');
    expect(options.length).toBeGreaterThan(0);

    fireEvent.mouseDown(options[0]);
    expect(onChange).toHaveBeenCalledWith("/home/user/projects");
  });
});
