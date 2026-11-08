"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import ReportConditions, { conditionCount, conditionsSummary } from "./report-conditions";
import SourceFreshness, { freshnessSummary } from "./source-freshness";
import HazardMark from "./ui/hazard-mark";
import { ButtonLink } from "./ui/button";
import Panel from "./ui/panel";
import RouteLine from "./ui/route-line";
import SegmentedChoice from "./ui/segmented-choice";
import SiteFooter from "./ui/site-footer";
import SiteHeader from "./ui/site-header";
import StatusLine from "./ui/status-line";
import { getMockRouteResponse, getMockStatus } from "@/lib/mock-routes";
import { RouteOut, RouteResponse, StatusResponse, profileLabel } from "@/lib/planner-types";
import { formatDistance, plural } from "@/lib/report-format";
import { ReportParams, parseReportParams, reportHref } from "@/lib/route-link";

type Demo = { response: RouteResponse; status: StatusResponse };

export default function RouteReportPage() {
  const searchParams = useSearchParams();
  const params = useMemo(() => parseReportParams(new URLSearchParams(searchParams.toString())), [searchParams]);
  return <RouteReport key={searchParams.toString()} params={params} />;
}

function RouteReport({ params }: { params: ReportParams }) {
  // The demo is built once in the browser; `now` ticks so "Checked X min ago" stays true.
  const [demo, setDemo] = useState<Demo | null>(null);
  const [now, setNow] = useState<Date | null>(null);
  const [rank, setRank] = useState(params.rank);
  const [announcement, setAnnouncement] = useState("");

  useEffect(() => {
    const start = new Date();
    setDemo({ response: getMockRouteResponse(params, start, { empty: params.empty }), status: getMockStatus(start) });
    setNow(start);
    const timer = window.setInterval(() => setNow(new Date()), 30000);
    return () => window.clearInterval(timer);
  }, [params]);

  const route = demo?.response.routes.find((option) => option.rank === rank) ?? demo?.response.routes[0];
  const emptyToggle = reportHref(params, rank) + (params.empty ? "" : "&demo=empty");

  return (
    <div className="shell">
      <SiteHeader />
      <main id="main">
        <div className="report-intro">
          <Link className="back-link" href="/">
            Back to the planner
          </Link>
          <h1>
            {params.from.name} to {params.to.name}
          </h1>
          <p className="lead">Route report for {profileLabel(params.profile).toLowerCase()}.</p>
          <p className="note">
            <strong>Demo data.</strong> These routes, steps and conditions are samples so the screen can be reviewed
            before it connects to the route service. They are not current Edmonton conditions.
            {params.usedDefaultTrip ? " No trip was given, so this is the demo trip." : ""}{" "}
            <Link href={emptyToggle}>
              {params.empty ? "Show the demo with conditions" : "Show the demo with no known obstructions"}
            </Link>
          </p>
        </div>

        {!demo || !now || !route ? (
          <StatusLine>Loading the route report.</StatusLine>
        ) : (
          <ReportBody
            response={demo.response}
            status={demo.status}
            route={route}
            from={params.from.name}
            to={params.to.name}
            params={params}
            now={now}
            announcement={announcement}
            onChoose={(next) => {
              setRank(next.rank);
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
  response: RouteResponse;
  status: StatusResponse;
  route: RouteOut;
  from: string;
  to: string;
  params: ReportParams;
  now: Date;
  announcement: string;
  onChoose: (route: RouteOut) => void;
};

// Sections in the order of web/ACCESSIBILITY.md: summary and freshness, route choice
// and its line, numbered steps, grouped conditions and sources, then the Maps hand-off.
function ReportBody({ response, status, route, from, to, params, now, announcement, onChoose }: BodyProps) {
  const warnings = [...response.warnings, ...route.warnings];
  const empty = conditionCount(route.report.groups) === 0;
  const routes = response.routes;
  const options = routes.map((option) => ({
    id: String(option.rank),
    label: `Route ${option.rank}${option.rank === 1 ? ", recommended" : ""}: ${Math.round(option.eta_min)} min, ${formatDistance(option.distance_m)}`,
    description: `${option.reason ? `${option.reason}. ` : ""}${conditionsSummary(option.report)}.`
  }));

  return (
    <div className="page report">
      <div className="page-main panel-stack">
        <Panel aria-labelledby="summary-title">
          <h2 id="summary-title">Safety summary</h2>
          <p className="lead">{empty ? "No known obstructions on this route." : `${conditionsSummary(route.report)}.`}</p>
          <p className="muted">{freshnessSummary(status.sources, now)}</p>
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
            legend={`${plural(routes.length, "demo route", "demo routes")} for this trip`}
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
            from={from}
            to={to}
            profile={params.profile}
            now={now}
            headingId={`report-route-${route.rank}`}
          />
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
          <SourceFreshness sources={status.sources} now={now} />
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
          <p className="route-note note">{response.apple_maps_note}</p>
        </Panel>
      </div>
    </div>
  );
}
