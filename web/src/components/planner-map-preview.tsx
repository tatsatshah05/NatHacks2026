import { PlaceField, PlannerPlace } from "@/lib/planner-types";

type Props = {
  from: PlannerPlace | null;
  to: PlannerPlace | null;
  onPick: (field: PlaceField) => void;
};

// A drawn stand-in for the map: two labelled buttons choose the demo start and
// destination. It comes after the results in reading and tab order, and the search
// fields do everything it does.
export default function PlannerMapPreview({ from, to, onPick }: Props) {
  return (
    <section className="planner-map-panel" aria-labelledby="map-preview-title">
      <div className="panel-heading">
        <div>
          <p className="eyebrow">Map preview</p>
          <h2 id="map-preview-title">Or choose on the map</h2>
        </div>
        <span className="mock-badge">Demo map</span>
      </div>
      <div className="planner-map">
        <span className="map-road road-one" aria-hidden="true" />
        <span className="map-road road-two" aria-hidden="true" />
        <span className="map-river" aria-hidden="true" />
        <MapPlace field="from" place={from} onPick={onPick} />
        <MapPlace field="to" place={to} onPick={onPick} />
        <p className="map-legend">Demo preview. Use search for exact places.</p>
      </div>
      <p className="map-help">
        The map is optional: the Start and Destination buttons work with a keyboard, and search does the same job.
      </p>
    </section>
  );
}

type MapPlaceProps = { field: PlaceField; place: PlannerPlace | null; onPick: Props["onPick"] };

function MapPlace({ field, place, onPick }: MapPlaceProps) {
  const noun = field === "from" ? "Start" : "Destination";
  return (
    <button
      type="button"
      className={`map-place ${field === "from" ? "place-one" : "place-two"}${place ? " chosen" : ""}`}
      onClick={() => onPick(field)}
    >
      <span className="map-place-mark" aria-hidden="true">{place ? "✓" : field === "from" ? "A" : "B"}</span>
      <strong>{noun}</strong>
      <small>{place ? `Set: ${place.name}` : "Not set"}</small>
    </button>
  );
}
