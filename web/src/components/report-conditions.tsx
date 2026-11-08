import HazardMark, { effectShape } from "./ui/hazard-mark";
import TimeAgo from "./ui/time-ago";
import { ReportGroup, ReportItem } from "@/lib/planner-types";
import { confidenceLabel, effectLabel, formatDate, plural, sourceLabel, statusLabel } from "@/lib/report-format";

type Props = { groups: ReportGroup[]; now: Date; headingLevel?: 3 | 4 };

// The OP-208 report as grouped text: every group in the API's order, each item with
// its shape and effect in words, how it relates to the route, when OpenPath last
// checked it, its source, and a link to that source.
export default function ReportConditions({ groups, now, headingLevel = 3 }: Props) {
  const Heading = headingLevel === 3 ? "h3" : "h4";
  return (
    <div className="groups">
      {groups.map((group) => {
        const headingId = `group-${group.category}`;
        return (
          <section className="group" key={group.category} aria-labelledby={headingId}>
            <Heading id={headingId}>
              {group.label} <span className="group-count">({group.items.length})</span>
            </Heading>
            {group.items.length ? (
              <ul className="group-items" role="list">
                {group.items.map((item) => (
                  <li key={`${item.id}-${item.status}`}>
                    <ReportItemText item={item} now={now} />
                  </li>
                ))}
              </ul>
            ) : (
              <p className="muted">Nothing known in this group.</p>
            )}
          </section>
        );
      })}
    </div>
  );
}

function ReportItemText({ item, now }: { item: ReportItem; now: Date }) {
  return (
    <article className="item">
      <HazardMark shape={effectShape(item.effect)} label={effectLabel(item.effect)} />
      <p className="item-title">{item.title}</p>
      <p className="item-status">{statusLabel(item.status, item.distance_m)}.</p>
      {item.detail && <p>{item.detail}</p>}
      {item.note && <p className="item-note">{item.note}</p>}
      <ul className="item-meta" aria-label="Details">
        <li>{confidenceLabel(item.confidence)}</li>
        <li>
          <TimeAgo iso={item.observed_at} now={now} />
        </li>
        {item.ends_at && (
          <li>
            Until <time dateTime={item.ends_at}>{formatDate(item.ends_at)}</time>
          </li>
        )}
        <li>
          Source: {sourceLabel(item.source)}
          {item.attribution ? `, ${item.attribution}` : ""}
        </li>
      </ul>
      {item.url && (
        <a href={item.url} rel="noreferrer">
          View source<span className="sr-only"> for {item.title}</span>
        </a>
      )}
    </article>
  );
}

export function conditionCount(groups: ReportGroup[]): number {
  return groups.reduce((sum, group) => sum + group.items.length, 0);
}

export function conditionsSummary(report: { on_route: number; nearby: number; avoided: number }): string {
  if (!report.on_route && !report.nearby && !report.avoided) return "No known obstructions";
  return [
    `${plural(report.on_route, "condition", "conditions")} on this route`,
    `${report.nearby} within 50 m`,
    `${report.avoided} avoided`
  ].join(", ");
}
