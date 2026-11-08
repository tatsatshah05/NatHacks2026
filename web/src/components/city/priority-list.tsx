import { forwardRef } from "react";
import type { PriorityItem } from "@/lib/city";
import { CONFIDENCE_WORDS } from "@/lib/city";
import { effectLabel, sourceLabel } from "@/lib/report-format";
import HazardMark, { effectShape } from "../ui/hazard-mark";
import TimeAgo from "../ui/time-ago";
import styles from "./city-dashboard.module.css";

type RowProps = {
  item: PriorityItem;
  now: Date;
  selected: boolean;
  onToggle: (id: string) => void;
};

/** One ranked barrier. The rank is a real ranking, so its number is shown. */
export const PriorityRow = forwardRef<HTMLHeadingElement, RowProps>(function PriorityRow(
  { item, now, selected, onToggle },
  headingRef
) {
  const titleId = `rank-${item.rank}-title`;
  return (
    <li className={selected ? `${styles.row} ${styles.rowSelected}` : styles.row}>
      <p className={styles.rank} aria-hidden="true">
        {item.rank}
      </p>
      <div className={styles.rowBody}>
        <h3 className={styles.rowTitle} id={titleId} ref={headingRef} tabIndex={-1}>
          <span className="sr-only">Rank {item.rank}: </span>
          <HazardMark shape={effectShape(item.effect)} label={item.title} />
        </h3>
        <p className={styles.why}>{item.why}</p>
        <p className={styles.meta}>
          {effectLabel(item.effect)}. {CONFIDENCE_WORDS[item.confidence]}. <TimeAgo iso={item.observed_at} now={now} />.
        </p>
        <p className={styles.meta}>
          Source: {sourceLabel(item.source)}, {item.attribution}.
        </p>
      </div>
      <button
        type="button"
        className={`button button-text ${styles.rowAction}`}
        aria-pressed={selected}
        aria-describedby={titleId}
        onClick={() => onToggle(item.id)}
      >
        Show on map
      </button>
    </li>
  );
});
