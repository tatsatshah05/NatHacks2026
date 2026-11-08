import { PlaceField, PlannerPlace } from "@/lib/planner-types";

type Props = {
  from: PlannerPlace | null;
  to: PlannerPlace | null;
  onPick: (field: PlaceField) => void;
};

// Where the two demo places sit on the drawn map, as percentages of its box.
const POINTS = { from: { x: 22, y: 30 }, to: { x: 74, y: 70 } };

// A drawn stand-in for the map until OP-301: two labelled buttons choose the demo
// start and destination, and a line joins them once both are set. It comes after
// the results in reading and tab order, and the search fields do everything it does.
export default function PlannerMapPreview({ from, to, onPick }: Props) {
  return (
    <section className="preview" aria-labelledby="map-preview-title">
      <h2 className="sr-only" id="map-preview-title">
        Demo map
      </h2>
      <div className="preview-map">
        <svg className="preview-roads" viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true" focusable="false">
          <g stroke="var(--line)" strokeWidth="12" fill="none">
            <path d="M-5 42 L105 22" vectorEffect="non-scaling-stroke" />
            <path d="M-5 80 L105 62" vectorEffect="non-scaling-stroke" />
            <path d="M30 -5 L38 105" vectorEffect="non-scaling-stroke" />
            <path d="M68 -5 L62 105" vectorEffect="non-scaling-stroke" />
          </g>
          {from && to && (
            <path
              d={`M${POINTS.from.x} ${POINTS.from.y} L${POINTS.from.x} ${POINTS.to.y - 12} L${POINTS.to.x} ${POINTS.to.y}`}
              stroke="var(--action)"
              strokeWidth="4"
              strokeLinejoin="round"
              vectorEffect="non-scaling-stroke"
              fill="none"
            />
          )}
        </svg>
        <MapPlace field="from" place={from} onPick={onPick} />
        <MapPlace field="to" place={to} onPick={onPick} />
      </div>
      <p className="preview-caption">
        Demo map. The Start and Destination buttons choose sample places; search does the same job.
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
      <span className="map-place-mark" aria-hidden="true" />
      <span>
        <strong>{noun}</strong>
        <span>{place ? place.name : "Not set"}</span>
      </span>
    </button>
  );
}
