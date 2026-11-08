"use client";

import { FocusEvent, KeyboardEvent, ReactNode, useRef, useState } from "react";
import Field from "./ui/field";
import { MIN_SEARCH_CHARS, usePlaceSearch } from "@/lib/place-search";
import { PlaceField, PlannerPlace } from "@/lib/planner-types";

type PlaceInputProps = {
  field: PlaceField;
  label: string;
  value: string;
  selected: PlannerPlace | null;
  error: string;
  onQueryChange: (field: PlaceField, value: string) => void;
  onChoose: (field: PlaceField, place: PlannerPlace) => void;
  /** Related actions under the field, such as "Use my current location". */
  footer?: ReactNode;
};

// A labelled search field with a plain list of suggestion buttons (no listbox role,
// so no arrow-key promise). Suggestions come from GET /api/search (OP-302). The list
// opens on typing and closes on choice, Escape or when focus leaves the field. It
// sits in the page flow, so it never covers the controls below it.
export default function PlaceInput({ field, label, value, selected, error, onQueryChange, onChoose, footer }: PlaceInputProps) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [open, setOpen] = useState(false);
  // Search only while someone is typing a new place, not for the chosen one's name.
  const search = usePlaceSearch(value, open && !selected);
  const suggestions = search.results;
  const showList = open && !selected && suggestions.length > 0;
  const status = !open || selected ? "" : suggestionStatus(value, search);
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
        help="Type an address, an LRT station or a place name, then choose a suggestion."
        helpHidden
        error={error}
        note={selected ? `Selected: ${[selected.name, selected.description].filter(Boolean).join(", ")}.` : undefined}
        inputProps={{
          ref: inputRef,
          value,
          enterKeyHint: "search",
          autoComplete: "off",
          spellCheck: false,
          placeholder: field === "from" ? "Search a starting place" : "Search a destination",
          onChange: (event) => {
            onQueryChange(field, event.target.value);
            setOpen(true);
          }
        }}
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

function suggestionStatus(value: string, search: ReturnType<typeof usePlaceSearch>): string {
  if (value.trim().length < MIN_SEARCH_CHARS) return "";
  if (search.state === "error") return search.error;
  if (search.state === "loading") return search.slow && !search.results.length ? "Searching places." : "";
  if (search.state !== "done") return "";
  const count = search.results.length;
  if (count === 0) return "No matching places. Try a street address, an LRT station or a place name.";
  return count === 1 ? "1 suggestion" : `${count} suggestions`;
}

export function placeInputId(field: PlaceField): string {
  return `${field}-input`;
}
