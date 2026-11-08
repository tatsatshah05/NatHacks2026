"use client";

import { useState } from "react";
import Link from "next/link";
import PlaceInput, { placeInputId } from "./place-input";
import PlannerMapPreview from "./planner-map-preview";
import ProfilePicker from "./profile-picker";
import RouteCard from "./route-card";
import SiteHeader from "./site-header";
import { getMockRouteResponse, mockPlaces } from "@/lib/mock-routes";
import {
  PlaceField,
  PlannerPlace,
  PlannerQuery,
  RouteResponse,
  TravelProfile,
  profileLabel
} from "@/lib/planner-types";
import { reportHref } from "@/lib/route-link";

type ByField<T> = Record<PlaceField, T>;

const FIELD_LABEL: ByField<string> = { from: "From", to: "To" };
const FIELD_NOUN: ByField<string> = { from: "Start", to: "Destination" };

export default function PlannerPage() {
  const [places, setPlaces] = useState<ByField<PlannerPlace | null>>({ from: null, to: null });
  const [queries, setQueries] = useState<ByField<string>>({ from: "", to: "" });
  const [errors, setErrors] = useState<ByField<string>>({ from: "", to: "" });
  const [profile, setProfile] = useState<TravelProfile>("wheelchair");
  const [locationMessage, setLocationMessage] = useState("");
  // Demo routes for the current places and profile; any change clears them.
  const [result, setResult] = useState<{ query: PlannerQuery; response: RouteResponse } | null>(null);

  function choosePlace(field: PlaceField, place: PlannerPlace) {
    setPlaces((current) => ({ ...current, [field]: place }));
    setQueries((current) => ({ ...current, [field]: place.name }));
    setErrors((current) => ({ ...current, [field]: "" }));
    setResult(null);
  }

  function updateQuery(field: PlaceField, value: string) {
    setQueries((current) => ({ ...current, [field]: value }));
    setPlaces((current) => ({ ...current, [field]: null }));
    setResult(null);
  }

  function chooseProfile(next: TravelProfile) {
    setProfile(next);
    setResult(null);
  }

  function requestCurrentLocation() {
    if (!navigator.geolocation) {
      setLocationMessage("Current location is not available in this browser. Search for a starting place instead.");
      return;
    }
    setLocationMessage("Requesting your current location…");
    navigator.geolocation.getCurrentPosition(
      ({ coords }) => {
        choosePlace("from", {
          id: "current-location",
          name: "Current location",
          description: `${coords.latitude.toFixed(5)}, ${coords.longitude.toFixed(5)}`,
          coordinates: [coords.longitude, coords.latitude]
        });
        setLocationMessage("Using your current location as the starting point.");
      },
      () => setLocationMessage("We could not access your location. Search for a starting place instead."),
      { timeout: 8000 }
    );
  }

  function pickOnMap(field: PlaceField) {
    const place = field === "from" ? mockPlaces[0] : mockPlaces[1];
    choosePlace(field, place);
    setLocationMessage(`${FIELD_NOUN[field]} set to ${place.name} from the demo map.`);
  }

  function compare() {
    const missing = (["from", "to"] as const).find((field) => !places[field]);
    const same = places.from && places.to && places.from.id === places.to.id;
    if (missing || same) {
      const field = missing ?? "to";
      const message = missing
        ? `Choose ${missing === "from" ? "a starting place" : "a destination"} first.`
        : "Start and destination are the same place. Choose a different destination.";
      setErrors({ from: "", to: "", [field]: message });
      document.getElementById(placeInputId(field))?.focus();
      return;
    }
    if (!places.from || !places.to) return;
    setErrors({ from: "", to: "" });
    const query = { from: places.from, to: places.to, profile };
    setResult({ query, response: getMockRouteResponse(query, new Date()) });
  }

  const routes = result?.response.routes ?? [];
  const status = result
    ? `${routes.length} demo ${routes.length === 1 ? "route" : "routes"} for ${profileLabel(profile)}.`
    : "Choose places and compare routes to see demo results.";

  function placeInput(field: PlaceField) {
    return (
      <PlaceInput
        field={field}
        label={FIELD_LABEL[field]}
        value={queries[field]}
        selected={places[field]}
        places={mockPlaces}
        error={errors[field]}
        onQueryChange={updateQuery}
        onChoose={choosePlace}
        onMapPick={pickOnMap}
      />
    );
  }

  return (
    <div className="app-shell planner-shell">
      <SiteHeader current="plan" />

      <main id="main">
        <section className="planner-hero" aria-labelledby="planner-title">
          <p className="eyebrow">Accessible trip planner</p>
          <h1 id="planner-title">Where are you going?</h1>
          <p className="hero-copy">
            Choose your start, destination and travel needs. We&apos;ll compare routes around current obstructions.
          </p>
          <p className="demo-note">
            <strong>Demo data.</strong> Places, routes and conditions on this page are samples until the planner
            connects to the live route service.
          </p>
        </section>

        <div className="planner-layout">
          <form
            className="planner-form-panel"
            aria-labelledby="trip-title"
            noValidate
            onSubmit={(event) => {
              event.preventDefault();
              compare();
            }}
          >
            <div className="planner-section-heading">
              <div>
                <p className="eyebrow">1. Your trip</p>
                <h2 id="trip-title">Start and destination</h2>
              </div>
              <span className="mock-badge">Demo data</span>
            </div>
            {placeInput("from")}
            <button className="location-button" type="button" onClick={requestCurrentLocation}>
              <span aria-hidden="true">◎ </span>Use my current location for From
            </button>
            <p className="form-status" role="status">{locationMessage}</p>
            {placeInput("to")}
            <ProfilePicker value={profile} onChange={chooseProfile} />
            <button className="primary-button" type="submit">Compare accessible routes</button>
            <p className="form-hint">Choose both places to compare up to three routes.</p>
          </form>

          <section className="route-results" aria-labelledby="route-results-title">
            <div className="results-heading">
              <div>
                <p className="eyebrow">3. Compare routes</p>
                <h2 id="route-results-title">Route options</h2>
              </div>
              <span className="results-status">Demo route data</span>
            </div>
            <p className="route-status" role="status">{status}</p>
            {routes.length > 0 && (
              <div className="route-card-grid">
                {routes.map((route) => (
                  <RouteCard
                    key={route.rank}
                    route={route}
                    reportHref={result ? reportHref(result.query, route.rank) : "/route"}
                  />
                ))}
              </div>
            )}
          </section>

          <PlannerMapPreview from={places.from} to={places.to} onPick={pickOnMap} />
        </div>
      </main>

      <footer>
        <strong>OpenPath</strong>
        <span>Accessible information for every Edmonton journey.</span>
        <Link href="/city">View city conditions</Link>
      </footer>
    </div>
  );
}
