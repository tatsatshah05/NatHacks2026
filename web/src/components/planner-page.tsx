"use client";

// The planner (OP-301): search places, choose how you get around, find routes with
// GET /api/route, compare them on the line and the map, and follow live changes.
//
// The URL carries the trip (`/?from=lat,lng&to=lat,lng&profile=…`, lib/route-link.ts),
// so a shared link reopens the same routes. The map only illustrates the list.

import dynamic from "next/dynamic";
import { ReactNode, useCallback, useEffect, useMemo, useRef, useState } from "react";
import PlaceInput, { placeInputId } from "./place-input";
import type { MapCentre, MapCondition } from "./planner-map";
import ProfileIcon from "./profile-icons";
import ReportButton from "./report-button";
import LiveStatus from "./live-status";
import { NoRoute, RouteFailure } from "./route-problem";
import RouteResult from "./route-result";
import SaveRouteButton from "./save-route-button";
import Button from "./ui/button";
import CheckField from "./ui/check-field";
import CityWideConditions from "./ui/city-wide-conditions";
import LazyMount from "./ui/lazy-mount";
import Panel from "./ui/panel";
import { cityWideItems, stopId } from "./ui/route-line";
import SegmentedChoice from "./ui/segmented-choice";
import SiteFooter from "./ui/site-footer";
import SiteHeader from "./ui/site-header";
import StatusLine from "./ui/status-line";
import { getObstructionPoints, getRoutes, isAbort } from "@/lib/api";
import { useDataMode } from "@/lib/data-mode";
import { liveRouteFromQuery, useLiveUpdates } from "@/lib/live";
import { getMockRouteResponse } from "@/lib/mock-routes";
import {
  PlaceField,
  PlannerPlace,
  PlannerQuery,
  RouteOut,
  RouteResponse,
  TravelProfile,
  profilePhrase,
  travelProfiles
} from "@/lib/planner-types";
import { isCityWide, plural } from "@/lib/report-format";
import { parseTripParams, plannerHref, reportHref } from "@/lib/route-link";
import styles from "./planner-page.module.css";

// MapLibre is large and needs the browser; load it after the form is usable, and only
// once the map's place comes near the viewport (on a phone it sits below the form).
const PlannerMap = dynamic(() => import("./planner-map"), {
  ssr: false,
  loading: () => <div className={styles.mapLoading}>Loading the map.</div>
});

type ByField<T> = Record<PlaceField, T>;

const FIELD_LABEL: ByField<string> = { from: "From", to: "To" };

const PROFILE_OPTIONS = travelProfiles.map((option) => ({
  id: option.id,
  label: option.label,
  icon: <ProfileIcon profile={option.id} />
}));

const SLOW_MS = 6000;

type Result = { query: PlannerQuery; response: RouteResponse; now: Date; demo: boolean };
type Pending = { query: PlannerQuery; slow: boolean };

/**
 * Each route's "Save this route" button (OP-306). A saved route (OP-304) has no mode,
 * so the API watches the trip's walking route; a walk and LRT trip offers no Save
 * rather than promise alerts for a route the API does not watch.
 */
function saveAction(route: RouteOut, query: PlannerQuery): ReactNode {
  if (route.lrt) return null;
  return <SaveRouteButton query={query} rank={route.rank} />;
}

function reducedMotion(): boolean {
  return typeof window !== "undefined" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

export default function PlannerPage() {
  const [places, setPlaces] = useState<ByField<PlannerPlace | null>>({ from: null, to: null });
  const [queries, setQueries] = useState<ByField<string>>({ from: "", to: "" });
  const [errors, setErrors] = useState<ByField<string>>({ from: "", to: "" });
  const [profile, setProfile] = useState<TravelProfile>("wheelchair");
  const [includeLrt, setIncludeLrt] = useState(false);
  const [locationMessage, setLocationMessage] = useState("");
  // The routes shown, the request in flight and the last failure. Any change to the
  // trip clears the routes, so what is shown always matches the form.
  const [result, setResult] = useState<Result | null>(null);
  const [pending, setPending] = useState<Pending | null>(null);
  const [failure, setFailure] = useState<{ query: PlannerQuery; error: unknown } | null>(null);
  const [selectedRank, setSelectedRank] = useState(1);
  const [refreshNote, setRefreshNote] = useState("");
  const [conditions, setConditions] = useState<MapCondition[]>([]);

  const mode = useDataMode();
  const requestRef = useRef<AbortController | null>(null);
  const savedRef = useRef<string | null>(null);
  const centreRef = useRef<(() => MapCentre | null) | null>(null);
  const mapRef = useRef<HTMLDivElement>(null);

  const search = useCallback(async (query: PlannerQuery, { quiet = false }: { quiet?: boolean } = {}) => {
    requestRef.current?.abort();
    const controller = new AbortController();
    requestRef.current = controller;
    let slow: ReturnType<typeof setTimeout> | undefined;
    if (quiet) {
      setRefreshNote("Conditions changed. Checking the routes again.");
    } else {
      setResult(null);
      setFailure(null);
      setRefreshNote("");
      setSelectedRank(1);
      setPending({ query, slow: false });
      slow = setTimeout(() => setPending((current) => (current ? { ...current, slow: true } : current)), SLOW_MS);
    }
    try {
      const response = await getRoutes(query, controller.signal);
      setResult({ query, response, now: new Date(), demo: false });
      setFailure(null);
      if (quiet) setRefreshNote("Routes checked again after the change.");
      // The URL now reopens these routes; replaceState keeps the history clean.
      window.history.replaceState(null, "", plannerHref(query, { saved: savedRef.current }));
    } catch (error) {
      if (isAbort(error)) return;
      if (quiet) setRefreshNote("The routes could not be checked again. They show conditions from before the change.");
      else setFailure({ query, error });
    } finally {
      clearTimeout(slow);
      if (requestRef.current === controller) {
        requestRef.current = null;
        setPending(null);
      }
    }
  }, []);

  // A link with a trip fills the form and runs the search once, when the page opens.
  useEffect(() => {
    const trip = parseTripParams(new URLSearchParams(window.location.search));
    savedRef.current = trip.saved;
    setProfile(trip.profile);
    setIncludeLrt(trip.mode === "walk_lrt");
    const chosen = { from: trip.from, to: trip.to };
    setPlaces(chosen);
    setQueries({ from: trip.from?.name ?? "", to: trip.to?.name ?? "" });
    if (trip.from && trip.to) void search({ from: trip.from, to: trip.to, profile: trip.profile, mode: trip.mode });
    return () => requestRef.current?.abort();
  }, [search]);

  /** The trip changed: routes for the old one no longer apply. */
  function clearResults() {
    requestRef.current?.abort();
    setResult(null);
    setFailure(null);
    setPending(null);
    setRefreshNote("");
    savedRef.current = null;
    if (window.location.search) window.history.replaceState(null, "", "/");
  }

  function choosePlace(field: PlaceField, place: PlannerPlace) {
    setPlaces((current) => ({ ...current, [field]: place }));
    setQueries((current) => ({ ...current, [field]: place.name }));
    setErrors((current) => ({ ...current, [field]: "" }));
    clearResults();
  }

  function updateQuery(field: PlaceField, value: string) {
    setQueries((current) => ({ ...current, [field]: value }));
    setPlaces((current) => ({ ...current, [field]: null }));
    clearResults();
  }

  function chooseProfile(next: TravelProfile) {
    setProfile(next);
    clearResults();
  }

  function chooseLrt(next: boolean) {
    setIncludeLrt(next);
    clearResults();
  }

  function requestCurrentLocation() {
    if (!("geolocation" in navigator)) {
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
      (error) =>
        setLocationMessage(
          error.code === error.PERMISSION_DENIED
            ? "Location access is off for this site. Search for a starting place instead."
            : "Your location is not available. Search for a starting place instead."
        ),
      { enableHighAccuracy: true, timeout: 10000, maximumAge: 60000 }
    );
  }

  function findRoutes() {
    const missing = (["from", "to"] as const).find((field) => !places[field]);
    const same =
      places.from &&
      places.to &&
      places.from.coordinates[0] === places.to.coordinates[0] &&
      places.from.coordinates[1] === places.to.coordinates[1];
    if (missing || same) {
      const field = missing ?? "to";
      const message = missing
        ? `Choose ${missing === "from" ? "a starting place" : "a destination"} from the suggestions first.`
        : "Start and destination are the same place. Choose a different destination.";
      setErrors({ from: "", to: "", [field]: message });
      document.getElementById(placeInputId(field))?.focus();
      return;
    }
    if (!places.from || !places.to) return;
    setErrors({ from: "", to: "" });
    void search({ from: places.from, to: places.to, profile, mode: includeLrt ? "walk_lrt" : "walk" });
  }

  function showDemo(query: PlannerQuery) {
    const now = new Date();
    setFailure(null);
    setSelectedRank(1);
    setResult({ query, response: getMockRouteResponse(query, now), now, demo: true });
  }

  // Live updates follow the shown trip (never the demo). A route change is announced
  // by LiveStatus and the routes are fetched again, keeping the old ones on screen.
  const liveQuery = result && !result.demo ? result.query : null;
  const liveRoute = useMemo(() => (liveQuery ? liveRouteFromQuery(liveQuery) : null), [liveQuery]);
  const live = useLiveUpdates({ route: liveRoute, enabled: Boolean(liveRoute) });
  const changeSeq = live.routeChange?.seq ?? 0;
  useEffect(() => {
    if (changeSeq && liveQuery) void search(liveQuery, { quiet: true });
    // Only a new change refetches; the query is read when it arrives.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [changeSeq]);

  const routes = useMemo(() => result?.response.routes ?? [], [result]);
  const selected = routes.find((route) => route.rank === selectedRank) ?? routes[0] ?? null;

  // Where the selected route's conditions are, for the map's markers. The report has
  // no coordinates, so they come from /api/obstructions by id. Best effort: the list
  // already says everything, so a failure here only leaves the markers out.
  useEffect(() => {
    setConditions([]);
    if (!result || result.demo || !selected) return;
    const items = selected.report.groups.flatMap((group) => group.items).filter((item) => !isCityWide(item));
    if (!items.length) return;
    const points = (selected.geometry.coordinates as [number, number][]) ?? [];
    if (!points.length) return;
    const lngs = points.map((point) => point[0]);
    const lats = points.map((point) => point[1]);
    const pad = 0.003; // about 300 m, for items near or avoided by the route
    const bbox: [number, number, number, number] = [
      Math.min(...lngs) - pad,
      Math.min(...lats) - pad,
      Math.max(...lngs) + pad,
      Math.max(...lats) + pad
    ];
    const controller = new AbortController();
    getObstructionPoints(bbox, controller.signal)
      .then((found) =>
        setConditions(
          items.flatMap((item) => {
            const point = found.get(item.id);
            return point ? [{ id: item.id, effect: item.effect, title: item.title, lng: point.lng, lat: point.lat }] : [];
          })
        )
      )
      .catch(() => undefined);
    return () => controller.abort();
  }, [result, selected]);

  // "Show on the map": draw this route as the thick line and take the person to the
  // map. The button they pressed becomes text, so focus moves to the map section.
  const selectRoute = useCallback((rank: number) => {
    setSelectedRank(rank);
    const map = mapRef.current;
    if (!map) return;
    map.scrollIntoView({ block: "nearest", behavior: reducedMotion() ? "auto" : "smooth" });
    map.focus({ preventScroll: true });
  }, []);

  const toggleRoute = useCallback((rank: number, open: boolean) => {
    if (open) setSelectedRank(rank);
    else setSelectedRank((current) => (current === rank ? 1 : current));
  }, []);

  const jumpToCondition = useCallback(
    (id: string) => {
      const stop = document.getElementById(stopId(selectedRank, id));
      if (!stop) return;
      stop.scrollIntoView({ block: "center" });
      stop.focus({ preventScroll: true });
    },
    [selectedRank]
  );

  const everywhere = cityWideItems(routes);
  const replay = mode === "replay";
  const dataLabel = result?.demo
    ? "This is a demo route, not a real route or current conditions."
    : replay
      ? "Replayed data."
      : null;

  let status = "";
  if (pending) {
    const { query } = pending;
    status = `Finding routes ${profilePhrase(query.profile)} from ${query.from.name} to ${query.to.name}.`;
    if (pending.slow) status += " The route service is slow to answer. Still trying.";
  } else if (result && routes.length) {
    status = result.demo
      ? `${plural(routes.length, "demo route", "demo routes")} ${profilePhrase(result.query.profile)}. They are samples, not real routes or current conditions.`
      : `${plural(routes.length, "route", "routes")} ${profilePhrase(result.query.profile)}, best first.${replay ? " Replayed data, not current conditions." : ""}`;
  }
  // A failure or "no route" is announced by its own alert, so the status line stays empty.

  const notes = result ? tripNotes(result) : [];

  function placeInput(field: PlaceField) {
    return (
      <PlaceInput
        field={field}
        label={FIELD_LABEL[field]}
        value={queries[field]}
        selected={places[field]}
        error={errors[field]}
        onQueryChange={updateQuery}
        onChoose={choosePlace}
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
            <CheckField
              id="include-lrt"
              label="Include LRT trips"
              checked={includeLrt}
              onChange={chooseLrt}
              help="Walk, ride the LRT and walk on, when that is faster."
            />
            <Button type="submit" size="large" block>
              Find routes
            </Button>
          </form>

          <section className={`results ${styles.results}`} aria-label="Routes">
            <StatusLine>{status}</StatusLine>
            {failure && (
              <RouteFailure
                error={failure.error}
                onRetry={() => void search(failure.query)}
                onDemo={() => showDemo(failure.query)}
              />
            )}
            {result && !routes.length && <NoRoute response={result.response} profile={result.query.profile} />}
            {liveRoute && <LiveStatus live={live} />}
            {refreshNote && <p className={styles.refresh}>{refreshNote}</p>}
            {notes.length > 0 && (
              <ul className={`warnings ${styles.notes}`} aria-label="Notes about this trip">
                {notes.map((note) => (
                  <li key={note}>{note}</li>
                ))}
              </ul>
            )}
            {result && <CityWideConditions items={everywhere} now={result.now} />}
            {result && routes.length > 0 && (
              <ol className="routes" role="list">
                {routes.map((route) => (
                  <Panel
                    as="li"
                    // By rank, so a live refresh keeps open routes open and focus in place.
                    key={route.rank}
                    aria-labelledby={`route-${route.rank}-heading`}
                    className={route.rank === selected?.rank ? styles.selected : undefined}
                  >
                    <RouteResult
                      route={route}
                      routeCount={routes.length}
                      query={result.query}
                      now={result.now}
                      appleMapsNote={result.response.apple_maps_note}
                      reportLink={reportHref(result.query, route.rank, {
                        saved: savedRef.current,
                        demo: result.demo
                      })}
                      selected={route.rank === selected?.rank}
                      onSelect={selectRoute}
                      onToggle={toggleRoute}
                      dataLabel={dataLabel}
                      saveAction={result.demo ? null : saveAction(route, result.query)}
                    />
                  </Panel>
                ))}
              </ol>
            )}
          </section>
        </div>

        <div className="page-side">
          <section className={styles.side} aria-labelledby="planner-map-title" ref={mapRef} tabIndex={-1}>
            <h2 className="sr-only" id="planner-map-title">
              Map
            </h2>
            <LazyMount placeholder={<div className={styles.mapLoading}>Loading the map.</div>}>
              <PlannerMap
                routes={routes}
                selectedRank={selected?.rank ?? null}
                from={places.from}
                to={places.to}
                conditions={conditions}
                onCondition={jumpToCondition}
                centreRef={centreRef}
              />
            </LazyMount>
            {routes.length > 0 && (
              <p className={styles.mapNote}>The list has everything the map shows, in words.</p>
            )}
          </section>
          <section className={styles.report} aria-labelledby="report-title">
            <h2 className={styles.reportTitle} id="report-title">
              See snow, ice or a blocked path?
            </h2>
            <p>Tell other people planning a route. Use your location or the centre of the map.</p>
            <ReportButton getLocation={() => centreRef.current?.() ?? null} />
          </section>
        </div>
      </main>

      <SiteFooter>
        <p>Map data © OpenStreetMap contributors, map style OpenFreeMap.</p>
      </SiteFooter>
    </div>
  );
}

/** Notes about the whole trip: where the API moved a point, and whether LRT was used. */
function tripNotes({ query, response, demo }: Result): string[] {
  if (demo || !response.routes.length) return [];
  const notes = [...response.warnings];
  if (query.mode === "walk_lrt") {
    const unavailable = "LRT trips are not available right now. These are walking routes.";
    if (response.mode !== "walk_lrt") notes.push(unavailable);
    else if (!response.routes.some((route) => route.legs?.length)) {
      if (!notes.some((note) => note.startsWith("Walk and LRT trips are unavailable"))) {
        notes.push("No walk and LRT trip is faster than walking here, so these are walking routes.");
      }
    }
  }
  return [...new Set(notes)];
}
