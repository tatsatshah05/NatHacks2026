// OP-408: the automated accessibility audit behind web/ACCESSIBILITY.md.
//
// It drives the production web build with Playwright's Chromium and records, for
// every page of the app: axe-core violations (WCAG 2.0 and 2.1 A and AA, 2.2 AA) at
// 390 and 1280 px wide in light and dark; the keyboard walkthrough (Tab order as
// accessible names, visible focus on each stop, the skip link, Escape, Enter and
// Space on the controls that open or toggle something); the focus ring's contrast;
// a seeded route change announced in the aria-live region, with the speech and
// vibration calls the page makes; reflow at 200% zoom (640 CSS px) and at 320 CSS px;
// what still animates under prefers-reduced-motion; the shape, pattern and words of
// every hazard mark; and the size of every touch target on the phone viewport.
//
// It measures and writes; it never decides. The findings are read into
// web/ACCESSIBILITY.md by hand, with the tool versions this script prints.
//
//   cd web && E2E_WEB_URL=http://localhost:3811 E2E_API_URL=http://localhost:8811 \
//     E2E_ADMIN_KEY=test node scripts/a11y-audit.mjs
//
// The API must run in replay mode (DATA_MODE=replay, REPLAY_SNAPSHOT=cloud/tests/fixtures,
// REPLAY_AT=2026-11-10T18:00:00Z) with ADMIN_KEY set, and the web app must be the
// production build pointed at it (as scripts/e2e.py starts them). Output goes to
// test-results/a11y/ (gitignored): results.json, one screenshot per page and context,
// and a summary on stdout. A11Y_ONLY=axe,keyboard,... runs a subset of the sections.
// A11Y_BLOCK_TILES=1 drops the OpenFreeMap tile requests, as the e2e tests do.
//
// No dependency beyond @playwright/test and @axe-core/playwright (both devDependencies).

import { execSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { mkdir, writeFile } from "node:fs/promises";
import { createRequire } from "node:module";
import path from "node:path";
import AxeBuilder from "@axe-core/playwright";
import { chromium } from "@playwright/test";

const require = createRequire(import.meta.url);

/** The installed version of a package, read from the package.json above its entry file. */
function versionOf(name) {
  let dir = path.dirname(require.resolve(name));
  for (let depth = 0; depth < 6; depth += 1) {
    try {
      const pkg = JSON.parse(readFileSync(path.join(dir, "package.json"), "utf8"));
      if (pkg.name === name) return pkg.version;
    } catch (error) {
      // No package.json here: look one folder up.
    }
    dir = path.dirname(dir);
  }
  return null;
}

const WEB = (process.env.E2E_WEB_URL ?? "http://localhost:3140").replace(/\/+$/, "");
const API = (process.env.E2E_API_URL ?? "http://localhost:8740").replace(/\/+$/, "");
const ADMIN_KEY = process.env.E2E_ADMIN_KEY ?? "e2e";
const OUT = path.resolve(process.env.A11Y_OUT ?? "test-results/a11y");
const BLOCK_TILES = process.env.A11Y_BLOCK_TILES === "1";
const ONLY = new Set((process.env.A11Y_ONLY ?? "").split(",").map((part) => part.trim()).filter(Boolean));
const runs = (section) => ONLY.size === 0 || ONLY.has(section);

/** fetch with a deadline, so a stalled connection fails loudly instead of hanging the run. */
const http = (url, init = {}) => fetch(url, { ...init, signal: AbortSignal.timeout(20_000) });

const TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"];
const CLIENT_ID = "a11y-audit-device-0001"; // the anonymous device id the /alerts page reads
const MIN_TARGET_PX = 44;

// The trip every route page uses: the judge page's trip (web/src/app/demo/page.tsx,
// two entries of config/places.json). At the frozen replay clock its three routes
// carry a closure they avoid, a partly closed road and a station escalator outage,
// so the route line, the report and the hazard marks all have real content.
const TRIP = {
  from: { name: "Edmonton Convention Centre", lat: 53.541286, lng: -113.486191 },
  to: { name: "Alberta Legislature", lat: 53.536229, lng: -113.510275 },
  profile: "wheelchair"
};

const latLng = (place) => `${place.lat.toFixed(6)},${place.lng.toFixed(6)}`;

function plannerUrl(trip) {
  const params = new URLSearchParams({
    from: latLng(trip.from),
    from_name: trip.from.name,
    to: latLng(trip.to),
    to_name: trip.to.name,
    profile: trip.profile
  });
  return `/?${params.toString()}`;
}

const reportUrl = (trip, rank = 1) => `/route${plannerUrl(trip).slice(1)}&route=${rank}`;

/** Each page, with how to know it has finished loading. */
const PAGES = [
  { id: "planner", path: "/", ready: (page) => page.getByRole("heading", { level: 1, name: "Where are you going?" }) },
  {
    id: "planner-trip",
    path: plannerUrl(TRIP),
    ready: (page) => page.getByRole("status").filter({ hasText: /\d+ routes? for / })
  },
  {
    id: "route",
    path: reportUrl(TRIP),
    ready: (page) => page.getByRole("heading", { level: 2, name: "Conditions for route 1" })
  },
  { id: "alerts", path: "/alerts", ready: (page) => page.getByRole("status").filter({ hasText: /saved route/ }) },
  { id: "city", path: "/city", ready: (page) => page.getByRole("status").filter({ hasText: /barriers/ }) },
  { id: "settings", path: "/settings", ready: (page) => page.getByRole("heading", { level: 1, name: "Settings" }) },
  { id: "about", path: "/about", ready: (page) => page.getByRole("heading", { level: 1 }) },
  { id: "sources", path: "/sources", ready: (page) => page.getByRole("heading", { level: 1 }) },
  { id: "privacy", path: "/privacy", ready: (page) => page.getByRole("heading", { level: 1 }) },
  { id: "demo", path: "/demo", ready: (page) => page.getByRole("img", { name: /^QR code for / }) },
  {
    id: "not-found",
    path: "/no-such-page",
    ready: (page) => page.getByRole("heading", { level: 1, name: "This page does not exist." })
  }
];

// A11Y_PAGES=planner-trip,city limits the axe section to those pages; their earlier
// entries in results.json are replaced and the other pages' entries are kept.
const PAGE_FILTER = new Set((process.env.A11Y_PAGES ?? "").split(",").map((part) => part.trim()).filter(Boolean));
const AXE_PAGES = PAGE_FILTER.size ? PAGES.filter((spec) => PAGE_FILTER.has(spec.id)) : PAGES;

const PHONE = { width: 390, height: 844, mobile: true };
const LAPTOP = { width: 1280, height: 800, mobile: false };

// ------------------------------------------------------------------ helpers

/** Records the speech and vibration calls a page makes, without changing them. */
const HOOKS = `(() => {
  const calls = { speak: [], cancel: 0, vibrate: [] };
  window.__a11yCalls = calls;
  try {
    const proto = window.SpeechSynthesis && window.SpeechSynthesis.prototype;
    if (proto && proto.speak) {
      const speak = proto.speak;
      proto.speak = function (utterance) {
        calls.speak.push({ text: utterance && utterance.text, lang: utterance && utterance.lang, at: Date.now() });
        return speak.call(this, utterance);
      };
      const cancel = proto.cancel;
      proto.cancel = function () { calls.cancel += 1; return cancel.call(this); };
    }
    const nav = window.Navigator && window.Navigator.prototype;
    if (nav && nav.vibrate) {
      const vibrate = nav.vibrate;
      nav.vibrate = function (pattern) {
        const returned = vibrate.call(this, pattern);
        calls.vibrate.push({ pattern, returned, at: Date.now() });
        return returned;
      };
    }
  } catch (error) {}
})();`;

async function newContext(browser, { width, height, mobile, scheme = "light", reducedMotion = "no-preference", dsf, storage = {} }) {
  const context = await browser.newContext({
    viewport: { width, height },
    deviceScaleFactor: dsf ?? (mobile ? 3 : 1),
    isMobile: mobile,
    hasTouch: mobile,
    colorScheme: scheme,
    reducedMotion,
    locale: "en-CA",
    timezoneId: "America/Edmonton"
  });
  context.setDefaultTimeout(30_000);
  const seed = { "openpath.client_id": CLIENT_ID, ...storage };
  await context.addInitScript((values) => {
    try {
      for (const [key, value] of Object.entries(values)) window.localStorage.setItem(key, value);
    } catch (error) {}
  }, seed);
  await context.addInitScript(HOOKS);
  if (BLOCK_TILES) await context.route(/openfreemap\.org/, (route) => route.abort());
  return context;
}

async function open(page, spec) {
  await page.goto(`${WEB}${spec.path}`);
  await spec.ready(page).first().waitFor({ state: "visible", timeout: 45_000 });
}

/** Scroll the whole page once so lazily mounted maps mount, then back to the top. */
async function settle(page) {
  await page.evaluate(async () => {
    const step = Math.max(200, window.innerHeight - 100);
    const total = document.documentElement.scrollHeight;
    for (let y = 0; y < total; y += step) {
      window.scrollTo(0, y);
      await new Promise((resolve) => setTimeout(resolve, 60));
    }
    window.scrollTo(0, 0);
  });
  await page.waitForTimeout(1200);
}

const slug = (text) => text.replace(/[^a-z0-9]+/gi, "-").replace(/^-|-$/g, "").toLowerCase();

/** WCAG relative luminance contrast of two CSS colours ("rgb(…)" or "#rrggbb"). */
function contrast(a, b) {
  const parse = (value) => {
    const hex = value.trim().match(/^#([0-9a-f]{6})$/i);
    if (hex) return [0, 2, 4].map((index) => parseInt(hex[1].slice(index, index + 2), 16));
    const rgb = value.match(/rgba?\(([^)]+)\)/);
    if (!rgb) return null;
    return rgb[1].split(",").slice(0, 3).map((part) => parseFloat(part));
  };
  const luminance = (channels) => {
    const [r, g, b] = channels.map((channel) => {
      const c = channel / 255;
      return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
    });
    return 0.2126 * r + 0.7152 * g + 0.0722 * b;
  };
  const first = parse(a);
  const second = parse(b);
  if (!first || !second) return null;
  const [light, dark] = [luminance(first), luminance(second)].sort((x, y) => y - x);
  return Math.round(((light + 0.05) / (dark + 0.05)) * 100) / 100;
}

// Runs in the page: the focused element as a screen reader would name it, and whether
// its focus is visible (the project draws the ring on the control, or on the label or
// field box around a visually hidden input).
function describeActive() {
  const el = document.activeElement;
  if (!el || el === document.body || el === document.documentElement) return null;
  const text = (node) => (node?.textContent ?? "").replace(/\s+/g, " ").trim();
  const byIds = (ids) =>
    ids
      .split(/\s+/)
      .map((id) => text(document.getElementById(id)))
      .filter(Boolean)
      .join(" ");
  const name = (() => {
    if (el.getAttribute("aria-labelledby")) return byIds(el.getAttribute("aria-labelledby"));
    if (el.getAttribute("aria-label")) return el.getAttribute("aria-label").trim();
    if (el.labels && el.labels.length) return text(el.labels[0]);
    if (el.tagName === "IMG") return el.getAttribute("alt") ?? "";
    if (el.tagName === "SELECT" || el.tagName === "INPUT" || el.tagName === "TEXTAREA") return el.getAttribute("placeholder") ?? "";
    return text(el);
  })();
  const role = (() => {
    const explicit = el.getAttribute("role");
    if (explicit) return explicit;
    const tag = el.tagName.toLowerCase();
    if (tag === "a") return el.hasAttribute("href") ? "link" : "a";
    if (tag === "input") {
      const type = (el.getAttribute("type") ?? "text").toLowerCase();
      if (type === "checkbox" || type === "radio") return type;
      if (type === "search") return "searchbox";
      if (type === "file") return "file input";
      return "textbox";
    }
    if (tag === "select") return "combobox";
    if (tag === "textarea") return "textbox";
    if (tag === "summary") return "disclosure (summary)";
    if (tag === "canvas") return "canvas";
    if (/^h[1-6]$/.test(tag)) return "heading";
    return tag;
  })();
  const states = [];
  for (const attr of ["aria-pressed", "aria-expanded", "aria-checked", "aria-current", "aria-haspopup", "aria-disabled"]) {
    if (el.hasAttribute(attr)) states.push(`${attr}=${el.getAttribute(attr)}`);
  }
  if ((el.type === "checkbox" || el.type === "radio") && !el.hasAttribute("aria-checked")) states.push(`checked=${el.checked}`);
  if (el.disabled) states.push("disabled");
  const ring = (node) => {
    if (!node) return null;
    const style = getComputedStyle(node);
    const width = parseFloat(style.outlineWidth) || 0;
    if (style.outlineStyle !== "none" && width > 0 && style.outlineColor !== "transparent") {
      return { width, colour: style.outlineColor, offset: style.outlineOffset, shadow: style.boxShadow };
    }
    return null;
  };
  // The ring may sit on the element, on the label of a visually hidden input, or on the
  // field box around a text input (globals.css .control:has(input:focus-visible)).
  const carriers = [
    ["self", el],
    ["label", el.labels?.[0] ?? null],
    ["label (next)", el.nextElementSibling?.tagName === "LABEL" ? el.nextElementSibling : null],
    ["field box", el.closest(".control")],
    ["segment", el.closest(".segment")]
  ];
  let focusRing = null;
  for (const [carrier, node] of carriers) {
    const found = ring(node);
    if (found) {
      focusRing = { carrier, ...found };
      break;
    }
  }
  const rect = el.getBoundingClientRect();
  const inViewport = rect.bottom > 0 && rect.right > 0 && rect.top < window.innerHeight && rect.left < window.innerWidth;
  const pathOf = (node) => {
    const parts = [];
    while (node && node.nodeType === 1 && node !== document.body) {
      const index = Array.from(node.parentNode.children).indexOf(node) + 1;
      parts.unshift(`${node.tagName.toLowerCase()}:nth-child(${index})`);
      node = node.parentNode;
    }
    return parts.join(">");
  };
  return {
    key: pathOf(el),
    tag: el.tagName.toLowerCase(),
    role,
    name,
    states,
    focusVisible: el.matches(":focus-visible"),
    ring: focusRing,
    inViewport,
    insideMain: Boolean(el.closest("main")),
    insideDialog: Boolean(el.closest("dialog[open]")),
    rect: { width: Math.round(rect.width), height: Math.round(rect.height), top: Math.round(rect.top) }
  };
}

/** Tab from the top of the document until focus leaves it or returns to a seen stop. */
async function tabWalk(page, max = 400) {
  await page.evaluate(() => {
    document.activeElement?.blur?.();
    window.scrollTo(0, 0);
  });
  const stops = [];
  const seen = new Set();
  let end = "limit";
  for (let index = 0; index < max; index += 1) {
    await page.keyboard.press("Tab");
    const info = await page.evaluate(describeActive);
    if (!info) {
      end = "left the document";
      break;
    }
    if (seen.has(info.key)) {
      end = "cycled";
      break;
    }
    seen.add(info.key);
    stops.push(info);
  }
  return { stops, end };
}

const active = (page) => page.evaluate(describeActive);

async function post(url, body, headers = {}) {
  const response = await http(url, {
    method: "POST",
    headers: { "content-type": "application/json", ...headers },
    body: JSON.stringify(body)
  });
  const text = await response.text();
  let json = null;
  try {
    json = JSON.parse(text);
  } catch (error) {
    json = null;
  }
  return { status: response.status, json, text };
}

// ------------------------------------------------------------------ sections

async function axeSection(browser, results) {
  results.axe = PAGE_FILTER.size ? (results.axe ?? []).filter((entry) => !PAGE_FILTER.has(entry.page)) : [];
  for (const device of [PHONE, LAPTOP]) {
    for (const scheme of ["light", "dark"]) {
      const context = await newContext(browser, { ...device, scheme });
      const page = await context.newPage();
      for (const spec of AXE_PAGES) {
        await open(page, spec);
        await settle(page);
        const scan = await new AxeBuilder({ page }).withTags(TAGS).analyze();
        const violations = scan.violations.map((violation) => ({
          id: violation.id,
          impact: violation.impact,
          help: violation.help,
          helpUrl: violation.helpUrl,
          nodes: violation.nodes.map((node) => ({ target: node.target.join(" "), html: node.html.slice(0, 200) }))
        }));
        const counts = { critical: 0, serious: 0, moderate: 0, minor: 0 };
        for (const violation of violations) counts[violation.impact ?? "minor"] += violation.nodes.length;
        const shot = path.join(OUT, "screens", `${spec.id}-${scheme}-${device.width}.png`);
        await page.screenshot({ path: shot, fullPage: true });
        const entry = {
          page: spec.id,
          path: spec.path,
          scheme,
          width: device.width,
          violations,
          counts,
          passes: scan.passes.length,
          // "Incomplete" is axe asking for a human look, not a violation; keep what it looked at.
          incomplete: scan.incomplete.map((item) => ({
            id: item.id,
            impact: item.impact,
            nodes: item.nodes.length,
            targets: item.nodes.slice(0, 12).map((node) => ({
              target: node.target.join(" "),
              html: node.html.slice(0, 160),
              reason: node.any?.[0]?.message ?? node.all?.[0]?.message ?? null
            }))
          })),
          screenshot: path.relative(OUT, shot)
        };
        results.axe.push(entry);
        console.log(
          `axe ${spec.id} ${scheme} ${device.width}: ${violations.length} rules violated ` +
            `(critical ${counts.critical}, serious ${counts.serious}, moderate ${counts.moderate}, minor ${counts.minor}); ` +
            `${scan.passes.length} passed; ${scan.incomplete.length} incomplete`
        );
      }
      await context.close();
    }
  }
}

async function keyboardSection(browser, results) {
  results.keyboard = { ...(results.keyboard ?? {}), walks: [], skipLink: [] };
  for (const device of [LAPTOP, PHONE]) {
    const context = await newContext(browser, { ...device, scheme: "light" });
    const page = await context.newPage();
    for (const spec of PAGES) {
      // 1. The skip link: first Tab, visible, Enter, then the next Tab lands in main.
      await open(page, spec);
      await page.waitForTimeout(500);
      await page.keyboard.press("Tab");
      const first = await active(page);
      await page.keyboard.press("Enter");
      await page.waitForTimeout(200);
      const afterEnter = await active(page);
      await page.keyboard.press("Tab");
      const afterSkip = await active(page);
      results.keyboard.skipLink.push({
        page: spec.id,
        width: device.width,
        first: first ? { role: first.role, name: first.name, visible: first.inViewport && first.rect.top >= 0, ring: Boolean(first.ring) } : null,
        afterEnter: afterEnter ? { role: afterEnter.role, name: afterEnter.name } : "document",
        nextTab: afterSkip ? { role: afterSkip.role, name: afterSkip.name, insideMain: afterSkip.insideMain } : null
      });
      // 2. The full walk from the top. A fresh navigation, not a reload: the skip link
      // left "#main" in the address, and a reload would start the Tab order there.
      await open(page, spec);
      await settle(page);
      const walk = await tabWalk(page);
      const noRing = walk.stops.filter((stop) => !stop.ring);
      const offscreen = walk.stops.filter((stop) => !stop.inViewport);
      results.keyboard.walks.push({ page: spec.id, width: device.width, ...walk, noRing, offscreen });
      console.log(
        `keyboard ${spec.id} ${device.width}: ${walk.stops.length} stops, ${walk.end}; ` +
          `${noRing.length} without a visible ring, ${offscreen.length} outside the viewport when focused`
      );
    }
    await context.close();
  }

}

async function interactionsSection(browser, results) {
  // Enter, Space and Escape on what opens, toggles or closes (laptop, light).
  results.keyboard = { ...(results.keyboard ?? {}), interactions: [] };
  const context = await newContext(browser, { ...LAPTOP, scheme: "light" });
  const page = await context.newPage();
  const note = (entry) => {
    results.keyboard.interactions.push(entry);
    console.log(`interaction ${entry.page}: ${entry.check}: ${entry.result}`);
  };

  // Planner: suggestions open on typing, Escape closes them and keeps focus in the field.
  await open(page, PAGES[0]);
  const from = page.getByRole("textbox", { name: "From", exact: true });
  await from.focus();
  await page.keyboard.type("churchill");
  const suggestions = page.getByRole("list", { name: "From suggestions" });
  await suggestions.waitFor({ state: "visible" });
  const suggestionCount = await suggestions.getByRole("button").count();
  await page.keyboard.press("Escape");
  await page.waitForTimeout(150);
  const afterEscape = await active(page);
  note({
    page: "planner",
    check: "Escape closes the From suggestions",
    result: `${suggestionCount} suggestions shown; after Escape the list is ${(await suggestions.isVisible()) ? "still open" : "closed"} and focus is on ${afterEscape?.role} "${afterEscape?.name}"`
  });
  await page.keyboard.type(" ");
  await suggestions.waitFor({ state: "visible" });
  await page.keyboard.press("Tab");
  const onSuggestion = await active(page);
  await page.keyboard.press("Enter");
  await page.waitForTimeout(200);
  const chosen = await page.getByText(/^Selected: /).first().textContent();
  const afterChoose = await active(page);
  note({
    page: "planner",
    check: "Tab to a suggestion, Enter chooses it",
    result: `Tab landed on ${onSuggestion?.role} "${onSuggestion?.name}"; Enter gave "${chosen?.trim()}" and focus went to ${afterChoose?.role} "${afterChoose?.name}"`
  });
  // Submitting with nothing chosen: the error names the field and focus moves to it.
  await page.reload();
  await PAGES[0].ready(page).waitFor();
  await page.getByRole("button", { name: "Find routes" }).focus();
  await page.keyboard.press("Enter");
  await page.waitForTimeout(200);
  const afterEmptySubmit = await active(page);
  const fieldError = await page.locator(".field-error").first().textContent();
  note({
    page: "planner",
    check: "Enter on Find routes with no places",
    result: `error "${fieldError?.trim()}"; focus moved to ${afterEmptySubmit?.role} "${afterEmptySubmit?.name}"`
  });
  // Segmented profile radios: arrow keys move the choice.
  await page.getByRole("radio", { name: "Wheelchair" }).focus();
  await page.keyboard.press("ArrowDown");
  await page.waitForTimeout(100);
  const afterArrow = await active(page);
  note({
    page: "planner",
    check: "Arrow key in the profile group",
    result: `ArrowDown moved to ${afterArrow?.role} "${afterArrow?.name}" (${afterArrow?.states.join(", ")}), ring on ${afterArrow?.ring?.carrier ?? "nothing"}`
  });

  // Planner with routes: the alternate's disclosure, "Show on the map", the Report dialog.
  await open(page, PAGES[1]);
  await settle(page);
  const show2 = page.getByRole("button", { name: "Show route 2" });
  if ((await show2.count()) > 0) {
    await show2.focus();
    await page.keyboard.press("Enter");
    await page.waitForTimeout(200);
    const opened = await active(page);
    await page.keyboard.press("Space");
    await page.waitForTimeout(200);
    const closed = await active(page);
    note({
      page: "planner-trip",
      check: "Enter and Space on the route 2 disclosure",
      result: `Enter: "${opened?.name}" ${opened?.states.join(", ")}; Space: "${closed?.name}" ${closed?.states.join(", ")}; focus stayed on the button both times: ${opened?.key === closed?.key}`
    });
    await page.keyboard.press("Enter"); // open route 2 again: opening also draws it on the map
    await page.waitForTimeout(300);
    const route2Shown = await page.locator("#route-2-details").getByText("Shown on the map as the thick line.").count();
    const showRoute1 = page.getByRole("button", { name: /^Show on the map\s*, route 1$/ });
    const route1Offer = await showRoute1.count();
    await showRoute1.focus();
    await page.keyboard.press("Enter");
    await page.waitForTimeout(400);
    const afterShow = await active(page);
    const route1Shown = await page.getByText("Shown on the map as the thick line.").count();
    note({
      page: "planner-trip",
      check: "Opening route 2 draws it; Enter on Show on the map, route 1",
      result: `after opening route 2 its details say "Shown on the map as the thick line." ${route2Shown} time(s) and route 1 offers "Show on the map, route 1" ${route1Offer} time(s); Enter on it moved focus to ${afterShow?.tag} ${afterShow?.role} "${afterShow?.name}" (inside main: ${afterShow?.insideMain}); the sentence now appears ${route1Shown} time(s) on the page`
    });
  } else {
    note({ page: "planner-trip", check: "Enter and Space on the route 2 disclosure", result: "no second route for this trip" });
  }
  const reportButton = page.getByRole("button", { name: "Report a problem" });
  await reportButton.focus();
  await page.keyboard.press("Enter");
  await page.waitForTimeout(300);
  const dialogOpen = await page.locator("dialog[open]").count();
  const inDialog = await active(page);
  const dialogStops = [];
  for (let index = 0; index < 25; index += 1) {
    await page.keyboard.press("Tab");
    const stop = await active(page);
    dialogStops.push(stop ? { name: stop.name, role: stop.role, inside: stop.insideDialog, ring: Boolean(stop.ring) } : null);
  }
  await page.keyboard.press("Escape");
  await page.waitForTimeout(300);
  const dialogAfter = await page.locator("dialog[open]").count();
  const afterDialog = await active(page);
  const escaped = dialogStops.filter((stop) => stop && !stop.inside).length;
  note({
    page: "planner-trip",
    check: "Report a problem dialog: Enter opens, Tab stays inside, Escape closes and returns focus",
    result: `open dialogs after Enter: ${dialogOpen}; focus on ${inDialog?.role} "${inDialog?.name}"; 25 Tabs: ${dialogStops.filter(Boolean).length} stops, ${escaped} outside the dialog, ${dialogStops.filter((stop) => stop && !stop.ring).length} without a ring; after Escape: ${dialogAfter} open, focus on ${afterDialog?.role} "${afterDialog?.name}"`
  });
  results.keyboard.dialogStops = dialogStops;
  // Save this route: Enter opens the form with focus in the name field.
  const save = page.getByRole("button", { name: /^Save this route\s*, route 1$/ });
  if ((await save.count()) > 0) {
    await save.focus();
    await page.keyboard.press("Enter");
    await page.waitForTimeout(300);
    const inForm = await active(page);
    const form = page.getByRole("form", { name: "Save route 1" });
    const nameValue = await form.getByRole("textbox", { name: "Name for this route" }).inputValue();
    const buttons = await form.getByRole("button").allTextContents();
    await page.keyboard.press("Escape");
    await page.waitForTimeout(300);
    const formAfterEscape = await form.count();
    const afterFormEscape = await active(page);
    note({
      page: "planner-trip",
      check: "Enter on Save this route, then Escape",
      result: `focus on ${inForm?.role} "${inForm?.name}" with value "${nameValue}"; buttons: ${buttons.map((text) => text.trim()).join(" | ")}; after Escape the form is ${formAfterEscape ? "still open" : "closed"} and focus is on ${afterFormEscape?.role} "${afterFormEscape?.name}"`
    });
  }

  // Route report: the route choice radios. Focus must stay on the chosen radio, the
  // status line must announce the choice, and the report must not be fetched again.
  await open(page, PAGES[2]);
  const routeRequests = [];
  page.on("request", (request) => {
    if (request.url().includes("/api/route")) routeRequests.push(request.url());
  });
  const routeRadio = page.getByRole("radio").first();
  if ((await routeRadio.count()) > 0) {
    await routeRadio.focus();
    const before = await page.getByRole("heading", { level: 2, name: /^Conditions for route/ }).textContent();
    const requestsBefore = routeRequests.length;
    await page.keyboard.press("ArrowDown");
    await page.waitForTimeout(1500);
    const after = await page.getByRole("heading", { level: 2, name: /^Conditions for route/ }).textContent();
    const onRadio = await active(page);
    const choiceStatus = await page.locator("section[aria-labelledby='options-title']").getByRole("status").first().textContent();
    note({
      page: "route",
      check: "Arrow key on the route choice",
      result:
        `"${before?.trim()}" became "${after?.trim()}"; focus is on ${onRadio ? `${onRadio.role} "${onRadio.name.slice(0, 50)}"` : "the document body (focus was lost)"}; ` +
        `status line "${choiceStatus?.trim()}"; GET /api/route requests during the change: ${routeRequests.length - requestsBefore}`
    });
  }

  // Alerts: Remove opens a confirmation with focus on Keep it; Escape returns to Remove.
  await open(page, PAGES[3]);
  const remove = page.getByRole("button", { name: /^Remove / }).first();
  if ((await remove.count()) > 0) {
    await remove.focus();
    await page.keyboard.press("Enter");
    await page.waitForTimeout(300);
    const onKeep = await active(page);
    const groupText = await page.getByRole("group").first().textContent();
    await page.keyboard.press("Escape");
    await page.waitForTimeout(300);
    const backOnRemove = await active(page);
    note({
      page: "alerts",
      check: "Enter on Remove, then Escape",
      result: `confirmation "${groupText?.trim().slice(0, 80)}"; focus on ${onKeep?.role} "${onKeep?.name}"; after Escape focus is on ${backOnRemove?.role} "${backOnRemove?.name}"`
    });
  } else {
    note({ page: "alerts", check: "Enter on Remove, then Escape", result: "no saved route on the page" });
  }

  // City: the disclosure, the search, a select, Show on map, Show more.
  await open(page, PAGES[4]);
  // The page's own disclosure only: MapLibre's attribution is a <details> too.
  const method = page.locator("details").filter({ has: page.getByText("How the ranking works") }).first();
  await method.locator("summary").focus();
  await page.keyboard.press("Enter");
  await page.waitForTimeout(150);
  const openDetails = await method.evaluate((el) => el.open);
  await page.keyboard.press("Space");
  await page.waitForTimeout(150);
  const closedDetails = await method.evaluate((el) => el.open);
  note({ page: "city", check: "Enter and Space on How the ranking works", result: `open after Enter: ${openDetails}; open after Space: ${closedDetails}` });
  const countLine = () => page.getByRole("status").filter({ hasText: /barriers/ }).first().textContent();
  const clearButton = page.getByRole("button", { name: "Clear filters" });
  const search = page.getByRole("searchbox", { name: "Search" });
  await search.focus();
  await page.keyboard.type("trail");
  await page.waitForTimeout(400);
  const afterSearch = await countLine();
  const clearAfterSearch = await clearButton.count();
  await search.fill("");
  const effect = page.getByRole("combobox", { name: "Effect" });
  await effect.focus();
  await page.keyboard.press("ArrowDown");
  await page.waitForTimeout(400);
  const effectValue = await effect.inputValue();
  const afterEffect = await countLine();
  const clearAfterEffect = await clearButton.count();
  // On macOS an arrow key opens the native menu instead of changing the value, so
  // also choose an option the way the menu would.
  await effect.selectOption({ index: 1 });
  await page.waitForTimeout(400);
  const chosenEffect = await effect.inputValue();
  const afterChoice = await countLine();
  const clearAfterChoice = await clearButton.count();
  await effect.selectOption("");
  await page.waitForTimeout(200);
  note({
    page: "city",
    check: "Typing in Search, ArrowDown on Effect, choosing an Effect",
    result:
      `after typing "trail": "${afterSearch?.trim()}" (Clear filters offered: ${clearAfterSearch}); ` +
      `after ArrowDown on the closed Effect select (value "${effectValue}"): "${afterEffect?.trim()}" (Clear filters offered: ${clearAfterEffect}); ` +
      `after choosing "${chosenEffect}" from the menu: "${afterChoice?.trim()}" (Clear filters offered: ${clearAfterChoice})`
  });
  const showOnMap = page.getByRole("button", { name: "Show on map" }).first();
  await showOnMap.focus();
  await page.keyboard.press("Enter");
  await page.waitForTimeout(300);
  const pressed = await active(page);
  const mapHint = await page.getByRole("link", { name: "See it on the map" }).count();
  await page.keyboard.press("Space");
  await page.waitForTimeout(300);
  const unpressed = await active(page);
  note({
    page: "city",
    check: "Enter and Space on Show on map",
    result: `Enter: ${pressed?.states.join(", ")}, "See it on the map" links: ${mapHint}; Space: ${unpressed?.states.join(", ")}; focus stayed: ${pressed?.key === unpressed?.key}`
  });
  const more = page.getByRole("button", { name: /^Show \d+ more$/ });
  if ((await more.count()) > 0) {
    const label = await more.textContent();
    await more.focus();
    await page.keyboard.press("Enter");
    await page.waitForTimeout(400);
    const afterMore = await active(page);
    note({
      page: "city",
      check: `Enter on "${label?.trim()}"`,
      result: `focus moved to ${afterMore?.tag} ${afterMore?.role} "${afterMore?.name?.slice(0, 60)}" with ring on ${afterMore?.ring?.carrier ?? "nothing"}`
    });
  }

  // Settings: Space on a switch, arrow keys on text size.
  await open(page, PAGES[5]);
  const speakSwitch = page.getByRole("switch", { name: "Speak route changes" });
  await speakSwitch.focus();
  const switchBefore = await active(page);
  await page.keyboard.press("Space");
  await page.waitForTimeout(200);
  const switchAfter = await active(page);
  const statusAfter = await page.locator("main .status-line").first().textContent();
  await page.keyboard.press("Space");
  await page.waitForTimeout(200);
  note({
    page: "settings",
    check: "Space on the Speak route changes switch",
    result: `before: ${switchBefore?.states.join(", ")}, ring on ${switchBefore?.ring?.carrier ?? "nothing"}; after Space: ${switchAfter?.states.join(", ")}; status line "${statusAfter?.trim()}"`
  });
  await page.getByRole("radio", { name: /^Default/ }).focus();
  await page.keyboard.press("ArrowDown");
  await page.waitForTimeout(300);
  const textSize = await page.evaluate(() => document.documentElement.getAttribute("data-text"));
  const sizeStatus = await page.locator("main .status-line").first().textContent();
  await page.keyboard.press("ArrowUp");
  await page.waitForTimeout(200);
  note({
    page: "settings",
    check: "ArrowDown on Text size",
    result: `html data-text="${textSize}"; status line "${sizeStatus?.trim()}"`
  });
  await context.close();
}

async function focusRingSection(browser, results) {
  results.focusRing = [];
  for (const scheme of ["light", "dark"]) {
    const context = await newContext(browser, { ...LAPTOP, scheme });
    const page = await context.newPage();
    await open(page, PAGES[0]);
    const tokens = await page.evaluate(() => {
      const style = getComputedStyle(document.documentElement);
      const read = (name) => style.getPropertyValue(name).trim();
      return { focus: read("--focus"), card: read("--card"), snow: read("--snow"), ink: read("--ink"), action: read("--action") };
    });
    await page.getByRole("button", { name: "Find routes" }).focus();
    const measured = await page.evaluate(() => {
      const el = document.activeElement;
      const style = getComputedStyle(el);
      return {
        outlineWidth: style.outlineWidth,
        outlineStyle: style.outlineStyle,
        outlineColor: style.outlineColor,
        outlineOffset: style.outlineOffset,
        boxShadow: style.boxShadow,
        pageBackground: getComputedStyle(document.body).backgroundColor
      };
    });
    const entry = {
      scheme,
      tokens,
      ringOnCard: contrast(tokens.focus, tokens.card),
      ringOnSnow: contrast(tokens.focus, tokens.snow),
      ringOnAction: contrast(tokens.focus, tokens.action),
      measured
    };
    results.focusRing.push(entry);
    console.log(
      `focus ring ${scheme}: ${tokens.focus} on card ${tokens.card} = ${entry.ringOnCard}:1, on page ${tokens.snow} = ${entry.ringOnSnow}:1; measured ${measured.outlineWidth} ${measured.outlineStyle} ${measured.outlineColor}, offset ${measured.outlineOffset}`
    );
    await context.close();
  }
}

/** The best route's longest straight piece, as web/e2e/helpers.ts does, so the override blocks a path the route uses. */
function longestSegment(route) {
  const coords = route.geometry.coordinates;
  let best = [coords[0], coords[1]];
  let bestLength = -1;
  for (let index = 0; index + 1 < coords.length; index += 1) {
    const [aLng, aLat] = coords[index];
    const [bLng, bLat] = coords[index + 1];
    const dx = (bLng - aLng) * Math.cos((53.54 * Math.PI) / 180) * 111_320;
    const dy = (bLat - aLat) * 110_574;
    const length = Math.hypot(dx, dy);
    if (length > bestLength) {
      bestLength = length;
      best = [coords[index], coords[index + 1]];
    }
  }
  return { type: "LineString", coordinates: best };
}

async function liveSection(browser, results) {
  results.live = [];
  const variants = [
    { label: "speech and vibration on (defaults)", storage: {} },
    {
      label: "speech and vibration off",
      storage: { "openpath.speak": "off", "openpath.settings": JSON.stringify({ vibrate: false }) }
    }
  ];
  for (const [index, variant] of variants.entries()) {
    const context = await newContext(browser, { ...LAPTOP, scheme: "light", storage: variant.storage });
    const page = await context.newPage();
    await open(page, PAGES[1]);
    const liveLine = page.getByRole("region", { name: "Live updates" }).getByRole("status");
    await liveLine.filter({ hasText: "Live." }).waitFor({ state: "visible", timeout: 30_000 });
    const params = new URLSearchParams({ from: latLng(TRIP.from), to: latLng(TRIP.to), profile: TRIP.profile });
    const best = (await (await http(`${API}/api/route?${params}`)).json()).routes[0];
    const ref = `a11y-audit-${index}-${Date.now()}`;
    const title = "Sidewalk closed for the accessibility audit";
    const created = await post(
      `${API}/api/admin/override`,
      { source_ref: ref, title, geometry: longestSegment(best), ttl_s: 300 },
      { "X-Admin-Key": ADMIN_KEY }
    );
    const entry = { variant: variant.label, override: { status: created.status, rebuilt: created.json?.rebuilt, title: created.json?.title } };
    try {
      const announcer = page.getByRole("region", { name: "Live updates" }).locator('[aria-live="assertive"]');
      await announcer.filter({ hasText: "Route update:" }).waitFor({ state: "visible", timeout: 60_000 });
      entry.announced = (await announcer.textContent())?.trim();
      entry.ariaAtomic = await announcer.getAttribute("aria-atomic");
      const detail = announcer.locator("xpath=following-sibling::p");
      entry.detail = (await detail.count()) ? (await detail.first().textContent())?.trim() : null;
      await page.getByText("Routes checked again after the change.").waitFor({ state: "visible", timeout: 30_000 });
      entry.refreshed = true;
      entry.avoided = await page.getByRole("region", { name: "Routes" }).getByText(/^Avoided: path closed$/).count();
      await page.waitForTimeout(500);
      entry.calls = await page.evaluate(() => window.__a11yCalls);
      entry.speechApi = await page.evaluate(() => ({
        present: "speechSynthesis" in window,
        voices: window.speechSynthesis?.getVoices().length ?? null,
        speaking: window.speechSynthesis?.speaking ?? null,
        pending: window.speechSynthesis?.pending ?? null
      }));
      entry.status = (await liveLine.first().textContent())?.trim();
    } finally {
      await http(`${API}/api/admin/override/${ref}`, { method: "DELETE", headers: { "X-Admin-Key": ADMIN_KEY } });
    }
    results.live.push(entry);
    console.log(
      `live (${variant.label}): override ${entry.override.status} rebuilt=${entry.override.rebuilt}; announced "${entry.announced}"; ` +
        `speak calls ${entry.calls?.speak.length ?? "?"}, vibrate calls ${entry.calls?.vibrate.length ?? "?"}; avoided labels ${entry.avoided}`
    );
    await context.close();
  }
}

// Runs in the page: horizontal overflow and text clipped by its own box.
function measureReflow() {
  const root = document.documentElement;
  const clientWidth = root.clientWidth;
  const skip = (el) => el.closest(".sr-only, .maplibregl-map, script, style") || el.matches(".sr-only");
  const label = (el) => {
    const id = el.id ? `#${el.id}` : "";
    const cls = typeof el.className === "string" && el.className ? `.${el.className.trim().split(/\s+/).slice(0, 2).join(".")}` : "";
    return `${el.tagName.toLowerCase()}${id}${cls}`;
  };
  const offRight = [];
  const clipped = [];
  for (const el of document.body.querySelectorAll("*")) {
    if (skip(el)) continue;
    const rect = el.getBoundingClientRect();
    if (rect.width === 0 || rect.height === 0) continue;
    if (rect.right > clientWidth + 1) offRight.push({ el: label(el), right: Math.round(rect.right), width: Math.round(rect.width) });
    const hasText = Array.from(el.childNodes).some((node) => node.nodeType === 3 && node.textContent.trim());
    if (!hasText) continue;
    const style = getComputedStyle(el);
    const hidesX = ["hidden", "clip", "auto", "scroll"].includes(style.overflowX);
    const hidesY = ["hidden", "clip"].includes(style.overflowY);
    if ((hidesX && el.scrollWidth > el.clientWidth + 1) || (hidesY && el.scrollHeight > el.clientHeight + 1)) {
      clipped.push({ el: label(el), text: el.textContent.trim().slice(0, 60) });
    }
  }
  return {
    clientWidth,
    scrollWidth: root.scrollWidth,
    overflowPx: Math.max(0, root.scrollWidth - clientWidth),
    offRight: offRight.slice(0, 8),
    offRightCount: offRight.length,
    clipped: clipped.slice(0, 8),
    clippedCount: clipped.length,
    rootFontSize: getComputedStyle(root).fontSize
  };
}

async function zoomSection(browser, results) {
  results.zoom = [];
  const contexts = [
    { label: "200% zoom at 1280 (640 CSS px, device scale 2)", width: 640, height: 400, mobile: false, dsf: 2 },
    { label: "320 CSS px wide", width: 320, height: 568, mobile: true, dsf: 2 }
  ];
  for (const setting of contexts) {
    const context = await newContext(browser, { ...setting, scheme: "light" });
    const page = await context.newPage();
    for (const spec of PAGES) {
      await open(page, spec);
      await settle(page);
      const measured = await page.evaluate(measureReflow);
      const shot = path.join(OUT, "screens", `${spec.id}-zoom-${setting.width}.png`);
      await page.screenshot({ path: shot, fullPage: true });
      results.zoom.push({ page: spec.id, setting: setting.label, width: setting.width, ...measured, screenshot: path.relative(OUT, shot) });
      console.log(
        `zoom ${spec.id} ${setting.width}px: overflow ${measured.overflowPx} px, ${measured.offRightCount} elements past the right edge, ${measured.clippedCount} clipped text elements`
      );
    }
    await context.close();
  }
}

// Runs in the page: anything that still animates or transitions for longer than 20 ms.
function findMotion() {
  const seconds = (value) =>
    value
      .split(",")
      .map((part) => part.trim())
      .map((part) => (part.endsWith("ms") ? parseFloat(part) / 1000 : parseFloat(part)))
      .filter((number) => !Number.isNaN(number));
  const label = (el) => {
    const id = el.id ? `#${el.id}` : "";
    const cls = typeof el.className === "string" && el.className ? `.${el.className.trim().split(/\s+/).slice(0, 3).join(".")}` : "";
    return `${el.tagName.toLowerCase()}${id}${cls}`;
  };
  const moving = [];
  for (const el of document.querySelectorAll("*")) {
    for (const pseudo of [null, "::before", "::after"]) {
      const style = getComputedStyle(el, pseudo);
      const animation = style.animationName !== "none" && seconds(style.animationDuration).some((s) => s > 0.02);
      const transition =
        style.transitionProperty !== "none" && style.transitionProperty !== "" && seconds(style.transitionDuration).some((s) => s > 0.02);
      if (animation || transition) {
        moving.push({
          el: label(el) + (pseudo ?? ""),
          animation: animation ? `${style.animationName} ${style.animationDuration}` : null,
          transition: transition ? `${style.transitionProperty} ${style.transitionDuration}` : null
        });
      }
    }
  }
  const line = document.querySelector(".route-line");
  return {
    moving: moving.slice(0, 20),
    movingCount: moving.length,
    scrollBehavior: getComputedStyle(document.documentElement).scrollBehavior,
    routeLine: line ? { classes: line.className, animationName: getComputedStyle(line).animationName, duration: getComputedStyle(line).animationDuration } : null,
    mapCanvases: document.querySelectorAll(".maplibregl-canvas").length,
    dataMotion: document.documentElement.getAttribute("data-motion")
  };
}

/** What "Show on the map, route 1" asks of scrollIntoView: "smooth" or "auto" (instant). */
async function scrollBehaviour(page) {
  await page.evaluate(() => {
    window.__scrollCalls = [];
    const original = Element.prototype.scrollIntoView;
    Element.prototype.scrollIntoView = function (options) {
      window.__scrollCalls.push(options && typeof options === "object" ? (options.behavior ?? "auto") : String(options));
      return original.call(this, options);
    };
  });
  const show2 = page.getByRole("button", { name: "Show route 2" });
  if ((await show2.count()) === 0) return null;
  await show2.click(); // opening route 2 draws it on the map, so route 1 now offers the button
  await page.getByRole("button", { name: /^Show on the map\s*, route 1$/ }).click();
  await page.waitForTimeout(300);
  return page.evaluate(() => window.__scrollCalls);
}

async function motionSection(browser, results) {
  results.motion = { reduced: [], control: null, settingOnly: null };
  const context = await newContext(browser, { ...LAPTOP, scheme: "light", reducedMotion: "reduce" });
  const page = await context.newPage();
  for (const spec of PAGES) {
    await open(page, spec);
    await settle(page);
    const found = await page.evaluate(findMotion);
    const scroll = spec.id === "planner-trip" ? await scrollBehaviour(page) : undefined;
    results.motion.reduced.push({ page: spec.id, ...found, scrollIntoView: scroll });
    console.log(
      `motion (reduce) ${spec.id}: ${found.movingCount} elements still animating or transitioning; route line ${found.routeLine?.animationName ?? "absent"}; map canvases ${found.mapCanvases}` +
        (scroll ? `; Show on the map scrolled with behavior ${JSON.stringify(scroll)}` : "")
    );
  }
  await context.close();
  // Control: without the preference the line reveal must exist, or the check proves nothing.
  const plain = await newContext(browser, { ...LAPTOP, scheme: "light" });
  const plainPage = await plain.newPage();
  await open(plainPage, PAGES[1]);
  results.motion.control = { ...(await plainPage.evaluate(findMotion)), scrollIntoView: await scrollBehaviour(plainPage) };
  console.log(
    `motion (no preference) planner-trip: ${results.motion.control.movingCount} moving; route line ${results.motion.control.routeLine?.animationName} ${results.motion.control.routeLine?.duration}; Show on the map scrolled with behavior ${JSON.stringify(results.motion.control.scrollIntoView)}`
  );
  await plain.close();
  // The OP-307 setting alone ("Always reduce") with no system preference.
  const setting = await newContext(browser, {
    ...LAPTOP,
    scheme: "light",
    storage: { "openpath.settings": JSON.stringify({ motion: "reduce" }) }
  });
  const settingPage = await setting.newPage();
  await open(settingPage, PAGES[1]);
  await settle(settingPage);
  results.motion.settingOnly = { ...(await settingPage.evaluate(findMotion)), scrollIntoView: await scrollBehaviour(settingPage) };
  console.log(
    `motion (setting "Always reduce" only) planner-trip: ${results.motion.settingOnly.movingCount} moving; data-motion=${results.motion.settingOnly.dataMotion}; route line ${results.motion.settingOnly.routeLine?.animationName}; Show on the map scrolled with behavior ${JSON.stringify(results.motion.settingOnly.scrollIntoView)}`
  );
  await setting.close();
}

// Runs in the page: every hazard mark with its shape, fill and words.
function collectMarks(scope) {
  const root = scope ? document.querySelector(scope) : document;
  if (!root) return [];
  const marks = [];
  for (const mark of root.querySelectorAll(".hazard-mark, .stop, .city-wide li")) {
    // The hazard shape first: an avoided stop also draws the line's kink as an SVG.
    const svg = mark.querySelector("svg.hazard-shape") ?? mark.querySelector("svg");
    const outline = svg?.querySelector(":scope > circle, :scope > rect, :scope > polygon, :scope > path");
    const pattern = svg?.querySelector("pattern");
    const text = mark.textContent.replace(/\s+/g, " ").trim();
    if (!svg || !text) continue;
    marks.push({
      shape: outline ? `${outline.tagName}${outline.tagName === "polygon" ? ` ${outline.getAttribute("points")?.split(" ").length} points` : ""}` : "none",
      fill: outline?.getAttribute("fill") ?? "",
      pattern: pattern ? pattern.firstElementChild?.tagName ?? "tile" : null,
      colour: svg.style.color || getComputedStyle(svg).color,
      ariaHidden: svg.getAttribute("aria-hidden"),
      text: text.slice(0, 90)
    });
  }
  return marks;
}

async function colourSection(browser, results) {
  results.colour = [];
  const context = await newContext(browser, { ...LAPTOP, scheme: "light" });
  const page = await context.newPage();
  const checks = [
    { page: PAGES[4], scope: "ul.legend", label: "city legend" },
    { page: PAGES[4], scope: "ol[aria-labelledby='barriers-title']", label: "city list rows" },
    { page: PAGES[2], scope: ".groups", label: "route report items" },
    { page: PAGES[1], scope: ".routes", label: "planner route lines" },
    { page: PAGES[3], scope: "main", label: "alerts saved route status" }
  ];
  let current = null;
  for (const check of checks) {
    if (current !== check.page.id) {
      await open(page, check.page);
      await settle(page);
      current = check.page.id;
    }
    const marks = await page.evaluate(collectMarks, check.scope);
    const markers = await page.evaluate(() =>
      Array.from(document.querySelectorAll(".map-marker")).map((marker) => ({
        ariaHidden: marker.getAttribute("aria-hidden"),
        tabIndex: marker.getAttribute("tabindex"),
        size: `${Math.round(marker.getBoundingClientRect().width)}x${Math.round(marker.getBoundingClientRect().height)}`
      }))
    );
    results.colour.push({ label: check.label, page: check.page.id, marks, mapMarkers: markers.length, markerSample: markers[0] ?? null });
    console.log(`colour ${check.label}: ${marks.length} marks; map markers ${markers.length}${markers[0] ? ` (aria-hidden=${markers[0].ariaHidden}, tabindex=${markers[0].tabIndex}, ${markers[0].size})` : ""}`);
  }
  await context.close();
}

// Runs in the page: every interactive element's target size on this viewport.
function measureTargets(minimum) {
  const selector = "a[href], button, input, select, textarea, summary, [role='button'], [role='switch'], [tabindex]:not([tabindex='-1'])";
  const text = (node) => (node?.textContent ?? "").replace(/\s+/g, " ").trim();
  const nameOf = (el) => {
    if (el.getAttribute("aria-labelledby")) return text(document.getElementById(el.getAttribute("aria-labelledby").split(/\s+/)[0]));
    if (el.getAttribute("aria-label")) return el.getAttribute("aria-label");
    if (el.labels?.[0]) return text(el.labels[0]);
    return text(el) || el.getAttribute("placeholder") || el.getAttribute("title") || el.tagName.toLowerCase();
  };
  const out = [];
  for (const el of document.querySelectorAll(selector)) {
    if (el.closest("dialog:not([open]), [hidden], .sr-only") || el.matches(".sr-only")) continue;
    const style = getComputedStyle(el);
    if (style.display === "none" || style.visibility === "hidden") continue;
    // A visually hidden radio, checkbox or switch: its label is the target.
    let target = el;
    const rect0 = el.getBoundingClientRect();
    if ((el.type === "radio" || el.type === "checkbox") && (rect0.width <= 1 || style.opacity === "0")) target = el.labels?.[0] ?? el.closest("label") ?? el;
    else if (el.type === "checkbox" && el.labels?.[0]) target = el.labels[0];
    const rect = target.getBoundingClientRect();
    if (rect.width === 0 || rect.height === 0) continue;
    const inline = style.display.startsWith("inline") && el.tagName === "A" && el.parentElement && text(el.parentElement).length > text(el).length + 10;
    out.push({
      role: el.getAttribute("role") ?? el.tagName.toLowerCase() + (el.type ? `[${el.type}]` : ""),
      name: nameOf(el).slice(0, 60),
      width: Math.round(rect.width * 10) / 10,
      height: Math.round(rect.height * 10) / 10,
      ok: rect.width >= minimum && rect.height >= minimum,
      inlineText: Boolean(inline),
      inMap: Boolean(el.closest(".maplibregl-map"))
    });
  }
  return out;
}

async function targetsSection(browser, results) {
  results.targets = [];
  const context = await newContext(browser, { ...PHONE, scheme: "light" });
  const page = await context.newPage();
  for (const spec of PAGES) {
    await open(page, spec);
    await settle(page);
    const targets = await page.evaluate(measureTargets, MIN_TARGET_PX);
    const small = targets.filter((target) => !target.ok);
    results.targets.push({ page: spec.id, total: targets.length, small, all: targets });
    console.log(`targets ${spec.id} 390px: ${targets.length} interactive elements, ${small.length} under ${MIN_TARGET_PX} px: ${small.map((t) => `${t.role} "${t.name}" ${t.width}x${t.height}`).join("; ")}`);
  }
  await context.close();
}

async function settingsSection(browser, results) {
  const context = await newContext(browser, { ...LAPTOP, scheme: "light" });
  const page = await context.newPage();
  await open(page, PAGES[5]);
  const describeSwitches = () =>
    page.evaluate(() =>
      Array.from(document.querySelectorAll("[role='switch']")).map((el) => ({
        name: document.getElementById(el.getAttribute("aria-labelledby"))?.textContent.trim(),
        describedBy: (el.getAttribute("aria-describedby") ?? "")
          .split(/\s+/)
          .map((id) => document.getElementById(id)?.textContent.trim())
          .filter(Boolean)
          .join(" "),
        checked: el.checked,
        ariaChecked: el.getAttribute("aria-checked"),
        word: el.parentElement.querySelector("[aria-hidden='true']")?.textContent.trim()
      }))
    );
  const storage = () =>
    page.evaluate(() => ({ speak: localStorage.getItem("openpath.speak"), settings: localStorage.getItem("openpath.settings") }));
  const entry = { before: await describeSwitches(), storageBefore: await storage() };
  await page.getByRole("switch", { name: "Speak route changes" }).click();
  entry.speakStatus = (await page.locator("main .status-line").first().textContent())?.trim();
  await page.getByRole("switch", { name: "Vibrate on route changes" }).click();
  entry.vibrateStatus = (await page.locator("main .status-line").first().textContent())?.trim();
  entry.after = await describeSwitches();
  entry.storageAfter = await storage();
  entry.limits = await page.evaluate(() => Array.from(document.querySelectorAll("[id$='-limit']")).map((el) => el.textContent.trim()));
  // The planner's own speech checkbox reads the same setting.
  await open(page, PAGES[1]);
  const plannerBox = page.getByRole("checkbox", { name: "Speak route changes aloud" });
  await plannerBox.waitFor({ state: "visible", timeout: 30_000 });
  entry.plannerCheckboxAfterOff = await plannerBox.isChecked();
  await plannerBox.click();
  entry.plannerCheckboxToggled = await plannerBox.isChecked();
  entry.storageAfterPlanner = await storage();
  await open(page, PAGES[5]);
  entry.settingsAfterPlanner = await describeSwitches();
  await page.getByRole("button", { name: "Reset to defaults" }).click();
  entry.resetStatus = (await page.locator("main .status-line").first().textContent())?.trim();
  entry.afterReset = await describeSwitches();
  results.settings = entry;
  console.log(
    `settings: switches ${entry.before.map((s) => `${s.name}=${s.ariaChecked ?? s.checked} (${s.word})`).join(", ")}; after clicks: ${entry.after.map((s) => `${s.name}=${s.checked} (${s.word})`).join(", ")}; storage ${JSON.stringify(entry.storageAfter)}; planner checkbox ${entry.plannerCheckboxAfterOff} then ${entry.plannerCheckboxToggled}; reset "${entry.resetStatus}"`
  );
  await context.close();
}

// ------------------------------------------------------------------ main

async function main() {
  await mkdir(path.join(OUT, "screens"), { recursive: true });
  const health = await http(`${API}/health`)
    .then((response) => response.json())
    .catch(() => null);
  let commit = null;
  try {
    commit = execSync("git rev-parse HEAD", { encoding: "utf8" }).trim();
  } catch (error) {
    commit = null;
  }
  // A partial run (A11Y_ONLY) keeps the other sections of an earlier results.json.
  let previous = {};
  if (ONLY.size > 0) {
    try {
      previous = JSON.parse(readFileSync(path.join(OUT, "results.json"), "utf8"));
    } catch (error) {
      previous = {};
    }
  }
  const results = {
    ...previous,
    runs: [...(previous.runs ?? []), { date: new Date().toISOString(), sections: ONLY.size ? [...ONLY] : ["all"] }],
    date: new Date().toISOString(),
    commit,
    web: WEB,
    api: API,
    apiHealth: health,
    tools: {
      playwright: versionOf("@playwright/test"),
      axeCorePlaywright: versionOf("@axe-core/playwright"),
      axeCore: versionOf("axe-core"),
      chromium: null,
      node: process.version,
      tags: TAGS
    },
    trip: TRIP,
    blockTiles: BLOCK_TILES,
    savedRoute: null
  };

  const browser = await chromium.launch();
  try {
    results.tools.chromium = browser.version();
    console.log(`tools: ${JSON.stringify(results.tools)}; API ${JSON.stringify(health)}; commit ${commit}`);

    // One saved route for /alerts, under the device id every context is seeded with.
    const saved = await post(`${API}/api/saved-routes`, {
      client_id: CLIENT_ID,
      name: "Audit route",
      from: { lat: TRIP.from.lat, lng: TRIP.from.lng },
      to: { lat: TRIP.to.lat, lng: TRIP.to.lng },
      profile: TRIP.profile
    });
    results.savedRoute = { status: saved.status, id: saved.json?.id ?? null, name: saved.json?.name ?? null, routeStatus: saved.json?.status ?? null };
    console.log(`saved route for /alerts: ${saved.status} ${saved.text.slice(0, 160)}`);

    if (runs("axe")) await axeSection(browser, results);
    if (runs("keyboard")) await keyboardSection(browser, results);
    if (runs("interactions")) await interactionsSection(browser, results);
    if (runs("focus")) await focusRingSection(browser, results);
    if (runs("live")) await liveSection(browser, results);
    if (runs("zoom")) await zoomSection(browser, results);
    if (runs("motion")) await motionSection(browser, results);
    if (runs("colour")) await colourSection(browser, results);
    if (runs("targets")) await targetsSection(browser, results);
    if (runs("settings")) await settingsSection(browser, results);
  } finally {
    if (results.savedRoute?.id) {
      await http(`${API}/api/saved-routes/${results.savedRoute.id}?client_id=${CLIENT_ID}`, { method: "DELETE" }).catch(() => undefined);
    }
    await browser.close();
    await writeFile(path.join(OUT, "results.json"), JSON.stringify(results, null, 2));
    console.log(`written ${path.join(OUT, "results.json")}`);
  }
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
