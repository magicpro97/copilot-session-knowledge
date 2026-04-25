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

test("session detail route renders tabbed UI", async ({ page }) => {
  await aliasPlaceholderSession(page);
  await page.goto("/v2/sessions/_placeholder/");
  await expect(page).toHaveURL(/\/v2\/sessions\/_placeholder\/?(#overview)?$/);

  await expect(
    page.getByRole("main").getByLabel("Breadcrumb").getByRole("link", { name: "Sessions" }),
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
