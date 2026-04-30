import { expect, test } from "@playwright/test";
import {
  aliasPlaceholderSession,
  assertSeededSessionAvailable,
  SEEDED_SESSION_ID,
} from "./session-detail-alias";

test("shipped /v2 routes render expected headings", async ({ page }) => {
  const headingByRoute = [
    ["/v2/sessions/", "Sessions"],
    ["/v2/search/", "Search"],
    ["/v2/insights/", "Insights"],
    ["/v2/graph/", "Graph"],
    ["/v2/settings/", "Settings"],
  ] as const;

  for (const [route, heading] of headingByRoute) {
    await page.goto(route);
    await expect(page.getByRole("heading", { level: 1, name: heading })).toBeVisible({
      timeout: 20_000,
    });
  }

  await expect(page.getByText("Appearance & preferences", { exact: true })).toBeVisible();
  await expect(page.getByText("System health", { exact: true })).toBeVisible();
  await expect(page.getByText("Keyboard shortcuts reference", { exact: true })).toBeVisible();
});

test("sidebar navigation updates the top route subtitle", async ({ page }) => {
  const routeExpectations = [
    ["Search", "Query extracted knowledge and jump directly to matching sessions."],
    ["Insights", "Track knowledge trends, live feed status, and evaluation health."],
    ["Graph", "Explore relationships and embedding clusters in one network workspace."],
    ["Settings", "Tune preferences, diagnostics, and shortcut references."],
    ["Sessions", "Review indexed sessions and drill into details quickly."],
  ] as const;

  await page.goto("/v2/sessions/");
  await expect(page.getByText("Review indexed sessions and drill into details quickly.", { exact: true })).toBeVisible({
    timeout: 20_000,
  });

  for (const [label, subtitle] of routeExpectations) {
    await page.getByRole("link", { name: label, exact: true }).click();
    await expect(page.getByText(subtitle, { exact: true })).toBeVisible({
      timeout: 20_000,
    });
  }
});

test("session detail route renders tabbed UI", async ({ page }) => {
  await aliasPlaceholderSession(page);
  await page.goto("/v2/sessions/_placeholder/");
  await expect(page).toHaveURL(/\/v2\/sessions\/_placeholder\/?(#overview)?$/);

  await expect(
    page.getByRole("main").getByLabel("Breadcrumb").getByRole("link", { name: "Sessions" })
  ).toBeVisible();
  await expect(page.getByRole("tab", { name: "Overview" })).toBeVisible();
  await expect(page.getByRole("tab", { name: "Timeline" })).toBeVisible();
  await expect(page.getByRole("tab", { name: "Mindmap" })).toBeVisible();
  await expect(page.getByRole("tab", { name: "Checkpoints" })).toBeVisible();
});

test("direct real UUID session detail route renders tabbed UI", async ({ page }) => {
  await assertSeededSessionAvailable(page);
  const placeholderSessionRequests: string[] = [];
  page.on("request", (request) => {
    const url = request.url();
    if (url.includes("/api/sessions/_placeholder") || url.includes("/api/session/_placeholder/")) {
      placeholderSessionRequests.push(url);
    }
  });

  await page.goto(`/v2/sessions/${SEEDED_SESSION_ID}/`);
  await expect(page).toHaveURL(new RegExp(`/v2/sessions/${SEEDED_SESSION_ID}/?(#overview)?$`));
  await expect(page.getByText("Failed to load session detail", { exact: true })).toHaveCount(0);

  await expect(page.getByRole("tab", { name: "Overview" })).toBeVisible();
  await expect(page.getByRole("tab", { name: "Timeline" })).toBeVisible();
  await expect(page.getByRole("tab", { name: "Mindmap" })).toBeVisible();
  await expect(page.getByRole("tab", { name: "Checkpoints" })).toBeVisible();
  await page.getByRole("tab", { name: "Timeline" }).click();
  await page.getByRole("tab", { name: "Mindmap" }).click();
  await page.getByRole("tab", { name: "Checkpoints" }).click();
  await page.waitForLoadState("networkidle");
  expect(placeholderSessionRequests).toEqual([]);
});

test("sessions list click-through opens real UUID session detail", async ({ page }) => {
  await assertSeededSessionAvailable(page);
  await page.goto("/v2/sessions/");
  await expect(page.getByRole("heading", { level: 1, name: "Sessions" })).toBeVisible({
    timeout: 20_000,
  });

  await expect(page.locator("tbody tr div.animate-pulse")).toHaveCount(0);
  const firstDataRow = page.locator("tbody tr").first();
  await expect(firstDataRow).toBeVisible();
  await firstDataRow.click();
  await expect(page).toHaveURL(/\/v2\/sessions\/[^/]+\/?(#overview)?$/);
  await expect(page).not.toHaveURL(/\/v2\/sessions\/_placeholder\/?(#overview)?$/);
  await expect(page.getByRole("tab", { name: "Overview" })).toBeVisible();
});

test("checkpoint diff viewer loads diff results and supports both modes", async ({ page }) => {
  await assertSeededSessionAvailable(page);
  await page.route("**/api/diff*", async (route) => {
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        session_id: SEEDED_SESSION_ID,
        from: { seq: 1, title: "Checkpoint 1", file: "checkpoint_001.md" },
        to: { seq: 3, title: "Checkpoint 3", file: "checkpoint_003.md" },
        unified_diff: [
          "--- checkpoint_001.md",
          "+++ checkpoint_003.md",
          "@@ -1,2 +1,2 @@",
          "-Removed detail",
          " context line",
          "+Added detail",
        ].join("\\n"),
        files: [{ from: "checkpoint_001.md", to: "checkpoint_003.md" }],
        stats: { added: 1, removed: 1 },
      }),
    });
  });

  await page.goto(`/v2/sessions/${SEEDED_SESSION_ID}/#checkpoints`);
  await expect(page.getByRole("tab", { name: "Checkpoints" })).toHaveAttribute(
    "aria-selected",
    "true"
  );
  await page.getByRole("button", { name: "Compute diff" }).click();

  await expect(page.getByText("Checkpoint diff (1 → 3)", { exact: true })).toBeVisible();
  await expect(page.getByText("+1 added · -1 removed", { exact: true })).toBeVisible();
  await expect(page.getByText("Added detail")).toBeVisible();
  await expect(page.getByText("Removed detail")).toBeVisible();

  await page.getByRole("button", { name: "Side-by-side" }).click();
  const splitSummary = await page.evaluate(() => ({
    left: document.querySelectorAll('[data-diff-side="left"]').length,
    right: document.querySelectorAll('[data-diff-side="right"]').length,
  }));
  expect(splitSummary.left).toBeGreaterThan(0);
  expect(splitSummary.right).toBeGreaterThan(0);
});

test("graph evidence and similarity tabs render live product surfaces", async ({ page }) => {
  await page.goto("/v2/graph/");
  await expect(page.getByRole("heading", { level: 1, name: "Graph" })).toBeVisible({
    timeout: 20_000,
  });

  await expect(page.getByRole("tab", { name: /^(Evidence|Relationships)$/ })).toBeVisible();
  await expect(page.getByRole("tab", { name: /^(Similarity|Clusters)$/ })).toBeVisible();
  await expect(page.getByText("Filters", { exact: true })).toBeVisible();
  await expect(page.getByText("Label search", { exact: true })).toBeVisible();
  await expect(page.getByText(/Showing \d+ nodes and \d+ edges/)).toBeVisible();
  await expect(page.getByText("Relationships shell ready")).toHaveCount(0);

  await page.getByRole("tab", { name: /^(Similarity|Clusters)$/ }).click();
  await expect(page.getByText(/Selected (entry|point)/)).toBeVisible();
  const similaritySignal = page
    .getByText("Orientation map (secondary)", { exact: true })
    .or(page.getByText(/Showing \d+ \/ \d+ loaded points/))
    .or(page.getByRole("heading", { name: "No embedding points available" }));
  await expect(similaritySignal.first()).toBeVisible();
  await expect(page.getByText("Clusters shell ready")).toHaveCount(0);
});

test("graph communities tab renders and is not a placeholder shell", async ({ page }) => {
  await page.goto("/v2/graph/");
  await expect(page.getByRole("heading", { level: 1, name: "Graph" })).toBeVisible({
    timeout: 20_000,
  });

  const communitiesTab = page.getByRole("tab", { name: "Communities" });
  await expect(communitiesTab).toBeVisible();
  await communitiesTab.click();
  await expect(communitiesTab).toHaveAttribute("aria-selected", "true");
  await expect(page.getByText("Communities shell ready")).toHaveCount(0);
});

test("insights retrospective section loads repo-mode summary", async ({ page }) => {
  await page.goto("/v2/insights/");
  await expect(page.getByRole("tab", { name: "Dashboard" })).toBeVisible({
    timeout: 20_000,
  });

  const retroSummary = page.locator("summary").filter({ hasText: "Retrospective" }).first();
  await expect(retroSummary).toBeVisible();
  await retroSummary.click();
  await expect(page.getByText(/mode:\s*repo/i)).toBeVisible({ timeout: 20_000 });
});

test("settings page operator diagnostics cards render", async ({ page }) => {
  await page.goto("/v2/settings/");
  await expect(page.getByRole("heading", { level: 1, name: "Settings" })).toBeVisible({
    timeout: 20_000,
  });

  // All five diagnostic card titles render unconditionally (regardless of API state).
  await expect(page.getByText("Sync diagnostics", { exact: true })).toBeVisible();
  await expect(page.getByText("Trend Scout diagnostics", { exact: true })).toBeVisible();
  await expect(page.getByText("Tentacle runtime diagnostics", { exact: true })).toBeVisible();
  await expect(page.getByText("Skill outcome metrics", { exact: true })).toBeVisible();
  await expect(page.getByText("System health", { exact: true })).toBeVisible();
});

test("settings page operator-actions panels are display-only", async ({ page }) => {
  await page.goto("/v2/settings/");
  await expect(page.getByRole("heading", { level: 1, name: "Settings" })).toBeVisible({
    timeout: 20_000,
  });

  // Allow API calls to settle (data, error, or loading state is acceptable).
  await page.waitForLoadState("networkidle");

  // At least one operator-actions panel must render in the smoke environment.
  const operatorChecksLabel = page.getByText("Operator checks (read-only)");
  await expect(operatorChecksLabel.first()).toBeVisible({ timeout: 15_000 });

  // If operator panels are present, every action button inside them must be Copy-only.
  const panelCount = await operatorChecksLabel.count();
  expect(panelCount).toBeGreaterThan(0);
  for (let i = 0; i < panelCount; i += 1) {
    const buttons = operatorChecksLabel.nth(i).locator("..").getByRole("button");
    const buttonCount = await buttons.count();
    expect(buttonCount).toBeGreaterThan(0);
    for (let j = 0; j < buttonCount; j += 1) {
      await expect(buttons.nth(j)).toHaveAccessibleName("Copy");
    }
  }
});

test("search feedback submits and resets when the query changes", async ({ page }) => {
  await page.goto("/v2/search/?q=deterministic");
  const searchInput = page.getByRole("searchbox", { name: "Search sessions and knowledge" });
  const helpfulButton = page.getByRole("button", { name: "Helpful result" }).first();

  await expect(helpfulButton).toBeVisible({ timeout: 20_000 });
  await helpfulButton.click();
  await expect(page.getByText("👍 Thanks!").first()).toBeVisible();

  await searchInput.fill("e2e");
  await expect(searchInput).toHaveValue("e2e");
  await searchInput.press("Enter");
  await expect(page.getByRole("button", { name: "Helpful result" }).first()).toBeVisible({
    timeout: 20_000,
  });
});
