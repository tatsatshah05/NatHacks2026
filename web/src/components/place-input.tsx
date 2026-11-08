"use client";

import { FocusEvent, KeyboardEvent, ReactNode, useMemo, useRef, useState } from "react";
import Field from "./ui/field";
import { PlaceField, PlannerPlace } from "@/lib/planner-types";

type PlaceInputProps = {
  field: PlaceField;
  label: string;
  value: string;
  selected: PlannerPlace | null;
  places: PlannerPlace[];
  error: string;
  onQueryChange: (field: PlaceField, value: string) => void;
  onChoose: (field: PlaceField, place: PlannerPlace) => void;
  onMapPick: (field: PlaceField) => void;
  /** Related actions under the field, such as "Use my current location". */
  footer?: ReactNode;
};

// A labelled search field with a plain list of suggestion buttons (no listbox role,
// so no arrow-key promise). The list opens on typing and closes on choice, Escape or
// when focus leaves the field. It sits in the page flow, so it never covers the
// controls below it.
export default function PlaceInput({
  field,
  label,
  value,
  selected,
  places,
  error,
  onQueryChange,
  onChoose,
  onMapPick,
  footer
}: PlaceInputProps) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [open, setOpen] = useState(false);

  const query = value.trim().toLowerCase();
  const suggestions = useMemo(
    () => (query ? places.filter((place) => `${place.name} ${place.description}`.toLowerCase().includes(query)) : []),
    [places, query]
  );
  const showList = open && suggestions.length > 0;
  const status = !open || !query ? "" : suggestionStatus(suggestions.length);
  const inputId = placeInputId(field);

  function handleKeyDown(event: KeyboardEvent<HTMLElement>) {
    if (event.key === "Escape" && open) {
      event.preventDefault();
      setOpen(false);
      inputRef.current?.focus();
    }
  }

  function handleBlur(event: FocusEvent<HTMLDivElement>) {
    if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setOpen(false);
  }

  function choose(place: PlannerPlace) {
    onChoose(field, place);
    setOpen(false);
    // The chosen button disappears with the list; keep focus on the field.
    inputRef.current?.focus();
  }

  return (
    <div onBlur={handleBlur} onKeyDown={handleKeyDown}>
      <Field
        id={inputId}
        label={label}
        help="Type to search demo places, or choose one on the demo map."
        helpHidden
        error={error}
        note={selected ? `Selected: ${selected.name}, ${selected.description}` : undefined}
        inputProps={{
          ref: inputRef,
          value,
          autoComplete: "off",
          placeholder: field === "from" ? "Search a starting place" : "Search a destination",
          onChange: (event) => {
            onQueryChange(field, event.target.value);
            setOpen(true);
          }
        }}
        trailing={
          <button
            type="button"
            className="control-trailing"
            aria-label={`Choose ${label} on the demo map`}
            onClick={() => {
              setOpen(false);
              onMapPick(field);
            }}
          >
            <svg viewBox="0 0 24 24" width="22" height="22" aria-hidden="true" focusable="false" fill="none" stroke="currentColor" strokeWidth="2">
              <circle cx="12" cy="12" r="6" />
              <path d="M12 2v4M12 18v4M2 12h4M18 12h4" strokeLinecap="round" />
            </svg>
          </button>
        }
      >
        <p className="field-note" role="status">
          {status}
        </p>
        {showList && (
          <ul className="suggestions" aria-label={`${label} suggestions`}>
            {suggestions.map((place) => (
              <li key={place.id}>
                <button
                  type="button"
                  className="suggestion"
                  // Keep focus in the field on pointer press, so blur does not close the list first.
                  onMouseDown={(event) => event.preventDefault()}
                  onClick={() => choose(place)}
                >
                  <strong>{place.name}</strong>
                  <span>{place.description}</span>
                </button>
              </li>
            ))}
          </ul>
        )}
        {footer}
      </Field>
    </div>
  );
}

function suggestionStatus(count: number): string {
  if (count === 0) return "No matching places. Try another name or use the demo map.";
  return count === 1 ? "1 suggestion" : `${count} suggestions`;
}

export function placeInputId(field: PlaceField): string {
  return `${field}-input`;
}
