// The plain sentence for a source's last error. Run from web/ with `npm run test:lib`
// (Node 22.6 or newer strips the types of the imported module; no test runner is added).
import assert from "node:assert/strict";
import { test } from "node:test";
import { sourceErrorSentence } from "./source-error.ts";

const KEEPS = "OpenPath keeps its last known items.";
const MDN = "For more information check: https://developer.mozilla.org/en-US/docs/Web/HTTP/Status/";

// Recorded shapes of `last_error` (cloud/app/scheduler.py: "timed out after N s",
// "<Class>: <message>" or "store: <Class>: <message>", with httpx's own wording).
const CASES = [
  [
    `HTTPStatusError: Client error '429 Too Many Requests' for url 'https://api.open-meteo.com/v1/forecast?latitude=53.5'\n${MDN}429`,
    `The source refused the request (too many requests); ${KEEPS}`
  ],
  [
    `HTTPStatusError: Server error '503 Service Unavailable' for url 'https://data.edmonton.ca/resource/k4tx-5k8p.json'\n${MDN}503`,
    `The source had a server problem (status 503); ${KEEPS}`
  ],
  [
    `HTTPStatusError: Client error '403 Forbidden' for url 'https://data.edmonton.ca/resource/snws-u3zx.json'\n${MDN}403`,
    `The source refused the request (access denied); ${KEEPS}`
  ],
  [
    `HTTPStatusError: Client error '404 Not Found' for url 'https://data.edmonton.ca/resource/x.json'\n${MDN}404`,
    `The source answered that the data was not found (status 404); ${KEEPS}`
  ],
  ["ValueError: Weather API answered 502", `The source had a server problem (status 502); ${KEEPS}`],
  ["timed out after 10 s", `The source did not answer in time; ${KEEPS}`],
  ["ReadTimeout: The read operation timed out", `The source did not answer in time; ${KEEPS}`],
  ["ConnectTimeout: timed out", `The source did not answer in time; ${KEEPS}`],
  ["ConnectError: [Errno 8] nodename nor servname provided, or not known", `The source could not be reached; ${KEEPS}`],
  ["RemoteProtocolError: Server disconnected without sending a response.", `The source could not be reached; ${KEEPS}`],
  ["store: OperationalError: database is locked", `The result could not be saved to the database; ${KEEPS}`],
  ["ValueError: Expected a list of snow-clearing records", `The source sent an answer OpenPath could not read; ${KEEPS}`],
  ["JSONDecodeError: Expecting value: line 1 column 1 (char 0)", `The source sent an answer OpenPath could not read; ${KEEPS}`],
  [
    "FileNotFoundError: [Errno 2] No such file or directory: '/srv/snapshot/traffic_disruptions/current.json'",
    `The source could not be read; ${KEEPS}`
  ],
  ["unknown error", `The source could not be read; ${KEEPS}`]
];

test("each recorded error shape becomes its plain sentence", () => {
  for (const [raw, sentence] of CASES) assert.equal(sourceErrorSentence(raw), sentence, raw);
});

test("no sentence carries a class name, a URL, a path or the MDN link", () => {
  for (const [raw] of CASES) {
    const sentence = sourceErrorSentence(raw);
    assert.ok(sentence.endsWith(KEEPS), sentence);
    assert.doesNotMatch(sentence, /Error|Timeout|http|mozilla|\/|'/, sentence);
  }
});

test("no error means no sentence", () => {
  assert.equal(sourceErrorSentence(null), null);
  assert.equal(sourceErrorSentence(undefined), null);
  assert.equal(sourceErrorSentence(""), null);
  assert.equal(sourceErrorSentence("   "), null);
});

test("a status code outside 4xx and 5xx is not read as one", () => {
  assert.equal(sourceErrorSentence("ValueError: expected 200 rows, got 301"), `The source sent an answer OpenPath could not read; ${KEEPS}`);
});
