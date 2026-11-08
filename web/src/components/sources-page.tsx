"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { KNOWN_STATUS_KEYS, SOURCE_SECTIONS, type Freshness, type SourceRow, type SourceSection } from "@/lib/source-catalog";
import { formatAgo, plural, sourceLabel } from "@/lib/report-format";
import { getCameras, getStatus, type CameraCollection, type StatusResponse } from "@/lib/status-api";
import Button from "./ui/button";
import SiteFooter from "./ui/site-footer";
import SiteHeader from "./ui/site-header";
import StatusLine from "./ui/status-line";
import TimeAgo from "./ui/time-ago";
import styles from "./reading.module.css";

type SourceStatus = StatusResponse["sources"][number];

type Live = {
  status: StatusResponse | null;
  cameras: CameraCollection | null;
  loading: boolean;
  failed: boolean;
};

const REFRESH_MS = 60_000;

/** "every 2 min", "every hour": how often OpenPath reads a source. */
function cadenceText(seconds: number): string {
  if (seconds >= 3600 && seconds % 3600 === 0) return seconds === 3600 ? "every hour" : `every ${seconds / 3600} hours`;
  if (seconds >= 60) return `every ${Math.round(seconds / 60)} min`;
  return `every ${Math.round(seconds)} s`;
}

function StatusFreshness({ source, now }: { source: SourceStatus; now: Date }) {
  const items = plural(source.item_count, "current item", "current items");
  if (source.stale) {
    const ago = formatAgo(source.last_success, now);
    return (
      <>
        <span className={styles.stale}>Out of date:</span>{" "}
        {ago ? `last read ${ago}` : "no successful read yet"}; OpenPath keeps using its last known items.
      </>
    );
  }
  if (!source.last_success) return <>Waiting for the first read.</>;
  return (
    <>
      <TimeAgo iso={source.last_success} now={now} />.
      <span className={styles.detail}>
        Read {cadenceText(source.cadence_s)}. {items}.
      </span>
    </>
  );
}

function CameraFreshness({ cameras, now }: { cameras: CameraCollection; now: Date }) {
  const readings = cameras.features.filter((feature) => feature.properties.label !== "unknown");
  const newest = cameras.features
    .map((feature) => feature.properties.observed_at)
    .reduce<string | null>((max, value) => (!max || value > max ? value : max), null);
  if (!newest) return <>No camera readings right now. The ice-risk watch still runs.</>;
  return (
    <>
      <TimeAgo iso={newest} now={now} />.
      <span className={styles.detail}>
        {plural(readings.length, "camera view", "camera views")} with a road reading
        {cameras.features.length > readings.length ? `, ${cameras.features.length - readings.length} unknown` : ""}.
      </span>
    </>
  );
}

function FreshnessCell({ freshness, live, now }: { freshness: Freshness; live: Live; now: Date | null }) {
  if (freshness.kind === "fixed") return <>{freshness.text}</>;
  if (live.failed) return <>Not available right now.</>;
  if (live.loading || !now) return <span className={styles.detail}>Checking.</span>;
  if (freshness.kind === "cameras") {
    return live.cameras ? <CameraFreshness cameras={live.cameras} now={now} /> : <>Not available right now.</>;
  }
  const source = live.status?.sources.find((entry) => entry.source === freshness.key);
  if (!source) return <>Not scheduled on this server.</>;
  return <StatusFreshness source={source} now={now} />;
}

function SourceCell({ row }: { row: SourceRow }) {
  return (
    <>
      <span className={styles.sourceName}>{row.href ? <a href={row.href}>{row.name}</a> : row.name}</span>
      {row.details?.map((detail) => (
        <span className={styles.detail} key={detail}>
          {detail}
        </span>
      ))}
      {row.licence && (
        <span className={styles.credit}>
          Licence:{" "}
          {typeof row.licence === "string" ? row.licence : <a href={row.licence.href}>{row.licence.text}</a>}.
        </span>
      )}
      {row.credit && <span className={styles.credit}>Credit: &ldquo;{row.credit}&rdquo;</span>}
    </>
  );
}

function SourceTable({ section, live, now }: { section: SourceSection; live: Live; now: Date | null }) {
  const headingId = `sources-${section.id}`;
  return (
    <section aria-labelledby={headingId} className={styles.section}>
      <h2 id={headingId}>{section.title}</h2>
      {section.intro && (
        <p>
          {section.intro}{" "}
          {section.introLink ? <a href={section.introLink.href}>{section.introLink.text}</a> : null}.
        </p>
      )}
      <div className={styles.tableWrap}>
        {/* Explicit roles keep table semantics when narrow screens show rows as blocks. */}
        <table className={styles.table} role="table" aria-labelledby={headingId}>
          <thead role="rowgroup">
            <tr role="row">
              <th role="columnheader" scope="col" className={styles.colSource}>
                Source
              </th>
              <th role="columnheader" scope="col" className={styles.colUse}>
                Used for
              </th>
              <th role="columnheader" scope="col">
                Freshness
              </th>
            </tr>
          </thead>
          <tbody role="rowgroup">
            {section.rows.map((row) => (
              <tr role="row" key={row.name}>
                <th role="rowheader" scope="row">
                  <SourceCell row={row} />
                </th>
                <td role="cell">
                  <span className={styles.cellLabel} aria-hidden="true">
                    Used for
                  </span>
                  {row.usedFor}
                </td>
                <td role="cell">
                  <span className={styles.cellLabel} aria-hidden="true">
                    Freshness
                  </span>
                  <FreshnessCell freshness={row.freshness} live={live} now={now} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function summary(live: Live): string {
  if (live.loading) return "Checking how fresh each source is.";
  if (live.failed || !live.status) return "";
  const scheduled = live.status.sources;
  const stale = scheduled.filter((source) => source.stale);
  const fresh = scheduled.length - stale.length;
  let text = `${fresh} of ${plural(scheduled.length, "scheduled source", "scheduled sources")} up to date.`;
  if (stale.length) {
    text += ` Out of date: ${stale.map((source) => sourceLabel(source.source)).join(", ")}; OpenPath keeps using their last known items.`;
  }
  return text;
}

export default function SourcesPage() {
  const [live, setLive] = useState<Live>({ status: null, cameras: null, loading: true, failed: false });
  const [now, setNow] = useState<Date | null>(null);

  const load = useCallback(async (signal?: AbortSignal) => {
    try {
      const [status, cameras] = await Promise.all([getStatus(signal), getCameras(signal).catch(() => null)]);
      setLive({ status, cameras, loading: false, failed: false });
    } catch (reason) {
      if (reason instanceof DOMException && reason.name === "AbortError") return;
      setLive((previous) => ({ ...previous, loading: false, failed: true }));
    }
    setNow(new Date());
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    void load(controller.signal);
    const refresh = window.setInterval(() => void load(), REFRESH_MS);
    const tick = window.setInterval(() => setNow(new Date()), 30_000);
    return () => {
      controller.abort();
      window.clearInterval(refresh);
      window.clearInterval(tick);
    };
  }, [load]);

  const others = live.status?.sources.filter((source) => !KNOWN_STATUS_KEYS.has(source.source)) ?? [];
  const replay = live.status?.mode === "replay";

  return (
    <div className="shell">
      <SiteHeader current="sources" />
      <main id="main" className={styles.main}>
        <div className={styles.prose}>
          <h1>Data sources</h1>
          <p className="lead">
            OpenPath brings these public sources together. Each one is read through its official interface and credited as
            its licence asks.
          </p>
          <p>
            Freshness below is live from the OpenPath server. A source that fails keeps its last known items and is marked
            out of date; it never clears what OpenPath already knows.
          </p>
          <StatusLine>{summary(live)}</StatusLine>
          {replay && (
            <p className={styles.freshnessNote}>
              <strong>Replay.</strong> The server is playing back recorded data, not current conditions. Here
              &ldquo;checked&rdquo; means it re-read its recording.
            </p>
          )}
          {live.failed && (
            <div className={styles.actions}>
              <p>
                The OpenPath server did not answer, so live freshness is not shown. On its free plan it sleeps when idle and
                can take a minute to wake.
              </p>
              <Button variant="secondary" onClick={() => void load()}>
                Check again
              </Button>
            </div>
          )}
        </div>

        {SOURCE_SECTIONS.map((section) => (
          <SourceTable key={section.id} section={section} live={live} now={now} />
        ))}

        {others.length > 0 && now && (
          <section aria-labelledby="sources-other" className={styles.prose}>
            <h2 id="sources-other">Also checked</h2>
            <ul>
              {others.map((source) => (
                <li key={source.source}>
                  <strong>{sourceLabel(source.source)}</strong>: <StatusFreshness source={source} now={now} />
                </li>
              ))}
            </ul>
          </section>
        )}

        <div className={styles.prose}>
          <h2>How images are checked</h2>
          <p>
            Before a camera reading is posted, Claude, an AI model made by Anthropic, checks the same frame, and the
            reading is posted only when Claude and OpenPath&rsquo;s model agree. A photo added to a community report is
            checked the same way, by OpenPath&rsquo;s model and by Claude. Frames and photos are sent to the Anthropic API
            in memory; OpenPath keeps none of them, and Anthropic processes them under its API terms. The labels the model
            learned from were also made with Claude&rsquo;s help. Training frames come from 511 Alberta cameras and are
            credited to the Government of Alberta.
          </p>
          <p>
            Measured accuracy and its limits are on <Link href="/about#cameras">the About page</Link>. What OpenPath keeps
            about you is on <Link href="/privacy">the Privacy page</Link>.
          </p>
        </div>
      </main>
      <SiteFooter />
    </div>
  );
}
