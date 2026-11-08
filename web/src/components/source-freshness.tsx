import { SourceStatus } from "@/lib/planner-types";
import { formatAgo, formatDate, plural, sourceLabel } from "@/lib/report-format";

// When each source was last read. A stale source keeps its last good data, and says so.
export default function SourceFreshness({ sources, now }: { sources: SourceStatus[]; now: Date }) {
  return (
    <ul className="source-list">
      {sources.map((source) => {
        const ago = formatAgo(source.last_success, now);
        const failed = plural(source.consecutive_failures, "attempt", "attempts");
        return (
          <li key={source.source} className={source.stale ? "is-stale" : undefined}>
            <span className="source-name">
              {source.stale && <span aria-hidden="true">⚠ </span>}
              {sourceLabel(source.source)}
            </span>
            <span>
              {source.stale ? "Out of date: last read " : "Checked "}
              {ago && source.last_success ? (
                <time dateTime={source.last_success} title={formatDate(source.last_success)}>{ago}</time>
              ) : (
                "at an unknown time"
              )}
              {source.stale
                ? `. ${failed} failed; its last known items are still used.`
                : ""}
            </span>
            {source.url && (
              <a href={source.url} rel="noreferrer">
                Dataset<span className="sr-only"> for {sourceLabel(source.source)}</span>
              </a>
            )}
          </li>
        );
      })}
    </ul>
  );
}

/** One line for the summary: how fresh the sources are, and which are out of date. */
export function freshnessSummary(sources: SourceStatus[], now: Date): string {
  const fresh = sources.filter((source) => !source.stale && source.last_success);
  const stale = sources.filter((source) => source.stale);
  const oldest = fresh
    .map((source) => source.last_success ?? "")
    .reduce<string | null>((min, value) => (!min || value < min ? value : min), null);
  let text = `${fresh.length} of ${plural(sources.length, "source", "sources")} up to date`;
  if (oldest) text += `; the oldest was checked ${formatAgo(oldest, now)}`;
  text += ".";
  if (stale.length) {
    const names = stale.map((source) => sourceLabel(source.source)).join(", ");
    text += ` Out of date: ${names}; OpenPath keeps using its last known items.`;
  }
  return text;
}
