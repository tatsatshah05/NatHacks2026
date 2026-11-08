import type { ReportItem, RouteOut, TravelProfile } from "@/lib/planner-types";
import { formatDistance, isCityWide, plural } from "@/lib/report-format";
import { HazardShapeIcon, effectShape, effectText } from "./hazard-mark";
import TimeAgo from "./time-ago";

type Stop = { key: string; item: ReportItem };

// Stops are places on the route. City-wide items (the ice-risk watch) apply to every
// route equally, so they are shown once by the page, never as a stop on each line.
//
// Stop order: the API does not say where along the route each item lies, so stops
// follow the report's group order (indoor, sidewalks and paths, roads and crossings,
// weather and ice) and, within a group, the report's own order (on the route, nearby,
// avoided; worst effect first; nearest first). When GET /api/route gains a step index
// per item, sort by that instead.
function stops(route: RouteOut): Stop[] {
  return route.report.groups.flatMap((group) =>
    group.items
      .filter((item) => !isCityWide(item))
      .map((item) => ({ key: `${group.category}-${item.id}-${item.status}`, item }))
  );
}

/** City-wide items across a set of routes, once each. */
export function cityWideItems(routes: RouteOut[]): ReportItem[] {
  const seen = new Map<string, ReportItem>();
  for (const route of routes) {
    for (const group of route.report.groups) {
      for (const item of group.items) if (isCityWide(item) && !seen.has(item.id)) seen.set(item.id, item);
    }
  }
  return [...seen.values()];
}

/** "1 condition on this route, 1 within 50 m, 1 avoided", without city-wide items. */
export function routeTally(route: RouteOut): string {
  const items = stops(route).map(({ item }) => item);
  const count = (status: ReportItem["status"]) => items.filter((item) => item.status === status).length;
  const onRoute = count("on_route");
  const nearby = count("nearby");
  const avoided = count("avoided");
  if (!onRoute && !nearby && !avoided) return "No known conditions on this route.";
  const parts = [`${plural(onRoute, "condition", "conditions")} on this route`];
  if (nearby) parts.push(`${nearby} within 50 m`);
  if (avoided) parts.push(`${avoided} avoided`);
  return `${parts.join(", ")}.`;
}

function statusText(item: ReportItem): string {
  if (item.status === "avoided") return "Avoided";
  if (item.status === "nearby") {
    return item.distance_m === null ? "Near this route" : `${Math.round(item.distance_m)} m from this route`;
  }
  return "On this route";
}

// config/profiles.json removes stairs for these profiles, so their routes are step-free.
function qualifier(profile: TravelProfile): string | null {
  return profile === "wheelchair" || profile === "walker_cane" ? "step-free" : null;
}

type HeadingProps = {
  route: RouteOut;
  routeCount: number;
  profile: TravelProfile;
  headingId: string;
  headingLevel?: 2 | 3;
};

/** "35 min, 2.1 km, step-free" with the ETA at 39 px, then the rank and reason. */
export function RouteHeading({ route, routeCount, profile, headingId, headingLevel = 2 }: HeadingProps) {
  const Heading = headingLevel === 2 ? "h2" : "h3";
  const extra = qualifier(profile);
  const rank =
    route.rank === 1
      ? routeCount > 1
        ? "Recommended route"
        : "The only route found"
      : `Route ${route.rank} of ${routeCount}`;
  return (
    <>
      <Heading className="route-heading" id={headingId}>
        <span className="route-eta">{Math.round(route.eta_min)} min</span>, {formatDistance(route.distance_m)}
        {extra ? (
          <>
            , <span className="nowrap">{extra}</span>
          </>
        ) : (
          ""
        )}
      </Heading>
      <p className="route-reason">
        {rank}
        {route.reason ? `. ${route.reason}` : ""}.
      </p>
    </>
  );
}

type StopsProps = {
  route: RouteOut;
  from: string;
  to: string;
  now: Date;
  /** Draw the line top to bottom once. Off under reduced motion. */
  reveal?: boolean;
  /** Title and status only, for pages where the full condition report follows. */
  compact?: boolean;
};

/** The line itself: start, each condition on or near the route, end. */
export function RouteStops({ route, from, to, now, reveal = false, compact = false }: StopsProps) {
  return (
    <ol
      className={reveal ? "route-line is-revealing" : "route-line"}
      // Safari drops list semantics when list-style is none; keep them.
      role="list"
      aria-label={`Route ${route.rank}: start, conditions and end`}
    >
      <li className="stop is-first">
        <span className="stop-mark">
          <HazardShapeIcon shape="start" size={24} />
        </span>
        <div className="stop-body">
          <p className="stop-title">{from}</p>
          <p className="stop-label">Start</p>
        </div>
      </li>
      {stops(route).map(({ key, item }) => {
        const avoided = item.status === "avoided";
        return (
          <li className={avoided ? "stop is-avoided" : "stop"} key={key}>
            {avoided && (
              <svg className="stop-kink" viewBox="0 0 56 100" preserveAspectRatio="none" aria-hidden="true" focusable="false">
                <path
                  d="M16 0 V22 L6 36 V64 L16 78 V100"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="4"
                  strokeLinejoin="round"
                  vectorEffect="non-scaling-stroke"
                />
              </svg>
            )}
            <span className="stop-mark">
              <HazardShapeIcon shape={effectShape(item.effect)} size={24} />
            </span>
            <div className="stop-body">
              <p className="stop-title">{item.title}</p>
              <p className="stop-label">
                <strong>{statusText(item)}</strong>: {effectText(item.effect).toLowerCase()}
              </p>
              {!compact && item.detail && <p className="stop-detail">{item.detail}</p>}
              {!compact && (
                <p className="stop-checked">
                  <TimeAgo iso={item.observed_at} now={now} />
                </p>
              )}
            </div>
          </li>
        );
      })}
      <li className="stop is-last">
        <span className="stop-mark">
          <HazardShapeIcon shape="start" size={24} />
        </span>
        <div className="stop-body">
          <p className="stop-title">{to}</p>
          <p className="stop-label">End</p>
        </div>
      </li>
    </ol>
  );
}

type Props = HeadingProps & Omit<StopsProps, "route">;

/** Heading sentence, reason and the line: the whole route at a glance. */
export default function RouteLine({ route, routeCount, profile, headingId, headingLevel, ...stopProps }: Props) {
  return (
    <div className="route">
      <RouteHeading
        route={route}
        routeCount={routeCount}
        profile={profile}
        headingId={headingId}
        headingLevel={headingLevel}
      />
      <RouteStops route={route} {...stopProps} />
    </div>
  );
}
