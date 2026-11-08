import type { Metadata } from "next";
import Link from "next/link";
import SiteFooter from "@/components/ui/site-footer";
import SiteHeader from "@/components/ui/site-header";
import type { PlannerQuery } from "@/lib/planner-types";
import { encodeQr, qrPath, qrViewBox } from "@/lib/qr";
import { plannerHref } from "@/lib/route-link";
import { PITCH, PRODUCTION_URL, SITE_NAME, displayUrl } from "@/lib/site";
import styles from "./demo.module.css";

// The judge page (OP-411): one screen, read from across a table or a projector.
// The QR code is the page's one bold element; docs/design/DESIGN.md section 10.

export const metadata: Metadata = {
  title: `Demo | ${SITE_NAME}`,
  description: `Scan the code to open ${SITE_NAME} on your phone, then try a trip across downtown Edmonton.`
};

// The trip a judge is asked to try. Both places are entries of config/places.json
// (City of Edmonton parcel addresses; the Legislature is placed at Government
// Station, under its grounds). Checked against GET /api/route in replay mode on
// November 8, 2026: for a wheelchair, all three routes avoid the McKay School Park
// trail closure, the best one meets a partly closed 99 Avenue, and Government
// Centre station has an escalator out. Live data shows the day's conditions.
const TRIP: PlannerQuery = {
  from: {
    id: "place:edmonton-convention-centre",
    name: "Edmonton Convention Centre",
    description: "9797 Jasper Avenue NW",
    coordinates: [-113.486191, 53.541286]
  },
  to: {
    id: "place:alberta-legislature",
    name: "Alberta Legislature",
    description: "10800 97 Avenue NW",
    coordinates: [-113.510275, 53.536229]
  },
  profile: "wheelchair"
};

// Encoded once per server, not per request: the production code never changes.
const PRODUCTION_CODE = encodeQr(PRODUCTION_URL);

// A longer address still encodes (version 11 at level M), but would not scan from
// across a table. The override exists for local testing only.
const MAX_URL_LENGTH = 300;

type SearchParams = Record<string, string | string[] | undefined>;

/** The address the code points at: `?url=` (http or https) for local testing, else production. */
function qrTarget(params: SearchParams): { url: string; test: boolean } {
  const raw = Array.isArray(params.url) ? params.url[0] : params.url;
  if (raw && raw.length <= MAX_URL_LENGTH) {
    try {
      const parsed = new URL(raw);
      if (parsed.protocol === "http:" || parsed.protocol === "https:") return { url: raw, test: true };
    } catch {
      // Not an address: fall through to production.
    }
  }
  return { url: PRODUCTION_URL, test: false };
}

type Props = { searchParams: Promise<SearchParams> };

export default async function DemoPage({ searchParams }: Props) {
  const { url, test } = qrTarget(await searchParams);
  const code = test ? encodeQr(url) : PRODUCTION_CODE;

  return (
    <div className="shell">
      <SiteHeader />
      <main id="main" className={styles.main}>
        <div className={styles.intro}>
          <h1>{SITE_NAME}</h1>
          <p className={styles.pitch}>{PITCH}</p>
        </div>

        <figure className={styles.code}>
          <svg className={styles.qr} viewBox={qrViewBox(code)} role="img" aria-label={`QR code for ${url}`}>
            <path d={qrPath(code)} />
          </svg>
          <figcaption className={styles.address}>
            <a href={url}>{displayUrl(url)}</a>
          </figcaption>
          {test && (
            <p className={styles.testNote}>
              This code points at a test address from the page link. Without it, the code points at{" "}
              {displayUrl(PRODUCTION_URL)}.
            </p>
          )}
        </figure>

        <div className={styles.more}>
          <section aria-labelledby="demo-trip">
            <h2 id="demo-trip">Try this trip</h2>
            <p className={styles.trip}>
              <Link href={plannerHref(TRIP)}>
                {TRIP.from.name} to the {TRIP.to.name}, for a wheelchair
              </Link>
            </p>
            <p>
              Three routes, each drawn as a line with its known conditions as stops. In the recorded data they step
              around a river valley trail closed by fire damage, pass a partly closed road and end at a station with an
              escalator out. Live, they show today&apos;s conditions.
            </p>
          </section>
          <section aria-labelledby="demo-then">
            <h2 id="demo-then">Then try</h2>
            <ul className={styles.then}>
              <li>Switch the profile. Times and routes change with it.</li>
              <li>Open the route report for every condition and its source.</li>
              <li>Save the route and turn on alerts for when it changes.</li>
            </ul>
          </section>
        </div>
      </main>
      <SiteFooter />
    </div>
  );
}
