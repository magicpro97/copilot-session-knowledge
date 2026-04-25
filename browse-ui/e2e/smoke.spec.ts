import { test, expect } from "@playwright/test";

test("sessions page loads", async ({ page }) => {
  await page.goto("/v2/sessions/");
  await expect(page.locator("h1")).toBeVisible();
});
