// Flow 1: plan a route through the place search (OP-406, acceptance criterion 1), and
// the kind of trip (OP-420): Walking, Walking and transit (the default) or Transit only.
import { expect, test, type Page } from "@playwright/test";
import { TRIP, blockMapTiles, choosePlace, expectRoutes, plannerUrl, recommendedRoute, routeLine } from "./helpers";

const TRIP_KIND = "What kind of trip?";
// Each radio is named by its label followed by its one-line description, so the names
// are matched from their start; "Walking" alone would also match "Walking and transit".
const KIND = {
  walk: /^Walking(?! and)/,
  transit: /^Walking and transit/,
  only: /^Transit only/
} as const;
type Kind = keyof typeof KIND;

// The API's words when Transit only had to fall back to walking routes, and the page's
// own when the API has no transit planner at all (web/src/components/planner-page.tsx).
const NO_TRANSIT = "No bus or LRT trip was found for this journey; these are walking routes.";
const UNAVAILABLE = "LRT and bus trips are not available right now. These are walking routes.";

function tripKind(page: Page, kind: Kind) {
  return page.getByRole("group", { name: TRIP_KIND }).getByRole("radio", { name: KIND[kind] });
}

/**
 * Choose a kind of trip. Right after a page load the server's HTML is on screen before
 * React has hydrated the control, and a click that early is undone when hydration
 * restores the server's state; the page gives no signal when it is ready. So the choice
 * is retried until it holds, as a person's second tap would.
 */
async function chooseTripKind(page: Page, kind: Kind): Promise<void> {
  const radio = tripKind(page, kind);
  await expect(async () => {
    await radio.check();
    await expect(radio).toBeChecked({ timeout: 1_000 });
  }).toPass({ timeout: 10_000 });
}

/** The query of the GET /api/route request that `action` makes the page send. */
async function routeQuery(page: Page, action: () => Promise<void>): Promise<URLSearchParams> {
  const [request] = await Promise.all([
    page.waitForRequest((candidate) => candidate.url().includes("/api/route?")),
    action()
  ]);
  return new URL(request.url()).searchParams;
}

function addressQuery(page: Page): URLSearchParams {
  return new URL(page.url()).searchParams;
}

test("plan a route from the place search to the route list", async ({ page }) => {
  await blockMapTiles(page);
  await page.goto("/");
  await expect(page.getByRole("heading", { level: 1, name: "Where are you going?" })).toBeVisible();

  await choosePlace(page, "From", TRIP.from);
  await choosePlace(page, "To", TRIP.to);
  await page.getByRole("radio", { name: TRIP.profileLabel }).check();
  // Walking and transit is the default (lead decision, November 9). This flow checks the
  // walking route's heading and its Save action, which a transit trip does not have, so
  // it chooses Walking first: that request carries no mode and no transit parameter.
  await expect(tripKind(page, "transit")).toBeChecked();
  await chooseTripKind(page, "walk");
  const query = await routeQuery(page, () => page.getByRole("button", { name: "Find routes" }).click());
  expect(query.get("mode")).toBeNull();
  expect(query.get("transit")).toBeNull();

  await expectRoutes(page, TRIP);
  await expect(page.getByRole("status").filter({ hasText: /\d+ routes? for a wheelchair/ })).toBeVisible();

  // The recommended route has an ETA in minutes and a distance, and is step-free.
  await expect(recommendedRoute(page)).toHaveText(/^(\d+ h( \d+ min)?|\d+ min), \d+(\.\d+)? (m|km), step-free$/);

  // The route is also text: the line lists the start, every condition and the end.
  const line = routeLine(page);
  const stops = line.getByRole("listitem");
  expect(await stops.count()).toBeGreaterThanOrEqual(2);
  await expect(stops.first()).toContainText(TRIP.from.name);
  await expect(stops.last()).toContainText(TRIP.to.name);

  // Every route offers its full report, the save action and the Maps hand-off.
  await expect(page.getByRole("link", { name: /^View route report for route 1$/ })).toBeVisible();
  await expect(page.getByRole("button", { name: /^Save this route\s*, route 1$/ })).toBeVisible();
  await expect(page.getByRole("link", { name: /^Open in Google Maps\s*, route 1$/ })).toHaveAttribute(
    "href",
    /^https:\/\/www\.google\.com\/maps\/dir\/\?api=1/
  );

  // Changing the trip clears the routes, so what is shown always matches the form.
  await page.getByRole("radio", { name: "Standard walking" }).check();
  await expect(recommendedRoute(page)).toHaveCount(0);
});

// OP-420 (lead decision, November 9): Walking and transit is the default, a change is
// kept on the device, and a trip link decides for itself (web/src/lib/transit-choice.ts).
test("the kind of trip is Walking and transit by default, remembers a change, and follows a trip link", async ({
  page
}) => {
  await blockMapTiles(page);
  await page.goto("/");
  await expect(tripKind(page, "transit")).toBeChecked();

  await chooseTripKind(page, "only");
  await expect(tripKind(page, "only")).toBeChecked();
  expect(await page.evaluate(() => window.localStorage.getItem("openpath.transit"))).toBe("only");
  await page.reload();
  await expect(page.getByRole("heading", { level: 1, name: "Where are you going?" })).toBeVisible();
  await expect(tripKind(page, "only")).toBeChecked();

  // A trip link decides for itself, whatever is saved: mode=walk_lrt alone is Walking
  // and transit, with transit=only it is Transit only, and without a mode it is Walking.
  await page.goto(`${plannerUrl(TRIP)}&mode=walk_lrt`);
  await expect(tripKind(page, "transit")).toBeChecked();
  await page.goto(`${plannerUrl(TRIP)}&mode=walk_lrt&transit=only`);
  await expect(tripKind(page, "only")).toBeChecked();
  await page.goto(plannerUrl(TRIP));
  await expect(tripKind(page, "walk")).toBeChecked();

  // Links never touch the saved choice: a bare planner still opens with Transit only.
  await page.goto("/");
  await expect(tripKind(page, "only")).toBeChecked();

  // Walking is remembered too.
  await chooseTripKind(page, "walk");
  await page.goto("/");
  await expect(tripKind(page, "walk")).toBeChecked();

  // The values OP-419's two-state box saved still read: on is Walking and transit, off is Walking.
  await page.evaluate(() => window.localStorage.setItem("openpath.transit", "on"));
  await page.reload();
  await expect(tripKind(page, "transit")).toBeChecked();
  await page.evaluate(() => window.localStorage.setItem("openpath.transit", "off"));
  await page.reload();
  await expect(tripKind(page, "walk")).toBeChecked();
});

test("each kind of trip asks the API for the right routes and is kept in the address", async ({ page }) => {
  await blockMapTiles(page);
  await page.goto("/");
  await choosePlace(page, "From", TRIP.from);
  await choosePlace(page, "To", TRIP.to);
  const find = () => page.getByRole("button", { name: "Find routes" }).click();

  // Walking: no mode and no transit parameter, as before OP-311.
  await chooseTripKind(page, "walk");
  let query = await routeQuery(page, find);
  expect(query.get("mode")).toBeNull();
  expect(query.get("transit")).toBeNull();
  await expectRoutes(page, TRIP);
  expect(addressQuery(page).get("mode")).toBeNull();
  expect(addressQuery(page).get("transit")).toBeNull();

  // Walking and transit: walk_lrt with transit=always, so the API shows the best bus or
  // LRT trip even when it is slower. The address carries mode alone, as it did before.
  await chooseTripKind(page, "transit");
  await expect(recommendedRoute(page)).toHaveCount(0);
  query = await routeQuery(page, find);
  expect(query.get("mode")).toBe("walk_lrt");
  expect(query.get("transit")).toBe("always");
  await expectRoutes(page, TRIP);
  expect(addressQuery(page).get("mode")).toBe("walk_lrt");
  expect(addressQuery(page).get("transit")).toBeNull();

  // Transit only: walk_lrt with transit=only, and the address says so.
  await chooseTripKind(page, "only");
  await expect(recommendedRoute(page)).toHaveCount(0);
  query = await routeQuery(page, find);
  expect(query.get("mode")).toBe("walk_lrt");
  expect(query.get("transit")).toBe("only");
  await expectRoutes(page, TRIP);
  expect(addressQuery(page).get("mode")).toBe("walk_lrt");
  expect(addressQuery(page).get("transit")).toBe("only");
});

test("Transit only shows the API's warning as the note when it fell back to walking routes", async ({ page }) => {
  await blockMapTiles(page);
  // The replay API plans no transit trip here, so the fallback is staged from its real
  // answer: the walking routes with `mode` walk_lrt and the warning the API sends in that
  // case (cloud/app/routing_api.py, NO_TRANSIT_WARNING), followed by the planner's reason.
  await page.route(/\/api\/route\?/, async (route) => {
    const response = await route.fetch();
    const body = (await response.json()) as { mode?: string; warnings: string[] };
    body.mode = "walk_lrt";
    body.warnings = [
      ...body.warnings,
      NO_TRANSIT,
      "No LRT station or bus stop is within 1.2 km of the start on paths this profile can use."
    ];
    await route.fulfill({ response, json: body });
  });
  await page.goto(`${plannerUrl(TRIP)}&mode=walk_lrt&transit=only`);
  await expect(tripKind(page, "only")).toBeChecked();
  await expectRoutes(page, TRIP);

  const notes = page.getByRole("list", { name: "Notes about this trip" });
  await expect(notes.getByRole("listitem").filter({ hasText: NO_TRANSIT })).toHaveCount(1);
  await expect(notes.getByRole("listitem").filter({ hasText: UNAVAILABLE })).toHaveCount(0);
  // The walking routes are still whole routes, with their report and Maps hand-off.
  await expect(page.getByRole("link", { name: /^View route report for route 1$/ })).toBeVisible();
  await expect(page.getByRole("link", { name: /^Open in Google Maps\s*, route 1$/ })).toBeVisible();
});
