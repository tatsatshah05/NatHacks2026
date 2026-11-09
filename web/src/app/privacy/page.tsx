import type { Metadata } from "next";
import Link from "next/link";
import SiteFooter from "@/components/ui/site-footer";
import SiteHeader from "@/components/ui/site-header";
import styles from "@/components/reading.module.css";

export const metadata: Metadata = { title: "Privacy | OpenPath" };

// Every statement here comes from the code: lib/client-id.ts and lib/settings.ts (the
// device), cloud/app/saved_routes.py, push.py and push_api.py (alerts), reports.py and
// reports_api.py (reports, config/reports.json for expiry), camera_readings.py (camera
// readings), rate_limit.py (the per-connection counters), ws.py (live updates),
// vision/ai_check.py and docs/DATA_SOURCES.md (Anthropic, camera images), and the
// lead's decision of November 8, 2026 that report photos are checked by both the
// road-condition model and Claude. cloud/tests/test_privacy_inventory.py pins the
// database tables and columns this page describes. When one of them changes what is
// stored, change this page in the same pull request.

const REPO = "https://github.com/tatsatshah05/NatHacks2026";

export default function PrivacyPage() {
  return (
    <div className="shell">
      <SiteHeader current="privacy" />
      <main id="main" className={styles.main}>
        <article className={styles.prose}>
          <h1>Privacy</h1>
          <p className="lead">
            OpenPath has no accounts, no analytics and no advertising. This page lists everything it keeps, where it keeps
            it and for how long.
          </p>
          <p className={styles.dated}>Last updated November 8, 2026.</p>

          <h2>On this device</h2>
          <dl className={styles.terms}>
            <dt>An anonymous id</dt>
            <dd>
              A random string your browser makes the first time it needs one, stored as <code>openpath.client_id</code>. It
              is not tied to your name, email or phone. It limits how many reports one device can send, and it is how the
              server finds your saved routes and alerts.
            </dd>
            <dt>Your settings</dt>
            <dd>
              Speech, vibration, text size, theme, motion and the map style, stored as <code>openpath.speak</code> and{" "}
              <code>openpath.settings</code>. They never leave the device.
            </dd>
            <dt>Alerts, if you turn them on</dt>
            <dd>
              Your browser keeps OpenPath&rsquo;s service worker, the small script that shows alerts, and its push
              subscription.
            </dd>
          </dl>
          <p>Nothing else. OpenPath sets no cookies.</p>

          <h2>On the OpenPath server</h2>
          <p>The server keeps something only when you do one of these things.</p>
          <dl className={styles.terms}>
            <dt>You save a route</dt>
            <dd>
              Its name, its start and end points, the profile, your anonymous id, and the route OpenPath found for it (the
              paths it uses and its travel time), so it can tell you when the route changes. It also keeps the
              route&rsquo;s current status, when it was last checked and when you were last alerted.
            </dd>
            <dt>You turn on alerts</dt>
            <dd>
              The push address your browser&rsquo;s push service gave it, the two keys that encrypt alerts for your browser,
              your anonymous id, when you turned alerts on, when an alert last reached your browser, and a count of failed
              deliveries.
            </dd>
            <dt>You send a report</dt>
            <dd>
              Its type (snow, ice or a blocked path), its location, your note if you wrote one (up to 120 characters), when
              you sent it and when it expires. Reports are shown to everyone who uses OpenPath, without your id. Separately,
              your anonymous id, the report&rsquo;s id and the time are kept for 24 hours to limit each device to 5 reports
              an hour.
            </dd>
          </dl>
          <p>
            <strong>Photos are never stored.</strong> A photo you add to a report is held in memory while
            OpenPath&rsquo;s model and Claude check it, then dropped with the request. OpenPath never writes it to disk, the
            database or a log.
          </p>
          <p>
            <strong>Searches and open routes are not stored.</strong> What you type in From and To is looked up in
            OpenPath&rsquo;s own address index. While a route is open, the server holds its two points and profile in memory
            to send live updates, and forgets them when you leave.
          </p>
          <p>
            <strong>Not about you.</strong> The server also keeps the city&rsquo;s barriers from its public sources and the
            latest road-condition reading for each public 511 Alberta camera: a label such as dry, wet or ice, a score, the
            camera&rsquo;s position and the times of the reading, and one sentence on whether a vehicle in view appeared to
            have lost control, plus one item count per data source and day, so it can notice when a feed breaks. Never an
            image, and nothing from you.
          </p>
          <p>
            <strong>Rate limits.</strong> To stop floods, the server counts how many reports, saved routes and alert
            settings arrive from each network address, one minute at a time. The count lives in memory for that minute and
            is never written down.
          </p>
          <p>
            <strong>Logs.</strong> Like most web services, the server logs each request. That log can include your anonymous
            id, what you searched for and the points of routes you plan. Report photos and notes never go into it.
          </p>

          <h2>How long it is kept</h2>
          <ul>
            <li>Reports: 2 hours for ice, 6 hours for a blocked path and 12 hours for snow, then they are removed.</li>
            <li>The report limit log: 24 hours.</li>
            <li>The rate-limit count for your network address: one minute, in memory.</li>
            <li>Saved routes: until you remove them.</li>
            <li>
              Alert subscriptions: until your browser&rsquo;s push service says the subscription has ended, for example after
              you turn alerts off or block notifications, or after 5 failed deliveries in a row.
            </li>
            <li>Everything on this device: until you clear this site&rsquo;s data.</li>
          </ul>

          <h2>Who else handles data</h2>
          <dl className={styles.terms}>
            <dt>Your browser&rsquo;s push service</dt>
            <dd>
              Alerts travel through the push service of your browser&rsquo;s maker, such as Google, Apple or Mozilla. Each
              alert is encrypted for your browser, so the service cannot read it.
            </dd>
            <dt>Anthropic</dt>
            <dd>
              A photo you add to a report is checked by OpenPath&rsquo;s road-condition model and by Claude, Anthropic&rsquo;s
              AI model, so the photo is sent to the Anthropic API for that check. Frames from public 511 Alberta road
              cameras are sent the same way, so Claude can check each road reading. Images are sent in memory: OpenPath
              keeps no photo and no frame, and Anthropic processes them under its API terms. The labels OpenPath&rsquo;s
              camera model learned from were also made with Claude&rsquo;s help.
            </dd>
            <dt>OpenFreeMap</dt>
            <dd>
              Your browser loads map tiles straight from OpenFreeMap, which sees your IP address and the part of the map you
              look at.
            </dd>
            <dt>Vercel and Render</dt>
            <dd>
              Vercel hosts this web app, and Render hosts the server and its database. Both keep their own request logs,
              which can include your IP address.
            </dd>
            <dt>Google Maps and Apple Maps</dt>
            <dd>Only if you open a route in one of them: the link gives them your start, end and any detour points.</dd>
          </dl>

          <h2>Images</h2>
          <p>
            OpenPath never keeps your photos. Live camera frames are processed in memory and discarded. The only images the
            team keeps are labelled 511 Alberta frames used to train the camera model. They stay private on the team
            lead&rsquo;s laptop, are never published, and are all deleted after the event. Any frame showing a person who
            could be identified, or a readable licence plate, is deleted, not labelled.
          </p>
          <p>
            <strong>The camera guide</strong> (a preview on the route report) sends what your rear camera sees every few
            seconds while it runs, together with your position, your current step and the barriers near you, to Anthropic,
            so Claude can describe the frame in one sentence. Each frame is held in memory for one request and dropped;
            nothing is stored on the server, in the database or in a log. People may appear in the frames. Press Stop in
            the guide to end it at any time; that releases the camera and the position watch.
          </p>

          <h2>How to delete your data</h2>
          <ul>
            <li>
              <strong>Saved routes and alerts:</strong> remove each saved route and turn off alerts on the{" "}
              <Link href="/alerts">Alerts page</Link>. Do this first: once your anonymous id is gone, you cannot remove them
              yourself.
            </li>
            <li>
              <strong>This device:</strong> clear this site&rsquo;s data in your browser&rsquo;s settings. That deletes your
              anonymous id, your settings and the alert subscription.
            </li>
            <li>
              <strong>Reports</strong> expire on their own, as listed above.
            </li>
          </ul>
          <p>
            Questions about this page: open an issue on <a href={REPO}>OpenPath&rsquo;s GitHub repository</a>.
          </p>
        </article>
      </main>
      <SiteFooter />
    </div>
  );
}
