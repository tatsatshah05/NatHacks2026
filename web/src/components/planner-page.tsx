"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import PlaceInput from "./place-input";
import ProfilePicker from "./profile-picker";
import RouteCard from "./route-card";
import { getMockRoutes, mockPlaces, PlannerPlace, TravelProfile } from "@/lib/planner-types";

type PlaceField = "from" | "to";
const initialPlaces: Record<PlaceField, PlannerPlace | null> = { from: null, to: null };

export default function PlannerPage() {
  const [places, setPlaces] = useState(initialPlaces);
  const [queries, setQueries] = useState<Record<PlaceField, string>>({ from: "", to: "" });
  const [profile, setProfile] = useState<TravelProfile>("wheelchair");
  const [activeField, setActiveField] = useState<PlaceField | null>(null);
  const [locationMessage, setLocationMessage] = useState("");
  const [hasPlanned, setHasPlanned] = useState(false);

  const suggestions = useMemo(() => {
    if (!activeField || !queries[activeField].trim()) return [];
    const query = queries[activeField].toLowerCase();
    return mockPlaces.filter((place) => `${place.name} ${place.description}`.toLowerCase().includes(query));
  }, [activeField, queries]);
  const routes = hasPlanned && places.from && places.to ? getMockRoutes({ from: places.from, to: places.to, profile }) : [];

  function choosePlace(field: PlaceField, place: PlannerPlace) {
    setPlaces((current) => ({ ...current, [field]: place }));
    setQueries((current) => ({ ...current, [field]: place.name }));
    setActiveField(null);
  }

  function useCurrentLocation() {
    if (!navigator.geolocation) {
      setLocationMessage("Current location is not available in this browser.");
      return;
    }
    setLocationMessage("Requesting your current location…");
    navigator.geolocation.getCurrentPosition(
      ({ coords }) => {
        const currentPlace: PlannerPlace = {
          id: "current-location",
          name: "Current location",
          description: `${coords.latitude.toFixed(5)}, ${coords.longitude.toFixed(5)}`,
          coordinates: [coords.longitude, coords.latitude]
        };
        choosePlace("from", currentPlace);
        setLocationMessage("Using your current location as the starting point.");
      },
      () => setLocationMessage("We could not access your location. Search for a starting place instead."),
      { timeout: 8000 }
    );
  }

  function pickOnMap(field: PlaceField) {
    choosePlace(field, field === "from" ? mockPlaces[0] : mockPlaces[1]);
    setLocationMessage(`${field === "from" ? "Starting point" : "Destination"} selected from the map preview.`);
  }

  function updateQuery(field: PlaceField, value: string) {
    setQueries((current) => ({ ...current, [field]: value }));
    setPlaces((current) => ({ ...current, [field]: null }));
    setActiveField(field);
    setHasPlanned(false);
  }

  return (
    <main className="app-shell planner-shell">
      <header className="site-header">
        <Link className="brand" href="/" aria-label="OpenPath home"><span className="brand-mark" aria-hidden="true">⌁</span><span>OpenPath</span></Link>
        <nav aria-label="Primary navigation"><Link className="active-nav" href="/">Plan</Link><Link href="/city">City</Link></nav>
        <button className="outline-button" type="button" onClick={useCurrentLocation}>Use my location</button>
      </header>

      <section className="planner-hero" aria-labelledby="planner-title">
        <p className="eyebrow">Accessible trip planner</p>
        <h1 id="planner-title">Where are you going?</h1>
        <p className="hero-copy">Choose your start, destination and travel needs. We&apos;ll compare routes around current obstructions.</p>
      </section>

      <section className="planner-layout" aria-label="Plan an accessible route">
        <div className="planner-form-panel">
          <div className="planner-section-heading"><div><p className="eyebrow">1. Your trip</p><h2>Start and destination</h2></div><span className="mock-badge">Demo data</span></div>
          <PlaceInput field="from" label="From" value={queries.from} selected={places.from} activeField={activeField} suggestions={suggestions} onFocus={setActiveField} onChange={updateQuery} onChoose={choosePlace} onMapPick={pickOnMap} onEscape={() => setActiveField(null)} />
          <PlaceInput field="to" label="To" value={queries.to} selected={places.to} activeField={activeField} suggestions={suggestions} onFocus={setActiveField} onChange={updateQuery} onChoose={choosePlace} onMapPick={pickOnMap} onEscape={() => setActiveField(null)} />
          <button className="location-button" type="button" onClick={useCurrentLocation}>◎ Use my current location for From</button>
          {locationMessage && <p className="form-status" role="status">{locationMessage}</p>}
          <ProfilePicker value={profile} onChange={(nextProfile) => { setProfile(nextProfile); setHasPlanned(false); }} />
          <button className="primary-button" type="button" disabled={!places.from || !places.to} onClick={() => setHasPlanned(true)}>Compare accessible routes</button>
          {!places.from || !places.to ? <p className="form-hint">Choose both places to compare up to three routes.</p> : null}
        </div>

        <div className="planner-map-panel">
          <div className="panel-heading"><div><p className="eyebrow">Map preview</p><h2>Tap to choose a place</h2></div><span className="mock-badge">Demo map</span></div>
          <div className="planner-map" role="group" aria-label="Map preview with selectable places">
            <span className="map-road road-one" aria-hidden="true" /><span className="map-road road-two" aria-hidden="true" /><span className="map-river" aria-hidden="true" />
            <button type="button" className={`map-place place-one${places.from ? " chosen" : ""}`} onClick={() => pickOnMap("from")}><span aria-hidden="true">A</span><strong>Start</strong><small>{places.from?.name ?? "Choose From"}</small></button>
            <button type="button" className={`map-place place-two${places.to ? " chosen" : ""}`} onClick={() => pickOnMap("to")}><span aria-hidden="true">B</span><strong>Destination</strong><small>{places.to?.name ?? "Choose To"}</small></button>
            <p className="map-legend">Demo preview · use search for exact places</p>
          </div>
          <p className="map-help">Map selection is available by keyboard as labeled Start and Destination buttons.</p>
        </div>
      </section>

      <section className="route-results" aria-labelledby="route-results-title">
        <div className="results-heading"><div><p className="eyebrow">3. Compare routes</p><h2 id="route-results-title">{routes.length ? `${routes.length} routes found` : "Your route options"}</h2></div><span className="results-status">Demo route data</span></div>
        <p className="route-status" role="status">{hasPlanned ? `${routes.length} demo routes for ${profileLabel(profile)}` : "Choose places and compare routes to see demo results."}</p>
        <div className="route-card-grid">{routes.map((route, index) => <RouteCard key={route.rank} route={route} recommended={index === 0} />)}</div>
      </section>

      <footer><strong>OpenPath</strong><span>Accessible information for every Edmonton journey.</span><Link href="/city">View city conditions</Link></footer>
    </main>
  );
}

function profileLabel(profile: TravelProfile) {
  return { wheelchair: "Wheelchair", walker_cane: "Walker or cane", low_vision: "Low vision", standard: "Standard walking" }[profile];
}
