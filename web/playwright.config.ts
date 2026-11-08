// OP-406: end-to-end tests of the critical path (web/e2e/), run by `make e2e`.
//
// The web app under test is at E2E_WEB_URL and the API at E2E_API_URL (scripts/e2e.py
// sets both when it starts them; the deploy job points them at production for the
// @smoke subset). Two projects: a phone (390 x 844, touch) and a laptop (1280 x 800).
// In CI a failed test is retried once with a trace, screenshots are kept on failure,
// the list reporter prints the summary and the test-results folder is uploaded.
import { defineConfig } from "@playwright/test";

const webUrl = process.env.E2E_WEB_URL ?? "http://localhost:3140";
const ci = Boolean(process.env.CI);

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: true,
  forbidOnly: ci,
  retries: ci ? 1 : 0,
  workers: ci ? 2 : undefined,
  // The list reporter in CI; locally also an HTML report (`npx playwright show-report`).
  reporter: ci ? "list" : [["list"], ["html", { open: "never" }]],
  timeout: 90_000,
  expect: { timeout: 15_000 },
  outputDir: "test-results",
  use: {
    baseURL: webUrl,
    trace: "on-first-retry",
    video: "on-first-retry",
    // Playwright has no "on first retry" for screenshots; failures keep one.
    screenshot: "only-on-failure",
    locale: "en-CA",
    timezoneId: "America/Edmonton"
  },
  projects: [
    {
      name: "phone",
      use: {
        browserName: "chromium",
        viewport: { width: 390, height: 844 },
        deviceScaleFactor: 3,
        isMobile: true,
        hasTouch: true
      }
    },
    {
      name: "laptop",
      use: {
        browserName: "chromium",
        viewport: { width: 1280, height: 800 }
      }
    }
  ]
});
