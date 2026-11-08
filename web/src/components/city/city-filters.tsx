import type { Filters, PriorityItem } from "@/lib/city";
import {
  CATEGORY_LABELS,
  CATEGORY_ORDER,
  CONFIDENCE_ORDER,
  CONFIDENCE_WORDS,
  EFFECT_ORDER,
  hasFilters
} from "@/lib/city";
import { effectLabel, sourceLabel } from "@/lib/report-format";
import Button from "../ui/button";
import styles from "./city-dashboard.module.css";

type Props = {
  items: readonly PriorityItem[];
  filters: Filters;
  onChange: (filters: Filters) => void;
  onClear: () => void;
};

type Option = { value: string; label: string; count: number };

function options<T extends string>(
  items: readonly PriorityItem[],
  values: readonly T[],
  pick: (item: PriorityItem) => string,
  label: (value: T) => string,
  chosen: string
): Option[] {
  // Only values that occur, plus the chosen one, so a refresh never empties a choice.
  return values
    .map((value) => ({ value, label: label(value), count: items.filter((item) => pick(item) === value).length }))
    .filter((option) => option.count > 0 || option.value === chosen);
}

/** A compact toolbar: search plus four native selects. Each change filters at once. */
export default function CityFilters({ items, filters, onChange, onClear }: Props) {
  const sources = [...new Set([...items.map((item) => item.source), ...(filters.source ? [filters.source] : [])])].sort((a, b) =>
    sourceLabel(a).localeCompare(sourceLabel(b))
  );
  const selects: Array<{ key: Exclude<keyof Filters, "query">; label: string; all: string; options: Option[] }> = [
    {
      key: "effect",
      label: "Effect",
      all: "All",
      options: options(items, EFFECT_ORDER, (item) => item.effect, effectLabel, filters.effect)
    },
    {
      key: "category",
      label: "Where",
      all: "All",
      options: options(items, CATEGORY_ORDER, (item) => item.category, (value) => CATEGORY_LABELS[value], filters.category)
    },
    {
      key: "source",
      label: "Source",
      all: "All",
      options: options(items, sources, (item) => item.source, sourceLabel, filters.source)
    },
    {
      key: "confidence",
      label: "Confidence",
      all: "All",
      options: options(items, CONFIDENCE_ORDER, (item) => item.confidence, (value) => CONFIDENCE_WORDS[value], filters.confidence)
    }
  ];

  return (
    <form className={styles.toolbar} role="search" aria-label="Filter barriers" onSubmit={(event) => event.preventDefault()}>
      <div className={styles.search}>
        <label className="field-label" htmlFor="city-search">
          Search
        </label>
        <div className="control">
          <input
            className="control-input"
            id="city-search"
            type="search"
            value={filters.query}
            placeholder="Street, trail or station"
            autoComplete="off"
            onChange={(event) => onChange({ ...filters, query: event.target.value })}
          />
        </div>
      </div>
      {selects.map((select) => (
        <div className={styles.select} key={select.key}>
          <label className="field-label" htmlFor={`city-${select.key}`}>
            {select.label}
          </label>
          <div className={styles.selectWrap}>
            <select
              id={`city-${select.key}`}
              className={styles.selectControl}
              value={filters[select.key]}
              onChange={(event) => onChange({ ...filters, [select.key]: event.target.value })}
            >
              <option value="">{select.all}</option>
              {select.options.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label} ({option.count})
                </option>
              ))}
            </select>
          </div>
        </div>
      ))}
      {hasFilters(filters) && (
        <div className={styles.clear}>
          <Button variant="text" onClick={onClear}>
            Clear filters
          </Button>
        </div>
      )}
    </form>
  );
}
