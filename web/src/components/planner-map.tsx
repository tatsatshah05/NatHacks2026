"use client";

// The planner's map (OP-301): the API's routes on the OpenFreeMap liberty basemap, or
// on the high-contrast style when that setting is on (OP-307, wired in OP-410).
//
// - The selected route is the thick line (6 px, the route colour, on a casing); the
//   other routes are thin muted lines. A walk and LRT trip draws its rides as a double
//   line, as on the route line beside it.
// - Start and end are filled circles with a "Start" / "End" tag.
// - Conditions on the selected route use the HazardMark shapes. They are pointer
//   shortcuts only: hidden from assistive technology and out of the tab order. A click
//   jumps to the same stop in the list, which is the keyboard and screen-reader path.
// - Selecting a route fits the map to it; under reduced motion it jumps, never flies.
// - The style follows the settings and the theme (lib/map-style.ts); the element that
//   holds the canvas carries the map's palette (lib/map-surface.ts), so markers and
//   controls inside it keep their contrast on either basemap.
//
// The map only illustrates the list. If WebGL or the basemap fails, a sentence says so
// and the list still holds everything.

import { MutableRefObject, useEffect, useRef, useState } from "react";
import type { GeoJSONSource, LngLatBoundsLike, Map as MapLibreMap, Marker } from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import { effectShape, hazardShapeSvg } from "./ui/hazard-shapes";
import { type LinePalette, useMapSurface } from "@/lib/map-surface";
import maplibregl from "@/lib/maplibre";
import type { PlannerPlace, RouteOut } from "@/lib/planner-types";
import { prefersReducedMotion, useReducedMotion } from "@/lib/settings";
import styles from "./planner-map.module.css";

const EDMONTON: [number, number] = [-113.4938, 53.5461];

export type MapCondition = { id: string; effect: string; title: string; lng: number; lat: number };
export type MapCentre = { lat: number; lng: number };

type Props = {
  routes: RouteOut[];
  selectedRank: number | null;
  from: PlannerPlace | null;
  to: PlannerPlace | null;
  conditions: MapCondition[];
  onCondition: (id: string) => void;
  /** Filled with a function that reads the map centre, for "Use the map centre". */
  centreRef: MutableRefObject<(() => MapCentre | null) | null>;
};

type Coordinates = [number, number][];

const EMPTY: GeoJSON.FeatureCollection = { type: "FeatureCollection", features: [] };

function lineCoordinates(geometry: Record<string, unknown>): Coordinates {
  const coordinates = geometry.coordinates;
  if (geometry.type !== "LineString" || !Array.isArray(coordinates)) return [];
  return coordinates.filter(
    (point): point is [number, number] =>
      Array.isArray(point) && typeof point[0] === "number" && typeof point[1] === "number"
  );
}

function routeFeatures(routes: RouteOut[], selectedRank: number | null): GeoJSON.FeatureCollection {
  const features: GeoJSON.Feature[] = [];
  // The others first, so the selected route draws on top.
  const ordered = [...routes].sort((a, b) => Number(a.rank === selectedRank) - Number(b.rank === selectedRank));
  for (const route of ordered) {
    const selected = route.rank === selectedRank;
    if (selected && route.legs?.length) {
      for (const leg of route.legs) {
        features.push({
          type: "Feature",
          properties: { selected: true, ride: leg.kind === "ride" },
          geometry: { type: "LineString", coordinates: lineCoordinates(leg.geometry) }
        });
      }
    } else {
      features.push({
        type: "Feature",
        properties: { selected, ride: false },
        geometry: { type: "LineString", coordinates: lineCoordinates(route.geometry) }
      });
    }
  }
  return { type: "FeatureCollection", features };
}

function boundsOf(points: Coordinates): LngLatBoundsLike | null {
  if (!points.length) return null;
  let [minLng, minLat] = points[0];
  let [maxLng, maxLat] = points[0];
  for (const [lng, lat] of points) {
    minLng = Math.min(minLng, lng);
    maxLng = Math.max(maxLng, lng);
    minLat = Math.min(minLat, lat);
    maxLat = Math.max(maxLat, lat);
  }
  return [
    [minLng, minLat],
    [maxLng, maxLat]
  ];
}

function placeMarker(label: "Start" | "End"): HTMLElement {
  const element = document.createElement("div");
  element.className = styles.place;
  element.setAttribute("aria-hidden", "true");
  // Constant markup; no user text goes into HTML.
  element.innerHTML = `<span class="${styles.placeDot}"></span><span class="${styles.placeTag}">${label}</span>`;
  return element;
}

/**
 * The route source and its four layers. A style change (`map.setStyle`) drops every
 * layer a page added, so this runs on each `style.load` and draws the current data
 * again in the current palette.
 */
function addRouteLayers(map: MapLibreMap, lines: LinePalette, data: GeoJSON.FeatureCollection): void {
  if (map.getSource("routes")) return;
  map.addSource("routes", { type: "geojson", data });
  const layout = { "line-join": "round", "line-cap": "round" } as const;
  map.addLayer({
    id: "route-others",
    type: "line",
    source: "routes",
    filter: ["==", ["get", "selected"], false],
    layout,
    paint: { "line-color": lines.others, "line-width": 3 }
  });
  map.addLayer({
    id: "route-casing",
    type: "line",
    source: "routes",
    filter: ["==", ["get", "selected"], true],
    layout,
    paint: { "line-color": lines.casing, "line-width": ["case", ["get", "ride"], 14, 10] }
  });
  map.addLayer({
    id: "route-selected",
    type: "line",
    source: "routes",
    filter: ["all", ["==", ["get", "selected"], true], ["==", ["get", "ride"], false]],
    layout,
    paint: { "line-color": lines.route, "line-width": 6 }
  });
  map.addLayer({
    id: "route-ride",
    type: "line",
    source: "routes",
    filter: ["all", ["==", ["get", "selected"], true], ["==", ["get", "ride"], true]],
    layout,
    // Two 3 px rails with a 5 px gap: the double line of an LRT ride.
    paint: { "line-color": lines.route, "line-width": 3, "line-gap-width": 5 }
  });
}

export default function PlannerMap({ routes, selectedRank, from, to, conditions, onCondition, centreRef }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<MapLibreMap | null>(null);
  const placeMarkers = useRef<Marker[]>([]);
  const conditionMarkers = useRef<Marker[]>([]);
  const onConditionRef = useRef(onCondition);
  const [loaded, setLoaded] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const surface = useMapSurface();
  const reduce = useReducedMotion();
  // What the current style needs to draw again after a style change.
  const linesRef = useRef(surface.lines);
  const dataRef = useRef<GeoJSON.FeatureCollection>(EMPTY);
  const styleKeyRef = useRef(surface.styleKey);

  useEffect(() => {
    onConditionRef.current = onCondition;
  }, [onCondition]);
  useEffect(() => {
    linesRef.current = surface.lines;
  }, [surface.lines]);

  // Create the map once.
  useEffect(() => {
    if (!containerRef.current || mapRef.current) return;
    let map: MapLibreMap;
    try {
      map = new maplibregl.Map({
        container: containerRef.current,
        style: surface.style,
        center: EDMONTON,
        zoom: 11,
        fadeDuration: prefersReducedMotion() ? 0 : 300,
        // On a phone the map sits in a scrolling page: one finger scrolls the page,
        // two move the map.
        cooperativeGestures: window.matchMedia("(pointer: coarse)").matches,
        attributionControl: { compact: true }
      });
    } catch {
      setProblem("The map cannot be drawn in this browser. The route list has everything the map would show.");
      return;
    }
    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
    map.getCanvas().setAttribute("aria-label", "Map of the routes. The route list has the same information.");
    // Fires for the first style and after every setStyle.
    map.on("style.load", () => {
      addRouteLayers(map, linesRef.current, dataRef.current);
      setLoaded(true);
    });
    map.on("error", () => {
      if (!map.isStyleLoaded()) {
        setProblem("The map background did not load. The route list has everything the map would show.");
      }
    });
    mapRef.current = map;
    centreRef.current = () => {
      const centre = mapRef.current?.getCenter();
      return centre ? { lat: centre.lat, lng: centre.lng } : null;
    };
    return () => {
      centreRef.current = null;
      placeMarkers.current.forEach((marker) => marker.remove());
      conditionMarkers.current.forEach((marker) => marker.remove());
      map.remove();
      mapRef.current = null;
    };
    // The surface at creation time only; changes go through setStyle below.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [centreRef]);

  // The style follows the high-contrast setting, the theme and the text size.
  useEffect(() => {
    const map = mapRef.current;
    if (!map || styleKeyRef.current === surface.styleKey) return;
    styleKeyRef.current = surface.styleKey;
    setLoaded(false);
    map.setStyle(surface.style);
  }, [surface.styleKey, surface.style]);

  // The route lines.
  useEffect(() => {
    dataRef.current = routeFeatures(routes, selectedRank);
    const source = mapRef.current?.getSource("routes") as GeoJSONSource | undefined;
    if (loaded && source) source.setData(dataRef.current);
  }, [loaded, routes, selectedRank]);

  // Fit the map to the selected route, or to the chosen places before there are routes.
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    const selected = routes.find((route) => route.rank === selectedRank);
    const points: Coordinates = selected
      ? lineCoordinates(selected.geometry)
      : [from?.coordinates, to?.coordinates].filter((point): point is [number, number] => Boolean(point));
    const animate = !reduce;
    if (points.length === 1) {
      map.jumpTo({ center: points[0], zoom: Math.max(map.getZoom(), 14) });
      return;
    }
    const bounds = boundsOf(points);
    // More room at the bottom, where the attribution sits.
    const padding = { top: 56, right: 64, bottom: 72, left: 48 };
    if (bounds) map.fitBounds(bounds, { padding, maxZoom: 16, animate, duration: animate ? 500 : 0 });
    // Only a new route or place moves the map; a motion setting applies to the next move.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [routes, selectedRank, from, to]);

  // Start and end.
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    placeMarkers.current.forEach((marker) => marker.remove());
    placeMarkers.current = [];
    for (const [place, label] of [
      [from, "Start"],
      [to, "End"]
    ] as const) {
      if (!place) continue;
      placeMarkers.current.push(
        new maplibregl.Marker({ element: placeMarker(label), anchor: "center" }).setLngLat(place.coordinates).addTo(map)
      );
    }
  }, [from, to]);

  // Conditions on the selected route. Built from the conditions alone, never from the
  // selection, so an existing marker element is kept while the selection changes.
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    conditionMarkers.current.forEach((marker) => marker.remove());
    conditionMarkers.current = conditions.map((condition, index) => {
      const element = document.createElement("div");
      element.className = "map-marker";
      element.setAttribute("aria-hidden", "true");
      element.title = condition.title;
      // A constant SVG from hazard-shapes.ts; the item's text never goes into HTML.
      element.innerHTML = hazardShapeSvg(effectShape(condition.effect), 24, `route-marker-${index}`);
      element.addEventListener("click", () => onConditionRef.current(condition.id));
      return new maplibregl.Marker({ element, anchor: "center" }).setLngLat([condition.lng, condition.lat]).addTo(map);
    });
  }, [conditions]);

  const selected = routes.find((route) => route.rank === selectedRank);
  const { lines } = surface;
  const swatch = { background: lines.land };
  return (
    <div className={styles.frame}>
      <div className={`${styles.surface} ${surface.surfaceClass}`}>
        <div ref={containerRef} className={styles.canvas} />
      </div>
      {problem && <p className={styles.problem}>{problem}</p>}
      {selected && (
        <ul className={styles.key} aria-label="Map lines">
          <li>
            <svg className={styles.swatch} style={swatch} width="36" height="16" viewBox="0 0 36 16" aria-hidden="true" focusable="false">
              <path d="M6 8h24" stroke={lines.casing} strokeWidth="10" strokeLinecap="round" />
              <path d="M6 8h24" stroke={lines.route} strokeWidth="6" strokeLinecap="round" />
            </svg>
            Thick line: route {selected.rank}
          </li>
          {selected.legs?.some((leg) => leg.kind === "ride") && (
            <li>
              <svg className={styles.swatch} style={swatch} width="36" height="16" viewBox="0 0 36 16" aria-hidden="true" focusable="false">
                <path d="M6 4h24M6 12h24" stroke={lines.route} strokeWidth="3" strokeLinecap="round" />
              </svg>
              Double line: LRT ride
            </li>
          )}
          {routes.length > 1 && (
            <li>
              <svg className={styles.swatch} style={swatch} width="36" height="16" viewBox="0 0 36 16" aria-hidden="true" focusable="false">
                <path d="M6 8h24" stroke={lines.others} strokeWidth="3" strokeLinecap="round" />
              </svg>
              Thin lines: the other routes
            </li>
          )}
        </ul>
      )}
    </div>
  );
}
