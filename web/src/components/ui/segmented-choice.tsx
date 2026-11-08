import type { ReactNode } from "react";

export type SegmentOption<T extends string> = {
  id: T;
  label: string;
  /** One more line, shown in the stacked layout. */
  description?: string;
  icon?: ReactNode;
};

type Props<T extends string> = {
  legend: ReactNode;
  name: string;
  options: ReadonlyArray<SegmentOption<T>>;
  value: T;
  onChange: (id: T) => void;
  /** grid: connected segments, two per row. stack: one row per option. */
  layout?: "grid" | "stack";
};

// Native radios in a fieldset, so arrow keys move between segments and the group has
// one tab stop. The chosen segment is filled ink on snow (action blue on dark).
export default function SegmentedChoice<T extends string>({
  legend,
  name,
  options,
  value,
  onChange,
  layout = "grid"
}: Props<T>) {
  return (
    <fieldset className="segmented">
      <legend className="segmented-legend">{legend}</legend>
      <div className={layout === "grid" ? "segmented-grid" : "segmented-stack"}>
        {options.map((option) => (
          <label className="segment" key={option.id}>
            <input type="radio" name={name} value={option.id} checked={value === option.id} onChange={() => onChange(option.id)} />
            {option.icon && (
              <span className="segment-icon" aria-hidden="true">
                {option.icon}
              </span>
            )}
            <span className="segment-text">
              {option.label}
              {option.description && <span className="segment-desc">{option.description}</span>}
            </span>
          </label>
        ))}
      </div>
    </fieldset>
  );
}
