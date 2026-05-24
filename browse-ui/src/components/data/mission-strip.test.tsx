/**
 * Component tests for MissionStrip (Flight Recorder v3 §4d).
 */
import "@testing-library/jest-dom";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { MissionStrip, safeSlug } from "@/components/data/mission-strip";
import type { MissionRollup } from "@/lib/flight-recorder";

const ROLLUP: MissionRollup = {
  tools: [
    { name: "view", count: 12 },
    { name: "edit_file", count: 5 },
    { name: "grep", count: 3 },
  ],
  hooks: [{ name: "preToolUse", count: 9 }],
  skills: [{ name: "skill-A", count: 1 }],
  subagents: [],
  compactions: 2,
  errorCount: 3,
};

describe("MissionStrip", () => {
  it("renders all required aggregate chips", () => {
    render(<MissionStrip rollup={ROLLUP} />);
    expect(screen.getByTestId("flight-recorder-mission-strip")).toBeInTheDocument();
    expect(screen.getByTestId("mission-chip-tools")).toHaveTextContent(/Tools used: 20/);
    expect(screen.getByTestId("mission-chip-hooks")).toHaveTextContent(/Hooks fired: 9/);
    expect(screen.getByTestId("mission-chip-skills")).toHaveTextContent(/Skills: 1/);
    expect(screen.getByTestId("mission-chip-subagents")).toHaveTextContent(/Sub-agents: 0/);
    expect(screen.getByTestId("mission-chip-compactions")).toHaveTextContent(/Compactions: 2/);
    expect(screen.getByTestId("mission-chip-errors")).toHaveTextContent(/Errors: 3/);
  });

  it("renders top-N tool/hook item chips with safe slugs", () => {
    render(<MissionStrip rollup={ROLLUP} />);
    expect(screen.getByTestId(`mission-chip-tool-${safeSlug("view")}`)).toBeInTheDocument();
    expect(screen.getByTestId(`mission-chip-tool-${safeSlug("edit_file")}`)).toBeInTheDocument();
    expect(screen.getByTestId(`mission-chip-tool-${safeSlug("grep")}`)).toBeInTheDocument();
    expect(screen.getByTestId(`mission-chip-hook-${safeSlug("preToolUse")}`)).toBeInTheDocument();
  });

  it("invokes onSelectCategory with the chip kind", () => {
    const onCat = vi.fn();
    render(<MissionStrip rollup={ROLLUP} onSelectCategory={onCat} />);
    fireEvent.click(screen.getByTestId("mission-chip-tools"));
    expect(onCat).toHaveBeenCalledWith("tool");
    fireEvent.click(screen.getByTestId("mission-chip-errors"));
    expect(onCat).toHaveBeenLastCalledWith("error");
  });

  it("invokes onSelectItem with the tool name", () => {
    const onItem = vi.fn();
    render(<MissionStrip rollup={ROLLUP} onSelectItem={onItem} />);
    fireEvent.click(screen.getByTestId(`mission-chip-tool-${safeSlug("edit_file")}`));
    expect(onItem).toHaveBeenCalledWith("tool", "edit_file");
  });

  it("safeSlug rejects path traversal / control chars", () => {
    expect(safeSlug("../etc/passwd")).toMatch(/^[a-z0-9-]+$/);
    expect(safeSlug("")).toBe("none");
    expect(safeSlug("\u0000\u0001\u007fhello")).toBe("hello");
  });

  it("clamps top-N to topN prop", () => {
    render(<MissionStrip rollup={ROLLUP} topN={1} />);
    expect(screen.getByTestId(`mission-chip-tool-${safeSlug("view")}`)).toBeInTheDocument();
    expect(screen.queryByTestId(`mission-chip-tool-${safeSlug("edit_file")}`)).toBeNull();
  });
});
