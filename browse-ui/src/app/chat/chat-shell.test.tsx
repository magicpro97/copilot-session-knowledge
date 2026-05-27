import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

beforeAll(() => {
  window.HTMLElement.prototype.scrollIntoView = vi.fn();
  // Provide a no-op localStorage stub so HostPicker doesn't throw in jsdom.
  const storage: Record<string, string> = {};
  Object.defineProperty(window, "localStorage", {
    value: {
      getItem: (k: string) => storage[k] ?? null,
      setItem: (k: string, v: string) => {
        storage[k] = v;
      },
      removeItem: (k: string) => {
        delete storage[k];
      },
      clear: () => {
        Object.keys(storage).forEach((k) => delete storage[k]);
      },
    },
    writable: true,
  });
});

afterEach(async () => {
  const navigation = await import("next/navigation");
  vi.mocked(navigation.usePathname).mockReturnValue(
    "/v2/chat" as ReturnType<typeof navigation.usePathname>
  );
});

vi.mock("next/navigation", () => ({
  useRouter: vi.fn(() => ({ push: vi.fn() })),
  useSearchParams: vi.fn(() => new URLSearchParams()),
  usePathname: vi.fn(() => "/v2/chat"),
}));

vi.mock("@/lib/api/hooks", () => ({
  useOperatorSessions: vi.fn(() => ({
    data: { sessions: [], count: 0 },
    isLoading: false,
    isError: false,
  })),
  useOperatorSession: vi.fn(() => ({
    data: null,
    isLoading: false,
    isError: false,
    refetch: vi.fn(),
  })),
  useOperatorRuns: vi.fn(() => ({
    data: { runs: [], count: 0 },
    isLoading: false,
    isError: false,
    refetch: vi.fn(),
  })),
  useOperatorActiveRuns: vi.fn(() => ({
    data: { runs: [], count: 0 },
    isLoading: false,
    isError: false,
    refetch: vi.fn(),
  })),
  useCreateOperatorSession: vi.fn(() => ({ mutate: vi.fn(), isPending: false })),
  useDeleteOperatorSession: vi.fn(() => ({ mutate: vi.fn(), isPending: false })),
  useSubmitPrompt: vi.fn(() => ({ mutate: vi.fn(), isPending: false })),
  useUpdateOperatorSession: vi.fn(() => ({ mutate: vi.fn(), isPending: false })),
  useAdoptCliSession: vi.fn(() => ({ mutate: vi.fn(), isPending: false })),
  useConfirmAdoptedSession: vi.fn(() => ({ mutate: vi.fn(), isPending: false })),
  useCliSessions: vi.fn(() => ({
    data: { sessions: [], count: 0, truncated: false },
    isLoading: false,
    isError: false,
    error: null,
  })),
  useSkillCatalog: vi.fn(() => ({
    data: null,
    isLoading: false,
    isError: false,
    error: null,
  })),
  usePathSuggest: vi.fn(() => ({ data: { suggestions: [], count: 0 } })),
  useOperatorModelCatalog: vi.fn(() => ({
    data: {
      models: [
        { id: "gpt-5.4", display_name: "GPT 5.4", provider: "OpenAI", default: true },
        { id: "claude-sonnet-4.6", display_name: "Claude Sonnet 4.6", provider: "Anthropic" },
      ],
      default_model: "gpt-5.4",
    },
    isLoading: false,
    isError: false,
  })),
  useFilePreview: vi.fn(() => ({ data: null, isLoading: false, isError: false })),
  useFileDiff: vi.fn(() => ({ data: null, isLoading: false, isError: false })),
  useHostCapabilities: vi.fn(() => ({
    data: null,
    isLoading: false,
    isError: false,
  })),
  useTentacleStatus: vi.fn(() => ({
    data: undefined,
    isLoading: false,
    isError: false,
  })),
  useOperatorUsage: vi.fn(() => ({
    data: null,
    isLoading: false,
    isError: false,
  })),
  useGenericPromptPreflight: vi.fn(() => ({
    mutateAsync: vi.fn(),
    mutate: vi.fn(),
    isPending: false,
  })),
  useHostMetrics: vi.fn(() => ({
    data: null,
    isLoading: false,
    isError: false,
  })),
  useUsageOverride: vi.fn(() => ({
    mutateAsync: vi.fn(),
    mutate: vi.fn(),
    isPending: false,
  })),
  createOperatorStreamPath: vi.fn(() => "/api/operator/sessions/x/stream?run=y"),
  createOperatorStreamUrl: vi.fn(
    (sessionId: string, runId: string, host: { base_url: string }) =>
      `${host.base_url}/api/operator/sessions/${sessionId}/stream?run=${runId}`
  ),
}));

// Mock the shared host-provider so tests that render SessionCreateDialog get a known host state.
import type { HostState } from "@/providers/host-provider";
import { LOCAL_HOST } from "@/lib/host-profiles";

let hostStateMock: HostState = {
  host: LOCAL_HOST,
  diagnosticsEnabled: true,
  localDiagnosticsEnabled: true,
};
vi.mock("@/providers/host-provider", () => ({
  useHostState: vi.fn(() => hostStateMock),
}));

// Mocked at module scope so individual tests can override the supported flag
// to assert the unsupported branch of the active-host telemetry chip. Default
// returns supported=true so unrelated features (e.g. cli_adopt) keep working.
const useHostFeatureMock = vi.fn((_host: unknown, _feature: string, _enabled?: boolean) => ({
  supported: true,
  loading: false,
}));
vi.mock("@/lib/hosts", () => ({
  useHostFeature: (host: unknown, feature: string, enabled?: boolean) =>
    useHostFeatureMock(host, feature, enabled),
}));

import { ChatShell } from "@/components/chat/chat-shell";
import { COPILOT_MODES } from "@/components/chat/session-create-dialog";

describe("ChatShell", () => {
  it("renders the chat shell container", () => {
    render(<ChatShell />);
    expect(screen.getByTestId("chat-shell")).toBeInTheDocument();
  });

  it("shows empty state when no session is selected", () => {
    render(<ChatShell />);
    expect(screen.getByText("No session selected")).toBeInTheDocument();
  });

  it("shows 'Chat Sessions' heading in the sidebar", () => {
    render(<ChatShell />);
    expect(screen.getByText("Chat Sessions")).toBeInTheDocument();
  });

  it("shows 'New Chat' button", () => {
    render(<ChatShell />);
    expect(screen.getByRole("button", { name: "New chat session" })).toBeInTheDocument();
  });

  it("shows mobile 'Open session list' button", () => {
    render(<ChatShell />);
    expect(screen.getByRole("button", { name: "Open session list" })).toBeInTheDocument();
  });

  it("keeps hosted root chat idle until a remote host is selected", async () => {
    const hooks = await import("@/lib/api/hooks");
    const navigation = await import("next/navigation");

    window.localStorage.clear();
    hostStateMock = {
      host: LOCAL_HOST,
      diagnosticsEnabled: false,
      localDiagnosticsEnabled: false,
    };
    vi.mocked(navigation.usePathname).mockReturnValue(
      "/chat" as ReturnType<typeof navigation.usePathname>
    );

    render(<ChatShell />);

    expect(vi.mocked(hooks.useOperatorSessions).mock.calls.at(-1)?.[1]).toBe(false);
    expect(screen.getByText(/No compatible host is configured/i)).toBeInTheDocument();
    // The "local" chip in the top-bar must not be shown when operator is unavailable on hosted pages.
    expect(screen.queryByTestId("local-host-chip")).not.toBeInTheDocument();
  });
});

describe("ChatShell — multi-run history", () => {
  it("renders multiple historical runs from useOperatorRuns", async () => {
    const hooks = await import("@/lib/api/hooks");
    const navigation = await import("next/navigation");

    vi.mocked(hooks.useOperatorRuns).mockReturnValue({
      data: {
        runs: [
          {
            id: "run-1",
            session_id: "session-1",
            prompt: "First prompt",
            status: "done",
            exit_code: 0,
            started_at: "2024-01-01T00:00:00Z",
            finished_at: "2024-01-01T00:01:00Z",
            events: [],
          },
          {
            id: "run-2",
            session_id: "session-1",
            prompt: "Second prompt",
            status: "done",
            exit_code: 0,
            started_at: "2024-01-01T00:02:00Z",
            finished_at: "2024-01-01T00:03:00Z",
            events: [],
          },
        ],
        count: 2,
      },
      isLoading: false,
      isError: false,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof hooks.useOperatorRuns>);

    vi.mocked(hooks.useOperatorSession).mockReturnValue({
      data: {
        id: "session-1",
        name: "My Session",
        model: "claude-sonnet-4.5",
        mode: "interactive",
        workspace: "/Users/user/projects/app",
        add_dirs: [],
        created_at: "2024-01-01T00:00:00Z",
        updated_at: "2024-01-01T00:03:00Z",
        run_count: 2,
        last_run_id: "run-2",
        resume_ready: true,
      },
      isLoading: false,
      isError: false,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof hooks.useOperatorSession>);

    vi.mocked(navigation.useSearchParams).mockReturnValue(
      new URLSearchParams("s=session-1") as ReturnType<typeof navigation.useSearchParams>
    );

    render(<ChatShell />);
    expect(screen.getByText("First prompt")).toBeInTheDocument();
    expect(screen.getByText("Second prompt")).toBeInTheDocument();
  });
});

describe("COPILOT_MODES — valid CLI mode values", () => {
  const VALID_MODES = new Set(["interactive", "plan", "autopilot"]);
  const INVALID_MODES = new Set(["default", "code", "edit", "agent"]);

  it("only contains valid Copilot CLI 1.0.40 mode values", () => {
    for (const mode of COPILOT_MODES) {
      expect(VALID_MODES.has(mode.value), `"${mode.value}" is not a valid CLI mode`).toBe(true);
    }
  });

  it("does not contain any invalid/removed mode values", () => {
    for (const mode of COPILOT_MODES) {
      expect(INVALID_MODES.has(mode.value), `"${mode.value}" is an invalid CLI mode`).toBe(false);
    }
  });

  it("includes all three required modes", () => {
    const values = COPILOT_MODES.map((m) => m.value);
    expect(values).toContain("interactive");
    expect(values).toContain("plan");
    expect(values).toContain("autopilot");
  });
});

describe("SessionCreateDialog — dynamic model input", () => {
  it("renders a text input for model selection (not a locked dropdown)", async () => {
    render(<ChatShell />);

    // Open the dialog
    fireEvent.click(screen.getByRole("button", { name: "New chat session" }));

    // The model field should be a text input, not a select
    const modelInput = screen.getByLabelText("Model");
    expect(modelInput.tagName).toBe("INPUT");
    expect((modelInput as HTMLInputElement).type).toBe("text");
  });

  it("model input has a datalist sourced from the operator model catalog", async () => {
    render(<ChatShell />);

    fireEvent.click(screen.getByRole("button", { name: "New chat session" }));

    const modelInput = screen.getByLabelText("Model") as HTMLInputElement;
    const listId = modelInput.getAttribute("list");
    expect(listId).toBeTruthy();

    const datalist = document.getElementById(listId!);
    expect(datalist).not.toBeNull();
    expect(datalist!.tagName).toBe("DATALIST");

    const options = datalist!.querySelectorAll("option");
    const values = Array.from(options).map((o) => o.getAttribute("value"));
    expect(values).toContain("gpt-5.4");
    expect(values).toContain("claude-sonnet-4.6");
  });

  it("model input accepts arbitrary freetext model identifiers", async () => {
    render(<ChatShell />);

    fireEvent.click(screen.getByRole("button", { name: "New chat session" }));

    const modelInput = screen.getByLabelText("Model") as HTMLInputElement;
    fireEvent.change(modelInput, { target: { value: "my-custom-model-v1" } });
    expect(modelInput.value).toBe("my-custom-model-v1");
  });
});

describe("WorkspacePicker — hidden-folder toggle", () => {
  it("renders a toggle button for showing hidden folders", () => {
    render(<ChatShell />);

    // Open the dialog to expose WorkspacePicker
    fireEvent.click(screen.getByRole("button", { name: "New chat session" }));

    const toggleBtn = screen.getByRole("button", { name: "Show hidden folders" });
    expect(toggleBtn).toBeInTheDocument();
    expect(toggleBtn).toHaveAttribute("aria-pressed", "false");
  });

  it("toggle button switches to 'Hide hidden folders' when activated", () => {
    render(<ChatShell />);

    fireEvent.click(screen.getByRole("button", { name: "New chat session" }));

    const toggleBtn = screen.getByRole("button", { name: "Show hidden folders" });
    fireEvent.click(toggleBtn);

    expect(screen.getByRole("button", { name: "Hide hidden folders" })).toBeInTheDocument();
  });
});

describe("SessionCreateDialog — host picker", () => {
  it("renders the Agent Host label and a host selector inside the dialog", () => {
    render(<ChatShell />);
    fireEvent.click(screen.getByRole("button", { name: "New chat session" }));

    expect(screen.getByText("Agent Host")).toBeInTheDocument();
    // The add-host button should be present
    expect(screen.getByRole("button", { name: "Add agent host" })).toBeInTheDocument();
  });

  it("shows the Add host form when the add-host button is clicked", () => {
    render(<ChatShell />);
    fireEvent.click(screen.getByRole("button", { name: "New chat session" }));
    fireEvent.click(screen.getByRole("button", { name: "Add agent host" }));

    const hostAddForm = screen.getByTestId("host-add-form");
    expect(hostAddForm).toBeInTheDocument();
    expect(hostAddForm.className).toContain("bg-card");
    expect(screen.getByLabelText("Tunnel URL")).toBeInTheDocument();
  });

  it("Save host button is disabled when the URL field is empty", () => {
    render(<ChatShell />);
    fireEvent.click(screen.getByRole("button", { name: "New chat session" }));
    fireEvent.click(screen.getByRole("button", { name: "Add agent host" }));

    const saveBtn = screen.getByRole("button", { name: "Save host" });
    expect(saveBtn).toBeDisabled();
  });

  it("Save host button is enabled after a URL is entered", () => {
    render(<ChatShell />);
    fireEvent.click(screen.getByRole("button", { name: "New chat session" }));
    fireEvent.click(screen.getByRole("button", { name: "Add agent host" }));

    fireEvent.change(screen.getByLabelText("Tunnel URL"), {
      target: { value: "https://abc123.ngrok.io" },
    });

    const saveBtn = screen.getByRole("button", { name: "Save host" });
    expect(saveBtn).not.toBeDisabled();
  });
});

describe("HostPicker — local-only note on hosted page", () => {
  beforeEach(() => {
    Object.defineProperty(window, "location", {
      value: { ...window.location, origin: "https://agents.example.com" },
      configurable: true,
    });
  });
  afterEach(() => {
    Object.defineProperty(window, "location", {
      value: { ...window.location, origin: "http://localhost:3000" },
      configurable: true,
    });
  });

  it("shows local-only callout in HostPicker when local is selected on a hosted page", () => {
    hostStateMock = {
      host: LOCAL_HOST,
      diagnosticsEnabled: false,
      localDiagnosticsEnabled: false,
    };
    render(<ChatShell />);
    fireEvent.click(screen.getByRole("button", { name: "New chat session" }));
    expect(screen.getByTestId("local-hosted-note")).toBeInTheDocument();
  });

  it("shows hosted-aware Agent Host hint text on hosted pages", () => {
    hostStateMock = {
      host: LOCAL_HOST,
      diagnosticsEnabled: false,
      localDiagnosticsEnabled: false,
    };
    render(<ChatShell />);
    fireEvent.click(screen.getByRole("button", { name: "New chat session" }));
    expect(
      screen.getByText(/Open the local browse app directly.*HTTPS tunnel/i)
    ).toBeInTheDocument();
  });
});

describe("ChatShell — host URL in top-bar", () => {
  it("shows 'CLI Chat' placeholder when no session is selected", async () => {
    // Reset useOperatorSession to return no session (may be overridden by earlier tests)
    const hooks = await import("@/lib/api/hooks");
    vi.mocked(hooks.useOperatorSession).mockReturnValue({
      data: null,
      isLoading: false,
      isError: false,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof hooks.useOperatorSession>);

    const navigation = await import("next/navigation");
    vi.mocked(navigation.useSearchParams).mockReturnValue(
      new URLSearchParams() as ReturnType<typeof navigation.useSearchParams>
    );

    render(<ChatShell />);
    expect(screen.getByText("CLI Chat")).toBeInTheDocument();
  });
});

describe("ChatShell — remote host passed to Transcript", () => {
  it("shows remote host badge in top-bar when h= param is set and no session is active", async () => {
    const hooks = await import("@/lib/api/hooks");
    const navigation = await import("next/navigation");

    // Save a remote host profile so getAllHostProfiles() finds it
    const { saveHostProfile } = await import("@/lib/host-profiles");
    saveHostProfile({
      id: "remote-h2",
      label: "Dev Tunnel",
      base_url: "https://dev2.ngrok.io",
      token: "tok",
      cli_kind: "copilot",
      is_default: false,
    });

    // No session selected — just the remote host param
    vi.mocked(navigation.useSearchParams).mockReturnValue(
      new URLSearchParams("h=remote-h2") as ReturnType<typeof navigation.useSearchParams>
    );

    vi.mocked(hooks.useOperatorSession).mockReturnValue({
      data: null,
      isLoading: false,
      isError: false,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof hooks.useOperatorSession>);

    render(<ChatShell />);

    // The top-bar should display the remote host base_url as a badge title
    expect(screen.getByTitle("https://dev2.ngrok.io")).toBeInTheDocument();
  });
});

describe("ChatShell — shared selected-host reuse", () => {
  it("shows remote host badge from the shared selected host when no h= URL param", async () => {
    const hooks = await import("@/lib/api/hooks");
    const navigation = await import("next/navigation");
    const { saveHostProfile } = await import("@/lib/host-profiles");

    const sharedHost = {
      id: "persisted-remote-h1",
      label: "Persisted Tunnel",
      base_url: "https://persisted.ngrok.io",
      token: "tok-p",
      cli_kind: "copilot" as const,
      is_default: false,
    };
    saveHostProfile(sharedHost);
    hostStateMock = { host: sharedHost, diagnosticsEnabled: true };

    // No h= param in URL — ChatShell must fall back to the shared selected host.
    vi.mocked(navigation.useSearchParams).mockReturnValue(
      new URLSearchParams() as ReturnType<typeof navigation.useSearchParams>
    );

    vi.mocked(hooks.useOperatorSession).mockReturnValue({
      data: null,
      isLoading: false,
      isError: false,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof hooks.useOperatorSession>);

    render(<ChatShell />);

    // Top-bar must show the selected remote host as a badge even without a URL param.
    expect(screen.getByTitle(sharedHost.base_url)).toBeInTheDocument();
  });

  it("h= URL param takes precedence over the shared selected host", async () => {
    const hooks = await import("@/lib/api/hooks");
    const navigation = await import("next/navigation");
    const { saveHostProfile } = await import("@/lib/host-profiles");

    saveHostProfile({
      id: "remote-h2",
      label: "Tunnel 2",
      base_url: "https://dev2.ngrok.io",
      token: "tok-2",
      cli_kind: "copilot",
      is_default: false,
    });

    // Persist a different host
    saveHostProfile({
      id: "persisted-other",
      label: "Other Tunnel",
      base_url: "https://other.ngrok.io",
      token: "",
      cli_kind: "copilot",
      is_default: false,
    });
    hostStateMock = {
      host: {
        id: "persisted-other",
        label: "Other Tunnel",
        base_url: "https://other.ngrok.io",
        token: "",
        cli_kind: "copilot",
        is_default: false,
      },
      diagnosticsEnabled: true,
    };

    // But URL has a specific h= param pointing to remote-h2 (saved earlier)
    vi.mocked(navigation.useSearchParams).mockReturnValue(
      new URLSearchParams("h=remote-h2") as ReturnType<typeof navigation.useSearchParams>
    );

    vi.mocked(hooks.useOperatorSession).mockReturnValue({
      data: null,
      isLoading: false,
      isError: false,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof hooks.useOperatorSession>);

    render(<ChatShell />);

    // h= param (remote-h2 = dev2.ngrok.io) wins over persisted (other.ngrok.io)
    expect(screen.getByTitle("https://dev2.ngrok.io")).toBeInTheDocument();
    expect(screen.queryByTitle("https://other.ngrok.io")).not.toBeInTheDocument();
  });
});

describe("ChatShell — shared host selection", () => {
  it("uses the shared browse-wide host state when no h= URL param and updates on rerender", async () => {
    const hooks = await import("@/lib/api/hooks");
    const navigation = await import("next/navigation");
    const remoteHost = {
      id: "header-selected-remote",
      label: "Header Tunnel",
      base_url: "https://header.ngrok.io",
      token: "tok-header",
      cli_kind: "copilot" as const,
      is_default: false,
    };

    hostStateMock = { host: LOCAL_HOST, diagnosticsEnabled: true };
    vi.mocked(navigation.useSearchParams).mockReturnValue(
      new URLSearchParams() as ReturnType<typeof navigation.useSearchParams>
    );
    vi.mocked(hooks.useOperatorSession).mockReturnValue({
      data: null,
      isLoading: false,
      isError: false,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof hooks.useOperatorSession>);

    const { rerender } = render(<ChatShell />);

    expect(vi.mocked(hooks.useOperatorSessions).mock.calls.at(-1)?.[0]).toMatchObject({
      id: LOCAL_HOST.id,
    });

    hostStateMock = { host: remoteHost, diagnosticsEnabled: true };
    rerender(<ChatShell />);

    expect(vi.mocked(hooks.useOperatorSessions).mock.calls.at(-1)?.[0]).toMatchObject({
      id: remoteHost.id,
      base_url: remoteHost.base_url,
    });
    expect(screen.getByTitle(remoteHost.base_url)).toBeInTheDocument();
  });
});

// ─── Composer — file attachment UX ──────────────────────────────────────────

/**
 * Helper: set up a session so the Composer is rendered inside ChatShell.
 */
async function setupActiveSession() {
  const hooks = await import("@/lib/api/hooks");
  const navigation = await import("next/navigation");

  vi.mocked(hooks.useOperatorSession).mockReturnValue({
    data: {
      id: "session-1",
      name: "Test Session",
      model: "gpt-5.4",
      mode: "interactive",
      workspace: "/projects/test",
      add_dirs: [],
      created_at: "2024-01-01T00:00:00Z",
      updated_at: "2024-01-01T00:00:00Z",
      run_count: 0,
      last_run_id: null,
      resume_ready: true,
    },
    isLoading: false,
    isError: false,
    refetch: vi.fn(),
  } as unknown as ReturnType<typeof hooks.useOperatorSession>);

  vi.mocked(hooks.useOperatorRuns).mockReturnValue({
    data: { runs: [], count: 0 },
    isLoading: false,
    isError: false,
    refetch: vi.fn(),
  } as unknown as ReturnType<typeof hooks.useOperatorRuns>);

  vi.mocked(navigation.useSearchParams).mockReturnValue(
    new URLSearchParams("s=session-1") as ReturnType<typeof navigation.useSearchParams>
  );
}

describe("Composer — file attachment button", () => {
  it("renders the Attach files button when a session is active", async () => {
    await setupActiveSession();
    render(<ChatShell />);
    expect(screen.getByRole("button", { name: "Attach files" })).toBeInTheDocument();
  });

  it("shows a queued file chip after a file is selected", async () => {
    // FileReader is async — mock it to resolve synchronously
    const originalFileReader = global.FileReader;
    class MockFileReader {
      onload: (() => void) | null = null;
      onerror: (() => void) | null = null;
      result = "data:text/plain;base64,aGVsbG8=";
      readAsDataURL() {
        setTimeout(() => this.onload?.(), 0);
      }
    }
    global.FileReader = MockFileReader as unknown as typeof FileReader;

    await setupActiveSession();
    render(<ChatShell />);

    const fileInput = document.querySelector('input[type="file"]') as HTMLInputElement;
    expect(fileInput).not.toBeNull();

    const file = new File(["hello"], "notes.txt", { type: "text/plain" });
    Object.defineProperty(fileInput, "files", { value: [file], configurable: true });
    fireEvent.change(fileInput);

    await waitFor(() => {
      expect(screen.getByText("notes.txt")).toBeInTheDocument();
    });

    global.FileReader = originalFileReader;
  });

  it("removes a queued file chip when the remove button is clicked", async () => {
    const originalFileReader = global.FileReader;
    class MockFileReader {
      onload: (() => void) | null = null;
      onerror: (() => void) | null = null;
      result = "data:text/plain;base64,aGVsbG8=";
      readAsDataURL() {
        setTimeout(() => this.onload?.(), 0);
      }
    }
    global.FileReader = MockFileReader as unknown as typeof FileReader;

    await setupActiveSession();
    render(<ChatShell />);

    const fileInput = document.querySelector('input[type="file"]') as HTMLInputElement;
    const file = new File(["hello"], "remove-me.txt", { type: "text/plain" });
    Object.defineProperty(fileInput, "files", { value: [file], configurable: true });
    fireEvent.change(fileInput);

    await waitFor(() => {
      expect(screen.getByText("remove-me.txt")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole("button", { name: "Remove remove-me.txt" }));
    expect(screen.queryByText("remove-me.txt")).not.toBeInTheDocument();

    global.FileReader = originalFileReader;
  });
});

describe("ChatShell — run history with file attachments", () => {
  it("renders file chips in a historical run's user bubble when files are present", async () => {
    const hooks = await import("@/lib/api/hooks");
    const navigation = await import("next/navigation");

    vi.mocked(hooks.useOperatorRuns).mockReturnValue({
      data: {
        runs: [
          {
            id: "run-with-files",
            session_id: "session-files",
            prompt: "Analyze these files",
            status: "done",
            exit_code: 0,
            started_at: "2024-01-01T00:00:00Z",
            finished_at: "2024-01-01T00:01:00Z",
            events: [],
            files: [
              { name: "report.pdf", type: "application/pdf", size: 204800 },
              { name: "data.csv", type: "text/csv", size: 1024 },
            ],
          },
        ],
        count: 1,
      },
      isLoading: false,
      isError: false,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof hooks.useOperatorRuns>);

    vi.mocked(hooks.useOperatorSession).mockReturnValue({
      data: {
        id: "session-files",
        name: "Files Session",
        model: "gpt-5.4",
        mode: "interactive",
        workspace: "/projects",
        add_dirs: [],
        created_at: "2024-01-01T00:00:00Z",
        updated_at: "2024-01-01T00:01:00Z",
        run_count: 1,
        last_run_id: "run-with-files",
        resume_ready: true,
      },
      isLoading: false,
      isError: false,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof hooks.useOperatorSession>);

    vi.mocked(navigation.useSearchParams).mockReturnValue(
      new URLSearchParams("s=session-files") as ReturnType<typeof navigation.useSearchParams>
    );

    render(<ChatShell />);

    // Prompt text should appear
    expect(screen.getByText("Analyze these files")).toBeInTheDocument();
    // File chips should appear
    expect(screen.getByText("report.pdf")).toBeInTheDocument();
    expect(screen.getByText("data.csv")).toBeInTheDocument();
  });
});

describe("SessionCreateDialog — pre-populated from active host", () => {
  it("pre-populates host from the active host when dialog opens with LOCAL_HOST active", async () => {
    hostStateMock = { host: LOCAL_HOST, diagnosticsEnabled: true };

    render(<ChatShell />);
    fireEvent.click(screen.getByRole("button", { name: "New chat session" }));

    // The host selector is present and pre-populated (LOCAL_HOST is the active host)
    expect(screen.getByLabelText("Agent host")).toBeInTheDocument();
  });

  it("pre-populates host from the active host when a remote host is active", async () => {
    const { saveHostProfile } = await import("@/lib/host-profiles");
    const remoteHost = {
      id: "pre-pop-host",
      label: "Pre-pop Tunnel",
      base_url: "https://prepop.ngrok.io",
      token: "",
      cli_kind: "copilot" as const,
      is_default: false,
    };
    saveHostProfile(remoteHost);
    hostStateMock = { host: remoteHost, diagnosticsEnabled: true };

    render(<ChatShell />);
    fireEvent.click(screen.getByRole("button", { name: "New chat session" }));

    // HostPicker should be rendered with the remote host selected
    const hostSelect = screen.getByLabelText("Agent host");
    expect(hostSelect).toBeInTheDocument();
  });

  it("uses the URL-resolved active host when h= overrides the shared selected host", async () => {
    const hooks = await import("@/lib/api/hooks");
    const navigation = await import("next/navigation");
    const { saveHostProfile } = await import("@/lib/host-profiles");

    const routeHost = {
      id: "route-host",
      label: "Route Tunnel",
      base_url: "https://route.ngrok.io",
      token: "tok-route",
      cli_kind: "copilot" as const,
      is_default: false,
    };

    saveHostProfile(routeHost);
    hostStateMock = { host: LOCAL_HOST, diagnosticsEnabled: true };
    vi.mocked(navigation.useSearchParams).mockReturnValue(
      new URLSearchParams("h=route-host") as ReturnType<typeof navigation.useSearchParams>
    );

    render(<ChatShell />);
    fireEvent.click(screen.getByRole("button", { name: "New chat session" }));

    expect(vi.mocked(hooks.useCreateOperatorSession).mock.calls.at(-1)?.[0]).toMatchObject({
      id: routeHost.id,
      base_url: routeHost.base_url,
    });
    expect(vi.mocked(hooks.useOperatorModelCatalog).mock.calls.at(-1)?.[0]).toMatchObject({
      id: routeHost.id,
      base_url: routeHost.base_url,
    });
  });
});

describe("ChatShell — hosted-root idle behavior", () => {
  it("shows idle guidance when on hosted root with no remote host", async () => {
    const navigation = await import("next/navigation");
    vi.mocked(navigation.usePathname).mockReturnValue(
      "/chat" as ReturnType<typeof navigation.usePathname>
    );
    hostStateMock = { host: LOCAL_HOST, diagnosticsEnabled: false };

    render(<ChatShell />);
    expect(screen.getByTestId("chat-shell")).toBeInTheDocument();
  });
});

// ─── Session edit — metadata bar wiring ──────────────────────────────────────

describe("ChatShell — session edit wiring", () => {
  async function setupEditableSession() {
    const hooks = await import("@/lib/api/hooks");
    const navigation = await import("next/navigation");

    vi.mocked(hooks.useOperatorSession).mockReturnValue({
      data: {
        id: "edit-sess",
        name: "Editable Session",
        model: "claude-sonnet-4.6",
        mode: "interactive",
        workspace: "/projects/edit",
        add_dirs: [],
        created_at: "2024-01-01T00:00:00Z",
        updated_at: "2024-01-01T00:00:00Z",
        run_count: 1,
        last_run_id: "run-x",
        resume_ready: true,
      },
      isLoading: false,
      isError: false,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof hooks.useOperatorSession>);

    vi.mocked(hooks.useOperatorRuns).mockReturnValue({
      data: { runs: [], count: 0 },
      isLoading: false,
      isError: false,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof hooks.useOperatorRuns>);

    // Ensure no active run / pending submission leaks from prior tests
    vi.mocked(hooks.useSubmitPrompt).mockReturnValue({
      mutate: vi.fn(),
      isPending: false,
    } as unknown as ReturnType<typeof hooks.useSubmitPrompt>);

    vi.mocked(hooks.useUpdateOperatorSession).mockReturnValue({
      mutate: vi.fn(),
      isPending: false,
    } as unknown as ReturnType<typeof hooks.useUpdateOperatorSession>);

    vi.mocked(navigation.useSearchParams).mockReturnValue(
      new URLSearchParams("s=edit-sess") as ReturnType<typeof navigation.useSearchParams>
    );
  }

  it("renders the edit-session button in the metadata bar when a session is active", async () => {
    await setupEditableSession();
    render(<ChatShell />);
    expect(screen.getByTestId("edit-session-btn")).toBeInTheDocument();
  });

  it("edit button is disabled while an active run is in progress", async () => {
    const hooks = await import("@/lib/api/hooks");
    await setupEditableSession();

    // Simulate an active run by making the promptMutation pending
    vi.mocked(hooks.useSubmitPrompt).mockReturnValue({
      mutate: vi.fn(),
      isPending: true,
    } as unknown as ReturnType<typeof hooks.useSubmitPrompt>);

    render(<ChatShell />);
    const editBtn = screen.getByTestId("edit-session-btn");
    expect(editBtn).toBeDisabled();
  });

  it("calls useUpdateOperatorSession mutate with the payload on save", async () => {
    const hooks = await import("@/lib/api/hooks");
    const mutateMock = vi.fn();
    await setupEditableSession();

    vi.mocked(hooks.useUpdateOperatorSession).mockReturnValue({
      mutate: mutateMock,
      isPending: false,
    } as unknown as ReturnType<typeof hooks.useUpdateOperatorSession>);

    render(<ChatShell />);

    // Open the popover
    fireEvent.click(screen.getByTestId("edit-session-btn"));

    // Change the name field (wait for popover to render via portal)
    const nameInput = await screen.findByPlaceholderText("Session name");
    fireEvent.change(nameInput, { target: { value: "New Name" } });

    // Submit
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    expect(mutateMock).toHaveBeenCalledWith(
      expect.objectContaining({ payload: expect.objectContaining({ name: "New Name" }) })
    );
  });
});

// ─── Slash command integration ────────────────────────────────────────────────

describe("ChatShell — slash command integration", () => {
  async function setupActiveSession() {
    const hooks = await import("@/lib/api/hooks");
    const navigation = await import("next/navigation");

    vi.mocked(hooks.useOperatorSession).mockReturnValue({
      data: {
        id: "slash-sess",
        name: "Slash Session",
        model: "gpt-5.4",
        mode: "interactive",
        workspace: "/projects/slash",
        add_dirs: [],
        created_at: "2024-01-01T00:00:00Z",
        updated_at: "2024-01-01T00:00:00Z",
        run_count: 2,
        last_run_id: "run-s",
        resume_ready: false,
      },
      isLoading: false,
      isError: false,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof hooks.useOperatorSession>);

    vi.mocked(hooks.useOperatorRuns).mockReturnValue({
      data: { runs: [], count: 0 },
      isLoading: false,
      isError: false,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof hooks.useOperatorRuns>);

    vi.mocked(hooks.useSubmitPrompt).mockReturnValue({
      mutate: vi.fn(),
      isPending: false,
    } as unknown as ReturnType<typeof hooks.useSubmitPrompt>);

    vi.mocked(hooks.useUpdateOperatorSession).mockReturnValue({
      mutate: vi.fn(),
      isPending: false,
    } as unknown as ReturnType<typeof hooks.useUpdateOperatorSession>);

    vi.mocked(navigation.useSearchParams).mockReturnValue(
      new URLSearchParams("s=slash-sess") as ReturnType<typeof navigation.useSearchParams>
    );

    hostStateMock = { host: LOCAL_HOST, diagnosticsEnabled: true };
  }

  it("renders the Composer with the prompt textarea when a session is active", async () => {
    await setupActiveSession();
    render(<ChatShell />);
    expect(screen.getByRole("textbox", { name: "Prompt" })).toBeInTheDocument();
  });

  it("opens the help dialog when /help is submitted", async () => {
    await setupActiveSession();
    render(<ChatShell />);

    const ta = screen.getByRole("textbox", { name: "Prompt" });
    fireEvent.change(ta, { target: { value: "/help" } });
    fireEvent.submit(ta.closest("form")!);

    expect(await screen.findByText(/Slash Commands/i)).toBeInTheDocument();
    expect(await screen.findByText(/Supported in Browse Chat/i)).toBeInTheDocument();
  });

  it("opens the skills dialog when /skills is submitted", async () => {
    await setupActiveSession();
    render(<ChatShell />);

    const ta = screen.getByRole("textbox", { name: "Prompt" });
    fireEvent.change(ta, { target: { value: "/skills" } });
    fireEvent.submit(ta.closest("form")!);

    expect(await screen.findByText(/Installed Skills/i)).toBeInTheDocument();
  });

  it("shows installed skills from useSkillCatalog in the skills dialog", async () => {
    const hooks = await import("@/lib/api/hooks");
    await setupActiveSession();

    vi.mocked(hooks.useSkillCatalog).mockReturnValue({
      data: {
        skills: [
          {
            id: "skill-1",
            name: "Code Review",
            description: "Reviews code changes",
            source_path: "/global/skills/code-review.md",
            source_kind: "global" as const,
            status: "installed" as const,
          },
        ],
        total: 1,
        sources: { global: "/global/skills", project: null },
        runtime: { generated_at: "2024-01-01T00:00:00Z" },
      },
      isLoading: false,
      isError: false,
      error: null,
    } as unknown as ReturnType<typeof hooks.useSkillCatalog>);

    render(<ChatShell />);

    const ta = screen.getByRole("textbox", { name: "Prompt" });
    fireEvent.change(ta, { target: { value: "/skills" } });
    fireEvent.submit(ta.closest("form")!);

    expect(await screen.findByTestId("skills-list")).toBeInTheDocument();
    expect(screen.getByText("Code Review")).toBeInTheDocument();
  });

  it("shows backend upgrade guidance when the host lacks the skills catalog endpoint", async () => {
    const hooks = await import("@/lib/api/hooks");
    await setupActiveSession();

    vi.mocked(hooks.useSkillCatalog).mockReturnValue({
      data: null,
      isLoading: false,
      isError: true,
      error: new Error("API 404: Not Found"),
    } as unknown as ReturnType<typeof hooks.useSkillCatalog>);

    render(<ChatShell />);

    const ta = screen.getByRole("textbox", { name: "Prompt" });
    fireEvent.change(ta, { target: { value: "/skills" } });
    fireEvent.submit(ta.closest("form")!);

    expect(
      await screen.findByText(/does not support the installed skills catalog yet/i)
    ).toBeInTheDocument();
    expect(screen.getByText(/Update the browse backend running on/i)).toBeInTheDocument();
  });

  it("opens the session editor when /session is submitted", async () => {
    await setupActiveSession();
    render(<ChatShell />);

    const ta = screen.getByRole("textbox", { name: "Prompt" });
    fireEvent.change(ta, { target: { value: "/session" } });
    fireEvent.submit(ta.closest("form")!);

    expect(await screen.findByPlaceholderText("Session name")).toBeInTheDocument();
  });

  it("calls updateMutation with mode payload for /mode interactive", async () => {
    const hooks = await import("@/lib/api/hooks");
    const mutateMock = vi.fn();
    await setupActiveSession();

    vi.mocked(hooks.useUpdateOperatorSession).mockReturnValue({
      mutate: mutateMock,
      isPending: false,
    } as unknown as ReturnType<typeof hooks.useUpdateOperatorSession>);

    render(<ChatShell />);

    const ta = screen.getByRole("textbox", { name: "Prompt" });
    fireEvent.change(ta, { target: { value: "/mode interactive" } });
    fireEvent.submit(ta.closest("form")!);

    expect(mutateMock).toHaveBeenCalledWith(
      expect.objectContaining({ payload: expect.objectContaining({ mode: "interactive" }) })
    );
  });

  it("does not call updateMutation for /mode with invalid value", async () => {
    const hooks = await import("@/lib/api/hooks");
    const mutateMock = vi.fn();
    await setupActiveSession();

    vi.mocked(hooks.useUpdateOperatorSession).mockReturnValue({
      mutate: mutateMock,
      isPending: false,
    } as unknown as ReturnType<typeof hooks.useUpdateOperatorSession>);

    render(<ChatShell />);

    const ta = screen.getByRole("textbox", { name: "Prompt" });
    fireEvent.change(ta, { target: { value: "/mode invalid-mode" } });
    fireEvent.submit(ta.closest("form")!);

    expect(mutateMock).not.toHaveBeenCalled();
    expect(await screen.findByText("Invalid /mode command")).toBeInTheDocument();
    expect(
      screen.getByText(/Use \/mode interactive, \/mode plan, or \/mode autopilot/i)
    ).toBeInTheDocument();
  });

  it("disables the composer while a session update is pending", async () => {
    const hooks = await import("@/lib/api/hooks");
    await setupActiveSession();

    vi.mocked(hooks.useUpdateOperatorSession).mockReturnValue({
      mutate: vi.fn(),
      isPending: true,
    } as unknown as ReturnType<typeof hooks.useUpdateOperatorSession>);

    render(<ChatShell />);

    expect(screen.getByRole("textbox", { name: "Prompt" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Send prompt" })).toBeDisabled();
  });

  it("does not intercept path-like prompts", async () => {
    const hooks = await import("@/lib/api/hooks");
    const submitMock = vi.fn();
    await setupActiveSession();

    vi.mocked(hooks.useSubmitPrompt).mockReturnValue({
      mutate: submitMock,
      isPending: false,
    } as unknown as ReturnType<typeof hooks.useSubmitPrompt>);

    render(<ChatShell />);

    const ta = screen.getByRole("textbox", { name: "Prompt" });
    fireEvent.change(ta, { target: { value: "/Users/linhn/project" } });
    fireEvent.submit(ta.closest("form")!);

    // Should go to the server as a regular prompt, not be intercepted, and
    // must forward the active host id so the server can group usage entries
    // per-host (#556).
    expect(submitMock).toHaveBeenCalledWith(
      expect.objectContaining({
        prompt: "/Users/linhn/project",
        host_id: LOCAL_HOST.id,
      }),
      expect.anything()
    );
  });
});

// ─── CLI Adopt/Confirm — security & UX ───────────────────────────────────────

describe("ChatShell — CLI adopt security: no CLI UUID in URL", () => {
  it("does not put CLI UUID in router.push when adopting a session", async () => {
    const hooks = await import("@/lib/api/hooks");
    const navigation = await import("next/navigation");

    const pushMock = vi.fn();
    vi.mocked(navigation.useRouter).mockReturnValue({
      push: pushMock,
      back: vi.fn(),
      forward: vi.fn(),
      refresh: vi.fn(),
      replace: vi.fn(),
      prefetch: vi.fn(),
    } as unknown as ReturnType<typeof navigation.useRouter>);

    // adoptMutation.mutate calls onSuccess with the operator session (not CLI UUID)
    const operatorSession = {
      id: "operator-uuid-abc123",
      name: "My CLI Session",
      model: "gpt-5.4",
      mode: "interactive",
      workspace: "/projects/cli",
      add_dirs: [],
      created_at: "2024-01-01T00:00:00Z",
      updated_at: "2024-01-01T00:00:00Z",
      run_count: 0,
      last_run_id: null,
      resume_ready: false,
      source: "cli_adopt",
      confirmed_at: null,
    };

    vi.mocked(hooks.useAdoptCliSession).mockReturnValue({
      mutate: vi.fn((_args, callbacks) => {
        // Simulate the mutation succeeding with the operator session.
        // SECURITY: the CLI UUID 'cli-secret-uuid' must NOT appear in pushMock calls.
        callbacks?.onSuccess?.(operatorSession, _args, undefined);
      }),
      isPending: false,
    } as unknown as ReturnType<typeof hooks.useAdoptCliSession>);

    // Provide a non-empty CLI session list so the session item is rendered
    // and can be clicked. The test was previously vacuous because it checked
    // push calls that never happened (empty list → no item to click → no adopt).
    vi.mocked(hooks.useCliSessions).mockReturnValue({
      data: {
        sessions: [
          {
            cli_session_id: "cli-secret-uuid",
            title: "My CLI Session",
            mtime: "2024-01-01T00:00:00Z",
            workspace_hint: "/projects/cli",
            branch: null,
            repository: null,
          },
        ],
        count: 1,
        truncated: false,
      },
      isLoading: false,
      isError: false,
      error: null,
    } as unknown as ReturnType<typeof hooks.useCliSessions>);

    vi.mocked(navigation.useSearchParams).mockReturnValue(
      new URLSearchParams() as ReturnType<typeof navigation.useSearchParams>
    );
    hostStateMock = { host: LOCAL_HOST, diagnosticsEnabled: true, localDiagnosticsEnabled: true };

    render(<ChatShell />);

    // Open the dialog and switch to CLI History tab
    fireEvent.click(screen.getByRole("button", { name: "New chat session" }));
    fireEvent.click(screen.getByTestId("cli-history-tab"));

    // Click the rendered CLI session item to trigger the adopt flow
    const item = await screen.findByTestId("cli-session-item");
    fireEvent.click(item);

    // router.push must have been called (adopt mutation calls onSuccess → navigate)
    expect(pushMock).toHaveBeenCalled();

    // SECURITY: All push calls must use the operator session id only.
    // The CLI UUID and the field name 'resume_target' must never appear.
    for (const call of pushMock.mock.calls) {
      const url = String(call[0]);
      expect(url).toContain("operator-uuid-abc123");
      expect(url).not.toContain("cli-secret-uuid");
      expect(url).not.toContain("resume_target");
    }
  });

  it("does not write CLI UUID to localStorage", async () => {
    const navigation = await import("next/navigation");
    vi.mocked(navigation.useSearchParams).mockReturnValue(
      new URLSearchParams() as ReturnType<typeof navigation.useSearchParams>
    );
    hostStateMock = { host: LOCAL_HOST, diagnosticsEnabled: true, localDiagnosticsEnabled: true };

    const setItemSpy = vi.spyOn(window.localStorage, "setItem");

    render(<ChatShell />);
    fireEvent.click(screen.getByRole("button", { name: "New chat session" }));

    // Check that no localStorage.setItem call contains a CLI UUID pattern
    for (const call of setItemSpy.mock.calls) {
      // cli_session_id values would typically be a UUID pattern
      // Our security constraint: CLI UUID should never be stored.
      const value = call[1];
      expect(value).not.toMatch(/cli-session-id/);
    }

    setItemSpy.mockRestore();
  });
});

describe("ChatShell — unconfirmed CLI adoption: composer gating", () => {
  async function setupUnconfirmedCliSession() {
    const hooks = await import("@/lib/api/hooks");
    const navigation = await import("next/navigation");

    vi.mocked(hooks.useOperatorSession).mockReturnValue({
      data: {
        id: "adopted-session-1",
        name: "My CLI Session",
        model: "gpt-5.4",
        mode: "interactive",
        workspace: "/projects/cli",
        add_dirs: [],
        created_at: "2024-01-01T00:00:00Z",
        updated_at: "2024-01-01T00:00:00Z",
        run_count: 0,
        last_run_id: null,
        resume_ready: false,
        source: "cli_adopt",
        resume_target: null, // SECURITY: never rendered
        confirmed_at: null, // not yet confirmed
      },
      isLoading: false,
      isError: false,
      isSuccess: true,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof hooks.useOperatorSession>);

    vi.mocked(hooks.useOperatorRuns).mockReturnValue({
      data: { runs: [], count: 0 },
      isLoading: false,
      isError: false,
      isFetchedAfterMount: true,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof hooks.useOperatorRuns>);

    vi.mocked(hooks.useSubmitPrompt).mockReturnValue({
      mutate: vi.fn(),
      isPending: false,
    } as unknown as ReturnType<typeof hooks.useSubmitPrompt>);

    vi.mocked(hooks.useUpdateOperatorSession).mockReturnValue({
      mutate: vi.fn(),
      isPending: false,
    } as unknown as ReturnType<typeof hooks.useUpdateOperatorSession>);

    vi.mocked(hooks.useConfirmAdoptedSession).mockReturnValue({
      mutate: vi.fn(),
      isPending: false,
    } as unknown as ReturnType<typeof hooks.useConfirmAdoptedSession>);

    vi.mocked(navigation.useSearchParams).mockReturnValue(
      new URLSearchParams("s=adopted-session-1") as ReturnType<typeof navigation.useSearchParams>
    );

    hostStateMock = { host: LOCAL_HOST, diagnosticsEnabled: true, localDiagnosticsEnabled: true };
  }

  it("shows the confirmation panel when session is unconfirmed CLI adoption", async () => {
    await setupUnconfirmedCliSession();
    render(<ChatShell />);
    expect(screen.getByTestId("confirm-adoption-panel")).toBeInTheDocument();
    expect(screen.getByText(/Resume CLI session/i)).toBeInTheDocument();
  });

  it("disables the composer textarea while session is unconfirmed", async () => {
    await setupUnconfirmedCliSession();
    render(<ChatShell />);
    const textarea = screen.getByRole("textbox", { name: "Prompt" });
    expect(textarea).toBeDisabled();
  });

  it("disables the send button while session is unconfirmed", async () => {
    await setupUnconfirmedCliSession();
    render(<ChatShell />);
    expect(screen.getByRole("button", { name: "Send prompt" })).toBeDisabled();
  });

  it("shows the Confirm & Resume button in the confirmation panel", async () => {
    await setupUnconfirmedCliSession();
    render(<ChatShell />);
    expect(screen.getByTestId("confirm-adoption-btn")).toBeInTheDocument();
    expect(screen.getByTestId("confirm-adoption-btn")).not.toBeDisabled();
  });

  it("calls confirmMutation.mutate when Confirm & Resume is clicked", async () => {
    const hooks = await import("@/lib/api/hooks");
    const confirmMutateMock = vi.fn();
    await setupUnconfirmedCliSession();

    vi.mocked(hooks.useConfirmAdoptedSession).mockReturnValue({
      mutate: confirmMutateMock,
      isPending: false,
    } as unknown as ReturnType<typeof hooks.useConfirmAdoptedSession>);

    render(<ChatShell />);
    fireEvent.click(screen.getByTestId("confirm-adoption-btn"));
    expect(confirmMutateMock).toHaveBeenCalled();
  });

  it("does not render resume_target anywhere in the DOM", async () => {
    await setupUnconfirmedCliSession();
    render(<ChatShell />);
    // resume_target is null here but even if set, it must not be rendered
    const { container } = render(<ChatShell />);
    // Verify no element shows 'resume_target' as text
    expect(container.innerHTML).not.toContain("resume_target");
  });

  it("shows workspace in the confirmation panel", async () => {
    await setupUnconfirmedCliSession();
    render(<ChatShell />);
    // The setup mock has workspace: "/projects/cli"
    expect(screen.getByTestId("confirm-adoption-workspace")).toHaveTextContent("/projects/cli");
  });

  it("does not show confirm-adoption-add-dirs when add_dirs is empty", async () => {
    await setupUnconfirmedCliSession();
    render(<ChatShell />);
    // The setup mock has add_dirs: []
    expect(screen.queryByTestId("confirm-adoption-add-dirs")).not.toBeInTheDocument();
  });

  it("shows add_dirs in the confirmation panel when present", async () => {
    const hooks = await import("@/lib/api/hooks");
    await setupUnconfirmedCliSession();

    vi.mocked(hooks.useOperatorSession).mockReturnValue({
      data: {
        id: "adopted-session-1",
        name: "My CLI Session",
        model: "gpt-5.4",
        mode: "interactive",
        workspace: "/projects/cli",
        add_dirs: ["/extra/dir", "/another/dir"],
        created_at: "2024-01-01T00:00:00Z",
        updated_at: "2024-01-01T00:00:00Z",
        run_count: 0,
        last_run_id: null,
        resume_ready: false,
        source: "cli_adopt",
        resume_target: null,
        confirmed_at: null,
      },
      isLoading: false,
      isError: false,
      isSuccess: true,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof hooks.useOperatorSession>);

    render(<ChatShell />);
    expect(screen.getByTestId("confirm-adoption-add-dirs")).toHaveTextContent(
      "+/extra/dir, /another/dir"
    );
  });
});

describe("ChatShell — confirmed CLI adoption: composer enabled", () => {
  it("enables the composer when confirmed_at is set on a cli_adopt session", async () => {
    const hooks = await import("@/lib/api/hooks");
    const navigation = await import("next/navigation");

    vi.mocked(hooks.useOperatorSession).mockReturnValue({
      data: {
        id: "confirmed-session-1",
        name: "My Confirmed CLI Session",
        model: "gpt-5.4",
        mode: "interactive",
        workspace: "/projects/cli",
        add_dirs: [],
        created_at: "2024-01-01T00:00:00Z",
        updated_at: "2024-01-01T00:00:00Z",
        run_count: 0,
        last_run_id: null,
        resume_ready: true,
        source: "cli_adopt",
        resume_target: null,
        confirmed_at: "2024-01-01T00:01:00Z", // confirmed
      },
      isLoading: false,
      isError: false,
      isSuccess: true,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof hooks.useOperatorSession>);

    vi.mocked(hooks.useOperatorRuns).mockReturnValue({
      data: { runs: [], count: 0 },
      isLoading: false,
      isError: false,
      isFetchedAfterMount: true,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof hooks.useOperatorRuns>);

    vi.mocked(hooks.useSubmitPrompt).mockReturnValue({
      mutate: vi.fn(),
      isPending: false,
    } as unknown as ReturnType<typeof hooks.useSubmitPrompt>);

    vi.mocked(hooks.useUpdateOperatorSession).mockReturnValue({
      mutate: vi.fn(),
      isPending: false,
    } as unknown as ReturnType<typeof hooks.useUpdateOperatorSession>);

    vi.mocked(navigation.useSearchParams).mockReturnValue(
      new URLSearchParams("s=confirmed-session-1") as ReturnType<typeof navigation.useSearchParams>
    );

    hostStateMock = { host: LOCAL_HOST, diagnosticsEnabled: true, localDiagnosticsEnabled: true };

    render(<ChatShell />);

    // Confirmation panel must NOT be shown
    expect(screen.queryByTestId("confirm-adoption-panel")).not.toBeInTheDocument();

    // Composer must be enabled
    expect(screen.getByRole("textbox", { name: "Prompt" })).not.toBeDisabled();
  });
});

describe("SessionCreateDialog — CLI history tab", () => {
  it("renders the From CLI History tab when onAdopt is provided", async () => {
    hostStateMock = { host: LOCAL_HOST, diagnosticsEnabled: true, localDiagnosticsEnabled: true };
    render(<ChatShell />);
    fireEvent.click(screen.getByRole("button", { name: "New chat session" }));
    expect(screen.getByTestId("cli-history-tab")).toBeInTheDocument();
  });

  it("switches to CLI session list when the CLI History tab is clicked", async () => {
    const hooks = await import("@/lib/api/hooks");
    vi.mocked(hooks.useCliSessions).mockReturnValue({
      data: {
        sessions: [
          {
            cli_session_id: "cli-secret-uuid-not-in-url",
            title: "Fix the auth bug",
            mtime: "2024-01-01T00:00:00Z",
            workspace_hint: "/projects/auth",
            branch: "feature/auth-fix",
            repository: "my-org/my-repo",
          },
        ],
        count: 1,
        truncated: false,
      },
      isLoading: false,
      isError: false,
      error: null,
    } as unknown as ReturnType<typeof hooks.useCliSessions>);

    hostStateMock = { host: LOCAL_HOST, diagnosticsEnabled: true, localDiagnosticsEnabled: true };
    render(<ChatShell />);
    fireEvent.click(screen.getByRole("button", { name: "New chat session" }));
    fireEvent.click(screen.getByTestId("cli-history-tab"));

    expect(await screen.findByTestId("cli-session-list")).toBeInTheDocument();
    expect(screen.getByText("Fix the auth bug")).toBeInTheDocument();
    // workspace_hint is rendered as-is from server
    expect(screen.getByText("/projects/auth")).toBeInTheDocument();
  });

  it("cli-session-item does not have CLI UUID in any href or data attribute", async () => {
    const hooks = await import("@/lib/api/hooks");
    vi.mocked(hooks.useCliSessions).mockReturnValue({
      data: {
        sessions: [
          {
            cli_session_id: "SUPER_SECRET_CLI_UUID",
            title: "Secret session",
            mtime: "2024-01-01T00:00:00Z",
          },
        ],
        count: 1,
        truncated: false,
      },
      isLoading: false,
      isError: false,
      error: null,
    } as unknown as ReturnType<typeof hooks.useCliSessions>);

    hostStateMock = { host: LOCAL_HOST, diagnosticsEnabled: true, localDiagnosticsEnabled: true };
    const { container } = render(<ChatShell />);
    fireEvent.click(screen.getByRole("button", { name: "New chat session" }));
    fireEvent.click(screen.getByTestId("cli-history-tab"));

    await screen.findByTestId("cli-session-list");

    // The CLI UUID must not appear in any rendered URL/href attributes
    const anchors = container.querySelectorAll("a[href]");
    for (const a of anchors) {
      expect(a.getAttribute("href")).not.toContain("SUPER_SECRET_CLI_UUID");
    }

    // The CLI UUID must not appear in rendered text content (title is fine, UUID itself is not)
    // We check that the item text doesn't accidentally dump the UUID
    const items = container.querySelectorAll('[data-testid="cli-session-item"]');
    for (const item of items) {
      expect(item.textContent).not.toContain("SUPER_SECRET_CLI_UUID");
    }
  });
});

describe("SessionList — adopted session badge", () => {
  it("shows CLI (unconfirmed) badge for unconfirmed cli_adopt session", async () => {
    const hooks = await import("@/lib/api/hooks");
    const navigation = await import("next/navigation");

    vi.mocked(hooks.useOperatorSessions).mockReturnValue({
      data: {
        sessions: [
          {
            id: "adopted-1",
            name: "CLI Session",
            model: "gpt-5.4",
            mode: "interactive",
            workspace: "/projects/x",
            add_dirs: [],
            created_at: "2024-01-01T00:00:00Z",
            updated_at: "2024-01-01T00:00:00Z",
            run_count: 0,
            last_run_id: null,
            resume_ready: false,
            source: "cli_adopt",
            confirmed_at: null,
          },
        ],
        count: 1,
      },
      isLoading: false,
      isError: false,
    } as unknown as ReturnType<typeof hooks.useOperatorSessions>);

    vi.mocked(navigation.useSearchParams).mockReturnValue(
      new URLSearchParams() as ReturnType<typeof navigation.useSearchParams>
    );

    hostStateMock = { host: LOCAL_HOST, diagnosticsEnabled: true, localDiagnosticsEnabled: true };
    render(<ChatShell />);

    expect(
      screen.getByTitle(/Adopted from CLI history — pending confirmation/i)
    ).toBeInTheDocument();
  });

  it("shows CLI (confirmed) badge for confirmed cli_adopt session", async () => {
    const hooks = await import("@/lib/api/hooks");
    const navigation = await import("next/navigation");

    vi.mocked(hooks.useOperatorSessions).mockReturnValue({
      data: {
        sessions: [
          {
            id: "adopted-confirmed-1",
            name: "CLI Session Confirmed",
            model: "gpt-5.4",
            mode: "interactive",
            workspace: "/projects/x",
            add_dirs: [],
            created_at: "2024-01-01T00:00:00Z",
            updated_at: "2024-01-01T00:00:00Z",
            run_count: 1,
            last_run_id: "r1",
            resume_ready: true,
            source: "cli_adopt",
            confirmed_at: "2024-01-01T00:01:00Z",
          },
        ],
        count: 1,
      },
      isLoading: false,
      isError: false,
    } as unknown as ReturnType<typeof hooks.useOperatorSessions>);

    vi.mocked(navigation.useSearchParams).mockReturnValue(
      new URLSearchParams() as ReturnType<typeof navigation.useSearchParams>
    );

    hostStateMock = { host: LOCAL_HOST, diagnosticsEnabled: true, localDiagnosticsEnabled: true };
    render(<ChatShell />);

    expect(screen.getByTitle(/Adopted from CLI history \(confirmed\)/i)).toBeInTheDocument();
  });
});

describe("ChatShell — active host telemetry chip", () => {
  beforeEach(() => {
    // Reset mocks back to defaults for each test (vitest does not reset module
    // mocks automatically between tests defined in the same file).
    useHostFeatureMock.mockImplementation(() => ({ supported: true, loading: false }));
    hostStateMock = { host: LOCAL_HOST, diagnosticsEnabled: true, localDiagnosticsEnabled: true };
  });

  it("renders the disabled chip when the active host has not opted into telemetry", async () => {
    const hooks = await import("@/lib/api/hooks");
    const useHostMetricsSpy = vi.mocked(hooks.useHostMetrics);
    useHostMetricsSpy.mockClear();

    // LOCAL_HOST has no `telemetry_enabled`, so the chip must render the
    // muted "off" state and the metrics hook must be called with enabled=false
    // so React Query never polls /api/operator/host/metrics.
    render(<ChatShell />);

    expect(screen.getByTestId("active-host-telemetry-disabled")).toBeInTheDocument();
    expect(useHostMetricsSpy).toHaveBeenCalled();
    const lastCall = useHostMetricsSpy.mock.calls.at(-1);
    expect(lastCall?.[1]).toBe(false);
  });

  it("renders the unsupported chip when telemetry is opted in but capability is missing", async () => {
    useHostFeatureMock.mockImplementation((_h, feature: string) => {
      if (feature === "host_metrics") return { supported: false, loading: false };
      return { supported: true, loading: false };
    });
    hostStateMock = {
      host: { ...LOCAL_HOST, telemetry_enabled: true },
      diagnosticsEnabled: true,
      localDiagnosticsEnabled: true,
    };
    const hooks = await import("@/lib/api/hooks");
    const useHostMetricsSpy = vi.mocked(hooks.useHostMetrics);
    useHostMetricsSpy.mockClear();

    render(<ChatShell />);

    expect(screen.getByTestId("active-host-telemetry-unsupported")).toBeInTheDocument();
    // No polling when capability is missing.
    const lastCall = useHostMetricsSpy.mock.calls.at(-1);
    expect(lastCall?.[1]).toBe(false);
  });

  it("renders the unavailable chip when the metrics query errors", async () => {
    hostStateMock = {
      host: { ...LOCAL_HOST, telemetry_enabled: true },
      diagnosticsEnabled: true,
      localDiagnosticsEnabled: true,
    };
    const hooks = await import("@/lib/api/hooks");
    vi.mocked(hooks.useHostMetrics).mockReturnValueOnce({
      data: null,
      isLoading: false,
      isError: true,
    } as unknown as ReturnType<typeof hooks.useHostMetrics>);

    render(<ChatShell />);

    expect(screen.getByTestId("active-host-telemetry-error")).toBeInTheDocument();
  });

  it("renders aggregate CPU/RAM/network when data is available", async () => {
    hostStateMock = {
      host: { ...LOCAL_HOST, telemetry_enabled: true },
      diagnosticsEnabled: true,
      localDiagnosticsEnabled: true,
    };
    const hooks = await import("@/lib/api/hooks");
    vi.mocked(hooks.useHostMetrics).mockReturnValueOnce({
      data: {
        sampled_at: "2025-01-01T00:00:00Z",
        stale: false,
        cpu: { supported: true, percent: 42.7, load_1m: 1.5, load_5m: 1.2, load_15m: 1.0 },
        memory: { supported: true, percent: 63.1, total_bytes: 0, used_bytes: 0 },
        network: { supported: true, rx_bytes: 1234567, tx_bytes: 9876, iface_count: 1 },
        filesystem: { supported: true, mounts: {} },
      },
      isLoading: false,
      isError: false,
    } as unknown as ReturnType<typeof hooks.useHostMetrics>);

    render(<ChatShell />);

    const chip = screen.getByTestId("active-host-telemetry-data");
    expect(chip).toBeInTheDocument();
    expect(chip).toHaveAttribute("data-stale", "false");
    expect(screen.getByTestId("active-host-telemetry-cpu")).toHaveTextContent("cpu 43%");
    expect(screen.getByTestId("active-host-telemetry-mem")).toHaveTextContent("ram 63%");
    // 1234567 bytes → "1.2M", 9876 bytes → "9.9K"
    expect(screen.getByTestId("active-host-telemetry-net")).toHaveTextContent("↓1.2M ↑9.9K");
    expect(screen.queryByTestId("active-host-telemetry-stale")).not.toBeInTheDocument();
  });

  it("shows the stale badge when the metrics payload is marked stale", async () => {
    hostStateMock = {
      host: { ...LOCAL_HOST, telemetry_enabled: true },
      diagnosticsEnabled: true,
      localDiagnosticsEnabled: true,
    };
    const hooks = await import("@/lib/api/hooks");
    vi.mocked(hooks.useHostMetrics).mockReturnValueOnce({
      data: {
        sampled_at: "2025-01-01T00:00:00Z",
        stale: true,
        cpu: { supported: true, percent: 10, load_1m: 0.1, load_5m: 0.1, load_15m: 0.1 },
        memory: { supported: true, percent: 20, total_bytes: 0, used_bytes: 0 },
        network: { supported: false, rx_bytes: null, tx_bytes: null, iface_count: 0 },
        filesystem: { supported: false, mounts: {} },
      },
      isLoading: false,
      isError: false,
    } as unknown as ReturnType<typeof hooks.useHostMetrics>);

    render(<ChatShell />);

    expect(screen.getByTestId("active-host-telemetry-data")).toHaveAttribute("data-stale", "true");
    expect(screen.getByTestId("active-host-telemetry-stale")).toBeInTheDocument();
  });

  it("passes enabled=true to useHostMetrics only when both opt-in and capability are present", async () => {
    hostStateMock = {
      host: { ...LOCAL_HOST, telemetry_enabled: true },
      diagnosticsEnabled: true,
      localDiagnosticsEnabled: true,
    };
    const hooks = await import("@/lib/api/hooks");
    const useHostMetricsSpy = vi.mocked(hooks.useHostMetrics);
    useHostMetricsSpy.mockClear();

    render(<ChatShell />);

    const lastCall = useHostMetricsSpy.mock.calls.at(-1);
    expect(lastCall?.[1]).toBe(true);
  });
});
