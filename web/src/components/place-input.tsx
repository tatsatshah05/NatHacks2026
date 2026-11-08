"use client";

import { FocusEvent, KeyboardEvent, useMemo, useRef, useState } from "react";
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
  onMapPick
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
  const describedBy = [`${field}-hint`, error ? `${field}-error` : ""].filter(Boolean).join(" ");

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
    <div className="place-field" onBlur={handleBlur} onKeyDown={handleKeyDown}>
      <label htmlFor={inputId}>{label}</label>
      <div className={`place-input-row${error ? " has-error" : ""}`}>
        <span className="place-symbol" aria-hidden="true">{field === "from" ? "A" : "B"}</span>
        <input
          id={inputId}
          ref={inputRef}
          value={value}
          autoComplete="off"
          aria-describedby={describedBy}
          aria-invalid={error ? true : undefined}
          placeholder={field === "from" ? "Search starting point" : "Search destination"}
          onChange={(event) => {
            onQueryChange(field, event.target.value);
            setOpen(true);
          }}
        />
        <button
          type="button"
          className="map-pick-button"
          aria-label={`Choose ${label} on the demo map`}
          onClick={() => {
            setOpen(false);
            onMapPick(field);
          }}
        >
          <span aria-hidden="true">⌖</span>
        </button>
      </div>
      <span className="sr-only" id={`${field}-hint`}>
        Type to search demo places, or choose one on the demo map.
      </span>
      {error && <p className="field-error" id={`${field}-error`}>{error}</p>}
      {selected && <p className="selected-place">Selected: {selected.name}, {selected.description}</p>}
      <p className="suggestion-status" role="status">{status}</p>
      {showList && (
        <ul className="suggestion-list" aria-label={`${label} suggestions`}>
          {suggestions.map((place) => (
            <li key={place.id}>
              <button
                type="button"
                // Keep focus in the field on pointer press, so blur does not close the list first.
                onMouseDown={(event) => event.preventDefault()}
                onClick={() => choose(place)}
              >
                <strong>{place.name}</strong>
                <small>{place.description}</small>
              </button>
            </li>
          ))}
        </ul>
      )}
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
