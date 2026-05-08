/**
 * E2E tests for broker mode Add Host UI (#65) — diagnosis-driven display.
 *
 * Verifies that:
 * 1. The Broker Mode tab is NOT shown by default (not unconditional).
 * 2. After a failed probe (non-auth), the broker recommendation banner and
 *    Broker Mode tab appear (diagnosis-driven).
 * 3. Entering a bot name shows the deep-link preview.
 * 4. Saving a broker profile persists it with connectivity_mode === "broker".
 * 5. The saved host row shows a "broker" badge.
 *
 * Screenshot SHA-256 pattern mirrors host-pairing.spec.ts (lines 70-100):
 *   - Capture before-state screenshot
 *   - Trigger the UI change
 *   - Capture after-state screenshot
 *   - Assert afterHash !== beforeHash
 *   - Attach both hashes as JSON artifact for evidence
 *
 * NOTE: These tests run against the Next.js dev server (baseURL: http://127.0.0.1:8765).
 * To simulate a tunnel-hostile probe failure, tests mock the capabilities endpoint to
 * return HTTP 500, triggering `suggestBrokerFromProbeFail()` and the recommendation UI.
 */

import { createHash } from "node:crypto";
import type { Page } from "@playwright/test";
import { expect, test } from "./fixtures";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function sha256(buf: Buffer): string {
  return createHash("sha256").update(buf).digest("hex");
}

/** Navigate to /settings/#hosts. */
async function gotoHostsSettings(page: Page) {
  await page.goto("/settings/#hosts");
  await expect(page.getByRole("heading", { name: "Settings" })).toBeVisible({
    timeout: 20_000,
  });
  await expect(page.getByText("Hosts & connections")).toBeVisible();
}

/**
 * Route all /api/operator/capabilities requests to return HTTP 500.
 * This simulates a tunnel-hostile probe failure, triggering the broker
 * recommendation banner and diagnosis-driven Broker Mode tab (#65).
 */
async function mockProbeFailure(page: Page) {
  await page.route("**/api/operator/capabilities", (route) =>
    route.fulfill({ status: 500, body: JSON.stringify({ error: "simulated tunnel failure" }) })
  );
}

/**
 * Open the Add Host form, fill a URL, submit, and wait for the broker
 * recommendation banner to appear. Stops here — does NOT click the Broker
 * Mode tab. After this helper returns, the direct tab is still selected and
 * broker-instructions are NOT visible. Requires mockProbeFailure to be active.
 *
 * Use this helper in tests that verify the diagnosis-driven banner/tab appear
 * WITHOUT any explicit tab switch (the product never auto-selects broker mode).
 */
async function triggerBrokerBanner(page: Page, url = "https://fail.example.com") {
  await page.getByRole("button", { name: "Add remote host" }).click();
  await expect(page.getByTestId("host-add-form")).toBeVisible();

  // Fill in a URL and submit — the mocked probe will fail with HTTP 500.
  await page.getByRole("textbox", { name: "Tunnel URL" }).fill(url);
  await page.getByTestId("save-host-btn").click();

  // Wait for the broker recommendation banner — indicates diagnosis triggered.
  // The product sets brokerDiagnosis state but does NOT auto-select broker tab.
  await expect(page.getByTestId("broker-recommendation-banner")).toBeVisible({ timeout: 15_000 });
}

/**
 * Open the Add Host form, fill a URL, submit, wait for the broker
 * recommendation banner to appear, then click the Broker Mode tab.
 * After this helper returns, the broker form is active and ready.
 * Requires mockProbeFailure to be active.
 *
 * Use this helper in tests that need to interact with the broker form
 * (bot name, pairing token, save, clear diagnosis).
 */
async function triggerBrokerDiagnosis(page: Page, url = "https://fail.example.com") {
  await triggerBrokerBanner(page, url);

  // Click the Broker Mode tab to switch to the broker form.
  // (The tab appears after diagnosis; it is NOT auto-selected — the operator
  // must explicitly choose broker mode.)
  await page.getByTestId("tab-broker").click();
  await expect(page.getByTestId("broker-instructions")).toBeVisible();
}

// ---------------------------------------------------------------------------
// Tests — Broker Mode tab is diagnosis-driven (#65)
// ---------------------------------------------------------------------------

test(
  "Broker Mode tab is NOT visible by default when Add Host form opens (#65 diagnosis-driven guard)",
  async ({ page }) => {
    await gotoHostsSettings(page);

    // Open the add-host form.
    await page.getByRole("button", { name: "Add remote host" }).click();
    await expect(page.getByTestId("host-add-form")).toBeVisible();

    // Direct tab should be active and visible.
    await expect(page.getByTestId("tab-direct")).toBeVisible();
    await expect(page.getByTestId("tab-direct")).toHaveAttribute("aria-selected", "true");

    // Broker Mode tab must NOT be visible without a diagnosis — display is conditional.
    await expect(page.getByTestId("tab-broker")).not.toBeVisible();

    // Recommendation banner also must not be visible.
    await expect(page.getByTestId("broker-recommendation-banner")).not.toBeVisible();
  }
);

test(
  "After a failed probe, broker recommendation banner and Broker Mode tab appear — SHA-256 changes (#65)",
  async ({ page }, testInfo) => {
    await mockProbeFailure(page);
    await gotoHostsSettings(page);

    // Capture before state (form not yet open).
    const hostsCard = page.locator("#hosts");
    const before = await hostsCard.screenshot();
    const beforeHash = sha256(before);

    // Trigger the broker banner via a probe failure (does NOT click broker tab).
    // The product never auto-selects broker mode — the operator must explicitly
    // click the tab. Using triggerBrokerBanner() rather than triggerBrokerDiagnosis()
    // so the assertions below correctly reflect the post-diagnosis state.
    await triggerBrokerBanner(page);

    // Broker recommendation banner should now be visible.
    await expect(page.getByTestId("broker-recommendation-banner")).toBeVisible();

    // Broker Mode tab should now be visible (diagnosis-driven, not unconditional).
    await expect(page.getByTestId("tab-broker")).toBeVisible();

    // The broker tab is NOT auto-selected — direct tab remains active.
    // The operator must explicitly click the broker tab to switch modes.
    await expect(page.getByTestId("tab-direct")).toHaveAttribute("aria-selected", "true");
    await expect(page.getByTestId("tab-broker")).toHaveAttribute("aria-selected", "false");

    // Broker instructions are NOT shown until the user clicks the tab.
    await expect(page.getByTestId("broker-instructions")).not.toBeVisible();

    // Capture after state.
    const after = await hostsCard.screenshot();
    const afterHash = sha256(after);

    // Screenshot must have changed — UI rendered differently.
    expect(afterHash).not.toBe(beforeHash);

    // Attach hashes as evidence artifact.
    await testInfo.attach("broker-diagnosis-sha256", {
      contentType: "application/json",
      body: JSON.stringify({
        test: "broker-diagnosis-driven",
        issue: "#65",
        before: beforeHash,
        after: afterHash,
        changed: afterHash !== beforeHash,
      }),
    });
  }
);

test(
  "Broker Mode form shows deep-link preview when bot name is entered (#65)",
  async ({ page }) => {
    await mockProbeFailure(page);
    await gotoHostsSettings(page);

    // Trigger broker diagnosis.
    await triggerBrokerDiagnosis(page);

    // Should already be on broker tab (auto-switched).
    await expect(page.getByTestId("broker-instructions")).toBeVisible();

    // Type a bot name.
    await page.getByTestId("broker-bot-name").fill("my_copilot_bot");

    // Deep-link preview should appear.
    await expect(page.getByTestId("broker-deep-link")).toBeVisible();
    const deepLink = await page.getByTestId("broker-deep-link").getAttribute("href");
    expect(deepLink).toContain("https://t.me/my_copilot_bot");

    // Add pairing token.
    await page.getByTestId("broker-pairing-token").fill("tok123");

    // Deep-link should include the start= parameter.
    const deepLinkWithToken = await page.getByTestId("broker-deep-link").getAttribute("href");
    expect(deepLinkWithToken).toContain("start=tok123");
  }
);

test(
  "Saving a broker profile adds a host row with 'broker' badge and connectivity_mode persisted (#65)",
  async ({ page }, testInfo) => {
    await mockProbeFailure(page);
    await gotoHostsSettings(page);

    // Capture state before adding.
    const hostsCard = page.locator("#hosts");
    const before = await hostsCard.screenshot();
    const beforeHash = sha256(before);

    // Trigger broker diagnosis.
    await triggerBrokerDiagnosis(page);

    // Already on broker form — fill in bot name and pairing token.
    await page.getByTestId("broker-bot-name").fill("test_broker_bot");
    await page.getByTestId("broker-pairing-token").fill("pairing_secret_42");

    // Set a label.
    await page.getByRole("textbox", { name: "Host label" }).fill("Test Broker");

    // Save the broker profile (no network probe — this should complete immediately).
    await page.getByTestId("save-broker-btn").click();

    // The add form should close.
    await expect(page.getByTestId("host-add-form")).not.toBeVisible({ timeout: 5_000 });

    // A host row should appear with the broker badge.
    const brokerBadge = page.locator('[data-testid^="host-broker-badge-"]');
    await expect(brokerBadge).toBeVisible({ timeout: 5_000 });

    // The broker badge text should be "broker".
    await expect(brokerBadge).toHaveText(/broker/i);

    // Verify the profile is persisted in localStorage with connectivity_mode.
    const storedRaw = await page.evaluate(() =>
      window.localStorage.getItem("browse_host_profiles")
    );
    const stored = JSON.parse(storedRaw ?? "[]") as Array<Record<string, unknown>>;
    const brokerProfile = stored.find(
      (p) => typeof p.base_url === "string" && p.base_url.includes("test_broker_bot")
    );
    expect(brokerProfile).toBeDefined();
    expect(brokerProfile?.connectivity_mode).toBe("broker");

    // Capture after state for SHA-256 evidence.
    const after = await hostsCard.screenshot();
    const afterHash = sha256(after);
    expect(afterHash).not.toBe(beforeHash);

    await testInfo.attach("broker-save-sha256", {
      contentType: "application/json",
      body: JSON.stringify({
        test: "broker-save",
        issue: "#65",
        before: beforeHash,
        after: afterHash,
        changed: afterHash !== beforeHash,
        connectivity_mode: brokerProfile?.connectivity_mode,
      }),
    });
  }
);

test(
  "Switching back to Tunnel/Direct tab via 'try direct again' clears broker diagnosis (#65)",
  async ({ page }) => {
    await mockProbeFailure(page);
    await gotoHostsSettings(page);

    // Trigger broker diagnosis.
    await triggerBrokerDiagnosis(page);

    // Verify broker tab is visible and selected.
    await expect(page.getByTestId("tab-broker")).toBeVisible();
    await expect(page.getByTestId("tab-broker")).toHaveAttribute("aria-selected", "true");

    // Click "try direct again" in the recommendation banner.
    await page.getByRole("button", { name: "try direct again" }).click();

    // Broker tab should disappear (diagnosis cleared).
    await expect(page.getByTestId("tab-broker")).not.toBeVisible();
    await expect(page.getByTestId("broker-recommendation-banner")).not.toBeVisible();

    // Direct tab should be active.
    await expect(page.getByTestId("tab-direct")).toHaveAttribute("aria-selected", "true");

    // The URL input should be visible (back to direct form).
    await expect(page.getByRole("textbox", { name: "Tunnel URL" })).toBeVisible();
  }
);
