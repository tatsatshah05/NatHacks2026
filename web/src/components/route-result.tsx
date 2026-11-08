"use client";

import { ReactNode, useState } from "react";
import Button, { ButtonLink } from "./ui/button";
import { RouteHeading, RouteStops, routeTally } from "./ui/route-line";
import type { PlannerQuery, RouteOut } from "@/lib/planner-types";

type Props = {
  route: RouteOut;
  routeCount: number;
  query: PlannerQuery;
  now: Date;
  appleMapsNote: string;
  /** The route report's URL for this route. */
  reportLink: string;
  /** This route is the one drawn as the thick line on the map. */
  selected: boolean;
  /** Draw this route as the thick line; `reveal` brings the map into view. */
  onSelect: (rank: number, reveal: boolean) => void;
  /** An alternate was opened or closed. */
  onToggle: (rank: number, open: boolean) => void;
  /** "Replayed data, not current conditions." or the demo label; null when live. */
  dataLabel: string | null;
  /** The route's "Save this route" button (OP-306), placed with the other actions. */
  saveAction?: ReactNode;
};

// One route in the planner's results. The recommended route shows its whole line.
// Alternates are comparison rows (heading, reason, count) with a disclosure button that
// opens the line and the route's buttons in place.
export default function RouteResult({
  route,
  routeCount,
  query,
  now,
  appleMapsNote,
  reportLink,
  selected,
  onSelect,
  onToggle,
  dataLabel,
  saveAction
}: Props) {
  const recommended = route.rank === 1;
  const [open, setOpen] = useState(false);
  const headingId = `route-${route.rank}-heading`;
  const detailsId = `route-${route.rank}-details`;
  const mapsNote = route.lrt?.maps_note ?? appleMapsNote;

  const actions = (
    <>
      {route.warnings.length > 0 && (
        <ul className="warnings route-warnings" aria-label={`Notes for route ${route.rank}`}>
          {route.warnings.map((warning) => (
            <li key={warning}>{warning}</li>
          ))}
        </ul>
      )}
      <div className="route-actions">
        <ButtonLink variant="secondary" href={reportLink}>
          View route report<span className="sr-only"> for route {route.rank}</span>
        </ButtonLink>
        <ButtonLink variant="secondary" href={route.google_maps_url}>
          Open in Google Maps<span className="sr-only">, route {route.rank}</span>
        </ButtonLink>
        <ButtonLink variant="secondary" href={route.apple_maps_url}>
          Open in Apple Maps<span className="sr-only">, route {route.rank}</span>
        </ButtonLink>
        {saveAction}
      </div>
      <p className="route-note note">{mapsNote}</p>
      {route.lrt && <p className="route-note note">{route.lrt.attribution}.</p>}
    </>
  );

  const mapLine = (
    <p className="route-map-line">
      {selected ? (
        <span className="route-shown">
          <svg width="28" height="10" viewBox="0 0 28 10" aria-hidden="true" focusable="false">
            <path d="M2 5h24" stroke="currentColor" strokeWidth="6" strokeLinecap="round" />
          </svg>
          Shown on the map as the thick line.
        </span>
      ) : null}
      <Button variant="text" onClick={() => onSelect(route.rank, true)}>
        Show on the map<span className="sr-only">, route {route.rank}</span>
      </Button>
    </p>
  );

  const summary = (
    <>
      <RouteHeading route={route} routeCount={routeCount} profile={query.profile} headingId={headingId} />
      <p className="route-tally">
        {routeTally(route)}
        {dataLabel ? <span className="route-data"> {dataLabel}</span> : null}
      </p>
    </>
  );

  if (recommended) {
    return (
      <div className="route">
        {summary}
        {mapLine}
        <RouteStops route={route} from={query.from.name} to={query.to.name} now={now} reveal />
        {actions}
      </div>
    );
  }

  function toggle() {
    const next = !open;
    setOpen(next);
    onToggle(route.rank, next);
  }

  return (
    <div className="route">
      {summary}
      <Button variant="secondary" aria-expanded={open} aria-controls={detailsId} onClick={toggle}>
        {open ? `Hide route ${route.rank}` : `Show route ${route.rank}`}
      </Button>
      {/* Always in the DOM so aria-controls points at it; the line draws when it opens. */}
      <div id={detailsId} hidden={!open}>
        {mapLine}
        <RouteStops route={route} from={query.from.name} to={query.to.name} now={now} reveal />
        {actions}
      </div>
    </div>
  );
}
