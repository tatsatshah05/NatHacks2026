// Flow 4: save a route and find it on the Alerts page (OP-406, criterion 1).
//
// The saved list belongs to the device's anonymous id (localStorage
// "openpath.client_id", web/src/lib/client-id.ts), which survives a reload within
// the same browser context. The test removes what it saved through the API after.
import { expect, test } from "@playwright/test";
import { API_URL, TRIP, openPlannedTrip } from "./helpers";

type SavedRoute = { id: number; name: string };

test("save a route with a name and find it on the Alerts page after a reload", async ({ page, request }, testInfo) => {
  const name = `E2E ${testInfo.project.name} ${Date.now()}`;
  await openPlannedTrip(page, TRIP);

  await page.getByRole("button", { name: /^Save this route\s*, route 1$/ }).click();
  const form = page.getByRole("form", { name: "Save route 1" });
  const nameField = form.getByRole("textbox", { name: "Name for this route" });
  await expect(nameField).toBeFocused();
  await expect(nameField).toHaveValue(TRIP.to.name); // filled with the destination
  await nameField.fill(name);

  // Without alerts, so no browser permission prompt: "Save without alerts" when the
  // server can send alerts, otherwise the form's only save button, "Save route".
  const withoutAlerts = form.getByRole("button", { name: "Save without alerts" });
  if ((await withoutAlerts.count()) > 0) await withoutAlerts.click();
  else await form.getByRole("button", { name: "Save route", exact: true }).click();

  // The result paragraph: "Saved as <name>. ..." or "Saved. You'll get an alert ...".
  // (Its "Saved" is a <strong>, so the pattern needs the words after it.)
  const result = page.getByText(/^Saved(\.| as) .+/);
  await expect(result).toBeVisible();
  await expect(result).toBeFocused();
  await expect(page.getByRole("link", { name: "See your saved routes" })).toBeVisible();

  const clientId = await page.evaluate(() => window.localStorage.getItem("openpath.client_id"));
  expect(clientId, "the anonymous client id is kept on the device").toBeTruthy();

  try {
    await page.goto("/alerts");
    await page.reload();
    await expect(page.getByRole("heading", { level: 1, name: "Alerts" })).toBeVisible();
    await expect(page.getByRole("status").filter({ hasText: /\d+ saved routes?\./ })).toBeVisible();
    const saved = page.getByRole("listitem").filter({ has: page.getByRole("heading", { level: 3, name }) });
    await expect(saved).toHaveCount(1);
    await expect(saved).toContainText(TRIP.profileLabel);
    await expect(saved.getByText(/^(Clear|Changed|No route)/)).toBeVisible();
    await expect(saved.getByRole("link", { name: `Open route ${name}` })).toBeVisible();
    await expect(saved.getByRole("button", { name: `Remove ${name}` })).toBeVisible();
  } finally {
    // Leave the API as it was: remove what this test saved.
    const list = await request.get(`${API_URL}/api/saved-routes?client_id=${clientId}`);
    if (list.ok()) {
      const { routes } = (await list.json()) as { routes: SavedRoute[] };
      for (const route of routes.filter((item) => item.name === name)) {
        await request.delete(`${API_URL}/api/saved-routes/${route.id}?client_id=${clientId}`);
      }
    }
  }
});
