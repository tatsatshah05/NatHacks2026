"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import ReportConditions, { conditionCount, conditionsSummary } from "./report-conditions";
import SiteHeader from "./site-header";
import SourceFreshness, { freshnessSummary } from "./source-freshness";
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
  // The demo is built once in the browser; `now` ticks so "checked X min ago" stays true.
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

  const profile = profileLabel(params.profile);
  const route = demo?.response.routes.find((option) => option.rank === rank) ?? demo?.response.routes[0];
  const emptyToggle = reportHref(params, rank) + (params.empty ? "" : "&demo=empty");

  return (
    <div className="app-shell report-shell">
      <SiteHeader current="report" />
      <main id="main">
        <Link className="report-back" href="/">
          <span aria-hidden="true">← </span>Back to the planner
        </Link>

        <section className="report-hero" aria-labelledby="report-title">
          <p className="eyebrow">Route report · {profile}</p>
          <h1 id="report-title">
            {params.from.name} <span className="sr-only">to</span>
            <span aria-hidden="true">→</span> {params.to.name}
          </h1>
          <div className="demo-note">
            <p>
              <strong>Demo data.</strong> These routes, steps and conditions are samples so the screen can be
              reviewed before it connects to the route service. They are not current Edmonton conditions.
              {params.usedDefaultTrip ? " No trip was given, so this is the demo trip." : ""}
            </p>
            <Link href={emptyToggle}>
              {params.empty ? "Show the demo with conditions" : "Show the demo with no known obstructions"}
            </Link>
          </div>
        </section>

        {!demo || !now || !route ? (
          <p className="route-status" role="status">Loading the route report…</p>
        ) : (
          <ReportBody
            response={demo.response}
            status={demo.status}
            route={route}
            now={now}
            announcement={announcement}
            onChoose={(next) => {
              setRank(next.rank);
              setAnnouncement(
                `Showing route ${next.rank}: ${Math.round(next.eta_min)} min, ${formatDistance(next.distance_m)}. ` +
                  conditionsSummary(next.report) + "."
              );
            }}
          />
        )}
      </main>
      <footer>
        <strong>OpenPath</strong>
        <span>Conditions come from public data and can change. Check again before you leave.</span>
        <Link href="/city">View all city conditions</Link>
      </footer>
    </div>
  );
}

type BodyProps = {
  response: RouteResponse;
  status: StatusResponse;
  route: RouteOut;
  now: Date;
  announcement: string;
  onChoose: (route: RouteOut) => void;
};

// Sections in the order of web/ACCESSIBILITY.md: summary and freshness, route choice,
// numbered steps, grouped conditions and sources, then the Maps hand-off.
function ReportBody({ response, status, route, now, announcement, onChoose }: BodyProps) {
  const warnings = [...response.warnings, ...route.warnings];
  const empty = conditionCount(route.report.groups) === 0;
  const routes = response.routes;

  return (
    <>
      <section className="report-summary" aria-labelledby="summary-title">
        <div>
          <p className="eyebrow">Safety summary · Route {route.rank} of {routes.length}</p>
          <h2 id="summary-title">
            {empty ? "No known obstructions on this route" : `${conditionsSummary(route.report)}.`}
          </h2>
        </div>
        <p>
          <strong>{Math.round(route.eta_min)} min</strong>, {formatDistance(route.distance_m)}.{" "}
          {freshnessSummary(status.sources, now)}
        </p>
        {warnings.length > 0 && (
          <ul className="report-warnings" aria-label="Warnings">
            {warnings.map((warning) => <li key={warning}>{warning}</li>)}
          </ul>
        )}
      </section>

      <section className="report-section" aria-labelledby="options-title">
        <h2 id="options-title">Route options</h2>
        <fieldset className="route-options">
          <legend>
            Choose a route: {plural(routes.length, "demo route", "demo routes")} for this trip
          </legend>
          {routes.map((option) => (
            <label className={`route-option${option.rank === route.rank ? " selected" : ""}`} key={option.rank}>
              <input
                type="radio"
                name="route-choice"
                value={option.rank}
                checked={option.rank === route.rank}
                onChange={() => onChoose(option)}
              />
              <span>
                <strong>
                  Route {option.rank}
                  {option.rank === 1 ? " (recommended)" : ""}: {Math.round(option.eta_min)} min,{" "}
                  {formatDistance(option.distance_m)}
                </strong>
                <small>
                  {option.reason ? `${option.reason}. ` : ""}
                  {conditionsSummary(option.report)}.
                </small>
              </span>
            </label>
          ))}
        </fieldset>
        <p className="route-status" role="status">{announcement}</p>
      </section>

      <section className="report-section" aria-labelledby="steps-title">
        <h2 id="steps-title">Steps for route {route.rank}</h2>
        <ol className="route-steps">
          {route.steps.map((step, index) => (
            <li key={`${index}-${step.text}`}>
              <span className="step-number" aria-hidden="true">{index + 1}</span>
              <span>{step.text}</span>
            </li>
          ))}
        </ol>
      </section>

      <section className="report-section" aria-labelledby="conditions-title">
        <h2 id="conditions-title">Conditions for route {route.rank}</h2>
        {empty ? (
          <div className="empty-report">
            <span className="empty-check" aria-hidden="true">✓</span>
            <h3>No known obstructions on this route</h3>
            <p>
              Nothing on or within 50 m of this route in indoor access, sidewalks and paths, roads and crossings, or
              weather and ice. {freshnessSummary(status.sources, now)}
            </p>
          </div>
        ) : (
          <ReportConditions groups={route.report.groups} now={now} />
        )}
        <h3 className="sources-title">Sources checked</h3>
        <SourceFreshness sources={status.sources} now={now} />
      </section>

      <section className="report-section" aria-labelledby="maps-title">
        <h2 id="maps-title">Open in a maps app</h2>
        <div className="maps-links">
          <a className="outline-button" href={route.google_maps_url} rel="noreferrer">
            Open route {route.rank} in Google Maps
          </a>
          <a className="outline-button" href={route.apple_maps_url} rel="noreferrer">
            Open in Apple Maps
          </a>
        </div>
        <p className="maps-note">{response.apple_maps_note}</p>
      </section>
    </>
  );
}
