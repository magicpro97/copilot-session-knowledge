import "@testing-library/jest-dom";
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { MetadataBar } from "./metadata-bar";
import type { OperatorSession } from "@/lib/api/types";

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
