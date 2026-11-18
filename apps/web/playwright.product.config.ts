import { defineConfig, devices } from "@playwright/test";
import path from "node:path";
// v0.1 product page against cmd/product; kept apart from the shared
// three-route suite (tests/), whose expected test count is fixed.
// PRODUCT_WEB_PORT lets concurrent runs use different ports.
const webPort = process.env.PRODUCT_WEB_PORT ?? "3000";
const reports =
  process.env.POC_BROWSER_REPORT_DIR ?? path.resolve("../../reports/browser/product");
export default defineConfig({
  testDir: "./tests-product",
  forbidOnly: true, // test.only would silently skip the rest
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 30000,
  globalTimeout: 180000,
  reporter: [
    ["list"],
    ["json", { outputFile: path.join(reports, "results.json") }],
  ],
  outputDir: path.join(reports, "artifacts"),
  use: {
    baseURL: `http://127.0.0.1:${webPort}`,
    ...devices["Desktop Chrome"],
    viewport: { width: 1440, height: 1000 },
    launchOptions: process.env.POC_CHROMIUM_PATH
      ? { executablePath: process.env.POC_CHROMIUM_PATH }
      : {},
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  webServer: {
    command: `npm run start -- -p ${webPort}`,
    url: `http://127.0.0.1:${webPort}`,
    reuseExistingServer: false,
    timeout: 60000,
  },
});
