import { expect, test } from "@playwright/test";

test("settings shortcuts card visual snapshot", async ({ page }) => {
  await page.goto("/v2/settings/#shortcuts");
  await expect(page.getByRole("heading", { level: 1, name: "Settings" })).toBeVisible({
    timeout: 20_000,
  });
  const shortcutsCard = page
    .getByRole("heading", { level: 2, name: "Session detail + tabs" })
    .locator("xpath=..");

  await expect(shortcutsCard).toBeVisible();
  await expect(shortcutsCard).toHaveScreenshot("settings-shortcuts-card.png", {
    animations: "disabled",
    caret: "hide",
  });
});

test("command palette visual snapshot", async ({ page }) => {
  await page.addInitScript(() => {
    window.localStorage.removeItem("browse-ui-recent-commands");
    window.localStorage.removeItem("browse-ui-recent-searches");
  });
  await page.goto("/v2/sessions/");
  await expect(page.getByRole("heading", { level: 1, name: "Sessions" })).toBeVisible({
    timeout: 20_000,
  });
  await page.keyboard.press(`${process.platform === "darwin" ? "Meta" : "Control"}+KeyK`);

  const dialog = page.getByRole("dialog");
  await expect(page.getByPlaceholder("Type a command or search history...")).toBeVisible();
  await expect(dialog).toBeVisible();

  await expect(dialog).toHaveScreenshot("command-palette.png", {
    animations: "disabled",
    caret: "hide",
  });
});

test("graph tabs visual snapshot", async ({ page }) => {
  await page.goto("/v2/graph/");
  await expect(page.getByRole("heading", { level: 1, name: "Graph" })).toBeVisible({
    timeout: 20_000,
  });
  const tabsList = page.getByRole("tablist");
  await expect(tabsList).toBeVisible();
  await expect(page.getByRole("tab", { name: "Evidence" })).toBeVisible();
  await expect(page.getByRole("tab", { name: "Similarity" })).toBeVisible();
  await expect(page.getByRole("tab", { name: "Communities" })).toBeVisible();
  await expect(page.getByRole("tab", { name: "Relationships" })).toHaveCount(0);
  await expect(page.getByRole("tab", { name: "Clusters" })).toHaveCount(0);
  await expect(tabsList).toHaveScreenshot("graph-tabs.png", {
    animations: "disabled",
    caret: "hide",
  });
});
