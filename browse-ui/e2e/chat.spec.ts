import type { Page } from "@playwright/test";
import { expect, test } from "./fixtures";

const SESSION_ID = "e2e-operator-session-0001";
const CREATED_FILE = "~/projects/demo/notes.txt";
const CHANGED_FILE = "~/projects/demo/app.py";

const SEEDED_SESSION = {
  id: SESSION_ID,
  name: "E2E operator session",
  model: "claude-sonnet-4.5",
  mode: "interactive",
  workspace: "~/projects/demo",
  add_dirs: [],
  created_at: "2026-05-01T10:00:00Z",
  updated_at: "2026-05-01T10:05:00Z",
  run_count: 1,
  last_run_id: "run-created-1",
  resume_ready: true,
};

const CREATED_RUN = {
  id: "run-created-1",
  session_id: SESSION_ID,
  prompt: "Create a notes file with one line of text.",
  status: "done",
  exit_code: 0,
  started_at: "2026-05-01T10:01:00Z",
  finished_at: "2026-05-01T10:01:05Z",
  events: [
    {
      type: "assistant.message_start",
      idx: 0,
      event: { type: "assistant.message_start" },
      data: {},
    },
    {
      type: "assistant.message",
      idx: 1,
      event: { type: "assistant.message" },
      data: { content: "Created the requested notes file." },
    },
    {
      type: "tool.execution_start",
      idx: 2,
      event: { type: "tool.execution_start" },
      data: {
        toolName: "create_file",
        arguments: JSON.stringify({ path: CREATED_FILE }),
      },
    },
    {
      type: "tool.execution_complete",
      idx: 3,
      event: { type: "tool.execution_complete" },
      data: {
        result: {
          detailedContent: `Created ${CREATED_FILE}`,
        },
        toolTelemetry: {
          restrictedProperties: {
            filePaths: JSON.stringify([CREATED_FILE]),
            addedPaths: JSON.stringify([CREATED_FILE]),
          },
        },
      },
    },
  ],
};

const DIFF_RUN = {
  id: "run-diff-1",
  session_id: SESSION_ID,
  prompt: "Update the app title.",
  status: "done",
  exit_code: 0,
  started_at: "2026-05-01T10:02:00Z",
  finished_at: "2026-05-01T10:02:06Z",
  events: [
    {
      type: "assistant.message_start",
      idx: 0,
      event: { type: "assistant.message_start" },
      data: {},
    },
    {
      type: "assistant.message",
      idx: 1,
      event: { type: "assistant.message" },
      data: { content: "Updated the title in the existing file." },
    },
    {
      type: "tool.execution_start",
      idx: 2,
      event: { type: "tool.execution_start" },
      data: {
        toolName: "apply_patch",
        arguments: "*** Begin Patch\n*** End Patch\n",
      },
    },
    {
      type: "tool.execution_complete",
      idx: 3,
      event: { type: "tool.execution_complete" },
      data: {
        result: {
          detailedContent: [
            "--- a/app.py",
            "+++ b/app.py",
            "@@ -1 +1 @@",
            '-TITLE = "Old title"',
            '+TITLE = "New title"',
          ].join("\n"),
        },
        toolTelemetry: {
          restrictedProperties: {
            filePaths: JSON.stringify([CHANGED_FILE]),
          },
        },
      },
    },
  ],
};

type MockOperatorApiOptions = {
  sessions?: typeof SEEDED_SESSION[];
  session?: typeof SEEDED_SESSION | null;
  runs?: Array<typeof CREATED_RUN | typeof DIFF_RUN>;
  previewByPath?: Record<
    string,
    {
      path: string;
      content: string;
      mime: string;
      size: number;
    }
  >;
};

async function mockOperatorApi(
  page: Page,
  { sessions = [], session = null, runs = [], previewByPath = {} }: MockOperatorApiOptions = {}
) {
  await page.route("**/api/operator/**", async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;

    if (path === "/api/operator/sessions") {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({ sessions, count: sessions.length }),
      });
      return;
    }

    if (session && path === `/api/operator/sessions/${session.id}`) {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify(session),
      });
      return;
    }

    if (session && path === `/api/operator/sessions/${session.id}/runs`) {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({ runs, count: runs.length }),
      });
      return;
    }

    if (path === "/api/operator/suggest") {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({ suggestions: [], count: 0 }),
      });
      return;
    }

    if (path === "/api/operator/preview") {
      const preview = previewByPath[url.searchParams.get("path") || ""];
      if (!preview) {
        throw new Error(`Unexpected preview request: ${url.search}`);
      }
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify(preview),
      });
      return;
    }

    throw new Error(`Unexpected operator API request: ${path}${url.search}`);
  });
}

test("/chat renders the operator console shell and session dialog", async ({ page }) => {
  await mockOperatorApi(page);

  await page.goto("/v2/chat/");
  await expect(page.getByTestId("chat-shell")).toBeVisible({ timeout: 20_000 });
  await expect(page.getByText("No session selected", { exact: true })).toBeVisible();
  await expect(
    page.getByText("Run Copilot CLI prompts against a workspace and inspect results.", {
      exact: true,
    })
  ).toBeVisible();

  await page.getByRole("button", { name: "New chat session" }).click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await expect(page.getByText("Start a new chat session", { exact: true })).toBeVisible();
  await expect(page.getByText(/Must be a path under/)).toBeVisible();
});

test("/chat sidebar can be collapsed and reopened", async ({ page }) => {
  await mockOperatorApi(page);

  await page.goto("/v2/chat/");
  await expect(page.getByText("Chat Sessions", { exact: true })).toBeVisible();

  await page.getByRole("button", { name: "Collapse session list" }).click();
  await expect(page.getByRole("button", { name: "Expand session list" })).toBeVisible();

  await page.getByRole("button", { name: "Expand session list" }).click();
  await expect(page.getByText("Chat Sessions", { exact: true })).toBeVisible();
});

test("/chat previews a created file from persisted run history", async ({ page }) => {
  await mockOperatorApi(page, {
    sessions: [SEEDED_SESSION],
    session: SEEDED_SESSION,
    runs: [CREATED_RUN],
    previewByPath: {
      [CREATED_FILE]: {
        path: CREATED_FILE,
        content: "Created from Playwright.",
        mime: "text/plain",
        size: 24,
      },
    },
  });

  await page.goto(`/v2/chat/?s=${SESSION_ID}`);
  await expect(page.getByTestId("chat-shell")).toBeVisible({ timeout: 20_000 });
  await expect(page.getByText(CREATED_RUN.prompt, { exact: true })).toBeVisible();
  await expect(page.getByText(CREATED_FILE, { exact: true })).toBeVisible();

  await page.locator('[title="Preview file"]').click();
  await expect(page.getByText("Created from Playwright.", { exact: true })).toBeVisible();
});

test("/chat shows inline diff for a changed file from persisted run history", async ({ page }) => {
  await mockOperatorApi(page, {
    sessions: [{ ...SEEDED_SESSION, last_run_id: DIFF_RUN.id }],
    session: { ...SEEDED_SESSION, last_run_id: DIFF_RUN.id },
    runs: [DIFF_RUN],
  });

  await page.goto(`/v2/chat/?s=${SESSION_ID}`);
  await expect(page.getByTestId("chat-shell")).toBeVisible({ timeout: 20_000 });
  await expect(page.getByText(DIFF_RUN.prompt, { exact: true })).toBeVisible();
  await expect(page.getByText(CHANGED_FILE, { exact: true })).toBeVisible();

  await page.locator('[title="Show applied diff"]').click();
  await expect(page.getByText("+1 added · -1 removed", { exact: true })).toBeVisible();
  await expect(page.getByText('TITLE = "Old title"', { exact: false })).toBeVisible();
  await expect(page.getByText('TITLE = "New title"', { exact: false })).toBeVisible();
});
