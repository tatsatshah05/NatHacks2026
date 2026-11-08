// The @smoke subset: safe against production (OP-406, criterion 2).
//
// The deploy job runs `npm run e2e:smoke` with E2E_WEB_URL and E2E_API_URL pointing
// at the live site after /health reports the new commit. These tests only read:
// no override, no saved route, no report. A failure fails the deploy job, which is
// the alert; nothing is rolled back.
import { expect, test } from "@playwright/test";
import { API_URL, TRIP, expectRoutes, plannerUrl } from "./helpers";

type Status = { mode: string; sources: Array<{ source: string }> };

test.describe("smoke", { tag: "@smoke" }, () => {
  test("the home page loads with its heading and navigation", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByRole("heading", { level: 1, name: "Where are you going?" })).toBeVisible();
    const nav = page.getByRole("navigation", { name: "Main" });
    for (const label of ["Plan", "City", "Alerts", "Settings", "About"]) {
      await expect(nav.getByRole("link", { name: label, exact: true })).toBeVisible();
    }
    await expect(page.getByRole("button", { name: "Find routes" })).toBeVisible();
  });

  test("the API reports its sources", async ({ request }) => {
    const response = await request.get(`${API_URL}/api/status`);
    expect(response.status()).toBe(200);
    const body = (await response.json()) as Status;
    expect(["live", "replay"]).toContain(body.mode);
    expect(body.sources.length).toBeGreaterThan(0);
    expect(body.sources.map((source) => source.source)).toContain("city_lrt_devices");
  });

  test("a route plans between two fixed places", async ({ page }) => {
    await page.goto(plannerUrl(TRIP));
    // The free API instance can be slow to answer its first route after a deploy.
    await expectRoutes(page, TRIP, 60_000);
    await expect(page.getByRole("link", { name: /^View route report for route 1$/ })).toBeVisible();
  });
});
