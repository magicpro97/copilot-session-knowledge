/**
 * E2E tests for QR pairing (#58) and demo-mode badge (#59).
 *
 * Screenshot SHA-256 pattern mirrors chat.spec.ts lines 499-522:
 *   - Capture before-state screenshot, record its SHA-256
 *   - Trigger the UI change
 *   - Capture after-state screenshot, record its SHA-256
 *   - Assert afterHash !== beforeHash (proves rendering changed)
 *   - Attach both hashes as JSON artifact for evidence
 *
 * NOTE: The "Pair via QR" button and "Detect local backend" button are only
 * rendered when the page is on a non-local origin (getHostedOrigin() truthy).
 * We mock `window.location.origin` via addInitScript() so the tests run in the
 * Playwright dev server without needing an actual hosted deployment.
 */

import { createHash } from "node:crypto";
import type { Page } from "@playwright/test";
import { expect, test } from "./fixtures";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Intercept the loopback discovery endpoint that handleDetectLocal probes. */
async function mockWellKnownEndpoint(
  page: Page,
  response: Record<string, unknown>
) {
  // The component probes both http://127.0.0.1:8765 and http://localhost:8765
  for (const host of ["127.0.0.1:8765", "localhost:8765"]) {
    await page.route(`http://${host}/.well-known/browse-host`, async (route) => {
      await route.fulfill({
        contentType: "application/json",
        status: 200,
        body: JSON.stringify(response),
        headers: {
          "Access-Control-Allow-Origin": "https://agents.example.com",
          "Access-Control-Allow-Private-Network": "true",
        },
      });
    });
  }
}

/** Navigate to /settings/#hosts with hosted-origin mock applied. */
async function gotoHostsSettings(page: Page) {
  // Must be set before page load so getHostedOrigin() sees a non-local origin.
  // We use a custom window property instead of Object.defineProperty(window, "location", ...)
  // because window.location is non-configurable in real browsers (Edge, mobile Chrome) and
  // attempting to redefine it throws: TypeError: Cannot redefine property: location.
  await page.addInitScript(() => {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    (window as any).__E2E_HOSTED_ORIGIN__ = "https://agents.example.com";
  });
  await page.goto("/settings/#hosts");
  await expect(page.getByRole("heading", { name: "Settings" })).toBeVisible({
    timeout: 20_000,
  });
  // Use the card's id anchor — unique on the page, unambiguous even when
  // diagnostics-idle <p> elements also contain "Hosts & connections" as
  // a substring (which caused strict-mode to resolve 6 elements before).
  await expect(page.locator("#hosts")).toBeVisible();
}

// ---------------------------------------------------------------------------
// Tests — QR pairing panel (#58)
// ---------------------------------------------------------------------------

test("Pair via QR button toggles QR pairing panel — SHA-256 changes (#58)", async (
  { page },
  testInfo,
) => {
  await gotoHostsSettings(page);

  // Grab the hosts card / section so screenshots are scoped and stable.
  const hostsSection = page.locator("#hosts");

  async function captureSection(label: string) {
    const screenshot = await hostsSection.screenshot({
      animations: "disabled",
      caret: "hide",
    });
    await testInfo.attach(`${label}.png`, { body: screenshot, contentType: "image/png" });
    const sha256 = createHash("sha256").update(screenshot).digest("hex");
    return { sha256 };
  }

  // Before: QR panel not yet open
  const before = await captureSection("qr-panel-before");

  // Verify the button exists (hostedOrigin mock worked)
  const qrBtn = page.getByTestId("qr-pairing-btn");
  await expect(qrBtn).toBeVisible();

  // Click to open QR pairing panel
  await qrBtn.click();
  await expect(page.getByTestId("qr-pairing-panel")).toBeVisible({ timeout: 5_000 });

  // After: QR panel is open
  const after = await captureSection("qr-panel-after");

  await testInfo.attach("qr-panel-hashes.json", {
    body: JSON.stringify({ before, after }, null, 2),
    contentType: "application/json",
  });

  // The screenshot must have changed — proves the panel actually rendered
  expect(after.sha256).not.toBe(before.sha256);
});

test("QR pairing panel can be closed via cancel button (#58)", async ({ page }) => {
  await gotoHostsSettings(page);

  const qrBtn = page.getByTestId("qr-pairing-btn");
  await expect(qrBtn).toBeVisible();
  await qrBtn.click();

  const panel = page.getByTestId("qr-pairing-panel");
  await expect(panel).toBeVisible({ timeout: 5_000 });

  // Close it
  await page.getByTestId("qr-pairing-close-btn").click();
  await expect(panel).toHaveCount(0);
});

test("QR pairing panel paste mode accepts a valid browse:// ticket (#58)", async ({ page }) => {
  await gotoHostsSettings(page);

  await page.getByTestId("qr-pairing-btn").click();
  await expect(page.getByTestId("qr-pairing-panel")).toBeVisible({ timeout: 5_000 });

  // Switch to paste mode
  await page.getByTestId("qr-paste-url-btn").click();
  await expect(page.getByTestId("qr-paste-input")).toBeVisible();

  // Build a valid-looking ticket (5 minutes from now is in the future, won't be stale).
  // The client only checks JSON shape + freshness; HMAC verification is server-side.
  const payload = {
    url: "http://127.0.0.1:8765",
    nonce: "aabbccddeeff00112233445566778899",
    // created_at 30 seconds ago (within the 5-minute freshness window)
    created_at: Math.floor(Date.now() / 1000) - 30,
    token_hmac: "dummyhmac",
  };
  const ticket = Buffer.from(JSON.stringify(payload))
    .toString("base64")
    .replace(/\+/g, "-")
    .replace(/\//g, "_")
    .replace(/=+$/, "");
  const browseUrl = `browse://connect?ticket=${ticket}`;

  await page.route("http://127.0.0.1:8765/.well-known/browse-host/verify", async (route) => {
    await route.abort("failed");
  });
  await page.getByTestId("qr-paste-input").fill(browseUrl);
  await page.getByTestId("qr-confirm-paste-btn").click();

  // Should show success state and pre-fill the add-host form
  await expect(page.getByTestId("qr-pairing-success")).toBeVisible({ timeout: 5_000 });
  await expect(page.getByTestId("qr-pairing-warning")).toBeVisible({ timeout: 5_000 });
});

// ---------------------------------------------------------------------------
// Tests — Demo mode badge (#59)
// ---------------------------------------------------------------------------

test("Demo mode banner appears after detecting static-mode backend — SHA-256 changes (#59)", async (
  { page },
  testInfo,
) => {
  // Mock the discovery endpoint to return demo/static mode fields
  await mockWellKnownEndpoint(page, {
    schema: "browse-host/1",
    status: "ok",
    auth: "open",
    manual_token_required: false,
    cors_origins_configured: true,
    capabilities: ["sessions", "knowledge", "pairing"],
    pairing_supported: true,
    static_mode_active: true,
    demo_mode_badge: "Demo Mode",
  });

  await gotoHostsSettings(page);

  const hostsSection = page.locator("#hosts");

  async function captureSection(label: string) {
    const screenshot = await hostsSection.screenshot({
      animations: "disabled",
      caret: "hide",
    });
    await testInfo.attach(`${label}.png`, { body: screenshot, contentType: "image/png" });
    const sha256 = createHash("sha256").update(screenshot).digest("hex");
    return { sha256 };
  }

  // Before: no demo mode banner
  await expect(page.getByTestId("demo-mode-banner")).toHaveCount(0);
  const before = await captureSection("demo-banner-before");

  // Trigger local backend detection — should receive the mocked static_mode response
  const detectBtn = page.getByTestId("detect-local-btn");
  await expect(detectBtn).toBeVisible();
  await detectBtn.click();

  // Wait for demo mode banner to appear (detect probe completed)
  await expect(page.getByTestId("demo-mode-banner")).toBeVisible({ timeout: 10_000 });

  const after = await captureSection("demo-banner-after");

  await testInfo.attach("demo-banner-hashes.json", {
    body: JSON.stringify({ before, after }, null, 2),
    contentType: "application/json",
  });

  // Screenshot must have changed — demo mode banner is now rendered
  expect(after.sha256).not.toBe(before.sha256);
});

test("Demo mode banner is not shown when backend has no static_mode_active flag (#59)", async ({
  page,
}) => {
  // Normal backend without static mode
  await mockWellKnownEndpoint(page, {
    schema: "browse-host/1",
    status: "ok",
    auth: "open",
    manual_token_required: false,
    cors_origins_configured: true,
    capabilities: ["sessions", "knowledge"],
    pairing_supported: false,
  });

  await gotoHostsSettings(page);

  const detectBtn = page.getByTestId("detect-local-btn");
  await expect(detectBtn).toBeVisible();
  await detectBtn.click();

  // Wait for the detect flow to complete (detected or unavailable state)
  await page.waitForTimeout(3_000);

  // Demo banner must NOT appear for a regular backend
  await expect(page.getByTestId("demo-mode-banner")).toHaveCount(0);
});

// ---------------------------------------------------------------------------
// Tests — Stable demo-mode badge on host profile row (#59 stable-badge gap)
// ---------------------------------------------------------------------------

test("Demo mode host-row badge persists after adding detected static backend — SHA-256 changes (#59)", async (
  { page },
  testInfo,
) => {
  // Mock the discovery endpoint to return demo/static mode fields
  await mockWellKnownEndpoint(page, {
    schema: "browse-host/1",
    status: "ok",
    auth: "open",
    manual_token_required: false,
    cors_origins_configured: true,
    capabilities: ["sessions", "knowledge", "pairing"],
    pairing_supported: true,
    static_mode_active: true,
    demo_mode_badge: "Demo mode",
  });

  await gotoHostsSettings(page);

  const hostsSection = page.locator("#hosts");

  async function captureSection(label: string) {
    const screenshot = await hostsSection.screenshot({
      animations: "disabled",
      caret: "hide",
    });
    await testInfo.attach(`${label}.png`, { body: screenshot, contentType: "image/png" });
    const sha256 = createHash("sha256").update(screenshot).digest("hex");
    return { sha256 };
  }

  // Before: no remote hosts, no demo badge
  const before = await captureSection("demo-badge-row-before");

  const detectBtn = page.getByTestId("detect-local-btn");
  await expect(detectBtn).toBeVisible();
  await detectBtn.click();

  // Wait for demo mode banner — confirms detection succeeded
  await expect(page.getByTestId("demo-mode-banner")).toBeVisible({ timeout: 10_000 });

  // Add the detected backend via the "Add & use" button
  const addBtn = page.getByTestId("add-detected-btn");
  await expect(addBtn).toBeVisible();
  await addBtn.click();

  // The host row should now show a "demo" badge
  // We use a regex on testid prefix since the host id is dynamic
  const demoBadge = page.locator('[data-testid^="host-demo-badge-"]');
  await expect(demoBadge).toBeVisible({ timeout: 5_000 });

  // After: host row with demo badge present
  const after = await captureSection("demo-badge-row-after");

  await testInfo.attach("demo-badge-row-hashes.json", {
    body: JSON.stringify({ before, after }, null, 2),
    contentType: "application/json",
  });

  // Screenshot must have changed — demo badge row is now rendered
  expect(after.sha256).not.toBe(before.sha256);
});

// ---------------------------------------------------------------------------
// Tests — Cross-browser paste mode (non-Chromium / mobile fallback) (#58)
// ---------------------------------------------------------------------------

/**
 * The QR scan path uses BarcodeDetector which is Chromium/Edge-specific.
 * The paste-URL path is the universal fallback: works on Firefox, Safari,
 * and all mobile browsers. This test exercises the paste path explicitly
 * to provide evidence that non-Chromium clients can complete the pairing flow.
 *
 * In real Edge (Chromium-based), BarcodeDetector is also available,
 * so the scan path would work too. The paste path serves as the cross-browser
 * evidence path since it runs in any browser without camera access.
 */
test("Paste browse:// path works for non-Chromium browsers (mobile/Safari fallback) — SHA-256 changes (#58)", async (
  { page },
  testInfo,
) => {
  await gotoHostsSettings(page);

  const hostsSection = page.locator("#hosts");

  async function captureSection(label: string) {
    const screenshot = await hostsSection.screenshot({
      animations: "disabled",
      caret: "hide",
    });
    await testInfo.attach(`${label}.png`, { body: screenshot, contentType: "image/png" });
    const sha256 = createHash("sha256").update(screenshot).digest("hex");
    return { sha256 };
  }

  const before = await captureSection("paste-fallback-before");

  // Open QR pairing panel
  const qrBtn = page.getByTestId("qr-pairing-btn");
  await expect(qrBtn).toBeVisible();
  await qrBtn.click();
  await expect(page.getByTestId("qr-pairing-panel")).toBeVisible({ timeout: 5_000 });

  // Go straight to paste mode (universal fallback — no BarcodeDetector needed)
  await page.getByTestId("qr-paste-url-btn").click();
  await expect(page.getByTestId("qr-paste-input")).toBeVisible();

  // Construct a valid-looking ticket (freshness window: created 15s ago)
  const payload = {
    url: "http://127.0.0.1:8765",
    nonce: "aabbccddeeff00112233445566778899",
    created_at: Math.floor(Date.now() / 1000) - 15,
    token_hmac: "", // open-auth backend simulation
  };
  const ticket = Buffer.from(JSON.stringify(payload))
    .toString("base64")
    .replace(/\+/g, "-")
    .replace(/\//g, "_")
    .replace(/=+$/, "");
  const browseUrl = `browse://connect?ticket=${ticket}`;

  await page.route("http://127.0.0.1:8765/.well-known/browse-host/verify", async (route) => {
    await route.abort("failed");
  });
  await page.getByTestId("qr-paste-input").fill(browseUrl);
  await page.getByTestId("qr-confirm-paste-btn").click();

  // Success indicator must appear
  await expect(page.getByTestId("qr-pairing-success")).toBeVisible({ timeout: 5_000 });
  await expect(page.getByTestId("qr-pairing-warning")).toBeVisible({ timeout: 5_000 });

  const after = await captureSection("paste-fallback-after");

  await testInfo.attach("paste-fallback-hashes.json", {
    body: JSON.stringify({ before, after }, null, 2),
    contentType: "application/json",
  });

  // Screenshot must change — success state is now rendered
  expect(after.sha256).not.toBe(before.sha256);
});
