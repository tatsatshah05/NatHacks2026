import { getKindMeta } from "./obstruction-meta";
import { ReportGroup, ReportItem } from "@/lib/planner-types";
import {
  confidenceLabel,
  effectLabel,
  formatAgo,
  formatDate,
  plural,
  sourceLabel,
  statusLabel
} from "@/lib/report-format";

type Props = { groups: ReportGroup[]; now: Date };

// The OP-208 report as grouped text: every group in the API's order, each item with an
// icon, its kind in words, how it relates to the route, when OpenPath last checked it
// with its source, and a link to that source.
export default function ReportConditions({ groups, now }: Props) {
  return (
    <div className="report-groups">
      {groups.map((group) => {
        const headingId = `group-${group.category}`;
        return (
          <section className="report-group" key={group.category} aria-labelledby={headingId}>
            <h3 id={headingId}>
              {group.label} <span className="group-count">({group.items.length})</span>
            </h3>
            {group.items.length ? (
              <ul className="report-items">
                {group.items.map((item) => (
                  <li key={`${item.id}-${item.status}`}>
                    <ReportItemCard item={item} now={now} />
                  </li>
                ))}
              </ul>
            ) : (
              <p className="group-empty">No known obstructions in this group.</p>
            )}
          </section>
        );
      })}
    </div>
  );
}

function ReportItemCard({ item, now }: { item: ReportItem; now: Date }) {
  const meta = item.kind ? getKindMeta(item.kind) : null;
  const checked = formatAgo(item.observed_at, now);
  return (
    <article className={`report-item status-${item.status}`}>
      <span className={`report-icon ${meta?.pattern ?? "pattern-cross"}`} aria-hidden="true">{meta?.icon ?? "!"}</span>
      <div className="report-item-content">
        <p className="report-item-kind">
          <strong>{statusLabel(item.status, item.distance_m)}</strong>
          <span>{meta?.label ?? effectLabel(item.effect)}</span>
        </p>
        <h4>{item.title}</h4>
        {item.detail && <p className="report-detail">{item.detail}</p>}
        {item.note && <p className="report-note">{item.note}</p>}
        <ul className="report-meta" aria-label="Details">
          <li>{effectLabel(item.effect)}</li>
          <li>{confidenceLabel(item.confidence)}</li>
          <li>
            {checked && item.observed_at ? (
              <>
                Checked <time dateTime={item.observed_at} title={formatDate(item.observed_at)}>{checked}</time>
              </>
            ) : (
              "Check time unknown"
            )}
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
          <a className="report-source-link" href={item.url} rel="noreferrer">
            View source<span className="sr-only"> for {item.title}</span>
          </a>
        )}
      </div>
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
