// Flow 2: open the route report and read its four groups (OP-406, criterion 1).
import { expect, test } from "@playwright/test";
import { REPORT_GROUPS, TRIP, blockMapTiles, escapeRegExp, openPlannedTrip, reportUrl } from "./helpers";

test("the route report lists the four groups, each item with its source", async ({ page }) => {
  await openPlannedTrip(page, TRIP);
  await page.getByRole("link", { name: /^View route report for route 1$/ }).click();

  await expect(page).toHaveURL(/\/route\?/);
  await expect(page.getByRole("heading", { level: 1, name: `${TRIP.from.name} to ${TRIP.to.name}` })).toBeVisible();
  await expect(page.getByRole("heading", { level: 2, name: "Safety summary" })).toBeVisible();

  // The numbered text steps.
  const steps = page.getByRole("region", { name: "Steps for route 1" }).getByRole("listitem");
  expect(await steps.count()).toBeGreaterThanOrEqual(1);
  await expect(steps.first()).toHaveText(/\d+ m/);

  // The four fixed groups, always present and in order, each with its count.
  const conditions = page.getByRole("region", { name: "Conditions for route 1" });
  const headings = conditions.getByRole("heading", { level: 3 });
  const names = (await headings.allTextContents()).map((text) => text.replace(/\s*\(\d+\)\s*$/, "").trim());
  expect(names.slice(0, REPORT_GROUPS.length)).toEqual([...REPORT_GROUPS]);
  for (const group of REPORT_GROUPS) {
    await expect(
      conditions.getByRole("heading", { level: 3, name: new RegExp(`^${escapeRegExp(group)} \\(\\d+\\)$`) })
    ).toBeVisible();
  }

  // Every listed item names its source and attribution and links to the source.
  const items = conditions.getByRole("article");
  const count = await items.count();
  expect(count, "the recorded fixtures put a construction closure near this trip").toBeGreaterThan(0);
  for (let index = 0; index < count; index += 1) {
    const item = items.nth(index);
    const details = item.getByRole("list", { name: "Details" }).getByRole("listitem");
    await expect(details.filter({ hasText: /^Source: .+, .+$/ })).toHaveCount(1);
    await expect(details.filter({ hasText: /^(High|Medium|Low) confidence$/ })).toHaveCount(1);
    await expect(item.getByText(/^(On this route|Near this route|Avoided by this route)/)).toBeVisible();
  }

  // The sources checked and the Maps hand-off follow.
  await expect(conditions.getByRole("heading", { level: 3, name: "Sources checked" })).toBeVisible();
  await expect(page.getByRole("link", { name: /^Open in Google Maps\s*, route 1$/ })).toBeVisible();
});

// OP-420: the report reads the same `mode` and `transit` as the planner, so a Transit
// only link asks the API for the same routes and says so in its lead sentence.
test("a Transit only link asks the API for transit trips alone", async ({ page }) => {
  await blockMapTiles(page);
  const [request] = await Promise.all([
    page.waitForRequest((candidate) => candidate.url().includes("/api/route?")),
    page.goto(`${reportUrl(TRIP)}&mode=walk_lrt&transit=only`)
  ]);
  const query = new URL(request.url()).searchParams;
  expect(query.get("mode")).toBe("walk_lrt");
  expect(query.get("transit")).toBe("only");
  await expect(page.getByText("Route report for wheelchair, transit only.")).toBeVisible();
  await expect(page.getByRole("heading", { level: 2, name: "Steps for route 1" })).toBeVisible({ timeout: 30_000 });
  // Back to the planner keeps the choice in the link.
  await expect(page.getByRole("link", { name: "Back to the planner" })).toHaveAttribute(
    "href",
    /mode=walk_lrt&transit=only/
  );
});
