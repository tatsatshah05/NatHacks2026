import type { ReportItem, RouteOut, TravelProfile } from "@/lib/planner-types";
import { formatDistance } from "@/lib/report-format";
import { HazardShapeIcon, effectShape, effectText } from "./hazard-mark";
import TimeAgo from "./time-ago";

type Props = {
  route: RouteOut;
  routeCount: number;
  from: string;
  to: string;
  profile: TravelProfile;
  now: Date;
  headingId: string;
  headingLevel?: 2 | 3;
  /** Draw the line top to bottom once, when routes arrive. Off under reduced motion. */
  reveal?: boolean;
};

type Stop = { key: string; item: ReportItem };

// The route as a transit line: start, every known condition as a stop, end. Avoided
// conditions sit beside the line, where it kinks away from them.
//
// Stop order: the API does not say where along the route each item lies, so stops
// follow the report's group order (indoor, sidewalks and paths, roads and crossings,
// weather and ice) and, within a group, the report's own order (on the route, nearby,
// avoided; worst effect first; nearest first). When GET /api/route gains a step index
// per item, sort by that instead.
function stops(route: RouteOut): Stop[] {
  return route.report.groups.flatMap((group) =>
    group.items.map((item) => ({ key: `${group.category}-${item.id}-${item.status}`, item }))
  );
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

export default function RouteLine({
  route,
  routeCount,
  from,
  to,
  profile,
  now,
  headingId,
  headingLevel = 2,
  reveal = false
}: Props) {
  const Heading = headingLevel === 2 ? "h2" : "h3";
  const extra = qualifier(profile);
  const rank =
    route.rank === 1
      ? routeCount > 1
        ? "Recommended route"
        : "The only route found"
      : `Route ${route.rank} of ${routeCount}`;
  const line = stops(route);

  return (
    <div className="route">
      <Heading className="route-heading" id={headingId}>
        <span className="route-eta">{Math.round(route.eta_min)} min</span>, {formatDistance(route.distance_m)}
        {extra ? `, ${extra}` : ""}
      </Heading>
      <p className="route-reason">
        {rank}
        {route.reason ? `. ${route.reason}` : ""}.
      </p>
      <ol className={reveal ? "route-line is-revealing" : "route-line"} aria-label={`Route ${route.rank}: start, conditions and end`}>
        <li className="stop is-first">
          <span className="stop-mark">
            <HazardShapeIcon shape="start" size={24} />
          </span>
          <div className="stop-body">
            <p className="stop-title">{from}</p>
            <p className="stop-label">Start</p>
          </div>
        </li>
        {line.map(({ key, item }) => {
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
                {item.detail && <p className="stop-detail">{item.detail}</p>}
                <p className="stop-checked">
                  <TimeAgo iso={item.observed_at} now={now} />
                </p>
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
    </div>
  );
}
