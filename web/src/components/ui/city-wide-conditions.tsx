import type { ReportItem } from "@/lib/planner-types";
import { cameraFallback, useCamerasStatus } from "@/lib/camera-status";
import { cityWideSentence } from "@/lib/report-format";
import CameraGlyph from "../city/camera-glyph";
import { HazardShapeIcon, effectShape } from "./hazard-mark";
import TimeAgo from "./time-ago";

type Props = { items: ReportItem[]; now: Date };

// Conditions that cover the whole city, such as the ice-risk watch. They apply to every
// route equally and change none, so a page shows them once, above its routes, and not
// as stops on each line. When the road cameras are not reporting (OP-412), one more
// line says so and that ice risk comes from the weather watch alone.
export default function CityWideConditions({ items, now }: Props) {
  const fallback = cameraFallback(useCamerasStatus(), now);
  if (!items.length && !fallback) return null;
  return (
    <ul className="city-wide" role="list" aria-label="Conditions across the whole city">
      {items.map((item) => (
        <li key={item.id}>
          <HazardShapeIcon shape={effectShape(item.effect)} size={24} />
          <p>
            <strong>{cityWideSentence(item)}</strong> {item.detail ? `${item.detail} ` : ""}
            <span className="muted">
              <TimeAgo iso={item.observed_at} now={now} />.
            </span>
          </p>
        </li>
      ))}
      {fallback && (
        <li key="cameras" data-camera-state={fallback.state}>
          <CameraGlyph size={24} className="hazard-shape" />
          <p>{fallback.sentence}</p>
        </li>
      )}
    </ul>
  );
}
