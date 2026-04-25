import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  retries: 1,
  expect: {
    timeout: 15_000,
  },
  use: {
    baseURL: "http://127.0.0.1:8765",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [
    { name: "chromium", use: devices["Desktop Chrome"] },
  ],
  webServer: {
    command:
      "python3 ./scripts/create-e2e-db.py && python3 ../browse.py --port 8765 --db ./e2e/.fixtures/playwright.db",
    port: 8765,
    reuseExistingServer: false,
    timeout: 30_000,
  },
});
