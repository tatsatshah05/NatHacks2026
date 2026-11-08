import { RouteOut } from "@/lib/planner-types";

export default function RouteCard({ route }: { route: RouteOut }) {
  const recommended = route.rank === 1;
  const titleId = `route-${route.rank}-title`;
  return (
    <article className={`route-card${recommended ? " recommended" : ""}`} aria-labelledby={titleId}>
      <div className="route-card-top">
        <div>
          <h3 className="route-label" id={titleId}>
            Route {route.rank}
            {recommended ? ": recommended" : ""}
          </h3>
          <span className="recommended-badge">Demo route</span>
        </div>
        <p className="route-minutes">
          <strong>{Math.round(route.eta_min)}</strong> min
        </p>
      </div>
      <p className="route-distance">{(route.distance_m / 1000).toFixed(1)} km walk</p>
      {route.reason && <p className="route-reason">{route.reason}</p>}
      <div className="avoided-list">
        <strong>Avoids</strong>
        {route.hazards_avoided.length ? (
          <ul>
            {route.hazards_avoided.map((hazard) => (
              <li key={hazard.item_id}>
                <span aria-hidden="true">✓ </span>
                {hazard.title}
              </li>
            ))}
          </ul>
        ) : (
          <p>No known obstructions to avoid.</p>
        )}
      </div>
    </article>
  );
}
