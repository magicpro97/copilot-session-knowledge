import { expect, test } from "@playwright/test";
import { aliasPlaceholderSession } from "./session-detail-alias";

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
