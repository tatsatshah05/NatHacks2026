"use client";

import { useEffect, useState } from "react";
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
import { getRoute } from "@/lib/api";
import { mockPlaces } from "@/lib/mock-routes";
import {
  PlaceField,
  PlannerPlace,
  PlannerQuery,
  RouteResponse,
  TravelProfile,
  isTravelProfile,
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
  const [result, setResult] = useState<Result | null>(null);
  const [requestState, setRequestState] = useState<"idle" | "loading" | "error" | "offline">("idle");
  const [requestMessage, setRequestMessage] = useState("");
  const [selectedRank, setSelectedRank] = useState<number | null>(null);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const from = parseUrlPlace("from", params);
    const to = parseUrlPlace("to", params);
    const profileParam = params.get("profile");
    if (!from || !to || !isTravelProfile(profileParam)) return;
    const query = { from, to, profile: profileParam };
    setPlaces({ from, to });
    setQueries({ from: from.name, to: to.name });
    void loadRoutes(query);
    // URL restoration is intentionally performed once on initial page load.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function choosePlace(field: PlaceField, place: PlannerPlace) {
    setPlaces((current) => ({ ...current, [field]: place }));
    setQueries((current) => ({ ...current, [field]: place.name }));
    setErrors((current) => ({ ...current, [field]: "" }));
    setResult(null);
    setRequestState("idle");
    setRequestMessage("");
    setSelectedRank(null);
  }

  function updateQuery(field: PlaceField, value: string) {
    setQueries((current) => ({ ...current, [field]: value }));
    setPlaces((current) => ({ ...current, [field]: null }));
    setResult(null);
    setRequestState("idle");
    setRequestMessage("");
    setSelectedRank(null);
  }

  function chooseProfile(next: TravelProfile) {
    setProfile(next);
    setResult(null);
    setRequestState("idle");
    setRequestMessage("");
    setSelectedRank(null);
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

  async function loadRoutes(query: PlannerQuery) {
    if (!navigator.onLine) {
      setRequestState("offline");
      setRequestMessage("You appear to be offline. Reconnect and try again.");
      return;
    }
    setRequestState("loading");
    setRequestMessage("");
    setSelectedRank(1);
    try {
      const response = await getRoute(query);
      const now = new Date();
      setResult({ query, response, now });
      setRequestState("idle");
      const params = new URLSearchParams({
        from: `${query.from.coordinates[1]},${query.from.coordinates[0]}`,
        from_name: query.from.name,
        to: `${query.to.coordinates[1]},${query.to.coordinates[0]}`,
        to_name: query.to.name,
        profile: query.profile
      });
      window.history.replaceState(null, "", `/?${params.toString()}`);
    } catch (error) {
      if (error instanceof DOMException && error.name === "AbortError") return;
      setRequestState("error");
      setRequestMessage(error instanceof Error ? error.message : "The route service could not be reached.");
      setResult(null);
    }
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
    void loadRoutes(query);
  }

  const routes = result?.response.routes ?? [];
  const everywhere = cityWideItems(routes);
  const status = result
    ? `${routes.length} ${routes.length === 1 ? "route" : "routes"} ${profilePhrase(result.query.profile)}, best first.`
    : requestState === "loading"
      ? "Finding routes."
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
            Routes and conditions come from the OpenPath route service.
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
            {requestState !== "idle" && <StatusLine className={requestState === "error" ? "status-error" : undefined}>{requestMessage}</StatusLine>}
            {result && <CityWideConditions items={everywhere} now={result.now} />}
            {result && routes.length > 0 && (
              <ol className="routes" role="list">
                {routes.map((route) => (
                  <Panel as="li" key={`${result.now.getTime()}-${route.rank}`} aria-labelledby={`route-${route.rank}-heading`}>
                    <button
                      type="button"
                      className={`route-select${selectedRank === route.rank ? " is-selected" : ""}`}
                      aria-pressed={selectedRank === route.rank}
                      onClick={() => setSelectedRank(route.rank)}
                    >
                      Select route {route.rank}
                    </button>
                    <RouteResult route={route} routeCount={routes.length} query={result.query} now={result.now} appleMapsNote={result.response.apple_maps_note} />
                  </Panel>
                ))}
              </ol>
            )}
          </section>
        </div>

        <div className="page-side">
          <PlannerMapPreview from={places.from} to={places.to} selectedRank={selectedRank} onPick={pickOnMap} />
        </div>
      </main>

      <SiteFooter />
    </div>
  );
}

function parseUrlPlace(field: PlaceField, params: URLSearchParams): PlannerPlace | null {
  const values = (params.get(field) ?? "").split(",").map(Number);
  if (values.length !== 2 || values.some((value) => !Number.isFinite(value))) return null;
  const [lat, lng] = values;
  if (Math.abs(lat) > 90 || Math.abs(lng) > 180) return null;
  const name = params.get(`${field}_name`)?.trim() || (field === "from" ? "Start" : "Destination");
  return {
    id: `${field}-${lat},${lng}`,
    name,
    description: `${lat.toFixed(5)}, ${lng.toFixed(5)}`,
    coordinates: [lng, lat]
  };
}
