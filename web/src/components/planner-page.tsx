"use client";

import { useState } from "react";
import PlaceInput, { placeInputId } from "./place-input";
import PlannerMapPreview from "./planner-map-preview";
import ProfileIcon from "./profile-icons";
import RouteResult from "./route-result";
import Button from "./ui/button";
import CityWideConditions from "./ui/city-wide-conditions";
import Panel from "./ui/panel";
import { cityWideItems } from "./ui/route-line";
import SegmentedChoice from "./ui/segmented-choice";
import SiteFooter from "./ui/site-footer";
import SiteHeader from "./ui/site-header";
import StatusLine from "./ui/status-line";
import { getMockRouteResponse, mockPlaces } from "@/lib/mock-routes";
import {
  PlaceField,
  PlannerPlace,
  PlannerQuery,
  RouteResponse,
  TravelProfile,
  profilePhrase,
  travelProfiles
} from "@/lib/planner-types";

type ByField<T> = Record<PlaceField, T>;

const FIELD_LABEL: ByField<string> = { from: "From", to: "To" };
const FIELD_NOUN: ByField<string> = { from: "Start", to: "Destination" };

const PROFILE_OPTIONS = travelProfiles.map((option) => ({
  id: option.id,
  label: option.label,
  icon: <ProfileIcon profile={option.id} />
}));

type Result = { query: PlannerQuery; response: RouteResponse; now: Date };

export default function PlannerPage() {
  const [places, setPlaces] = useState<ByField<PlannerPlace | null>>({ from: null, to: null });
  const [queries, setQueries] = useState<ByField<string>>({ from: "", to: "" });
  const [errors, setErrors] = useState<ByField<string>>({ from: "", to: "" });
  const [profile, setProfile] = useState<TravelProfile>("wheelchair");
  const [locationMessage, setLocationMessage] = useState("");
  // Demo routes for the current places and profile; any change clears them.
  const [result, setResult] = useState<Result | null>(null);

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
      setLocationMessage("This browser cannot share your location. Search for a starting place instead.");
      return;
    }
    setLocationMessage("Asking for your location.");
    navigator.geolocation.getCurrentPosition(
      ({ coords }) => {
        choosePlace("from", {
          id: "current-location",
          name: "Current location",
          description: `${coords.latitude.toFixed(5)}, ${coords.longitude.toFixed(5)}`,
          coordinates: [coords.longitude, coords.latitude]
        });
        setLocationMessage("Starting from your current location.");
      },
      () => setLocationMessage("Your location is not available. Search for a starting place instead."),
      { timeout: 8000 }
    );
  }

  function pickOnMap(field: PlaceField) {
    const place = field === "from" ? mockPlaces[0] : mockPlaces[1];
    choosePlace(field, place);
    setLocationMessage(`${FIELD_NOUN[field]} set to ${place.name} from the demo map.`);
  }

  function findRoutes() {
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
    const now = new Date();
    setResult({ query, response: getMockRouteResponse(query, now), now });
  }

  const routes = result?.response.routes ?? [];
  const everywhere = cityWideItems(routes);
  const status = result
    ? `${routes.length} demo ${routes.length === 1 ? "route" : "routes"} ${profilePhrase(result.query.profile)}, best first.`
    : "";

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
        footer={
          field === "from" ? (
            <>
              <Button variant="text" onClick={requestCurrentLocation}>
                Use my current location
              </Button>
              <p className="field-note" role="status">
                {locationMessage}
              </p>
            </>
          ) : undefined
        }
      />
    );
  }

  return (
    <div className="shell">
      <SiteHeader current="plan" />

      <main id="main" className="page">
        <div className="page-main">
          <h1 className="page-title">Where are you going?</h1>
          <p className="note">
            <strong>Demo data.</strong> Places, routes and conditions here are samples, not current Edmonton
            conditions, until the planner connects to the route service.
          </p>

          <form
            className="form-stack"
            aria-labelledby="planner-form-name"
            noValidate
            onSubmit={(event) => {
              event.preventDefault();
              findRoutes();
            }}
          >
            <span className="sr-only" id="planner-form-name">
              Plan a trip
            </span>
            {placeInput("from")}
            {placeInput("to")}
            <SegmentedChoice
              legend="How do you get around?"
              name="travel-profile"
              options={PROFILE_OPTIONS}
              value={profile}
              onChange={chooseProfile}
            />
            <Button type="submit" size="large" block>
              Find routes
            </Button>
          </form>

          <section className="results" aria-label="Routes">
            <StatusLine>{status}</StatusLine>
            {result && <CityWideConditions items={everywhere} now={result.now} />}
            {result && routes.length > 0 && (
              <ol className="routes" role="list">
                {routes.map((route) => (
                  <Panel as="li" key={`${result.now.getTime()}-${route.rank}`} aria-labelledby={`route-${route.rank}-heading`}>
                    <RouteResult
                      route={route}
                      routeCount={routes.length}
                      query={result.query}
                      now={result.now}
                      appleMapsNote={result.response.apple_maps_note}
                    />
                  </Panel>
                ))}
              </ol>
            )}
          </section>
        </div>

        <div className="page-side">
          <PlannerMapPreview from={places.from} to={places.to} onPick={pickOnMap} />
        </div>
      </main>

      <SiteFooter />
    </div>
  );
}
