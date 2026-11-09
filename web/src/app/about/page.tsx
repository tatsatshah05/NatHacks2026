import type { Metadata } from "next";
import Link from "next/link";
import SiteFooter from "@/components/ui/site-footer";
import SiteHeader from "@/components/ui/site-header";
import styles from "@/components/reading.module.css";

export const metadata: Metadata = { title: "About | OpenPath" };

// Every statement here comes from PLAN.md (sections 7 and 8), config/profiles.json,
// docs/DATA_SOURCES.md, docs/OP-311.md, docs/DEPLOY.md and vision/MODEL_CARD.md.
// When one of those changes, change this page in the same pull request.

const MODEL_CARD = "https://github.com/tatsatshah05/NatHacks2026/blob/main/vision/MODEL_CARD.md";

export default function AboutPage() {
  return (
    <div className="shell">
      <SiteHeader current="about" />
      <main id="main" className={styles.main}>
        <article className={styles.prose}>
          <h1>About OpenPath</h1>
          <p className="lead">
            OpenPath plans walking routes across Edmonton for the way you get around, and lists every known barrier
            between where you are and where you are going.
          </p>
          <p>
            The City of Edmonton already publishes most of this: broken LRT elevators, construction that closes
            sidewalks, closed trails, snow complaints. It is spread across separate datasets. OpenPath reads them
            together with live weather and public road cameras, and finds routes that avoid what you cannot pass.
          </p>

          <h2>How a route is built</h2>
          <ol className={styles.line}>
            <li>
              <strong>Find the places.</strong>
              Your start and destination are matched against City addresses, LRT stations and named places. No outside
              geocoder is used.
            </li>
            <li>
              <strong>Read the conditions.</strong>
              City feeds and weather are read on a schedule, from every 2 minutes for LRT elevators to every hour for
              construction permits. Camera readings and community reports arrive as they are made.
            </li>
            <li>
              <strong>Place them on paths.</strong>
              Each condition is matched to the OpenStreetMap paths it touches: within 30&nbsp;m of a point, or 15&nbsp;m of a line.
            </li>
            <li>
              <strong>Weigh the paths for your profile.</strong>
              A path you cannot use is removed; a harder one counts as longer.
            </li>
            <li>
              <strong>Find up to three routes.</strong>
              The best route, plus up to two alternates that each share less than 70% of their length with a route
              already found.
            </li>
            <li>
              <strong>Write the report.</strong>
              Every condition on a route or within 50&nbsp;m of it, grouped as indoor, sidewalks and paths, roads and
              crossings, and weather and ice, with its source and when it was checked. Conditions a route avoids are
              marked as avoided.
            </li>
            <li>
              <strong>Hand off.</strong>
              Open the route in Google Maps or Apple Maps for turn-by-turn directions. Apple Maps may not keep the
              detour.
            </li>
          </ol>

          <h2>Profiles</h2>
          <p>
            Choose the one that fits you when you plan. Each profile treats conditions differently. Times use an assumed
            walking pace, not a measured one.
          </p>
          <dl className={styles.terms}>
            <dt>Wheelchair</dt>
            <dd>
              Avoids stairs, closed paths and paths with snow. A path with ice risk counts as three times as long. Pace 1.0&nbsp;m/s (3.6&nbsp;km/h).
            </dd>
            <dt>Walker or cane</dt>
            <dd>
              Avoids stairs, closed paths and paths with ice risk. A path with snow counts as three times as long. Pace 0.9&nbsp;m/s (3.2&nbsp;km/h).
            </dd>
            <dt>Low vision</dt>
            <dd>
              Avoids closed paths. Snow counts as twice as long and ice risk as three times. Stairs are allowed. Pace 1.0&nbsp;m/s
              (3.6&nbsp;km/h).
            </dd>
            <dt>Standard</dt>
            <dd>
              Avoids closed paths. Snow and ice risk each count as one and a half times as long. Stairs are allowed. Pace 1.3&nbsp;m/s (4.7&nbsp;km/h).
            </dd>
          </dl>
          <p>
            Conditions marked &ldquo;take care&rdquo;, such as an escalator out, a road closure or a plow that has passed,
            never change a route. They are listed in the report so you know what to expect.
          </p>

          <h2>Ice risk, never &ldquo;black ice detected&rdquo;</h2>
          <p>
            Black ice is hard to see by definition, so OpenPath never says it has found black ice. It reports ice risk, from
            two signals.
          </p>
          <p>
            <strong>The ice-risk watch</strong> covers the whole city. It turns on when the air or the ground is at or below
            0&nbsp;&deg;C and at least one of these is true: there was rain or snow in the last 6 hours; the air was above 0&nbsp;&deg;C in the last 12 hours, so melted water can refreeze; or the air is within 1&nbsp;&deg;C of the dew point, so
            frost can form. Because it applies to every path equally, it is shown once above your routes and never changes
            them.
          </p>
          <p>
            <strong>A camera reading</strong> is local. A reading of ice, or of wet while the watch is on, marks ice risk on
            crossings within 50&nbsp;m of that camera, at medium confidence. Wet without freezing is ignored.
          </p>

          <h2 id="cameras">Road cameras and the model</h2>
          <p>
            A small image model looks at frames from 511 Alberta road cameras and reads each one as dry, wet, partly
            snow-covered, snow-covered, ice or unusable. Frames are processed in memory and never stored. A reading changes
            only after two frames in a row agree, and an unusable frame gives &ldquo;unknown&rdquo;, never &ldquo;dry&rdquo;.
          </p>
          <p>
            Before a reading is posted, Claude, an AI model made by Anthropic, looks at the same frame. If the two do not
            agree, the reading is &ldquo;unknown&rdquo;.
          </p>
          <p>Measured on November 8, 2026, for the current model (from its model card):</p>
          <ul>
            <li>
              On 76 frames from 26 cameras it never saw in training, it matched the labels 81.6% of the time. With a slightly
              different training split, the same test scored 60.5%, so 81.6% is one result, not a stable figure.
            </li>
            <li>It recognised 1 of 4 wet roads. Wet roads are its weak point.</li>
            <li>The test had no snow or ice frames, so snow and ice are untested.</li>
            <li>
              It was trained on night frames only, and its labels were made with Claude&rsquo;s help, not by people. The
              scores show agreement with those labels.
            </li>
          </ul>
          <p>
            The full results are in the <a href={MODEL_CARD}>model card</a>. If the cameras or the model stop, OpenPath
            falls back to the ice-risk watch.
          </p>

          <h2>Limits</h2>
          <ul>
            <li>
              <strong>Data is only as fresh as its source.</strong> The <Link href="/sources">data sources page</Link> shows
              when each one was last checked. A source that fails keeps its last known items and is marked out of date.
            </li>
            <li>
              <strong>Maps have gaps.</strong> OpenStreetMap does not map sidewalks separately in every neighbourhood; there,
              routes follow the street. OpenPath knows about stairs, but not about curb ramps, slope or surface.
            </li>
            <li>
              <strong>Elevators are not on the paths yet.</strong> An LRT elevator outage is reported for the whole station,
              because the elevators are not yet matched to mapped paths. Walking routes cannot avoid them, so check the
              station warning in the report.
            </li>
            <li>
              <strong>LRT and bus waits are estimates.</strong> Walk and LRT or bus trips, when they are switched on, use
              waits estimated from the timetable, never live arrivals.
            </li>
            <li>
              <strong>Snow complaints are neighbourhood-wide.</strong> 311 records are located only to the neighbourhood,
              so they are advisories and never change a route.
            </li>
            <li>
              <strong>The server sleeps.</strong> It runs on Render&rsquo;s free plan, which sleeps when idle, so the first
              request after a quiet spell can be slow.
            </li>
            <li>
              <strong>Replay is labelled.</strong> When OpenPath plays back recorded data, every page says Replay. Recorded
              data is never shown as live.
            </li>
          </ul>
          <p>Conditions can change faster than any feed. Check again before you leave.</p>

          <h2>Who made it</h2>
          <p>
            OpenPath was built in 48 hours at natHACKS 2026 in Edmonton, in November 2026, by Team ClaudesPlan: Tatsat, Ipsa,
            Estella, Kaleb, Kashish and Matthew. It has no hardware and no accounts; it runs on public data, public cameras
            and community reports. Read <Link href="/privacy">what it keeps about you</Link>, or change speech, text size and
            motion in <Link href="/settings">Settings</Link>.
          </p>
        </article>
      </main>
      <SiteFooter />
    </div>
  );
}
