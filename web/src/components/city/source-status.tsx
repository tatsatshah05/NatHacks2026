import type { SourceStatus as Source, StatusResponse } from "@/lib/city";
import { formatAgo, plural, sourceLabel } from "@/lib/report-format";
import Panel from "../ui/panel";
import StatusLine from "../ui/status-line";
import TimeAgo from "../ui/time-ago";
import styles from "./city-dashboard.module.css";

type Props = {
  status: StatusResponse | null;
  error: string | null;
  now: Date;
};

/** Every scheduled source: fresh or out of date, how old, and its last error in words. */
export default function SourceStatusPanel({ status, error, now }: Props) {
  const sources = status?.sources ?? [];
  return (
    <Panel aria-labelledby="source-status-title" className={styles.panel}>
      <h2 id="source-status-title">Source status</h2>
      {error && (
        <p className={styles.problem}>
          <strong>Source status could not be loaded.</strong> {error} Refresh the list to try again.
        </p>
      )}
      {status?.mode === "replay" && (
        <p>These sources are replaying recorded responses, not reading the City live.</p>
      )}
      {status && !status.scheduler_running && (
        <p>
          The scheduler is not running on this server, so no source is being read. The list shows what the
          database already holds.
        </p>
      )}
      {status && sources.length === 0 && <p>No scheduled sources are reported.</p>}
      {sources.length > 0 && (
        <ul className={styles.sources}>
          {sources.map((source) => (
            <SourceRow key={source.source} source={source} now={now} />
          ))}
        </ul>
      )}
    </Panel>
  );
}

/**
 * What the data quality checks (OP-407) have to say about one source, as plain
 * sentences: rows its own validation rejected in the last read, field names it
 * has never seen, and a sudden fall in its item count since yesterday. Empty
 * when there is nothing to say.
 */
export function qualitySentences(source: Source): string {
  const sentences: string[] = [];
  if (source.rejected_rows > 0) {
    const reason = source.last_validation_error ? `: ${source.last_validation_error}` : "";
    sentences.push(`${plural(source.rejected_rows, "row", "rows")} rejected this fetch${reason}.`);
  }
  const fields = source.unexpected_fields ?? [];
  if (fields.length > 0) sentences.push(`New fields: ${fields.join(", ")}.`);
  const change = source.count_change;
  if (change?.drop && typeof change.yesterday === "number") {
    sentences.push(`Items fell from ${change.yesterday} to ${change.today} since yesterday.`);
  }
  return sentences.join(" ");
}

function SourceRow({ source, now }: { source: Source; now: Date }) {
  const name = sourceLabel(source.source);
  const items = plural(source.item_count, "item", "items");
  const failed = plural(source.consecutive_failures, "attempt", "attempts");
  let state: "fresh" | "stale" | "waiting";
  if (source.last_success && !source.stale) state = "fresh";
  else if (source.last_success || source.consecutive_failures > 0) state = "stale";
  else state = "waiting";
  return (
    <li className={state === "stale" ? styles.sourceStale : undefined}>
      <p className={styles.sourceName}>
        {name}
        <span className={styles.sourceState}>
          {state === "fresh" ? "Up to date" : state === "stale" ? "Out of date" : "Not read yet"}
        </span>
      </p>
      {state === "fresh" && (
        <p className={styles.meta}>
          <TimeAgo iso={source.last_success} now={now} />. {items}.
        </p>
      )}
      {state === "stale" && (
        <p className={styles.meta}>
          Last read {formatAgo(source.last_success, now) ?? "never"}. {failed} failed in a row; its last known{" "}
          {source.item_count === 1 ? "item is" : "items are"} still used.
        </p>
      )}
      {state === "waiting" && <p className={styles.meta}>Waiting for the first read.</p>}
      {source.last_error && state !== "fresh" && (
        <p className={styles.meta}>Last error: {source.last_error}</p>
      )}
      {source.skipped_items > 0 && (
        <p className={styles.meta}>
          {plural(source.skipped_items, "item", "items")} in the last read could not be used and{" "}
          {source.skipped_items === 1 ? "was" : "were"} skipped.
        </p>
      )}
      {/* Always in the DOM so a change is announced; empty when there is nothing to say. */}
      <StatusLine>{qualitySentences(source)}</StatusLine>
    </li>
  );
}
