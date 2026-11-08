"use client";

// Place search for the planner's From and To fields (GET /api/search, OP-302).
//
// - Waits 150 ms after the last keystroke, then asks the API.
// - Cancels the request in flight when the text changes, so a slow answer for "chu"
//   can never replace the answer for "churchill".
// - Remembers answers for this page, so going back to a query costs nothing.
// - Keeps the last suggestions on screen while the next ones load, so the list does
//   not flicker as someone types.

import { useEffect, useRef, useState } from "react";
import { ApiError, isAbort, searchPlaces } from "./api";
import type { PlannerPlace, SearchResult } from "./planner-types";

export const SEARCH_DEBOUNCE_MS = 150;
export const MIN_SEARCH_CHARS = 2; // cloud/app/search.py MIN_QUERY_CHARS
const SLOW_MS = 400;

export type PlaceSearchState = "idle" | "loading" | "done" | "error";

export type PlaceSearch = {
  state: PlaceSearchState;
  results: PlannerPlace[];
  /** Why the search failed, in words that say what to do. */
  error: string;
  /** True once a request has been pending for a while; the field then says so. */
  slow: boolean;
};

const KIND_TEXT: Record<SearchResult["kind"], string> = {
  address: "Street address",
  station: "LRT station",
  place: "Place"
};

/** A search result as a planner place ([lng, lat], as in GeoJSON). */
export function placeFromResult(result: SearchResult): PlannerPlace {
  return {
    id: `${result.kind}:${result.lat.toFixed(6)},${result.lng.toFixed(6)}:${result.label}`,
    name: result.label,
    description: result.detail || KIND_TEXT[result.kind],
    coordinates: [result.lng, result.lat]
  };
}

function failureText(error: unknown): string {
  if (error instanceof ApiError && error.failure === "offline") {
    return "This device is offline, so places cannot be searched. Connect, then type again.";
  }
  return "Place search did not answer. Check your connection, then type again.";
}

export function usePlaceSearch(text: string, enabled: boolean): PlaceSearch {
  const query = text.trim().replace(/\s+/g, " ");
  const key = query.toLowerCase();
  const cache = useRef(new Map<string, PlannerPlace[]>());
  const [search, setSearch] = useState<PlaceSearch>({ state: "idle", results: [], error: "", slow: false });

  useEffect(() => {
    if (!enabled || query.length < MIN_SEARCH_CHARS) {
      setSearch({ state: "idle", results: [], error: "", slow: false });
      return;
    }
    const cached = cache.current.get(key);
    if (cached) {
      setSearch({ state: "done", results: cached, error: "", slow: false });
      return;
    }
    const controller = new AbortController();
    setSearch((current) => ({ ...current, state: "loading", error: "" }));
    const slow = setTimeout(() => setSearch((current) => ({ ...current, slow: true })), SLOW_MS);
    const start = setTimeout(() => {
      searchPlaces(query, controller.signal)
        .then((found) => {
          const results = found.map(placeFromResult);
          cache.current.set(key, results);
          setSearch({ state: "done", results, error: "", slow: false });
        })
        .catch((error: unknown) => {
          if (isAbort(error)) return;
          setSearch({ state: "error", results: [], error: failureText(error), slow: false });
        })
        .finally(() => clearTimeout(slow));
    }, SEARCH_DEBOUNCE_MS);
    return () => {
      clearTimeout(start);
      clearTimeout(slow);
      controller.abort();
    };
  }, [enabled, key, query]);

  return search;
}
