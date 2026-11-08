// axe on every page of the flows (OP-406, feeding OP-408).
//
// Rules: WCAG 2.0 and 2.1 A and AA, plus 2.2 AA. A serious or critical violation
// fails the test; moderate and minor ones are counted in the report (an annotation
// per page and the full violations as an attachment) but do not fail it.
//
// Kept apart from the flows so a rule that proves flaky can be excluded below,
// with the reason beside it. No rule is excluded today.
import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page, type TestInfo } from "@playwright/test";
import { TRIP, blockMapTiles, openPlannedTrip, reportUrl } from "./helpers";

const TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"];
// Rule ids to skip, each with a comment saying why. Empty on purpose.
const EXCLUDED_RULES: string[] = [];
const BLOCKING = new Set(["serious", "critical"]);

type Impact = "critical" | "serious" | "moderate" | "minor";

async function checkAccessibility(page: Page, testInfo: TestInfo, label: string): Promise<void> {
  const results = await new AxeBuilder({ page }).withTags(TAGS).disableRules(EXCLUDED_RULES).analyze();
  const counts: Record<Impact, number> = { critical: 0, serious: 0, moderate: 0, minor: 0 };
  for (const violation of results.violations) counts[(violation.impact ?? "minor") as Impact] += violation.nodes.length;
  const summary =
    `${label}: ${results.violations.length} rules violated on ${Object.values(counts).reduce((a, b) => a + b, 0)} elements ` +
    `(critical ${counts.critical}, serious ${counts.serious}, moderate ${counts.moderate}, minor ${counts.minor}); ` +
    `${results.passes.length} rules passed; ${results.incomplete.length} need a manual check`;
  testInfo.annotations.push({ type: "axe", description: summary });
  console.log(`axe ${summary}`);
  await testInfo.attach(`axe-${label.replace(/[^a-z0-9]+/gi, "-").toLowerCase()}.json`, {
    body: JSON.stringify({ summary, violations: results.violations, incomplete: results.incomplete }, null, 2),
    contentType: "application/json"
  });
  const blocking = results.violations
    .filter((violation) => BLOCKING.has(violation.impact ?? ""))
    .map(
      (violation) =>
        `${violation.id} (${violation.impact}): ${violation.help}\n  ${violation.nodes
          .map((node) => node.target.join(" "))
          .join("\n  ")}`
    );
  expect(blocking, `serious or critical axe violations on ${label}`).toEqual([]);
}

test.describe("accessibility", () => {
  test("planner, empty", async ({ page }, testInfo) => {
    await blockMapTiles(page);
    await page.goto("/");
    await expect(page.getByRole("heading", { level: 1, name: "Where are you going?" })).toBeVisible();
    await checkAccessibility(page, testInfo, "planner empty");
  });

  test("planner with routes", async ({ page }, testInfo) => {
    await openPlannedTrip(page, TRIP);
    await checkAccessibility(page, testInfo, "planner with routes");
  });

  test("route report", async ({ page }, testInfo) => {
    await blockMapTiles(page);
    await page.goto(reportUrl(TRIP));
    await expect(page.getByRole("heading", { level: 2, name: "Conditions for route 1" })).toBeVisible({ timeout: 30_000 });
    await checkAccessibility(page, testInfo, "route report");
  });

  test("alerts", async ({ page }, testInfo) => {
    await page.goto("/alerts");
    await expect(page.getByRole("status").filter({ hasText: /saved route/ })).toBeVisible();
    await checkAccessibility(page, testInfo, "alerts");
  });

  test("city dashboard", async ({ page }, testInfo) => {
    await blockMapTiles(page);
    await page.goto("/city");
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
    await checkAccessibility(page, testInfo, "city dashboard");
  });

  test("settings", async ({ page }, testInfo) => {
    await page.goto("/settings");
    await expect(page.getByRole("heading", { level: 1, name: "Settings" })).toBeVisible();
    await checkAccessibility(page, testInfo, "settings");
  });
});
