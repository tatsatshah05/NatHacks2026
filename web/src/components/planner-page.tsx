"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import {
  getMockRoutes,
  mockPlaces,
  PlannerPlace,
  TravelProfile,
  travelProfiles
} from "@/lib/planner-types";

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
    return mockPlaces.filter((place) =>
      `${place.name} ${place.description}`.toLowerCase().includes(query)
    );
  }, [activeField, queries]);

  const routes = places.from && places.to
    ? getMockRoutes({ from: places.from, to: places.to, profile })
    : [];

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
        const nearest = mockPlaces[0];
        choosePlace("from", nearest);
        setLocationMessage(`Using your current location near ${nearest.name}.`);
        void coords;
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
        <Link className="brand" href="/" aria-label="PathSense home">
          <span className="brand-mark" aria-hidden="true">⌁</span><span>PathSense</span>
        </Link>
        <nav aria-label="Primary navigation">
          <Link className="active-nav" href="/">Plan</Link>
          <a href="#alerts">Alerts</a>
          <Link href="/city">City</Link>
        </nav>
        <button className="outline-button" type="button" onClick={useCurrentLocation}>Use my location</button>
      </header>

      <section className="planner-hero" aria-labelledby="planner-title">
        <p className="eyebrow">Accessible trip planner</p>
        <h1 id="planner-title">Where are you going?</h1>
        <p className="hero-copy">Choose your start, destination and travel needs. We&apos;ll compare routes around current obstructions.</p>
      </section>

      <section className="planner-layout" aria-label="Plan an accessible route">
        <div className="planner-form-panel">
          <div className="planner-section-heading">
            <div><p className="eyebrow">1. Your trip</p><h2>Start and destination</h2></div>
            <span className="mock-badge">Demo data</span>
          </div>
          <PlaceInput
            field="from"
            label="From"
            value={queries.from}
            selected={places.from}
            activeField={activeField}
            suggestions={suggestions}
            onFocus={setActiveField}
            onChange={updateQuery}
            onChoose={choosePlace}
            onMapPick={pickOnMap}
          />
          <PlaceInput
            field="to"
            label="To"
            value={queries.to}
            selected={places.to}
            activeField={activeField}
            suggestions={suggestions}
            onFocus={setActiveField}
            onChange={updateQuery}
            onChoose={choosePlace}
            onMapPick={pickOnMap}
          />
          <button className="location-button" type="button" onClick={useCurrentLocation}>◎ Use my current location for From</button>
          {locationMessage && <p className="form-status" role="status">{locationMessage}</p>}

          <fieldset className="profile-fieldset">
            <legend><span className="eyebrow">2. Your travel needs</span><strong>How are you moving?</strong></legend>
            <div className="profile-grid">
              {travelProfiles.map((option) => (
                <label className={`profile-option${profile === option.id ? " selected" : ""}`} key={option.id}>
                  <input
                    type="radio"
                    name="travel-profile"
                    value={option.id}
                    checked={profile === option.id}
                    onChange={() => { setProfile(option.id); setHasPlanned(false); }}
                  />
                  <span className="profile-icon" aria-hidden="true">{option.icon}</span>
                  <span><strong>{option.label}</strong><small>{option.description}</small></span>
                </label>
              ))}
            </div>
          </fieldset>
          <button className="primary-button" type="button" disabled={!places.from || !places.to} onClick={() => setHasPlanned(true)}>
            Compare accessible routes
          </button>
          {!places.from || !places.to ? <p className="form-hint">Choose both places to compare up to three routes.</p> : null}
        </div>

        <div className="planner-map-panel">
          <div className="panel-heading"><div><p className="eyebrow">Map preview</p><h2>Tap to choose a place</h2></div><span className="live-pill"><span aria-hidden="true" /> Live conditions</span></div>
          <div className="planner-map" role="group" aria-label="Map preview with selectable places">
            <span className="map-road road-one" aria-hidden="true" /><span className="map-road road-two" aria-hidden="true" /><span className="map-river" aria-hidden="true" />
            <button type="button" className={`map-place place-one${places.from ? " chosen" : ""}`} onClick={() => pickOnMap("from")}><span aria-hidden="true">A</span><strong>Start</strong><small>{places.from?.name ?? "Choose From"}</small></button>
            <button type="button" className={`map-place place-two${places.to ? " chosen" : ""}`} onClick={() => pickOnMap("to")}><span aria-hidden="true">B</span><strong>Destination</strong><small>{places.to?.name ?? "Choose To"}</small></button>
            <p className="map-legend">Preview only · use search for exact places</p>
          </div>
          <p className="map-help">Map selection is available by keyboard as labeled Start and Destination buttons.</p>
        </div>
      </section>

      <section className="route-results" aria-labelledby="route-results-title" aria-live="polite">
        <div className="results-heading"><div><p className="eyebrow">3. Compare routes</p><h2 id="route-results-title">{routes.length ? `${routes.length} routes found` : "Your route options"}</h2></div>{hasPlanned && <span className="results-status">Mock routes ready</span>}</div>
        {!routes.length && <p className="empty-state">Your route options will appear here after you choose a From and To place.</p>}
        <div className="route-card-grid">
          {routes.map((route, index) => <RouteCard key={route.id} route={route} recommended={index === 0} />)}
        </div>
      </section>

      <footer id="alerts"><strong>PathSense</strong><span>Routes are based on current conditions and should be checked before departure.</span><Link href="/city">View city conditions</Link></footer>
    </main>
  );
}

function PlaceInput({
  field, label, value, selected, activeField, suggestions, onFocus, onChange, onChoose, onMapPick
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
}) {
  const listId = `${field}-suggestions`;
  return (
    <div className="place-field">
      <label htmlFor={`${field}-input`}>{label}</label>
      <div className="place-input-row">
        <span className="place-symbol" aria-hidden="true">{field === "from" ? "A" : "B"}</span>
        <input id={`${field}-input`} value={value} onFocus={() => onFocus(field)} onChange={(event) => onChange(field, event.target.value)} placeholder={field === "from" ? "Search starting point" : "Search destination"} aria-describedby={`${field}-hint`} />
        <button type="button" className="map-pick-button" onClick={() => onMapPick(field)} aria-label={`Choose ${label} on map`}>⌖</button>
      </div>
      <span className="sr-only" id={`${field}-hint`}>Search by address or place name, or choose it on the map.</span>
      {selected && <p className="selected-place">Selected: {selected.name}, {selected.description}</p>}
      {activeField === field && suggestions.length > 0 && <div className="suggestion-list" id={listId} role="listbox" aria-label={`${label} suggestions`}>{suggestions.map((place) => <button type="button" role="option" aria-selected={false} key={place.id} onClick={() => onChoose(field, place)}><strong>{place.name}</strong><small>{place.description}</small></button>)}</div>}
    </div>
  );
}

function RouteCard({ route, recommended }: { route: ReturnType<typeof getMockRoutes>[number]; recommended: boolean }) {
  return (
    <article className={`route-card${recommended ? " recommended" : ""}`}>
      <div className="route-card-top"><div><p className="route-label">{route.label}</p>{recommended && <span className="recommended-badge">Recommended</span>}</div><span className="route-minutes"><strong>{route.minutes}</strong> min</span></div>
      <p className="route-distance">{route.distanceKm.toFixed(1)} km · {route.note}</p>
      <div className="avoided-list"><strong>Avoids</strong>{route.avoidedItems.map((item) => <span key={item}>✓ {item}</span>)}</div>
      <button type="button" className="route-button">View route details<span className="sr-only"> for {route.label}</span></button>
    </article>
  );
}
