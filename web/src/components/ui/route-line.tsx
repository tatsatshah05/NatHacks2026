import type { ReactNode } from "react";
import type { LegOut, ReportItem, RouteOut, StationOut, TravelProfile } from "@/lib/planner-types";
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

/** The element id of a condition's stop, so the map can jump to it. */
export function stopId(rank: number, itemId: string): string {
  return `route-${rank}-stop-${itemId.replace(/[^a-zA-Z0-9_-]/g, "-")}`;
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
        <span className="route-eta">{Math.round(route.eta_min)} min</span>,{" "}
        {route.lrt ? `${formatDistance(route.lrt.walk_distance_m)} on foot` : formatDistance(route.distance_m)}
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

type StopContext = { rank: number; now: Date; compact: boolean };
// Which line runs past a row: the single walking line or the double ride line.
type Rail = "walk" | "ride";
// What a ride is on (OP-417). The API says "lrt" or "bus" on each ride leg.
type Vehicle = NonNullable<LegOut["vehicle"]>;

function EndStop({ name, label, first }: { name: string; label: string; first?: boolean }) {
  return (
    <li className={first ? "stop is-first" : "stop is-last"}>
      <span className="stop-mark">
        <HazardShapeIcon shape="start" size={24} />
      </span>
      <div className="stop-body">
        <p className="stop-title">{name}</p>
        <p className="stop-label">{label}</p>
      </div>
    </li>
  );
}

function ConditionStop({ item, context, rail = "walk" }: { item: ReportItem; context: StopContext; rail?: Rail }) {
  const avoided = item.status === "avoided";
  const classes = ["stop", avoided ? "is-avoided" : "", rail === "ride" ? "on-ride" : ""].filter(Boolean).join(" ");
  return (
    <li className={classes} id={stopId(context.rank, item.id)} tabIndex={-1}>
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
        {!context.compact && item.detail && <p className="stop-detail">{item.detail}</p>}
        {!context.compact && (
          <p className="stop-checked">
            <TimeAgo iso={item.observed_at} now={context.now} />
          </p>
        )}
      </div>
    </li>
  );
}

/** The line itself: start, each condition on or near the route, end. */
export function RouteStops({ route, from, to, now, reveal = false, compact = false }: StopsProps) {
  const context: StopContext = { rank: route.rank, now, compact };
  const legs = route.legs ?? [];
  return (
    <ol
      className={reveal ? "route-line is-revealing" : "route-line"}
      // Safari drops list semantics when list-style is none; keep them.
      role="list"
      aria-label={`Route ${route.rank}: start, ${legs.length ? "walks, rides, stations, " : ""}conditions and end`}
    >
      <EndStop name={from} label="Start" first />
      {legs.length ? (
        <TripRows route={route} legs={legs} from={from} to={to} context={context} />
      ) : (
        stops(route).map(({ key, item }) => <ConditionStop key={key} item={item} context={context} />)
      )}
      <EndStop name={to} label="End" />
    </ol>
  );
}

// ------------------------------- walk and LRT trips (OP-311), bus rides (OP-417)

const minutes = (value: number) => `${Math.max(1, Math.round(value))} min`;

function vehicleOf(leg: LegOut): Vehicle {
  return leg.vehicle ?? "lrt";
}

/**
 * "Capital Line toward Century Park" (or both lines on a shared track), or for a bus
 * "Bus 8 toward Abbottsfield": the route number and the destination, as on the bus.
 */
function rideTitle(leg: LegOut): string {
  const heads = leg.headsigns ?? [];
  if (vehicleOf(leg) === "bus") {
    const numbers = leg.route_short_names ?? [];
    if (!numbers.length) return `Bus toward ${heads.join(" or ") || leg.to_name}`;
    return numbers.map((number, index) => (heads[index] ? `Bus ${number} toward ${heads[index]}` : `Bus ${number}`)).join(" or ");
  }
  const lines = leg.lines ?? [];
  if (!lines.length) return `LRT toward ${heads.join(" or ") || leg.to_name}`;
  return lines.map((line, index) => (heads[index] ? `${line} toward ${heads[index]}` : line)).join(" or ");
}

function LegRow({ leg, from, to }: { leg: LegOut; from: string; to: string }) {
  let body: ReactNode;
  if (leg.kind === "ride") {
    const stopsText = leg.stops ? `${plural(leg.stops, "stop", "stops")}, ` : "";
    body = (
      <>
        <p className="stop-title">{rideTitle(leg)}</p>
        <p className="stop-label">
          {stopsText}
          {minutes(leg.ride_min ?? leg.duration_min)}
        </p>
        {leg.wait && <p className="stop-label">About {minutes(leg.wait.minutes)} wait (estimate)</p>}
      </>
    );
  } else {
    const verb = leg.kind === "transfer" ? "Change trains: walk" : "Walk";
    const target = leg.to_name === "Destination" ? to : leg.to_name;
    const origin = leg.from_name === "Start" ? from : leg.from_name;
    body = (
      <p className="stop-label">
        {verb} {formatDistance(leg.distance_m)}, about {minutes(leg.duration_min)}
        <span className="sr-only">
          , from {origin} to {target}
        </span>
      </p>
    );
  }
  return (
    <li className={`stop leg is-${leg.kind === "ride" ? "ride" : "walk"}`}>
      <span className="stop-mark" aria-hidden="true" />
      <div className="stop-body">{body}</div>
    </li>
  );
}

function StationStop({
  name,
  station,
  vehicle,
  railIn,
  railOut,
  context
}: {
  name: string;
  station: StationOut | null;
  vehicle: Vehicle;
  railIn: Rail;
  railOut: Rail;
  context: StopContext;
}) {
  const classes = ["stop", "is-station", railIn === "ride" ? "ride-in" : "", railOut === "ride" ? "ride-out" : ""]
    .filter(Boolean)
    .join(" ");
  const access =
    vehicle === "bus" ? "Bus stop" : station?.access === "at_grade" ? "LRT stop at street level" : "LRT station";
  return (
    <li className={classes}>
      <span className="stop-mark">
        <svg className="station-mark" width="24" height="24" viewBox="0 0 24 24" aria-hidden="true" focusable="false">
          <circle cx="12" cy="12" r="8" fill="var(--card)" stroke="currentColor" strokeWidth="4" />
        </svg>
      </span>
      <div className="stop-body">
        <p className="stop-title">{station?.name ?? name}</p>
        <p className="stop-label">{access}</p>
        {!context.compact && station?.note && <p className="stop-detail">{station.note}</p>}
      </div>
    </li>
  );
}

const DEVICE_SOURCE = "city_lrt_devices";

/**
 * A walk and LRT or bus trip as a line: walks as the single line, rides as a double
 * line with the line name or the bus number, stations and stops as rings. A station's
 * elevator and escalator outages are stops beside it, on its walking side. Other
 * conditions have no position yet, so they follow the last leg in the report's order.
 */
function TripRows({
  route,
  legs,
  from,
  to,
  context
}: {
  route: RouteOut;
  legs: LegOut[];
  from: string;
  to: string;
  context: StopContext;
}) {
  const stations = route.lrt?.stations ?? [];
  const items = stops(route);
  const used = new Set<string>();
  const atStation = (station: StationOut | null) => {
    if (!station) return [];
    const ids = new Set(station.devices.map((device) => `${DEVICE_SOURCE}:${device.device_id}`));
    return items.filter(({ key, item }) => ids.has(item.id) && !used.has(key) && used.add(key));
  };
  const rows: ReactNode[] = [];
  legs.forEach((leg, index) => {
    if (index > 0 && legs[index - 1].to_name === leg.from_name) {
      const station = stations.find((option) => option.name === leg.from_name) ?? null;
      const railIn: Rail = legs[index - 1].kind === "ride" ? "ride" : "walk";
      const railOut: Rail = leg.kind === "ride" ? "ride" : "walk";
      // The ride that arrives or leaves says what kind of stop this is.
      const vehicle = vehicleOf(railOut === "ride" ? leg : legs[index - 1]);
      const conditions = atStation(station).map(({ key, item }) => (
        <ConditionStop key={key} item={item} context={context} rail={railIn === "ride" && railOut === "ride" ? "ride" : "walk"} />
      ));
      const stop = (
        <StationStop
          key={`station-${index}`}
          name={leg.from_name}
          station={station}
          vehicle={vehicle}
          railIn={railIn}
          railOut={railOut}
          context={context}
        />
      );
      // Outages sit on the station's walking side: before it when boarding, after it otherwise.
      if (railOut === "ride" && railIn === "walk") rows.push(...conditions, stop);
      else rows.push(stop, ...conditions);
    }
    rows.push(<LegRow key={`leg-${index}`} leg={leg} from={from} to={to} />);
  });
  for (const { key, item } of items) {
    if (!used.has(key)) rows.push(<ConditionStop key={key} item={item} context={context} />);
  }
  return <>{rows}</>;
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
