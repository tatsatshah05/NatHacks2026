// Flow 1: plan a route through the place search (OP-406, acceptance criterion 1).
import { expect, test } from "@playwright/test";
import { TRIP, blockMapTiles, choosePlace, expectRoutes, plannerUrl, recommendedRoute, routeLine } from "./helpers";

const TRANSIT = "Include LRT and bus trips";

test("plan a route from the place search to the route list", async ({ page }) => {
  await blockMapTiles(page);
  await page.goto("/");
  await expect(page.getByRole("heading", { level: 1, name: "Where are you going?" })).toBeVisible();

  await choosePlace(page, "From", TRIP.from);
  await choosePlace(page, "To", TRIP.to);
  await page.getByRole("radio", { name: TRIP.profileLabel }).check();
  // Transit is on by default (lead decision, November 9). This flow checks the walking
  // route's heading and its Save action, which a transit route does not have, so it
  // turns the box off first.
  const transit = page.getByRole("checkbox", { name: TRANSIT });
  await expect(transit).toBeChecked();
  await transit.uncheck();
  await expect(transit).not.toBeChecked();
  await page.getByRole("button", { name: "Find routes" }).click();

  await expectRoutes(page, TRIP);
  await expect(page.getByRole("status").filter({ hasText: /\d+ routes? for a wheelchair/ })).toBeVisible();

  // The recommended route has an ETA in minutes and a distance, and is step-free.
  await expect(recommendedRoute(page)).toHaveText(/^\d+ min, \d+(\.\d+)? (m|km), step-free$/);

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

// Lead decision, November 9: transit is on by default, a change is kept on the device,
// and a trip link still decides the box for itself (web/src/lib/transit-choice.ts).
test("Include LRT and bus trips is on by default, remembers being turned off, and follows a trip link", async ({ page }) => {
  await blockMapTiles(page);
  await page.goto("/");
  const transit = page.getByRole("checkbox", { name: TRANSIT });
  await expect(transit).toBeChecked();

  await transit.uncheck();
  await expect(transit).not.toBeChecked();
  expect(await page.evaluate(() => window.localStorage.getItem("openpath.transit"))).toBe("off");
  await page.reload();
  await expect(page.getByRole("heading", { level: 1, name: "Where are you going?" })).toBeVisible();
  await expect(transit).not.toBeChecked();

  // A trip link without mode=walk_lrt turns the box off, with it on, whatever is saved.
  await page.goto(`${plannerUrl(TRIP)}&mode=walk_lrt`);
  await expect(transit).toBeChecked();
  await page.goto(plannerUrl(TRIP));
  await expect(transit).not.toBeChecked();

  // Turning it on is remembered too, and a bare planner opens with it.
  await transit.check();
  await page.goto("/");
  await expect(transit).toBeChecked();
});
