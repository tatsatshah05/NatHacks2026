// OP-418 and OP-419: PIP, the camera guide, inside a started trip on the route report.
// The replay API answers 503 for POST /api/guide/frame by design, so the described-frame
// test fulfils that request itself; the camera is stubbed in the page (headless Chromium
// has none). The trip itself is covered in trip.spec.ts.
import { expect, test, type Locator, type Page } from "@playwright/test";
import {
  START_ROUTE,
  currentStep,
  denyCamera,
  fakeCamera,
  openReport,
  recordSpeech,
  spokenSoFar,
  startTrip
} from "./helpers";

const PIP_TITLE = "PIP, the camera guide (preview)";
const HELP = "PIP describes what the camera sees. It does not decide for you.";
const PROMISE = "PIP does not replace a cane or a guide dog.";
const NOTICE =
  "Your camera's view and your current route step are sent to Anthropic to be described and are not stored. " +
  "Your position stays with OpenPath and is not sent to Anthropic. People may appear in the view. Press Stop PIP at any time.";
const PIP_START_HELP = "PIP starts the trip and describes what the camera sees. It does not decide for you.";
const UNAVAILABLE_STATUS = "Camera guidance unavailable, reading route steps";
const CAMERA_DENIED = "Camera not available. I'll read the route steps.";
const UNAVAILABLE = "Camera guidance is not available right now. The route steps still work.";
const SENTENCE = "I see a glass door about two metres ahead on your left.";

/** Start the trip, press PIP with the keyboard and expect the notice with Start PIP focused. */
async function openPip(page: Page): Promise<{ trip: Locator; pip: Locator }> {
  const trip = await startTrip(page);
  const pip = trip.getByRole("region", { name: PIP_TITLE });
  await expect(pip.getByText(HELP)).toBeVisible();
  await expect(pip.getByText(PROMISE)).toBeVisible();
  const button = pip.getByRole("button", { name: "PIP", exact: true });
  await expect(button).toBeVisible();
  const box = await button.boundingBox();
  expect(box?.height ?? 0, "PIP is at least 44 px tall").toBeGreaterThanOrEqual(44);
  expect(box?.width ?? 0, "PIP is at least 44 px wide").toBeGreaterThanOrEqual(44);

  await button.focus();
  await page.keyboard.press("Enter");
  await expect(pip.getByText(NOTICE)).toBeVisible();
  await expect(pip.getByRole("button", { name: "Start PIP", exact: true })).toBeFocused();
  await expect(pip.getByText(HELP), "the help stays while the notice is open").toBeVisible();
  return { trip, pip };
}

test("PIP shows the notice once; with the camera refused it says so and the trip goes on", async ({ page }) => {
  await recordSpeech(page);
  await denyCamera(page);
  await openReport(page);
  const { trip, pip } = await openPip(page);

  // Escape closes the notice and returns focus to PIP; the trip is untouched.
  await page.keyboard.press("Escape");
  await expect(pip.getByText(NOTICE)).toHaveCount(0);
  const button = pip.getByRole("button", { name: "PIP", exact: true });
  await expect(button).toBeFocused();
  await expect(currentStep(trip)).toHaveText(/^Step 1 of \d+: /);

  // PIP again: the notice is still owed, because it was never accepted.
  await button.click();
  await expect(pip.getByText(NOTICE)).toBeVisible();
  await pip.getByRole("button", { name: "Start PIP", exact: true }).click();

  const stopPip = pip.getByRole("button", { name: "Stop PIP", exact: true });
  await expect(stopPip).toBeFocused();
  await expect(pip.getByRole("status")).toHaveText(UNAVAILABLE_STATUS);
  const transcript = trip.getByRole("list", { name: "Said so far" });
  await expect(transcript.getByRole("listitem").first()).toHaveText(/^Step 1 of \d+: .+\.$/);
  await expect(transcript.getByRole("listitem").filter({ hasText: CAMERA_DENIED })).toHaveCount(1);
  await expect(pip.getByText(HELP)).toBeVisible();

  // Both sentences went through speechSynthesis, the camera's one unchanged (no "PIP says").
  const spoken = await spokenSoFar(page);
  expect(spoken[0]).toMatch(/^Step 1 of \d+: /);
  expect(spoken).toContain(CAMERA_DENIED);

  // Stop PIP turns the camera off; the trip and its current step stay.
  await stopPip.click();
  await expect(button).toBeFocused();
  await expect(pip.getByRole("status")).toHaveCount(0);
  await expect(currentStep(trip)).toHaveText(/^Step 1 of \d+: /);

  // The notice was accepted once in this trip, so PIP now goes straight to the camera.
  await button.click();
  await expect(pip.getByText(NOTICE)).toHaveCount(0);
  await expect(pip.getByRole("status")).toHaveText(UNAVAILABLE_STATUS);

  // Stop ends both: the report is back, with focus on Start route.
  await trip.getByRole("button", { name: "Stop", exact: true }).click();
  await expect(page.getByRole("region", { name: "Your trip" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: START_ROUTE })).toBeFocused();
});

test("a described frame is spoken as it is and listed as PIP's; Stop PIP leaves the trip running", async ({ page }) => {
  await recordSpeech(page);
  await fakeCamera(page);

  const requests: Array<{ contentType: string; context: unknown }> = [];
  await page.route("**/api/guide/frame", async (route) => {
    const request = route.request();
    const body = request.postDataBuffer()?.toString("latin1") ?? "";
    const match = body.match(/name="context"\r\n\r\n([\s\S]*?)\r\n--/);
    requests.push({
      contentType: request.headers()["content-type"] ?? "",
      context: match ? (JSON.parse(Buffer.from(match[1], "latin1").toString("utf8")) as unknown) : null
    });
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      headers: { "access-control-allow-origin": "*" },
      body: JSON.stringify({
        say: SENTENCE,
        hazards: [{ kind: "door", where: "left", text: "glass door" }],
        landmarks: ["glass door"],
        confidence: "medium",
        checked_by: ["claude"],
        latency_ms: 900,
        mode: "live"
      })
    });
  });

  await openReport(page);
  const { trip, pip } = await openPip(page);
  await pip.getByRole("button", { name: "Start PIP", exact: true }).click();
  await expect(pip.getByRole("status")).toHaveText("PIP is on");

  const transcript = trip.getByRole("list", { name: "Said so far" });
  const line = transcript.getByRole("listitem").filter({ hasText: SENTENCE });
  await expect(line).toHaveCount(1, { timeout: 20_000 });
  await expect(line).toHaveText(`PIP: ${SENTENCE}`);
  const spoken = await spokenSoFar(page);
  expect(spoken).toContain(SENTENCE);
  expect(spoken).not.toContain(`PIP: ${SENTENCE}`);

  // The frame went as multipart with the route context beside it.
  expect(requests.length).toBeGreaterThanOrEqual(1);
  expect(requests[0].contentType).toMatch(/^multipart\/form-data; boundary=/);
  const context = requests[0].context as Record<string, unknown>;
  expect(context.profile).toBe("wheelchair");
  expect(typeof context.client_id).toBe("string");
  expect(context.step).toMatch(/\d+ m/);
  expect(Array.isArray(context.nearby)).toBe(true);
  expect(context.previous).toBeNull();

  // Stop PIP: no more frames; the trip, its current step and its Stop stay.
  await pip.getByRole("button", { name: "Stop PIP", exact: true }).click();
  await expect(pip.getByRole("button", { name: "PIP", exact: true })).toBeFocused();
  const sent = requests.length;
  await page.waitForTimeout(2500);
  expect(requests.length).toBe(sent);
  await expect(currentStep(trip)).toHaveText(/^Step 1 of \d+: /);
  await expect(trip.getByRole("button", { name: "Stop", exact: true })).toBeVisible();
});

// Lead feedback of November 9, from using v1.1: PIP is reachable beside Start route, and one
// tap starts the trip and the camera together.
test("PIP beside Start route starts the trip and the camera in one tap; the notice once; Stop ends both", async ({ page }) => {
  await recordSpeech(page);
  await fakeCamera(page);
  await openReport(page);

  const topPip = page.getByRole("button", { name: "PIP", exact: true });
  await expect(topPip).toHaveAccessibleDescription(PIP_START_HELP);
  await expect(page.getByRole("region", { name: "Your trip" })).toHaveCount(0);
  await topPip.focus();
  await page.keyboard.press("Enter");

  // The trip is running (the first step shown and spoken) and the notice is open, Start PIP focused.
  const trip = page.getByRole("region", { name: "Your trip" });
  await expect(trip).toBeVisible();
  await expect(currentStep(trip)).toHaveText(/^Step 1 of \d+: .+\.$/);
  const pip = trip.getByRole("region", { name: PIP_TITLE });
  await expect(pip.getByText(NOTICE)).toBeVisible();
  const startPip = pip.getByRole("button", { name: "Start PIP", exact: true });
  await expect(startPip).toBeFocused();
  expect((await spokenSoFar(page))[0]).toMatch(/^Step 1 of \d+: /);

  // Start PIP: the camera is on while the trip runs (the replay API answers 503 later, which is the fallback).
  await startPip.click();
  await expect(pip.getByRole("button", { name: "Stop PIP", exact: true })).toBeFocused();
  await expect(pip.getByRole("status")).toHaveText(/^(Starting the camera|PIP is on|Camera guidance unavailable, reading route steps)$/);
  await expect(currentStep(trip)).toHaveText(/^Step 1 of \d+: /);
  await expect(trip.getByRole("button", { name: "Stop", exact: true })).toBeVisible();

  // Stop ends both: the row with Start route and PIP is back, focus on Start route.
  await trip.getByRole("button", { name: "Stop", exact: true }).click();
  await expect(page.getByRole("region", { name: "Your trip" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: START_ROUTE })).toBeFocused();
  await expect(topPip).toBeVisible();

  // The notice was accepted once on this report, so PIP now goes straight to the camera, with the trip.
  await topPip.click();
  await expect(trip).toBeVisible();
  await expect(pip.getByText(NOTICE)).toHaveCount(0);
  const stopPip = pip.getByRole("button", { name: "Stop PIP", exact: true });
  await expect(stopPip).toBeFocused();
  await expect(pip.getByRole("status")).toHaveText(/^(Starting the camera|PIP is on|Camera guidance unavailable, reading route steps)$/);
  await expect(currentStep(trip)).toHaveText(/^Step 1 of \d+: /);

  // Start route alone keeps today's behaviour: a trip without the camera.
  await trip.getByRole("button", { name: "Stop", exact: true }).click();
  await page.getByRole("button", { name: START_ROUTE }).click();
  await expect(trip).toBeVisible();
  await expect(pip.getByRole("button", { name: "PIP", exact: true })).toBeVisible();
  await expect(pip.getByRole("status")).toHaveCount(0);
  await expect(trip.getByRole("button", { name: "Stop", exact: true })).toBeFocused();
});

test("against the replay API, PIP says once that it is unavailable and the steps still work", async ({ page }) => {
  await recordSpeech(page);
  await fakeCamera(page);
  const statuses: number[] = [];
  page.on("response", (response) => {
    if (response.url().includes("/api/guide/frame")) statuses.push(response.status());
  });

  await openReport(page);
  const { trip, pip } = await openPip(page);
  await pip.getByRole("button", { name: "Start PIP", exact: true }).click();
  await expect(pip.getByRole("status")).toHaveText(UNAVAILABLE_STATUS, { timeout: 20_000 });
  expect(statuses).toContain(503);

  const transcript = trip.getByRole("list", { name: "Said so far" });
  await expect(transcript.getByRole("listitem").filter({ hasText: UNAVAILABLE })).toHaveCount(1);
  expect(await spokenSoFar(page)).toContain(UNAVAILABLE);

  // The steps go on: Next step moves the current step and is spoken.
  await trip.getByRole("button", { name: "Next step" }).click();
  await expect(currentStep(trip)).toHaveText(/^(Step 2 of \d+: |You're at the end of the route)/);
  expect((await spokenSoFar(page)).at(-1)).toMatch(/^(Step 2 of \d+: |You're at the end of the route)/);

  // Said once: the fallback is not repeated while PIP waits to try again.
  await page.waitForTimeout(2500);
  await expect(transcript.getByRole("listitem").filter({ hasText: UNAVAILABLE })).toHaveCount(1);
});
