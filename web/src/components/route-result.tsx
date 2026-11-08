"use client";

import { useState } from "react";
import SaveRouteButton from "./save-route-button";
import Button, { ButtonLink } from "./ui/button";
import { RouteHeading, RouteStops, routeTally } from "./ui/route-line";
import type { PlannerQuery, RouteOut } from "@/lib/planner-types";
import { reportHref } from "@/lib/route-link";

type Props = {
  route: RouteOut;
  routeCount: number;
  query: PlannerQuery;
  now: Date;
  appleMapsNote: string;
};

// One route in the planner's results. The recommended route shows its whole line.
// Alternates are comparison rows (heading, reason, count) with a disclosure button that
// opens the line and the route's buttons in place.
export default function RouteResult({ route, routeCount, query, now, appleMapsNote }: Props) {
  const recommended = route.rank === 1;
  const [open, setOpen] = useState(false);
  const headingId = `route-${route.rank}-heading`;
  const detailsId = `route-${route.rank}-details`;

  const actions = (
    <>
      <div className="route-actions">
        <ButtonLink variant="secondary" href={reportHref(query, route.rank)}>
          View route report<span className="sr-only"> for route {route.rank}</span>
        </ButtonLink>
        <SaveRouteButton query={query} rank={route.rank} />
        <ButtonLink variant="secondary" href={route.google_maps_url}>
          Open in Google Maps<span className="sr-only">, route {route.rank}</span>
        </ButtonLink>
        <ButtonLink variant="secondary" href={route.apple_maps_url}>
          Open in Apple Maps<span className="sr-only">, route {route.rank}</span>
        </ButtonLink>
      </div>
      <p className="route-note note">{appleMapsNote}</p>
    </>
  );

  const summary = (
    <>
      <RouteHeading route={route} routeCount={routeCount} profile={query.profile} headingId={headingId} />
      <p className="route-tally">{routeTally(route)}</p>
    </>
  );

  if (recommended) {
    return (
      <div className="route">
        {summary}
        <RouteStops route={route} from={query.from.name} to={query.to.name} now={now} reveal />
        {actions}
      </div>
    );
  }

  return (
    <div className="route">
      {summary}
      <Button variant="secondary" aria-expanded={open} aria-controls={detailsId} onClick={() => setOpen((value) => !value)}>
        {open ? `Hide route ${route.rank}` : `Show route ${route.rank}`}
      </Button>
      {/* Always in the DOM so aria-controls points at it; the line draws when it opens. */}
      <div id={detailsId} hidden={!open}>
        <RouteStops route={route} from={query.from.name} to={query.to.name} now={now} reveal />
        {actions}
      </div>
    </div>
  );
}
