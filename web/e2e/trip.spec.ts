// OP-419: Start a trip on the route report. The trip speaks the steps and follows the
// GPS on its own (headless Chromium refuses geolocation, so the steps move by hand
// here); nothing goes to the API until PIP is turned on, which guide.spec.ts covers.
import { expect, test } from "@playwright/test";
import {
  START_ROUTE,
  TRIP,
  currentStep,
  openPlannedTrip,
  openReport,
  recordSpeech,
  spokenSoFar,
  startTrip
} from "./helpers";

const READY = "Ready to start. Press Start route.";
const HELP = "PIP describes what the camera sees. It does not decide for you.";
const PIP_START_HELP = "PIP starts the trip and describes what the camera sees. It does not decide for you.";

test("Start shows the first step large and speaks it, posts nothing, and Stop returns the report", async ({ page }) => {
  await recordSpeech(page);
  const frames: string[] = [];
  page.on("request", (request) => {
    if (request.url().includes("/api/guide/frame")) frames.push(request.url());
  });
  await openReport(page);

  // Before Start: the report as before, plus one row with the primary Start route and
  // PIP beside it (the one-tap start with the camera, guide.spec.ts). No trip yet.
  const start = page.getByRole("button", { name: START_ROUTE });
  await expect(start).toBeVisible();
  const box = await start.boundingBox();
  expect(box?.height ?? 0, "Start route is at least 44 px tall").toBeGreaterThanOrEqual(44);
  expect(box?.width ?? 0, "Start route is at least 44 px wide").toBeGreaterThanOrEqual(44);
  const topPip = page.getByRole("button", { name: "PIP", exact: true });
  await expect(topPip).toHaveCount(1);
  await expect(topPip).toHaveAccessibleDescription(PIP_START_HELP);
  const pipBox = await topPip.boundingBox();
  expect(pipBox?.height ?? 0, "PIP is at least 44 px tall").toBeGreaterThanOrEqual(44);
  expect(pipBox?.width ?? 0, "PIP is at least 44 px wide").toBeGreaterThanOrEqual(44);
  // PIP sits beside Start route or wraps under it, never before it; nothing asks to save first.
  expect(pipBox?.y ?? 0, "PIP is not above Start route").toBeGreaterThanOrEqual((box?.y ?? 0) - 1);
  expect(pipBox?.x ?? 0, "PIP comes after Start route").toBeGreaterThan((box?.x ?? 0) - 1);
  await expect(page.getByRole("region", { name: "Your trip" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Stop PIP", exact: true })).toHaveCount(0);
  const steps = page.getByRole("region", { name: "Steps for route 1" }).getByRole("listitem");
  const stepsBefore = await steps.allTextContents();
  expect(stepsBefore.length).toBeGreaterThanOrEqual(1);
  const headingsBefore = await page.getByRole("heading", { level: 2 }).allTextContents();
  expect(headingsBefore).toContain("Steps for route 1");
  expect(headingsBefore).toContain("Conditions for route 1");
  expect(headingsBefore).toContain("Open in a maps app");

  const trip = await startTrip(page);
  const step = currentStep(trip);
  await expect(step).toHaveText(new RegExp(`^Step 1 of ${stepsBefore.length}: .+\\.$`));
  const stop = trip.getByRole("button", { name: "Stop", exact: true });
  await expect(stop).toBeFocused();
  expect((await stop.boundingBox())?.height ?? 0, "Stop is large").toBeGreaterThanOrEqual(56);
  await expect(trip.getByRole("heading", { level: 3, name: "PIP, the camera guide (preview)" })).toBeVisible();
  await expect(trip.getByText(HELP)).toBeVisible();
  await expect(trip.getByRole("button", { name: "PIP", exact: true })).toBeVisible();

  // The first step went through speechSynthesis (the speech setting is on by default) and is listed.
  const spoken = await spokenSoFar(page);
  expect(spoken).toHaveLength(1);
  expect(spoken[0]).toMatch(/^Step 1 of \d+: /);
  await expect(trip.getByRole("list", { name: "Said so far" }).getByRole("listitem")).toHaveText([spoken[0]]);

  // Next step moves the status line and is spoken. The route's own step list is untouched.
  await trip.getByRole("button", { name: "Next step" }).click();
  const next = stepsBefore.length > 1 ? /^Step 2 of \d+: / : /^You're at the end of the route, /;
  await expect(step).toHaveText(next);
  expect((await spokenSoFar(page))[1]).toMatch(next);
  expect(await steps.allTextContents()).toEqual(stepsBefore);
  const headingsDuring = await page.getByRole("heading", { level: 2 }).allTextContents();
  expect(headingsDuring).toContain("Your trip");
  for (const heading of headingsBefore) expect(headingsDuring).toContain(heading);

  // Nothing is posted while PIP is off: more than one frame interval passes without a request.
  await page.waitForTimeout(2500);
  expect(frames).toEqual([]);

  // Stop returns the page to the report state, with focus on Start route and PIP beside it again.
  await stop.click();
  await expect(page.getByRole("region", { name: "Your trip" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: START_ROUTE })).toBeFocused();
  await expect(page.getByRole("button", { name: "PIP", exact: true })).toHaveCount(1);
  expect(await page.getByRole("heading", { level: 2 }).allTextContents()).toEqual(headingsBefore);
  expect(frames).toEqual([]);
});

test("the planner's Start action opens the report with Start route focused, and starts nothing", async ({ page }) => {
  await recordSpeech(page);
  await openPlannedTrip(page, TRIP);
  // Start comes first in the route's actions; the others keep their order after it.
  const actions = page.getByRole("link", { name: /^(Start route\s*1|View route report for route 1|Open in Google Maps\s*, route 1)$/ });
  const names = await actions.allTextContents();
  expect(names[0]).toMatch(/^Start route\s*1$/);
  expect(names[1]).toMatch(/^View route report for route 1$/);
  await actions.first().click();

  await expect(page).toHaveURL(/\/route\?.*\bstart=1/);
  const start = page.getByRole("button", { name: START_ROUTE });
  await expect(start).toBeVisible({ timeout: 30_000 });
  await expect(start).toBeFocused();
  // Start route takes the focus, not the PIP button beside it.
  await expect(page.getByRole("button", { name: "PIP", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "PIP", exact: true })).not.toBeFocused();
  await expect(page.getByRole("status").filter({ hasText: READY })).toBeVisible();
  // No trip, no speech, no camera: those wait for a tap on this page.
  await expect(page.getByRole("region", { name: "Your trip" })).toHaveCount(0);
  expect(await spokenSoFar(page)).toEqual([]);
  // The report is otherwise the usual one.
  await expect(page.getByRole("heading", { level: 2, name: "Steps for route 1" })).toBeVisible();
  await expect(page.getByRole("heading", { level: 2, name: "Conditions for route 1" })).toBeVisible();
});
