"use client";

import dynamic from "next/dynamic";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { CameraFeature, Filters, PriorityResponse, StatusResponse } from "@/lib/city";
import {
  CAMERA_VIEWER_URL,
  CONFIDENCE_WORDS,
  NO_FILTERS,
  applyFilters,
  cameraName,
  downloadCsv,
  getCameras,
  getPriority,
  getStatus,
  hasFilters,
  readingWords,
  scoreWords
} from "@/lib/city";
import { effectLabel, plural, sourceLabel } from "@/lib/report-format";
import { freshnessSummary } from "../source-freshness";
import Button from "../ui/button";
import HazardMark, { HAZARD_SHAPES, SHAPE_TEXT, effectShape } from "../ui/hazard-mark";
import LazyMount from "../ui/lazy-mount";
import SiteFooter from "../ui/site-footer";
import SiteHeader from "../ui/site-header";
import StatusLine from "../ui/status-line";
import TimeAgo from "../ui/time-ago";
import CameraGlyph from "./camera-glyph";
import { groupCameras } from "./cameras";
import CityFilters from "./city-filters";
import { PriorityRow } from "./priority-list";
import RoadCameras from "./road-cameras";
import SourceStatusPanel from "./source-status";
import styles from "./city-dashboard.module.css";

// MapLibre needs the browser; the lists carry everything without it.
const CityMap = dynamic(() => import("./city-map"), {
  ssr: false,
  loading: () => <div className={`map-canvas ${styles.mapCanvas}`} aria-hidden="true" />
});

// Rows per page: 25 on a laptop, 10 on a phone, where each row is taller.
const PAGE_WIDE = 25;
const PAGE_NARROW = 10;

// The four shapes a barrier can take, for the legend under the map.
const LEGEND = HAZARD_SHAPES.filter(
  (shape): shape is "blocked" | "ice" | "snow" | "caution" => shape !== "start" && shape !== "clear"
);

type Problems = { ranking: string | null; status: string | null; cameras: string | null };
const NO_PROBLEMS: Problems = { ranking: null, status: null, cameras: null };

function message(reason: unknown, fallback: string): string {
  return reason instanceof Error ? reason.message : fallback;
}

export default function CityDashboard() {
  const [ranking, setRanking] = useState<PriorityResponse | null>(null);
  const [status, setStatus] = useState<StatusResponse | null>(null);
  const [cameraFeatures, setCameraFeatures] = useState<CameraFeature[]>([]);
  const [problems, setProblems] = useState<Problems>(NO_PROBLEMS);
  const [loading, setLoading] = useState(true);
  const [loadedAt, setLoadedAt] = useState<Date | null>(null);
  const [now, setNow] = useState<Date>(() => new Date());
  const [filters, setFilters] = useState<Filters>(NO_FILTERS);
  const [pageSize, setPageSize] = useState(PAGE_WIDE);
  const [limit, setLimit] = useState<number | null>(null); // null: one page
  const [selectedId, setSelectedId] = useState<string | null>(null);
  // After "Show more", focus moves to the first new row; after "Show the top N
  // only", to the button that takes its place.
  const focusRank = useRef<number | null>(null);
  const moreRef = useRef<HTMLButtonElement>(null);
  const focusMore = useRef(false);

  useEffect(() => {
    const wide = window.matchMedia("(min-width: 640px)");
    const apply = () => setPageSize(wide.matches ? PAGE_WIDE : PAGE_NARROW);
    apply();
    wide.addEventListener("change", apply);
    return () => wide.removeEventListener("change", apply);
  }, []);

  const load = useCallback(async (signal?: AbortSignal) => {
    setLoading(true);
    const [rankingResult, statusResult, camerasResult] = await Promise.allSettled([
      getPriority(signal),
      getStatus(signal),
      getCameras(signal)
    ]);
    if (signal?.aborted) return;
    // A failed request keeps the last good data on screen and says so.
    if (rankingResult.status === "fulfilled") setRanking(rankingResult.value);
    if (statusResult.status === "fulfilled") setStatus(statusResult.value);
    if (camerasResult.status === "fulfilled") setCameraFeatures(camerasResult.value);
    setProblems({
      ranking:
        rankingResult.status === "rejected" ? message(rankingResult.reason, "The ranking service did not answer.") : null,
      status:
        statusResult.status === "rejected" ? message(statusResult.reason, "The status service did not answer.") : null,
      cameras:
        camerasResult.status === "rejected" ? message(camerasResult.reason, "The camera service did not answer.") : null
    });
    const at = new Date();
    if (rankingResult.status === "fulfilled") setLoadedAt(at);
    setNow(at);
    setLoading(false);
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    void load(controller.signal);
    const timer = window.setInterval(() => setNow(new Date()), 30000);
    return () => {
      controller.abort();
      window.clearInterval(timer);
    };
  }, [load]);

  const items = useMemo(() => ranking?.items ?? [], [ranking]);
  const filtered = useMemo(() => applyFilters(items, filters), [items, filters]);
  const visible = Math.min(limit ?? pageSize, filtered.length);
  const shown = filtered.slice(0, visible);
  const remaining = filtered.length - visible;
  const cameras = useMemo(() => groupCameras(cameraFeatures), [cameraFeatures]);
  const filtering = hasFilters(filters);
  const replay = ranking?.mode === "replay" || status?.mode === "replay";

  useEffect(() => {
    if (focusRank.current !== null) {
      document.getElementById(`rank-${focusRank.current}-title`)?.focus();
      focusRank.current = null;
    } else if (focusMore.current) {
      moreRef.current?.focus();
      focusMore.current = false;
    }
  }, [visible]);

  const showMore = (next: number) => {
    focusRank.current = filtered[visible]?.rank ?? null;
    setLimit(next);
  };
  const changeFilters = (next: Filters) => {
    setFilters(next);
    setLimit(null);
  };

  const toggle = useCallback((id: string) => setSelectedId((current) => (current === id ? null : id)), []);
  const select = useCallback((id: string) => setSelectedId(id), []);

  const count = (() => {
    if (!ranking) return loading ? "Loading the ranking." : "";
    const total = items.length;
    if (!total) return "";
    if (!filtered.length) return `No barriers match these filters. Clear the filters to see all ${total}.`;
    const of = filtering ? `${filtered.length} of ${total} barriers match` : plural(total, "barrier", "barriers");
    if (!remaining) return filtering ? `${of}.` : `${of}, all shown.`;
    return `${of}. Showing the top ${visible}.`;
  })();

  const selectedItem = items.find((item) => item.id === selectedId);
  const selectedCamera = selectedItem ? undefined : cameras.find((camera) => camera.id === selectedId);
  const counts = ranking?.pedestrian_counts;

  return (
    <div className="shell">
      <SiteHeader current="city" />

      <main id="main" className={styles.page}>
        <div className={styles.intro}>
          <h1 className="page-title">Barriers to clear first</h1>
          <p className="lead">
            Every barrier OpenPath knows about, ranked by the saved trips it gets in the way of and the people who
            walk nearby.
          </p>
          <details className={styles.method}>
            <summary>How the ranking works</summary>
            <p>
              A barrier moves up for each saved route whose path it is on. Routes already detouring around a closure
              still count, because the closure is what pushed them off their direct way.
              {ranking && ` ${plural(ranking.saved_routes, "saved route is", "saved routes are")} checked.`}
            </p>
            <p>
              It also moves up for the people City pedestrian counters see within 400 m, using a typical day
              {counts?.window_start && counts.window_end
                ? ` (the median day from ${formatDay(counts.window_start)} to ${formatDay(counts.window_end)})`
                : ""}
              . Closer counters count more. About 1,000 people a day weigh as much as one more saved route.
            </p>
            <p>
              Closures count most, because they stop everyone. Snow and ice risk count less, because they stop some
              people. Take-care notices count least. Notices that cover a whole area come last: there is no single
              place to clear.
            </p>
            {counts && (
              <p>
                Pedestrian counts: City of Edmonton,{" "}
                <a href={counts.url} rel="noreferrer">
                  Daily Pedestrian and Bike Counts
                </a>
                , {counts.attribution}.
              </p>
            )}
          </details>

          <div className="freshness">
            <p className="muted">
              {loadedAt ? <TimeAgo iso={loadedAt.toISOString()} now={now} verb="Updated" /> : "Not loaded yet."}
            </p>
            <Button variant="secondary" onClick={() => void load()} disabled={loading}>
              Refresh list
            </Button>
          </div>
          {status && status.sources.length > 0 && (
            <p>
              {freshnessSummary(status.sources, now)} <a href="#source-status-title">See source status</a>
            </p>
          )}

          {problems.ranking && (
            <div className="alert" role="alert">
              <p>
                <strong>The ranking could not be loaded.</strong> {problems.ranking}{" "}
                {ranking ? "The list below is from the last good load." : ""} Try again in a minute.
              </p>
              <Button variant="secondary" onClick={() => void load()} disabled={loading}>
                Try again
              </Button>
            </div>
          )}

          {items.length > 0 && (
            <CityFilters
              items={items}
              filters={filters}
              onChange={changeFilters}
              onClear={() => {
                changeFilters(NO_FILTERS);
                document.getElementById("city-search")?.focus();
              }}
            />
          )}

          <div className={styles.countRow}>
            <StatusLine>{count}</StatusLine>
            {filtered.length > 0 && (
              <Button
                variant="secondary"
                onClick={() => downloadCsv(filtered, new Date(), filtering)}
              >
                Download {filtering ? "these" : "all"} {filtered.length} as CSV
              </Button>
            )}
            {items.length > 0 && <a href="#city-map">Go to the map</a>}
          </div>
        </div>

        <div className={ranking || !loading ? styles.list : `${styles.list} loading-space`}>
          {/* The rows are h3s; this h2 keeps the outline in order (h1, h2, h3) without
              adding a visible heading above a list that already has its count. */}
          <h2 className="sr-only" id="barriers-title">
            Barriers, most important first
          </h2>
          {ranking && !items.length && !problems.ranking && (
            <p className={styles.empty}>
              No barriers are reported right now. The list fills in as the City&apos;s sources report closures,
              outages and snow.
            </p>
          )}
          {shown.length > 0 && (
            <ol className={styles.rows} aria-labelledby="barriers-title">
              {shown.map((item) => (
                <PriorityRow
                  key={item.id}
                  item={item}
                  now={now}
                  selected={selectedId === item.id}
                  onToggle={toggle}
                />
              ))}
            </ol>
          )}
          {remaining > 0 && (
            <div className={styles.more}>
              {/* A plain button styled as Button, because Button does not take a ref. */}
              <button
                type="button"
                className="button button-secondary"
                ref={moreRef}
                onClick={() => showMore(visible + pageSize)}
              >
                Show {Math.min(pageSize, remaining)} more
              </button>
              {remaining > pageSize && (
                <Button variant="text" onClick={() => showMore(filtered.length)}>
                  Show all {filtered.length}
                </Button>
              )}
            </div>
          )}
          {!remaining && filtered.length > pageSize && (
            <div className={styles.more}>
              <Button
                variant="text"
                onClick={() => {
                  focusMore.current = true;
                  setLimit(null);
                }}
              >
                Show the top {pageSize} only
              </Button>
            </div>
          )}
        </div>

        <div className={styles.side} id="city-map" tabIndex={-1}>
          <h2 className="sr-only">Map</h2>
          <LazyMount placeholder={<div className={`map-canvas ${styles.mapCanvas}`} aria-hidden="true" />}>
            <CityMap items={filtered} cameras={cameras} selectedId={selectedId} onSelect={select} />
          </LazyMount>
          {selectedItem && (
            <div className={styles.detail}>
              <h3>
                <span className="sr-only">Selected: </span>
                Rank {selectedItem.rank}
              </h3>
              <HazardMark shape={effectShape(selectedItem.effect)} label={selectedItem.title} />
              {selectedItem.detail && <p>{selectedItem.detail}</p>}
              <p>{selectedItem.why}</p>
              <p className={styles.meta}>
                {effectLabel(selectedItem.effect)}. {CONFIDENCE_WORDS[selectedItem.confidence]}.{" "}
                <TimeAgo iso={selectedItem.observed_at} now={now} />. Source: {sourceLabel(selectedItem.source)},{" "}
                {selectedItem.attribution}.
              </p>
              {selectedItem.url && (
                <p>
                  <a href={selectedItem.url} rel="noreferrer">
                    Open the source record<span className="sr-only"> for {selectedItem.title}</span>
                  </a>
                </p>
              )}
            </div>
          )}
          {selectedCamera && (
            <div className={styles.detail}>
              <h3 className={styles.cameraName}>
                <CameraGlyph size={20} className={styles.cameraIcon} />
                <span className="sr-only">Selected camera: </span>
                {cameraName({ camera_id: selectedCamera.cameraId, view_id: selectedCamera.views[0].properties.view_id })}
              </h3>
              <ul className={styles.views}>
                {selectedCamera.views.map((view, index) => {
                  const score = scoreWords(view.properties.score);
                  return (
                    <li key={view.id}>
                      {selectedCamera.views.length > 1 && <>View {index + 1}: </>}
                      <strong>{readingWords(view.properties)}</strong>
                      {score && <>. {score}</>}. <TimeAgo iso={view.properties.observed_at} now={now} />.
                    </li>
                  );
                })}
              </ul>
              <p>
                <a href={CAMERA_VIEWER_URL} rel="noreferrer">
                  Open 511 Alberta&apos;s camera list
                </a>
              </p>
            </div>
          )}
          <ul className="legend" aria-label="Map shapes">
            {LEGEND.map((shape) => (
              <li key={shape}>
                <HazardMark shape={shape} label={SHAPE_TEXT[shape]} size={18} />
              </li>
            ))}
            <li>
              <span className="hazard-mark">
                <CameraGlyph size={18} />
                <span className="hazard-label">Road camera, with its reading</span>
              </span>
            </li>
          </ul>
          <p className="muted">
            The map shows the barriers that match the filters, and every road camera. The lists hold everything it
            shows.
          </p>
        </div>

        <div className={styles.after}>
          <RoadCameras
            cameras={cameras}
            error={problems.cameras}
            replay={replay}
            now={now}
            selectedId={selectedId}
            onToggle={toggle}
          />
          <SourceStatusPanel status={status} error={problems.status} now={now} />
        </div>
      </main>

      <SiteFooter>
        <p>Map data © OpenStreetMap contributors. Road camera data: 511 Alberta, Government of Alberta.</p>
      </SiteFooter>
    </div>
  );
}

/** "Oct 11" for a dataset day: a calendar date, not an instant. */
function formatDay(day: string): string {
  return new Intl.DateTimeFormat("en-CA", { month: "short", day: "numeric", timeZone: "UTC" }).format(
    new Date(`${day}T12:00:00Z`)
  );
}
