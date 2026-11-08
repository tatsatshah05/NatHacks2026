// Flow 3: a seeded live change reaches the open planner (OP-406, criterion 1).
//
// The page follows its trip over WS /ws (OP-303). POST /api/admin/override puts a
// labelled "blocked" test item on a path the recommended route uses; the API rebuilds
// its hazard map at once, the hub sends route_changed, the page announces it in its
// aria-live="assertive" region and fetches the routes again.
import { expect, test } from "@playwright/test";
import { ADMIN_KEY, API_URL, fetchBestRoute, liveTrip, longestSegment, openPlannedTrip } from "./helpers";

const TITLE = "Sidewalk closed for the end-to-end test";
const headers = { "X-Admin-Key": ADMIN_KEY };

test("a seeded closure is announced and the routes refresh", async ({ page, request }, testInfo) => {
  const trip = liveTrip(testInfo.project.name);
  const ref = `e2e-${testInfo.project.name}-${Date.now()}`;

  await openPlannedTrip(page, trip);
  // The live connection is up before the change is seeded, in words, not colour.
  const liveLine = page.getByRole("region", { name: "Live updates" }).getByRole("status");
  await expect(liveLine).toContainText("Live.", { timeout: 30_000 });

  const best = await fetchBestRoute(request, trip);
  const created = await request.post(`${API_URL}/api/admin/override`, {
    headers,
    data: { source_ref: ref, title: TITLE, geometry: longestSegment(best), ttl_s: 300 }
  });
  expect(created.status(), await created.text()).toBe(201);
  const body = (await created.json()) as { rebuilt: boolean; title: string };
  expect(body.rebuilt).toBe(true);
  expect(body.title).toBe(`Test override: ${TITLE}`);

  try {
    // The assertive region gets the one sentence a screen reader hears.
    const announcer = page.locator('[aria-live="assertive"]');
    await expect(announcer).toContainText("Route update:", { timeout: 60_000 });
    await expect(announcer).toContainText(`Test override: ${TITLE} now blocks your route`);

    // The routes are fetched again, and the new recommended route avoids the item.
    await expect(page.getByText("Routes checked again after the change.")).toBeVisible({ timeout: 30_000 });
    const routes = page.getByRole("region", { name: "Routes" });
    await expect(routes.getByText(`Test override: ${TITLE}`).first()).toBeVisible();
    await expect(routes.getByText(/^Avoided: path closed$/).first()).toBeVisible();
  } finally {
    const removed = await request.delete(`${API_URL}/api/admin/override/${ref}`, { headers });
    expect([204, 404]).toContain(removed.status());
  }
});
