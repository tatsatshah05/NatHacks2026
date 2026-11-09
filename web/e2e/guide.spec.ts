// OP-418: the camera guide on the route report. The replay API answers 503 for
// POST /api/guide/frame by design, so the described-frame test fulfils that request
// itself; the camera is stubbed in the page (headless Chromium has none).
import { expect, test, type Locator, type Page } from "@playwright/test";
import { TRIP, blockMapTiles, reportUrl } from "./helpers";

const PROMISE = "Guide describes what the camera sees. It does not replace a cane or a guide dog.";
const NOTICE =
  "Your camera's view is sent to Anthropic to be described and is not stored. People may appear in it. Press Stop at any time.";
const CAMERA_DENIED = "Camera not available. I'll read the route steps.";
const SENTENCE = "I see a glass door about two metres ahead on your left.";

type Spoken = { spoken: string[] };

/** Record what the page speaks, without a voice: headless Chromium cannot be heard. */
async function recordSpeech(page: Page): Promise<void> {
  await page.addInitScript(() => {
    (window as unknown as Spoken).spoken = [];
    window.speechSynthesis.speak = (utterance: SpeechSynthesisUtterance) => {
      (window as unknown as Spoken).spoken.push(utterance.text);
    };
  });
}

/** Open the report, find the panel, press Guide with the keyboard and expect the notice. */
async function openGuide(page: Page): Promise<Locator> {
  await blockMapTiles(page);
  await page.goto(reportUrl(TRIP));
  await expect(page.getByRole("heading", { level: 2, name: "Steps for route 1" })).toBeVisible({ timeout: 30_000 });

  const panel = page.getByRole("region", { name: "Camera guide (preview)" });
  await expect(panel.getByText(PROMISE)).toBeVisible();
  const guide = panel.getByRole("button", { name: "Guide", exact: true });
  await expect(guide).toBeVisible();
  const box = await guide.boundingBox();
  expect(box?.height ?? 0, "Guide is at least 44 px tall").toBeGreaterThanOrEqual(44);
  expect(box?.width ?? 0, "Guide is at least 44 px wide").toBeGreaterThanOrEqual(44);

  await guide.focus();
  await page.keyboard.press("Enter");
  await expect(panel.getByText(NOTICE)).toBeVisible();
  await expect(panel.getByRole("button", { name: "Start" })).toBeFocused();
  await expect(panel.getByText(PROMISE), "the promise stays while the panel is open").toBeVisible();
  return panel;
}

test("Guide shows the notice; with the camera denied it says so and reads the first step", async ({ page }) => {
  await recordSpeech(page);
  await page.addInitScript(() => {
    const devices = (navigator.mediaDevices ?? {}) as MediaDevices;
    Object.defineProperty(navigator, "mediaDevices", { value: devices, configurable: true });
    devices.getUserMedia = () => Promise.reject(new DOMException("Permission denied", "NotAllowedError"));
  });
  const panel = await openGuide(page);
  const stepsBefore = await page.getByRole("region", { name: "Steps for route 1" }).getByRole("listitem").allTextContents();

  await panel.getByRole("button", { name: "Start" }).click();
  const stop = panel.getByRole("button", { name: "Stop" });
  await expect(stop).toBeFocused();
  expect((await stop.boundingBox())?.height ?? 0).toBeGreaterThanOrEqual(56);

  const transcript = panel.getByRole("list", { name: "Said so far" });
  await expect(transcript.getByRole("listitem").first()).toHaveText(/^Step 1 of \d+: .+\.$/);
  await expect(transcript.getByRole("listitem").filter({ hasText: CAMERA_DENIED })).toHaveCount(1);
  await expect(panel.getByRole("status")).toHaveText("Camera guidance unavailable, reading route steps");
  await expect(panel.getByText(PROMISE)).toBeVisible();

  // Both sentences went through speechSynthesis (the speech setting is on by default).
  const spoken = await page.evaluate(() => (window as unknown as Spoken).spoken);
  expect(spoken[0]).toMatch(/^Step 1 of \d+: /);
  expect(spoken).toContain(CAMERA_DENIED);

  // The route's own step list is untouched.
  const stepsAfter = await page.getByRole("region", { name: "Steps for route 1" }).getByRole("listitem").allTextContents();
  expect(stepsAfter).toEqual(stepsBefore);

  // Stop closes the panel and returns focus to Guide.
  await stop.click();
  await expect(panel.getByRole("button", { name: "Guide", exact: true })).toBeFocused();
  await expect(panel.getByText(NOTICE)).toHaveCount(0);
});

test("a described frame is spoken and appears in the transcript", async ({ page }) => {
  await recordSpeech(page);
  // A canvas stands in for the rear camera: its stream has real frames to draw.
  await page.addInitScript(() => {
    const devices = (navigator.mediaDevices ?? {}) as MediaDevices;
    Object.defineProperty(navigator, "mediaDevices", { value: devices, configurable: true });
    devices.getUserMedia = () => {
      const canvas = document.createElement("canvas");
      canvas.width = 320;
      canvas.height = 240;
      const context = canvas.getContext("2d");
      const paint = () => {
        if (!context) return;
        context.fillStyle = `hsl(${(Date.now() / 20) % 360} 40% 50%)`;
        context.fillRect(0, 0, canvas.width, canvas.height);
      };
      paint();
      window.setInterval(paint, 100);
      return Promise.resolve(canvas.captureStream(10));
    };
  });

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

  const panel = await openGuide(page);
  await panel.getByRole("button", { name: "Start" }).click();
  await expect(panel.getByRole("status")).toHaveText("Guiding");

  const transcript = panel.getByRole("list", { name: "Said so far" });
  await expect(transcript.getByRole("listitem").filter({ hasText: SENTENCE })).toHaveCount(1, { timeout: 20_000 });
  const spoken = await page.evaluate(() => (window as unknown as Spoken).spoken);
  expect(spoken).toContain(SENTENCE);

  // The frame went as multipart with the route context beside it.
  expect(requests.length).toBeGreaterThanOrEqual(1);
  expect(requests[0].contentType).toMatch(/^multipart\/form-data; boundary=/);
  const context = requests[0].context as Record<string, unknown>;
  expect(context.profile).toBe(TRIP.profile);
  expect(typeof context.client_id).toBe("string");
  expect(context.step).toMatch(/\d+ m/);
  expect(Array.isArray(context.nearby)).toBe(true);
  expect(context.previous).toBeNull();

  await panel.getByRole("button", { name: "Stop" }).click();
  await expect(panel.getByRole("button", { name: "Guide", exact: true })).toBeFocused();
});
