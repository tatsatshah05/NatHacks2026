import type { ReportItem } from "@/lib/planner-types";
import { cityWideSentence } from "@/lib/report-format";
import { HazardShapeIcon, effectShape } from "./hazard-mark";
import TimeAgo from "./time-ago";

type Props = { items: ReportItem[]; now: Date };

// Conditions that cover the whole city, such as the ice-risk watch. They apply to every
// route equally and change none, so a page shows them once, above its routes, and not
// as stops on each line.
export default function CityWideConditions({ items, now }: Props) {
  if (!items.length) return null;
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
    </ul>
  );
}
