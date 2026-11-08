"use client";

import { KeyboardEvent } from "react";
import { PlannerPlace } from "@/lib/planner-types";

type PlaceField = "from" | "to";

export default function PlaceInput({
  field, label, value, selected, activeField, suggestions, onFocus, onChange, onChoose, onMapPick, onEscape
}: {
  field: PlaceField;
  label: string;
  value: string;
  selected: PlannerPlace | null;
  activeField: PlaceField | null;
  suggestions: PlannerPlace[];
  onFocus: (field: PlaceField) => void;
  onChange: (field: PlaceField, value: string) => void;
  onChoose: (field: PlaceField, place: PlannerPlace) => void;
  onMapPick: (field: PlaceField) => void;
  onEscape: () => void;
}) {
  function handleKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === "Escape") {
      event.preventDefault();
      onEscape();
    }
  }

  return (
    <div className="place-field">
      <label htmlFor={`${field}-input`}>{label}</label>
      <div className="place-input-row">
        <span className="place-symbol" aria-hidden="true">{field === "from" ? "A" : "B"}</span>
        <input id={`${field}-input`} value={value} onFocus={() => onFocus(field)} onKeyDown={handleKeyDown} onChange={(event) => onChange(field, event.target.value)} placeholder={field === "from" ? "Search starting point" : "Search destination"} aria-describedby={`${field}-hint`} />
        <button type="button" className="map-pick-button" onClick={() => onMapPick(field)} aria-label={`Choose ${label} on map`}>⌖</button>
      </div>
      <span className="sr-only" id={`${field}-hint`}>Search by address or place name, or choose it on the map.</span>
      {selected && <p className="selected-place">Selected: {selected.name}, {selected.description}</p>}
      {activeField === field && <p className="suggestion-status" role="status">{suggestions.length ? `${suggestions.length} suggestions` : "No matching places"}</p>}
      {activeField === field && suggestions.length > 0 && (
        <ul className="suggestion-list" aria-label={`${label} suggestions`}>
          {suggestions.map((place) => <li key={place.id}><button type="button" onClick={() => onChoose(field, place)}><strong>{place.name}</strong><small>{place.description}</small></button></li>)}
        </ul>
      )}
    </div>
  );
}
