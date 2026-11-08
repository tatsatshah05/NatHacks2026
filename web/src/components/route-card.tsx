import { RouteOut } from "@/lib/planner-types";

export default function RouteCard({ route, recommended }: { route: RouteOut; recommended: boolean }) {
  return (
    <article className={`route-card${recommended ? " recommended" : ""}`}>
      <div className="route-card-top">
        <div><p className="route-label">{route.reason ?? (recommended ? "Recommended route" : "Alternate route")}</p>{recommended && <span className="recommended-badge">Demo route</span>}</div>
        <span className="route-minutes"><strong>{Math.round(route.eta_min)}</strong> min</span>
      </div>
      <p className="route-distance">{(route.distance_m / 1000).toFixed(1)} km · Demo route data</p>
      <div className="avoided-list"><strong>Avoids</strong>{route.hazards_avoided.map((hazard) => <span key={hazard.item_id}>✓ {hazard.title}</span>)}</div>
    </article>
  );
}
