import { defineConfig, devices } from "@playwright/test";
import path from "node:path";
// v0.1 product page against cmd/product; kept apart from the shared
// three-route suite (tests/), whose expected test count is fixed.
const reports =
  process.env.POC_BROWSER_REPORT_DIR ?? path.resolve("../../reports/browser/product");
export default defineConfig({
  testDir: "./tests-product",
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
    baseURL: "http://127.0.0.1:3000",
    ...devices["Desktop Chrome"],
    viewport: { width: 1440, height: 1000 },
    launchOptions: process.env.POC_CHROMIUM_PATH
      ? { executablePath: process.env.POC_CHROMIUM_PATH }
      : {},
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  webServer: {
    command: "npm run start",
    url: "http://127.0.0.1:3000",
    reuseExistingServer: false,
    timeout: 60000,
  },
});
