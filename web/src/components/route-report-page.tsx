"use client";

// The route report (OP-211, live since OP-301). It reads the trip from the URL (the
// same parameters as the planner, lib/route-link.ts), asks GET /api/route and
// GET /api/status, and shows one route in full. `demo=1` shows the labelled demo the
// planner offers when the route service cannot be reached.

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import ReportConditions, { conditionCount, conditionsSummary } from "./report-conditions";
import { NoRoute, RouteFailure } from "./route-problem";
import SourceFreshness, { freshnessSummary } from "./source-freshness";
import CityWideConditions from "./ui/city-wide-conditions";
import HazardMark from "./ui/hazard-mark";
import { ButtonLink } from "./ui/button";
import Panel from "./ui/panel";
import RouteLine, { cityWideItems } from "./ui/route-line";
import SegmentedChoice from "./ui/segmented-choice";
import SiteFooter from "./ui/site-footer";
import SiteHeader from "./ui/site-header";
import StatusLine from "./ui/status-line";
import { getRoutes, getStatus, isAbort } from "@/lib/api";
import { useDataMode } from "@/lib/data-mode";
import { getMockRouteResponse, getMockStatus } from "@/lib/mock-routes";
import { RouteOut, RouteResponse, StatusResponse, profileLabel } from "@/lib/planner-types";
import { formatDistance, plural } from "@/lib/report-format";
import { ReportParams, parseReportParams, plannerHref, reportHref } from "@/lib/route-link";

type Loaded = { response: RouteResponse; status: StatusResponse | null; demo: boolean };

export default function RouteReportPage() {
  const searchParams = useSearchParams();
  const key = searchParams.toString();
  const params = useMemo(() => parseReportParams(new URLSearchParams(key)), [key]);
  // Choosing a route rewrites only `route=` (and `demo=`) in the address, and Next feeds
  // replaceState back into useSearchParams. The trip alone keys the report, so a choice
  // never remounts it: focus stays on the chosen radio, the status line announces the
  // choice and the routes are not fetched again (OP-408).
  const tripKey = useMemo(() => {
    const trip = new URLSearchParams(key);
    trip.delete("route");
    trip.delete("demo");
    return trip.toString();
  }, [key]);
  if (!params) return <NoTrip />;
  return <RouteReport key={tripKey} params={params} />;
}

function NoTrip() {
  return (
    <div className="shell">
      <SiteHeader />
      <main id="main">
        <div className="report-intro">
          <h1>Route report</h1>
          <p className="lead">This link does not include a trip.</p>
          <p>
            <Link href="/">Plan a route</Link> to see its report.
          </p>
        </div>
      </main>
      <SiteFooter />
    </div>
  );
}

function RouteReport({ params }: { params: ReportParams }) {
  const [loaded, setLoaded] = useState<Loaded | null>(null);
  const [failure, setFailure] = useState<unknown>(null);
  const [attempt, setAttempt] = useState(0);
  const [demo, setDemo] = useState(params.demo);
  // `now` ticks so "Checked X min ago" stays true.
  const [now, setNow] = useState<Date>(() => new Date());
  const [rank, setRank] = useState(params.rank);
  const [announcement, setAnnouncement] = useState("");
  const mode = useDataMode();

  useEffect(() => {
    const timer = window.setInterval(() => setNow(new Date()), 30000);
    return () => window.clearInterval(timer);
  }, []);

  useEffect(() => {
    setLoaded(null);
    setFailure(null);
    const start = new Date();
    setNow(start);
    if (demo) {
      setLoaded({ response: getMockRouteResponse(params, start), status: getMockStatus(start), demo: true });
      return;
    }
    const controller = new AbortController();
    // Freshness is best effort: the report still shows without it.
    const status = getStatus(controller.signal).catch(() => null);
    getRoutes(params, controller.signal)
      .then(async (response) => setLoaded({ response, status: await status, demo: false }))
      .catch((error: unknown) => {
        if (!isAbort(error)) setFailure(error);
      });
    return () => controller.abort();
    // Keyed by its trip, this component only ever sees `params` change its rank or demo
    // flag, both kept in state above, so the trip is read when the effect runs.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [demo, attempt]);

  const retry = useCallback(() => {
    setDemo(false);
    setAttempt((value) => value + 1);
  }, []);

  const routes = loaded?.response.routes ?? [];
  const route = routes.find((option) => option.rank === rank) ?? routes[0];
  const backHref = plannerHref(params, { saved: params.saved });

  return (
    <div className="shell">
      <SiteHeader />
      <main id="main">
        <div className="report-intro">
          <Link className="back-link" href={backHref}>
            Back to the planner
          </Link>
          <h1>
            {params.from.name} to {params.to.name}
          </h1>
          <p className="lead">
            Route report for {profileLabel(params.profile).toLowerCase()}
            {params.mode === "walk_lrt" ? ", walking and LRT" : ""}.
          </p>
          {demo ? (
            <p className="note">
              <strong>Demo data.</strong> These routes, steps and conditions are samples, shown because the route service
              could not be reached. They are not current Edmonton conditions.
            </p>
          ) : mode === "replay" ? (
            <p className="note">
              <strong>Replayed data.</strong> These conditions were recorded earlier and are not current.
            </p>
          ) : null}
        </div>

        {failure ? (
          <div className="page report">
            <div className="page-main">
              <RouteFailure error={failure} onRetry={retry} onDemo={() => setDemo(true)} />
            </div>
          </div>
        ) : !loaded ? (
          <div className="loading-space">
            <StatusLine>Loading the route report.</StatusLine>
          </div>
        ) : !route ? (
          <div className="page report">
            <div className="page-main">
              <NoRoute response={loaded.response} profile={params.profile} />
              <p>
                <Link href={backHref}>Change the trip in the planner</Link>
              </p>
            </div>
          </div>
        ) : (
          <ReportBody
            loaded={loaded}
            route={route}
            params={params}
            now={now}
            announcement={announcement}
            onChoose={(next) => {
              setRank(next.rank);
              window.history.replaceState(
                null,
                "",
                reportHref(params, next.rank, { saved: params.saved, demo: loaded.demo })
              );
              setAnnouncement(
                `Showing route ${next.rank}: ${Math.round(next.eta_min)} min, ${formatDistance(next.distance_m)}. ` +
                  conditionsSummary(next.report) +
                  "."
              );
            }}
          />
        )}
      </main>
      <SiteFooter>
        <p>
          <Link href="/city">See all city conditions</Link>
        </p>
      </SiteFooter>
    </div>
  );
}

type BodyProps = {
  loaded: Loaded;
  route: RouteOut;
  params: ReportParams;
  now: Date;
  announcement: string;
  onChoose: (route: RouteOut) => void;
};

// Sections in the order of web/ACCESSIBILITY.md: summary and freshness, route choice
// and its line, numbered steps, grouped conditions and sources, then the Maps hand-off.
function ReportBody({ loaded, route, params, now, announcement, onChoose }: BodyProps) {
  const { response, status } = loaded;
  const warnings = [...new Set([...response.warnings, ...route.warnings])];
  const empty = conditionCount(route.report.groups) === 0;
  const routes = response.routes;
  const noun = loaded.demo ? ["demo route", "demo routes"] : ["route", "routes"];
  const options = routes.map((option) => ({
    id: String(option.rank),
    label: `Route ${option.rank}${option.rank === 1 ? ", recommended" : ""}: ${Math.round(option.eta_min)} min, ${
      option.lrt ? `${formatDistance(option.lrt.walk_distance_m)} on foot` : formatDistance(option.distance_m)
    }`,
    description: `${option.reason ? `${option.reason}. ` : ""}${conditionsSummary(option.report)}.`
  }));

  return (
    <div className="page report">
      <div className="page-main panel-stack">
        <Panel aria-labelledby="summary-title">
          <h2 id="summary-title">Safety summary</h2>
          <p className="lead">{empty ? "No known obstructions on this route." : `${conditionsSummary(route.report)}.`}</p>
          <p className="muted">
            {status ? freshnessSummary(status.sources, now) : "Source freshness is not available right now."}
          </p>
          {warnings.length > 0 && (
            <ul className="warnings" aria-label="Warnings">
              {warnings.map((warning) => (
                <li key={warning}>{warning}</li>
              ))}
            </ul>
          )}
        </Panel>

        <Panel aria-labelledby="options-title">
          <h2 id="options-title">Choose a route</h2>
          <SegmentedChoice
            legend={`${plural(routes.length, noun[0], noun[1])} for this trip`}
            name="route-choice"
            layout="stack"
            options={options}
            value={String(route.rank)}
            onChange={(id) => {
              const next = routes.find((option) => String(option.rank) === id);
              if (next) onChoose(next);
            }}
          />
          <StatusLine className="route-note">{announcement}</StatusLine>
        </Panel>

        <Panel aria-labelledby={`report-route-${route.rank}`}>
          <RouteLine
            route={route}
            routeCount={routes.length}
            from={params.from.name}
            to={params.to.name}
            profile={params.profile}
            now={now}
            headingId={`report-route-${route.rank}`}
            compact
          />
          <p className="route-note note">Every condition is described in full under Conditions.</p>
        </Panel>
      </div>

      <div className="page-side report-side panel-stack">
        <Panel aria-labelledby="steps-title">
          <h2 id="steps-title">Steps for route {route.rank}</h2>
          <ol className="steps">
            {route.steps.map((step, index) => (
              <li key={`${index}-${step.text}`}>{step.text}</li>
            ))}
          </ol>
        </Panel>

        <Panel aria-labelledby="conditions-title">
          <h2 id="conditions-title">Conditions for route {route.rank}</h2>
          {/* Conditions that cover the whole city, once, as on the planner (OP-410). */}
          <CityWideConditions items={cityWideItems([route])} now={now} />
          {empty ? (
            <div className="empty">
              <HazardMark shape="clear" label="No known obstructions on this route" size={24} />
              <p>
                Nothing is known on or within 50 m of this route in indoor access, sidewalks and paths, roads and
                crossings, or weather and ice. Check again before you leave.
              </p>
            </div>
          ) : (
            <ReportConditions groups={route.report.groups} now={now} />
          )}
          <h3 className="sources-title">Sources checked</h3>
          {status ? (
            <SourceFreshness sources={status.sources} now={now} />
          ) : (
            <p className="muted">The list of sources is not available right now.</p>
          )}
          {route.lrt && <p className="muted">{route.lrt.attribution}.</p>}
        </Panel>

        <Panel aria-labelledby="maps-title">
          <h2 id="maps-title">Open in a maps app</h2>
          <div className="report-actions">
            <ButtonLink variant="secondary" href={route.google_maps_url}>
              Open in Google Maps<span className="sr-only">, route {route.rank}</span>
            </ButtonLink>
            <ButtonLink variant="secondary" href={route.apple_maps_url}>
              Open in Apple Maps<span className="sr-only">, route {route.rank}</span>
            </ButtonLink>
          </div>
          <p className="route-note note">{route.lrt?.maps_note ?? response.apple_maps_note}</p>
        </Panel>
      </div>
    </div>
  );
}
